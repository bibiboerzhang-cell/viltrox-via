"""I1 · API Token Broker(配额/冷却/健康调度)。

为多 provider(gemini/claude/openai/apify)做"按 label 引用"的 token 调度位:
- pick_token() 给出最优可用 label(启用 + 未冷却 + used<quota,按 used_today 升序);
- record_use()/mark_cooldown()/reset_daily() 维护配额、冷却、每日复位;
- broker_status() 汇总各 provider 的可用/冷却/耗尽计数。

【安全红线】表里【绝不】存原始密钥/token 明文;只存 label(引用名)。真实密钥仍在
env/secrets,使用方拿到 pick_token() 给的 label 后自行按 label 解析真实 key。本模块
不接触任何 key 字节,也不写任何 key 进日志。

约定:get_conn() 取连接;占位符用 ?;BOOLEAN 读回是 int 1/0,用 truthy 容错;
读表前 table_exists() 守卫,缺表/异常诚实降级;best-effort 写失败只 warning 不抛。
红线:绝不读写 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists

logger = get_logger(__name__)

_TABLE = "vkpi_api_tokens"

# provider 白名单(同 migration 186 的 CHECK 约束)。
ALLOWED_PROVIDERS = ("gemini", "claude", "openai", "apify")

# health 取值白名单。
HEALTH_OK = "ok"
HEALTH_COOLDOWN = "cooldown"
HEALTH_EXHAUSTED = "exhausted"


# ---------------------------------------------------------------------------
# 时间 / 布尔适配
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_str() -> str:
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _db_bool(value: Any) -> bool | int:
    """PG 用 Python bool;SQLite 用 1/0。"""
    return bool(value) if is_postgres_runtime() else (1 if value else 0)


def _truthy(value: Any) -> bool:
    """BOOLEAN 列读回可能是 int 1/0 / 't' / 'true' / True,统一判真。"""
    return value in (1, True, "t", "true", "1")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: Any) -> datetime | None:
    """把 cooldown_until 读回值(字符串 / datetime / None)解析为 aware datetime。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# SQLite 自愈 schema(镜像 186 的 PG DDL;PG runtime 直接 return)
