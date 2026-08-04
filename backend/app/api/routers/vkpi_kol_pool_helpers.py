"""backend/app/api/routers/vkpi_kol_pool_helpers.py

行为不变抽取:vkpi_kol_pool.py 的内聚私有 helper 簇(URL 分流 / 智能搜索会话挂载 /
stale-while-revalidate 刷新闸)。函数体逐字搬运,原文件 re-export 兜住所有调用点。

红线:零触 viltrox_fit_score;此模块只做编排/会话挂载/刷新入队,绝不写 fit。
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from fastapi import Request

from app.core.release_validation import release_validation_active
import app.domains.kol.search_sessions as kol_search_sessions
import app.domains.sync.refresh_tier as refresh_tier
import app.domains.tasks.enqueue as task_enqueue


def _on_demand_refresh_enabled() -> bool:
    """Runtime provider gate for P1.X.C stale-while-revalidate.

    Search/detail endpoints may expose freshness state and record search
    interest by default, but they must not enqueue provider work unless an
    operator explicitly enables this gate in the runtime environment.
    """
    for name in ("VKPI_KOL_ON_DEMAND_REFRESH_ENABLED", "VKPI_ENABLE_KOL_ON_DEMAND_REFRESH"):
        value = os.getenv(name, "").strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
    return False


def _int_or_none(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


_KNOWN_URL_DOMAINS = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com",
    "twitter.com", "x.com", "bilibili.com", "b23.tv", "douyin.com", "twitch.tv", "reddit.com",
)


def _looks_like_url(value: str) -> bool:
    """诊断 P1-2/3 分流CJK收口:真闸门。此前 urlparse netloc 含点即判 url,致含域名词的
    中文问句(如「找像youtube.com的博主」无空格、「推荐 example.com 的人」带空格)被静默
    吞进 URL 分支、语义意图丢失。现加三重校验:无空白 + host 纯 ASCII DNS-label(CJK/
    punycode-折叠的中文主机一律拒)+ 命中已知平台域或合法 TLD;否则回退 recall。"""
    text = str(value or "").strip()
    if not text:
        return False
    # 真 URL 不含空白——含空白的输入是问句,回退 recall
    if any(ch.isspace() for ch in text):
        return False
    candidate = text if "://" in text else f"https://{text}"
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        return False
    # host 必须是合法 DNS 名(纯 ASCII 字母数字/连字符/点)——CJK 主机拒判
    if not all(ch.isascii() and (ch.isalnum() or ch in ".-") for ch in host):
        return False
    if any(host == d or host.endswith("." + d) for d in _KNOWN_URL_DOMAINS):
        return True
    tld = host.rsplit(".", 1)[-1]
    return tld.isalpha() and len(tld) >= 2


def _smart_query_type(*, branch: str, result: dict | None = None) -> str:
    if branch == "url":
        url_type = str((result or {}).get("url_type") or "").strip()
        if url_type == "video":
            return "url_video"
        if url_type == "profile":
            return "url_profile"
        return "unknown"
    if branch == "recall":
        return "text_recall"
    return "unknown"


def _attach_smart_url_session(
    *,
    body: dict,
    result: dict,
    query_text: str,
    staff: dict,
) -> dict:
    session = kol_search_sessions.ensure_session_for_result(
        session_id=_int_or_none(body.get("session_id")),
        create=bool(body.get("create_session", True)),
        query_text=query_text,
        query_type=_smart_query_type(branch="url", result=result),
        source=str(body.get("source") or "kol_smart_search"),
        input_payload={key: value for key, value in body.items() if key != "api_token"},
        staff=staff,
    )
    if session:
        result["search_session"] = kol_search_sessions.attach_url_result(int(session["id"]), result)
    return result


def _attach_smart_recall_session(
    *,
    body: dict,
    result: dict,
    query_text: str,
    staff: dict,
) -> dict:
    session = kol_search_sessions.ensure_session_for_result(
        session_id=_int_or_none(body.get("session_id")),
        create=bool(body.get("create_session", True)),
        query_text=query_text,
        query_type="text_recall",
        source=str(body.get("source") or "kol_smart_search"),
        input_payload={key: value for key, value in body.items() if key != "api_token"},
        staff=staff,
    )
    if session:
        result["search_session"] = kol_search_sessions.attach_recall_result(int(session["id"]), result)
    return result


async def _maybe_enqueue_refresh(
    request: Request,
    kol_pool_id: int,
    *,
    staff: dict,
    enabled: bool,
    force: bool = False,
    reason: str = "stale_while_revalidate",
) -> dict:
    if release_validation_active():
        # freshness_for_kol has a compatibility schema guard, so even that
        # helper is intentionally skipped while the candidate must stay
        # database-read-only. Keep a stable response shape for the UI.
        return {
            "triggered": False,
            "reason": "release_validation_fenced",
            "freshness": None,
            "search_marker": None,
            "provider_calls_enabled": False,
        }
    search_marker = refresh_tier.record_kol_search(int(kol_pool_id))
    freshness = refresh_tier.freshness_for_kol(int(kol_pool_id))
    provider_calls_enabled = _on_demand_refresh_enabled()
    if not enabled:
        return {
            "triggered": False,
            "reason": "not_requested",
            "freshness": freshness,
            "search_marker": search_marker,
            "provider_calls_enabled": provider_calls_enabled,
        }
    if not force and not freshness.get("needs_refresh"):
        return {
            "triggered": False,
            "reason": "fresh",
            "freshness": freshness,
            "search_marker": search_marker,
            "provider_calls_enabled": provider_calls_enabled,
        }
    if not provider_calls_enabled:
        return {
            "triggered": False,
            "reason": "on_demand_refresh_disabled",
            "message": "stale-while-revalidate is reporting freshness only; provider enqueue is disabled by runtime policy",
            "freshness": freshness,
            "search_marker": search_marker,
            "provider_calls_enabled": False,
        }
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        return {
            "triggered": False,
            "reason": "job_queue_unavailable",
            "freshness": freshness,
            "search_marker": search_marker,
            "provider_calls_enabled": True,
        }
    try:
        queued = await task_enqueue.enqueue_kol_pool_on_demand_refresh(
            queue,
            int(kol_pool_id),
            reason=reason,
            max_posts=1,
            staff=staff,
        )
    except ValueError as exc:
        return {
            "triggered": False,
            "reason": "not_enqueueable",
            "message": str(exc),
            "freshness": freshness,
            "search_marker": search_marker,
            "provider_calls_enabled": True,
        }
    return {
        "triggered": True,
        "reason": reason,
        "freshness": freshness,
        "search_marker": search_marker,
        "provider_calls_enabled": True,
        **queued,
    }
