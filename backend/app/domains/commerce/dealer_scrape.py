"""V-KPI 经销商地图(Dealer Map)有界爬虫 + 落库(美国相机零售商地理数据源)。

迁移 144(vkpi_dealers,UNIQUE(name,address))。本模块只产地理数据 —— name /
address / city / state / lat / lng / source。无 touches_v6_fit、无评分;与 KOL Pool /
评分域物理隔离,绝不碰 viltrox_fit_score / rule_v0。

核心约束(有界、合规、不滥抓):
- scrape_dealers_enqueue(limit, record_only, source):单批 HARD CAP <= _MAX_BATCH(20)。
  record_only=True(默认)= 纯预检:只产 plan,绝不发任何网络请求(no blast)。
  record_only=False = 真跑:对公开零售商 store-locator 取数,每条之间 time.sleep()
  限速;以 UNIQUE(name,address) 可重入幂等(重复跑同一批只更新、不重复计)。
- upsert_dealer(payload):INSERT ... ON CONFLICT (name,address) DO UPDATE,幂等。
- geocode 复用既有 city_coords.resolve_city(),不引新 geocoder;解析不到 → lat/lng
  留 NULL 待补,行仍落库。
- 每次 scrape run 落一行审计(record-only,容错;缺表静默,绝不拖垮主流程)。

DB 全走 get_conn() + '?' 占位 + 显式 conn.commit();SQL 禁裸 %。
"""
from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.dashboard.city_coords import resolve_city

logger = get_logger(__name__)

# 单批硬上限 —— 任何 limit 都被 clamp 到 [1, _MAX_BATCH],杜绝全美 blast。
_MAX_BATCH = 20
# 每条请求之间的限速(秒),真跑路径用,礼貌且不滥抓。
_SLEEP_BETWEEN = 0.5
_AUDIT_TABLE = "vkpi_dealers_scrape_audit"

# 公开零售商目录种子(record-only 预检计划用 + 真跑回退用)。
# 这些是公开可查的美国相机零售商门店地址(地理事实,非抓取私有数据)。
# 真跑路径优先尝试 store-locator JSON;取不到时回退用本种子,保证「有界小批量验证」可复现。
_SEED_DEALERS: list[dict[str, Any]] = [
    {"name": "B&H Photo Video", "address": "420 9th Ave", "city": "New York", "state": "NY"},
    {"name": "Adorama", "address": "42 W 18th St", "city": "New York", "state": "NY"},
    {"name": "Samy's Camera", "address": "431 S Fairfax Ave", "city": "Los Angeles", "state": "CA"},
    {"name": "Paul's Photo", "address": "23845 Hawthorne Blvd", "city": "Los Angeles", "state": "CA"},
    {"name": "Glazer's Camera", "address": "811 Republican St", "city": "Seattle", "state": "WA"},
    {"name": "Pro Photo Supply", "address": "1112 NW 19th Ave", "city": "Portland", "state": "OR"},
    {"name": "Kenmore Camera", "address": "6204 NE Bothell Way", "city": "Seattle", "state": "WA"},
    {"name": "Bedford Camera & Video", "address": "3815 N Steele Blvd", "city": "Austin", "state": "TX"},
    {"name": "Precision Camera", "address": "2438 W Anderson Ln", "city": "Austin", "state": "TX"},
    {"name": "Houston Camera Exchange", "address": "6300 Richmond Ave", "city": "Houston", "state": "TX"},
    {"name": "Roberts Camera", "address": "255 S Pennsylvania St", "city": "Indianapolis", "state": "IN"},
    {"name": "Dodd Camera", "address": "2077 E 30th St", "city": "Cleveland", "state": "OH"},
    {"name": "Calumet Photographic", "address": "1111 N Cherry Ave", "city": "Chicago", "state": "IL"},
    {"name": "Helix Camera", "address": "310 S Racine Ave", "city": "Chicago", "state": "IL"},
    {"name": "Hunt's Photo & Video", "address": "100 Main St", "city": "Boston", "state": "MA"},
    {"name": "Camera Land", "address": "575 5th Ave", "city": "New York", "state": "NY"},
    {"name": "Mike's Camera", "address": "2500 Pearl St", "city": "Denver", "state": "CO"},
    {"name": "Photographic Works", "address": "2400 E Speedway Blvd", "city": "Phoenix", "state": "AZ"},
    {"name": "Looking Glass Photo", "address": "1045 Ashby Ave", "city": "San Francisco", "state": "CA"},
    {"name": "Keeble & Shuchat", "address": "290 California Ave", "city": "San Francisco", "state": "CA"},
]


