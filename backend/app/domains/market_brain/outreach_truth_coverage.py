"""Behavior-preserving coverage calculations for outreach truth bindings."""
from __future__ import annotations

from typing import Any


def _binding_matches(binding: dict[str, Any], run: dict[str, Any], action_id: int, ops: dict[str, Any]) -> bool:
    return bool(
        int(binding.get("action_inbox_id") or 0) == action_id
        and int(binding.get("kol_pool_id") or 0) == int(run["kol_pool_id"])
        and ops["_sku"](binding.get("product_sku")) == ops["_sku"](run.get("product_sku"))
        and ops["_channel"](binding.get("channel")) == ops["_channel"](run.get("channel"))
        and ops["_dt"](binding.get("observation_start_at"))
        == run["observation_start_at"]
        and ops["_dt"](binding.get("observation_end_at")) == run["observation_end_at"]
    )


def _verified_binding(
    db: Any,
    run: dict[str, Any],
    action_id: int,
    result: dict[str, Any],
    ops: dict[str, Any],
) -> tuple[bool, bool]:
    binding = None
    if ops["table_exists"](ops["TABLE"]) and ops["table_exists"]("vkpi_event_ledger"):
        binding = ops["_load_binding"](
            db,
            "prediction_organization_id=? AND prediction_run_id=?",
            (ops["PREDICTION_ORGANIZATION"], str(run["run_id"])),
        )
    if binding is None:
        return False, False
    if _binding_matches(binding, run, action_id, ops) and ops["_binding_proof_valid"](db, binding):
        result["verified_bound"] += 1
        from app.domains.market_brain import outreach_reply_truth

        if outreach_reply_truth.verified_receipt_for_binding(db, binding) is not None:
            result["verified_actual"] += 1
        else:
            result["missing_verified_actual"] += 1
        return True, False
    result["invalid_bridge"] += 1
    return False, True


def _classify_unverified_censor(
    db: Any,
    run: dict[str, Any],
    action_id: int,
    result: dict[str, Any],
    ops: dict[str, Any],
) -> None:
    try:
        _action, approved_at, action_error = ops["_verified_action"](
            db, action_id, lock_for_update=False,
        )
        pool_raw = db.execute(
            "SELECT platform, linked_main_kol_id FROM vkpi_kol_pool WHERE id=?",
            (int(run["kol_pool_id"]),),
        ).fetchone()
        pool = dict(pool_raw) if pool_raw is not None else {}
        kol_id = ops["_positive_int"](pool.get("linked_main_kol_id"))
        projects = (
            ops["_exact_projects"](
                db,
                kol_id=kol_id,
                product_sku=str(run["product_sku"]),
                channel=str(run["channel"]),
            )
            if kol_id > 0
            else []
        )
        server_now = ops["_server_now"](db)
        if action_error is None and approved_at is not None and server_now is not None and projects:
            candidates, flags = ops["_eligible_project_outbounds"](
                db,
                projects=projects,
                kol_id=kol_id,
                start=run["observation_start_at"],
                end=run["observation_end_at"],
                approved_at=approved_at,
                server_now=server_now,
            )
            if not candidates and not flags["in_window"]:
                result["censored_no_outbound_unverified"] += 1
    except Exception:
        ops["logger"].debug("outreach unverified censor classification failed", exc_info=True)


def _process_due_row(
    db: Any,
    original: dict[str, Any],
    result: dict[str, Any],
    ops: dict[str, Any],
) -> None:
    result["registered_due"] += 1
    run, _error = ops["_validated_run"](original)
    if run is None:
        result["invalid_due_contract"] += 1
        result["unbound"] += 1
        if len(result["invalid_run_ids"]) < 100:
            result["invalid_run_ids"].append(str(original.get("run_id") or ""))
        return
    result["valid_registered_due"] += 1
    action_id = int(run["action_inbox_id"])
    verified, bridge_invalid = _verified_binding(db, run, action_id, result, ops)
    if verified:
        return
    if not bridge_invalid:
        _classify_unverified_censor(db, run, action_id, result, ops)
    result["unbound"] += 1
    if len(result["unbound_action_ids"]) < 100:
        result["unbound_action_ids"].append(action_id)


def process_coverage_rows(
    db: Any,
    rows: Any,
    current: Any,
    result: dict[str, Any],
    ops: dict[str, Any],
) -> None:
    for raw in rows:
        original = dict(raw)
        created = ops["_dt"](original.get("created_at"))
        if created is not None and current < created + ops["timedelta"](days=ops["HORIZON_DAYS"]):
            continue
        _process_due_row(db, original, result, ops)


def finalize_coverage(result: dict[str, Any], ops: dict[str, Any]) -> dict[str, Any]:
    due = int(result["registered_due"])
    bound = int(result["verified_bound"])
    actual = int(result["verified_actual"])
    result["binding_coverage"] = round(bound / due, 4) if due else None
    result["actual_coverage"] = round(actual / due, 4) if due else None
    result["bound_actual_coverage"] = round(actual / bound, 4) if bound else None
    binding_sample_ready = bool(due > 0 and bound / due >= ops["MIN_CLAIMABLE_COVERAGE"])
    actual_sample_ready = bool(
        due > 0
        and actual >= ops["MIN_CLAIMABLE_ACTUALS"]
        and actual / due >= ops["MIN_CLAIMABLE_COVERAGE"]
    )
    result["manager_attested_sample_ready"] = bool(
        binding_sample_ready
        and actual_sample_ready
        and int(result["invalid_due_contract"]) == 0
        and int(result["invalid_bridge"]) == 0
    )
    result["binding_claimable"] = False
    result["actual_claimable"] = False
    result["claimable"] = False
    result["claim_level"] = "descriptive_only"
    return result
