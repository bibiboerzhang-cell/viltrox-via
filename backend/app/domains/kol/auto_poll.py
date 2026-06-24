"""D3 · 关注 KOL 自动轮询:对收藏/已合作/高价值/进项目的 KOL 定期轻量刷 metadata。

裁令:被运营「盯着」的 KOL(收藏夹、进了项目、平台分层头部/腰部)其档案不该过期。
本模块每日挑出「有 last_seen_at 但超过 24 小时未更新」的优先级 KOL,best-effort 入队一条
轻量 metadata 刷新作业(job_type='kol_auto_poll');worker 端再去真抓——本模块绝不自己跑抓取。

口径(只用 vkpi_kol_pool 真实存在的列 + 已存在的关联表,缺表一律降级):
  - 优先级来源(任一即算「关注」):
      ① 收藏:vkpi_kol_pool_favorites.kol_pool_id(任意员工收藏即算);
      ② 进项目:vkpi_project_kol_assignments.kol_pool_id;
      ③ 高价值:vkpi_kol_pool.dashboard_tier IN ('头部','腰部')。
    上述表/列均 table_exists / information_schema 容错——缺则该来源跳过,不假设其存在。
  - 应轮询 = 上述任一来源命中 且 last_seen_at IS NOT NULL 且 last_seen_at < now-24h
    (有过快照、但已陈旧)。从未抓过(last_seen_at NULL)的不在本轮——那是首抓链,另算。
  - 排序:last_seen_at 最旧者优先(越久没刷越该刷)。

红线(逐条):
  - 绝不读写 viltrox_fit_score / viltrox_fit_reason / rule_v0;本模块零触评分。
  - 不自己烧 LLM、不真跑抓取;唯一写点是 best-effort INSERT apify_jobs(轻量作业)。
  - 队列缺失(apify_jobs 表不存在)→ 不入队,只诚实返回候选清单(available=False)。
  - 所有读表前 table_exists 守卫;任何异常 → 诚实降级(status/available=False),
    best-effort 写失败只 logger.warning 不抛。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

# 轻量轮询作业类型;worker 端可据此走「只刷 metadata」轻路径(不下视频/不烧 LLM)。
AUTO_POLL_JOB_TYPE = "kol_auto_poll"
# metadata 视为陈旧的阈值(小时);超过即应轮询。
STALE_AFTER_HOURS = 24
# 单次默认/最大候选与入队量,防一次刷爆队列。
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
# 高价值分层口径(dashboard_tier 真实值)。
HIGH_VALUE_TIERS = ("头部", "腰部")


def _int(value: Any, default: int = 0) -> int:
    """容错转 int(BOOLEAN 回来是 1/0、None、字符串均兜底)。"""
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _row_dict(row: Any) -> dict[str, Any]:
    """把游标行统一成 dict(兼容 dict / Row)。"""
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _priority_sources() -> dict[str, bool]:
    """探测哪些「关注」来源在本库可用(缺表则该来源不参与)。纯只读、全容错。"""
    return {
        "favorites": table_exists("vkpi_kol_pool_favorites"),
        "project_assignments": table_exists("vkpi_project_kol_assignments"),
        # 高价值口径只依赖 vkpi_kol_pool 自身列,只要主表在即可用。
        "high_value_tier": table_exists("vkpi_kol_pool"),
    }


def select_poll_candidates(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """从 vkpi_kol_pool 选出应轮询的关注 KOL(收藏/进项目/高价值,且 metadata 已陈旧)。

    返回每个候选的轻量画像:id/handle/platform/last_seen_at/dashboard_tier/priority_reasons。
    主表缺失或异常 → 返回 [](诚实降级)。排序:last_seen_at 最旧优先。
    """
    safe_limit = max(1, min(_int(limit, DEFAULT_LIMIT), MAX_LIMIT))
    if not table_exists("vkpi_kol_pool"):
        logger.warning("vkpi.kol.auto_poll.pool_table_missing")
        return []

    sources = _priority_sources()
    conn = get_conn()

    # 动态拼「关注」过滤:任一可用来源命中即算。无可用来源 → 空(诚实,不假设)。
    priority_clauses: list[str] = []
    if sources.get("favorites"):
        priority_clauses.append(
            "p.id IN (SELECT kol_pool_id FROM vkpi_kol_pool_favorites WHERE kol_pool_id IS NOT NULL)"
        )
    if sources.get("project_assignments"):
        priority_clauses.append(
            "p.id IN (SELECT kol_pool_id FROM vkpi_project_kol_assignments WHERE kol_pool_id IS NOT NULL)"
        )
    if sources.get("high_value_tier"):
        # 占位符承载分层值,避免把中文常量拼进 SQL 文本。
        tier_ph = ",".join("?" for _ in HIGH_VALUE_TIERS)
        priority_clauses.append(f"p.dashboard_tier IN ({tier_ph})")

    if not priority_clauses:
        logger.warning("vkpi.kol.auto_poll.no_priority_source_available")
        return []

    where_priority = " OR ".join(priority_clauses)
    # 陈旧口径:有过快照(last_seen_at 非空)且超过 N 小时未更新。
    # 阈值用占位符传(整数小时),区间用数据库侧 NOW() 计,避免应用侧时区漂移。
    sql = f"""
        SELECT p.id,
               p.handle,
               p.platform,
               p.display_name,
               p.dashboard_tier,
               p.last_seen_at,
               p.sync_status
        FROM vkpi_kol_pool p
        WHERE p.last_seen_at IS NOT NULL
          AND p.last_seen_at < NOW() - (? * INTERVAL '1 hour')
          AND ({where_priority})
        ORDER BY p.last_seen_at ASC
        LIMIT ?
    """
    params: list[Any] = [STALE_AFTER_HOURS]
    if sources.get("high_value_tier"):
        params.extend(HIGH_VALUE_TIERS)
    params.append(safe_limit)

    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    except Exception:
        logger.warning("vkpi.kol.auto_poll.select_candidates_failed", exc_info=True)
        return []

    candidate_ids = [_int(_row_dict(r).get("id")) for r in rows]
    candidate_ids = [cid for cid in candidate_ids if cid > 0]
    fav_ids = _ids_in_source(conn, "vkpi_kol_pool_favorites", candidate_ids) if sources.get("favorites") else set()
    proj_ids = (
        _ids_in_source(conn, "vkpi_project_kol_assignments", candidate_ids)
        if sources.get("project_assignments")
        else set()
    )

    out: list[dict[str, Any]] = []
    for r in rows:
        d = _row_dict(r)
        kid = _int(d.get("id"))
        if kid <= 0:
            continue
        reasons: list[str] = []
        if kid in fav_ids:
            reasons.append("favorite")
        if kid in proj_ids:
            reasons.append("in_project")
        if str(d.get("dashboard_tier") or "") in HIGH_VALUE_TIERS:
            reasons.append("high_value_tier")
        last_seen = d.get("last_seen_at")
        out.append(
            {
                "kol_pool_id": kid,
                "handle": d.get("handle"),
                "platform": d.get("platform"),
                "display_name": d.get("display_name"),
                "dashboard_tier": d.get("dashboard_tier"),
                "last_seen_at": last_seen.isoformat() if hasattr(last_seen, "isoformat") else last_seen,
                "sync_status": d.get("sync_status"),
                "priority_reasons": reasons or ["stale_priority"],
            }
        )
    return out


def _ids_in_source(conn: Any, table: str, candidate_ids: list[int]) -> set[int]:
    """给定候选 id,返回其中命中某关联表 kol_pool_id 的子集(标注 priority_reasons 用)。纯只读。"""
    wanted = [cid for cid in candidate_ids if cid and cid > 0]
    if not wanted or not table_exists(table):
        return set()
    placeholders = ",".join("?" for _ in wanted)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT kol_pool_id FROM {table} WHERE kol_pool_id IN ({placeholders})",
            tuple(wanted),
        ).fetchall()
    except Exception:
        logger.warning("vkpi.kol.auto_poll.source_probe_failed table=%s", table, exc_info=True)
        return set()
    return {_int(_row_dict(r).get("kol_pool_id")) for r in rows if _int(_row_dict(r).get("kol_pool_id")) > 0}


def _already_queued_ids(conn: Any, candidate_ids: list[int]) -> set[int]:
    """已有 queued/running/retrying 的 kol_auto_poll 作业的 kol_pool_id(去重,防重复入队)。纯只读。"""
    wanted = [cid for cid in candidate_ids if cid and cid > 0]
    if not wanted or not table_exists("apify_jobs"):
        return set()
    try:
        rows = conn.execute(
            """
            SELECT (payload->>'kol_pool_id') AS kid
            FROM apify_jobs
            WHERE job_type = ?
              AND status IN ('queued', 'running', 'retrying')
            """,
            (AUTO_POLL_JOB_TYPE,),
        ).fetchall()
    except Exception:
        logger.warning("vkpi.kol.auto_poll.dedupe_probe_failed", exc_info=True)
        return set()
    out: set[int] = set()
    wanted_set = set(wanted)
    for r in rows:
        kid = _int(_row_dict(r).get("kid"))
        if kid in wanted_set:
            out.add(kid)
    return out


def enqueue_auto_poll(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """对应轮询的关注 KOL best-effort 入队一条轻量 metadata 刷新作业。

    队列(apify_jobs)在 → 逐个 INSERT queued 轻量作业(已在队列的跳过、去重);
    队列缺失 → 不入队,只返回候选清单(available=False,诚实降级)。
    绝不真跑抓取、绝不触 viltrox_fit_score。staff 仅作 triggered_by 标注(无登录人时为 None)。
    """
    candidates = select_poll_candidates(DEFAULT_LIMIT)
    triggered_by = None
    if isinstance(staff, dict):
        triggered_by = staff.get("staff_id") or staff.get("id") or staff.get("user_id")

    base = {
        "status": "ok",
        "job_type": AUTO_POLL_JOB_TYPE,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "viltrox_fit_score_untouched": True,
    }

    if not candidates:
        base["status"] = "nothing_to_poll"
        base["enqueued"] = []
        base["enqueued_count"] = 0
        base["queue_available"] = table_exists("apify_jobs")
        base["writes"] = []
        return base

    if not table_exists("apify_jobs"):
        # 队列不可用:不入队,诚实返回候选,标 available=False。
        base["status"] = "queue_unavailable"
        base["queue_available"] = False
        base["enqueued"] = []
        base["enqueued_count"] = 0
        base["writes"] = []
        return base

    conn = get_conn()
    candidate_ids = [_int(c.get("kol_pool_id")) for c in candidates]
    already = _already_queued_ids(conn, candidate_ids)

    enqueued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for cand in candidates:
        kid = _int(cand.get("kol_pool_id"))
        if kid <= 0:
            continue
        if kid in already:
            skipped.append({"kol_pool_id": kid, "reason": "already_queued"})
            continue
        payload = {
            "kol_pool_id": kid,
            "mode": "metadata_light",
            "handle": cand.get("handle"),
            "platform": cand.get("platform"),
            "priority_reasons": cand.get("priority_reasons"),
            "last_seen_at": cand.get("last_seen_at"),
            "trigger": "auto_poll_daily",
            "triggered_by_staff_id": triggered_by,
            "summary": f"关注KOL轻量轮询 · KOL {kid}",
            "viltrox_fit_score_untouched": True,
        }
        try:
            import json

            row = conn.execute(
                """
                INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
                VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
                RETURNING id
                """,
                (AUTO_POLL_JOB_TYPE, json.dumps(payload, ensure_ascii=False)),
            ).fetchone()
            conn.commit()
            enqueued.append({"kol_pool_id": kid, "job_id": _row_dict(row).get("id")})
        except Exception as exc:
            logger.warning("vkpi.kol.auto_poll.insert_failed kid=%s err=%s", kid, exc, exc_info=True)
            skipped.append({"kol_pool_id": kid, "reason": "enqueue_failed", "error": str(exc)[:300]})

    base["status"] = "queued" if enqueued else "nothing_to_queue"
    base["queue_available"] = True
    base["enqueued"] = enqueued
    base["enqueued_count"] = len(enqueued)
    base["skipped"] = skipped
    base["writes"] = ["apify_jobs"] if enqueued else []
    return base


def auto_poll_status() -> dict[str, Any]:
    """只读状态:有多少应轮询候选、队列是否可用、当前在队的 kol_auto_poll 作业数。

    任何缺表/异常 → available=False 的诚实降级,绝不抛。供 GET .../auto-poll/status 直读。
    """
    out: dict[str, Any] = {
        "available": True,
        "job_type": AUTO_POLL_JOB_TYPE,
        "stale_after_hours": STALE_AFTER_HOURS,
        "high_value_tiers": list(HIGH_VALUE_TIERS),
        "priority_sources": _priority_sources(),
        "viltrox_fit_score_untouched": True,
    }
    if not table_exists("vkpi_kol_pool"):
        out["available"] = False
        out["reason"] = "vkpi_kol_pool_missing"
        return out

    try:
        candidates = select_poll_candidates(MAX_LIMIT)
        out["candidate_count"] = len(candidates)
        out["candidates_preview"] = candidates[:10]
    except Exception:
        logger.warning("vkpi.kol.auto_poll.status_candidate_count_failed", exc_info=True)
        out["candidate_count"] = None

    queue_available = table_exists("apify_jobs")
    out["queue_available"] = queue_available
    if queue_available:
        try:
            row = get_conn().execute(
                """
                SELECT count(*) AS n
                FROM apify_jobs
                WHERE job_type = ?
                  AND status IN ('queued', 'running', 'retrying')
                """,
                (AUTO_POLL_JOB_TYPE,),
            ).fetchone()
            out["in_queue"] = _int(_row_dict(row).get("n"))
        except Exception:
            logger.warning("vkpi.kol.auto_poll.status_queue_count_failed", exc_info=True)
            out["in_queue"] = None
    else:
        out["in_queue"] = 0
    return out


__all__ = [
    "AUTO_POLL_JOB_TYPE",
    "STALE_AFTER_HOURS",
    "DEFAULT_LIMIT",
    "select_poll_candidates",
    "enqueue_auto_poll",
    "auto_poll_status",
]
