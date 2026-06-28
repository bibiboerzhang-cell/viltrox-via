"""失败池治理(支撑层)：把 apify_jobs 里 status='failed' 的死信按
job_type × last_error_category 分桶,标注「可回收 vs 永久死」,并提供一个
**默认 dry_run** 的安全回收口:只把「可回收桶」的 failed job 重置回 queued。

设计口径(复用 worker 现有重试语义,不另造表、不碰红线 viltrox_fit_score):

- 分类词表完全沿用 app.workers.apify_jobs_worker_helpers._error_category 的输出
  族(provider_pressure / timeout / media_resolve / download / content_restricted /
  content_blocked / content_unavailable / permanent / stale_running / code_error /
  unknown)。本模块**不重新发明**类别,只把这些类别映射到「能否回收」。

- 回收口径与 worker maintenance 的 _adopt_recent_provider_pressure_failures 对齐:
  重置 = status→'queued' + 设 next_retry_at(给一个温和退避,避免回收即雪崩)+
  保留 attempts(不清零,继续受 max_attempts 闸门约束)。同时**额外**只回收
  attempts < max_attempts 的行 —— 已经把重试预算耗尽的死信不回收(否则纯浪费)。

- 安全网:DB 里持久化的 last_error_category 可能与真实 last_error 文本不一致
  (历史分类器版本漂移 / kol_auto_poll 的 ValueError 被标成 unknown)。回收前
  对每行用 _error_category(last_error) **重新派生**一次真实类别,以真实类别为准
  判定可回收性 —— 防止把「payload 校验失败」这类永久代码错误误回收。

红线:本模块零 fit 写;只读 + 仅把 failed→queued(回收口)。
"""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.workers.apify_jobs_worker_helpers import _error_category


logger = get_logger(__name__)


# ── 可回收 vs 永久死 的类别口径 ───────────────────────────────────────────────
# 「天气问题」:provider 限流 / 瞬时 5xx / 媒体解析抖动 / 下载超时 / 心跳过期被回收。
# 这些再跑一次有合理成功概率 → 可回收。
RECYCLABLE_CATEGORIES: frozenset[str] = frozenset(
    {
        "provider_pressure",  # 429 / 5xx / 限流 / 临时过载
        "timeout",            # gemini_call_timeout 等瞬时超时
        "media_resolve",      # media_resolve_failed:解析抖动,重试可恢复
        "download",           # yt-dlp / 直链下载超时,多为瞬时网络/代理问题
        "stale_running",      # 心跳过期被 reclaim,本质是被打断而非真失败
    }
)

# 「确定性死」:内容下架/门禁/不存在/不支持/代码缺陷 —— 再跑一万次也一样。
PERMANENT_CATEGORIES: frozenset[str] = frozenset(
    {
        "content_blocked",      # geo / DMCA / 封号 / 强制下架
        "content_restricted",   # 私密 / 年龄限制 / 登录墙(需凭证而非重试)
        "content_unavailable",  # 404 / 已删除 / 不存在
        "permanent",            # unsupported / invalid_video_url / not_video
        "code_error",           # ModuleNotFound / TypeError 等,要改代码
    }
)

# unknown 不在两表里:保守起见**不回收**(见 _is_recyclable_category)。
# 因为 unknown 往往藏着永久错(如 kol_auto_poll 的 payload 校验 ValueError)。


def _max_attempts() -> int:
    """回收只针对仍有重试预算的行。沿用 worker 的环境变量口径,默认 2。

    在函数体内读环境(而非模块顶层),与 worker maintaintenance 同样的运行期解析风格,
    避免导入期固化、也便于测试覆盖。
    """
    try:
        return max(1, int(os.environ.get("APIFY_WORKER_MAX_ATTEMPTS", "2")))
    except (TypeError, ValueError):
        return 2


def _recycle_backoff_seconds() -> int:
    """回收后给一个温和退避,避免一次性回收数百条立刻砸向 provider。默认 5 分钟。"""
    try:
        return max(0, int(os.environ.get("FAILED_POOL_RECYCLE_BACKOFF_SECONDS", "300")))
    except (TypeError, ValueError):
        return 300


def _is_recyclable_category(category: str) -> bool:
    """单一可回收性判据。unknown / 任何不认识的类别 → False(保守)。"""
    return category in RECYCLABLE_CATEGORIES


def _effective_category(stored_category: Any, last_error: Any) -> str:
    """以真实 last_error 文本重新派生类别为准,持久化列只作回退。

    防住「DB 里标 unknown 但其实是 content_unavailable / code_error」这类历史漂移:
    只要 last_error 文本能派生出一个**非 unknown** 的明确类别,就采信派生结果;
    否则回退到持久化的 last_error_category(再不行落 'unknown')。
    """
    text = str(last_error or "")
    derived = _error_category(text) if text else "unknown"
    if derived and derived != "unknown":
        return derived
    stored = str(stored_category or "").strip()
    return stored or "unknown"


