"""发现即建档(B+A 合体,2026-07-07 用户裁令):新 KOL 入库后按相关度分档自动点火完整档案。

- full(高相关,score>=0.6 且未降档):深爬(3 帖)+ 评论采集——深爬自动衍生 dossier/代表作深析/契合,
  评论完成自动刷新受众 → 约 3-5 分钟出满血档案,约 $0.1/人;
- light(中相关/相关):深爬(1 帖)——基础档案 + 1 条代表作,约 $0.03/人;
- 手动升级:任意 KOL 可经 build_full_profile() 强制 full(抽屉「一键补全」)。

纪律:全链幂等(下游入队函数各自去重,评论入队本模块自查 queued/running);
预算闸在 worker 侧兜底;env KOL_DISCOVERY_BUILDOUT=0 可整体关闭(默认开);
评论任务标 batch=on_demand_batch 走批量档,绝不插队用户交互任务。
红线:零触 viltrox_fit_score;全部复用既有入队函数,不新增任何抓取/LLM 直调。
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

FULL_TIER_SCORE = 0.6


def _enabled() -> bool:
    return str(os.environ.get("KOL_DISCOVERY_BUILDOUT", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _profile_url(kol_pool_id: int) -> str:
    row = get_conn().execute(
        "SELECT profile_url FROM vkpi_kol_pool WHERE id = ?", (int(kol_pool_id),)
    ).fetchone()
    return str(((dict(row).get("profile_url")) if row else "") or "").strip()


def _enqueue_comments_collect(kol_pool_id: int, *, source: str) -> dict[str, Any]:
    """评论采集入队(幂等:同 KOL 已有 queued/running 即复用)。payload 契约与 worker
    run_kol_pool_comments_for_job 一致;batch=on_demand_batch 走批量档不插队交互。"""
    conn = get_conn()
    existing = conn.execute(
        """
        SELECT id, status FROM apify_jobs
        WHERE job_type = 'kol_pool_comments_collect'
          AND status IN ('queued', 'running')
          AND payload->>'kol_pool_id' = ?
        ORDER BY id DESC LIMIT 1
        """,
        (str(int(kol_pool_id)),),
    ).fetchone()
    if existing:
        d = dict(existing)
        return {"status": "already_queued", "job_id": int(d["id"])}
    # 90s 延迟:评论必须等深爬把 evidence 落地(掐表实测 race——同时入队时评论 job 早 1-7 秒
    # 空转 done,0 评论 → refresh_audience_stats 永不触发,受众页签永不亮)。深爬实测 5-17s,90s 稳。
    conn.execute(
        "INSERT INTO apify_jobs (job_type, payload, next_retry_at) VALUES (?, ?, NOW() + make_interval(secs => 90))",
        (
            "kol_pool_comments_collect",
            json.dumps(
                {"kol_pool_id": int(kol_pool_id), "batch": "on_demand_batch", "source": source},
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    return {"status": "queued"}


def ignite_profile_buildout(
    kol_pool_id: int,
    *,
    score: float = 0.0,
    demoted: bool = False,
    force_full: bool = False,
    source: str = "discovery",
) -> dict[str, Any]:
    """按档点火。返回 {"tier": "full"|"light"|"skipped", ...};best-effort,任何失败不抛。"""
    result: dict[str, Any] = {"kol_pool_id": int(kol_pool_id), "tier": "skipped"}
    if not _enabled():
        result["reason"] = "env_off"
        return result
    try:
        url = _profile_url(int(kol_pool_id))
        if not url:
            result["reason"] = "no_profile_url"
            return result
        full = bool(force_full) or (float(score or 0) >= FULL_TIER_SCORE and not demoted)
        from app.domains.kol import url_deep_crawl

        crawl = url_deep_crawl.enqueue_profile_deep_crawl_job(
            url,
            kol_pool_id=int(kol_pool_id),
            max_posts=3 if full else 1,
            staff=None,
            queue_lane="interactive" if source == "manual_build_full" else "batch",
        )
        result["deep_crawl"] = {"status": str((crawl or {}).get("status") or "queued")}
        if full:
            result["comments"] = _enqueue_comments_collect(int(kol_pool_id), source=source)
        result["tier"] = "full" if full else "light"
        logger.info(
            "discovery buildout ignited | kol=%s tier=%s score=%.3f source=%s",
            kol_pool_id, result["tier"], float(score or 0), source,
        )
    except Exception:
        logger.warning("discovery buildout ignite failed kol=%s(不阻断主流程)", kol_pool_id, exc_info=True)
        result["reason"] = "error"
    return result


def build_full_profile(kol_pool_id: int) -> dict[str, Any]:
    """手动一键补全:强制 full 档(抽屉按钮/API 用)。"""
    return ignite_profile_buildout(int(kol_pool_id), force_full=True, source="manual_build_full")
