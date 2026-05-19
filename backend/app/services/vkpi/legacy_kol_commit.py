"""P2D dry-run planning for legacy KOL commits into vkpi_kol_pool."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.legacy_import_audit import _text
from app.services.vkpi.legacy_import_staging import ensure_legacy_staging_schema, json_dumps


IMPORT_SOURCE_TYPE = "legacy_excel_p2d"


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row.items()) if hasattr(row, "items") else dict(row)


def _load_json(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _fetch_batch(batch_uid: str) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_legacy_import_batches WHERE batch_uid=?", (batch_uid,)).fetchone()
    if not row:
        raise ValueError(f"batch not found: {batch_uid}")
    return _row_to_dict(row)


def _identity(entity: dict[str, Any]) -> str:
    platform = _text(entity.get("normalized_platform"))
    handle = _text(entity.get("normalized_handle"))
    if platform and handle:
        return f"{platform}:{handle}"
    return _text(entity.get("display_name")) or _text(entity.get("entity_uid"))


def _review_state(entity: dict[str, Any]) -> str:
    label = _text(entity.get("weak_label"))
    decision = _text(entity.get("resolution_decision"))
    if label == "ready":
        return "ready_auto"
    if decision == "keep_separate":
        return "reviewed_keep_separate"
    if decision == "escalate":
        return "needs_human_review"
    if decision == "merge_with":
        return "merged_source"
    if decision == "drop":
        return "dropped"
    if label == "blocked_risk":
        return "blocked"
    return "pending_decision"


def _skip_reason(entity: dict[str, Any], *, include_blocked: bool) -> str:
    label = _text(entity.get("weak_label"))
    decision = _text(entity.get("resolution_decision"))
    platform = _text(entity.get("normalized_platform"))
    handle = _text(entity.get("normalized_handle"))
    if label == "blocked_risk" and not include_blocked:
        return "blocked_risk"
    if decision == "drop":
        return "dropped"
    if decision == "merge_with":
        return "merged_into_target"
    if label != "ready" and label != "blocked_risk" and not decision:
        return "pending_decision"
    if label == "blocked_risk" and decision not in {"escalate"}:
        return "blocked_without_escalate"
    if not platform or not handle:
        return "missing_platform_handle"
    return ""


def _target_payload(entity: dict[str, Any], *, batch_uid: str) -> dict[str, Any]:
    identity = _load_json(entity.get("identity_json") or "{}", {})
    evidence = _load_json(entity.get("evidence_json") or "{}", {})
    review_reasons = _load_json(entity.get("review_reason_json") or "[]", [])
    visibility = _text(entity.get("contact_visibility_level")) or "restricted"
    email = _text(entity.get("email")) if visibility == "public" else ""
    review_state = _review_state(entity)
    sync_status = "needs_human_review" if review_state == "needs_human_review" else "imported"
    source_ref = f"legacy_batch:{batch_uid}:entity:{entity['entity_uid']}"
    raw_platform_data = {
        "source": "legacy_excel_p2d",
        "batch_uid": batch_uid,
        "entity_uid": entity.get("entity_uid"),
        "weak_label": entity.get("weak_label"),
        "resolution_decision": entity.get("resolution_decision") or "",
        "review_state": review_state,
        "contact_status": entity.get("contact_status") or "",
        "contact_visibility_level": visibility,
        "contact_has_email": bool(_text(entity.get("email"))),
        "contact_has_phone": bool(_text(entity.get("phone"))),
        "identity": identity,
        "evidence_summary": {
            "evidence_count": int(entity.get("evidence_count") or 0),
            "kol_profile_rows": int(entity.get("kol_profile_rows") or 0),
            "cooperation_rows": int(entity.get("cooperation_rows") or 0),
            "risk_rows": int(entity.get("risk_rows") or 0),
            "sources": evidence.get("sources") if isinstance(evidence, dict) else {},
        },
    }
    return {
        "pool_uid": f"legacy-{entity['entity_uid']}",
        "platform": _text(entity.get("normalized_platform")),
        "handle": _text(entity.get("normalized_handle")),
        "profile_url": _text(entity.get("profile_url")),
        "display_name": _text(entity.get("display_name")),
        "country": _text(entity.get("country") or entity.get("region")),
        "email": email,
        "source_type": IMPORT_SOURCE_TYPE,
        "source_ref": source_ref,
        "sync_status": sync_status,
        "raw_platform_data": json_dumps(raw_platform_data),
        "potential_concerns_json": json_dumps(review_reasons),
    }


def _existing_pool_item(platform: str, handle: str) -> dict[str, Any] | None:
    row = get_conn().execute(
        """
        SELECT id, pool_uid, platform, handle, source_type, source_ref, sync_status
        FROM vkpi_kol_pool
        WHERE lower(platform)=lower(?) AND lower(handle)=lower(?)
        """,
        (platform, handle),
    ).fetchone()
    return _row_to_dict(row) if row else None


def dry_run_kol_pool_commit(
    batch_uid: str,
    *,
    include_blocked: bool = False,
    sample_limit: int = 20,
) -> dict[str, Any]:
    """Plan P2D KOL pool writes without mutating official tables."""

    ensure_legacy_staging_schema()
    conn = get_conn()
    batch = _fetch_batch(batch_uid)
    import_batch_id = int(batch["id"])
    committed_refs_count = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_legacy_import_committed_refs WHERE import_batch_id=?",
            (import_batch_id,),
        ).fetchone()["n"]
    )
    rows = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM vkpi_legacy_kol_entities
            WHERE import_batch_id=?
            ORDER BY weak_label, normalized_platform, normalized_handle, id
            """,
            (import_batch_id,),
        ).fetchall()
    ]

    action_counts: Counter[str] = Counter()
    skip_counts: Counter[str] = Counter()
    weak_label_counts: Counter[str] = Counter()
    review_state_counts: Counter[str] = Counter()
    contact_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    for entity in rows:
        label = _text(entity.get("weak_label"))
        weak_label_counts[label] += 1
        review_state = _review_state(entity)
        review_state_counts[review_state] += 1
        skip_reason = _skip_reason(entity, include_blocked=include_blocked)
        if skip_reason:
            skip_counts[skip_reason] += 1
            if len(samples) < sample_limit:
                samples.append(
                    {
                        "entity_uid": entity.get("entity_uid"),
                        "identity": _identity(entity),
                        "weak_label": label,
                        "decision": entity.get("resolution_decision") or "",
                        "plan_action": "skip",
                        "skip_reason": skip_reason,
                    }
                )
            continue

        payload = _target_payload(entity, batch_uid=batch_uid)
        existing = _existing_pool_item(payload["platform"], payload["handle"])
        plan_action = "update" if existing else "insert"
        action_counts[plan_action] += 1
        if payload["email"]:
            contact_counts["email_public"] += 1
        elif bool(_text(entity.get("email"))):
            contact_counts["email_restricted_omitted"] += 1
        if bool(_text(entity.get("phone"))):
            contact_counts["phone_restricted_omitted"] += 1
        if len(samples) < sample_limit:
            samples.append(
                {
                    "entity_uid": entity.get("entity_uid"),
                    "identity": _identity(entity),
                    "weak_label": label,
                    "decision": entity.get("resolution_decision") or "",
                    "review_state": review_state,
                    "plan_action": plan_action,
                    "target_table": "vkpi_kol_pool",
                    "target_id": existing.get("id") if existing else "",
                    "sync_status": payload["sync_status"],
                    "source_ref": payload["source_ref"],
                }
            )

    return {
        "batch_uid": batch_uid,
        "batch_id": import_batch_id,
        "mode": "dry_run",
        "include_blocked": include_blocked,
        "committed_refs_count": committed_refs_count,
        "entity_count": len(rows),
        "planned_writes": int(action_counts["insert"] + action_counts["update"]),
        "insert_count": int(action_counts["insert"]),
        "update_count": int(action_counts["update"]),
        "skip_count": int(sum(skip_counts.values())),
        "skip_counts": dict(sorted(skip_counts.items())),
        "weak_label_counts": dict(sorted(weak_label_counts.items())),
        "review_state_counts": dict(sorted(review_state_counts.items())),
        "contact_counts": dict(sorted(contact_counts.items())),
        "samples": samples,
    }


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
    return "\n".join(lines)