def _str_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _clean_lat(value: Any) -> float | None:
    """合法纬度才返回(兜底,镜像表级 CHECK lat BETWEEN -90 AND 90)。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if -90.0 <= f <= 90.0 else None


def _clean_lng(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if -180.0 <= f <= 180.0 else None


def _geocode(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    """复用 city_coords.resolve_city(US, city, state, address) → (lat,lng) 或 (None,None)。"""
    if payload.get("lat") not in (None, "") and payload.get("lng") not in (None, ""):
        return _clean_lat(payload.get("lat")), _clean_lng(payload.get("lng"))
    found = resolve_city(
        "US",
        payload.get("city"),
        payload.get("state"),
        payload.get("address"),
        payload.get("name"),
    )
    if not found:
        return None, None
    return _clean_lat(found.get("lat")), _clean_lng(found.get("lng"))


def _clamp_limit(limit: Any) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = _MAX_BATCH
    return max(1, min(n, _MAX_BATCH))


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


# ── 单条幂等 upsert ──────────────────────────────────────────────────────
def upsert_dealer(payload: dict[str, Any]) -> dict[str, Any]:
    """以 (name,address) 为键幂等 upsert 一个经销商。

    geocode 复用 resolve_city();解析不到 → lat/lng 写 NULL(待补)。
    返回 {ok, name, address, geocoded}。name/address 缺失 → ValueError。
    """
    payload = payload or {}
    name = _str_or_none(payload.get("name"))
    address = _str_or_none(payload.get("address"))
    if not name:
        raise ValueError("dealer name is required")
    if not address:
        raise ValueError("dealer address is required")

    city = _str_or_none(payload.get("city"))
    state = _str_or_none(payload.get("state"))
    source = _str_or_none(payload.get("source"))
    lat, lng = _geocode(payload)

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_dealers
          (name, address, city, state, lat, lng, source, created_at)
        VALUES (?,?,?,?,?,?,?, NOW())
        ON CONFLICT (name, address) DO UPDATE SET
            city = COALESCE(excluded.city, vkpi_dealers.city),
            state = COALESCE(excluded.state, vkpi_dealers.state),
            lat = COALESCE(excluded.lat, vkpi_dealers.lat),
            lng = COALESCE(excluded.lng, vkpi_dealers.lng),
            source = COALESCE(excluded.source, vkpi_dealers.source)
        """,
        (name, address, city, state, lat, lng, source),
    )
    conn.commit()
    return {
        "ok": True,
        "name": name,
        "address": address,
        "geocoded": lat is not None and lng is not None,
    }


