"""Fail-closed Action -> Project -> Outreach truth binding.

The request names an approved Action, never a Project or a message.  Under one
transaction the server re-verifies the immutable Action approval and prediction
run, resolves the only eligible project/first outbound snapshot, and records a
manager-attested append-only binding plus its required event.  Mutable message
rows are candidates for human verification; they are never an actual by
themselves.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.actions import approval_evidence
from app.domains.kol.identity import normalize_platform
from app.domains.market_brain import outreach_truth_coverage, prediction_truth
from app.domains.platform import event_ledger, review_contract
from app.domains.staff import is_manager_staff

TABLE = "vkpi_action_outreach_truth_bridges"
REPLY_TABLE = "vkpi_action_outreach_reply_truth_receipts"
EVENT_TYPE = "action_outreach_bound"
EVENT_SOURCE = "gtm.outreach_truth_bridge"
PREDICTION_ORGANIZATION = "viltrox"
ORGANIZATION_ID = 1
HORIZON_DAYS = 7
MIN_CLAIMABLE_COVERAGE = 0.90
MIN_CLAIMABLE_ACTUALS = 50

_REQUIRED_TABLES = (
    TABLE,
    "vkpi_action_inbox",
    "vkpi_prediction_runs",
    "vkpi_projects",
    "vkpi_kol_pool",
    "vkpi_messages",
    "vkpi_event_ledger",
)
_RUN_ID_RE = re.compile(r"^gtmact_([1-9][0-9]*)_kol_outreach_reply_outcome_7d$")

logger = get_logger(__name__)


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_dumps(value).encode("utf-8")).hexdigest()


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _sku(value: Any) -> str:
    return _text(value, 120).casefold()


def _channel(value: Any) -> str:
    return normalize_platform(_text(value, 60))


def _dt(value: Any) -> datetime | None:
    parsed = prediction_truth.parse_iso_datetime(value)
    return parsed.astimezone(timezone.utc) if parsed is not None else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "t", "true", "yes"}


def _iso(value: Any) -> str:
    parsed = _dt(value)
    if parsed is None:
        raise ValueError("invalid immutable timestamp")
    return parsed.isoformat()


def _server_now(conn: Any) -> datetime | None:
    row = conn.execute("SELECT CURRENT_TIMESTAMP AS server_now").fetchone()
    return _dt(dict(row).get("server_now")) if row is not None else None


def _request_contract(*, action_inbox_id: int, actor_staff_id: int) -> dict[str, Any]:
    return {
        "schema": "vkpi_action_outreach_binding_request/v2",
        "organization_id": ORGANIZATION_ID,
        "action_inbox_id": int(action_inbox_id),
        "actor_staff_id": int(actor_staff_id),
    }


def _binding_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "vkpi_action_outreach_truth_bridge/v2",
        "organization_id": int(row["organization_id"]),
        "action_inbox_id": int(row["action_inbox_id"]),
        "prediction_organization_id": str(row["prediction_organization_id"]),
        "prediction_run_id": str(row["prediction_run_id"]),
        "project_id": int(row["project_id"]),
        "kol_pool_id": int(row["kol_pool_id"]),
        "kol_id": int(row["kol_id"]),
        "product_sku": str(row["product_sku"]),
        "channel": str(row["channel"]),
        "first_outbound_message_id": int(row["first_outbound_message_id"]),
        "first_outbound_at": _iso(row["first_outbound_at"]),
        "first_outbound_created_at": _iso(row["first_outbound_created_at"]),
        "observation_start_at": _iso(row["observation_start_at"]),
        "observation_end_at": _iso(row["observation_end_at"]),
        "action_approved_at": _iso(row["action_approved_at"]),
        "approval_snapshot_sha256": str(row["approval_snapshot_sha256"]),
        "actor_staff_id": int(row["actor_staff_id"]),
        "correlation_id": str(row["correlation_id"]),
        "request_fingerprint": str(row["request_fingerprint"]),
        "verified_at": _iso(row["verified_at"]),
    }


def _binding_provenance(binding_fingerprint: str) -> dict[str, Any]:
    return {
        "evidence_verification": "manager_attested_server_resolved_outbound_snapshot",
        "approval_snapshot_reverified": True,
        "prediction_run_immutable": True,
        "bridge_immutable": True,
        "client_project_or_message_id_used": False,
        "client_metadata_used": False,
        "binding_fingerprint": binding_fingerprint,
    }


def _event_matches(conn: Any, binding: dict[str, Any]) -> bool:
    rows = conn.execute(
        """
        SELECT actor_id, payload_json, provenance_json
        FROM vkpi_event_ledger
        WHERE organization_id=? AND event_type=? AND entity_type='action_outreach_bridge'
          AND entity_id=? AND source=?
        ORDER BY id
        """,
        (ORGANIZATION_ID, EVENT_TYPE, str(binding["id"]), EVENT_SOURCE),
    ).fetchall()
    if len(rows) != 1:
        return False
    event = dict(rows[0])
    fingerprint = str(binding.get("binding_fingerprint") or "")
    return bool(
        str(event.get("actor_id") or "") == str(binding.get("actor_staff_id") or "")
        and _loads(event.get("payload_json"))
        == {**_binding_snapshot(binding), "binding_fingerprint": fingerprint}
        and _loads(event.get("provenance_json")) == _binding_provenance(fingerprint)
    )


def _binding_proof_valid(conn: Any, binding: dict[str, Any]) -> bool:
    try:
        expected = _sha256(_binding_snapshot(binding))
        return expected == str(binding.get("binding_fingerprint") or "") and _event_matches(conn, binding)
    except Exception:
        return False


def _load_binding(conn: Any, where: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM {TABLE} WHERE organization_id=? AND {where}",
        (ORGANIZATION_ID, *params),
    ).fetchone()
    return dict(row) if row is not None else None


def _idempotent_result(conn: Any, binding: dict[str, Any], *, request_fingerprint: str) -> dict[str, Any]:
    if str(binding.get("request_fingerprint") or "") != request_fingerprint:
        return {"ok": False, "reason": "outreach_binding_correlation_conflict"}
    if not _binding_proof_valid(conn, binding):
        return {"ok": False, "reason": "outreach_binding_event_conflict"}
    return {
        "ok": True,
        "id": int(binding["id"]),
        "action_inbox_id": int(binding["action_inbox_id"]),
        "project_id": int(binding["project_id"]),
        "first_outbound_message_id": int(binding["first_outbound_message_id"]),
        "prediction_run_id": str(binding["prediction_run_id"]),
        "correlation_id": str(binding["correlation_id"]),
        "idempotent": True,
    }


def _race_result(
    conn: Any, *, action_id: int, correlation: str, request_fingerprint: str,
) -> dict[str, Any] | None:
    by_correlation = _load_binding(conn, "correlation_id=?", (correlation,))
    if by_correlation is not None:
        return _idempotent_result(
            conn, by_correlation, request_fingerprint=request_fingerprint,
        )
    by_action = _load_binding(conn, "action_inbox_id=?", (action_id,))
    if by_action is not None:
        if not _binding_proof_valid(conn, by_action):
            return {"ok": False, "reason": "outreach_binding_event_conflict"}
        return {
            "ok": False,
            "reason": "outreach_action_already_bound",
            "existing_binding_id": int(by_action["id"]),
        }
    return None


def _action_id_from_run(run: dict[str, Any]) -> int:
    contract = prediction_truth.parse_evaluation_contract(run)
    action_id = _positive_int((contract or {}).get("target_action_inbox_id"))
    if action_id > 0:
        return action_id
    match = _RUN_ID_RE.fullmatch(str(run.get("run_id") or ""))
    return _positive_int(match.group(1)) if match else 0


def _validated_run(
    run: dict[str, Any], *, expected_action_id: int | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    from app.domains.market_brain.gtm_prediction_producer import REGISTRY_KEY, prediction_run_id

    contract = prediction_truth.parse_evaluation_contract(run)
    created = _dt(run.get("created_at"))
    start = _dt((contract or {}).get("observation_start_at"))
    action_id = _positive_int((contract or {}).get("target_action_inbox_id"))
    prediction = _loads(run.get("prediction"))
    kol_pool_id = _positive_int(prediction.get("kol_pool_id"))
    values = {
        "p10": _finite_float(run.get("p10")),
        "p50": _finite_float(run.get("p50")),
        "p90": _finite_float(run.get("p90")),
        "value": _finite_float(prediction.get("value")),
        "prediction_p10": _finite_float(prediction.get("p10")),
        "prediction_p50": _finite_float(prediction.get("p50")),
        "prediction_p90": _finite_float(prediction.get("p90")),
    }
    probabilities_valid = bool(
        all(value is not None for value in values.values())
        and 0.0 <= float(values["p10"]) <= float(values["p50"])
        <= float(values["p90"]) <= 1.0
        and values["value"] == values["p50"]
        and values["prediction_p10"] == values["p10"]
        and values["prediction_p50"] == values["p50"]
        and values["prediction_p90"] == values["p90"]
    )
    if (
        contract is None
        or str(run.get("organization_id") or "") != PREDICTION_ORGANIZATION
        or str(run.get("task_type") or "") != "kol_outreach_reply_probability"
        or str(contract.get("registry_key") or "") != REGISTRY_KEY
        or str(contract.get("outcome_action_type") or "") != "kol_outreach"
        or int(contract.get("horizon_days") or 0) != HORIZON_DAYS
        or int(run.get("horizon_days") or 0) != HORIZON_DAYS
        or action_id <= 0
        or (expected_action_id is not None and action_id != expected_action_id)
        or str(run.get("run_id") or "") != prediction_run_id(action_id)
        or start is None
        or created is None
        or start != created
        or kol_pool_id <= 0
        or not _sku(run.get("product_sku"))
        or not _channel(run.get("channel"))
        or not probabilities_valid
    ):
        return None, "outreach_prediction_contract_invalid"
    return {
        **run,
        "contract": contract,
        "action_inbox_id": action_id,
        "observation_start_at": start,
        "observation_end_at": start + timedelta(days=HORIZON_DAYS),
        "kol_pool_id": kol_pool_id,
        "p50": float(values["p50"]),
    }, None


def _registered_prediction(
    conn: Any, action_id: int,
) -> tuple[dict[str, Any] | None, str | None]:
    from app.domains.market_brain.gtm_prediction_producer import prediction_run_id

    row = conn.execute(
        """
        SELECT organization_id, run_id, task_type, product_sku, channel, horizon_days,
               input_summary, prediction, p10, p50, p90, created_at
        FROM vkpi_prediction_runs
        WHERE organization_id=? AND run_id=?
        """,
        (PREDICTION_ORGANIZATION, prediction_run_id(action_id)),
    ).fetchone()
    if row is None:
        return None, "outreach_prediction_not_found"
    return _validated_run(dict(row), expected_action_id=action_id)


def _message_rows(conn: Any, *, project_id: int, kol_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, project_id, kol_id, source, direction, body, snippet,
                   evidence_url, captured_at, created_at
            FROM vkpi_messages
            WHERE project_id=? AND kol_id=?
            ORDER BY captured_at, id
            """,
            (project_id, kol_id),
        ).fetchall()
    ]


