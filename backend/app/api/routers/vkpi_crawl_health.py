"""抓取健康只读总览(LE 车道)：近 N 天「任务类型 × 状态 × 原因」汇总 + 完成率。

原因轴为什么不能只按 ``last_error_category``:最大的四个失败桶
(``url_unknown_unsupported`` 202 / ``non_video_post_no_video_signal`` 14 /
``llm_json_malformed`` 12 / ``budget_blocked`` 7)的 ``last_error_category`` **恒为 NULL**
—— 它们没走 worker 的统一 block 入口。只按类别聚合会把 239 行塞进「未分类」。
所以归一走 ``last_error`` 文本 → 稳定码 → 本模块码表。

为什么新写码表:把真库 712 条 failed/blocked/triage 喂进账号级进度那份
(``video_analysis_progress_reasons``)实测 269 条(37.8%)落「原因待排查」,且那份
文案是视频口径,挂到主页抓取 / 匹配度分析上会说错话。本模块每一句都做**任务类型中立**
措辞,类别轴仍复用同一套六类,认不出的码回落既有分类器 + 诚实兜底句,绝不硬贴标签。
实测本模块把未归类从 269/712 降到 0/712。

只读纪律:两条纯 SELECT 投影;零写库、零外部取数、零模型调用。
诚实空态:窗口内零行 → ``rows=[]`` + ``empty_reason``,完成率 ``null``
(**绝不用 0 或 100% 冒充**;本地近 30 天只有 48 行,空态是常态)。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.kol.video_analysis_progress_reasons import failure_category
from app.domains.settings import use_cases as settings_use_cases

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-crawl-health"])

MAX_WINDOW_DAYS = 3650
FAILED_STATES: tuple[str, ...] = ("failed", "blocked", "triage")
IN_FLIGHT_STATES: tuple[str, ...] = ("queued", "running")
UNCLASSIFIED_HUMAN = "原因待排查"

# 任务类型 → 中文一句(门面零内部标识符)。未命中回落「后台任务」。
_JOB_LABELS: dict[str, str] = {
    "video": "内容深度解读",
    "kol_profile_deep_crawl": "创作者主页抓取",
    "kol_content_fit_analysis": "内容匹配度分析",
    "account_dossier_extract": "账号档案抓取",
    "kol_auto_poll": "创作者自动巡检",
    "kol_pool_comments_collect": "评论采集",
    "smart_search_profile_advance": "智能搜索建档",
    "kol_audience_stats_refresh": "受众数据刷新",
    "logistics_track_sync": "物流轨迹同步",
    "project_retrospective_aggregate": "项目复盘汇总",
    "project_contract_extract": "合同信息提取",
    "kol_outreach_draft": "外联邮件草拟",
    "video_url_resolve": "内容链接解析",
    "comment_intelligence": "评论意向分析",
    "contract_invoice_extract": "合同发票解析",
    "kol_lookup": "创作者深度抓取",
}
_JOB_LABEL_FALLBACK = "后台任务"
_STATUS_LABELS: dict[str, str] = {
    "queued": "排队中",
    "running": "进行中",
    "done": "已完成",
    "failed": "失败",
    "blocked": "已拦截",
    "triage": "待人工",
}

# 稳定机器码 → (六类之一, 任务类型中立的中文一句)。精确匹配优先。
_EXACT_REASONS: dict[str, tuple[str, str]] = {
    "url_unknown_unsupported": ("download", "链接所属站点不在可抓取范围,已跳过"),
    "unsupported_platform": ("download", "链接所属站点不在可抓取范围,已跳过"),
    "non_video_post_no_video_signal": ("download", "这条内容里没有可解读的影像,已跳过"),
    "llm_json_malformed": ("model", "解读结果格式异常,可重新发起"),
    "budget_blocked": ("budget", "本期额度已用完"),
    "budget_guard_blocked": ("budget", "本期额度已用完"),
    "fallback_to_rule": ("model", "已改用备用方式给出结果"),
    "no_ready_video_analysis": ("model", "缺少可用的内容解读结果"),
    "unknown_job_type": ("unknown", "任务类型无法识别,已记录"),
    "video_analysis_authorization_fence_required": (
        "authorization",
        "缺少发起人授权:请从 MY KOL 页重新发起",
    ),
}

# 子串规则(顺序即优先级;匹配对象是归一后的稳定码,不是整段堆栈)。
# 真库里 41 条失败是异常文本而非稳定码,精确表结构上抓不到,必须有这一层。
_TEXT_REASONS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        (
            "nameerror", "attributeerror", "importerror", "modulenotfound", "typeerror",
            "keyerror", "valueerror", "indexerror", "has no attribute", "undefinedcolumn",
            "not defined", "traceback", "foreignkeyviolation", "dataerror",
        ),
        "unknown",
        "程序出错:已记录,请联系管理员",
    ),
    (("delimiter", "expecting", "malformed", "unterminated"), "model", "解读结果格式异常,可重新发起"),
    (
        ("adminshutdown", "terminating connection", "server disconnected", "connection reset",
         "ssl", "file api upload"),
        "provider",
        "服务连接中断,可重新发起",
    ),
    (("cancelled_by_scope", "scopedenied", "scope denied"), "authorization", "已按指定范围取消,未执行"),
    (("stale_running", "reclaimed"), "provider", "任务中断后已回收,可重新发起"),
    (("resolve_timeout",), "provider", "素材解析超时,可稍后重新发起"),
    (
        ("deleted_or_private", "has been removed", "does not exist", "age restricted", "private video"),
        "download",
        "原内容已删除或不可公开访问",
    ),
    (
        ("scraped_no_downloadable_url", "media_resolve", "returned no", "no_media"),
        "download",
        "没有找到可下载的素材地址",
    ),
    (("yt-dlp", "yt_dlp", "download failed", "download_failed"), "download", "素材下载失败:站点限制或网络不稳"),
    (("timeout", "timed out", "429", "rate limit", "overloaded", "unavailable"), "provider", "上游响应超时,可稍后重新发起"),
    (("budget", "quota"), "budget", "本期额度已用完"),
    (("fence", "authorization", "permission", "forbidden", "denied"), "authorization", "缺少发起人授权:请重新发起"),
    (("unsupported", "not supported"), "download", "链接所属站点不在可抓取范围,已跳过"),
)

# 认不出的码按类别给诚实兜底句;unknown 兜底句就是「原因待排查」,统计口径里算未归类。
_CATEGORY_FALLBACK: dict[str, str] = {
    "download": "内容获取失败,原因待核实",
    "authorization": "缺少发起人授权:请重新发起",
    "budget": "本期额度已用完",
    "model": "解读结果不可用,可重新发起",
    "provider": "上游服务暂时不可用,可稍后重新发起",
    "unknown": UNCLASSIFIED_HUMAN,
}

_JSON_REASON_RE = re.compile(r'"(?:reason|reason_detail|failure_code)"\s*:\s*"([^"]{1,160})"')

# 窗口计数。命中 idx_apify_jobs_status_created 前导列;聚合列必须 AS 别名。
_WINDOW_COUNT_SQL = (
    "SELECT COALESCE(job_type,'') AS job_type, status, COUNT(*) AS n "
    "FROM apify_jobs "
    "WHERE created_at >= NOW() - make_interval(days => ?) "
    "GROUP BY 1, 2 ORDER BY 3 DESC"
)
# 失败明细取样(原因轴)。last_error 在库里最长 2000 字符,取前 600 足够归一,
# 又不会把 5000 行的取样撑成 10MB。
_FAILURE_SAMPLE_SQL = (
    "SELECT COALESCE(job_type,'') AS job_type, status, "
    "COALESCE(last_error_category,'') AS last_error_category, "
    "SUBSTR(COALESCE(last_error,''), 1, 600) AS last_error "
    "FROM apify_jobs "
    "WHERE created_at >= NOW() - make_interval(days => ?) "
    "AND status IN ('failed','blocked','triage') "
    "ORDER BY id DESC LIMIT ?"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def job_label(job_type: Any) -> str:
    """任务类型 → 中文一句;认不出用通用词,绝不把内部标识符抬到门面上。"""
    return _JOB_LABELS.get(_text(job_type), _JOB_LABEL_FALLBACK)


def reason_code(last_error: Any) -> str:
    """从 last_error(JSON 或纯文本)取稳定机器码。

    与账号级进度那份的差别:JSON 被 worker 截断成半截时不回落成「整段截断 JSON」,
    而是用一条不含字面百分号的正则把 reason 抠出来 —— 否则截断行会炸出高基数长尾码。
    """
    raw = _text(last_error)
    if not raw:
        return ""
    if raw.startswith("{"):
        parsed: Any = None
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError) as exc:
            logger.debug("crawl_health.reason_json_truncated: %s", type(exc).__name__)
        if isinstance(parsed, dict):
            for key in ("reason", "reason_detail", "failure_code"):
                code = _text(parsed.get(key))
                if code:
                    return code[:160]
        matched = _JSON_REASON_RE.search(raw)
        if matched:
            return matched.group(1).strip()[:160]
    return (raw.splitlines()[0] if raw else "")[:160]


def crawl_reason(*, last_error_category: Any = "", last_error: Any = "") -> dict[str, Any]:
    """一条失败行 → {reason_code, category, reason_human, classified}。"""
    code = reason_code(last_error) or _text(last_error_category)
    key = code.lower()
    hit = _EXACT_REASONS.get(key)
    if hit is None:
        for markers, category, human in _TEXT_REASONS:
            if any(marker in key for marker in markers):
                hit = (category, human)
                break
    if hit is not None:
        return {"reason_code": code, "category": hit[0], "reason_human": hit[1], "classified": True}
    category = failure_category(last_error_category=last_error_category, last_error=last_error)
    human = _CATEGORY_FALLBACK.get(category, UNCLASSIFIED_HUMAN)
    return {
        "reason_code": code,
        "category": category,
        "reason_human": human,
        "classified": human != UNCLASSIFIED_HUMAN,
    }


def _rows_of(conn: Any, sql: str, params: tuple[Any, ...]) -> list[Any]:
    """只读投影;失败回空列表并尽量回滚(避免把事务卡在 aborted 态)。"""
    try:
        return list(conn.execute(sql, params).fetchall() or [])
    except Exception as exc:  # noqa: BLE001 - 只读运维视图不许因一条查询炸掉整页
        logger.warning("crawl_health.query_failed: %s", type(exc).__name__)
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            try:
                rollback()
            except Exception as rb_exc:  # noqa: BLE001 - 回滚本身失败只记录
                logger.debug("crawl_health.rollback_failed: %s", type(rb_exc).__name__)
        return []


def _status_totals(count_rows: list[Any]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in count_rows:
        status = _text(row["status"])
        totals[status] = totals.get(status, 0) + int(row["n"] or 0)
    return totals


def _ratio(numerator: int, denominator: int) -> float | None:
    """分母为 0 → None(诚实空态;绝不回 0 或 1 冒充)。"""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _summarize_reasons(sample_rows: list[Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]], int]:
    """取样行 → (Top 原因表, 每任务类型的原因分布, 未归类条数)。

    按 ``reason_human`` 分组而不是按码分组:异常文本的码是高基数长尾,按码分组会
    把一类问题拆成几十行。码只作为排查线索挂在组上。
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    by_job: dict[str, dict[str, int]] = {}
    unclassified = 0
    for row in sample_rows:
        job_type = _text(row["job_type"])
        reason = crawl_reason(
            last_error_category=row["last_error_category"], last_error=row["last_error"]
        )
        if not reason["classified"]:
            unclassified += 1
        human = str(reason["reason_human"])
        key = (str(reason["category"]), human)
        bucket = buckets.setdefault(
            key,
            {"category": key[0], "reason_human": human, "count": 0, "sample_reason_code": reason["reason_code"], "job_types": []},
        )
        bucket["count"] = int(bucket["count"]) + 1
        if job_type and job_type not in bucket["job_types"]:
            bucket["job_types"].append(job_type)
        per_job = by_job.setdefault(job_type, {})
        per_job[human] = per_job.get(human, 0) + 1
    top = sorted(buckets.values(), key=lambda item: (-int(item["count"]), str(item["reason_human"])))
    return top, by_job, unclassified


