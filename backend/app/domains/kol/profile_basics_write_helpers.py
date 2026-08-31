"""Transaction helpers for the KOL profile-basics write facade.

The facade supplies every database primitive at call time.  That keeps its
existing monkeypatch and transaction seams intact while moving branch-heavy
planning code out of the public module.
"""
from __future__ import annotations

from typing import Any, Callable, Collection


def resolve_profile_identity(
    db: Any,
    kol_pool_id: int | None,
    requested_identity: dict[str, Any],
    *,
    dry_run: bool,
    lock_identity: Callable[[Any, dict[str, Any]], None],
    canonical_existing_id: Callable[[Any, dict[str, Any]], int | None],
    rollback: Callable[[Any], None],
) -> tuple[int | None, bool, int | None]:
    identity_write_locked = bool(not kol_pool_id and not dry_run)
    canonical_match_id: int | None = None
    if not kol_pool_id:
        try:
            if not dry_run:
                lock_identity(db, requested_identity)
            canonical_match_id = canonical_existing_id(db, requested_identity)
        except Exception:
            if not dry_run:
                rollback(db)
            raise
        if canonical_match_id:
            kol_pool_id = canonical_match_id
    return kol_pool_id, identity_write_locked, canonical_match_id


def prepare_profile_write(
    db: Any,
    kol_pool_id: int | None,
    profile_data: dict[str, Any],
    columns: set[str],
    *,
    now: str,
    dry_run: bool,
    identity_write_locked: bool,
    canonical_match_id: int | None,
    method: str,
    whitelist: Collection[str],
    update_fields: Collection[str],
    insert_fields: Collection[str],
    load_pool_row: Callable[[Any, int | None], dict[str, Any] | None],
    normalise_profile_data: Callable[..., dict[str, Any]],
    should_write: Callable[..., bool],
    rollback: Callable[[Any], None],
    token_hex: Callable[[int], str],
) -> tuple[
    dict[str, Any] | None, str, dict[str, Any], list[str], list[str], dict[str, Any]
]:
    try:
        row = load_pool_row(db, kol_pool_id) if kol_pool_id else None
    except Exception:
        if identity_write_locked:
            rollback(db)
        raise
    operation = "update" if row else "insert"
    normalized_input = dict(profile_data)
    if canonical_match_id and row:
        normalized_input.pop("platform", None)
        normalized_input.pop("handle", None)
    normalized = normalise_profile_data(
        normalized_input, existing=row, now=now, method=method
    )
    ignored_fields = sorted(set(profile_data) - set(whitelist))
    if operation == "insert":
        if not normalized.get("platform") or not normalized.get("handle"):
            if not dry_run:
                rollback(db)
            raise ValueError("platform and handle are required for new KOL profile basics")
        normalized.setdefault("pool_uid", f"url-profile-{token_hex(8)}")
    write_fields = update_fields if operation == "update" else insert_fields
    allowed_fields = [field for field in write_fields if field in columns]
    missing_columns = sorted(set(write_fields) - set(allowed_fields) - {"pool_uid"})
    planned_values = {
        field: normalized[field]
        for field in allowed_fields
        if field in normalized and should_write(field, normalized[field], operation=operation)
    }
    if operation == "insert" and "pool_uid" in columns:
        planned_values["pool_uid"] = normalized["pool_uid"]
    return row, operation, normalized, ignored_fields, missing_columns, planned_values


def profile_prewrite_response(
    db: Any,
    *,
    dry_run: bool,
    identity_write_locked: bool,
    brand_gate: dict[str, Any] | None,
    operation: str,
    kol_pool_id: int | None,
    row: dict[str, Any] | None,
    normalized: dict[str, Any],
    planned_values: dict[str, Any],
    ignored_fields: list[str],
    missing_columns: list[str],
    before_scores: dict[int, dict[str, Any]],
    method: str,
    rollback: Callable[[Any], None],
    logger: Any,
) -> dict[str, Any] | None:
    if brand_gate and not dry_run:
        if identity_write_locked:
            rollback(db)
        logger.info(
            "kol_pool brand-official enrollment skipped platform=%r handle=%r brand=%s field=%s",
            normalized.get("platform"),
            str(normalized.get("handle") or "")[:60],
            brand_gate.get("brand"),
            brand_gate.get("field"),
        )
        return {
            "ok": True,
            "dry_run": False,
            "skipped": True,
            "skip_reason": brand_gate.get("reason"),
            "brand_official": brand_gate,
            "operation": operation,
            "kol_pool_id": None,
            "fields_written": [],
            "ignored_fields": ignored_fields,
            "missing_columns": missing_columns,
            "score_before": before_scores,
            "score_after": before_scores,
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
            "method": method,
            "matched_existing": False,
        }
    if not dry_run:
        return None
    return {
        "ok": True,
        "dry_run": True,
        "skipped": bool(brand_gate),
        "skip_reason": brand_gate.get("reason") if brand_gate else "",
        "operation": operation,
        "kol_pool_id": int(kol_pool_id) if row else None,
        "fields_to_write": sorted(planned_values),
        "planned_values": planned_values,
        "ignored_fields": ignored_fields,
        "missing_columns": missing_columns,
        "score_before": before_scores,
        "score_after": before_scores,
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
        "method": method,
        "matched_existing": bool(row),
    }


def execute_profile_values(
    db: Any,
    *,
    operation: str,
    kol_pool_id: int | None,
    planned_values: dict[str, Any],
    before_scores: dict[int, dict[str, Any]],
    row: dict[str, Any] | None,
    execute_update: Callable[[Any, int, dict[str, Any]], None],
    execute_insert: Callable[[Any, dict[str, Any]], int],
    score_snapshot: Callable[[Any, list[int]], dict[int, dict[str, Any]]],
    changed_score_ids: Callable[
        [dict[int, dict[str, Any]], dict[int, dict[str, Any]]], list[int]
    ],
    preexisting_pool_id: Callable[[Any, Any, Any], int | None],
    new_row_has_score: Callable[[dict[str, Any]], bool],
) -> tuple[int, dict[int, dict[str, Any]], list[int], bool]:
    changed_ids: list[int] = []
    matched_existing = bool(row)
    if operation == "update":
        target_id = int(kol_pool_id or 0)
        if not planned_values:
            after_scores = before_scores
        else:
            execute_update(db, target_id, planned_values)
            after_scores = score_snapshot(db, [target_id])
            changed_ids = changed_score_ids(before_scores, after_scores)
        return target_id, after_scores, changed_ids, matched_existing
    pre_id = preexisting_pool_id(
        db, planned_values.get("platform"), planned_values.get("handle")
    )
    matched_existing = pre_id is not None
    insert_before_scores = score_snapshot(db, [pre_id]) if pre_id else {}
    target_id = execute_insert(db, planned_values)
    after_scores = score_snapshot(db, [target_id])
    if pre_id is not None and int(pre_id) == int(target_id):
        changed_ids = changed_score_ids(insert_before_scores, after_scores)
    elif new_row_has_score(after_scores.get(target_id, {})):
        changed_ids = [target_id]
    return target_id, after_scores, changed_ids, matched_existing