# ---------------------------------------------------------------------------
def ensure_schema() -> None:
    """SQLite 本地自愈建表;PG runtime 走迁移,直接返回。失败只 warning 不抛。"""
    if is_postgres_runtime():
        return
    try:
        conn = get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                label TEXT NOT NULL,
                quota_daily INTEGER NOT NULL DEFAULT 0,
                used_today INTEGER NOT NULL DEFAULT 0,
                cost_cents_today INTEGER NOT NULL DEFAULT 0,
                cooldown_until TEXT,
                health TEXT NOT NULL DEFAULT 'ok',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CONSTRAINT vkpi_api_tokens_provider_chk
                    CHECK (provider IN ('gemini','claude','openai','apify')),
                CONSTRAINT vkpi_api_tokens_health_chk
                    CHECK (health IN ('ok','cooldown','exhausted')),
                CONSTRAINT vkpi_api_tokens_quota_chk CHECK (quota_daily >= 0),
                CONSTRAINT vkpi_api_tokens_label_chk CHECK (length(trim(label)) > 0),
                CONSTRAINT vkpi_api_tokens_uq UNIQUE (provider, label)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_vkpi_api_tokens_provider_health
                ON vkpi_api_tokens(provider, health, enabled)
            """
        )
        conn.commit()
    except Exception as exc:
        logger.warning("token_broker SQLite schema 自愈失败(降级继续): %s", exc)


# ---------------------------------------------------------------------------
# 行 -> 公共 payload(本表本就无密钥,直接出)
# ---------------------------------------------------------------------------
def _row_to_public(row: Any) -> dict[str, Any]:
    item = dict(row)
    return {
        "id": _int(item.get("id")),
        "provider": item.get("provider") or "",
        "label": item.get("label") or "",
        "quota_daily": _int(item.get("quota_daily")),
        "used_today": _int(item.get("used_today")),
        "cost_cents_today": _int(item.get("cost_cents_today")),
        "cooldown_until": item.get("cooldown_until") or None,
        "health": item.get("health") or HEALTH_OK,
        "enabled": _truthy(item.get("enabled")),
        "updated_at": item.get("updated_at") or "",
    }


# ---------------------------------------------------------------------------
# register_token:登记一个 token 引用位(provider + label,只存引用名)
# ---------------------------------------------------------------------------
def register_token(provider: str, label: str, quota_daily: int = 0) -> dict[str, Any]:
    """登记 / 更新一个 token 引用位(只存 label,绝不存原始密钥)。

    UNIQUE(provider,label) 幂等:已存在则刷新 quota_daily 与 enabled,不重置用量。
    返回脱敏 payload;参数非法或缺表则 {ok:False}。
    """
    provider = str(provider or "").strip().lower()
    label = str(label or "").strip()
    quota = max(0, _int(quota_daily, 0))
    if provider not in ALLOWED_PROVIDERS:
        return {"ok": False, "error": f"unsupported provider (allowed: {', '.join(ALLOWED_PROVIDERS)})"}
    if not label:
        return {"ok": False, "error": "label required"}

    ensure_schema()
    if not table_exists(_TABLE):
        return {"ok": False, "error": "token broker table unavailable"}

    now = _utcnow_str()
    conn = get_conn()
    try:
        existing = conn.execute(
            f"SELECT id FROM {_TABLE} WHERE provider=? AND label=?",
            (provider, label),
        ).fetchone()
        if existing:
            conn.execute(
                f"""
                UPDATE {_TABLE}
                SET quota_daily=?, enabled=?, updated_at=?
                WHERE provider=? AND label=?
                """,
                (quota, _db_bool(True), now, provider, label),
            )
        else:
            conn.execute(
                f"""
                INSERT INTO {_TABLE}
                    (provider, label, quota_daily, used_today, cost_cents_today,
                     cooldown_until, health, enabled, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    provider,
                    label,
                    quota,
                    0,
                    0,
                    None,
                    HEALTH_OK,
                    _db_bool(True),
                    now,
                    now,
                ),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("token_broker register_token 失败(provider=%s): %s", provider, exc)
        return {"ok": False, "error": "register failed"}

    row = conn.execute(
        f"""
        SELECT id, provider, label, quota_daily, used_today, cost_cents_today,
               cooldown_until, health, enabled, updated_at
        FROM {_TABLE} WHERE provider=? AND label=?
        """,
        (provider, label),
    ).fetchone()
    result = _row_to_public(row) if row else {}
    result["ok"] = bool(row)
    return result


# ---------------------------------------------------------------------------
# pick_token:挑最优可用 label(enabled + 未冷却 + used<quota,按 used_today 升序)
# ---------------------------------------------------------------------------
def pick_token(provider: str) -> str | None:
    """返回该 provider 最优可用 token 的 label;无可用返回 None。

    可用 = enabled 真 且 (cooldown_until 为空或已过) 且 (quota_daily<=0 视为无限,
    否则 used_today<quota_daily)。排序:used_today 升序优先用得最少的,再 id 升序稳定。
    缺表/异常诚实降级返回 None。绝不返回任何 key 字节。
    """
    provider = str(provider or "").strip().lower()
    if provider not in ALLOWED_PROVIDERS:
        return None
    ensure_schema()
    if not table_exists(_TABLE):
        return None
    try:
        rows = get_conn().execute(
            f"""
            SELECT id, label, quota_daily, used_today, cooldown_until, enabled
            FROM {_TABLE}
            WHERE provider=? AND enabled=?
            ORDER BY used_today ASC, id ASC
            """,
            (provider, _db_bool(True)),
        ).fetchall()
    except Exception as exc:
        logger.warning("token_broker pick_token 查询失败(provider=%s): %s", provider, exc)
        return None

    now = _utcnow()
    for row in rows:
        item = dict(row)
        if not _truthy(item.get("enabled")):
            continue
        cooldown = _parse_dt(item.get("cooldown_until"))
        if cooldown is not None and cooldown > now:
            continue
        quota = _int(item.get("quota_daily"))
        used = _int(item.get("used_today"))
        if quota > 0 and used >= quota:
            continue
        return str(item.get("label") or "") or None
    return None


