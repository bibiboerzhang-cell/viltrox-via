"""竞品新品雷达 —— 每早用 Gemini + Google 搜索接地,实时查海外竞品(Sony/Sigma/Tamron/DJI…)
新镜头/相机发布 + 对 Viltrox 的机会/威胁,接进 Signals & Alerts。

红线 / 安全:
- 复用 ai_today 的 `_generate`(Gemini 接地优先 + Claude 兜底)+ `_parse_json`。
- 过预算闸 `check_budget("cron:competitor_radar", est)`(硬上限,见 migration 152)。
- 只写本表 `vkpi_competitor_radar`,绝不碰 vkpi_kol_pool / viltrox_fit_score / 指纹。
- 海外焦点;一天一次小调用。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.costs import budget_guard
from app.domains.market.ai_today import (
    _RESULT_CONTRACT_VERSION,
    _call_generator,
    _contract_result,
    _freshness_payload,
    _generate,
    _generation_parts,
    _market_sources,
    _parse_datetime,
    _parse_json,
    _provider_call_attempted,
    _public_http_url,
    _result_status,
    _strict_text,
    _validate_grounding_sources,
)

logger = get_logger(__name__)

_BUDGET_SCOPE = "cron:competitor_radar"
_EST_COST = 0.05
_ORIGIN_EXTERNAL = "external"
_ORIGIN_OWNED = "owned"
_ORIGIN_UNKNOWN = "unknown"
_DIRECT_RELATIONS = {"direct", "grounding", "primary", "source"}
_OWNED_WEB_DOMAINS = {"viltrox.com"}
_SOCIAL_PLATFORMS_BY_HOST = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "reddit.com": "reddit",
    "twitter.com": "x",
    "x.com": "x",
}
_PLATFORM_ALIASES = {
    "google news": "google_news",
    "google_news_rss": "google_news",
    "google search": "google_search",
    "twitter": "x",
    "web": "website",
}
_NON_PLATFORM_VALUES = {
    "",
    "external",
    "market_signal",
    "other",
    "unknown",
    "vkpi_competitor_signals",
    "vkpi_market_mentions",
    "vkpi_market_sources",
}
_ACCOUNT_FIELDS = (
    "account_handle",
    "author_handle",
    "channel_handle",
    "creator_handle",
    "handle",
    "username",
    "account_name",
    "author",
    "channel_name",
    "creator_name",
    "publisher",
    "source_account",
)
_SOCIAL_PLATFORM_NAMES = frozenset(_SOCIAL_PLATFORMS_BY_HOST.values())
_SIGNAL_TYPES = {"market", "competitor", "public_excellent"}
_MODEL_CONTENT_ORIGINS = {_ORIGIN_EXTERNAL, _ORIGIN_UNKNOWN}


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _brand_ascii_key(value: Any) -> str:
    """ASCII 归一化品牌键:模型常回「Meike (美科)」式中英混写,原样小写包含
    匹配对英文接地源标题恒 miss(2026-07-17 线上两连发 item_source_not_grounded
    实证)。剥括注后只留 ASCII 词元;纯 CJK 品牌返回空串,回退 URL 匹配(闸不放宽)。"""
    raw = str(value or "").lower()
    raw = re.sub(r"[\(（][^\)）]*[\)）]", " ", raw)
    tokens = re.findall(r"[a-z0-9][a-z0-9&'-]*", raw)
    return " ".join(tokens).strip()


_GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"


def _fetch_final_url(url: str, timeout_seconds: float = 6.0) -> str:
    """跟随重定向链拿最终落地 URL;stream 不迭代 body,只取 Location 终点。"""
    import httpx

    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout_seconds) as response:
        return str(response.url)


def _resolve_grounding_redirects(
    sources: list[dict[str, Any]],
    *,
    max_resolve: int = 8,
) -> list[dict[str, Any]]:
    """把 Gemini 接地元数据的 vertexaisearch 重定向壳解析成真实文章 URL。

    2026-07-17 线上实证:接地源 title 只有域名(nbcnews.com 级)、URL 全是
    重定向壳——条目级 brand/URL 匹配在壳上**结构性恒败**(壳里永远没有品牌词,
    条目自报 URL 也永远等不上壳)。解析后真 URL 的路径 slug 天然携带品牌词,
    既有匹配闸不放宽即可命中,且落库/卡面拿到的就是可回跳的真链。
    解析失败保留壳 URL,闸照旧严格(fail-closed,绝不编 URL)。"""
    resolved: list[dict[str, Any]] = []
    budget = max_resolve
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = _text(source.get("source_url"), source.get("url"))
        if budget > 0 and _GROUNDING_REDIRECT_HOST in url:
            budget -= 1
            try:
                final_url = _public_http_url(_fetch_final_url(url))
            except Exception:
                logger.debug("competitor_radar.grounding_redirect_resolve_failed", exc_info=True)
                final_url = ""
            if final_url and _GROUNDING_REDIRECT_HOST not in final_url:
                source = {**source, "source_url": final_url, "url": final_url}
        resolved.append(source)
    return resolved


def _validate_item_sources(value: Any, field: str) -> tuple[list[dict[str, Any]], list[str]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        return [], [f"{field}:expected_list"]
    if len(value) > 8:
        return [], [f"{field}:too_many_items"]
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw_source in enumerate(value):
        if not isinstance(raw_source, dict):
            errors.append(f"{field}[{index}]:expected_object")
            continue
        raw_url = raw_source.get("source_url") or raw_source.get("url")
        source_url = _public_http_url(raw_url)
        if not source_url:
            errors.append(f"{field}[{index}].url:invalid_public_http_url")
            continue
        normalized.append({**raw_source, "url": source_url, "source_url": source_url})
    return normalized, errors


def _validate_competitor_content(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _contract_result("invalid", {"items": []}, ["result:expected_object"])
    raw_items = value.get("items")
    if raw_items is None:
        return _contract_result("degraded", {"items": []}, ["items:missing"])
    if not isinstance(raw_items, list):
        return _contract_result("invalid", {"items": []}, ["items:expected_list"])
    if not raw_items:
        return _contract_result("degraded", {"items": []}, ["items:empty"])
    if len(raw_items) > 6:
        return _contract_result("invalid", {"items": []}, ["items:too_many_items"])

    normalized_items: list[dict[str, Any]] = []
    invalid: list[str] = []
    partial: list[str] = []
    for index, raw_item in enumerate(raw_items):
        prefix = f"items[{index}]"
        if not isinstance(raw_item, dict):
            invalid.append(f"{prefix}:expected_object")
            continue
        item = dict(raw_item)
        for field, max_length in (
            ("brand", 80),
            ("title", 200),
            ("summary", 500),
            ("impact", 500),
        ):
            text, field_invalid, field_partial = _strict_text(
                raw_item.get(field),
                f"{prefix}.{field}",
                max_length=max_length,
            )
            invalid.extend(field_invalid)
            partial.extend(field_partial)
            item[field] = text

        signal_type, field_invalid, field_partial = _strict_text(
            raw_item.get("signal_type"),
            f"{prefix}.signal_type",
            max_length=40,
        )
        invalid.extend(field_invalid)
        partial.extend(field_partial)
        signal_type = signal_type.lower()
        if signal_type and signal_type not in _SIGNAL_TYPES:
            invalid.append(f"{prefix}.signal_type:unsupported_value")
        item["signal_type"] = signal_type

        content_origin, field_invalid, field_partial = _strict_text(
            raw_item.get("content_origin"),
            f"{prefix}.content_origin",
            max_length=20,
        )
        invalid.extend(field_invalid)
        partial.extend(field_partial)
        content_origin = content_origin.lower()
        if content_origin and content_origin not in _MODEL_CONTENT_ORIGINS:
            invalid.append(f"{prefix}.content_origin:unsupported_value")
        item["content_origin"] = content_origin

        source_platform, field_invalid, field_partial = _strict_text(
            raw_item.get("source_platform"),
            f"{prefix}.source_platform",
            max_length=40,
        )
        invalid.extend(field_invalid)
        partial.extend(field_partial)
        item["source_platform"] = source_platform.lower()

        source_url_raw = raw_item.get("source_url")
        if source_url_raw is None or source_url_raw == "":
            partial.append(f"{prefix}.source_url:missing")
            item["source_url"] = ""
        elif not isinstance(source_url_raw, str):
            invalid.append(f"{prefix}.source_url:expected_string")
            item["source_url"] = ""
        else:
            source_url = _public_http_url(source_url_raw)
            if not source_url:
                invalid.append(f"{prefix}.source_url:invalid_public_http_url")
            item["source_url"] = source_url

        published_at_raw = raw_item.get("published_at")
        if published_at_raw is None:
            item["published_at"] = ""
        elif not isinstance(published_at_raw, str):
            invalid.append(f"{prefix}.published_at:expected_string")
            item["published_at"] = ""
        else:
            published_at = published_at_raw.strip()
            if published_at and _parse_datetime(published_at) is None:
                invalid.append(f"{prefix}.published_at:invalid_datetime")
            item["published_at"] = published_at

        item_sources, source_errors = _validate_item_sources(raw_item.get("sources"), f"{prefix}.sources")
        invalid.extend(source_errors)
        if "sources" in raw_item:
            item["sources"] = item_sources
        normalized_items.append(item)

    normalized = {"items": normalized_items}
    if invalid:
        return _contract_result("invalid", normalized, invalid + partial)
    if partial:
        return _contract_result("degraded", normalized, partial)
    return _contract_result("ready", normalized)


def _host(source_url: Any) -> str:
    try:
        return (urlparse(str(source_url or "").strip()).hostname or "").lower()
    except Exception:
        return ""


def _is_public_http_url(source_url: Any) -> bool:
    try:
        parsed = urlparse(str(source_url or "").strip())
    except Exception:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def _normalized_handle(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", unquote(str(value or "")).lower().lstrip("@"))


def _host_platform(host: str) -> str:
    for domain, platform in _SOCIAL_PLATFORMS_BY_HOST.items():
        if host == domain or host.endswith(f".{domain}"):
            return platform
    if host == "news.google.com" or host.endswith(".news.google.com"):
        return "google_news"
    return "website" if host else _ORIGIN_UNKNOWN


def _source_platform(value: dict[str, Any], source_url: str) -> str:
    raw = _text(value.get("source_platform"), value.get("platform"), value.get("provider")).lower()
    raw = _PLATFORM_ALIASES.get(raw, raw.replace("-", "_"))
    if raw not in _NON_PLATFORM_VALUES:
        return raw
    return _host_platform(_host(source_url))


def _canonical_origin(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"owned", "brand_owned", "first_party", "internal", "viltrox_owned"}:
        return _ORIGIN_OWNED
    if raw in {"external", "third_party", "public", "public_excellent", "competitor", "market_external"}:
        return _ORIGIN_EXTERNAL
    if raw in {"unknown", "unverified"}:
        return _ORIGIN_UNKNOWN
    return ""


def _is_viltrox_owned(value: dict[str, Any], source_url: str) -> bool:
    if _canonical_origin(value.get("content_origin")) == _ORIGIN_OWNED:
        return True

    host = _host(source_url)
    if host in _OWNED_WEB_DOMAINS or any(host.endswith(f".{domain}") for domain in _OWNED_WEB_DOMAINS):
        return True

    platform = _host_platform(host)
    if platform != "website" and source_url:
        try:
            segments = [part for part in unquote(urlparse(source_url).path).split("/") if part]
        except Exception:
            segments = []
        account_candidates: list[str] = []
        if platform == "youtube" and segments:
            if segments[0].startswith("@"):
                account_candidates.append(segments[0])
            elif segments[0].lower() in {"c", "channel", "user"} and len(segments) > 1:
                account_candidates.append(segments[1])
        elif platform == "instagram" and segments and segments[0].lower() not in {"p", "reel", "reels", "stories"}:
            account_candidates.append(segments[0])
        elif platform == "tiktok" and segments and segments[0].startswith("@"):
            account_candidates.append(segments[0])
        elif platform in {"facebook", "x"} and segments and segments[0].lower() not in {"i", "reel", "share", "watch"}:
            account_candidates.append(segments[0])
        elif platform == "reddit" and len(segments) > 1 and segments[0].lower() in {"r", "u", "user"}:
            account_candidates.append(segments[1])
        if any(_normalized_handle(candidate).startswith("viltrox") for candidate in account_candidates):
            return True

    for key in _ACCOUNT_FIELDS:
        candidate = _normalized_handle(value.get(key))
        if candidate.startswith("viltrox"):
            return True
    return False


def _is_ambiguous_viltrox_social(value: dict[str, Any], source_url: str) -> bool:
    platform = _source_platform(value, source_url)
    if platform not in _SOCIAL_PLATFORM_NAMES:
        return False
    subject = _normalized_handle(f"{_text(value.get('brand'))} {_text(value.get('title'))}")
    if "viltrox" not in subject:
        return False
    return not any(_normalized_handle(value.get(key)) for key in _ACCOUNT_FIELDS)


def normalize_content_provenance(
    value: dict[str, Any] | None,
    *,
    observed_at: Any = "",
) -> dict[str, str]:
    """Return additive, conservative provenance for a Signals content row."""
    row = value if isinstance(value, dict) else {}
    source_url = _text(row.get("source_url"), row.get("url"), row.get("permalink"))
    explicit_origin = _canonical_origin(row.get("content_origin"))
    if _is_viltrox_owned(row, source_url):
        content_origin = _ORIGIN_OWNED
    elif explicit_origin == _ORIGIN_UNKNOWN:
        content_origin = _ORIGIN_UNKNOWN
    elif _is_ambiguous_viltrox_social(row, source_url):
        content_origin = _ORIGIN_UNKNOWN
    elif _is_public_http_url(source_url):
        content_origin = _ORIGIN_EXTERNAL
    else:
        # An LLM/provider label without a verifiable public URL is not enough to
        # turn missing provenance into an external-content claim.
        content_origin = _ORIGIN_UNKNOWN
    return {
        "content_origin": content_origin,
        "source_platform": _source_platform(row, source_url),
        "source_url": source_url,
        "published_at": _text(
            row.get("published_at"),
            row.get("publish_date"),
            row.get("published"),
            row.get("posted_at"),
        ),
        "observed_at": _text(
            row.get("observed_at"),
            row.get("captured_at"),
            row.get("created_at"),
            row.get("detected_at"),
            observed_at,
        ),
    }


def _normalized_source(
    source: Any,
    *,
    observed_at: Any = "",
    default_relation: str = "unknown",
) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        return None
    row = dict(source)
    provenance = normalize_content_provenance(row, observed_at=observed_at)
    row.update(provenance)
    row["url"] = provenance["source_url"]
    row["relation_type"] = _text(row.get("relation_type"), default_relation)
    return row


def _dedupe_sources(
    sources: list[Any],
    *,
    observed_at: Any = "",
    default_relation: str = "unknown",
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for source in sources or []:
        row = _normalized_source(source, observed_at=observed_at, default_relation=default_relation)
        if row is None:
            continue
        source_url = _text(row.get("source_url"))
        key = (
            source_url or _text(row.get("title")),
            "" if source_url else _text(row.get("provider"), row.get("source_platform")),
            "",
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(row)
    return normalized


def _direct_item_source(item: dict[str, Any]) -> dict[str, Any] | None:
    source_url = _text(item.get("source_url"), item.get("url"), item.get("permalink"))
    if not source_url:
        return None
    return {
        "brand": item.get("brand"),
        "title": _text(item.get("source_title"), item.get("title")),
        "url": source_url,
        "source_url": source_url,
        "provider": _text(item.get("source_platform"), item.get("platform"), item.get("provider")),
        "source_platform": _text(item.get("source_platform"), item.get("platform")),
        "content_origin": item.get("content_origin"),
        "published_at": _text(item.get("published_at"), item.get("publish_date")),
        "observed_at": item.get("observed_at"),
        **{key: item.get(key) for key in _ACCOUNT_FIELDS},
        "relation_type": "direct",
    }


def _primary_source(item: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any] | None:
    item_url = _text(item.get("source_url"), item.get("url"), item.get("permalink"))
    if item_url:
        for source in sources:
            if _text(source.get("source_url"), source.get("url")) == item_url:
                return source
    for source in sources:
        if _text(source.get("relation_type")).lower() in _DIRECT_RELATIONS and source.get("source_url"):
            return source
    return None


def normalize_signal_item(
    value: dict[str, Any] | None,
    *,
    sources: list[Any] | None = None,
    observed_at: Any = "",
) -> dict[str, Any]:
    """Add flat provenance while retaining the legacy radar item contract."""
    item = dict(value) if isinstance(value, dict) else {}
    item_sources = list(item.get("sources")) if isinstance(item.get("sources"), list) else []
    direct_source = _direct_item_source(item)
    if direct_source:
        item_sources.insert(0, direct_source)
    item_sources.extend(sources or [])
    normalized_sources = _dedupe_sources(item_sources, observed_at=observed_at, default_relation="direct")
    primary = _primary_source(item, normalized_sources)

    direct_provenance = normalize_content_provenance(item, observed_at=observed_at)
    primary_provenance = normalize_content_provenance(primary, observed_at=observed_at) if primary else {}
    if (
        _is_viltrox_owned(item, direct_provenance["source_url"])
        or primary_provenance.get("content_origin") == _ORIGIN_OWNED
    ):
        content_origin = _ORIGIN_OWNED
    elif _canonical_origin(item.get("content_origin")) == _ORIGIN_UNKNOWN:
        content_origin = _ORIGIN_UNKNOWN
    elif _is_ambiguous_viltrox_social(item, direct_provenance["source_url"]):
        content_origin = _ORIGIN_UNKNOWN
    elif direct_provenance["content_origin"] == _ORIGIN_EXTERNAL:
        content_origin = _ORIGIN_EXTERNAL
    else:
        content_origin = _text(primary_provenance.get("content_origin"), _ORIGIN_UNKNOWN)

    item.update(
        {
            "content_origin": content_origin,
            "source_platform": _text(
                ""
                if direct_provenance.get("source_platform") == _ORIGIN_UNKNOWN
                else direct_provenance.get("source_platform"),
                primary_provenance.get("source_platform"),
                _ORIGIN_UNKNOWN,
            ),
            "source_url": _text(direct_provenance.get("source_url"), primary_provenance.get("source_url")),
            "published_at": _text(direct_provenance.get("published_at"), primary_provenance.get("published_at")),
            "observed_at": _text(
                direct_provenance.get("observed_at"),
                primary_provenance.get("observed_at"),
                observed_at,
            ),
            "sources": normalized_sources,
        }
    )
    return item


def _ensure_schema() -> None:
    # PostgreSQL schema is migration-owned
    # (152_vkpi_competitor_radar.sql).  Runtime DDL on a GET or scheduler
    # execution can block behind unrelated transactions and fan the wait out
    # across the web pool.  SQLite keeps its idempotent fixture bootstrap.
    if is_postgres_runtime():
        return
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_competitor_radar (
            snapshot_date DATE PRIMARY KEY,
            content_json  TEXT NOT NULL,
            model         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def generate_competitor_radar() -> dict[str, Any]:
    """每早一次:预算闸 → Gemini(Google 接地)查外部市场信号 → 存库。"""
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        logger.info("competitor_radar.budget_blocked", extra={"scope": _BUDGET_SCOPE})
        return {
            "status": "budget_blocked",
            "result_status": "degraded",
            "contract_status": "degraded",
            "contract_version": _RESULT_CONTRACT_VERSION,
            "reason": "budget_blocked",
            "provenance": {
                "provider": "none",
                "status": "budget_blocked",
                "attempts": [],
                "fallback_used": False,
            },
        }

    today_label = datetime.now(tz=timezone.utc).strftime("%Y年%m月%d日")
    prompt = (
        f"【重要·今天的真实日期是 {today_label}】请严格按此日期判断「近期/最近」,绝不要把往年旧发布当成当下新动态;\n"
        f"无法实时联网就别编造具体的「刚发布」事件(宁可笼统也不错报时间)。\n"
        "你是 Viltrox(唯卓仕,海外镜头/相机配件品牌)的竞品情报分析师。\n"
        "请用 Google 搜索查清【近期(过去 1–2 周到今天)海外·国际相机/镜头行业的真实竞品动态】:\n"
        "Sony / Sigma / Tamron / Nikon / Canon / Fujifilm / DJI / Panasonic 等的新镜头/相机发布、\n"
        "重大产品新闻、价格变动,以及公开平台上值得学习的优秀摄影/视频内容。\n"
        "**默认只取外部来源:行业媒体、竞品公开渠道、第三方创作者/社区优秀内容;不要使用 Viltrox 官网或\n"
        "Viltrox 自营社媒账号作为条目来源。只取海外/英文圈真实信息,不要中国大陆平台传闻,务必基于搜索、不要编。**\n"
        "每条必须给直接可回跳的公开 source_url;没有可核验 URL 时 content_origin 必须写 unknown,不得猜 external。\n"
        "为 Viltrox 输出 JSON(只输出 JSON,不要多余文字):\n"
        '{\n'
        '  "items": [\n'
        '    {"signal_type": "market|competitor|public_excellent", "brand": "竞品/来源名", '
        '"title": "新品/动态/优秀公开内容(带时间)", "summary": "一句话说明", '
        '"impact": "对 Viltrox 的机会、威胁或可借鉴点(一句)", "content_origin": "external|unknown", '
        '"source_platform": "website|youtube|instagram|reddit|x|other|unknown", '
        '"source_url": "直接公开链接或空字符串", "published_at": "ISO-8601 时间或空字符串"}\n'
        '  ]\n'
        '}\n'
        "items 取 3–5 条,优先 competitor/market,至少包含一条有证据的 public_excellent,按重要性排序。"
    )
    raw, model_used, sources, provenance = _generation_parts(
        _call_generator(_generate, prompt, _validate_competitor_content)
    )
    content = _parse_json(raw)

    if _provider_call_attempted(provenance):
        try:
            budget_guard.record_cost(scope=_BUDGET_SCOPE, cost_usd=_EST_COST)
        except Exception:
            logger.debug("competitor_radar.record_cost_failed", exc_info=True)

    generated_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    contract = _validate_competitor_content(content)
    contract_status = str(contract.get("status") or "invalid")
    if contract_status != "ready":
        reason = "invalid_result_contract" if contract_status == "invalid" else "partial_result_contract"
        logger.warning(
            "competitor_radar.result_contract_rejected",
            extra={"status": contract_status, "errors": contract.get("errors")},
        )
        return {
            "status": contract_status,
            "result_status": contract_status,
            "contract_status": contract_status,
            "contract_version": _RESULT_CONTRACT_VERSION,
            "reason": reason,
            "validation_errors": list(contract.get("errors") or []),
            "model": str(model_used or ""),
            "provenance": provenance,
            "generated_at": generated_at,
        }

    source_contract = _validate_grounding_sources(sources)
    # 壳解析在去重前:两个壳解析到同一篇真文章时按真 URL 合并。
    grounding_sources = _dedupe_sources(
        _resolve_grounding_redirects(list(source_contract.get("value") or [])),
        observed_at=generated_at,
        default_relation="grounding",
    )
    if source_contract.get("status") != "ready" or not grounding_sources:
        result_status = "invalid" if source_contract.get("status") == "invalid" else "degraded"
        logger.warning(
            "competitor_radar.ungrounded_not_persisted",
            extra={"model": model_used, "reason": "no_grounded_citations"},
        )
        return {
            "status": "ungrounded",
            "result_status": result_status,
            "contract_status": result_status,
            "contract_version": _RESULT_CONTRACT_VERSION,
            "reason": "invalid_grounding_contract" if result_status == "invalid" else "no_grounded_citations",
            "model": str(model_used or ""),
            "sources": [],
            "validation_errors": list(source_contract.get("errors") or []),
            "provenance": provenance,
            "generated_at": generated_at,
        }
    items = list((contract.get("value") or {}).get("items") or [])

    def item_has_grounding(item: dict[str, Any]) -> bool:
        item_url = str(item.get("source_url") or "")
        brand = str(item.get("brand") or "").strip().lower()
        brand_key = _brand_ascii_key(brand)
        for source in grounding_sources:
            if not isinstance(source, dict):
                continue
            source_url = _text(source.get("source_url"), source.get("url"))
            source_blob = " ".join(
                str(source.get(key) or "").lower()
                for key in ("title", "source_url", "url")
            )
            if (
                (item_url and item_url == source_url)
                or (brand and brand in source_blob)
                or (brand_key and brand_key in source_blob)
            ):
                return True
        return False

    # 条目级 fail-closed(2026-07-17):此前任一条目未接地→整批连坐拒绝,
    # 实测引文只覆盖部分品牌时(4条里1条 Canon 传闻无引文)批批全灭。
    # 改为丢弃未接地条目、只落库已接地子集;全部未接地才整批拒绝。
    grounded_items = [
        item for item in items if isinstance(item, dict) and item_has_grounding(item)
    ]
    dropped_ungrounded = sum(1 for item in items if isinstance(item, dict)) - len(grounded_items)
    if dropped_ungrounded:
        logger.warning(
            "competitor_radar.item_sources_not_grounded",
            extra={
                "count": dropped_ungrounded,
                "kept": len(grounded_items),
                "model": model_used,
            },
        )
    if not grounded_items:
        return {
            "status": "degraded",
            "result_status": "degraded",
            "contract_status": "degraded",
            "contract_version": _RESULT_CONTRACT_VERSION,
            "reason": "item_source_not_grounded",
            "validation_errors": ["items.source_url:not_in_grounding_sources"],
            "model": str(model_used or ""),
            "sources": grounding_sources,
            "provenance": provenance,
            "generated_at": generated_at,
        }
    items = grounded_items
    clean: list[dict[str, Any]] = []
    for it in items[:6]:
        d = it if isinstance(it, dict) else {}
        brand = str(d.get("brand") or "")[:40]
        item_sources = list(d.get("sources")) if isinstance(d.get("sources"), list) else []
        item_url = _text(d.get("source_url"), d.get("url"))
        brand_key = _brand_ascii_key(brand)
        for source in grounding_sources:
            source_url = _text(source.get("source_url"), source.get("url"))
            source_blob = " ".join(
                str(source.get(key) or "").lower()
                for key in ("title", "source_url", "url")
            )
            if (
                (item_url and source_url == item_url)
                or (brand and brand.lower() in source_blob)
                or (brand_key and brand_key in source_blob)
            ):
                item_sources.append(source)
        clean.append(
            normalize_signal_item(
                {
                    "signal_type": _text(d.get("signal_type"), "competitor")[:40],
                    "brand": brand,
                    "title": str(d.get("title") or "")[:160],
                    "summary": str(d.get("summary") or "")[:240],
                    "impact": str(d.get("impact") or "")[:240],
                    "content_origin": d.get("content_origin"),
                    "source_platform": d.get("source_platform"),
                    "source_url": item_url,
                    "published_at": d.get("published_at"),
                    "observed_at": generated_at,
                    "account_handle": d.get("account_handle"),
                    "author_handle": d.get("author_handle"),
                    "channel_handle": d.get("channel_handle"),
                },
                sources=item_sources,
                observed_at=generated_at,
            )
        )
    if any(item.get("content_origin") == _ORIGIN_OWNED for item in clean):
        logger.warning("competitor_radar.owned_source_rejected", extra={"model": model_used})
        return {
            "status": "degraded",
            "result_status": "degraded",
            "contract_status": "degraded",
            "contract_version": _RESULT_CONTRACT_VERSION,
            "reason": "owned_source_excluded",
            "validation_errors": ["items.content_origin:owned_source"],
            "model": str(model_used or ""),
            "sources": grounding_sources,
            "provenance": provenance,
            "generated_at": generated_at,
        }
    payload = {
        "items": clean,
        "sources": grounding_sources,
        "evidence": grounding_sources,
        # 诚实记账:本轮被丢弃的未接地条目数(0=全部接地),前端/审计可见。
        "dropped_ungrounded": dropped_ungrounded,
        "status": "ready",
        "result_status": "ready",
        "contract_status": "ready",
        "contract_version": _RESULT_CONTRACT_VERSION,
        "provenance": provenance,
        "generated_at": generated_at,
    }
    _ensure_schema()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_competitor_radar (snapshot_date, content_json, model)
        VALUES (CURRENT_DATE, ?, ?)
        ON CONFLICT (snapshot_date) DO UPDATE
          SET content_json = excluded.content_json, model = excluded.model, created_at = now()
        """,
        (json.dumps(payload, ensure_ascii=False), str(model_used or "")),
    )
    conn.commit()
    logger.info("competitor_radar.generated", extra={"items": len(clean), "model": model_used})
    return {
        "status": "ok",
        "result_status": "ready",
        "contract_status": "ready",
        "contract_version": _RESULT_CONTRACT_VERSION,
        "items": len(clean),
        "provenance": provenance,
        "generated_at": generated_at,
    }


