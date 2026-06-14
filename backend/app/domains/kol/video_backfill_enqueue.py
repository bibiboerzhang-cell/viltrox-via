"""Lane D(用户裁令「搜索时顺带懒抓」):把视频证据回填从「一次性全量 $660 批跑」改为
**搜索驱动的懒回填**——每次搜索召回库内候选时,对其中**缺视频证据**的头部少数(默认2)顺带
入队 account_deep 抓取,成本摊到未来、按需、自动优先「真被搜到的人」,显著省消耗。

控量四闸防烧爆:① 每次只取 top_n(默认2,clamp 1~5)② 只抓 has_video_evidence=false 的
③ 近期已抓(last_scrape_at < N 天)跳过,避免反复churn抓不到的号 ④ enqueue_profile_deep_crawl_job
按 URL 去重(同 URL 已 queued/running 不重入)。Apify 调用在 worker 侧走预算闸(闸A)。
零写 viltrox_fit_score、零改 rule_v0;唯一写=INSERT apify_jobs(account_deep 抓取)。
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol import search_sessions
from app.domains.kol import url_deep_crawl

DEFAULT_TOP_N = 2
MAX_TOP_N = 5
RESCRAPE_COOLDOWN_DAYS = 7  # 近期已抓 → 冷却期内不重抓(防反复 churn 抓不到的号)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _top_pool_candidates(session: dict[str, Any], top_n: int) -> list[int]:
    """从 session items 取库内候选(recall_candidate/existing_kol,有 kol_pool_id)头部 N 个 id。"""
    out: list[int] = []
    seen: set[int] = set()
    for item in session.get("items") or []:
        if str(item.get("item_type") or "") not in ("recall_candidate", "existing_kol"):
            continue
        kid = _int(item.get("kol_pool_id"))
        if kid <= 0 or kid in seen:
            continue
        seen.add(kid)
        out.append(kid)
        if len(out) >= top_n:
            break
    return out


def _eligible_for_backfill(conn: Any, kol_pool_ids: list[int]) -> list[dict[str, Any]]:
    """筛出缺视频证据、有 profile_url、且非近期已抓的候选(纯 SELECT,不触 fit)。"""
    wanted = [kid for kid in kol_pool_ids if kid and kid > 0]
    if not wanted:
        return []
    placeholders = ",".join("?" for _ in wanted)
    # 'http%' 与冷却时间戳都作参数传(% 在值里不在 SQL 字面,避开 psycopg 占位冲突);不用 INTERVAL/NOW。
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(days=int(RESCRAPE_COOLDOWN_DAYS))
    rows = conn.execute(
        f"""
        SELECT id, profile_url
        FROM vkpi_kol_pool
        WHERE id IN ({placeholders})
          AND COALESCE(has_video_evidence, FALSE) = FALSE
          AND profile_url IS NOT NULL AND lower(profile_url) LIKE ?
          AND (last_scrape_at IS NULL OR last_scrape_at < ?)
        """,
        (*wanted, "http%", cutoff),
    ).fetchall()
    return [dict(r) for r in rows]


def enqueue_lazy_video_backfill_for_session(
    *,
    session_id: int,
    top_n: int = DEFAULT_TOP_N,
    max_posts: int = 3,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对 session 头部**缺视频**的库内候选懒入队 account_deep 抓取(至多 top_n 个)。

    幂等/控量:缺视频 + 有 URL + 非近期已抓 + URL 未在队 → 才抓;成本摊到未来。
    返回入队明细;零写 fit,Apify 调用在 worker 走预算闸。
    """
    sid = int(session_id)
    safe_top_n = max(1, min(_int(top_n, DEFAULT_TOP_N), MAX_TOP_N))
    try:
        session = search_sessions.get_session(sid)
    except Exception:
        return {"status": "session_unavailable", "enqueued": 0, "items": []}
    pool_ids = _top_pool_candidates(session, safe_top_n)
    if not pool_ids:
        return {"status": "no_pool_candidates", "enqueued": 0, "items": []}

    conn = get_conn()
    try:
        eligible = _eligible_for_backfill(conn, pool_ids)
    except Exception:
        return {"status": "probe_failed", "enqueued": 0, "items": []}

    enqueued: list[dict[str, Any]] = []
    for row in eligible[:safe_top_n]:
        kid = _int(row.get("id"))
        url = str(row.get("profile_url") or "").strip()
        if not kid or not url:
            continue
        try:
            res = url_deep_crawl.enqueue_profile_deep_crawl_job(
                url, kol_pool_id=kid, max_posts=max_posts, staff=staff
            )
            enqueued.append({"kol_pool_id": kid, "status": res.get("status"), "job_id": res.get("job_id")})
        except Exception as exc:  # 单个失败不阻断,懒抓尽力而为
            enqueued.append({"kol_pool_id": kid, "status": "error", "error": str(exc)[:200]})
    queued_n = sum(1 for e in enqueued if e.get("status") == "queued")
    return {
        "status": "ok",
        "enqueued": queued_n,
        "considered": len(pool_ids),
        "eligible": len(eligible),
        "items": enqueued,
        "note": "搜索驱动懒回填:缺视频的库内候选顺带抓 account_deep,成本摊到未来(top_n={}, cooldown={}d)".format(
            safe_top_n, RESCRAPE_COOLDOWN_DAYS
        ),
    }


__all__ = ["enqueue_lazy_video_backfill_for_session", "DEFAULT_TOP_N", "MAX_TOP_N"]
