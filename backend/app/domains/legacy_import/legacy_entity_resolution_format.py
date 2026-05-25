"""Text formatters for legacy entity resolution CLI output."""
from __future__ import annotations

from typing import Any

from app.domains.legacy_import.legacy_import_audit import _text


def _identity_label(row: dict[str, Any]) -> str:
    platform = _text(row.get("normalized_platform"))
    handle = _text(row.get("normalized_handle"))
    display_name = _text(row.get("display_name"))
    if platform and handle:
        return f"{platform}:{handle}"
    if handle:
        return handle
    if display_name:
        return display_name
    return _text(row.get("canonical_key")) or _text(row.get("entity_uid"))


def _truncate(value: str, *, max_chars: int = 1200) -> str:
    text = _text(value)
    return text if len(text) <= max_chars else text[:max_chars] + "...[truncated]"


def format_resolution_summary(result: dict[str, Any]) -> str:
    lines = [
        f"batch_uid={result.get('batch_uid', '')}",
        f"run_uid={result.get('run_uid', '')}",
        f"status={result.get('status', '')}",
        f"entity_count={int(result.get('entity_count', 0))}",
        f"ready_count={int(result.get('ready_count', 0))}",
        f"review_count={int(result.get('review_count', 0))}",
        f"blocked_count={int(result.get('blocked_count', 0))}",
        f"no_identifier_rows={int(result.get('no_identifier_rows', 0))}",
    ]
    for label, count in sorted((result.get("label_counts") or {}).items()):
        lines.append(f"weak_label.{label}={int(count)}")
    for pipeline, count in sorted((result.get("pipeline_ref_counts") or {}).items()):
        lines.append(f"refs.{pipeline}={int(count)}")
    return "\n".join(lines)


def format_pending_reviews(result: dict[str, Any]) -> str:
    label = result.get("weak_label") or "all"
    lines = [
        f"batch_uid={result.get('batch_uid', '')}",
        f"weak_label={label}",
        f"include_blocked={str(bool(result.get('include_blocked'))).lower()}",
        f"pending_reviews={int(result.get('pending_count', 0))}",
        f"shown={int(result.get('shown_count', 0))}",
    ]
    for row in result.get("rows") or []:
        lines.append(
            "entity="
            f"{row.get('entity_uid', '')} "
            f"weak_label={row.get('weak_label', '')} "
            f"identity={_identity_label(row)} "
            f"refs={int(row.get('evidence_count') or 0)} "
            f"profiles={int(row.get('kol_profile_rows') or 0)} "
            f"cooperations={int(row.get('cooperation_rows') or 0)} "
            f"risks={int(row.get('risk_rows') or 0)}"
        )
    return "\n".join(lines)


def format_entity_detail(result: dict[str, Any]) -> str:
    entity = result.get("entity") or {}
    lines = [
        f"batch_uid={result.get('batch_uid', '')}",
        f"entity_uid={entity.get('entity_uid', '')}",
        f"identity={_identity_label(entity)}",
        f"weak_label={entity.get('weak_label', '')}",
        f"resolution_status={entity.get('resolution_status', '')}",
        f"resolution_decision={entity.get('resolution_decision') or ''}",
        f"merge_target_uid={entity.get('merge_target_uid') or ''}",
        f"decision_reason={entity.get('decision_reason') or ''}",
        f"decision_note={entity.get('decision_note') or ''}",
        f"ref_count={int(result.get('ref_count', 0))}",
    ]
    for idx, ref in enumerate(result.get("refs") or [], start=1):
        lines.extend(
            [
                f"ref.{idx}.pipeline={ref.get('pipeline', '')}",
                f"ref.{idx}.staging={ref.get('staging_table', '')}:{ref.get('staging_id', '')}",
                f"ref.{idx}.source={ref.get('source_sheet', '')}:{ref.get('source_row', '')}",
                f"ref.{idx}.raw_row_json={_truncate(ref.get('raw_row_json') or '{}')}",
            ]
        )
    return "\n".join(lines)


def format_decision_result(result: dict[str, Any]) -> str:
    prefix = "[COMMIT]" if result.get("committed") else "[DRY-RUN]"
    lines = [
        f"{prefix} entity_uid={result.get('entity_uid', '')}",
        f"identity={result.get('identity', '')}",
        f"weak_label={result.get('weak_label', '')}",
        f"action={result.get('action', '')}",
    ]
    if result.get("target_entity_uid"):
        lines.append(f"target={result.get('target_entity_uid')} identity={result.get('target_identity', '')}")
    if result.get("reason"):
        lines.append(f"reason={result.get('reason')}")
    if result.get("note"):
        lines.append(f"note={result.get('note')}")
    if not result.get("committed"):
        lines.append("Add --commit to apply.")
    return "\n".join(lines)


def format_bulk_decision_result(result: dict[str, Any]) -> str:
    prefix = "[COMMIT]" if result.get("committed") else "[DRY-RUN]"
    verb = "Decided" if result.get("committed") else "Would decide"
    lines = [
        f"{prefix} {verb} {int(result.get('count', 0))} entities as {result.get('action', '')}.",
        f"batch_uid={result.get('batch_uid', '')}",
        f"weak_label={result.get('weak_label', '')}",
    ]
    if result.get("reason"):
        lines.append(f"reason={result.get('reason')}")
    if result.get("note"):
        lines.append(f"note={result.get('note')}")
    for idx, row in enumerate(result.get("sample") or [], start=1):
        lines.append(f"sample.{idx}={row.get('entity_uid', '')} identity={_identity_label(row)}")
    if not result.get("committed"):
        lines.append("Add --commit to apply.")
    return "\n".join(lines)


def format_review_progress(result: dict[str, Any]) -> str:
    lines = [
        f"Batch: {result.get('batch_uid', '')}",
        f"Total entities: {int(result.get('entity_count', 0))}",
        "",
        "Decision distribution:",
    ]
    for row in result.get("rows") or []:
        label = row.get("weak_label") or ""
        decision = row.get("resolution_decision") or "NULL"
        status = row.get("resolution_status") or ""
        lines.append(f"  {label} | {decision} | {status}: {int(row.get('n') or 0)}")
    lines.extend(
        [
            "",
            f"Pending (excluding blocked_risk): {int(result.get('pending_count', 0))}",
            f"Blocked pending: {int(result.get('blocked_pending_count', 0))}",
        ]
    )
    return "\n".join(lines)