def get_competitor_radar() -> dict[str, Any]:
    """读最新竞品雷达，并只读补齐每条信号的原始来源与新鲜度。"""
    try:
        _ensure_schema()
        row = get_conn().execute(
            "SELECT snapshot_date, content_json, model, created_at "
            "FROM vkpi_competitor_radar ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {
                "available": False,
                "status": "invalid",
                "result_status": "invalid",
                "is_ready": False,
                "reason": "not_generated_yet",
            }
        d = dict(row)
        try:
            content = json.loads(d.get("content_json") or "{}")
        except (TypeError, ValueError):
            content = {}
        content = content if isinstance(content, dict) else {}
        contract = _validate_competitor_content(content)
        source_contract = _validate_grounding_sources(content.get("sources"))
        contract_statuses = {
            str(contract.get("status") or "invalid"),
            str(source_contract.get("status") or "degraded"),
        }
        contract_status = (
            "invalid"
            if "invalid" in contract_statuses
            else "degraded"
            if "degraded" in contract_statuses
            else "ready"
        )
        validation_errors = [
            *list(contract.get("errors") or []),
            *list(source_contract.get("errors") or []),
        ]
        if contract_status == "invalid":
            generated_at = _text(content.get("generated_at"), d.get("created_at"), d.get("snapshot_date"))
            freshness = _freshness_payload(generated_at)
            metadata = {
                "status": "invalid",
                "result_status": "invalid",
                "contract_status": "invalid",
                "contract_version": _RESULT_CONTRACT_VERSION,
                "is_ready": False,
                "snapshot_date": str(d.get("snapshot_date") or ""),
                "generated_at": generated_at,
                "items": [],
                "sources": [],
                "evidence": [],
                "validation_errors": validation_errors,
                "provenance": content.get("provenance") if isinstance(content.get("provenance"), dict) else {},
                **freshness,
            }
            return {
                "available": False,
                "reason": "invalid_result_contract",
                "model": d.get("model"),
                **metadata,
                "content": metadata,
            }

        items = list((contract.get("value") or {}).get("items") or [])
        brands = [str(item.get("brand") or "") for item in items if isinstance(item, dict)]
        generated_at = _text(content.get("generated_at"), d.get("created_at"), d.get("snapshot_date"))
        market_sources = _dedupe_sources(
            _market_sources(brands, limit=12),
            observed_at=generated_at,
            default_relation="brand_context",
        )
        grounding_sources = _dedupe_sources(
            list(source_contract.get("value") or []),
            observed_at=generated_at,
            default_relation="grounding",
        )
        enriched_items: list[dict[str, Any]] = []
        for raw_item in items:
            item = raw_item if isinstance(raw_item, dict) else {}
            brand = str(item.get("brand") or "").strip().lower()
            item_sources = list(item.get("sources")) if isinstance(item.get("sources"), list) else []
            urls = {
                _text(source.get("source_url"), source.get("url"))
                for source in item_sources
                if isinstance(source, dict) and source.get("url")
            }
            for source in market_sources:
                source_blob = " ".join(
                    str(source.get(key) or "").lower()
                    for key in ("brand", "title", "url")
                )
                if not brand or brand not in source_blob:
                    continue
                source_url = _text(source.get("source_url"), source.get("url"))
                if source_url and source_url not in urls:
                    item_sources.append(source)
                    urls.add(source_url)
                if len(item_sources) >= 3:
                    break
            has_direct_source = any(
                isinstance(source, dict)
                and _text(source.get("relation_type")).lower() in _DIRECT_RELATIONS
                and _text(source.get("source_url"), source.get("url"))
                for source in item_sources
            )
            if not has_direct_source and brand:
                for source in grounding_sources:
                    if not isinstance(source, dict):
                        continue
                    if brand not in str(source.get("title") or "").lower():
                        continue
                    item_sources.append(source)
                    break
            enriched_items.append(
                normalize_signal_item(
                    item,
                    sources=item_sources,
                    observed_at=generated_at,
                )
            )

        # Signals opens on external evidence first. The sort is stable, so the
        # model's importance order remains intact within each origin bucket.
        origin_rank = {_ORIGIN_EXTERNAL: 0, _ORIGIN_UNKNOWN: 1, _ORIGIN_OWNED: 2}
        enriched_items.sort(key=lambda item: origin_rank.get(str(item.get("content_origin") or ""), 1))

        freshness = _freshness_payload(content.get("generated_at"), d.get("created_at"), d.get("snapshot_date"))
        result_status = _result_status(
            contract_status,
            str(freshness.get("freshness_status") or "unknown"),
            grounded=bool(grounding_sources),
        )
        enriched = {
            **content,
            **freshness,
            "status": result_status,
            "result_status": result_status,
            "contract_status": contract_status,
            "contract_version": _RESULT_CONTRACT_VERSION,
            "is_ready": result_status == "ready",
            "snapshot_date": str(d.get("snapshot_date") or ""),
            "generated_at": generated_at,
            "items": enriched_items,
            "sources": grounding_sources,
            "evidence": grounding_sources,
            "validation_errors": validation_errors,
            "provenance": content.get("provenance") if isinstance(content.get("provenance"), dict) else {},
        }
        return {
            "available": bool(enriched_items),
            "status": result_status,
            "result_status": result_status,
            "contract_status": contract_status,
            "contract_version": _RESULT_CONTRACT_VERSION,
            "is_ready": result_status == "ready",
            "model": d.get("model"),
            "snapshot_date": enriched["snapshot_date"],
            "generated_at": generated_at,
            **freshness,
            "content": enriched,
        }
    except Exception:
        logger.debug("competitor_radar.get_failed", exc_info=True)
        return {
            "available": False,
            "status": "invalid",
            "result_status": "invalid",
            "is_ready": False,
            "reason": "read_error",
        }
