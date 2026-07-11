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
from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.domains.market.ai_today import _freshness_payload, _generate, _market_sources, _parse_json

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


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


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


def _generation_parts(result: Any) -> tuple[str, str, list[dict[str, Any]]]:
    """Accept both the historical two-tuple and the grounded three-tuple."""
    if isinstance(result, tuple) and len(result) >= 3:
        raw_sources = result[2] if isinstance(result[2], list) else []
        return str(result[0] or ""), str(result[1] or ""), raw_sources
    if isinstance(result, tuple) and len(result) >= 2:
        return str(result[0] or ""), str(result[1] or ""), []
    return "", "", []


def _ensure_schema() -> None:
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_competitor_radar (
            snapshot_date DATE PRIMARY KEY,
            content_json  TEXT NOT NULL,
            model         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()


def generate_competitor_radar() -> dict[str, Any]:
    """每早一次:预算闸 → Gemini(Google 接地)查外部市场信号 → 存库。"""
    if not budget_guard.check_budget(_BUDGET_SCOPE, _EST_COST):
        logger.info("competitor_radar.budget_blocked", extra={"scope": _BUDGET_SCOPE})
        return {"status": "budget_blocked"}

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
    raw, model_used, sources = _generation_parts(_generate(prompt))
    content = _parse_json(raw)
    items = content.get("items") if isinstance(content, dict) else None
    if not isinstance(items, list) or not items:
        logger.warning("competitor_radar.parse_empty")
        return {"status": "parse_empty"}

    try:
        budget_guard.record_cost(scope=_BUDGET_SCOPE, cost_usd=_EST_COST)
    except Exception:
        logger.debug("competitor_radar.record_cost_failed", exc_info=True)

    generated_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    grounding_sources = _dedupe_sources(sources, observed_at=generated_at, default_relation="grounding")
    if not grounding_sources:
        logger.warning(
            "competitor_radar.ungrounded_not_persisted",
            extra={"model": model_used, "reason": "no_grounded_citations"},
        )
        return {
            "status": "ungrounded",
            "reason": "no_grounded_citations",
            "model": str(model_used or ""),
            "sources": [],
            "generated_at": generated_at,
        }
    clean: list[dict[str, Any]] = []
    for it in items[:6]:
        d = it if isinstance(it, dict) else {}
        brand = str(d.get("brand") or "")[:40]
        item_sources = list(d.get("sources")) if isinstance(d.get("sources"), list) else []
        item_url = _text(d.get("source_url"), d.get("url"))
        for source in grounding_sources:
            source_url = _text(source.get("source_url"), source.get("url"))
            source_blob = " ".join(
                str(source.get(key) or "").lower()
                for key in ("title", "source_url", "url")
            )
            if (item_url and source_url == item_url) or (not item_url and brand and brand.lower() in source_blob):
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
    payload = {
        "items": clean,
        "sources": grounding_sources,
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
    return {"status": "ok", "items": len(clean)}


def get_competitor_radar() -> dict[str, Any]:
    """读最新竞品雷达，并只读补齐每条信号的原始来源与新鲜度。"""
    try:
        _ensure_schema()
        row = get_conn().execute(
            "SELECT snapshot_date, content_json, model, created_at "
            "FROM vkpi_competitor_radar ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"available": False, "reason": "not_generated_yet"}
        d = dict(row)
        content = json.loads(d.get("content_json") or "{}")
        content = content if isinstance(content, dict) else {}
        items = content.get("items") if isinstance(content.get("items"), list) else []
        brands = [str(item.get("brand") or "") for item in items if isinstance(item, dict)]
        generated_at = _text(content.get("generated_at"), d.get("created_at"), d.get("snapshot_date"))
        market_sources = _dedupe_sources(
            _market_sources(brands, limit=12),
            observed_at=generated_at,
            default_relation="brand_context",
        )
        grounding_sources = _dedupe_sources(
            content.get("sources") if isinstance(content.get("sources"), list) else [],
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
        enriched = {
            **content,
            **freshness,
            "snapshot_date": str(d.get("snapshot_date") or ""),
            "items": enriched_items,
            "sources": grounding_sources,
        }
        return {
            "available": True,
            "model": d.get("model"),
            "snapshot_date": enriched["snapshot_date"],
            **freshness,
            "content": enriched,
        }
    except Exception:
        logger.debug("competitor_radar.get_failed", exc_info=True)
        return {"available": False, "reason": "read_error"}
