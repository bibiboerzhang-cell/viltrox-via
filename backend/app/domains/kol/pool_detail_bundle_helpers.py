"""Internal projections for :func:`kol.pool.detail_bundle`.

The public read boundary stays in ``pool.py``.  Dependencies that callers and
tests late-bind on that facade are passed in explicitly so moving these pure
projections does not freeze a monkeypatched function or connection factory.
"""
from __future__ import annotations

import json
from typing import Any, Callable


def detail_analysis_items(
    analysis_evidence: list[dict[str, Any]],
    analysis_cache: dict[tuple[str, str], dict[str, Any]],
    *,
    int_or_none: Callable[[Any], int | None],
    final_cache_projection: Callable[..., tuple[Any, str, str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[int, str], dict[str, int]]:
    analysis_items: list[dict[str, Any]] = []
    analysis_states: dict[int, str] = {}
    counts = {"ready": 0, "quality_incomplete": 0, "legacy_unverified": 0, "qa_ready": 0}
    for video in analysis_evidence:
        evidence_id = int_or_none(video.get("evidence_id") or video.get("id"))
        if not evidence_id:
            continue
        final_cache_entry = analysis_cache.get((str(evidence_id), "video_analysis_final_v1"))
        qa_entry = analysis_cache.get((str(evidence_id), "video_analysis_final_v1_keyframe_qa"))
        final_entry, final_state, final_reason, final_projection = final_cache_projection(
            final_cache_entry, target_id=str(evidence_id)
        )
        analysis_states[evidence_id] = final_state
        if final_state == "ready":
            counts["ready"] += 1
        elif final_state == "quality_incomplete":
            counts["quality_incomplete"] += 1
        elif final_state == "legacy_unverified":
            counts["legacy_unverified"] += 1
        if final_state == "ready" and qa_entry and qa_entry.get("status") == "ready":
            counts["qa_ready"] += 1
        else:
            qa_entry = None
        projected_video = dict(video)
        projected_video["has_final_v1_cache"] = final_state == "ready"
        projected_video["analysis_cache_state"] = final_state
        analysis_items.append(
            {
                "video": projected_video,
                "final_entry": final_entry,
                "raw_final_entry": final_cache_entry if final_state == "legacy_unverified" else None,
                "qa_entry": qa_entry,
                "state": final_state,
                "reason": final_reason,
                **(
                    {
                        key: final_projection[key]
                        for key in (
                            "terminal",
                            "revalidation_required",
                            "claim_status",
                            "cache_reuse_status",
                            "cache_id",
                            "reasons",
                        )
                        if key in final_projection
                    }
                    if final_state == "legacy_unverified" else {}
                ),
            }
        )
    return analysis_items, analysis_states, counts


def apply_analysis_states(
    videos: list[dict[str, Any]],
    analysis_states: dict[int, str],
    *,
    int_or_none: Callable[[Any], int | None],
) -> None:
    for video in videos:
        evidence_id = int_or_none(video.get("evidence_id") or video.get("id"))
        if evidence_id in analysis_states:
            video["has_final_v1_cache"] = analysis_states[evidence_id] == "ready"
            video["analysis_cache_state"] = analysis_states[evidence_id]


def apply_creator_gear(
    item: dict[str, Any],
    analysis_items: list[dict[str, Any]],
    raw_platform_data_for_derivation: Any,
    creator_gear_helpers: Callable[[], tuple[Any, Any]],
    kol_pool_id: int,
    *,
    logger: Any,
) -> None:
    try:
        aggregate_creator_gear, gear_from_text = creator_gear_helpers()
        gear_results: list[dict[str, Any]] = []
        for analysis in analysis_items:
            final_entry = analysis.get("final_entry")
            if not final_entry:
                continue
            result = final_entry.get("result")
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except Exception:
                    continue
            if isinstance(result, dict):
                gear_results.append(result)
        gear = aggregate_creator_gear(gear_results)
        if not gear.get("camera_body"):
            bio_gear = gear_from_text(
                str(item.get("bio") or "") + " " + str(raw_platform_data_for_derivation or "")
            )
            if bio_gear.get("camera_body"):
                bio_gear["uses_viltrox"] = any(
                    "viltrox" in lens.lower() for lens in (bio_gear.get("lens_brands") or [])
                )
                gear = bio_gear
        if gear.get("camera_body"):
            item["device_primary"] = gear["camera_body"]
            item["device_lenses"] = gear.get("lens_brands") or []
            item["device_uses_viltrox"] = bool(gear.get("uses_viltrox"))
            item["upgrade_window"] = (
                "low" if gear.get("uses_viltrox")
                else ("high" if gear.get("lens_brands") else "medium")
            )
    except Exception:
        logger.warning("creator_gear extract failed kol=%s", kol_pool_id, exc_info=True)


def apply_audience_language(
    item: dict[str, Any],
    audience_language_reader: Callable[[], Any],
    kol_pool_id: int,
    *,
    logger: Any,
) -> None:
    try:
        audience_language_for_kol = audience_language_reader()
        item["audience_languages"] = audience_language_for_kol(int(kol_pool_id))
    except Exception:
        logger.warning("audience_language failed kol=%s", kol_pool_id, exc_info=True)


def apply_audience_estimated(item: dict[str, Any]) -> None:
    try:
        raw = item.get("audience_estimated_json")
        audience = (
            json.loads(raw)
            if isinstance(raw, str) and raw.strip()
            else (raw if isinstance(raw, dict) else None)
        )
        item["audience_estimated"] = audience if isinstance(audience, dict) and audience else None
    except Exception:
        item["audience_estimated"] = None


def readiness_and_summary(
    *,
    kol_pool_id: int,
    item: dict[str, Any],
    analysis_evidence: list[dict[str, Any]],
    analysis_items: list[dict[str, Any]],
    llm_deep: Any,
    counts: dict[str, int],
    build_analysis_readiness: Callable[..., dict[str, Any]],
    evidence_quality_projection: Callable[[dict[str, Any]], dict[str, Any]],
    load_readiness_video_evidence: Callable[..., dict[str, Any]],
    get_conn: Callable[[], Any],
    int_or_none: Callable[[Any], int | None],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    readiness_sample = load_readiness_video_evidence(int(kol_pool_id), limit=200, conn=get_conn())
    analysis_readiness = build_analysis_readiness(
        item=item,
        videos=list(readiness_sample.get("items") or []),
        analysis_items=analysis_items,
        llm_deep=llm_deep,
        sample_scope=str(readiness_sample.get("sample_scope") or "active_video_evidence_up_to_200"),
        sample_limit=int_or_none(readiness_sample.get("limit")),
        sample_truncated=bool(readiness_sample.get("truncated")),
    )
    evidence_quality = evidence_quality_projection(analysis_readiness)
    summary = {
        "evidence_count": len(analysis_evidence),
        "ready_count": counts["ready"],
        "pending_count": max(
            0,
            len(analysis_evidence)
            - counts["ready"]
            - counts["quality_incomplete"]
            - counts["legacy_unverified"],
        ),
        "quality_incomplete_count": counts["quality_incomplete"],
        "legacy_unverified_count": counts["legacy_unverified"],
        "qa_ready_count": counts["qa_ready"],
        "source": "vkpi_analysis_cache",
        "analysis_readiness": {
            key: analysis_readiness.get(key)
            for key in (
                "level",
                "status",
                "claim_status",
                "decision_mode",
                "recommendation_status",
                "key_sample_count",
                "evidence_coverage",
                "blocking_gaps",
            )
        },
    }
    return analysis_readiness, evidence_quality, summary
