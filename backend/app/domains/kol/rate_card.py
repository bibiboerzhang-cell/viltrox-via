"""KOL 报价卡(rate_card v0)—— 件B:一个 KOL「请他大概要花多少钱」的可追溯估价。

跨代理契约(A 预测 / D 组合会 import 本模块,路径与签名固定):
    estimate_rate(kol_pool_id: int) -> dict
    返回键含 status / estimated_usd_p50 / low / high / method / source_count / confidence,
    另带 basis(估价依据,可追溯)与 currency;对缺 KOL / 缺表 / 缺数据永不抛异常,
    诚实降级(status="not_found" / "empty" 或 method=cpm_benchmark_v0 + confidence=low)。

两条估价路径:
  1) recorded_rates_median_v0 —— vkpi_kol_rates(迁移211)里有真实报价行
     (source in contract/outreach_reply/negotiation)时,取中位数为 p50、min/max 为区间;
     含 contract 行 confidence=high,否则 medium。
  2) cpm_benchmark_v0 —— 无真实报价(或表未建)时兜底:粉丝层级 x 平台的行业 CPM 区间
     (模块内常量 CPM_BENCHMARK_USD,来源为行业基准 v0 待喂养校准)x 均播放折算,
     confidence 恒为 low;均播放缺失时按粉丝数 x 保守观看率估播放并在 basis 里如实标注。

数据源(读):vkpi_kol_pool(followers/avg_views/platform)/ vkpi_kol_rates /
vkpi_kol_video_evidence(均播放兜底)。写:仅 add_rate 落 vkpi_kol_rates 一表。

compat 约定:SQL 占位符用 ?;SQL 禁 percent 字符(不用 LIKE);BOOLEAN 读回可能是
int 1/0(本模块未用布尔列);函数内懒 import get_conn。
红线:纯报价台账 + 决定性算术,零 LLM 零新采集;估价数字必须可追溯(basis/method/
confidence),数据不足诚实 low confidence 或空态,绝不杜撰;零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from contextvars import ContextVar
import statistics
from datetime import date, datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── 口径常量 ─────────────────────────────────────────────────────────

# 真实报价来源(参与中位数);cpm_benchmark 行只是基准折算存档,不算真报价。
REAL_QUOTE_SOURCES: tuple[str, ...] = ("contract", "outreach_reply", "negotiation")
ALLOWED_SOURCES: tuple[str, ...] = REAL_QUOTE_SOURCES + ("cpm_benchmark",)
ALLOWED_CONFIDENCE: tuple[str, ...] = ("low", "medium", "high")
# 录入时缺省 confidence 按来源推:合同成交价最硬,基准折算最软。
DEFAULT_CONFIDENCE_BY_SOURCE: dict[str, str] = {
    "contract": "high",
    "outreach_reply": "medium",
    "negotiation": "medium",
    "cpm_benchmark": "low",
}

# 粉丝层级:nano <10k / micro <100k / mid <500k / macro >=500k。
TIER_BOUNDS: tuple[tuple[str, int, int | None], ...] = (
    ("nano", 0, 10_000),
    ("micro", 10_000, 100_000),
    ("mid", 100_000, 500_000),
    ("macro", 500_000, None),
)

# 行业 CPM 基准区间(USD / 千播放,赞助内容口径)——「行业基准 v0 待喂养校准」:
# 数字为业内公开区间的保守取值,专供无真实报价时兜底;每进一条真报价即被中位数覆盖,
# 后续可用已录报价反推校准本表。层级越高单位 CPM 越贵(议价力/制作规格溢价)。
CPM_BENCHMARK_USD: dict[str, dict[str, tuple[float, float]]] = {
    "youtube": {
        "nano": (20.0, 50.0),
        "micro": (25.0, 60.0),
        "mid": (30.0, 75.0),
        "macro": (35.0, 90.0),
    },
    "instagram": {
        "nano": (15.0, 40.0),
        "micro": (20.0, 50.0),
        "mid": (25.0, 60.0),
        "macro": (30.0, 75.0),
    },
    "tiktok": {
        "nano": (10.0, 30.0),
        "micro": (15.0, 40.0),
        "mid": (20.0, 50.0),
        "macro": (25.0, 60.0),
    },
}
# 未知平台按 instagram 档兜底(中间水位,不取最贵也不取最便宜)。
FALLBACK_PLATFORM = "instagram"

# 均播放彻底缺失时的保守观看率(均播放 = 粉丝 x 观看率)——同样属行业基准 v0 待喂养校准;
# 用到该兜底时 basis.view_source 如实标 follower_ratio_estimate,confidence 恒 low。
VIEW_RATE_BY_PLATFORM: dict[str, float] = {
    "youtube": 0.10,
    "instagram": 0.08,
    "tiktok": 0.08,
}
FALLBACK_VIEW_RATE = 0.08

# Request-local batch rows used by GTM planning.  ``estimate_rate`` remains the
# only calculator; this context only replaces its repeated per-KOL SELECTs.
_BATCH_READS: ContextVar[dict[str, Any] | None] = ContextVar(
    "vkpi_rate_card_batch_reads",
    default=None,
)


# ── 小工具 ───────────────────────────────────────────────────────────


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _usd(value: float) -> float:
    return round(float(value), 2)


def _tier_for_followers(followers: int) -> str:
    for name, low, high in TIER_BOUNDS:
        if followers >= low and (high is None or followers < high):
            return name
    return "nano"


def _cpm_range(platform: str, tier: str) -> tuple[float, float]:
    table = CPM_BENCHMARK_USD.get(platform) or CPM_BENCHMARK_USD[FALLBACK_PLATFORM]
    return table.get(tier) or table["micro"]


# ── 数据装载(读)────────────────────────────────────────────────────


def _load_pool_row(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    batch = _BATCH_READS.get()
    if batch is not None and int(kol_pool_id) in batch["ids"]:
        row = batch["pool"].get(int(kol_pool_id))
        return dict(row) if row else None
    row = conn.execute(
        """
        SELECT id, handle, display_name, platform, followers, avg_views
        FROM vkpi_kol_pool WHERE id = ?
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_rate_rows(conn: Any, kol_pool_id: int, limit: int = 200) -> list[dict[str, Any]]:
    """已录报价行(新在前)。表未建(迁移211未 apply)时由调用方捕获异常降级。"""
    batch = _BATCH_READS.get()
    if (
        batch is not None
        and int(kol_pool_id) in batch["ids"]
        and int(limit) == int(batch["rate_limit"])
    ):
        return [dict(row) for row in batch["rates"].get(int(kol_pool_id), [])]
    rows = conn.execute(
        """
        SELECT id, platform, content_type, amount_usd, currency, source,
               confidence, note, effective_date, created_at
        FROM vkpi_kol_rates
        WHERE kol_pool_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def _evidence_avg_views(conn: Any, kol_pool_id: int) -> int | None:
    """池表 avg_views 缺失时,退证据表有播放数视频的均播放;再缺诚实 None。"""
    batch = _BATCH_READS.get()
    if batch is not None and int(kol_pool_id) in batch["ids"]:
        value = batch["evidence_avg"].get(int(kol_pool_id))
        return int(value) if value is not None else None
    try:
        row = conn.execute(
            """
            SELECT AVG(view_count) AS av, COUNT(*) AS n
            FROM vkpi_kol_video_evidence
            WHERE kol_pool_id = ? AND view_count IS NOT NULL AND view_count > 0
            """,
            (int(kol_pool_id),),
        ).fetchone()
        data = dict(row) if row else {}
        if (_int_or_none(data.get("n")) or 0) > 0:
            av = _float_or_none(data.get("av"))
            return int(round(av)) if av and av > 0 else None
    except Exception as exc:  # noqa: BLE001 — 证据表读挂不影响主路径
        logger.warning("rate_card evidence avg_views failed for kol=%s: %s", kol_pool_id, exc)
    return None


def estimate_rates(
    kol_pool_ids: list[int],
    *,
    conn: Any = None,
) -> dict[int, dict[str, Any]]:
    """Batch-load inputs and reuse ``estimate_rate`` for every requested KOL.

    The recorded-rate per-KOL cap, ordering, CPM fallback and confidence rules
    remain exactly those of the public single-item function.
    """
    from collections import defaultdict
    from app.db.connection import get_conn

    ids: list[int] = []
    for value in kol_pool_ids:
        parsed = _int_or_none(value)
        if parsed is not None and parsed > 0 and parsed not in ids:
            ids.append(parsed)
    if not ids:
        return {}
    db = conn or get_conn()
    placeholders = ",".join("?" for _ in ids)
    rate_limit = 200
    try:
        pool_rows = db.execute(
            "SELECT id, handle, display_name, platform, followers, avg_views "
            f"FROM vkpi_kol_pool WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - 批读失败退原单人契约
        logger.warning("rate batch pool preload failed, falling back: %s", exc)
        return {kol_pool_id: estimate_rate(kol_pool_id, conn=db) for kol_pool_id in ids}

    try:
        rate_rows = db.execute(
            f"""
            WITH ranked AS (
              SELECT id, kol_pool_id, platform, content_type, amount_usd,
                     currency, source, confidence, note, effective_date, created_at,
                     ROW_NUMBER() OVER (
                       PARTITION BY kol_pool_id ORDER BY created_at DESC, id DESC
                     ) AS batch_rank
              FROM vkpi_kol_rates
              WHERE kol_pool_id IN ({placeholders})
            )
            SELECT * FROM ranked WHERE batch_rank <= ?
            ORDER BY kol_pool_id, batch_rank
            """,
            (*ids, rate_limit),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - 任一批读不等价时退回原单人契约
        logger.warning("rate batch recorded-rate preload failed, falling back: %s", exc)
        return {kol_pool_id: estimate_rate(kol_pool_id, conn=db) for kol_pool_id in ids}

    try:
        avg_rows = db.execute(
            f"""
            SELECT kol_pool_id, AVG(view_count) AS av, COUNT(*) AS n
            FROM vkpi_kol_video_evidence
            WHERE kol_pool_id IN ({placeholders})
              AND view_count IS NOT NULL AND view_count > 0
            GROUP BY kol_pool_id
            """,
            tuple(ids),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - 任一批读不等价时退回原单人契约
        logger.warning("rate batch evidence averages failed, falling back: %s", exc)
        return {kol_pool_id: estimate_rate(kol_pool_id, conn=db) for kol_pool_id in ids}

    rates_by_kol: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw in rate_rows:
        row = dict(raw)
        rates_by_kol[int(row["kol_pool_id"])].append(row)
    evidence_avg: dict[int, int] = {}
    for raw in avg_rows:
        row = dict(raw)
        if (_int_or_none(row.get("n")) or 0) <= 0:
            continue
        average = _float_or_none(row.get("av"))
        if average is not None and average > 0:
            evidence_avg[int(row["kol_pool_id"])] = int(round(average))
    batch = {
        "ids": frozenset(ids),
        "pool": {int(dict(row)["id"]): dict(row) for row in pool_rows},
        "rates": dict(rates_by_kol),
        "rate_limit": rate_limit,
        "evidence_avg": evidence_avg,
    }
    token = _BATCH_READS.set(batch)
    try:
        return {kol_pool_id: estimate_rate(kol_pool_id, conn=db) for kol_pool_id in ids}
    finally:
        _BATCH_READS.reset(token)


# ── 契约主入口:estimate_rate ────────────────────────────────────────


def estimate_rate(kol_pool_id: int, *, conn: Any = None) -> dict[str, Any]:
    """KOL 报价估算(跨代理契约,永不抛异常)。

    返回键:status / estimated_usd_p50 / low / high / method / source_count / confidence
    / currency / basis / kol_pool_id / generated_at。low 与 high 同时以
    estimated_usd_low / estimated_usd_high 别名重复给出,双命名兼容消费方。
    """
    base: dict[str, Any] = {
        "kol_pool_id": int(kol_pool_id),
        "status": "empty",
        "estimated_usd_p50": None,
        "low": None,
        "high": None,
        "estimated_usd_low": None,
        "estimated_usd_high": None,
        "currency": "USD",
        "method": None,
        "source_count": 0,
        "confidence": "low",
        "basis": {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from app.db.connection import get_conn

        db = conn or get_conn()
        pool = _load_pool_row(db, int(kol_pool_id))
        if not pool:
            base["status"] = "not_found"
            base["reason"] = f"kol_pool {int(kol_pool_id)} not found"
            return base

        # 路径一:已录真实报价 → 中位数覆盖基准
        rate_rows: list[dict[str, Any]] = []
        rates_table_ok = True
        try:
            rate_rows = _load_rate_rows(db, int(kol_pool_id))
        except Exception as exc:  # noqa: BLE001 — 表未建(迁移211未 apply)→ 基准兜底,不炸
            rates_table_ok = False
            logger.warning("vkpi_kol_rates unavailable, cpm fallback (kol=%s): %s", kol_pool_id, exc)

        real_rows = [
            r for r in rate_rows
            if _text(r.get("source"), 40) in REAL_QUOTE_SOURCES
            and (_float_or_none(r.get("amount_usd")) or 0) > 0
        ]
        if real_rows:
            amounts = sorted(_float_or_none(r.get("amount_usd")) or 0.0 for r in real_rows)
            has_contract = any(_text(r.get("source"), 40) == "contract" for r in real_rows)
            base.update(
                status="ready",
                estimated_usd_p50=_usd(statistics.median(amounts)),
                low=_usd(amounts[0]),
                high=_usd(amounts[-1]),
                method="recorded_rates_median_v0",
                source_count=len(real_rows),
                confidence="high" if has_contract else "medium",
                basis={
                    "kind": "recorded_rates",
                    "amounts_usd": [_usd(a) for a in amounts],
                    "source_breakdown": {
                        s: sum(1 for r in real_rows if _text(r.get("source"), 40) == s)
                        for s in REAL_QUOTE_SOURCES
                        if any(_text(r.get("source"), 40) == s for r in real_rows)
                    },
                    "latest_recorded_at": _iso(real_rows[0].get("created_at")),
                    "note": "真实报价中位数(contract 在列则 confidence=high);基准兜底已被覆盖。",
                },
            )
            base["estimated_usd_low"] = base["low"]
            base["estimated_usd_high"] = base["high"]
            return base

        # 路径二:CPM 行业基准兜底(粉丝层级 x 平台 CPM x 均播放)
        followers = _int_or_none(pool.get("followers"))
        platform = _text(pool.get("platform"), 30).lower() or FALLBACK_PLATFORM
        views = _int_or_none(pool.get("avg_views"))
        view_source = "pool_avg_views"
        if not views or views <= 0:
            views = _evidence_avg_views(db, int(kol_pool_id))
            view_source = "evidence_avg_views"
        if (not views or views <= 0) and followers and followers > 0:
            rate = VIEW_RATE_BY_PLATFORM.get(platform, FALLBACK_VIEW_RATE)
            views = int(round(followers * rate))
            view_source = "follower_ratio_estimate"

        if not followers or followers <= 0 or not views or views <= 0:
            base["status"] = "empty"
            base["reason"] = (
                "无已录报价,且粉丝数/均播放均缺失,CPM 基准折算无从下手 — "
                "先补齐账号档案(followers/avg_views)或手动录入一条报价。"
            )
            base["basis"] = {
                "kind": "insufficient_data",
                "followers": followers,
                "avg_views": views,
                "rates_table_available": rates_table_ok,
            }
            return base

        tier = _tier_for_followers(followers)
        cpm_low, cpm_high = _cpm_range(platform, tier)
        low = _usd(views / 1000.0 * cpm_low)
        high = _usd(views / 1000.0 * cpm_high)
        base.update(
            status="ready",
            estimated_usd_p50=_usd((low + high) / 2.0),
            low=low,
            high=high,
            method="cpm_benchmark_v0",
            source_count=0,
            confidence="low",
            basis={
                "kind": "cpm_benchmark",
                "followers": followers,
                "tier": tier,
                "platform": platform,
                "views_used": views,
                "view_source": view_source,
                "cpm_usd_per_1k": [cpm_low, cpm_high],
                "formula": "views_used / 1000 x cpm_usd_per_1k",
                "benchmark_version": "行业基准 v0 待喂养校准",
                "rates_table_available": rates_table_ok,
                "note": "无真实报价的行业基准折算,仅供谈判锚点;录入真报价后自动被中位数覆盖。",
            },
        )
        base["estimated_usd_low"] = base["low"]
        base["estimated_usd_high"] = base["high"]
        return base
    except Exception as exc:  # noqa: BLE001 — 契约要求永不炸,诚实回原因
        logger.warning("estimate_rate failed for kol=%s: %s", kol_pool_id, exc)
        base["status"] = "error"
        base["reason"] = str(exc)[:300]
        return base


# ── CRUD:add_rate / list_rates ─────────────────────────────────────


def add_rate(
    kol_pool_id: int,
    payload: dict[str, Any],
    *,
    staff_id: int | None = None,
    conn: Any = None,
) -> dict[str, Any]:
    """手动录一条报价(写 vkpi_kol_rates 单表)。

    校验失败抛 ValueError(路由转 400);KOL 不存在抛 LookupError(路由转 404);
    表未建抛 RuntimeError(路由诚实回 error,提示先 apply 迁移211)。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {int(kol_pool_id)} not found")

    data = payload if isinstance(payload, dict) else {}
    amount = _float_or_none(data.get("amount_usd"))
    if amount is None or amount <= 0:
        raise ValueError("amount_usd 必须是大于 0 的数字(单位 USD)")
    if amount > 10_000_000:
        raise ValueError("amount_usd 超出合理上限(10,000,000 USD),疑似录入错误")

    source = _text(data.get("source"), 40).lower() or "negotiation"
    if source not in ALLOWED_SOURCES:
        raise ValueError(f"source 必须是 {'/'.join(ALLOWED_SOURCES)} 之一")

    confidence = _text(data.get("confidence"), 20).lower()
    if not confidence:
        confidence = DEFAULT_CONFIDENCE_BY_SOURCE.get(source, "medium")
    if confidence not in ALLOWED_CONFIDENCE:
        raise ValueError("confidence 必须是 low/medium/high 之一")

    platform = _text(data.get("platform"), 30).lower() or _text(pool.get("platform"), 30).lower()
    content_type = _text(data.get("content_type"), 60)
    currency = (_text(data.get("currency"), 10).upper() or "USD")
    if currency != "USD":
        raise ValueError("v0 仅支持 USD 口径(换汇请在录入前自行折算并在 note 注明原币种)")
    note = _text(data.get("note"), 500)

    effective_date: date | None = None
    raw_date = _text(data.get("effective_date"), 20)
    if raw_date:
        try:
            effective_date = date.fromisoformat(raw_date)
        except Exception as exc:
            raise ValueError("effective_date 格式必须是 YYYY-MM-DD") from exc

    try:
        row = db.execute(
            """
            INSERT INTO vkpi_kol_rates
                (kol_pool_id, platform, content_type, amount_usd, currency,
                 source, confidence, note, effective_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id, created_at
            """,
            (
                int(kol_pool_id),
                platform,
                content_type,
                _usd(amount),
                currency,
                source,
                confidence,
                note,
                effective_date,
            ),
        ).fetchone()
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 — 回滚失败不掩盖原始错误
            logger.debug("回滚失败(best-effort,不掩盖原始错误)", exc_info=True)
        raise RuntimeError(
            f"报价写入失败(vkpi_kol_rates 表可能未建,先 apply 迁移211):{str(exc)[:200]}"
        ) from exc

    created = dict(row) if row else {}
    if staff_id:
        logger.info("kol rate recorded: kol=%s amount_usd=%s source=%s by staff=%s",
                    kol_pool_id, _usd(amount), source, staff_id)
    return {
        "status": "ok",
        "id": _int_or_none(created.get("id")),
        "rate": {
            "kol_pool_id": int(kol_pool_id),
            "platform": platform,
            "content_type": content_type,
            "amount_usd": _usd(amount),
            "currency": currency,
            "source": source,
            "confidence": confidence,
            "note": note,
            "effective_date": _iso(effective_date),
            "created_at": _iso(created.get("created_at")),
        },
    }


def list_rates(kol_pool_id: int, *, limit: int = 100, conn: Any = None) -> dict[str, Any]:
    """该 KOL 已录报价列表(新在前);KOL 不存在抛 LookupError;表未建诚实 empty 不炸。"""
    from app.db.connection import get_conn

    db = conn or get_conn()
    pool = _load_pool_row(db, int(kol_pool_id))
    if not pool:
        raise LookupError(f"kol_pool {int(kol_pool_id)} not found")

    try:
        rows = _load_rate_rows(db, int(kol_pool_id), limit=max(1, min(int(limit), 500)))
    except Exception as exc:  # noqa: BLE001 — 表未建(迁移211未 apply)诚实空态
        logger.warning("list_rates table unavailable (kol=%s): %s", kol_pool_id, exc)
        return {
            "status": "empty",
            "kol_pool_id": int(kol_pool_id),
            "reason": "报价台账表未建(迁移 211 未 apply),暂无可列报价;估价走 CPM 基准兜底。",
            "items": [],
            "count": 0,
        }

    items = [
        {
            "id": _int_or_none(r.get("id")),
            "platform": _text(r.get("platform"), 30),
            "content_type": _text(r.get("content_type"), 60),
            "amount_usd": _float_or_none(r.get("amount_usd")),
            "currency": _text(r.get("currency"), 10) or "USD",
            "source": _text(r.get("source"), 40),
            "confidence": _text(r.get("confidence"), 20),
            "note": _text(r.get("note"), 500),
            "effective_date": _iso(r.get("effective_date")),
            "created_at": _iso(r.get("created_at")),
        }
        for r in rows
    ]
    if not items:
        return {
            "status": "empty",
            "kol_pool_id": int(kol_pool_id),
            "reason": "该 KOL 暂无已录报价 — 录入合同价/外联回复价后,估价将从行业基准切换为真报价中位数。",
            "items": [],
            "count": 0,
        }
    return {"status": "ready", "kol_pool_id": int(kol_pool_id), "items": items, "count": len(items)}
