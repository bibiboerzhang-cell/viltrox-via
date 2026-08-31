"""Pure result-shaping helpers for the category-tracks read model."""
from __future__ import annotations

from typing import Any, Mapping


def build_track_dimensions(
    docs: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    catalog: dict[str, Any],
    products: list[dict[str, Any]],
    ops: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    category_items: list[dict[str, Any]] = []
    for key, label, terms in ops["_category_tracks_def"]():
        track_docs = [d for d in docs if ops["_matches_any"](d["lower"], terms)]
        track_evidence = [
            row for row in evidence if ops["_matches_any"](row["blob"], terms)
        ]
        if key == "af_lens":
            track_docs += [d for d in docs if d not in track_docs and d["focals"]]
            track_evidence += [
                row
                for row in evidence
                if row not in track_evidence and row["focals"]
            ]
        sku_count = ops["_catalog_line_sku_count"](catalog, products, key)
        category_items.append(
            {
                "track_id": "cat:" + key,
                "dimension": "category",
                "key": key,
                "label": label,
                **ops["_signals_for"](track_docs, track_evidence, sku_count),
            }
        )
    ops["_finalize_dimension"](category_items)

    catalog_focals = set((catalog.get("focals") or {}).keys())
    voiced_focals: set[str] = set()
    for doc in docs:
        voiced_focals |= doc["focals"]
    for row in evidence:
        voiced_focals |= row["focals"]
    focal_items: list[dict[str, Any]] = []
    for focal in sorted(catalog_focals | voiced_focals, key=ops["_focal_mm"]):
        track_docs = [d for d in docs if focal in d["focals"]]
        track_evidence = [row for row in evidence if focal in row["focals"]]
        cat_slot = (catalog.get("focals") or {}).get(focal) or {}
        sku_count = ops["_int0"](cat_slot.get("sku_count"))
        if sku_count == 0 and not track_docs and not track_evidence:
            continue
        focal_items.append(
            {
                "track_id": "focal:" + focal,
                "dimension": "focal",
                "key": focal,
                "label": focal + " 焦段",
                "mm": ops["_focal_mm"](focal),
                "in_catalog": sku_count > 0,
                **ops["_signals_for"](track_docs, track_evidence, sku_count),
            }
        )
    ops["_finalize_dimension"](focal_items)
    return category_items, focal_items


def rank_tracks(
    items: list[dict[str, Any]], ops: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    opportunities: list[dict[str, Any]] = []
    no_go: list[dict[str, Any]] = []
    for item in items:
        reason = ops["_no_go_reason"](item)
        if reason is not None:
            no_go.append(
                {
                    "track_id": item["track_id"],
                    "dimension": item["dimension"],
                    "label": item["label"],
                    "reason": reason,
                    "demand_total": item["demand"]["total"],
                    "opportunity_score": item["opportunity"]["score"],
                }
            )
            continue
        if item["opportunity"]["score"] <= 0:
            continue
        opportunities.append(
            {
                "track_id": item["track_id"],
                "dimension": item["dimension"],
                "label": item["label"],
                "opportunity": item["opportunity"],
                "demand": {
                    key: value
                    for key, value in item["demand"].items()
                    if key not in ("wish_quotes", "voice_quotes")
                },
                "coverage": item["coverage"],
                "competitors": {
                    key: value
                    for key, value in item["competitors"].items()
                    if key != "example"
                },
                "evidence": ops["_evidence_bundle"](item),
            }
        )
    opportunities.sort(
        key=lambda item: (
            -item["opportunity"]["score"],
            -item["demand"]["total"],
            item["track_id"],
        )
    )
    no_go.sort(key=lambda item: (-item["demand_total"], item["track_id"]))
    return opportunities, no_go


def mount_signals(
    docs: list[dict[str, Any]], ops: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if not (
        ops["_MARKET_VOICE_AVAILABLE"]
        and ops["_MV_MOUNT_RE"] is not None
        and ops["_mv_mount_label"] is not None
    ):
        return []
    buckets: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        if not doc["is_wish"]:
            continue
        for match in ops["_MV_MOUNT_RE"].findall(doc["lower"]):
            label = ops["_mv_mount_label"](
                match if isinstance(match, str) else str(match)
            )
            if label:
                buckets.setdefault(label, []).append(doc)
    return [
        {"mount": mount, "wish_count": len(rows), "quotes": ops["_top_quotes"](rows)}
        for mount, rows in sorted(buckets.items(), key=lambda pair: -len(pair[1]))
    ]
