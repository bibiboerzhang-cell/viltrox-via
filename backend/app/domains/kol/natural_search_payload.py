"""Filtering and merge reducers for the local natural-search facade."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _filtered_list_row(
    raw: dict[str, Any],
    *,
    parsed: dict[str, Any],
    platform: str,
    matches_platform: Callable[[dict[str, Any], str], bool],
    row_text: Callable[[dict[str, Any]], str],
    int_value: Callable[..., int],
    row_level: Callable[[int], str],
    row_has_contact: Callable[[dict[str, Any]], bool],
    natural_match_score: Callable[[dict[str, Any], dict[str, Any]], tuple[int, list[str]]],
    clamp_score: Callable[[Any], int],
) -> dict[str, Any] | None:
    row = dict(raw)
    if platform and not matches_platform(row, platform):
        return None
    text = row_text(row)
    followers = int_value(
        row.get("snapshot_follower_count"), int_value(row.get("follower_count"))
    )
    has_structured_filter = any(
        parsed.get(key)
        for key in (
            "platform",
            "country",
            "level",
            "requires_contact",
            "requires_low_risk",
            "requires_collaboration",
        )
    )
    if parsed.get("requires_contact") and not row_has_contact(row):
        return None
    level = str(parsed.get("level") or "")
    if level and followers and row_level(followers) != level:
        return None
    keywords = [str(keyword).lower() for keyword in parsed.get("keywords") or []]
    if keywords and not has_structured_filter and not any(
        keyword in text for keyword in keywords
    ):
        return None
    country = str(parsed.get("country") or "").upper()
    row_country = str(row.get("country") or row.get("country_code") or "").upper()
    if country and row_country and country != row_country and country.lower() not in text:
        return None
    score, reasons = natural_match_score(row, parsed)
    row["natural_match_score"] = score
    row["natural_match_reasons"] = reasons
    row["score"] = max(
        clamp_score(row.get("score") or row.get("account_score") or row.get("product_fit")),
        score,
    )
    return row


def _merge_history_rows(
    rows: list[dict[str, Any]],
    pool_rows: list[dict[str, Any]],
    *,
    platform: str,
    matches_platform: Callable[[dict[str, Any], str], bool],
    normalize_history_handle: Callable[[Any], str],
) -> None:
    seen_keys = {
        (
            str(row.get("platform") or "").lower(),
            normalize_history_handle(row.get("handle") or row.get("channel_name") or ""),
        )
        for row in rows
    }
    for row in pool_rows:
        if platform and not matches_platform(row, platform):
            continue
        key = (
            str(row.get("platform") or "").lower(),
            normalize_history_handle(row.get("handle") or row.get("channel_name") or ""),
        )
        if key in seen_keys:
            continue
        rows.append(row)
        seen_keys.add(key)


def natural_search_payload(
    body: dict[str, Any],
    staff: dict[str, Any] | None,
    *,
    parse_natural_query: Callable[[str, str], dict[str, Any]],
    list_kols: Callable[..., dict[str, Any]],
    history_search: Callable[..., list[dict[str, Any]]],
    normalize_history_handle: Callable[[Any], str],
    matches_platform: Callable[[dict[str, Any], str], bool],
    row_text: Callable[[dict[str, Any]], str],
    int_value: Callable[..., int],
    row_level: Callable[[int], str],
    row_has_contact: Callable[[dict[str, Any]], bool],
    natural_match_score: Callable[[dict[str, Any], dict[str, Any]], tuple[int, list[str]]],
    clamp_score: Callable[[Any], int],
    mask_pool_item: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    query = str(body.get("query") or body.get("q") or "").strip()
    limit = max(1, min(200, int_value(body.get("limit"), 100)))
    parsed = parse_natural_query(query, str(body.get("platform") or ""))
    platform = str(parsed.get("platform") or "")
    raw_rows = list_kols(search="", platform=platform, limit=500, staff=staff).get("kols") or []
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        row = _filtered_list_row(
            raw,
            parsed=parsed,
            platform=platform,
            matches_platform=matches_platform,
            row_text=row_text,
            int_value=int_value,
            row_level=row_level,
            row_has_contact=row_has_contact,
            natural_match_score=natural_match_score,
            clamp_score=clamp_score,
        )
        if row is not None:
            rows.append(row)
    pool_rows = history_search(query, parsed, limit=limit)
    _merge_history_rows(
        rows,
        pool_rows,
        platform=platform,
        matches_platform=matches_platform,
        normalize_history_handle=normalize_history_handle,
    )
    rows.sort(
        key=lambda item: (
            int_value(item.get("natural_match_score")),
            int_value(item.get("snapshot_follower_count"), int_value(item.get("follower_count"))),
        ),
        reverse=True,
    )
    notes = ["规则解析版，复用现有 kols / snapshots / reports / vkpi_kol_pool 字段；未新增后端表。"]
    if not rows:
        notes.append("没有命中时不会伪造推荐，请先补候选池或放宽关键词。")
    return {
        "query": query,
        "parsed": parsed,
        "items": [mask_pool_item(item) for item in rows[:limit]],
        "method": "local_natural_search_v1_existing_kols",
        "degraded": True,
        "notes": notes,
    }