def triage_report() -> dict[str, Any]:
    """按 job_type × last_error_category 分桶计数,并标注每桶可回收性。

    返回:
      {
        "total_failed": int,
        "recyclable_total": int,
        "permanent_total": int,
        "unknown_total": int,          # 既非明确可回收也非明确永久(保守不回收)
        "buckets": [
          {
            "job_type": str,
            "category": str,           # 以真实 last_error 重派生后的有效类别
            "count": int,
            "disposition": "recyclable" | "permanent" | "unknown",
            "recyclable": bool,
            "with_retry_budget": int,  # 该桶里 attempts < max_attempts 的行数(真正能回收的量)
          }, ...
        ],
      }
    分桶以「有效类别」(重派生)为准,而非裸持久化列 —— 报告即真相。
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
          COALESCE(job_type, '') AS job_type,
          COALESCE(last_error_category, '') AS stored_category,
          last_error,
          attempts
        FROM apify_jobs
        WHERE status = 'failed'
        """
    ).fetchall()

    max_attempts = _max_attempts()
    # 用真实类别重新分桶:(job_type, effective_category) -> {count, with_retry_budget}
    buckets: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        job_type = str(row["job_type"] or "")
        effective = _effective_category(row["stored_category"], row["last_error"])
        key = (job_type, effective)
        bucket = buckets.setdefault(key, {"count": 0, "with_retry_budget": 0})
        bucket["count"] += 1
        try:
            attempts = int(row["attempts"] or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts < max_attempts:
            bucket["with_retry_budget"] += 1

    out_buckets: list[dict[str, Any]] = []
    recyclable_total = permanent_total = unknown_total = 0
    for (job_type, category), agg in buckets.items():
        if _is_recyclable_category(category):
            disposition = "recyclable"
            recyclable_total += agg["count"]
        elif category in PERMANENT_CATEGORIES:
            disposition = "permanent"
            permanent_total += agg["count"]
        else:
            disposition = "unknown"
            unknown_total += agg["count"]
        out_buckets.append(
            {
                "job_type": job_type,
                "category": category,
                "count": agg["count"],
                "disposition": disposition,
                "recyclable": disposition == "recyclable",
                "with_retry_budget": agg["with_retry_budget"],
            }
        )

    out_buckets.sort(key=lambda b: (b["count"], b["job_type"]), reverse=True)
    total_failed = sum(b["count"] for b in out_buckets)
    return {
        "total_failed": total_failed,
        "recyclable_total": recyclable_total,
        "permanent_total": permanent_total,
        "unknown_total": unknown_total,
        "buckets": out_buckets,
    }


def recycle_recyclable(dry_run: bool = True, limit: int | None = None) -> dict[str, Any]:
    """只把「可回收桶」里仍有重试预算的 failed job 安全重置回 queued。

    口径(与 worker maintenance 的 provider-pressure 领养对齐):
      - 只选 status='failed' 且(真实重派生类别 ∈ RECYCLABLE_CATEGORIES)且
        attempts < max_attempts 的行;
      - 重置 = status→'queued',next_retry_at = NOW()+退避,updated_at=NOW();
        attempts **不清零**(继续受 max_attempts 约束),last_error 保留作审计;
        last_error_category 用重派生后的真实类别校正(顺手修脏)。

    dry_run=True(默认):**只返回将处理的清单,不写库**。
    dry_run=False:逐行回收(每行独立小事务,失败不影响其他行)。

    返回:
      {
        "dry_run": bool,
        "max_attempts": int,
        "backoff_seconds": int,
        "candidate_count": int,        # 命中可回收且有预算的总数
        "recycled_count": int,         # 实际重置(dry_run 恒为 0)
        "candidates": [{"id", "job_type", "category", "attempts"}...],  # dry_run 下为完整清单
      }
    """
    conn = get_conn()
    max_attempts = _max_attempts()
    backoff = _recycle_backoff_seconds()

    rows = conn.execute(
        """
        SELECT
          id,
          COALESCE(job_type, '') AS job_type,
          COALESCE(last_error_category, '') AS stored_category,
          last_error,
          attempts
        FROM apify_jobs
        WHERE status = 'failed'
          AND attempts < ?
        ORDER BY id DESC
        """,
        (max_attempts,),
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        effective = _effective_category(row["stored_category"], row["last_error"])
        if not _is_recyclable_category(effective):
            continue
        try:
            attempts = int(row["attempts"] or 0)
        except (TypeError, ValueError):
            attempts = 0
        candidates.append(
            {
                "id": int(row["id"]),
                "job_type": str(row["job_type"] or ""),
                "category": effective,
                "attempts": attempts,
            }
        )

    if limit is not None and limit >= 0:
        candidates = candidates[:limit]

    if dry_run:
        logger.info(
            "failed_pool dry_run | candidate_count=%s max_attempts=%s",
            len(candidates),
            max_attempts,
        )
        return {
            "dry_run": True,
            "max_attempts": max_attempts,
            "backoff_seconds": backoff,
            "candidate_count": len(candidates),
            "recycled_count": 0,
            "candidates": candidates,
        }

    recycled = 0
    for cand in candidates:
        try:
            conn.execute(
                """
                UPDATE apify_jobs
                SET status = 'queued',
                    last_error_category = ?,
                    next_retry_at = NOW() + make_interval(secs => ?),
                    updated_at = NOW()
                WHERE id = ?
                  AND status = 'failed'
                """,
                (cand["category"], backoff, cand["id"]),
            )
            try:
                conn.commit()
            except Exception:  # noqa: BLE001 — autocommit 环境无 commit,忽略
                logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
                pass
            recycled += 1
        except Exception as exc:  # noqa: BLE001 — 单行失败不拖垮整批
            logger.warning(
                "failed_pool recycle row failed | id=%s error=%s", cand["id"], exc
            )

    logger.warning(
        "failed_pool recycled | recycled_count=%s candidate_count=%s backoff_seconds=%s",
        recycled,
        len(candidates),
        backoff,
    )
    return {
        "dry_run": False,
        "max_attempts": max_attempts,
        "backoff_seconds": backoff,
        "candidate_count": len(candidates),
        "recycled_count": recycled,
        "candidates": candidates,
    }
