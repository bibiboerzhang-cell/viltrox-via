"""Text formatters for legacy KOL commit and rollback CLI output."""
from __future__ import annotations

from typing import Any


def format_kol_pool_commit_plan(result: dict[str, Any]) -> str:
    lines = [
        f"batch_uid={result.get('batch_uid', '')}",
        f"mode={result.get('mode', '')}",
        f"include_blocked={str(bool(result.get('include_blocked'))).lower()}",
        f"entity_count={int(result.get('entity_count', 0))}",
        f"planned_writes={int(result.get('planned_writes', 0))}",
        f"insert_count={int(result.get('insert_count', 0))}",
        f"update_count={int(result.get('update_count', 0))}",
        f"skip_count={int(result.get('skip_count', 0))}",
        f"committed_refs_count={int(result.get('committed_refs_count', 0))}",
    ]
    if "committed_refs_total" in result:
        lines.append(f"committed_refs_total={int(result.get('committed_refs_total', 0))}")
    if "commit_attempt" in result:
        lines.append(f"commit_attempt={int(result.get('commit_attempt', 0))}")
    if result.get("rollback_policy"):
        lines.append(f"rollback_policy={result.get('rollback_policy', '')}")
    if result.get("rollback_until"):
        lines.append(f"rollback_until={result.get('rollback_until', '')}")
    if result.get("mode") == "commit":
        lines.append(f"committed_refs_written={int(result.get('committed_refs_count', 0))}")
    for key, value in result.get("weak_label_counts", {}).items():
        lines.append(f"weak_label.{key}={int(value)}")
    for key, value in result.get("review_state_counts", {}).items():
        lines.append(f"review_state.{key}={int(value)}")
    for key, value in result.get("skip_counts", {}).items():
        lines.append(f"skip.{key}={int(value)}")
    for key, value in result.get("contact_counts", {}).items():
        lines.append(f"contact.{key}={int(value)}")
    for index, sample in enumerate(result.get("samples") or [], start=1):
        lines.append(
            f"sample.{index}="
            f"{sample.get('plan_action')} "
            f"{sample.get('entity_uid')} "
            f"identity={sample.get('identity')} "
            f"weak_label={sample.get('weak_label')} "
            f"decision={sample.get('decision')} "
            f"sync_status={sample.get('sync_status', '')} "
            f"skip_reason={sample.get('skip_reason', '')}"
        )
    for index, sample in enumerate(result.get("committed_samples") or [], start=1):
        lines.append(
            f"committed_sample.{index}="
            f"{sample.get('commit_action')} "
            f"attempt={sample.get('commit_attempt', '')} "
            f"{sample.get('entity_uid')} "
            f"identity={sample.get('identity')} "
            f"target_id={sample.get('target_id')}"
        )
    return "\n".join(lines)


def format_kol_pool_rollback(result: dict[str, Any]) -> str:
    lines = [
        f"batch_uid={result.get('batch_uid', '')}",
        f"mode={result.get('mode', '')}",
        f"rollback_refs_count={int(result.get('rollback_refs_count', 0))}",
        f"insert_refs={int(result.get('insert_refs', 0))}",
        f"update_refs={int(result.get('update_refs', 0))}",
        f"rollback_allowed={str(bool(result.get('rollback_allowed'))).lower()}",
        f"rollback_forced={str(bool(result.get('rollback_forced'))).lower()}",
        f"rollback_policy={result.get('rollback_policy', '')}",
        f"rollback_until={result.get('rollback_until', '')}",
        f"rollback_window_reason={result.get('rollback_window_reason', '')}",
    ]
    if result.get("mode") == "rollback":
        lines.append(f"rolled_back_refs={int(result.get('rolled_back_refs', 0))}")
    for index, sample in enumerate(result.get("samples") or [], start=1):
        metadata = sample.get("metadata") or {}
        lines.append(
            f"sample.{index}="
            f"{sample.get('commit_action')} "
            f"attempt={sample.get('commit_attempt', '')} "
            f"target_id={sample.get('target_id')} "
            f"entity_uid={metadata.get('entity_uid', '')} "
            f"identity={metadata.get('identity', '')}"
        )
    if result.get("mode") == "rollback_preview":
        lines.append("Add --commit to apply rollback.")
    return "\n".join(lines)
