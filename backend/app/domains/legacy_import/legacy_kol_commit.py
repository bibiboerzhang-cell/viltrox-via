"""P2D planning, commit, and rollback for legacy KOL imports."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.db.connection import get_conn
from app.domains.legacy_import.legacy_import_audit import _text
from app.domains.legacy_import.legacy_import_staging import ensure_legacy_staging_schema, json_dumps
from app.domains.legacy_import.legacy_kol_commit_window import (
    format_ts as _format_ts,
    rollback_until_for_policy as _rollback_until_for_policy,
    rollback_window as _rollback_window,
    utcnow as _utcnow,
    utcnow_dt as _utcnow_dt,
)
from app.domains.legacy_import.legacy_kol_commit_format import format_kol_pool_commit_plan, format_kol_pool_rollback


IMPORT_SOURCE_TYPE = "legacy_excel_p2d"
POOL_COLUMNS = [
    "pool_uid",
    "platform",
    "handle",
    "profile_url",
    "display_name",
    "country",
    "email",
    "source_type",
    "source_ref",
    "sync_status",
    "raw_platform_data",
    "potential_concerns_json",
    "last_seen_at",
    "updated_at",
]


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


def _pool_row_by_key(platform: str, handle: str) -> dict[str, Any] | None:
    row = get_conn().execute(
        """
        SELECT *
        FROM vkpi_kol_pool
        WHERE lower(platform)=lower(?) AND lower(handle)=lower(?)
        """,
        (platform, handle),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _pool_row_by_id(pool_id: int) -> dict[str, Any] | None:
    row = get_conn().execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(pool_id),)).fetchone()
    return _row_to_dict(row) if row else None


def _fetch_entities(import_batch_id: int) -> list[dict[str, Any]]:
    return [
        _row_to_dict(row)
        for row in get_conn().execute(
            """
            SELECT *
            FROM vkpi_legacy_kol_entities
            WHERE import_batch_id=?
            ORDER BY weak_label, normalized_platform, normalized_handle, id
            """,
            (import_batch_id,),
        ).fetchall()
    ]


def _build_commit_plans(batch_uid: str, import_batch_id: int, *, include_blocked: bool) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for entity in _fetch_entities(import_batch_id):
        label = _text(entity.get("weak_label"))
        review_state = _review_state(entity)
        skip_reason = _skip_reason(entity, include_blocked=include_blocked)
        base = {
            "entity": entity,
            "entity_uid": entity.get("entity_uid"),
            "identity": _identity(entity),
            "weak_label": label,
            "decision": entity.get("resolution_decision") or "",
            "review_state": review_state,
        }
        if skip_reason:
            plans.append({**base, "plan_action": "skip", "skip_reason": skip_reason, "payload": {}, "existing": None})
            continue

        payload = _target_payload(entity, batch_uid=batch_uid)
        existing = _pool_row_by_key(payload["platform"], payload["handle"])
        plans.append(
            {
                **base,
                "plan_action": "update" if existing else "insert",
                "skip_reason": "",
                "payload": payload,
                "existing": existing,
            }
        )
    return plans


def _committed_refs_count(import_batch_id: int, *, active_only: bool = True) -> int:
    clause = " AND rollback_status='not_rolled_back'" if active_only else ""
    return int(
        get_conn()
        .execute(
            f"SELECT COUNT(*) AS n FROM vkpi_legacy_import_committed_refs WHERE import_batch_id=?{clause}",
            (import_batch_id,),
        )
        .fetchone()["n"]
    )


def _next_commit_attempt(import_batch_id: int) -> int:
    row = get_conn().execute(
        """
        SELECT COALESCE(MAX(commit_attempt), 0) + 1 AS next_attempt
        FROM vkpi_legacy_import_committed_refs
        WHERE import_batch_id=?
        """,
        (import_batch_id,),
    ).fetchone()
    return int(row["next_attempt"] or 1)


def _summarize_plans(plans: list[dict[str, Any]], *, sample_limit: int) -> dict[str, Any]:
    action_counts: Counter[str] = Counter()
    skip_counts: Counter[str] = Counter()
    weak_label_counts: Counter[str] = Counter()
    review_state_counts: Counter[str] = Counter()
    contact_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []

    for plan in plans:
        entity = plan["entity"]
        action = plan["plan_action"]
        weak_label_counts[_text(plan.get("weak_label"))] += 1
        review_state_counts[_text(plan.get("review_state"))] += 1
        if action == "skip":
            skip_counts[_text(plan.get("skip_reason"))] += 1
        else:
            action_counts[action] += 1
            payload = plan["payload"]
            if payload.get("email"):
                contact_counts["email_public"] += 1
            elif bool(_text(entity.get("email"))):
                contact_counts["email_restricted_omitted"] += 1
            if bool(_text(entity.get("phone"))):
                contact_counts["phone_restricted_omitted"] += 1
        if len(samples) < sample_limit:
            existing = plan.get("existing") or {}
            samples.append(
                {
                    "entity_uid": plan.get("entity_uid"),
                    "identity": plan.get("identity"),
                    "weak_label": plan.get("weak_label"),
                    "decision": plan.get("decision"),
                    "review_state": plan.get("review_state"),
                    "plan_action": action,
                    "target_table": "vkpi_kol_pool" if action != "skip" else "",
                    "target_id": existing.get("id", ""),
                    "sync_status": (plan.get("payload") or {}).get("sync_status", ""),
                    "source_ref": (plan.get("payload") or {}).get("source_ref", ""),
                    "skip_reason": plan.get("skip_reason", ""),
                }
            )

    return {
        "entity_count": len(plans),
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


def dry_run_kol_pool_commit(
    batch_uid: str,
    *,
    include_blocked: bool = False,
    sample_limit: int = 20,
) -> dict[str, Any]:
    """Plan P2D KOL pool writes without mutating official tables."""

    ensure_legacy_staging_schema()
    batch = _fetch_batch(batch_uid)
    import_batch_id = int(batch["id"])
    plans = _build_commit_plans(batch_uid, import_batch_id, include_blocked=include_blocked)
    summary = _summarize_plans(plans, sample_limit=sample_limit)

    return {
        "batch_uid": batch_uid,
        "batch_id": import_batch_id,
        "mode": "dry_run",
        "include_blocked": include_blocked,
        "committed_refs_count": _committed_refs_count(import_batch_id),
        **summary,
    }


def _insert_pool_item(payload: dict[str, Any], *, now: str) -> dict[str, Any]:
    # 管3 卫生闸(咽喉审计乙案,2026-06-12):staging normalized_handle 直入池,此前零校验。
    from app.domains.kol.pool_common import _garbage_handle_rule

    handle_value = str(payload.get("handle") or "").strip().lower()
    rule = _garbage_handle_rule(handle_value)
    if rule:
        raise ValueError(f"garbage handle rejected by pipe3 gate: handle={handle_value[:60]!r} rule={rule}")
    values = {**payload, "last_seen_at": now, "updated_at": now}
    values["created_at"] = now
    columns = [
        "pool_uid",
        "platform",
        "handle",
        "profile_url",
        "display_name",
        "country",
        "email",
        "source_type",
        "source_ref",
        "sync_status",
        "raw_platform_data",
        "potential_concerns_json",
        "last_seen_at",
        "created_at",
        "updated_at",
    ]
    row = get_conn().execute(
        f"""
        INSERT INTO vkpi_kol_pool ({", ".join(columns)})
        VALUES ({", ".join("?" for _ in columns)})
        RETURNING *
        """,
        [values.get(column) for column in columns],
    ).fetchone()
    return _row_to_dict(row)


def _update_pool_item(pool_id: int, payload: dict[str, Any], *, now: str) -> dict[str, Any]:
    row = get_conn().execute(
        """
        UPDATE vkpi_kol_pool
        SET profile_url=COALESCE(NULLIF(?, ''), profile_url),
            display_name=COALESCE(NULLIF(?, ''), display_name),
            country=COALESCE(NULLIF(?, ''), country),
            email=COALESCE(NULLIF(?, ''), email),
            source_type=?,
            source_ref=?,
            sync_status=?,
            raw_platform_data=?,
            potential_concerns_json=?,
            last_seen_at=?,
            updated_at=?
        WHERE id=?
        RETURNING *
        """,
        (
            payload["profile_url"],
            payload["display_name"],
            payload["country"],
            payload["email"],
            payload["source_type"],
            payload["source_ref"],
            payload["sync_status"],
            payload["raw_platform_data"],
            payload["potential_concerns_json"],
            now,
            now,
            int(pool_id),
        ),
    ).fetchone()
    return _row_to_dict(row)


def _insert_committed_ref(
    *,
    import_batch_id: int,
    commit_attempt: int,
    entity: dict[str, Any],
    target_id: int,
    commit_action: str,
    previous_snapshot: dict[str, Any],
    new_snapshot: dict[str, Any],
    actor_staff_id: int | None,
) -> None:
    get_conn().execute(
        """
        INSERT INTO vkpi_legacy_import_committed_refs (
          import_batch_id, commit_attempt, pipeline, staging_table, staging_id, target_table,
          target_id, commit_action, previous_snapshot_json, new_snapshot_json,
          rollback_status, committed_by_staff_id, metadata_json
        ) VALUES (?, ?, 'kol_entities', 'vkpi_legacy_kol_entities', ?, 'vkpi_kol_pool',
          ?, ?, ?, ?, 'not_rolled_back', ?, ?)
        ON CONFLICT(import_batch_id, commit_attempt, pipeline, staging_table, staging_id, target_table, target_id)
        DO UPDATE SET
          commit_action=excluded.commit_action,
          previous_snapshot_json=excluded.previous_snapshot_json,
          new_snapshot_json=excluded.new_snapshot_json,
          rollback_status='not_rolled_back',
          committed_by_staff_id=excluded.committed_by_staff_id,
          rolled_back_by_staff_id=NULL,
          committed_at=NOW(),
          rolled_back_at=NULL,
          metadata_json=excluded.metadata_json
        """,
        (
            import_batch_id,
            int(commit_attempt),
            int(entity["id"]),
            str(target_id),
            commit_action,
            json_dumps(previous_snapshot),
            json_dumps(new_snapshot),
            actor_staff_id,
            json_dumps(
                {
                    "entity_uid": entity.get("entity_uid"),
                    "weak_label": entity.get("weak_label"),
                    "resolution_decision": entity.get("resolution_decision") or "",
                    "identity": _identity(entity),
                    "commit_attempt": int(commit_attempt),
                }
            ),
        ),
    )


def commit_kol_pool_batch(
    batch_uid: str,
    *,
    include_blocked: bool = False,
    actor_staff_id: int | None = None,
    sample_limit: int = 20,
) -> dict[str, Any]:
    """Commit P2D KOL pool writes and record row-level rollback refs."""

    ensure_legacy_staging_schema()
    conn = get_conn()
    batch = _fetch_batch(batch_uid)
    import_batch_id = int(batch["id"])
    if _committed_refs_count(import_batch_id, active_only=True):
        raise RuntimeError("batch already has committed refs; rollback before committing again")
    if _text(batch.get("status")) not in {"staged", "committing", "rolled_back"}:
        raise RuntimeError(f"batch status must be staged before P2D commit, got: {batch.get('status')}")

    plans = _build_commit_plans(batch_uid, import_batch_id, include_blocked=include_blocked)
    summary = _summarize_plans(plans, sample_limit=sample_limit)
    if int(summary["planned_writes"]) <= 0:
        raise RuntimeError("no P2D writes planned")

    now_dt = _utcnow_dt()
    now = _format_ts(now_dt) or _utcnow()
    rollback_policy = _text(batch.get("rollback_policy")) or "manual_30m"
    rollback_until = _format_ts(_rollback_until_for_policy(now_dt, rollback_policy))
    commit_attempt = _next_commit_attempt(import_batch_id)
    committed_samples: list[dict[str, Any]] = []
    try:
        conn.execute(
            "UPDATE vkpi_legacy_import_batches SET status='committing', updated_at=? WHERE id=?",
            (now, import_batch_id),
        )
        for plan in plans:
            if plan["plan_action"] == "skip":
                continue
            entity = plan["entity"]
            payload = plan["payload"]
            existing = plan.get("existing")
            previous_snapshot: dict[str, Any] = {}
            if existing:
                previous_snapshot = _pool_row_by_id(int(existing["id"])) or {}
                new_snapshot = _update_pool_item(int(existing["id"]), payload, now=now)
                commit_action = "update"
            else:
                new_snapshot = _insert_pool_item(payload, now=now)
                commit_action = "insert"
            _insert_committed_ref(
                import_batch_id=import_batch_id,
                commit_attempt=commit_attempt,
                entity=entity,
                target_id=int(new_snapshot["id"]),
                commit_action=commit_action,
                previous_snapshot=previous_snapshot,
                new_snapshot=new_snapshot,
                actor_staff_id=actor_staff_id,
            )
            if len(committed_samples) < sample_limit:
                committed_samples.append(
                    {
                        "entity_uid": entity.get("entity_uid"),
                        "identity": _identity(entity),
                        "commit_action": commit_action,
                        "commit_attempt": commit_attempt,
                        "target_id": int(new_snapshot["id"]),
                    }
                )

        committed_rows = int(summary["planned_writes"])
        conn.execute(
            """
            UPDATE vkpi_legacy_import_batches
            SET status='committed',
                committed_rows=?,
                committed_by_staff_id=?,
                committed_at=?,
                rolled_back_rows=0,
                rolled_back_by_staff_id=NULL,
                rolled_back_at=NULL,
                rollback_until=?,
                updated_at=?
            WHERE id=?
            """,
            (committed_rows, actor_staff_id, now, rollback_until, now, import_batch_id),
        )
        conn.execute(
            """
            INSERT INTO vkpi_legacy_import_logs (
              import_batch_id, actor_staff_id, action, status, detail, row_count, metadata_json
            ) VALUES (?, ?, 'commit_legacy_kol_pool', 'ok', ?, ?, ?)
            """,
            (
                import_batch_id,
                actor_staff_id,
                f"committed {committed_rows} legacy KOL entities into vkpi_kol_pool",
                committed_rows,
                json_dumps({"batch_uid": batch_uid, "summary": summary}),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "batch_uid": batch_uid,
        "batch_id": import_batch_id,
        "mode": "commit",
        "include_blocked": include_blocked,
        "commit_attempt": commit_attempt,
        "committed_refs_count": _committed_refs_count(import_batch_id, active_only=True),
        "committed_refs_total": _committed_refs_count(import_batch_id, active_only=False),
        "rollback_policy": rollback_policy,
        "rollback_until": rollback_until or "",
        "committed_samples": committed_samples,
        **summary,
    }


def preview_kol_pool_rollback(batch_uid: str, *, sample_limit: int = 20, force: bool = False) -> dict[str, Any]:
    ensure_legacy_staging_schema()
    batch = _fetch_batch(batch_uid)
    import_batch_id = int(batch["id"])
    window = _rollback_window(batch, force=force)
    rows = [
        _row_to_dict(row)
        for row in get_conn().execute(
            """
            SELECT *
            FROM vkpi_legacy_import_committed_refs
            WHERE import_batch_id=? AND target_table='vkpi_kol_pool' AND rollback_status='not_rolled_back'
            ORDER BY commit_attempt DESC, id DESC
            """,
            (import_batch_id,),
        ).fetchall()
    ]
    counts = Counter(_text(row.get("commit_action")) for row in rows)
    return {
        "batch_uid": batch_uid,
        "batch_id": import_batch_id,
        "mode": "rollback_preview",
        "rollback_refs_count": len(rows),
        "insert_refs": int(counts["insert"]),
        "update_refs": int(counts["update"]),
        "rollback_allowed": bool(window["allowed"]),
        "rollback_forced": bool(window["forced"]),
        "rollback_policy": window["policy"],
        "rollback_until": window["rollback_until"] or "",
        "rollback_window_reason": window["reason"],
        "samples": [
            {
                "ref_id": int(row["id"]),
                "commit_attempt": int(row.get("commit_attempt") or 1),
                "commit_action": row["commit_action"],
                "target_id": row["target_id"],
                "metadata": _load_json(row.get("metadata_json") or "{}", {}),
            }
            for row in rows[:sample_limit]
        ],
    }


def _restore_pool_row(pool_id: int, snapshot: dict[str, Any]) -> None:
    columns = [column for column in snapshot.keys() if column != "id"]
    if not columns:
        raise RuntimeError(f"missing previous snapshot for vkpi_kol_pool id={pool_id}")
    assignments = ", ".join(f"{column}=?" for column in columns)
    params = [snapshot.get(column) for column in columns]
    params.append(int(pool_id))
    get_conn().execute(f"UPDATE vkpi_kol_pool SET {assignments} WHERE id=?", params)


def rollback_kol_pool_commit(
    batch_uid: str,
    *,
    actor_staff_id: int | None = None,
    sample_limit: int = 20,
    force: bool = False,
) -> dict[str, Any]:
    ensure_legacy_staging_schema()
    conn = get_conn()
    batch = _fetch_batch(batch_uid)
    import_batch_id = int(batch["id"])
    preview = preview_kol_pool_rollback(batch_uid, sample_limit=sample_limit, force=force)
    if int(preview["rollback_refs_count"]) <= 0:
        raise RuntimeError("no committed vkpi_kol_pool refs to roll back")
    if not bool(preview.get("rollback_allowed")):
        raise RuntimeError(
            f"rollback not allowed: {preview.get('rollback_window_reason')} "
            f"(policy={preview.get('rollback_policy')}, rollback_until={preview.get('rollback_until')}); "
            "rerun with --force-rollback for emergency rollback"
        )

    now = _utcnow()
    rolled_back = 0
    try:
        conn.execute(
            "UPDATE vkpi_legacy_import_batches SET status='rolling_back', updated_at=? WHERE id=?",
            (now, import_batch_id),
        )
        refs = [
            _row_to_dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM vkpi_legacy_import_committed_refs
                WHERE import_batch_id=? AND target_table='vkpi_kol_pool' AND rollback_status='not_rolled_back'
                ORDER BY commit_attempt DESC, id DESC
                """,
                (import_batch_id,),
            ).fetchall()
        ]
        for ref in refs:
            target_id = int(ref["target_id"])
            action = _text(ref.get("commit_action"))
            if action == "insert":
                conn.execute("DELETE FROM vkpi_kol_pool WHERE id=?", (target_id,))
            elif action == "update":
                previous = _load_json(ref.get("previous_snapshot_json") or "{}", {})
                _restore_pool_row(target_id, previous)
            else:
                raise RuntimeError(f"unsupported rollback commit_action: {action}")
            conn.execute(
                """
                UPDATE vkpi_legacy_import_committed_refs
                SET rollback_status='rolled_back',
                    rolled_back_by_staff_id=?,
                    rolled_back_at=?
                WHERE id=?
                """,
                (actor_staff_id, now, int(ref["id"])),
            )
            rolled_back += 1

        conn.execute(
            """
            UPDATE vkpi_legacy_import_batches
            SET status='rolled_back',
                rolled_back_rows=?,
                rolled_back_by_staff_id=?,
                rolled_back_at=?,
                updated_at=?
            WHERE id=?
            """,
            (rolled_back, actor_staff_id, now, now, import_batch_id),
        )
        conn.execute(
            """
            INSERT INTO vkpi_legacy_import_logs (
              import_batch_id, actor_staff_id, action, status, detail, row_count, metadata_json
            ) VALUES (?, ?, 'rollback_legacy_kol_pool', 'ok', ?, ?, ?)
            """,
            (
                import_batch_id,
                actor_staff_id,
                f"rolled back {rolled_back} vkpi_kol_pool refs",
                rolled_back,
                json_dumps({"batch_uid": batch_uid, "preview": preview, "force_rollback": force}),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {**preview, "mode": "rollback", "rolled_back_refs": rolled_back}