def _lock_kol_message_scope(
    conn: Any, *, kol_id: int, project_ids: list[int] | None = None,
) -> None:
    """Freeze existing rows and FK parents against message/project phantoms on PG."""
    if not is_postgres_runtime():
        return
    conn.execute("SELECT id FROM kols WHERE id=? FOR UPDATE", (int(kol_id),)).fetchall()
    if project_ids:
        placeholders = ",".join("?" for _ in project_ids)
        conn.execute(
            f"SELECT id FROM vkpi_projects WHERE id IN ({placeholders}) FOR UPDATE",
            tuple(int(value) for value in project_ids),
        ).fetchall()
        conn.execute(
            f"SELECT id FROM vkpi_messages WHERE project_id IN ({placeholders}) FOR UPDATE",
            tuple(int(value) for value in project_ids),
        ).fetchall()
    else:
        conn.execute(
            "SELECT id FROM vkpi_projects WHERE kol_id=? FOR UPDATE", (int(kol_id),),
        ).fetchall()
        conn.execute(
            "SELECT id FROM vkpi_messages WHERE kol_id=? FOR UPDATE", (int(kol_id),),
        ).fetchall()


def _lock_pool_kol_message_scope(
    conn: Any, *, kol_pool_id: int, project_ids: list[int] | None = None,
) -> dict[str, Any] | None:
    """Lock the mutable identity root before deriving the exact outreach scope.

    On PostgreSQL the order is deliberately pool -> KOL -> projects -> messages.
    ``FOR UPDATE`` on the FK parents conflicts with the ``FOR KEY SHARE`` lock a
    concurrent project/message insert must acquire, so a later candidate scan
    cannot miss a phantom child row.  SQLite's write transaction provides the
    equivalent single-writer boundary for local operation.
    """
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    row = conn.execute(
        f"SELECT id, platform, linked_main_kol_id FROM vkpi_kol_pool "
        f"WHERE id=?{lock}",
        (int(kol_pool_id),),
    ).fetchone()
    if row is None:
        return None
    pool = dict(row)
    kol_id = _positive_int(pool.get("linked_main_kol_id"))
    if kol_id > 0:
        _lock_kol_message_scope(conn, kol_id=kol_id, project_ids=project_ids)
    return pool


