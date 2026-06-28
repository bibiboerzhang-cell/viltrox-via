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


def _error_category(message: str) -> str:
    text = str(message or "").lower()
    if any(marker in text for marker in ("429", "resource_exhausted", "rate limit", "quota exceeded")):
        return "provider_pressure"
    if any(
        marker in text
        for marker in (
            "500",
            "502",
            "503",
            "504",
            "5xx",
            "internal server error",
            "server error",
            "unavailable",
            "service unavailable",
            "high demand",
            "temporarily overloaded",
        )
    ):
        return "provider_pressure"
    if "gemini_call_timeout" in text:
        return "timeout"
    if "media_resolve_failed" in text or "media_resolve" in text:
        return "media_resolve"
    if "yt-dlp" in text or "yt_dlp" in text or "direct_video_download_failed" in text or "download_failed" in text:
        return "download"
    # content_restricted:内容存在但被门禁挡住(私密/年龄限制/登录/会员/订阅),需要凭证而非重试。
    # 放在 download 之后,避免吞掉 yt-dlp 下载失败(那是 download);"sign in to confirm" 这类
    # 是 YouTube 风控登录墙,归门禁。
    if any(
        marker in text
        for marker in (
            "age restricted",
            "age-restricted",
            "private video",
            "login required",
            "sign in to confirm",
            "members-only",
            "subscriber-only",
            "this video is private",
            "account is private",
            "requires authentication",
        )
    ):
        return "content_restricted"
    # content_blocked:内容被地域/版权/平台强制下架(geo/DMCA/removed/terminated),不可恢复。
    # "video unavailable" 是歧义词:明确"has been removed"/版权/封号的归 blocked,纯泛化 404/not_found 归
    # content_unavailable(见下)。这里只收明确下架信号。
    if any(
        marker in text
        for marker in (
            "not available in your country",
            "geoblock",
            "geo",
            "blocked in",
            "copyright",
            "dmca",
            "has been removed",
            "account terminated",
            "content warning",
        )
    ):
        return "content_blocked"
    # content_unavailable:泛化的找不到/已删除,既不是明确门禁也不是明确下架。
    # "video unavailable" 在缺乏更强信号时落这里(歧义安全网)。
    if any(
        marker in text
        for marker in (
            "video unavailable",
            "not_found",
            "not found",
            "404",
            "does not exist",
            "deleted",
        )
    ):
        return "content_unavailable"
    if "unsupported" in text or "invalid_video_url" in text or "not_video" in text or "bad url" in text:
        return "permanent"
    if "stale_running_reclaimed" in text:
        return "stale_running"
    # 代码级错误(缺依赖/异常类型)——和"天气问题"(provider_pressure/download)区分,
    # 这类要改代码而非重试。放在 transient 判据之后,避免吞掉 yt-dlp/429 等真瞬时态。
    if any(
        marker in text
        for marker in (
            "modulenotfounderror",
            "importerror",
            "nameerror",
            "attributeerror",
            "typeerror",
            "keyerror",
            "indexerror",
            "syntaxerror",
            "unboundlocalerror",
            "traceback (most recent call last)",
            # 运行期消息形态(无类名前缀):ModuleNotFoundError/ImportError 的裸文本
            "no module named",
            "cannot import name",
        )
    ):
        return "code_error"
    return "unknown"


def _provider_retry_reason(message: str, *, next_retry_at: Any | None = None) -> str:
    suffix = ""
    if next_retry_at:
        suffix = f" | next_retry_at={next_retry_at}"
    return f"provider_pressure_retry_scheduled: {message}{suffix}"[:2000]


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
