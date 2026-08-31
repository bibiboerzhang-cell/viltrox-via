"""Response projection helpers for the KOL focal matrix."""
from __future__ import annotations

from typing import Any, Callable, Iterable


def build_focal_cells(
    catalog_focals: dict[str, dict[str, Any]],
    focal_hits: dict[str, dict[str, Any]],
    *,
    focal_sort_mm: Callable[[str], float],
    average: Callable[[list[int]], int | None],
    line_labels: dict[str, str],
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    all_focals = sorted(set(catalog_focals) | set(focal_hits), key=focal_sort_mm)
    for focal in all_focals:
        catalog = catalog_focals.get(focal)
        hit = focal_hits.get(focal)
        cell: dict[str, Any] = {
            "focal": focal,
            "mm": focal_sort_mm(focal),
            "in_catalog": bool(catalog),
            "covered": bool(hit),
            "video_count": hit["video_count"] if hit else 0,
            "avg_views": average(hit["views"]) if hit else None,
            "title_hits": hit["title_hits"] if hit else 0,
            "deep_hits": hit["deep_hits"] if hit else 0,
            "top_example": hit["top"] if hit else None,
        }
        if catalog:
            cell["catalog"] = {
                "sku_count": catalog["sku_count"],
                "official_sku_count": catalog["official_sku_count"],
                "value_usd": round(catalog["value_usd"], 2),
                "max_price_usd": catalog["max_price_usd"],
                "flagship": catalog["flagship"] or None,
                "series": sorted(catalog["series"]),
                "lines": sorted(line_labels.get(line, line) for line in catalog["lines"]),
            }
        cells.append(cell)
    return cells


def build_line_cells(
    product_lines: Iterable[tuple[str, str, Any]],
    catalog_lines: dict[str, dict[str, Any]],
    line_hits: dict[str, dict[str, Any]],
    *,
    average: Callable[[list[int]], int | None],
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for key, label, _terms in product_lines:
        catalog = catalog_lines.get(key)
        hit = line_hits.get(key)
        if not catalog and not hit:
            continue
        cells.append({
            "key": key,
            "label": label,
            "in_catalog": bool(catalog),
            "catalog_sku_count": catalog["sku_count"] if catalog else 0,
            "covered": bool(hit),
            "video_count": hit["video_count"] if hit else 0,
            "avg_views": average(hit["views"]) if hit else None,
            "example_title": (hit["example"] or None) if hit else None,
        })
    return cells


def _ready_gap_block(
    *,
    catalog_gap_items: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    line_gaps: list[dict[str, Any]],
    creator_context: dict[str, Any],
    gap_focals: set[str],
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "status": "ready",
        "items": catalog_gap_items,
        "recommendations": recommendations,
        "product_lines": line_gaps,
        "creator_context": creator_context,
        "catalog_gap_count": len(gap_focals),
        "ranking_method": "mount_content_price_series_v2",
        "recommendation_status": (
            "ready"
            if recommendations
            else "insufficient_evidence"
            if gap_focals
            else "not_applicable"
        ),
    }
    if gap_focals and not recommendations:
        block["recommendation_reason"] = (
            "存在目录焦段空白,但机身/卡口/常用镜头证据不足或冲突;不生成伪 Top1。"
        )
    return block


def build_coverage_blocks(
    *,
    videos: list[dict[str, Any]],
    covered_focals: list[dict[str, Any]],
    zoom_mentions: dict[str, int],
    catalog_gap_items: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    line_gaps: list[dict[str, Any]],
    creator_context: dict[str, Any],
    gap_focals: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not videos:
        return (
            {
                "status": "empty",
                "reason": "该 KOL 暂无有效视频证据(vkpi_kol_video_evidence 空/全 inactive),焦段覆盖无从统计。",
            },
            {
                "status": "empty",
                "reason": "没有任何视频证据时全目录皆是空白,排序无意义 — 先补充 evidence 再看可切入焦段。",
                "items": [],
                "recommendations": [],
                "creator_context": creator_context,
                "catalog_gap_count": len(gap_focals),
                "recommendation_status": "insufficient_evidence",
            },
        )
    gaps = _ready_gap_block(
        catalog_gap_items=catalog_gap_items,
        recommendations=recommendations,
        line_gaps=line_gaps,
        creator_context=creator_context,
        gap_focals=gap_focals,
    )
    if not covered_focals:
        return (
            {
                "status": "empty",
                "reason": f"扫了 {len(videos)} 条视频(标题+深析文本),没提到任何具体焦段 — 词表/正则不硬猜。",
            },
            gaps,
        )
    covered = {
        "status": "ready",
        "focal_count": len(covered_focals),
        "zoom_mentions": [
            {"range_mm": key, "video_count": value}
            for key, value in sorted(zoom_mentions.items(), key=lambda item: item[1], reverse=True)
        ][:6],
    }
    return covered, gaps


def matched_products_block(items: list[dict[str, Any]]) -> dict[str, Any]:
    if items:
        return {"status": "ready", "items": items}
    return {
        "status": "empty",
        "reason": "视频文本里没同时命中「焦段+系列/光圈+Viltrox 语境」,不硬贴我方 SKU。",
    }
