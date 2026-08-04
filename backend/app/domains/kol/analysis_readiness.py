"""Conservative read-side evidence readiness for one KOL detail bundle.

The contract is descriptive only.  It never writes or re-scores
``viltrox_fit_score`` and it deliberately abstains when the persisted evidence
cannot support a requested decision scope.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from app.db.connection import get_conn, is_postgres_runtime


VERSION = "kol_analysis_readiness_v1"
THRESHOLDS = {
    "overall_min_video_samples": 5,
    "overall_min_view_count_ratio": 0.8,
    "overall_min_deep_ready": 3,
    "overall_min_deep_ratio": 0.5,
    "minimum_usable_video_samples": 3,
    "minimum_usable_view_count_ratio": 0.5,
    "fresh_max_age_days": 90,
    "stale_after_days": 180,
    "content_fit_min_full_video_proven": 1,
}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator > 0 else None


def _evidence_id(value: dict[str, Any]) -> int | None:
    raw = value.get("evidence_id") or value.get("id")
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest(values: Iterable[Any]) -> datetime | None:
    parsed = [item for value in values if (item := _parse_time(value))]
    return max(parsed) if parsed else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _age_days(value: datetime | None, now: datetime) -> int | None:
    if not value:
        return None
    return max(0, (now - value).days)


def _walk_values(value: Any, keys: set[str], *, depth: int = 0) -> list[Any]:
    if depth > 8:
        return []
    output: list[Any] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).strip().lower() in keys:
                output.append(nested)
            output.extend(_walk_values(nested, keys, depth=depth + 1))
    elif isinstance(value, list):
        for nested in value:
            output.extend(_walk_values(nested, keys, depth=depth + 1))
    return output


_VIDEO_EVIDENCE_TYPES = {"video", "short", "shorts", "reel", "reels", "clip"}
_TIMESTAMP_KEYS = {
    "timestamp",
    "timestamps",
    "time",
    "timecode",
    "time_code",
    "start_time",
    "end_time",
}
_VILTROX_SIGNAL_KEYS = {
    "viltrox_detected",
    "viltrox_product",
    "viltrox_products",
    "viltrox_products_all",
}


def _normalized_media_kind(video: dict[str, Any]) -> str:
    """Treat legacy blank evidence kinds as video, but never images as video."""

    media_kind = str(video.get("media_kind") or "").strip().lower()
    evidence_type = str(video.get("evidence_type") or "").strip().lower()
    return media_kind or evidence_type or "video"


def _is_video_evidence(video: dict[str, Any]) -> bool:
    return _normalized_media_kind(video) in _VIDEO_EVIDENCE_TYPES


def load_readiness_video_evidence(
    kol_pool_id: int,
    *,
    limit: int = 200,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Load the active analysis denominator independently from drawer pagination.

    ``limit + 1`` is selected so callers can disclose a partial denominator
    rather than silently presenting the first 200 rows as the complete
    evidence population.  The query deliberately uses only long-lived schema
    columns so the read path works in both SQLite test/dev and Postgres.
    """

    safe_limit = max(1, min(200, int(limit or 200)))
    active_predicate = "is_active IS NOT FALSE" if is_postgres_runtime() else "COALESCE(is_active, 1) != 0"
    rows = (conn or get_conn()).execute(
        f"""
        SELECT
            id AS evidence_id,
            id,
            view_count,
            duration_seconds,
            publish_date,
            posted_at,
            media_kind,
            evidence_type,
            created_at,
            updated_at
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id=?
          AND {active_predicate}
          AND LOWER(COALESCE(
                NULLIF(TRIM(media_kind), ''),
                NULLIF(TRIM(evidence_type), ''),
                'video'
              ))
              IN ('video', 'short', 'shorts', 'reel', 'reels', 'clip')
        ORDER BY COALESCE(publish_date, posted_at, updated_at, created_at) DESC,
                 id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), safe_limit + 1),
    ).fetchall()
    truncated = len(rows) > safe_limit
    items = []
    for row in rows[:safe_limit]:
        item = dict(row)
        # Rows admitted by the effective media-kind query are videos, including
        # legacy blank kind.  ``media_kind`` must win over the historical
        # ``evidence_type`` because older image/carousel rows can still carry
        # evidence_type='video'.
        # Set an explicit projection so downstream clients cannot reclassify a
        # blank legacy evidence_type as an unknown/non-video media item.
        item["media_kind"] = "video"
        items.append(item)
    return {
        "items": items,
        "limit": safe_limit,
        "truncated": truncated,
        "sample_scope": "active_video_evidence_up_to_200",
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "full", "complete"}


def _full_video_receipt(entry: dict[str, Any], video: dict[str, Any]) -> tuple[bool, str]:
    """Require an explicit scope or duration receipt; File API method alone is not proof."""
    result = _as_dict(entry.get("result"))
    provenance = _as_dict(result.get("provenance"))
    coverage_containers = [
        _as_dict(result.get("analysis_coverage")),
        _as_dict(result.get("coverage_receipt")),
        _as_dict(provenance.get("analysis_coverage")),
    ]
    coverage_containers = [container for container in coverage_containers if container]
    scope_values = [
        container.get(key)
        for container in coverage_containers
        for key in ("analysis_scope", "coverage_scope", "media_scope", "input_scope")
    ]
    explicit_scope = any(
        str(value or "").strip().lower() in {"full_video", "full_source", "full_duration"}
        for value in scope_values
    )
    full_flags = [
        container.get(key)
        for container in coverage_containers
        for key in ("full_video", "full_video_analyzed", "source_complete")
    ]
    if explicit_scope and (not full_flags or any(_truthy(value) for value in full_flags)):
        return True, "explicit_full_video_scope"
    if any(_truthy(value) for value in full_flags):
        return True, "explicit_full_video_flag"

    source_durations = [
        container.get(key)
        for container in coverage_containers
        for key in ("source_duration_seconds", "video_duration_seconds")
    ]
    analyzed_durations = [
        container.get(key)
        for container in coverage_containers
        for key in ("analyzed_duration_seconds", "covered_duration_seconds")
    ]
    source_duration = next((_number(value) for value in source_durations if _number(value) is not None), None)
    analyzed_duration = next((_number(value) for value in analyzed_durations if _number(value) is not None), None)
    if source_duration and analyzed_duration and source_duration > 0 and analyzed_duration / source_duration >= 0.95:
        return True, "duration_coverage_at_least_95pct"

    # The persisted method proves that one local file was sent to Gemini, but
    # it does not prove that the downloader captured the complete source video.
    method = str(result.get("method") or _as_dict(result.get("provenance")).get("method") or "").lower()
    if "fileapi" in method:
        return False, "full_file_input_without_source_completeness_receipt"
    if video.get("has_final_v1_cache"):
        return False, "final_v1_without_full_video_receipt"
    return False, "no_full_video_receipt"


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _contains_viltrox(value: Any, *, depth: int = 0) -> bool:
    if depth > 4:
        return False
    if isinstance(value, str):
        return "viltrox" in value.lower()
    if isinstance(value, dict):
        return any(_contains_viltrox(nested, depth=depth + 1) for nested in value.values())
    if isinstance(value, list):
        return any(_contains_viltrox(nested, depth=depth + 1) for nested in value)
    return False


def _direct_timestamp_context(value: dict[str, Any]) -> bool:
    for key, nested in value.items():
        if str(key).strip().lower() not in _TIMESTAMP_KEYS:
            continue
        if isinstance(nested, (list, dict)) and nested:
            return True
        if isinstance(nested, (str, int, float)) and str(nested).strip():
            return True
    return False


def _direct_brand_context(value: dict[str, Any]) -> bool:
    """Detect a brand signal at this object level, not in arbitrary siblings."""

    for key, nested in value.items():
        normalized_key = str(key).strip().lower()
        if normalized_key == "viltrox_detected" and _truthy(nested):
            return True
        if normalized_key in _VILTROX_SIGNAL_KEYS - {"viltrox_detected"}:
            if nested not in (None, "", [], {}) and (_contains_viltrox(nested) or bool(nested)):
                return True
        # A timestamp/timeline note such as "00:14 Viltrox lens shown" is
        # itself a locally grounded brand observation.
        if normalized_key in _TIMESTAMP_KEYS and _contains_viltrox(nested):
            return True
        # Scene entries often use visual/note/description rather than a formal
        # product field.  Only inspect direct scalar text at this level.
        if isinstance(nested, str) and "viltrox" in nested.lower():
            return True
        if normalized_key in {"product_presence", "brand_exposure"} and _contains_viltrox(nested):
            return True
    return False


def _has_timestamp_context(result: dict[str, Any], *, depth: int = 0) -> bool:
    """Require timestamp and Viltrox evidence to co-occur locally.

    A global ``viltrox_detected=true`` in one branch plus an unrelated
    timestamp elsewhere is not timestamp-grounded evidence.  Co-occurrence
    must be in the same object (including one scene/timeline list entry), or
    the timestamp note itself must name Viltrox.
    """

    if depth > 8:
        return False
    if _direct_timestamp_context(result) and _direct_brand_context(result):
        return True
    for nested in result.values():
        if isinstance(nested, dict) and _has_timestamp_context(nested, depth=depth + 1):
            return True
        if isinstance(nested, list):
            for entry in nested:
                if isinstance(entry, dict) and _has_timestamp_context(entry, depth=depth + 1):
                    return True
    return False


def _has_brand_signal(video: dict[str, Any], result: dict[str, Any]) -> bool:
    if video.get("llm_viltrox_detected") is True or _as_list(video.get("llm_viltrox_products")):
        return True
    detected = _walk_values(result, {"viltrox_detected"})
    products = _walk_values(result, {"viltrox_products", "viltrox_products_all"})
    product_presence = _walk_values(result, {"product_presence"})
    explicit_presence = any(
        "viltrox" in json.dumps(value, ensure_ascii=False).lower()
        for value in product_presence
        if value not in (None, "", [], {})
    )
    return any(_truthy(value) for value in detected) or any(bool(value) for value in products) or explicit_presence


def _gap(code: str, *, severity: str, message: str, observed: Any, required: Any) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "observed": observed,
        "required": required,
    }


def _scope(
    *,
    level: str,
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    if blockers:
        decision_mode = "abstain"
        recommendation_status = "abstain"
    elif level == "decision_ready":
        decision_mode = "human_decision_support"
        recommendation_status = "decision_support_ready"
    else:
        decision_mode = "human_review_required"
        recommendation_status = "provisional"
    return {
        "level": level,
        "status": level,
        "claim_status": "descriptive_only",
        "decision_mode": decision_mode,
        "recommendation_status": recommendation_status,
        "blocking_gaps": blockers,
        "warnings": warnings,
    }


def build_analysis_readiness(
    *,
    item: dict[str, Any],
    videos: list[dict[str, Any]],
    analysis_items: list[dict[str, Any]],
    llm_deep: dict[str, Any],
    now: datetime | None = None,
    sample_scope: str = "provided_video_evidence",
    sample_limit: int | None = None,
    sample_truncated: bool = False,
) -> dict[str, Any]:
    """Build an evidence-only contract without provider calls or writes."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    video_rows = [video for video in videos if _is_video_evidence(video)]
    video_ids = {evidence_id for video in video_rows if (evidence_id := _evidence_id(video))}
    analysis_by_id = {
        evidence_id: entry
        for entry in analysis_items
        if (evidence_id := _evidence_id(_as_dict(entry.get("video"))))
    }
    deep_ready_ids = {
        evidence_id
        for evidence_id, entry in analysis_by_id.items()
        if evidence_id in video_ids and _as_dict(entry.get("final_entry")).get("status") == "ready"
    }
    qa_ready_ids = {
        evidence_id
        for evidence_id, entry in analysis_by_id.items()
        if evidence_id in video_ids and _as_dict(entry.get("qa_entry")).get("status") == "ready"
    }
    view_known = sum(1 for video in video_rows if video.get("view_count") not in (None, ""))
    video_total = len(video_rows)
    deep_ready = len(deep_ready_ids)
    qa_ready = len(qa_ready_ids)

    full_video_receipts: list[dict[str, Any]] = []
    full_video_unproven: list[dict[str, Any]] = []
    for evidence_id in sorted(deep_ready_ids):
        entry = analysis_by_id[evidence_id]
        video = _as_dict(entry.get("video"))
        proven, basis = _full_video_receipt(_as_dict(entry.get("final_entry")), video)
        target = full_video_receipts if proven else full_video_unproven
        target.append({"evidence_id": evidence_id, "basis": basis})

    structured_collaborations = _as_list(item.get("brand_collaborations_json"))
    brand_counts = {
        "model_detected_with_timestamp_context": 0,
        "model_detected_without_timestamp": 0,
        "structured_collaboration_record": len(structured_collaborations),
    }
    for evidence_id in sorted(deep_ready_ids):
        entry = analysis_by_id[evidence_id]
        video = _as_dict(entry.get("video"))
        result = _as_dict(_as_dict(entry.get("final_entry")).get("result"))
        if not _has_brand_signal(video, result):
            continue
        key = (
            "model_detected_with_timestamp_context"
            if _has_timestamp_context(result)
            else "model_detected_without_timestamp"
        )
        brand_counts[key] += 1
    brand_types = [key for key, count in brand_counts.items() if count > 0]
    if brand_counts["model_detected_with_timestamp_context"]:
        strongest_brand_type = "model_detected_with_timestamp_context"
    elif brand_counts["structured_collaboration_record"]:
        strongest_brand_type = "structured_collaboration_record"
    elif brand_counts["model_detected_without_timestamp"]:
        strongest_brand_type = "model_detected_without_timestamp"
    else:
        strongest_brand_type = "none"

    profile_latest = _latest(
        item.get(key) for key in ("profile_backfilled_at", "last_seen_at", "updated_at", "created_at")
    )
    evidence_latest = _latest(
        video.get(key)
        for video in video_rows
        for key in ("metrics_scraped_at", "scraped_at", "updated_at", "publish_date", "posted_at", "created_at")
    )
    analysis_latest = _latest(
        value
        for analysis in analysis_items
        if _evidence_id(_as_dict(analysis.get("video"))) in video_ids
        for value in (
            _as_dict(analysis.get("final_entry")).get("updated_at"),
            _as_dict(analysis.get("final_entry")).get("created_at"),
            _as_dict(analysis.get("qa_entry")).get("updated_at"),
            _as_dict(analysis.get("qa_entry")).get("created_at"),
        )
    ) or _latest(entry.get("created_at") for entry in _as_list(llm_deep.get("items")) if isinstance(entry, dict))
    freshness_ages = {
        "profile": _age_days(profile_latest, current),
        "evidence": _age_days(evidence_latest, current),
        "analysis": _age_days(analysis_latest, current),
    }
    required_ages = [freshness_ages[key] for key in ("evidence", "analysis") if freshness_ages[key] is not None]
    decision_age_days = max(required_ages) if len(required_ages) == 2 else None
    if decision_age_days is None:
        freshness_status = "unknown"
    elif decision_age_days <= THRESHOLDS["fresh_max_age_days"]:
        freshness_status = "fresh"
    elif decision_age_days <= THRESHOLDS["stale_after_days"]:
        freshness_status = "aging"
    else:
        freshness_status = "stale"

    deep_ratio = _ratio(deep_ready, video_total)
    view_ratio = _ratio(view_known, video_total)
    overall_blockers: list[dict[str, Any]] = []
    overall_warnings: list[dict[str, Any]] = []
    if video_total < THRESHOLDS["minimum_usable_video_samples"]:
        overall_blockers.append(_gap(
            "video_sample_insufficient", severity="blocking",
            message="视频证据样本不足，停止输出总体合作结论。",
            observed=video_total, required=THRESHOLDS["minimum_usable_video_samples"],
        ))
    elif video_total < THRESHOLDS["overall_min_video_samples"]:
        overall_warnings.append(_gap(
            "video_sample_below_decision_target", severity="warning",
            message="样本可作描述，但不足以达到决策支持门槛。",
            observed=video_total, required=THRESHOLDS["overall_min_video_samples"],
        ))
    if view_ratio is None or view_ratio < THRESHOLDS["minimum_usable_view_count_ratio"]:
        overall_blockers.append(_gap(
            "view_count_coverage_insufficient", severity="blocking",
            message="播放量字段覆盖不足，不能可靠比较内容表现。",
            observed=view_ratio, required=THRESHOLDS["minimum_usable_view_count_ratio"],
        ))
    elif view_ratio < THRESHOLDS["overall_min_view_count_ratio"]:
        overall_warnings.append(_gap(
            "view_count_coverage_below_decision_target", severity="warning",
            message="播放量覆盖只够临时判断。",
            observed=view_ratio, required=THRESHOLDS["overall_min_view_count_ratio"],
        ))
    if deep_ready == 0:
        overall_blockers.append(_gap(
            "deep_analysis_missing", severity="blocking",
            message="没有 ready 的 final_v1 深析，必须放弃内容质量结论。",
            observed=0, required=1,
        ))
    elif deep_ready < THRESHOLDS["overall_min_deep_ready"] or (deep_ratio or 0) < THRESHOLDS["overall_min_deep_ratio"]:
        overall_warnings.append(_gap(
            "deep_analysis_coverage_below_decision_target", severity="warning",
            message="深析覆盖可作描述，但不足以支撑稳定决策。",
            observed={"ready": deep_ready, "ratio": deep_ratio},
            required={"ready": THRESHOLDS["overall_min_deep_ready"], "ratio": THRESHOLDS["overall_min_deep_ratio"]},
        ))
    if freshness_status == "stale":
        overall_blockers.append(_gap(
            "evidence_stale", severity="blocking",
            message="证据或深析已过期，停止输出当前合作建议。",
            observed=decision_age_days, required=f"<= {THRESHOLDS['stale_after_days']} days",
        ))
    elif freshness_status != "fresh":
        overall_warnings.append(_gap(
            "freshness_not_decision_ready", severity="warning",
            message="证据时效不足或无法确认。",
            observed=freshness_status, required=f"<= {THRESHOLDS['fresh_max_age_days']} days",
        ))
    if qa_ready == 0 and deep_ready:
        overall_warnings.append(_gap(
            "qa_missing", severity="warning",
            message="深析结果尚无独立关键帧 QA。",
            observed=0, required=1,
        ))
    if sample_truncated:
        overall_warnings.append(_gap(
            "evidence_sample_truncated_at_limit", severity="warning",
            message=(
                "活跃视频证据超过分析读取上限，当前比率只代表已披露的前 200 条样本。"
            ),
            observed={"sample_count": video_total, "limit": sample_limit},
            required="complete_active_video_evidence_population",
        ))

    overall_ready = bool(
        not overall_blockers
        and video_total >= THRESHOLDS["overall_min_video_samples"]
        and (view_ratio or 0) >= THRESHOLDS["overall_min_view_count_ratio"]
        and deep_ready >= THRESHOLDS["overall_min_deep_ready"]
        and (deep_ratio or 0) >= THRESHOLDS["overall_min_deep_ratio"]
        and freshness_status == "fresh"
        and qa_ready >= 1
        and not sample_truncated
    )
    overall_level = "insufficient" if overall_blockers else "decision_ready" if overall_ready else "provisional"
    overall_scope = _scope(level=overall_level, blockers=overall_blockers, warnings=overall_warnings)

    content_blockers = list(overall_blockers)
    content_warnings = list(overall_warnings)
    if len(full_video_receipts) < THRESHOLDS["content_fit_min_full_video_proven"]:
        content_blockers.append(_gap(
            "full_video_coverage_unproven", severity="blocking",
            message=(
                "final_v1 已有结果，但缺少原视频全时长覆盖凭证；"
                "内容契合结论必须 abstain。"
            ),
            observed=len(full_video_receipts), required=THRESHOLDS["content_fit_min_full_video_proven"],
        ))
    content_level = (
        "insufficient" if overall_level == "insufficient"
        else "decision_ready" if overall_level == "decision_ready" and not content_blockers
        else "provisional"
    )
    content_scope = _scope(level=content_level, blockers=content_blockers, warnings=content_warnings)

    brand_blockers: list[dict[str, Any]] = []
    brand_warnings: list[dict[str, Any]] = []
    timestamped_brand_count = brand_counts["model_detected_with_timestamp_context"]
    if not brand_types:
        brand_blockers.append(_gap(
            "brand_history_evidence_missing", severity="blocking",
            message=(
                "未发现品牌历史证据；这不降低新创作者总体可用性，"
                "但品牌历史结论必须 abstain。"
            ),
            observed=0, required=1,
        ))
    elif timestamped_brand_count == 0:
        brand_warnings.append(_gap(
            "brand_history_not_timestamp_grounded", severity="warning",
            message="品牌记录可作描述，但缺少视频时间戳语境。",
            observed=brand_types, required="model_detected_with_timestamp_context",
        ))
    if timestamped_brand_count and not full_video_receipts:
        brand_warnings.append(_gap(
            "brand_video_source_completeness_unproven", severity="warning",
            message="品牌检测有时间戳语境，但原视频完整性仍未证明。",
            observed=0, required=1,
        ))
    brand_ready = bool(timestamped_brand_count and full_video_receipts and freshness_status == "fresh")
    brand_level = "insufficient" if brand_blockers else "decision_ready" if brand_ready else "provisional"
    brand_scope = _scope(level=brand_level, blockers=brand_blockers, warnings=brand_warnings)

    evidence_coverage = {
        "video_total": video_total,
        "sample_scope": str(sample_scope or "provided_video_evidence"),
        "sample_limit": sample_limit,
        "sample_truncated": bool(sample_truncated),
        "denominator_status": "partial_at_limit" if sample_truncated else "complete_for_scope",
        "deep_ready": deep_ready,
        "deep_ratio": deep_ratio,
        "qa_ready": qa_ready,
        "qa_ratio": _ratio(qa_ready, video_total),
        "full_video_proven": len(full_video_receipts),
        "full_video_ratio": _ratio(len(full_video_receipts), video_total),
        "full_video_receipts": full_video_receipts,
        "full_video_unproven": full_video_unproven,
    }
    freshness = {
        "status": freshness_status,
        "profile_latest_at": _iso(profile_latest),
        "evidence_latest_at": _iso(evidence_latest),
        "analysis_latest_at": _iso(analysis_latest),
        "age_days": freshness_ages,
        "decision_age_days": decision_age_days,
        "basis": "max_age_of_latest_evidence_and_latest_analysis",
    }
    brand_evidence = {
        "types": brand_types,
        "counts": brand_counts,
        "strongest_type": strongest_brand_type,
        "claimable_for_brand_history": brand_ready,
        "note": "缺少品牌证据不阻断 overall；只影响 brand_history 作用域。",
    }
    result = {
        "version": VERSION,
        "level": overall_level,
        "status": overall_level,
        "claim_status": "descriptive_only",
        "decision_mode": overall_scope["decision_mode"],
        "recommendation_status": overall_scope["recommendation_status"],
        "abstain": overall_scope["decision_mode"] == "abstain",
        "key_sample_count": video_total,
        "view_count_completeness": {
            "known": view_known,
            "total": video_total,
            "ratio": view_ratio,
            "unknown": max(0, video_total - view_known),
        },
        "evidence_coverage": evidence_coverage,
        "brand_evidence": brand_evidence,
        "freshness": freshness,
        "blocking_gaps": overall_blockers,
        "warnings": overall_warnings,
        "scopes": {
            "overall": overall_scope,
            "content_fit": content_scope,
            "brand_history": brand_scope,
        },
        "thresholds": dict(THRESHOLDS),
        "diagnostics": {
            "source": "detail_bundle_local_evidence_only",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "viltrox_fit_score_write": False,
            "full_video_rule": "explicit_scope_flag_or_95pct_duration_receipt; fileapi_method_alone_is_not_proof",
        },
    }
    return result


def evidence_quality_projection(readiness: dict[str, Any]) -> dict[str, Any]:
    """Small compatibility projection for current UI clients."""
    return {
        "version": readiness.get("version"),
        "level": readiness.get("level"),
        "status": readiness.get("status"),
        "claim_status": readiness.get("claim_status"),
        "decision_mode": readiness.get("decision_mode"),
        "recommendation_status": readiness.get("recommendation_status"),
        "key_sample_count": readiness.get("key_sample_count"),
        "evidence_coverage": readiness.get("evidence_coverage"),
        "view_count_completeness": readiness.get("view_count_completeness"),
        "brand_evidence": readiness.get("brand_evidence"),
        "freshness": readiness.get("freshness"),
        "blockers": readiness.get("blocking_gaps") or [],
        "gaps": [gap.get("code") for gap in readiness.get("blocking_gaps") or []],
        "scopes": readiness.get("scopes") or {},
    }
