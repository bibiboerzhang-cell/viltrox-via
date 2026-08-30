"""失败池治理(支撑层)：把 apify_jobs 里死信(status='failed' 与 'triage')按
job_type × last_error_category 分桶,标注「可回收 vs 永久死」,并提供一个
**默认 dry_run + 批量上限闸** 的安全排水口:只把「可回收桶」的死信重置回 queued,
让 worker 真去重跑——但默认不写库、且单次最多放 batch_limit 条,绝不一次性把整池放出去砸预算。

排水覆盖两个死信状态:
  - status='failed':历史死信(triage 引擎接线前,或 unknown 类保守落点)。
  - status='triage' :worker 失败瞬间分流落点(含「可重试类但重试预算耗尽」的行)。
两者都可能含「天气问题」类(download/media_resolve/timeout/provider_pressure),都该能受控排水。

设计口径(复用 worker 现有重试语义,不另造表、不碰红线 viltrox_fit_score):

- 分类词表完全沿用 app.platform.provider_error_category.error_category 的输出
  族(provider_pressure / timeout / media_resolve / download / content_restricted /
  content_blocked / content_unavailable / permanent / stale_running / code_error /
  unknown)。本模块**不重新发明**类别,只把这些类别映射到「能否回收」。

- 回收口径与 worker maintenance 的 _adopt_recent_provider_pressure_failures 对齐:
  重置 = status→'queued' + 设 next_retry_at(给一个温和退避,避免回收即雪崩)。
  attempts 处理分两档(默认保守):
    * reset_attempts=False(默认):**保留 attempts**(不清零,继续受 max_attempts 约束),
      且只回收 attempts < max_attempts 的行 —— 已经把重试预算耗尽的死信**不回收**(否则纯浪费)。
    * reset_attempts=True(显式放量):**清零 attempts=0**,让重试预算耗尽的「天气问题」死信
      也能被 worker 再领一次。这是真会重新烧抓取/LLM 预算的放量动作,必须配 batch_limit 上限。

- 批量上限(batch_limit):单次排水默认只放 FAILED_POOL_RECYCLE_BATCH_LIMIT(默认 20)条,
  硬上限 FAILED_POOL_RECYCLE_BATCH_MAX(默认 100)封顶,防止误传超大 limit 把整池(数百条)
  一次性放出去砸爆 Apify / LLM 预算。dry_run 也受同一上限约束(清单即真实将放出的量)。

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
from app.platform.provider_error_category import error_category


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


# 排水覆盖的死信状态:'failed'(历史/unknown 落点)+ 'triage'(失败瞬间分流落点,含耗尽的可重试类)。
# 用元组而非动态字符串,IN 子句里直接内联这些**受控字面量**(非用户输入),compat 合规。
DRAINABLE_STATUSES: tuple[str, ...] = ("failed", "triage")


def _batch_max() -> int:
    """单次排水的**硬上限**:任何 batch_limit(含 env 默认/调用方显式传入)都不得超过它。
    封住「误传 limit=10000 把整池放出去」。默认 100;可经 env 收紧/放宽,但仍 >=1。
    """
    try:
        return max(1, int(os.environ.get("FAILED_POOL_RECYCLE_BATCH_MAX", "100")))
    except (TypeError, ValueError):
        return 100


def _default_batch_limit() -> int:
    """调用方未显式传 batch_limit 时的默认单次放量。默认 20,且不超过硬上限。"""
    try:
        configured = int(os.environ.get("FAILED_POOL_RECYCLE_BATCH_LIMIT", "20"))
    except (TypeError, ValueError):
        configured = 20
    return max(1, min(configured, _batch_max()))


def _effective_batch_limit(batch_limit: int | None) -> int:
    """把调用方传入的 batch_limit 收敛到 [1, batch_max] 区间;None → env 默认。

    口径:None 用 _default_batch_limit();显式值钳到 [1, _batch_max()]。
    保证「无论怎么调用,单次排水都不可能超过硬上限」—— 这是预算安全的最后一道闸。
    """
    hard_max = _batch_max()
    if batch_limit is None:
        return _default_batch_limit()
    try:
        value = int(batch_limit)
    except (TypeError, ValueError):
        return _default_batch_limit()
    return max(1, min(value, hard_max))


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
    derived = error_category(text) if text else "unknown"
    if derived and derived != "unknown":
        return derived
    stored = str(stored_category or "").strip()
    return stored or "unknown"


def triage_report() -> dict[str, Any]:
    """按 job_type × last_error_category 分桶计数,并标注每桶可回收性。

    口径覆盖两个死信状态(status IN ('failed','triage')),让报告即真相:不论死信
    停在历史 failed 还是新的 triage 落点,都被纳入分桶。total_failed 含两态总量。

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
        WHERE status IN ('failed', 'triage')
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


