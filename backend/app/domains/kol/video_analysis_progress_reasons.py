"""账号级视频深析进度的「失败可读化」与「ETA 车道口径」(O 车道,F3+F7)。

只读、零 LLM、零 fit。给 ``video_analysis_enqueue.account_video_analysis_progress`` 与
``/kol-pool/{id}/video-analysis-progress`` 端点用。

O→F 契约(冻结):
- 每个失败项多两字段 ``failure_category``(download|authorization|budget|model|provider|unknown)
  与 ``failure_reason_human``(中文一句,门面零内部术语);``failure_code`` 保留稳定机器码。
- ``eta_seconds`` = ceil((前方排队数 + 本账号进行中) / 活跃车道数) × 最近 done p50。
  活跃车道数优先 vkpi_worker_heartbeat 在窗心跳数,退 apify_jobs running 去重 lease_owner,
  再退 env 槽位提示;``eta.lanes_basis`` 诚实写明口径。

failure_category 以 ``last_error_category`` 为准(V→O 契约);类别是 'blocked'/'unknown'/空时
才扫 last_error 文本与子进程 stderr 尾巴里的稳定标记。
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

FAILURE_CATEGORIES: tuple[str, ...] = ("download", "authorization", "budget", "model", "provider", "unknown")
UNKNOWN_REASON_HUMAN = "分析未完成:原因待排查"
_HEARTBEAT_WINDOW_SEC = 120
_RUNNING_LEASE_WINDOW_MIN = 30

# worker 侧 last_error_category(apify_jobs_worker_helpers._error_category / _block_job)→ 六类。
_CATEGORY_BY_WORKER_CATEGORY: dict[str, str] = {
    "download": "download",
    "media_resolve": "download",
    "content_restricted": "download",
    "content_blocked": "download",
    "content_unavailable": "download",
    "permanent": "download",
    "provider_pressure": "provider",
    "timeout": "provider",
    "stale_running": "provider",
    "proxy": "provider",
    "authorization": "authorization",
    "budget": "budget",
    "model": "model",
    "provider": "provider",
    "code_error": "unknown",
}

# 文本标记扫描(仅当结构化类别不可用);顺序即优先级。
_TEXT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("budget", ("budget",)),
    (
        "authorization",
        (
            "fence", "authorization", "scope", "permission", "actor", "forbidden",
            "identity", "capability", "release_validation", "drifted", "denied", "revoked",
        ),
    ),
    (
        "model",
        (
            "model_binding", "execution_class", "readiness", "model_mismatch", "invalid final_v1",
            "invalidfinalv1", "unsupported_llm", "derive_method", "not_production_ready",
            "llm_json_malformed",
        ),
    ),
    (
        "provider",
        ("gemini_call_timeout", "timeout", "429", "resource_exhausted", "rate limit", "503", "502", "522",
         "unavailable", "overloaded", "provider_pressure", "proxy", "stale_running"),
    ),
    (
        "download",
        ("download", "yt-dlp", "yt_dlp", "media_resolve", "image_post_no_video", "unsupported_platform",
         "private video", "age restricted", "video unavailable", "not found", "has been removed",
         # 档案 / 评论 / 受众三段的稳定码(全码精确串,禁前缀:'unsupported' 会抢走视频段既有文案)。
         # 这些桶的写点绕开了 _block_job,last_error_category 恒 NULL,只能靠文本命中,
         # 不补这一列就有 200+ 条挤进「未分类」(2026-09-03 取证)。
         "url_unknown_unsupported", "unsupported_or_unresolved_url", "url_unknown_needs_human_choice",
         "cn_platform_video_only", "non_video_post", "no_downloadable_url",
         "no_posts", "no_commenters", "no_comments", "comments_collect_failed", "comments_job_not_ready",
         "pending_comments", "deep_crawl_not_executed", "profile_crawl_not_executed",
         "insufficient_evidence", "no_ready_video_analysis", "content_fit_not_ready"),
    ),
)

# 机器码 → 中文一句(门面零内部术语)。按 (category, markers, text) 顺序首个命中生效。
_HUMAN_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("authorization", ("release_validation",), "系统正在发布验证,付费分析暂停:请稍后重新发起"),
    ("authorization", ("local_evaluation",), "本地评估授权无效:请重新发起"),
    ("authorization", ("fence_required", "fence_invalid", "fence_action_mismatch"), "授权围栏缺失:请从 MY KOL 页重新发起"),
    (
        "authorization",
        ("actor_inactive", "permission_revoked", "actor_changed", "permission_required",
         "identity_required", "scope_denied", "write_forbidden", "read_forbidden", "denied"),
        "发起人权限已变更:请用有权限的账号重新发起",
    ),
    (
        "authorization",
        ("drifted", "duplicate_not_writable", "not_found", "inactive", "not_video"),
        "视频归属或授权已变更:请重新发起深析",
    ),
    ("authorization", (), "授权校验未通过:请从 MY KOL 页重新发起"),
    ("budget", ("disabled", "not_configured"), "预算功能未开启:请联系管理员"),
    ("budget", (), "预算已达上限"),
    ("model", ("invalid final_v1", "invalidfinalv1", "model_mismatch", "json"), "分析结果格式异常:请重新发起"),
    ("model", ("unsupported", "derive"), "该任务类型暂不支持视频深析"),
    ("model", (), "分析模型暂不可用:请稍后重试"),
    ("provider", ("timeout",), "分析服务响应超时"),
    ("provider", ("proxy", "522"), "网络代理不稳"),
    ("provider", ("stale_running", "reclaimed"), "任务被中断后已回收"),
    ("provider", (), "分析服务繁忙"),
    # ↓ 四段(档案/评论/受众/内容匹配)的稳定码:必须排在下面的通用 download 规则之前,
    #   否则 'url_unknown_unsupported' 会被 'unsupported' 抢走、错报成「该链接不是可分析的视频」。
    ("download", ("url_unknown_unsupported", "unsupported_or_unresolved_url"), "这个主页链接暂时不支持自动抓取:目前支持 YouTube / Instagram / TikTok 主页"),
    ("download", ("cn_platform_video_only",), "这个平台只能按单条内容分析,主页链接暂时抓不了"),
    ("download", ("url_unknown_needs_human_choice",), "这个链接指向不止一个账号,需要人工确认是哪一个"),
    ("download", ("unsupported_platform",), "这个平台暂不支持这项分析:请换一个支持的账号或链接"),
    ("download", ("non_video_post", "no_downloadable_url"), "这条内容里没有视频,没有可分析的画面"),
    ("download", ("no_commenters", "no_comments"), "这些内容下面没有可用的评论"),
    ("download", ("no_posts",), "还没有抓到这个账号的内容:先跑一次账号分析"),
    ("download", ("comments_job_not_ready", "pending_comments"), "评论还在收集中:收完会自动继续"),
    ("download", ("comments_collect_failed",), "评论这一段没有取到数据:可以再试一次"),
    ("download", ("deep_crawl_not_executed", "profile_crawl_not_executed"), "账号分析没有真正跑起来:可以再试一次"),
    ("download", ("insufficient_evidence", "no_ready_video_analysis", "content_fit_not_ready"), "可用素材还不够:先补一条视频分析再看这一段"),
    ("download", ("private video", "age restricted", "login required", "sign in", "members-only", "content_restricted", "requires authentication"), "视频需登录或为私密内容,无法获取"),
    ("download", ("content_unavailable", "not found", "not_found", "404", "deleted", "does not exist"), "视频已删除或不存在"),
    ("download", ("content_blocked", "geo", "copyright", "dmca", "removed", "terminated"), "视频已被平台下架或限制地区"),
    ("download", ("image_post_no_video", "unsupported", "invalid_video_url", "not_video", "permanent", "bad url"), "该链接不是可分析的视频"),
    ("download", (), "视频下载失败:平台限制或代理不稳"),
    ("unknown", ("code_error", "modulenotfound", "importerror", "typeerror", "keyerror", "valueerror", "attributeerror", "traceback"), "分析程序出错:已记录,请联系管理员"),
    ("unknown", ("foreignkeyviolation", "ledger"), "记账校验失败:已记录,请联系管理员"),
    ("unknown", (), UNKNOWN_REASON_HUMAN),
)
# 「下一步能做什么」的封闭动作码(门面按码出按钮/提示;绝不投自由文本)。
NEXT_STEPS: tuple[str, ...] = (
    "retry", "switch_source", "check_budget", "reissue_from_my_kol", "wait_auto_retry", "none",
)
_NEXT_STEP_BY_CATEGORY: dict[str, str] = {
    "authorization": "reissue_from_my_kol",
    "budget": "check_budget",
    "model": "retry",
    "provider": "wait_auto_retry",
    "download": "retry",
    "unknown": "retry",
}
# (标记, 动作码, 是否「本就不适用」);顺序即优先级,标记一律全码精确串。
_NEXT_STEP_RULES: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (
        ("url_unknown_unsupported", "unsupported_or_unresolved_url", "url_unknown_needs_human_choice",
         "cn_platform_video_only", "unsupported_platform", "missing_profile_url", "invalid_video_url"),
        "switch_source", True,
    ),
    (("non_video_post", "image_post_no_video", "no_downloadable_url", "no_commenters", "no_comments"), "none", True),
    (("content_unavailable", "has been removed", "does not exist"), "none", True),
    (("comments_job_not_ready", "pending_comments"), "wait_auto_retry", False),
)

_RETRY_SUFFIX_ACTIVE = ":会自动重试"
_RETRY_SUFFIX_TERMINAL = ":多次重试仍失败,请稍后重新发起"
_ACTIVE_STATES = ("queued", "running", "retrying", "processing")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _reason_code(last_error: Any) -> str:
    """从 last_error(JSON 或纯文本)里取稳定机器码:JSON 优先 reason / reason_detail。"""
    raw = _text(last_error)
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001 - 截断 JSON 也要能读
            data = None
        if isinstance(data, dict):
            for key in ("reason", "reason_detail", "failure_code"):
                code = _text(data.get(key))
                if code:
                    return code[:160]
    first = raw.splitlines()[0] if raw else ""
    return first[:160]


def failure_category(*, last_error_category: Any, last_error: Any = None, stderr_tail: Any = None) -> str:
    """六类之一;结构化类别优先,其余扫文本。"""
    category = _text(last_error_category).lower()
    mapped = _CATEGORY_BY_WORKER_CATEGORY.get(category)
    if mapped:
        return mapped
    blob = " ".join(part for part in (_text(last_error).lower(), _text(stderr_tail).lower()) if part)
    for target, markers in _TEXT_MARKERS:
        if any(marker in blob for marker in markers):
            return target
    return "unknown"


def failure_reason_human(category: str, *, status: Any = "", last_error: Any = None, stderr_tail: Any = None) -> str:
    """中文一句;provider 类按任务是否还在途补「会自动重试 / 多次重试仍失败」。"""
    cat = category if category in FAILURE_CATEGORIES else "unknown"
    blob = " ".join(part for part in (_text(last_error).lower(), _text(stderr_tail).lower()) if part)
    text = UNKNOWN_REASON_HUMAN
    for rule_category, markers, human in _HUMAN_RULES:
        if rule_category != cat:
            continue
        if not markers or any(marker in blob for marker in markers):
            text = human
            break
    if cat == "provider":
        text += _RETRY_SUFFIX_ACTIVE if _text(status).lower() in _ACTIVE_STATES else _RETRY_SUFFIX_TERMINAL
    return text


def failure_fields(*, status: Any, last_error_category: Any, last_error: Any = None, stderr_tail: Any = None) -> dict[str, Any]:
    """给一条失败 job 生成 O→F 契约字段;非失败态返回三个 None。"""
    state = _text(status).lower()
    if state not in ("failed", "blocked", "triage") and not (state == "queued" and _text(last_error_category)):
        return {"failure_category": None, "failure_reason_human": None, "failure_code": None}
    category = failure_category(last_error_category=last_error_category, last_error=last_error, stderr_tail=stderr_tail)
    return {
        "failure_category": category,
        "failure_reason_human": failure_reason_human(category, status=state, last_error=last_error, stderr_tail=stderr_tail),
        "failure_code": _reason_code(last_error) or (_text(last_error_category) or None),
    }


def failure_guidance_fields(
    *,
    status: Any,
    last_error_category: Any,
    last_error: Any = None,
    stderr_tail: Any = None,
) -> dict[str, Any]:
    """``failure_fields`` 的加法扩展:多两个键,既有六类元组与既有三键一字不动。

    - ``failure_next_step``:封闭动作码(见 ``NEXT_STEPS``),回答「下一步能做什么」;
    - ``failure_not_applicable``:True 表示这不是可自愈的故障,而是「这段对这个对象本就不适用」
      (链接不支持 / 内容里没有视频 / 帖子下没有评论),重试一百次也不会变。

    非失败态与 ``failure_fields`` 同口径:三个 None + 两个 None。
    """
    fields = failure_fields(
        status=status,
        last_error_category=last_error_category,
        last_error=last_error,
        stderr_tail=stderr_tail,
    )
    category = fields.get("failure_category")
    if not category:
        return {**fields, "failure_next_step": None, "failure_not_applicable": None}
    blob = " ".join(part for part in (_text(last_error).lower(), _text(stderr_tail).lower()) if part)
    for markers, step, not_applicable in _NEXT_STEP_RULES:
        if any(marker in blob for marker in markers):
            return {**fields, "failure_next_step": step, "failure_not_applicable": not_applicable}
    return {
        **fields,
        "failure_next_step": _NEXT_STEP_BY_CATEGORY.get(str(category), "retry"),
        "failure_not_applicable": False,
    }


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int | None:
    """单值 COUNT;失败回 None(并尽量回滚,避免 PG 事务卡在 aborted 态)。"""
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:  # noqa: BLE001 - 只读探测不许炸进度端点
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception as rb_exc:  # noqa: BLE001
                logger.debug("progress_reasons.rollback_failed: %s", type(rb_exc).__name__)
        return None
    if row is None:
        return 0
    data = dict(row) if not isinstance(row, dict) else row
    try:
        return int(list(data.values())[0] or 0)
    except (TypeError, ValueError, IndexError):
        return 0


def env_video_lane_hint() -> int:
    """env 槽位提示(与 worker 的 APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY 同源;解析失败按 1)。"""
    raw = str(os.environ.get("APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY", "1") or "1").strip()
    try:
        return max(1, min(16, int(raw)))
    except ValueError:
        return 1


def active_lane_count(conn: Any) -> tuple[int, str]:
    """活跃车道数与口径:heartbeat 在窗数 → running 去重 lease_owner → env 提示。"""
    beats = _scalar(
        conn,
        "SELECT COUNT(*) AS n FROM vkpi_worker_heartbeat WHERE last_heartbeat_at >= NOW() - make_interval(secs => ?)",
        (_HEARTBEAT_WINDOW_SEC,),
    )
    if beats:
        return max(1, beats), "worker_heartbeat"
    owners = _scalar(
        conn,
        "SELECT COUNT(DISTINCT lease_owner) AS n FROM apify_jobs "
        "WHERE status='running' AND lease_owner IS NOT NULL AND updated_at >= NOW() - make_interval(mins => ?)",
        (_RUNNING_LEASE_WINDOW_MIN,),
    )
    if owners:
        return max(1, owners), "running_lease_owners"
    return env_video_lane_hint(), "env_concurrency_hint"


def queue_ahead_count(conn: Any, *, derive_method: str, earliest_queued_created_at: Any) -> int:
    """全局 FCFS 口径:比本账号最早排队任务更早创建、仍在 queued/running 的同类视频任务数。"""
    if earliest_queued_created_at in (None, ""):
        return 0
    ahead = _scalar(
        conn,
        "SELECT COUNT(*) AS n FROM apify_jobs WHERE job_type='video' AND payload->>'derive_method'=? "
        "AND status IN ('queued','running') AND created_at < ?",
        (derive_method, earliest_queued_created_at),
    )
    return max(0, ahead or 0)


def estimate_eta_seconds(*, in_progress: int, queue_ahead: int, lanes: int, p50_ms: int | None) -> int | None:
    """ceil((前方 + 本账号进行中) / 车道数) × p50;无样本或无在途 → None(诚实)。"""
    if not in_progress or not p50_ms:
        return None
    total = max(0, int(queue_ahead)) + max(0, int(in_progress))
    waves = -(-total // max(1, int(lanes)))
    return int(round(waves * int(p50_ms) / 1000.0))


__all__ = [
    "FAILURE_CATEGORIES",
    "NEXT_STEPS",
    "active_lane_count",
    "env_video_lane_hint",
    "estimate_eta_seconds",
    "failure_category",
    "failure_fields",
    "failure_guidance_fields",
    "failure_reason_human",
    "queue_ahead_count",
]