def _job_breakdown(count_rows: list[Any], reasons_by_job: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = {}
    for row in count_rows:
        job_type = _text(row["job_type"])
        entry = jobs.setdefault(
            job_type,
            {"job_type": job_type, "job_label": job_label(job_type), "total": 0, "by_status": {}},
        )
        status = _text(row["status"])
        count = int(row["n"] or 0)
        entry["total"] = int(entry["total"]) + count
        entry["by_status"][status] = int(entry["by_status"].get(status, 0)) + count
    out: list[dict[str, Any]] = []
    for entry in jobs.values():
        by_status: dict[str, int] = entry["by_status"]
        done = int(by_status.get("done", 0))
        ended = done + sum(int(by_status.get(state, 0)) for state in FAILED_STATES)
        top_reasons = sorted(reasons_by_job.get(str(entry["job_type"]), {}).items(), key=lambda kv: -kv[1])
        entry["completion_rate"] = _ratio(done, ended)
        entry["in_flight"] = sum(int(by_status.get(state, 0)) for state in IN_FLIGHT_STATES)
        entry["top_reason"] = top_reasons[0][0] if top_reasons else None
        out.append(entry)
    return sorted(out, key=lambda item: -int(item["total"]))


def crawl_health_overview(*, window_days: int = 30, sample_limit: int = 2000) -> dict[str, Any]:
    """近 ``window_days`` 天的抓取健康投影。纯读,零写库。"""
    from app.db.connection import get_conn, table_exists

    days = max(1, min(MAX_WINDOW_DAYS, int(window_days)))
    limit = max(100, min(5000, int(sample_limit)))
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": days,
        "window_start": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status_labels": dict(_STATUS_LABELS),
        "rows": [],
        "by_job": [],
        "top_reasons": [],
        "totals": {},
        "in_flight": 0,
        "completion_rate": None,
        "completion_rate_basis": "已完成 ÷(已完成 + 失败 + 已拦截 + 待人工);排队中/进行中不进分母",
        "execution_success_rate": None,
        "execution_success_rate_basis": "已完成 ÷(已完成 + 失败);与学习复盘页同口径,已拦截不进分母",
        "sample_size": 0,
        "sample_limit": limit,
        "sample_truncated": False,
        "unclassified_count": 0,
        "empty_reason": None,
    }
    if not table_exists("apify_jobs"):
        payload["empty_reason"] = "抓取记录尚未建立,暂时没有可看的数据"
        return payload

    conn = get_conn()
    count_rows = _rows_of(conn, _WINDOW_COUNT_SQL, (days,))
    if not count_rows:
        payload["empty_reason"] = f"近 {days} 天没有抓取记录"
        return payload

    sample_rows = _rows_of(conn, _FAILURE_SAMPLE_SQL, (days, limit))
    top_reasons, reasons_by_job, unclassified = _summarize_reasons(sample_rows)
    totals = _status_totals(count_rows)
    done = int(totals.get("done", 0))
    failed = int(totals.get("failed", 0))
    ended = done + sum(int(totals.get(state, 0)) for state in FAILED_STATES)
    payload.update(
        {
            "rows": [
                {
                    "job_type": _text(row["job_type"]),
                    "job_label": job_label(row["job_type"]),
                    "status": _text(row["status"]),
                    "count": int(row["n"] or 0),
                }
                for row in count_rows
            ],
            "by_job": _job_breakdown(count_rows, reasons_by_job),
            "top_reasons": top_reasons,
            "totals": totals,
            "in_flight": sum(int(totals.get(state, 0)) for state in IN_FLIGHT_STATES),
            "completion_rate": _ratio(done, ended),
            "execution_success_rate": _ratio(done, done + failed),
            "sample_size": len(sample_rows),
            "sample_truncated": len(sample_rows) >= limit,
            "unclassified_count": unclassified,
        }
    )
    return payload


@router.get("/ops/crawl-health")
def crawl_health_route(
    window_days: int = Query(default=30, ge=1, le=MAX_WINDOW_DAYS),
    sample_limit: int = Query(default=2000, ge=100, le=5000),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """近 N 天抓取健康(系统级运维数,manager 及以上可见;纯读)。"""
    if not settings_use_cases.is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")
    return crawl_health_overview(window_days=window_days, sample_limit=sample_limit)


__all__ = [
    "MAX_WINDOW_DAYS",
    "UNCLASSIFIED_HUMAN",
    "crawl_health_overview",
    "crawl_reason",
    "job_label",
    "reason_code",
    "router",
]