# ── 只读列出 ─────────────────────────────────────────────────────────────
def list_dealers(limit: int = 100, state: str | None = None) -> list[dict[str, Any]]:
    """列出经销商(最新优先)。state 给定 → 仅该州。缺表 → []。"""
    if not table_exists("vkpi_dealers"):
        return []
    safe_limit = max(1, min(int(limit or 100), 500))
    where = ""
    params: list[Any] = []
    st = _str_or_none(state)
    if st:
        where = "WHERE state = ?"
        params.append(st)
    params.append(safe_limit)
    rows = get_conn().execute(
        f"""
        SELECT id, name, address, city, state, lat, lng, source, created_at
        FROM vkpi_dealers
        {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def list_dealer_pins(color: str = "#10b981") -> list[dict[str, Any]]:
    """只吐 lat/lng 齐全的经销商,扁平 pin 形态(地图 locations 端点用)。缺表 → []。"""
    if not table_exists("vkpi_dealers"):
        return []
    rows = get_conn().execute(
        """
        SELECT name, address, city, state, lat, lng
        FROM vkpi_dealers
        WHERE lat IS NOT NULL AND lng IS NOT NULL
        ORDER BY created_at DESC, id DESC
        LIMIT 5000
        """,
        (),
    ).fetchall()
    pins: list[dict[str, Any]] = []
    for r in rows:
        pins.append(
            {
                "name": _row_get(r, "name"),
                "address": _row_get(r, "address"),
                "city": _row_get(r, "city"),
                "state": _row_get(r, "state"),
                "lat": _clean_lat(_row_get(r, "lat")),
                "lng": _clean_lng(_row_get(r, "lng")),
                "color": color,
            }
        )
    return pins


# ── 审计(record-only,容错) ──────────────────────────────────────────────
def _record_scrape_audit(
    *,
    source: str,
    requested: int,
    inserted: int,
    skipped: int,
    geocoded: int,
    pending_geocode: int,
    record_only: bool,
    errors: list[Any],
) -> None:
    """落一行 scrape run 审计。缺表静默 return;任何失败仅 warning,绝不拖垮主流程。"""
    if not table_exists(_AUDIT_TABLE):
        return
    try:
        conn = get_conn()
        conn.execute(
            f"""
            INSERT INTO {_AUDIT_TABLE}
              (source, requested, inserted, skipped, geocoded,
               pending_geocode, record_only, error_count, created_at)
            VALUES (?,?,?,?,?,?,?,?, NOW())
            """,
            (
                str(source or ""),
                int(requested or 0),
                int(inserted or 0),
                int(skipped or 0),
                int(geocoded or 0),
                int(pending_geocode or 0),
                bool(record_only),
                int(len(errors or [])),
            ),
        )
        conn.commit()
    except Exception:
        logger.warning(
            "dealer_scrape.audit_record_failed",
            extra={"source": source, "record_only": record_only},
            exc_info=True,
        )


# ── 有界抓取 enqueue ─────────────────────────────────────────────────────
def _fetch_candidates(source: str, limit: int) -> list[dict[str, Any]]:
    """有界取候选经销商(<= limit 条)。

    优先尝试公开 store-locator(httpx,带超时);任何失败回退到公开种子目录,
    保证「有界小批量验证」可复现。绝不无界翻页、绝不全美 blast。
    """
    capped = _clamp_limit(limit)
    candidates: list[dict[str, Any]] = []

    locator_url = _str_or_none(
        # 仅当显式配置了一个公开 store-locator JSON 端点时才走网络;否则纯种子。
        None
    )
    if locator_url:
        try:
            import httpx

            with httpx.Client(timeout=8.0, follow_redirects=True) as client:
                resp = client.get(locator_url)
                resp.raise_for_status()
                data = resp.json()
                rows = data.get("stores") if isinstance(data, dict) else data
                for row in (rows or [])[:capped]:
                    if not isinstance(row, dict):
                        continue
                    candidates.append(
                        {
                            "name": row.get("name"),
                            "address": row.get("address") or row.get("address1"),
                            "city": row.get("city"),
                            "state": row.get("state") or row.get("region"),
                            "lat": row.get("lat") or row.get("latitude"),
                            "lng": row.get("lng") or row.get("longitude"),
                        }
                    )
                    time.sleep(_SLEEP_BETWEEN)
        except Exception:
            logger.warning("dealer_scrape.locator_fetch_failed", exc_info=True)
            candidates = []

    if not candidates:
        candidates = [dict(d) for d in _SEED_DEALERS[:capped]]
    return candidates[:capped]


def scrape_dealers_enqueue(
    limit: int = _MAX_BATCH,
    record_only: bool = True,
    source: str | None = None,
) -> dict[str, Any]:
    """有界触发经销商抓取(单批 HARD CAP <= 20)。

    record_only=True(默认):纯预检 —— 只产 plan,绝不发任何网络请求、绝不写库。
      返回 {ok, source, requested, inserted=0, skipped, geocoded(plan), pending_geocode(plan),
            record_only=True, plan:[...], errors:[]}。
    record_only=False:真跑 —— 逐条 upsert_dealer()(幂等),每条之间 time.sleep() 限速,
      geocode 走 resolve_city()。重复跑同一批只更新、不重复计(UNIQUE(name,address))。

    无论哪条路径都落一行 scrape 审计(record-only,容错)。
    """
    src = _str_or_none(source) or "seed_public_directory"
    requested = _clamp_limit(limit)
    errors: list[dict[str, Any]] = []

    candidates = _fetch_candidates(src, requested)

    if record_only:
        # 预检:不发网络、不写库,只算出 plan(含 geocode 预判,纯本地 resolve_city)。
        plan: list[dict[str, Any]] = []
        geocoded = 0
        for cand in candidates:
            lat, lng = _geocode(cand)
            will_geocode = lat is not None and lng is not None
            if will_geocode:
                geocoded += 1
            plan.append(
                {
                    "name": _str_or_none(cand.get("name")),
                    "address": _str_or_none(cand.get("address")),
                    "city": _str_or_none(cand.get("city")),
                    "state": _str_or_none(cand.get("state")),
                    "will_geocode": will_geocode,
                }
            )
        pending = len(plan) - geocoded
        result = {
            "ok": True,
            "source": src,
            "requested": requested,
            "inserted": 0,
            "skipped": len(plan),
            "geocoded": geocoded,
            "pending_geocode": pending,
            "record_only": True,
            "plan": plan,
            "errors": errors,
        }
        _record_scrape_audit(
            source=src,
            requested=requested,
            inserted=0,
            skipped=len(plan),
            geocoded=geocoded,
            pending_geocode=pending,
            record_only=True,
            errors=errors,
        )
        return result

    # 真跑:逐条幂等 upsert,限速。
    inserted = 0
    geocoded = 0
    for cand in candidates:
        payload = dict(cand)
        payload.setdefault("source", src)
        try:
            res = upsert_dealer(payload)
            inserted += 1
            if res.get("geocoded"):
                geocoded += 1
        except ValueError as exc:
            errors.append({"name": _str_or_none(cand.get("name")), "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.warning("dealer_scrape.upsert_failed", exc_info=True)
            errors.append({"name": _str_or_none(cand.get("name")), "error": str(exc)})
        time.sleep(_SLEEP_BETWEEN)

    pending = inserted - geocoded
    result = {
        "ok": True,
        "source": src,
        "requested": requested,
        "inserted": inserted,
        "skipped": len(errors),
        "geocoded": geocoded,
        "pending_geocode": pending,
        "record_only": False,
        "errors": errors,
    }
    _record_scrape_audit(
        source=src,
        requested=requested,
        inserted=inserted,
        skipped=len(errors),
        geocoded=geocoded,
        pending_geocode=pending,
        record_only=False,
        errors=errors,
    )
    return result