def _eligible_project_outbounds(
    conn: Any,
    *,
    projects: list[dict[str, Any]],
    kol_id: int,
    start: datetime,
    end: datetime,
    approved_at: datetime,
    server_now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    candidates: list[dict[str, Any]] = []
    flags = {"preapproval": False, "unverified_clock": False, "in_window": False}
    for project in projects:
        in_window: list[tuple[datetime, int, dict[str, Any], datetime | None]] = []
        for row in _message_rows(conn, project_id=int(project["id"]), kol_id=kol_id):
            if _text(row.get("direction"), 20).lower() != "outbound":
                continue
            captured = _dt(row.get("captured_at"))
            created = _dt(row.get("created_at"))
            if captured is None or not start <= captured <= end:
                continue
            flags["in_window"] = True
            in_window.append((captured, int(row["id"]), row, created))
        if not in_window:
            continue
        captured, _message_id, row, created = min(
            in_window, key=lambda item: (item[0], item[1]),
        )
        # The causal anchor is the first outbound in the exact project window.
        # A later post-approval row may not hide an earlier pre-approval send.
        if captured < approved_at:
            flags["preapproval"] = True
            continue
        if (
            created is None
            or captured > created
            or created > end
            or created > server_now
        ):
            flags["unverified_clock"] = True
            continue
        candidates.append({
            "project": project,
            "outbound": {**row, "captured_at": captured, "created_at": created},
        })
    return candidates, flags


def _exact_projects(
    conn: Any, *, kol_id: int, product_sku: str, channel: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, kol_id, product_sku, platform, stage_status
        FROM vkpi_projects WHERE kol_id=? ORDER BY id
        """,
        (kol_id,),
    ).fetchall()
    return [
        project
        for project in (dict(row) for row in rows)
        if str(project.get("stage_status") or "").strip().lower() != "deleted"
        and _sku(project.get("product_sku")) == _sku(product_sku)
        and _channel(project.get("platform")) == _channel(channel)
    ]


def _action_row(
    conn: Any, action_id: int, *, lock_for_update: bool,
) -> dict[str, Any] | None:
    lock = " FOR UPDATE" if lock_for_update and is_postgres_runtime() else ""
    row = conn.execute(
        f"SELECT {approval_evidence.APPROVAL_CONTRACT_COLUMNS} "
        f"FROM vkpi_action_inbox WHERE id=?{lock}",
        (action_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _verified_action(
    conn: Any, action_id: int, *, lock_for_update: bool = True,
) -> tuple[dict[str, Any] | None, datetime | None, str | None]:
    action = _action_row(conn, action_id, lock_for_update=lock_for_update)
    if action is None:
        return None, None, "outreach_action_not_found"
    if (
        str(action.get("category") or "") != "gtm_bet"
        or str(action.get("status") or "") not in {"approved", "executing", "executed"}
        or not _truthy(action.get("requires_approval"))
    ):
        return None, None, "outreach_action_not_approved_gtm_bet"
    approved_at = _dt(action.get("approved_at"))
    if approved_at is None or not approval_evidence.verified_approval_snapshot(conn, action):
        return None, None, "outreach_action_approval_proof_invalid"
    return action, approved_at, None


def create_outreach_binding(
    action_inbox_id: int,
    *,
    correlation_id: str,
    staff: dict[str, Any] | None,
    _connection: Any = None,
) -> dict[str, Any]:
    """Append one manager-attested outbound binding and event atomically."""
    reviewer = review_contract.reviewer_context(staff)
    if reviewer is None or not is_manager_staff(staff or {}):
        return {"ok": False, "reason": "outreach_binding_scope_unavailable"}
    actor_id, organization_id = reviewer
    action_id = _positive_int(action_inbox_id)
    correlation = review_contract.normalize_correlation(correlation_id)
    if correlation is None:
        return {"ok": False, "reason": "outreach_binding_correlation_required"}
    if action_id <= 0:
        return {"ok": False, "reason": "outreach_binding_ids_required"}
    if organization_id != ORGANIZATION_ID:
        return {"ok": False, "reason": "outreach_binding_scope_unavailable"}
    if not all(table_exists(name) for name in _REQUIRED_TABLES):
        return {"ok": False, "reason": "outreach_binding_schema_unavailable"}

    request = _request_contract(action_inbox_id=action_id, actor_staff_id=actor_id)
    request_fingerprint = _sha256(request)
    conn = _connection or get_conn()
    try:
        if not is_postgres_runtime() and not bool(getattr(conn, "in_transaction", False)):
            conn.execute("BEGIN IMMEDIATE")
        replay = _race_result(
            conn, action_id=action_id, correlation=correlation,
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            conn.rollback()
            return replay

        action, approved_at, action_error = _verified_action(conn, action_id)
        if action is None or approved_at is None:
            conn.rollback()
            return {"ok": False, "reason": action_error}
        run, run_error = _registered_prediction(conn, action_id)
        if run is None:
            conn.rollback()
            return {"ok": False, "reason": run_error}
        server_now = _server_now(conn)
        if server_now is None:
            conn.rollback()
            return {"ok": False, "reason": "outreach_server_clock_unavailable"}

        pool = _lock_pool_kol_message_scope(
            conn, kol_pool_id=int(run["kol_pool_id"]),
        )
        if pool is None:
            conn.rollback()
            return {"ok": False, "reason": "outreach_kol_pool_not_found"}
        kol_id = _positive_int(pool.get("linked_main_kol_id"))
        run_channel = _channel(run.get("channel"))
        if kol_id <= 0:
            conn.rollback()
            return {"ok": False, "reason": "outreach_kol_link_missing"}
        if _channel(pool.get("platform")) != run_channel:
            conn.rollback()
            return {"ok": False, "reason": "outreach_kol_channel_mismatch"}

        projects = _exact_projects(
            conn, kol_id=kol_id, product_sku=str(run["product_sku"]), channel=run_channel,
        )
        if not projects:
            conn.rollback()
            return {"ok": False, "reason": "outreach_project_scope_not_found"}
        if len(projects) != 1:
            conn.rollback()
            return {"ok": False, "reason": "outreach_project_ambiguous"}
        candidates, flags = _eligible_project_outbounds(
            conn,
            projects=projects,
            kol_id=kol_id,
            start=run["observation_start_at"],
            end=run["observation_end_at"],
            approved_at=approved_at,
            server_now=server_now,
        )
        if not candidates:
            conn.rollback()
            if flags["preapproval"]:
                return {"ok": False, "reason": "outreach_outbound_precedes_approval"}
            if flags["unverified_clock"]:
                return {"ok": False, "reason": "outreach_outbound_evidence_unverified"}
            return {"ok": False, "reason": "outreach_first_outbound_not_observed"}
        if len(candidates) != 1:
            conn.rollback()
            return {"ok": False, "reason": "outreach_project_ambiguous"}

        project = candidates[0]["project"]
        outbound = candidates[0]["outbound"]
        binding_data = {
            **request,
            "schema": "vkpi_action_outreach_truth_bridge/v2",
            "prediction_organization_id": PREDICTION_ORGANIZATION,
            "prediction_run_id": str(run["run_id"]),
            "project_id": int(project["id"]),
            "kol_pool_id": int(run["kol_pool_id"]),
            "kol_id": kol_id,
            "product_sku": _text(run.get("product_sku"), 120),
            "channel": run_channel,
            "first_outbound_message_id": int(outbound["id"]),
            "first_outbound_at": outbound["captured_at"].isoformat(),
            "first_outbound_created_at": outbound["created_at"].isoformat(),
            "observation_start_at": run["observation_start_at"].isoformat(),
            "observation_end_at": run["observation_end_at"].isoformat(),
            "action_approved_at": approved_at.isoformat(),
            "approval_snapshot_sha256": str(action["approval_snapshot_sha256"]),
            "correlation_id": correlation,
            "request_fingerprint": request_fingerprint,
            "verified_at": server_now.isoformat(),
        }
        binding_fingerprint = _sha256(binding_data)
        inserted = conn.execute(
            f"""
            INSERT INTO {TABLE} (
                organization_id, action_inbox_id, prediction_organization_id,
                prediction_run_id, project_id, kol_pool_id, kol_id, product_sku,
                channel, first_outbound_message_id, first_outbound_at,
                first_outbound_created_at, observation_start_at, observation_end_at,
                action_approved_at, approval_snapshot_sha256, actor_staff_id,
                correlation_id, request_fingerprint, binding_fingerprint, verified_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            RETURNING id
            """,
            (
                ORGANIZATION_ID, action_id, PREDICTION_ORGANIZATION,
                binding_data["prediction_run_id"], binding_data["project_id"],
                binding_data["kol_pool_id"], kol_id, binding_data["product_sku"],
                run_channel, binding_data["first_outbound_message_id"],
                binding_data["first_outbound_at"], binding_data["first_outbound_created_at"],
                binding_data["observation_start_at"], binding_data["observation_end_at"],
                binding_data["action_approved_at"], binding_data["approval_snapshot_sha256"],
                actor_id, correlation, request_fingerprint, binding_fingerprint,
                binding_data["verified_at"],
            ),
        ).fetchone()
        if inserted is None:
            raise RuntimeError("outreach binding insert returned no id")
        binding_id = int(dict(inserted)["id"])
        event_ledger.insert_required(
            conn,
            EVENT_TYPE,
            entity_type="action_outreach_bridge",
            entity_id=binding_id,
            actor_type="staff",
            actor_id=actor_id,
            source=EVENT_SOURCE,
            payload={**binding_data, "binding_fingerprint": binding_fingerprint},
            trace_id=event_ledger.new_trace_id("action-outreach", action_id, binding_id),
            provenance=_binding_provenance(binding_fingerprint),
            organization_id=ORGANIZATION_ID,
        )
        conn.commit()
        return {
            "ok": True,
            "id": binding_id,
            "action_inbox_id": action_id,
            "project_id": int(project["id"]),
            "first_outbound_message_id": int(outbound["id"]),
            "prediction_run_id": str(run["run_id"]),
            "correlation_id": correlation,
            "idempotent": False,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("outreach_truth_bridge rollback failed", exc_info=True)
        try:
            raced = _race_result(
                conn, action_id=action_id, correlation=correlation,
                request_fingerprint=request_fingerprint,
            )
            if raced is not None:
                return raced
        except Exception:
            logger.debug("outreach_truth_bridge race read failed", exc_info=True)
        logger.warning("outreach binding write failed action_id=%s", action_id, exc_info=True)
        return {"ok": False, "reason": "outreach_binding_write_failed"}


def get_outreach_binding_status(
    action_inbox_id: int,
    *,
    staff: dict[str, Any] | None,
    _connection: Any = None,
) -> dict[str, Any]:
    """Recover a proof-valid binding after refresh without exposing message data."""
    reviewer = review_contract.reviewer_context(staff)
    if reviewer is None or not is_manager_staff(staff or {}):
        return {"ok": False, "reason": "outreach_binding_scope_unavailable"}
    _actor_id, organization_id = reviewer
    action_id = _positive_int(action_inbox_id)
    if organization_id != ORGANIZATION_ID:
        return {"ok": False, "reason": "outreach_binding_scope_unavailable"}
    if action_id <= 0:
        return {"ok": False, "reason": "outreach_binding_ids_required"}
    if not table_exists(TABLE):
        return {"ok": False, "reason": "outreach_binding_schema_unavailable"}
    conn = _connection or get_conn()
    try:
        binding = _load_binding(conn, "action_inbox_id=?", (action_id,))
        if binding is None:
            action = _action_row(conn, action_id, lock_for_update=False)
            if action is None: return {"ok": False, "reason": "outreach_action_not_found"}
            _eligible, approved_at, eligibility_error = _verified_action(conn, action_id, lock_for_update=False)
            if eligibility_error == "outreach_action_not_found": return {"ok": False, "reason": eligibility_error}
            bindable = approved_at is not None and eligibility_error is None
            return {"ok": True, "status": "unbound", "bound": False, "bindable": bindable,
                    "action_inbox_id": action_id, "eligibility_reason": "eligible" if bindable else eligibility_error, "binding": None, "reply_verification": None}
        if not _binding_proof_valid(conn, binding):
            return {"ok": False, "reason": "outreach_binding_event_conflict"}
        receipt_summary = None
        if table_exists(REPLY_TABLE):
            from app.domains.market_brain import outreach_reply_truth

            receipt = outreach_reply_truth.verified_receipt_for_binding(conn, binding)
            if receipt is not None:
                receipt_summary = {
                    "id": int(receipt["id"]),
                    "outcome": str(receipt["outcome"]),
                    "verified_at": _iso(receipt["verified_at"]),
                    "review_candidate_sha256": str(receipt["review_candidate_sha256"]),
                    "review_candidate": _loads(receipt.get("review_candidate_json")),
                    "review_candidate_canonical_json": review_contract.canonical_review_json(
                        _loads(receipt.get("review_candidate_json")),
                    ),
                }
        return {
            "ok": True,
            "status": (
                "reply_verified" if receipt_summary is not None
                else "bound_pending_reply_verification"
            ),
            "binding": {
                "id": int(binding["id"]),
                "action_inbox_id": int(binding["action_inbox_id"]),
                "prediction_run_id": str(binding["prediction_run_id"]),
                "project_id": int(binding["project_id"]),
                "kol_pool_id": int(binding["kol_pool_id"]),
                "product_sku": str(binding["product_sku"]),
                "channel": str(binding["channel"]),
                "first_outbound_at": _iso(binding["first_outbound_at"]),
                "observation_start_at": _iso(binding["observation_start_at"]),
                "observation_end_at": _iso(binding["observation_end_at"]),
                "binding_fingerprint": str(binding["binding_fingerprint"]),
            },
            "reply_verification": receipt_summary,
        }
    except Exception:
        logger.warning("outreach binding status failed action_id=%s", action_id, exc_info=True)
        return {"ok": False, "reason": "outreach_binding_status_unavailable"}


def resolve_reply_actual(
    conn: Any,
    *,
    action_inbox_id: int,
    kol_pool_id: int,
    kol_id: int,
    product_sku: str,
    channel: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Resolve 0/1 only from a valid bridge and immutable reply receipt."""
    missing = {
        "reply_outcome": None,
        "reply_outcome_binding": "server_owned_action_project_outreach_bridge_missing",
        "reply_outcome_first_outbound_at": None,
        "reply_outcome_correlated_inbound_n": 0,
        "reply_outcome_note": (
            "Client-controlled message metadata is not verification; mutable message rows "
            "are candidates, not verified actuals."
        ),
    }
    action_id = _positive_int(action_inbox_id)
    if action_id <= 0 or not callable(getattr(conn, "execute", None)):
        return missing
    try:
        binding = _load_binding(conn, "action_inbox_id=?", (action_id,))
        if binding is None:
            return missing
        if not _binding_proof_valid(conn, binding):
            return {
                **missing,
                "reply_outcome_binding": "server_owned_bridge_proof_invalid",
                "reply_outcome_note": "Binding hash or exactly-one required event is invalid.",
            }
        bound_start = _dt(binding.get("observation_start_at"))
        bound_end = _dt(binding.get("observation_end_at"))
        first_outbound = _dt(binding.get("first_outbound_at"))
        caller_start = _dt(start)
        caller_end = _dt(end)
        if (
            int(binding.get("kol_pool_id") or 0) != int(kol_pool_id or 0)
            or int(binding.get("kol_id") or 0) != int(kol_id or 0)
            or _sku(binding.get("product_sku")) != _sku(product_sku)
            or _channel(binding.get("channel")) != _channel(channel)
            or None in {bound_start, bound_end, first_outbound, caller_start, caller_end}
            or bound_start != caller_start
            or bound_end != caller_end
            or not bound_start <= first_outbound <= bound_end
        ):
            return {**missing, "reply_outcome_binding": "server_owned_bridge_contract_mismatch"}
        from app.domains.market_brain import outreach_reply_truth

        resolved = outreach_reply_truth.resolve_verified_reply(conn, binding)
        if resolved is None:
            return {
                **missing,
                "reply_outcome_binding": "server_owned_reply_evidence_unverified",
                "reply_outcome_bridge_id": int(binding["id"]),
                "reply_outcome_project_id": int(binding["project_id"]),
                "reply_outcome_first_outbound_message_id": int(
                    binding["first_outbound_message_id"]
                ),
                "reply_outcome_first_outbound_at": first_outbound.isoformat(),
                "reply_outcome_note": (
                    "A manager must append an immutable replied/no-reply receipt; raw rows "
                    "alone never establish an actual."
                ),
            }
        return resolved
    except Exception:
        logger.warning("outreach actual read failed action_id=%s", action_id, exc_info=True)
        return {**missing, "reply_outcome_binding": "server_owned_bridge_read_unavailable"}


def _coverage_result() -> dict[str, Any]:
    return {
        "status": "ok", "registered_due": 0, "valid_registered_due": 0,
        "invalid_due_contract": 0,
        "verified_bound": 0,
        "verified_actual": 0,
        "missing_verified_actual": 0,
        "censored_no_outbound_unverified": 0,
        "unbound": 0,
        "invalid_bridge": 0,
        "binding_coverage": None, "actual_coverage": None,
        "bound_actual_coverage": None,
        "binding_claimable": False,
        "actual_claimable": False,
        "manager_attested_sample_ready": False, "provider_completeness_verified": False,
        "evidence_class": "manager_attested_mutable_message_snapshot",
        "claimable": False, "claim_level": "descriptive_only",
        "claim_blockers": ["provider_sync_completeness_receipt_missing"],
        "minimum_coverage": MIN_CLAIMABLE_COVERAGE,
        "minimum_verified_actuals": MIN_CLAIMABLE_ACTUALS,
        "invalid_run_ids": [], "unbound_action_ids": [],
    }


def outreach_prediction_coverage(
    conn: Any = None, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Count every due binary outreach run; invalid/missing evidence stays in denominator."""
    result = _coverage_result()
    if not table_exists("vkpi_prediction_runs"):
        return {**result, "status": "not_applicable"}
    db = conn or get_conn()
    current = _dt(now) if now is not None else datetime.now(timezone.utc)
    current = current or datetime.now(timezone.utc)
    try:
        rows = db.execute(
            """
            SELECT organization_id, run_id, task_type, product_sku, channel,
                   horizon_days, input_summary, prediction, p10, p50, p90, created_at
            FROM vkpi_prediction_runs
            WHERE organization_id=? AND task_type='kol_outreach_reply_probability'
            ORDER BY created_at, run_id
            """,
            (PREDICTION_ORGANIZATION,),
        ).fetchall()
        outreach_truth_coverage.process_coverage_rows(
            db, rows, current, result, globals(),
        )
        outreach_truth_coverage.finalize_coverage(result, globals())
        # Mutable source messages lack a completeness watermark/late-arrival reconciliation.
        return result
    except Exception:
        logger.warning("outreach prediction coverage failed", exc_info=True)
        return {**result, "status": "error", "claim_level": "descriptive_only"}


__all__ = [
    "TABLE",
    "REPLY_TABLE",
    "MIN_CLAIMABLE_COVERAGE",
    "MIN_CLAIMABLE_ACTUALS",
    "create_outreach_binding",
    "get_outreach_binding_status",
    "resolve_reply_actual",
    "outreach_prediction_coverage",
]