def recycle_recyclable(
    dry_run: bool = True,
    limit: int | None = None,
    *,
    batch_limit: int | None = None,
    reset_attempts: bool = False,
) -> dict[str, Any]:
    """把「可回收桶」里的死信(status IN ('failed','triage'))受控重置回 queued,让 worker 再跑。

    口径(与 worker maintenance 的 provider-pressure 领养对齐):
      - 只选 status ∈ ('failed','triage') 且(真实重派生类别 ∈ RECYCLABLE_CATEGORIES)的行;
      - reset_attempts=False(默认):额外只取 attempts < max_attempts 的行(仍有重试预算),
        重置时 **保留 attempts**(继续受 max_attempts 约束);
      - reset_attempts=True(显式放量):不再卡 attempts 预算,**清零 attempts=0** 让耗尽的
        「天气问题」死信也能被 worker 再领一次(真会重新烧抓取/LLM 预算,务必配小 batch_limit);
      - 重置 = status→'queued',next_retry_at = NOW()+退避,updated_at=NOW(),last_error 保留作审计;
        last_error_category 用重派生后的真实类别校正(顺手修脏)。

    安全闸:
      - dry_run=True(默认):**只返回将处理的清单,不写库**(清单已是 batch_limit 截断后的真实将放量)。
      - batch_limit:单次最多放这么多条,默认 env FAILED_POOL_RECYCLE_BATCH_LIMIT(20),
        硬上限 FAILED_POOL_RECYCLE_BATCH_MAX(100)封顶 —— 任何调用都不可能一次性放出整池。
      - limit:旧位置参数,向后兼容;若传入则与 batch_limit 取**更小**者(更保守的那个生效)。

    dry_run=False:逐行回收(每行独立小事务,失败不影响其他行)。

    返回:
      {
        "dry_run": bool,
        "max_attempts": int,
        "backoff_seconds": int,
        "batch_limit": int,            # 本次实际生效的单次上限(已钳到硬上限)
        "reset_attempts": bool,
        "eligible_count": int,         # 命中可回收(经 batch_limit 截断**前**)的总数
        "candidate_count": int,        # 本次将处理(截断后)的数量
        "recycled_count": int,         # 实际重置(dry_run 恒为 0)
        "candidates": [{"id", "job_type", "category", "attempts"}...],  # 截断后的清单
      }
    """
    conn = get_conn()
    max_attempts = _max_attempts()
    backoff = _recycle_backoff_seconds()
    # 旧 limit 与新 batch_limit 取更保守者:任一非 None 都参与,最终再钳到硬上限。
    requested = [v for v in (batch_limit, limit) if v is not None and v >= 0]
    chosen_limit = min(requested) if requested else None
    effective_limit = _effective_batch_limit(chosen_limit)

    # reset_attempts=False:只取仍有预算的行(attempts < max_attempts),compat ? 占位。
    # reset_attempts=True:不卡预算(放量),取全部死信;status 字面量内联(受控,非用户输入)。
    if reset_attempts:
        rows = conn.execute(
            """
            SELECT
              id,
              COALESCE(job_type, '') AS job_type,
              COALESCE(last_error_category, '') AS stored_category,
              last_error,
              attempts
            FROM apify_jobs
            WHERE status IN ('failed', 'triage')
            ORDER BY id DESC
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
              id,
              COALESCE(job_type, '') AS job_type,
              COALESCE(last_error_category, '') AS stored_category,
              last_error,
              attempts
            FROM apify_jobs
            WHERE status IN ('failed', 'triage')
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

    eligible_count = len(candidates)
    # 批量上限闸:截断到 effective_limit。dry_run 清单也截断 → 清单即真实将放量,不误导。
    candidates = candidates[:effective_limit]

    if dry_run:
        logger.info(
            "failed_pool dry_run | eligible=%s candidate_count=%s batch_limit=%s reset_attempts=%s max_attempts=%s",
            eligible_count,
            len(candidates),
            effective_limit,
            reset_attempts,
            max_attempts,
        )
        return {
            "dry_run": True,
            "max_attempts": max_attempts,
            "backoff_seconds": backoff,
            "batch_limit": effective_limit,
            "reset_attempts": reset_attempts,
            "eligible_count": eligible_count,
            "candidate_count": len(candidates),
            "recycled_count": 0,
            "candidates": candidates,
        }

    recycled = 0
    for cand in candidates:
        try:
            if reset_attempts:
                # 放量:清零 attempts,让耗尽的可重试类也能被 worker 再领。
                conn.execute(
                    """
                    UPDATE apify_jobs
                    SET status = 'queued',
                        attempts = 0,
                        last_error_category = ?,
                        next_retry_at = NOW() + make_interval(secs => ?),
                        updated_at = NOW()
                    WHERE id = ?
                      AND status IN ('failed', 'triage')
                    """,
                    (cand["category"], backoff, cand["id"]),
                )
            else:
                # 保守:保留 attempts,仍受 max_attempts 约束。
                conn.execute(
                    """
                    UPDATE apify_jobs
                    SET status = 'queued',
                        last_error_category = ?,
                        next_retry_at = NOW() + make_interval(secs => ?),
                        updated_at = NOW()
                    WHERE id = ?
                      AND status IN ('failed', 'triage')
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
        "failed_pool recycled | recycled_count=%s candidate_count=%s eligible=%s batch_limit=%s reset_attempts=%s backoff_seconds=%s",
        recycled,
        len(candidates),
        eligible_count,
        effective_limit,
        reset_attempts,
        backoff,
    )
    return {
        "dry_run": False,
        "max_attempts": max_attempts,
        "backoff_seconds": backoff,
        "batch_limit": effective_limit,
        "reset_attempts": reset_attempts,
        "eligible_count": eligible_count,
        "candidate_count": len(candidates),
        "recycled_count": recycled,
        "candidates": candidates,
    }
