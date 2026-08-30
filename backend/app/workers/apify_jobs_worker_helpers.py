"""Pure helpers extracted from apify_jobs_worker (no DB / I/O / global state).

Behavior-preserving extraction: every function below is moved verbatim from
apify_jobs_worker.py and re-exported there via a barrel import, so all call sites
resolve unchanged.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from app.platform.provider_error_category import _error_category


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, "", b""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return default
    return parsed if parsed is not None else default


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


_SENSITIVE_URL_USERINFO_RE = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s@]+@)", re.IGNORECASE)
_SENSITIVE_AUTH_RE = re.compile(r"\b(authorization)\b\s*([:=])\s*(?:bearer\s+)?([^,\s'\"}\]]+)", re.IGNORECASE)
_SENSITIVE_BEARER_RE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/\-=]+", re.IGNORECASE)
_SENSITIVE_KV_RE = re.compile(
    r"\b("
    r"proxy|token|api[_-]?key|key|secret|password|passwd|access[_-]?token|refresh[_-]?token|client[_-]?secret"
    r")\b\s*([:=])\s*([^,\s'\"}\]]+)",
    re.IGNORECASE,
)


def _redact_sensitive_text(value: Any, *, limit: int = 2000) -> str:
    text = str(value or "")
    text = _SENSITIVE_URL_USERINFO_RE.sub(lambda match: f"{match.group(1)}***@", text)
    text = _SENSITIVE_AUTH_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    text = _SENSITIVE_BEARER_RE.sub("Bearer ***", text)
    text = _SENSITIVE_KV_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    return text[:limit]


def _provider_retry_reason(message: str, *, next_retry_at: Any | None = None) -> str:
    suffix = ""
    if next_retry_at:
        suffix = f" | next_retry_at={next_retry_at}"
    return f"provider_pressure_retry_scheduled: {message}{suffix}"[:2000]


# 失败分流口径(10E):把 _error_category 输出的细类映射到「该怎么处置这条失败 job」。
# 与 app.domains.kol.failed_pool_triage 的 RECYCLABLE/PERMANENT 口径**完全一致**,这里只是把
# 它前移到 worker 失败的那一瞬(inline),让每条失败当场就分流,而不是先全堆成 failed 再批量治理。
#
# - 「天气问题」(瞬时态,再跑有合理成功概率)→ 可重试:provider 限流 / 瞬时 5xx / gemini 超时 /
#   yt-dlp / 直链下载(代理·网络抖动)/ 媒体解析抖动 / 心跳过期被回收。
# - 「确定性死」(再跑一万次也一样,需要人工/凭证/改代码)→ 转人工三角(triage):
#   内容下架(content_blocked)/ 门禁登录墙(content_restricted=auth)/ 404 已删除
#   (content_unavailable=no_data)/ 不支持的 URL(permanent)/ 代码缺陷(code_error)。
# - unknown / 任何不认识的类别:**既不重试也不三角**,保守落 failed(可能藏永久错,留给批量
#   治理层重新派生后再定夺),与 failed_pool_triage 对 unknown 的处置一致。
_RETRYABLE_CATEGORIES: frozenset[str] = frozenset(
    {
        "provider_pressure",  # 429 / 5xx / 限流 / 临时过载
        "timeout",            # gemini_call_timeout 等瞬时超时
        "download",           # yt-dlp / 直链下载(代理·网络抖动)
        "media_resolve",      # media_resolve_failed:解析抖动,重试可恢复
        "stale_running",      # 心跳过期被 reclaim,本质是被打断而非真失败
    }
)

_TRIAGE_CATEGORIES: frozenset[str] = frozenset(
    {
        "content_blocked",      # geo / DMCA / 封号 / 强制下架
        "content_restricted",   # 私密 / 年龄限制 / 登录墙(auth:需凭证而非重试)
        "content_unavailable",  # 404 / 已删除 / 不存在(no_data)
        "permanent",            # unsupported / invalid_video_url / not_video
        "code_error",           # ModuleNotFound / TypeError 等,要改代码
    }
)


def _failure_disposition(category: str) -> str:
    """把失败类别分流成处置动作:'retry' / 'triage' / 'failed'。

    纯函数,不读 attempts 预算——预算闸由调用方(_fail_job)把守。这里只回答
    「这一类失败本质上能不能靠再跑一次恢复」:
      - 'retry'  → 可重试类(timeout/proxy/限流/媒体解析/被回收),重新 queued 由 worker 再跑;
      - 'triage' → 不可重试类(no_data/auth/下架/代码错),标 status='triage' 待人工;
      - unknown / 不认识的类别 → 也给 'retry'(实测大半是瞬时环境问题,如并发死锁重跑即过);
        预算仍由 _fail_job 把守,耗尽走 retry 类既有归宿(triage 可见池),不再死在无人排水的 failed 池。
    """
    cat = str(category or "").strip().lower()
    if cat in _RETRYABLE_CATEGORIES:
        return "retry"
    if cat in _TRIAGE_CATEGORIES:
        return "triage"
    return "retry"


def _derive_method(payload: dict[str, Any]) -> str:
    return str(payload.get("derive_method") or payload.get("analysis_method") or "mock").strip().lower() or "mock"


def _target(payload: dict[str, Any]) -> tuple[str, str]:
    return str(payload.get("target_type") or "").strip(), str(payload.get("target_id") or "").strip()


def _url_host(url: str) -> str:
    try:
        return urlparse(str(url or "")).netloc.lower()
    except Exception:
        return ""


def _platform_from_content_url(url: str) -> str:
    host = _url_host(url)
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return "unsupported"


def _parse_apify_resolver_stdout(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {
        "scraped_ok": False,
        "error": "apify resolver returned no JSON",
        "_parse_error": True,
    }


def _parse_last_json_stdout(stdout: str) -> dict[str, Any] | None:
    for line in reversed(str(stdout or "").splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _compact_text(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _score_value(value: Any) -> float | None:
    raw = value.get("score") if isinstance(value, dict) else value
    if raw in (None, ""):
        return None
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0.0
    if parsed > 100:
        return 100.0
    return round(parsed, 3)


def _score_confidence(value: Any) -> float | None:
    if not isinstance(value, dict) or value.get("confidence") in (None, ""):
        return None
    try:
        parsed = float(value.get("confidence"))
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return round(parsed, 5)


def _final_v1_payload(result: Any) -> dict[str, Any]:
    root = _as_dict(result)
    nested = _as_dict(root.get("video_analysis_final_v1"))
    if _as_dict(nested.get("layer1_visual_content")) or _as_dict(nested.get("layer6_flags_and_scores")):
        return nested
    return root


# 通用值工具(从 apify_jobs_worker.py 搬来,与 _int_or_none 同款;worker re-import 回去)。
def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _rate(numerator: Any, denominator: Any) -> float | None:
    top = _int_or_none(numerator)
    bottom = _int_or_none(denominator)
    if top is None or bottom is None or bottom <= 0:
        return None
    return round(top / bottom, 6)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