# ---------------------------------------------------------------------------
# record_use:记一次使用(used_today+1,叠加 cost),触配额则置 exhausted
# ---------------------------------------------------------------------------
def record_use(label: str, cost_cents: int = 0) -> dict[str, Any]:
    """对某 label 记一次使用:used_today+1、cost_cents_today 累加。

    用量触达 quota_daily(>0)时把 health 标 'exhausted'。best-effort,失败只 warning。
    label 可能跨 provider 复用?本表 UNIQUE(provider,label),按 label 更新全部命中行。
    """
    label = str(label or "").strip()
    cost = max(0, _int(cost_cents, 0))
    if not label:
        return {"ok": False, "error": "label required"}
    ensure_schema()
    if not table_exists(_TABLE):
        return {"ok": False, "error": "token broker table unavailable"}

    now = _utcnow_str()
    conn = get_conn()
    try:
        cursor = conn.execute(
            f"""
            UPDATE {_TABLE}
            SET used_today = used_today + 1,
                cost_cents_today = cost_cents_today + ?,
                updated_at = ?
            WHERE label = ?
            """,
            (cost, now, label),
        )
        # 触配额 -> exhausted(只对设了正配额且已达标且当前为 ok 的行)
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET health = ?, updated_at = ?
            WHERE label = ? AND quota_daily > 0 AND used_today >= quota_daily
                  AND health = ?
            """,
            (HEALTH_EXHAUSTED, now, label, HEALTH_OK),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("token_broker record_use 失败(label=%s): %s", label, exc)
        return {"ok": False, "error": "record failed"}

    updated = _int(getattr(cursor, "rowcount", 0))
    return {"ok": updated > 0, "updated": updated}


# ---------------------------------------------------------------------------
# mark_cooldown:把某 label 冷却 N 秒,health -> 'cooldown'
# ---------------------------------------------------------------------------
def mark_cooldown(label: str, seconds: int) -> dict[str, Any]:
    """把某 label 冷却 seconds 秒:写 cooldown_until=now+seconds、health='cooldown'。

    seconds<=0 视作"立即解冷却"(清 cooldown_until + health 复 ok)。失败只 warning。
    """
    label = str(label or "").strip()
    secs = _int(seconds, 0)
    if not label:
        return {"ok": False, "error": "label required"}
    ensure_schema()
    if not table_exists(_TABLE):
        return {"ok": False, "error": "token broker table unavailable"}

    now = _utcnow_str()
    conn = get_conn()
    try:
        if secs <= 0:
            cursor = conn.execute(
                f"""
                UPDATE {_TABLE}
                SET cooldown_until = NULL, health = ?, updated_at = ?
                WHERE label = ? AND health = ?
                """,
                (HEALTH_OK, now, label, HEALTH_COOLDOWN),
            )
        else:
            until = (_utcnow() + timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%SZ")
            cursor = conn.execute(
                f"""
                UPDATE {_TABLE}
                SET cooldown_until = ?, health = ?, updated_at = ?
                WHERE label = ?
                """,
                (until, HEALTH_COOLDOWN, now, label),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("token_broker mark_cooldown 失败(label=%s): %s", label, exc)
        return {"ok": False, "error": "cooldown failed"}

    updated = _int(getattr(cursor, "rowcount", 0))
    return {"ok": updated > 0, "updated": updated, "cooldown_seconds": max(0, secs)}


# ---------------------------------------------------------------------------
# reset_daily:每日复位 used_today/cost_cents_today,清因配额耗尽的健康态
# ---------------------------------------------------------------------------
def reset_daily() -> dict[str, Any]:
    """每日复位:used_today=0、cost_cents_today=0。

    把因配额耗尽(exhausted)的 health 复 'ok';冷却中(cooldown)的不动,交给
    pick_token 按 cooldown_until 过期自然恢复。供调度器每日 00:00 调。失败只 warning。
    """
    ensure_schema()
    if not table_exists(_TABLE):
        return {"ok": False, "error": "token broker table unavailable"}

    now = _utcnow_str()
    conn = get_conn()
    try:
        cursor = conn.execute(
            f"""
            UPDATE {_TABLE}
            SET used_today = 0,
                cost_cents_today = 0,
                health = CASE WHEN health = ? THEN ? ELSE health END,
                updated_at = ?
            """,
            (HEALTH_EXHAUSTED, HEALTH_OK, now),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("token_broker reset_daily 失败: %s", exc)
        return {"ok": False, "error": "reset failed"}

    return {"ok": True, "reset_count": _int(getattr(cursor, "rowcount", 0))}


# ---------------------------------------------------------------------------
# broker_status:各 provider 可用/冷却/耗尽计数(只读看板)
# ---------------------------------------------------------------------------
def broker_status() -> dict[str, Any]:
    """各 provider 的 token 健康汇总:available / cooldown / exhausted / disabled / total。

    available 按运行时口径计算(enabled 且未冷却且未达配额),非仅看 health 列,
    保证与 pick_token 的"可用"定义一致。缺表/异常诚实降级返回 available=False。
    """
    ensure_schema()
    if not table_exists(_TABLE):
        return {"available": False, "reason": "token broker table unavailable", "providers": {}}

    try:
        rows = get_conn().execute(
            f"""
            SELECT provider, label, quota_daily, used_today, cost_cents_today,
                   cooldown_until, health, enabled
            FROM {_TABLE}
            ORDER BY provider ASC, label ASC
            """
        ).fetchall()
    except Exception as exc:
        logger.warning("token_broker broker_status 查询失败: %s", exc)
        return {"available": False, "reason": "query failed", "providers": {}}

    now = _utcnow()
    providers: dict[str, dict[str, Any]] = {
        p: {
            "total": 0,
            "available": 0,
            "cooldown": 0,
            "exhausted": 0,
            "disabled": 0,
            "cost_cents_today": 0,
        }
        for p in ALLOWED_PROVIDERS
    }
    totals = {"total": 0, "available": 0, "cooldown": 0, "exhausted": 0, "disabled": 0, "cost_cents_today": 0}

    for row in rows:
        item = dict(row)
        provider = str(item.get("provider") or "")
        bucket = providers.setdefault(
            provider,
            {"total": 0, "available": 0, "cooldown": 0, "exhausted": 0, "disabled": 0, "cost_cents_today": 0},
        )
        cost = _int(item.get("cost_cents_today"))
        bucket["total"] += 1
        bucket["cost_cents_today"] += cost
        totals["total"] += 1
        totals["cost_cents_today"] += cost

        if not _truthy(item.get("enabled")):
            bucket["disabled"] += 1
            totals["disabled"] += 1
            continue

        cooldown = _parse_dt(item.get("cooldown_until"))
        is_cooling = cooldown is not None and cooldown > now
        quota = _int(item.get("quota_daily"))
        used = _int(item.get("used_today"))
        is_exhausted = quota > 0 and used >= quota

        if is_cooling:
            bucket["cooldown"] += 1
            totals["cooldown"] += 1
        elif is_exhausted:
            bucket["exhausted"] += 1
            totals["exhausted"] += 1
        else:
            bucket["available"] += 1
            totals["available"] += 1

    return {
        "available": True,
        "generated_at": _utcnow_str(),
        "providers": providers,
        "totals": totals,
    }
