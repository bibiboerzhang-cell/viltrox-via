"""Read-only external market signal smoke for Google News RSS and RSS feeds.

The service normalizes a tiny allowlisted sample into the same evidence shape
used by V-KPI Intelligence Cards. Defaults do not make HTTP calls, write the DB,
trigger sync, enqueue tasks, or call an LLM.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from app.domains.market.signal_taxonomy import (
    TIER1_GROUPS,
    TIER2_GROUPS,
    dedupe_keywords,
    keyword_groups,
    keyword_hits,
    summarize_keyword_groups,
)


ALLOWED_HOSTS = {
    "news.google.com",
    "petapixel.com",
    "www.petapixel.com",
    "www.dpreview.com",
    "dpreview.com",
    "www.cined.com",
    "cined.com",
    "www.fstoppers.com",
    "fstoppers.com",
}

SOURCE_GROUP_LABELS = {
    "google_news_core": "Google News 核心查询",
    "rss_industry_watch": "行业 RSS 白名单",
    "lens_competitor_watch": "镜头竞品",
    "lighting_watch": "灯光业务",
    "cross_industry_watch": "跨产业链",
    "cinema_accessory_watch": "影视配件",
}

SOURCE_GROUP_PLAN_META = {
    "google_news_core": {
        "priority": "high",
        "rank": 10,
        "reason": "先看 Viltrox / LAB / 品牌自身是否有当天外部信号。",
    },
    "lens_competitor_watch": {
        "priority": "high",
        "rank": 20,
        "reason": "镜头竞品是主战场，优先监控 Sigma / Tamron / Samyang 等动态。",
    },
    "cross_industry_watch": {
        "priority": "high",
        "rank": 30,
        "reason": "DJI / Insta360 / Hollyland 等跨产业链动作会影响创作者预算与内容方向。",
    },
    "rss_industry_watch": {
        "priority": "medium",
        "rank": 40,
        "reason": "行业媒体用于补充 Google News 没覆盖到的评测、发布和创作者趋势。",
    },
    "lighting_watch": {
        "priority": "low",
        "rank": 50,
        "reason": "灯光业务先作为低频观察源，避免和镜头主线争抢执行窗口。",
    },
    "cinema_accessory_watch": {
        "priority": "low",
        "rank": 60,
        "reason": "影视配件信号用于产品生态判断，默认低频候选。",
    },
}

SOURCE_KEY_PLAN_RANK = {
    "google_news_viltrox": 10,
    "google_news_lens_competitors": 10,
    "google_news_cinema_ecosystem": 10,
    "rss_petapixel": 10,
    "rss_dpreview": 20,
    "rss_cined": 30,
    "rss_fstoppers": 40,
    "google_news_lighting": 10,
    "google_news_cinema_accessory": 10,
}

DEFAULT_SOURCES: list[dict[str, Any]] = [
    {
        "source_key": "google_news_viltrox",
        "source_group": "google_news_core",
        "provider": "google_news",
        "source_type": "google_news_rss",
        "query": "Viltrox lens OR Viltrox LAB",
        "purpose": "brand_and_product_watch",
        "cadence": "manual_smoke_then_daily_candidate",
    },
    {
        "source_key": "google_news_lens_competitors",
        "source_group": "lens_competitor_watch",
        "provider": "google_news",
        "source_type": "google_news_rss",
        "query": "Sigma OR Tamron OR Samyang lens review camera",
        "purpose": "lens_competitor_watch",
        "cadence": "manual_smoke_then_daily_candidate",
    },
    {
        "source_key": "google_news_lighting",
        "source_group": "lighting_watch",
        "provider": "google_news",
        "source_type": "google_news_rss",
        "query": "Aputure OR Godox OR Nanlite creator lighting",
        "purpose": "lighting_competitor_watch",
        "cadence": "manual_smoke_then_weekly_candidate",
    },
    {
        "source_key": "google_news_cinema_accessory",
        "source_group": "cinema_accessory_watch",
        "provider": "google_news",
        "source_type": "google_news_rss",
        "query": "SmallRig OR Tilta cinema camera accessory",
        "purpose": "cinema_accessory_watch",
        "cadence": "manual_smoke_then_weekly_candidate",
    },
    {
        "source_key": "google_news_cinema_ecosystem",
        "source_group": "cross_industry_watch",
        "provider": "google_news",
        "source_type": "google_news_rss",
        "query": "DJI Insta360 Hollyland camera creator",
        "purpose": "cross_industry_watch",
        "cadence": "manual_smoke_then_daily_candidate",
    },
    {
        "source_key": "rss_petapixel",
        "source_group": "rss_industry_watch",
        "provider": "rss",
        "source_type": "rss_feed",
        "feed_url": "https://petapixel.com/feed/",
        "purpose": "industry_news_watch",
        "cadence": "manual_smoke_then_daily_candidate",
    },
    {
        "source_key": "rss_dpreview",
        "source_group": "rss_industry_watch",
        "provider": "rss",
        "source_type": "rss_feed",
        "feed_url": "https://www.dpreview.com/feeds/news.xml",
        "purpose": "industry_news_watch",
        "cadence": "manual_smoke_then_daily_candidate",
    },
    {
        "source_key": "rss_fstoppers",
        "source_group": "rss_industry_watch",
        "provider": "rss",
        "source_type": "rss_feed",
        "feed_url": "https://fstoppers.com/feed",
        "purpose": "creator_news_watch",
        "cadence": "manual_smoke_then_weekly_candidate",
    },
    {
        "source_key": "rss_cined",
        "source_group": "rss_industry_watch",
        "provider": "rss",
        "source_type": "rss_feed",
        "feed_url": "https://www.cined.com/feed/",
        "purpose": "cinema_news_watch",
        "cadence": "manual_smoke_then_weekly_candidate",
    },
]


def _now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _hash(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def _is_allowlisted_url(url: str) -> bool:
    host = _host(url)
    return host in ALLOWED_HOSTS


def google_news_rss_url(query: str) -> str:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }
    )
    return f"https://news.google.com/rss/search?{params}"


def _source_url(source: dict[str, Any]) -> str:
    if source.get("source_type") == "google_news_rss":
        return google_news_rss_url(_text(source.get("query")))
    return _text(source.get("feed_url"), source.get("url"))


def _fetch_url_text(url: str, *, timeout_seconds: int = 8) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "V-KPI market signal smoke/0.1 (+read-only; contact: ops)",
            "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
        },
    )
    with urllib.request.urlopen(req, timeout=max(1, int(timeout_seconds or 8))) as response:  # noqa: S310 - allowlisted URL only.
        raw = response.read(1_000_000)
    return raw.decode("utf-8", errors="replace")


def _child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and _text(found.text):
            return _text(found.text)
    for child in list(node):
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in {name.lower() for name in names} and _text(child.text):
            return _text(child.text)
    return ""


def _published_at(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except Exception:
        return value


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def _parse_feed_items(xml_text: str, source: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except Exception as exc:
        raise ValueError(f"invalid RSS/XML feed: {exc}") from exc

    nodes = list(root.findall(".//item"))
    if not nodes:
        nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "entry"]

    rows: list[dict[str, Any]] = []
    for node in nodes[: max(0, int(limit or 0))]:
        title = _child_text(node, ("title",))
        link = _child_text(node, ("link",))
        if not link:
            link_node = node.find("link")
            if link_node is not None:
                link = _text(link_node.attrib.get("href"))
        description = _strip_html(_child_text(node, ("description", "summary", "content")))
        published = _published_at(_child_text(node, ("pubDate", "published", "updated")))
        rows.append(
            {
                "provider": source.get("provider"),
                "source_key": source.get("source_key"),
                "source_type": source.get("source_type"),
                "source_url": link,
                "title": title,
                "summary": description[:1000],
                "published_at": published,
                "query": source.get("query", ""),
                "feed_url": _source_url(source),
                "purpose": source.get("purpose", ""),
            }
        )
    return rows


def _score_item(item: dict[str, Any]) -> float:
    groups = item.get("keyword_groups") if isinstance(item.get("keyword_groups"), dict) else {}
    tier1_hits = sum(len(groups.get(name, [])) for name in TIER1_GROUPS)
    tier2_hits = sum(len(groups.get(name, [])) for name in TIER2_GROUPS)
    viltrox_hits = len(groups.get("viltrox_products", []))
    generic_hits = len(groups.get("generic_imaging_terms", []))
    score = 0.12 + tier1_hits * 0.14 + tier2_hits * 0.09 + viltrox_hits * 0.18 + min(generic_hits, 5) * 0.025
    return round(max(0.0, min(1.0, score)), 3)


def _normalize_item(raw: dict[str, Any], index: int) -> dict[str, Any]:
    text = " ".join([_text(raw.get("title")), _text(raw.get("summary"), raw.get("description"))])
    hits = keyword_hits(text)
    groups = keyword_groups(hits)
    uid = _text(raw.get("source_uid")) or f"external:{_hash(raw.get('provider'), raw.get('source_url'), raw.get('title'), index)}"
    item = {
        "source_uid": uid,
        "provider": _text(raw.get("provider"), "external"),
        "source_key": _text(raw.get("source_key"), "manual_external"),
        "source_type": _text(raw.get("source_type"), "external_item"),
        "source_url": _text(raw.get("source_url"), raw.get("url")),
        "title": _text(raw.get("title"))[:500],
        "summary": _text(raw.get("summary"), raw.get("description"))[:1000],
        "published_at": _text(raw.get("published_at"), raw.get("published")),
        "query": _text(raw.get("query")),
        "feed_url": _text(raw.get("feed_url")),
        "purpose": _text(raw.get("purpose")),
        "keyword_hits": hits,
        "keyword_groups": groups,
    }
    item["score"] = _score_item(item)
    item["business_signal"] = bool(groups.get("viltrox_products") or any(groups.get(name) for name in TIER1_GROUPS | TIER2_GROUPS))
    return item


def _source_status(source: dict[str, Any], *, execute_http_fetch: bool, fetched: bool, error: str = "") -> dict[str, Any]:
    url = _source_url(source)
    return {
        "source_key": source.get("source_key"),
        "source_group": source.get("source_group", ""),
        "provider": source.get("provider"),
        "source_type": source.get("source_type"),
        "purpose": source.get("purpose"),
        "url": url,
        "allowlisted": _is_allowlisted_url(url),
        "status": "fetched" if fetched else "skipped" if not execute_http_fetch else "error",
        "error": error,
    }


def _filter_sources(sources: list[dict[str, Any]], *, source_key: str = "", source_group: str = "") -> list[dict[str, Any]]:
    rows = [dict(source) for source in sources]
    if source_key:
        rows = [source for source in rows if source.get("source_key") == source_key]
    if source_group:
        rows = [source for source in rows if source.get("source_group") == source_group]
    return rows


def _plan_rank(source: dict[str, Any]) -> tuple[int, int, str]:
    group = _text(source.get("source_group"))
    meta = SOURCE_GROUP_PLAN_META.get(group, {})
    source_key = _text(source.get("source_key"))
    return int(meta.get("rank") or 999), int(SOURCE_KEY_PLAN_RANK.get(source_key, 999)), source_key


def _plan_source_row(source: dict[str, Any]) -> dict[str, Any]:
    url = _source_url(source)
    return {
        "source_key": source.get("source_key"),
        "source_group": source.get("source_group", ""),
        "source_group_label": SOURCE_GROUP_LABELS.get(str(source.get("source_group") or ""), ""),
        "provider": source.get("provider"),
        "source_type": source.get("source_type"),
        "purpose": source.get("purpose"),
        "cadence": source.get("cadence", ""),
        "query": source.get("query", ""),
        "url": url,
        "allowlisted": _is_allowlisted_url(url),
    }


def build_external_source_matrix(
    *,
    sources: list[dict[str, Any]] | None = None,
    source_group: str = "",
) -> dict[str, Any]:
    """Return the allowlisted external source matrix with no HTTP calls."""

    rows = _filter_sources([dict(source) for source in (sources or DEFAULT_SOURCES)], source_group=source_group)
    matrix: list[dict[str, Any]] = []
    for source in rows:
        url = _source_url(source)
        matrix.append(
            {
                "source_key": source.get("source_key"),
                "source_group": source.get("source_group", ""),
                "source_group_label": SOURCE_GROUP_LABELS.get(str(source.get("source_group") or ""), ""),
                "provider": source.get("provider"),
                "source_type": source.get("source_type"),
                "purpose": source.get("purpose"),
                "cadence": source.get("cadence", ""),
                "query": source.get("query", ""),
                "url": url,
                "allowlisted": _is_allowlisted_url(url),
                "default_http_fetch": False,
                "default_write_db": False,
                "default_llm_call": False,
            }
        )
    groups: dict[str, int] = {}
    for row in matrix:
        key = _text(row.get("source_group"), "ungrouped")
        groups[key] = groups.get(key, 0) + 1
    checks = {
        "no_http_by_default": True,
        "no_db_write_by_default": True,
        "allowlisted_sources_only": all(row.get("allowlisted") for row in matrix),
        "source_group_filter_matched": not source_group or bool(matrix),
    }
    return {
        "mode": "market_external_source_matrix_v0",
        "generated_at": _now_z(),
        "provider_calls": False,
        "external_http_calls": False,
        "llm_calls": False,
        "write_db": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "source_count": len(matrix),
            "source_groups": groups,
            "selected_group": source_group,
        },
        "sources": matrix,
        "policy": {
            "live_fetch_requires_explicit_flag": True,
            "manager_required_for_api_live_fetch": True,
            "daily_candidate_not_enabled": True,
        },
    }


def build_external_daily_candidate_plan(
    *,
    sources: list[dict[str, Any]] | None = None,
    source_group: str = "",
    max_http_calls: int = 6,
    limit_per_source: int = 5,
) -> dict[str, Any]:
    """Return a read-only daily candidate plan for external market signals.

    This answers what should be run next without running it. The output is
    intentionally explicit about every side effect staying disabled.
    """

    rows = _filter_sources([dict(source) for source in (sources or DEFAULT_SOURCES)], source_group=source_group)
    bounded_calls = max(0, min(int(max_http_calls or 0), 20))
    per_source_limit = max(1, min(int(limit_per_source or 5), 25))
    eligible_sources = [source for source in sorted(rows, key=_plan_rank) if _is_allowlisted_url(_source_url(source))]
    planned_sources = eligible_sources[:bounded_calls]
    skipped_sources = eligible_sources[bounded_calls:]

    group_map: dict[str, dict[str, Any]] = {}
    for source in planned_sources:
        group = _text(source.get("source_group"), "ungrouped")
        meta = SOURCE_GROUP_PLAN_META.get(group, {})
        row = group_map.setdefault(
            group,
            {
                "source_group": group,
                "label": SOURCE_GROUP_LABELS.get(group, group),
                "priority": meta.get("priority", "low"),
                "rank": int(meta.get("rank") or 999),
                "reason": meta.get("reason", ""),
                "planned_http_calls": 0,
                "planned_item_limit": 0,
                "estimated_cost_usd": 0.0,
                "requires_human_ack": True,
                "execution_mode": "manual_smoke_only",
                "write_db_after_review_only": True,
                "candidate_sources": [],
            },
        )
        row["planned_http_calls"] += 1
        row["planned_item_limit"] += per_source_limit
        row["candidate_sources"].append(_plan_source_row(source))

    groups = sorted(group_map.values(), key=lambda row: (int(row.get("rank") or 999), str(row.get("source_group") or "")))
    source_groups: dict[str, int] = {}
    for source in rows:
        group = _text(source.get("source_group"), "ungrouped")
        source_groups[group] = source_groups.get(group, 0) + 1

    planned_http_calls = len(planned_sources)
    planned_item_limit = planned_http_calls * per_source_limit
    checks = {
        "no_http_call": True,
        "no_db_write": True,
        "no_llm_call": True,
        "no_sync_triggered": True,
        "no_task_enqueued": True,
        "human_ack_required": all(group.get("requires_human_ack") for group in groups) if groups else True,
        "bounded_http_calls": planned_http_calls <= bounded_calls,
        "allowlisted_sources_only": all(_is_allowlisted_url(_source_url(source)) for source in planned_sources),
        "source_filter_matched": not source_group or bool(rows),
    }
    return {
        "mode": "market_external_daily_candidate_plan_v0",
        "generated_at": _now_z(),
        "provider_calls": False,
        "external_http_calls": False,
        "llm_calls": False,
        "gemini_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "source_count": len(rows),
            "source_groups": source_groups,
            "selected_source_group": source_group,
            "max_http_calls": bounded_calls,
            "limit_per_source": per_source_limit,
            "planned_group_count": len(groups),
            "planned_source_count": len(planned_sources),
            "skipped_source_count": len(skipped_sources),
            "planned_http_calls": planned_http_calls,
            "planned_item_limit": planned_item_limit,
            "estimated_cost_usd": 0.0,
            "blocked_auto_run": True,
        },
        "groups": groups,
        "planned_sources": [_plan_source_row(source) for source in planned_sources],
        "skipped_sources": [_plan_source_row(source) for source in skipped_sources],
        "policy": {
            "read_only_plan": True,
            "manual_live_fetch_only": True,
            "requires_human_ack_before_http": True,
            "writes_require_reviewed_raw_signal_package": True,
            "llm_summary_requires_quality_gate": True,
            "recommended_next_command_template": (
                "PYTHONPATH=backend .venv/bin/python scripts/vkpi_market_external_signal_smoke.py "
                "--execute-http-fetch --source-group <source_group> --limit-per-source {limit_per_source} --out-dir runtime/ops"
            ).format(limit_per_source=per_source_limit),
        },
    }


def build_external_signal_smoke(
    *,
    sources: list[dict[str, Any]] | None = None,
    raw_items: list[dict[str, Any]] | None = None,
    execute_http_fetch: bool = False,
    source_key: str = "",
    source_group: str = "",
    limit_per_source: int = 5,
    timeout_seconds: int = 8,
) -> dict[str, Any]:
    """Build a no-write external signal smoke report."""

    all_sources = _filter_sources(
        [dict(source) for source in (sources or DEFAULT_SOURCES)],
        source_key=source_key,
        source_group=source_group,
    )

    limit = max(1, min(int(limit_per_source or 5), 25))
    generated_at = _now_z()
    statuses: list[dict[str, Any]] = []
    fetched_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for source in all_sources:
        url = _source_url(source)
        if not execute_http_fetch:
            statuses.append(_source_status(source, execute_http_fetch=False, fetched=False))
            continue
        if not _is_allowlisted_url(url):
            error = f"source host is not allowlisted: {_host(url)}"
            statuses.append(_source_status(source, execute_http_fetch=True, fetched=False, error=error))
            errors.append({"source_key": _text(source.get("source_key")), "error": error})
            continue
        try:
            xml_text = _fetch_url_text(url, timeout_seconds=timeout_seconds)
            fetched = _parse_feed_items(xml_text, source, limit=limit)
            statuses.append(_source_status(source, execute_http_fetch=True, fetched=True))
            fetched_rows.extend(fetched)
        except Exception as exc:
            error = str(exc)[:500]
            statuses.append(_source_status(source, execute_http_fetch=True, fetched=False, error=error))
            errors.append({"source_key": _text(source.get("source_key")), "error": error})

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate([*(raw_items or []), *fetched_rows]):
        if not isinstance(item, dict):
            continue
        row = _normalize_item(item, index)
        uid = row["source_uid"]
        if not uid or uid in seen:
            continue
        seen.add(uid)
        normalized.append(row)

    signal_items = [item for item in normalized if item.get("business_signal")]
    keyword_summary = summarize_keyword_groups(normalized)
    top_candidates = sorted(signal_items, key=lambda item: (float(item.get("score") or 0), len(item.get("keyword_hits") or [])), reverse=True)[:10]
    external_http_calls = bool(execute_http_fetch)
    checks = {
        "no_db_write": True,
        "no_sync_triggered": True,
        "no_llm_call": True,
        "limit_is_bounded": limit <= 25,
        "allowlisted_sources_only": all(status.get("allowlisted") for status in statuses) if statuses else True,
        "source_filter_matched": not (source_key or source_group) or bool(all_sources),
        "default_http_off": not execute_http_fetch or external_http_calls,
        "keywords_applied": all("keyword_hits" in item and "keyword_groups" in item for item in normalized),
        "live_fetch_returned_items": not execute_http_fetch or bool(normalized),
    }
    return {
        "mode": "market_external_signal_smoke_v0",
        "generated_at": generated_at,
        "provider_calls": external_http_calls,
        "external_http_calls": external_http_calls,
        "llm_calls": False,
        "gemini_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "sources_requested": len(all_sources),
            "sources_fetched": sum(1 for status in statuses if status.get("status") == "fetched"),
            "selected_source_key": source_key,
            "selected_source_group": source_group,
            "items_loaded": len(normalized),
            "business_signal_items": len(signal_items),
            "keyword_hit_items": keyword_summary["keyword_hit_posts"],
            "tier1_mentions": keyword_summary["tier1_mentions"],
            "tier2_mentions": keyword_summary["tier2_mentions"],
            "viltrox_product_mentions": keyword_summary["viltrox_product_mentions"],
            "tier2_recommended_next_run": keyword_summary["tier2_recommended_next_run"],
            "top_keywords": keyword_summary["top_keywords"],
        },
        "source_statuses": statuses,
        "items": normalized,
        "top_candidates": top_candidates,
        "errors": errors,
        "policy": {
            "allowlisted_http_only": True,
            "manual_live_fetch_only": True,
            "raw_sources_before_db_write": True,
            "llm_summary_requires_quality_gate": True,
            "keywords": dedupe_keywords(),
        },
    }


def build_external_signal_smoke_from_file(path: str | Path) -> dict[str, Any]:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("external signal smoke input must be a JSON object")
    raw_items = report.get("items") if isinstance(report.get("items"), list) else []
    sources = report.get("sources") if isinstance(report.get("sources"), list) else None
    return build_external_signal_smoke(sources=sources, raw_items=raw_items)
