"""P2C entity resolution for legacy V-KPI staging rows."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.vkpi.legacy_import_audit import _first_url, _text
from app.services.vkpi.legacy_import_staging import (
    PIPELINE_TABLES,
    ensure_legacy_staging_schema,
    json_dumps,
)
from app.services.vkpi.legacy_entity_resolution_decisions import (
    DECISION_STATUS,
    identity_label as _identity_label,
    validate_decision as _validate_decision,
)
from app.services.vkpi.legacy_entity_resolution_format import (
    format_bulk_decision_result,
    format_decision_result,
    format_entity_detail,
    format_pending_reviews,
    format_resolution_summary,
    format_review_progress,
)


logger = get_logger(__name__)


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row.items()) if hasattr(row, "items") else dict(row)


def _load_json(value: str) -> Any:
    try:
        return json.loads(value or "{}")
    except Exception as exc:
        logger.warning("legacy entity resolution json parse failed: %s", exc)
        return {}


def _profile_url_from_raw(raw: dict[str, Any]) -> str:
    for key in ("主页链接", "频道/主页链接", "红人视频链接", "发布链接", "内容发布链接", "回片链接"):
        if _text(raw.get(key)):
            return _text(raw.get(key))
    return _first_url(" ".join(_text(value) for value in raw.values()))


def _canonical_key(row: dict[str, Any]) -> str:
    key = _text(row.get("dedup_key"))
    if key:
        return key.lower()
    platform = _text(row.get("normalized_platform") or row.get("platform")).lower()
    handle = _text(row.get("normalized_handle") or row.get("handle")).lower()
    return f"{platform}:{handle}" if platform and handle else ""


def _split_key(key: str) -> tuple[str, str]:
    if ":" not in key:
        return "", key
    platform, handle = key.split(":", 1)
    return platform, handle


def _fetch_batch_id(batch_uid: str) -> int:
    row = get_conn().execute("SELECT id FROM vkpi_legacy_import_batches WHERE batch_uid=?", (batch_uid,)).fetchone()
    if not row:
        raise ValueError(f"batch not found: {batch_uid}")
    return int(row["id"])


def _fetch_staging_rows(import_batch_id: int) -> list[dict[str, Any]]:
    conn = get_conn()
    queries = {
        "kol_profiles": """
            SELECT id, source_sheet, source_row, platform, normalized_platform,
                   handle, normalized_handle, dedup_key, display_name, country,
                   region, category, email, phone, contact_missing,
                   contact_visibility_level, raw_row_json
            FROM vkpi_legacy_kol_profiles_staging
            WHERE import_batch_id=?
        """,
        "cooperations": """
            SELECT id, source_sheet, source_row, platform, normalized_platform,
                   handle, normalized_handle, dedup_key, display_name, product,
                   project, status, content_link, cost_amount, cost_currency,
                   raw_row_json
            FROM vkpi_legacy_cooperations_staging
            WHERE import_batch_id=?
        """,
        "risk_watchlist": """
            SELECT id, source_sheet, source_row, platform, normalized_platform,
                   handle, normalized_handle, dedup_key, display_name, risk_type,
                   risk_reason, severity, evidence, status, raw_row_json
            FROM vkpi_legacy_risk_watchlist_staging
            WHERE import_batch_id=?
        """,
    }
    rows: list[dict[str, Any]] = []
    for pipeline, sql in queries.items():
        table = PIPELINE_TABLES[pipeline]
        for row in conn.execute(sql, (import_batch_id,)).fetchall():
            item = _row_to_dict(row)
            item["pipeline"] = pipeline
            item["staging_table"] = table
            item["canonical_key"] = _canonical_key(item)
            item["raw"] = _load_json(item.get("raw_row_json") or "{}")
            rows.append(item)
    return rows


def _pick_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profiles = [row for row in rows if row["pipeline"] == "kol_profiles"]
    if profiles:
        return profiles[0]
    return rows[0]


def _contact_status(profile: dict[str, Any]) -> str:
    if _text(profile.get("email")) or _text(profile.get("phone")):
        return "available_restricted"
    if profile.get("pipeline") == "kol_profiles" and bool(profile.get("contact_missing")):
        return "missing"
    return "unknown"


def _weak_label(
    *,
    profile_count: int,
    cooperation_count: int,
    risk_rows: list[dict[str, Any]],
    contact_status: str,
) -> tuple[str, float, list[str]]:
    reasons: list[str] = []
    high_risk = any(_text(row.get("severity")).lower() == "high" for row in risk_rows)
    if profile_count == 0:
        reasons.append("missing_kol_profile")
    if risk_rows:
        reasons.append("risk_watchlist")
    if contact_status == "missing":
        reasons.append("contact_missing")
    if cooperation_count == 0:
        reasons.append("no_cooperation_history")

    if high_risk:
        return "blocked_risk", 0.82, reasons
    if profile_count and cooperation_count and not risk_rows:
        return "ready", 0.98, reasons
    if profile_count and not risk_rows:
        return "profile_only_review", 0.9, reasons
    if profile_count and risk_rows:
        return "risk_review", 0.88, reasons
    if cooperation_count:
        return "profile_missing_review", 0.78, reasons
    return "manual_review", 0.65, reasons or ["low_evidence"]


def _entity_payload(canonical_key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    platform, handle = _split_key(canonical_key)
    profile = _pick_profile(rows)
    risk_rows = [row for row in rows if row["pipeline"] == "risk_watchlist"]
    profile_count = sum(1 for row in rows if row["pipeline"] == "kol_profiles")
    cooperation_count = sum(1 for row in rows if row["pipeline"] == "cooperations")
    contact_status = _contact_status(profile)
    weak_label, confidence, reasons = _weak_label(
        profile_count=profile_count,
        cooperation_count=cooperation_count,
        risk_rows=risk_rows,
        contact_status=contact_status,
    )
    display_name = next((_text(row.get("display_name")) for row in rows if _text(row.get("display_name"))), handle)
    profile_url = next((_profile_url_from_raw(row.get("raw") or {}) for row in rows if _profile_url_from_raw(row.get("raw") or {})), "")
    identity = {
        "canonical_key": canonical_key,
        "platform": platform,
        "handle": handle,
        "display_name_candidates": sorted({_text(row.get("display_name")) for row in rows if _text(row.get("display_name"))}),
        "profile_url": profile_url,
        "contact_status": contact_status,
    }
    evidence = {
        "sources": Counter(row["pipeline"] for row in rows),
        "source_rows": [
            {
                "pipeline": row["pipeline"],
                "staging_id": int(row["id"]),
                "source_sheet": row["source_sheet"],
                "source_row": int(row["source_row"]),
            }
            for row in rows[:50]
        ],
        "risk": [
            {
                "risk_type": _text(row.get("risk_type")),
                "severity": _text(row.get("severity")),
                "status": _text(row.get("status")),
            }
            for row in risk_rows
        ],
    }
    return {
        "canonical_key": canonical_key,
        "normalized_platform": platform,
        "normalized_handle": handle,
        "display_name": display_name,
        "profile_url": profile_url,
        "country": _text(profile.get("country")),
        "region": _text(profile.get("region")),
        "category": _text(profile.get("category")),
        "email": _text(profile.get("email")),
        "phone": _text(profile.get("phone")),
        "contact_status": contact_status,
        "contact_visibility_level": _text(profile.get("contact_visibility_level")) or "restricted",
        "confidence_score": confidence,
        "weak_label": weak_label,
        "resolution_status": "candidate",
        "evidence_count": len(rows),
        "kol_profile_rows": profile_count,
        "cooperation_rows": cooperation_count,
        "risk_rows": len(risk_rows),
        "review_reason_json": json_dumps(reasons),
        "identity_json": json_dumps(identity),
        "evidence_json": json_dumps(evidence),
    }

def _insert_entity(conn: Any, import_batch_id: int, run_id: int, payload: dict[str, Any]) -> int:
    columns = [
        "import_batch_id",
        "run_id",
        "entity_uid",
        "canonical_key",
        "normalized_platform",
        "normalized_handle",
        "display_name",
        "profile_url",
        "country",
        "region",
        "category",
        "email",
        "phone",
        "contact_status",
        "contact_visibility_level",
        "confidence_score",
        "weak_label",
        "resolution_status",
        "evidence_count",
        "kol_profile_rows",
        "cooperation_rows",
        "risk_rows",
        "review_reason_json",
        "identity_json",
        "evidence_json",
    ]
    entity_uid = "legacy_kol_" + hashlib.sha1(f"{import_batch_id}:{payload['canonical_key']}".encode("utf-8")).hexdigest()[:20]
    values = {**payload, "import_batch_id": import_batch_id, "run_id": run_id, "entity_uid": entity_uid}
    placeholders = ", ".join("?" for _ in columns)
    row = conn.execute(
        f"INSERT INTO vkpi_legacy_kol_entities ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id",
        [values.get(column) for column in columns],
    ).fetchone()
    return int(row["id"])


def _insert_refs(conn: Any, import_batch_id: int, entity_id: int, canonical_key: str, rows: list[dict[str, Any]]) -> int:
    inserted = 0
    for row in rows:
        confidence = 0.99 if row["pipeline"] == "kol_profiles" else 0.9 if row["pipeline"] == "cooperations" else 0.85
        conn.execute(
            """
            INSERT INTO vkpi_legacy_kol_entity_refs (
              import_batch_id, entity_id, pipeline, staging_table, staging_id,
              source_sheet, source_row, match_key, match_method, confidence_score,
              evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'dedup_key', ?, ?)
            """,
            (
                import_batch_id,
                entity_id,
                row["pipeline"],
                row["staging_table"],
                int(row["id"]),
                row["source_sheet"],
                int(row["source_row"]),
                canonical_key,
                confidence,
                json_dumps({"raw_dedup_key": row.get("dedup_key", ""), "display_name": row.get("display_name", "")}),
            ),
        )
        inserted += 1
    return inserted


def resolve_batch(batch_uid: str, *, reset: bool = True) -> dict[str, Any]:
    ensure_legacy_staging_schema()
    conn = get_conn()
    import_batch_id = _fetch_batch_id(batch_uid)
    run_uid = f"p2c_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:10]}"
    try:
        if reset:
            conn.execute("DELETE FROM vkpi_legacy_kol_entity_refs WHERE import_batch_id=?", (import_batch_id,))
            conn.execute("DELETE FROM vkpi_legacy_kol_entities WHERE import_batch_id=?", (import_batch_id,))
            conn.execute("DELETE FROM vkpi_legacy_resolution_runs WHERE import_batch_id=?", (import_batch_id,))
        run_row = conn.execute(
            """
            INSERT INTO vkpi_legacy_resolution_runs (import_batch_id, run_uid, status, metadata_json)
            VALUES (?, ?, 'running', ?)
            RETURNING id
            """,
            (import_batch_id, run_uid, json_dumps({"batch_uid": batch_uid})),
        ).fetchone()
        run_id = int(run_row["id"])
        rows = _fetch_staging_rows(import_batch_id)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        no_identifier_rows = 0
        for row in rows:
            key = row.get("canonical_key") or ""
            if not key:
                no_identifier_rows += 1
                continue
            grouped[key].append(row)

        ref_count = 0
        for canonical_key, entity_rows in sorted(grouped.items()):
            payload = _entity_payload(canonical_key, entity_rows)
            entity_id = _insert_entity(conn, import_batch_id, run_id, payload)
            ref_count += _insert_refs(conn, import_batch_id, entity_id, canonical_key, entity_rows)

        summary = _resolution_counts(import_batch_id)
        metadata = {
            "batch_uid": batch_uid,
            "run_uid": run_uid,
            "no_identifier_rows": no_identifier_rows,
            "input_rows": len(rows),
            "label_counts": summary["label_counts"],
            "pipeline_ref_counts": summary["pipeline_ref_counts"],
        }
        conn.execute(
            """
            UPDATE vkpi_legacy_resolution_runs
            SET status='completed',
                entity_count=?,
                ref_count=?,
                ready_count=?,
                review_count=?,
                blocked_count=?,
                metadata_json=?,
                completed_at=?
            WHERE id=?
            """,
            (
                summary["entity_count"],
                ref_count,
                summary["ready_count"],
                summary["review_count"],
                summary["blocked_count"],
                json_dumps(metadata),
                datetime.utcnow().isoformat(timespec="seconds"),
                run_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO vkpi_legacy_import_logs (
              import_batch_id, action, status, detail, row_count, metadata_json
            ) VALUES (?, 'resolve_legacy_kol_entities', 'ok', ?, ?, ?)
            """,
            (
                import_batch_id,
                f"resolved {summary['entity_count']} canonical KOL candidates",
                summary["entity_count"],
                json_dumps(metadata),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return inspect_resolution(batch_uid)


def _resolution_counts(import_batch_id: int) -> dict[str, Any]:
    conn = get_conn()
    label_rows = conn.execute(
        """
        SELECT weak_label, COUNT(*) AS n
        FROM vkpi_legacy_kol_entities
        WHERE import_batch_id=?
        GROUP BY weak_label
        ORDER BY n DESC
        """,
        (import_batch_id,),
    ).fetchall()
    ref_rows = conn.execute(
        """
        SELECT pipeline, COUNT(*) AS n
        FROM vkpi_legacy_kol_entity_refs
        WHERE import_batch_id=?
        GROUP BY pipeline
        ORDER BY n DESC
        """,
        (import_batch_id,),
    ).fetchall()
    label_counts = {row["weak_label"]: int(row["n"]) for row in label_rows}
    pipeline_ref_counts = {row["pipeline"]: int(row["n"]) for row in ref_rows}
    entity_count = sum(label_counts.values())
    ready_count = int(label_counts.get("ready", 0))
    blocked_count = sum(count for label, count in label_counts.items() if label.startswith("blocked"))
    review_count = entity_count - ready_count - blocked_count
    return {
        "entity_count": entity_count,
        "ready_count": ready_count,
        "review_count": review_count,
        "blocked_count": blocked_count,
        "label_counts": label_counts,
        "pipeline_ref_counts": pipeline_ref_counts,
    }


def inspect_resolution(batch_uid: str) -> dict[str, Any]:
    ensure_legacy_staging_schema()
    conn = get_conn()
    import_batch_id = _fetch_batch_id(batch_uid)
    run = conn.execute(
        """
        SELECT *
        FROM vkpi_legacy_resolution_runs
        WHERE import_batch_id=?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (import_batch_id,),
    ).fetchone()
    counts = _resolution_counts(import_batch_id)
    metadata: dict[str, Any] = {}
    if run:
        metadata = _load_json(run["metadata_json"])
    no_identifier_rows = int(metadata.get("no_identifier_rows") or 0)
    return {
        "batch_uid": batch_uid,
        "run_uid": run["run_uid"] if run else "",
        "status": run["status"] if run else "missing",
        "entity_count": counts["entity_count"],
        "ready_count": counts["ready_count"],
        "review_count": counts["review_count"],
        "blocked_count": counts["blocked_count"],
        "no_identifier_rows": no_identifier_rows,
        "label_counts": counts["label_counts"],
        "pipeline_ref_counts": counts["pipeline_ref_counts"],
    }


def list_pending_reviews(
    batch_uid: str,
    *,
    weak_label: str = "",
    include_blocked: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    ensure_legacy_staging_schema()
    conn = get_conn()
    import_batch_id = _fetch_batch_id(batch_uid)
    where = [
        "import_batch_id=?",
        "weak_label <> 'ready'",
        "resolution_decision IS NULL",
    ]
    params: list[Any] = [import_batch_id]
    label = _text(weak_label)
    if label:
        where.append("weak_label=?")
        params.append(label)
    elif not include_blocked:
        where.append("weak_label <> 'blocked_risk'")

    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM vkpi_legacy_kol_entities WHERE {where_sql}",
        params,
    ).fetchone()
    query = f"""
        SELECT id, entity_uid, weak_label, resolution_status, normalized_platform,
               normalized_handle, display_name, evidence_count, kol_profile_rows,
               cooperation_rows, risk_rows, resolution_decision
        FROM vkpi_legacy_kol_entities
        WHERE {where_sql}
        ORDER BY weak_label, normalized_platform, normalized_handle, id
    """
    query_params = list(params)
    if limit > 0:
        query += " LIMIT ?"
        query_params.append(int(limit))
    rows = [_row_to_dict(row) for row in conn.execute(query, query_params).fetchall()]
    return {
        "batch_uid": batch_uid,
        "pending_count": int(total["n"] if total else 0),
        "shown_count": len(rows),
        "include_blocked": include_blocked,
        "weak_label": label,
        "rows": rows,
    }


def show_entity(entity_uid: str, *, ref_limit: int = 100) -> dict[str, Any]:
    ensure_legacy_staging_schema()
    conn = get_conn()
    entity = _fetch_entity_by_uid(entity_uid)
    refs = []
    for ref_row in conn.execute(
        """
        SELECT *
        FROM vkpi_legacy_kol_entity_refs
        WHERE entity_id=?
        ORDER BY pipeline, source_sheet, source_row, id
        LIMIT ?
        """,
        (int(entity["id"]), int(ref_limit)),
    ).fetchall():
        ref = _row_to_dict(ref_row)
        table = PIPELINE_TABLES.get(_text(ref.get("pipeline")), "")
        raw_row_json = "{}"
        if table:
            raw_row = conn.execute(f"SELECT raw_row_json FROM {table} WHERE id=?", (int(ref["staging_id"]),)).fetchone()
            if raw_row:
                raw_row_json = raw_row["raw_row_json"] or "{}"
        ref["raw_row_json"] = raw_row_json
        refs.append(ref)

    return {
        "batch_uid": entity.get("batch_uid", ""),
        "entity": entity,
        "refs": refs,
        "ref_count": len(refs),
    }


def decide_resolution(
    entity_uid: str,
    *,
    action: str,
    target_entity_uid: str = "",
    reason: str = "",
    note: str = "",
    actor: str = "cli",
    commit: bool = False,
) -> dict[str, Any]:
    ensure_legacy_staging_schema()
    conn = get_conn()
    entity = _fetch_entity_by_uid(entity_uid)
    action = _text(action)
    target = _validate_decision(
        entity,
        action=action,
        target_entity_uid=target_entity_uid,
        reason=reason,
        note=note,
    )
    result = {
        "committed": bool(commit),
        "mode": "commit" if commit else "dry_run",
        "entity_uid": entity_uid,
        "identity": _identity_label(entity),
        "weak_label": entity.get("weak_label", ""),
        "previous_decision": entity.get("resolution_decision"),
        "action": action,
        "target_entity_uid": target.get("entity_uid", ""),
        "target_identity": _identity_label(target) if target else "",
        "reason": _text(reason),
        "note": _text(note),
    }
    if not commit:
        return result

    try:
        conn.execute(
            """
            UPDATE vkpi_legacy_kol_entities
            SET resolution_decision=?,
                merge_target_entity_id=?,
                merge_target_uid=?,
                decision_reason=?,
                decision_note=?,
                decided_by=?,
                decided_at=?,
                resolution_status=?
            WHERE id=?
            """,
            (
                action,
                int(target["id"]) if target else None,
                target.get("entity_uid", "") if target else "",
                _text(reason),
                _text(note),
                _text(actor) or "cli",
                datetime.utcnow().isoformat(timespec="seconds"),
                DECISION_STATUS[action],
                int(entity["id"]),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def bulk_decide(
    batch_uid: str,
    *,
    weak_label: str,
    action: str,
    reason: str = "",
    note: str = "",
    actor: str = "",
    commit: bool = False,
    sample_limit: int = 5,
) -> dict[str, Any]:
    ensure_legacy_staging_schema()
    label = _text(weak_label)
    if not label:
        raise ValueError("bulk decisions require --weak-label")
    action = _text(action)
    if action == "merge_with":
        raise ValueError("bulk merge_with is not supported; decide merges one entity at a time")
    fake_entity = {"weak_label": label}
    _validate_decision(fake_entity, action=action, reason=reason, note=note)

    conn = get_conn()
    import_batch_id = _fetch_batch_id(batch_uid)
    rows = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT id, entity_uid, weak_label, normalized_platform, normalized_handle,
                   display_name, evidence_count, kol_profile_rows, cooperation_rows, risk_rows
            FROM vkpi_legacy_kol_entities
            WHERE import_batch_id=?
              AND weak_label=?
              AND weak_label <> 'ready'
              AND resolution_decision IS NULL
            ORDER BY normalized_platform, normalized_handle, id
            """,
            (import_batch_id, label),
        ).fetchall()
    ]
    sample = rows[: max(0, int(sample_limit))]
    result = {
        "batch_uid": batch_uid,
        "committed": bool(commit),
        "mode": "commit" if commit else "dry_run",
        "weak_label": label,
        "action": action,
        "count": len(rows),
        "sample": sample,
        "reason": _text(reason),
        "note": _text(note),
    }
    if not commit or not rows:
        return result

    decided_by = _text(actor) or f"bulk:{label}:{action}"
    try:
        conn.execute(
            """
            UPDATE vkpi_legacy_kol_entities
            SET resolution_decision=?,
                merge_target_entity_id=NULL,
                merge_target_uid='',
                decision_reason=?,
                decision_note=?,
                decided_by=?,
                decided_at=?,
                resolution_status=?
            WHERE import_batch_id=?
              AND weak_label=?
              AND weak_label <> 'ready'
              AND resolution_decision IS NULL
            """,
            (
                action,
                _text(reason),
                _text(note),
                decided_by,
                datetime.utcnow().isoformat(timespec="seconds"),
                DECISION_STATUS[action],
                import_batch_id,
                label,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result


def review_progress(batch_uid: str) -> dict[str, Any]:
    ensure_legacy_staging_schema()
    conn = get_conn()
    import_batch_id = _fetch_batch_id(batch_uid)
    rows = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT weak_label, resolution_decision, resolution_status, COUNT(*) AS n
            FROM vkpi_legacy_kol_entities
            WHERE import_batch_id=?
            GROUP BY weak_label, resolution_decision, resolution_status
            ORDER BY weak_label, resolution_decision, resolution_status
            """,
            (import_batch_id,),
        ).fetchall()
    ]
    totals = _resolution_counts(import_batch_id)
    pending = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_legacy_kol_entities
        WHERE import_batch_id=?
          AND weak_label <> 'ready'
          AND weak_label <> 'blocked_risk'
          AND resolution_decision IS NULL
        """,
        (import_batch_id,),
    ).fetchone()
    blocked_pending = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_legacy_kol_entities
        WHERE import_batch_id=?
          AND weak_label='blocked_risk'
          AND resolution_decision IS NULL
        """,
        (import_batch_id,),
    ).fetchone()
    return {
        "batch_uid": batch_uid,
        "entity_count": totals["entity_count"],
        "ready_count": totals["ready_count"],
        "review_count": totals["review_count"],
        "blocked_count": totals["blocked_count"],
        "pending_count": int(pending["n"] if pending else 0),
        "blocked_pending_count": int(blocked_pending["n"] if blocked_pending else 0),
        "rows": rows,
    }
