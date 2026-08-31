"""Contract extraction internals (moved verbatim from contracts.py, batch refactor).

Behavior-preserving extraction: staff/budget/context resolution, cost ledger writes,
signed-version link-key preservation, and the apply/mark-failed/heavy-core extraction body.
Re-exported by contracts.py so existing call sites and imports stay unchanged.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.domains.projects import contracts_extract_normalization

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import audit
from app.domains.costs import budget_guard
from app.domains.projects.workflow_common import _int, staff_id, utcnow
from app.services.ai.analyzers.claude_contract_extract import (
    DEFAULT_CONTRACT_MODEL,
    estimate_contract_extract_cost,
    extract_contract_pdf_with_timeout,
)


logger = get_logger(__name__)
CONTRACT_DERIVE_METHOD = "claude_contract_extract_v1"
CONTRACT_BUDGET_SCOPE = "cron:vkpi_contract_extract"
CONTRACT_SINGLE_CALL_SCOPE = "single_call_contract"
# 签署版关联键(批E,2026-06-12):存于 raw_extracted_json,提取/失败回写时必须保留。
CONTRACT_LINK_KEYS = ("signed_version_of", "superseded_by")

# A contract extraction is useful only when at least one of these core facts is
# non-empty, correctly typed, and backed by both confidence and evidence.
CONTRACT_MINIMUM_VALID_FIELDS = frozenset(
    {
        "fee_amount",
        "contract_duration",
        "start_date",
        "end_date",
        "platforms",
        "deliverable_count",
        "deliverables",
        "must_include",
        "usage_rights",
        "exclusivity",
        "buyout_rights",
        "breach_terms",
        "payment_terms",
        "cancellation_terms",
        "revision_terms",
        "promised_publish_deadline",
    }
)
CONTRACT_BUSINESS_FIELDS = (
    "fee_amount",
    "fee_currency",
    "contract_duration",
    "start_date",
    "end_date",
    "platforms",
    "deliverable_count",
    "deliverables",
    "must_include",
    "usage_rights",
    "exclusivity",
    "buyout_rights",
    "breach_terms",
    "payment_terms",
    "cancellation_terms",
    "revision_terms",
    "promised_publish_deadline",
)
CONTRACT_TEXT_FIELDS = frozenset(
    {
        "fee_currency",
        "contract_duration",
        "usage_rights",
        "exclusivity",
        "buyout_rights",
        "breach_terms",
        "payment_terms",
        "cancellation_terms",
        "revision_terms",
    }
)
CONTRACT_STORAGE_COLUMNS = {
    "fee_amount": ("fee_amount",),
    "fee_currency": ("fee_currency",),
    "contract_duration": ("contract_duration",),
    "start_date": ("start_date",),
    "end_date": ("end_date",),
    "platforms": ("platforms_json", "promised_platforms_json"),
    "deliverable_count": ("deliverable_count",),
    "deliverables": ("deliverables_json", "promised_deliverables_json"),
    "must_include": ("must_include_json", "promised_must_include_json"),
    "usage_rights": ("usage_rights",),
    "exclusivity": ("exclusivity",),
    "buyout_rights": ("buyout_rights",),
    "breach_terms": ("breach_terms",),
    "payment_terms": ("payment_terms",),
    "cancellation_terms": ("cancellation_terms",),
    "revision_terms": ("revision_terms",),
    "promised_publish_deadline": ("promised_publish_deadline",),
}
# One document quote can legitimately support closely coupled fields.
CONTRACT_EVIDENCE_ALIASES = {
    "fee_currency": ("fee_currency", "fee_amount"),
    "platforms": ("platforms", "deliverables"),
    "deliverable_count": ("deliverable_count", "deliverables"),
    "must_include": ("must_include", "deliverables"),
    "promised_publish_deadline": ("promised_publish_deadline", "deliverables"),
}


class ContractExtractionValidationError(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _date(value: Any) -> str | None:
    import re

    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match.group(0) if match else None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "{}")
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_meaningful(item) for item in value)
    if isinstance(value, dict):
        return any(_meaningful(item) for item in value.values())
    return True


def _valid_evidence(value: Any) -> bool:
    return isinstance(value, (str, list, dict)) and _meaningful(value)


def _normalized_business_field(field: str, value: Any) -> Any:
    return contracts_extract_normalization.normalized_business_field(
        field,
        value,
        text_fields=CONTRACT_TEXT_FIELDS,
        math_module=math,
        date_type=date,
        datetime_type=datetime,
        date_parser=_date,
    )


def _validated_extraction_data(extraction: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, float]]:
    if not isinstance(extraction, dict) or extraction.get("ok") is False:
        raise ContractExtractionValidationError("contract_extraction_invalid_envelope")
    data = extraction.get("extracted")
    if not isinstance(data, dict) or not data:
        raise ContractExtractionValidationError("contract_extraction_empty_object")

    invalid_fields: list[str] = []
    normalized: dict[str, Any] = {}
    for field in CONTRACT_BUSINESS_FIELDS:
        if field not in data:
            continue
        try:
            normalized[field] = _normalized_business_field(field, data[field])
        except (TypeError, ValueError):
            invalid_fields.append(field)

    if "summary" in data and not isinstance(data["summary"], str):
        invalid_fields.append("summary")
    for field in ("risk_flags", "missing_or_unclear_fields"):
        if field in data and not isinstance(data[field], list):
            invalid_fields.append(field)

    confidence = data.get("field_confidence")
    evidence = data.get("evidence")
    if not isinstance(confidence, dict):
        invalid_fields.append("field_confidence")
        confidence = {}
    if not isinstance(evidence, dict):
        invalid_fields.append("evidence")
        evidence = {}

    for field, value in confidence.items():
        if not isinstance(field, str) or isinstance(value, bool) or not isinstance(value, (int, float)):
            invalid_fields.append(f"field_confidence.{field}")
            continue
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0 or numeric > 1:
            invalid_fields.append(f"field_confidence.{field}")
    for field, value in evidence.items():
        if not isinstance(field, str) or (value not in (None, "", [], {}) and not isinstance(value, (str, list, dict))):
            invalid_fields.append(f"evidence.{field}")

    if invalid_fields:
        names = ",".join(sorted(set(invalid_fields)))
        raise ContractExtractionValidationError(f"contract_extraction_invalid_fields:{names}")

    supported: dict[str, Any] = {}
    supported_confidence: dict[str, float] = {}
    for field, value in normalized.items():
        if not _meaningful(value):
            continue
        confidence_value = confidence.get(field)
        if isinstance(confidence_value, bool) or not isinstance(confidence_value, (int, float)):
            continue
        numeric_confidence = float(confidence_value)
        if numeric_confidence <= 0:
            continue
        evidence_keys = CONTRACT_EVIDENCE_ALIASES.get(field, (field,))
        if not any(_valid_evidence(evidence.get(key)) for key in evidence_keys):
            continue
        supported[field] = value
        supported_confidence[field] = numeric_confidence

    if not CONTRACT_MINIMUM_VALID_FIELDS.intersection(supported):
        raise ContractExtractionValidationError("contract_extraction_no_evidence_backed_business_fields")
    return data, supported, supported_confidence


def _protected_business_fields(current: dict[str, Any]) -> set[str]:
    manual = _json_object(current.get("manual_overrides_json"))
    confirmed = _json_object(current.get("field_confirmed_json"))
    if manual.get("all") or confirmed.get("all"):
        return set(CONTRACT_BUSINESS_FIELDS)

    protected: set[str] = set()
    for field, columns in CONTRACT_STORAGE_COLUMNS.items():
        aliases = {field, *columns, *(column.removesuffix("_json") for column in columns)}
        if any(alias in manual for alias in aliases) or any(confirmed.get(alias) for alias in aliases):
            protected.add(field)
    return protected


def _append_field_updates(updates: list[str], params: list[Any], field: str, value: Any) -> None:
    columns = CONTRACT_STORAGE_COLUMNS[field]
    if field in {"platforms", "deliverables", "must_include"}:
        for column in columns:
            updates.append(f"{column}=CASE WHEN status='confirmed' THEN {column} ELSE ?::jsonb END")
            params.append(_json(value))
        return
    for column in columns:
        updates.append(f"{column}=CASE WHEN status='confirmed' THEN {column} ELSE ? END")
        params.append(value)


def _assignment_context(conn: Any, project_id: int, assignment_id: int | None, kol_pool_id: int | None) -> dict[str, Any]:
    context: dict[str, Any] = {"project_id": int(project_id)}
    if assignment_id:
        row = conn.execute(
            """
            SELECT a.id AS assignment_id, a.kol_pool_id, p.project_name, kp.display_name, kp.handle
            FROM vkpi_project_kol_assignments a
            JOIN vkpi_projects p ON p.id=a.project_id
            LEFT JOIN vkpi_kol_pool kp ON kp.id=a.kol_pool_id
            WHERE a.id=? AND a.project_id=?
            """,
            (int(assignment_id), int(project_id)),
        ).fetchone()
        if not row:
            raise LookupError("assignment not found for project")
        context.update(dict(row))
    elif kol_pool_id:
        row = conn.execute(
            "SELECT id AS kol_pool_id, display_name, handle FROM vkpi_kol_pool WHERE id=?",
            (int(kol_pool_id),),
        ).fetchone()
        if row:
            context.update(dict(row))
    context["kol_name"] = context.get("display_name") or context.get("handle") or ""
    return context


def _budget_preflight(file_size_bytes: int) -> dict[str, Any]:
    estimated = estimate_contract_extract_cost(int(file_size_bytes or 0))
    scope_plan = budget_guard.check_budget_scopes(
        ["monthly_total", "provider:claude", CONTRACT_BUDGET_SCOPE],
        estimated,
        require_configured=True,
    )
    single = budget_guard.get_budget_status(CONTRACT_SINGLE_CALL_SCOPE, estimated_cost=0)
    single_cap = float(single.get("cap_usd") or 0)
    single_allowed = bool(single.get("configured")) and (single_cap <= 0 or estimated <= single_cap)
    return {
        "allowed": bool(scope_plan.get("allowed")) and single_allowed,
        "estimated_cost_usd": estimated,
        "checks": scope_plan.get("checks") or [],
        "single_call_contract": {**single, "allowed": single_allowed, "estimated_cost_usd": estimated},
    }


def _staff_id_by_user_id(user_id: Any) -> int | None:
    candidate = _int(user_id)
    if not candidate:
        return None
    row = get_conn().execute(
        "SELECT id FROM staff WHERE user_id=? ORDER BY active DESC, id DESC LIMIT 1",
        (int(candidate),),
    ).fetchone()
    return int(row["id"]) if row else None


def _valid_staff_id(value: Any) -> int | None:
    candidate = _int(value)
    if not candidate:
        return None
    row = get_conn().execute("SELECT id FROM staff WHERE id=? LIMIT 1", (int(candidate),)).fetchone()
    return int(row["id"]) if row else None


def _ledger_staff_id(staff: dict[str, Any] | None) -> int | None:
    if not isinstance(staff, dict):
        return None
    return (
        _valid_staff_id(staff.get("staff_id"))
        or _staff_id_by_user_id(staff.get("user_id"))
        or _staff_id_by_user_id(staff.get("id"))
        or _valid_staff_id(staff.get("id"))
    )


def _triggered_by_user_id(staff: dict[str, Any] | None) -> int | None:
    if not isinstance(staff, dict):
        return None
    user_id = _int(staff.get("user_id"))
    if user_id:
        return user_id
    candidate = _int(staff.get("id") or staff.get("staff_id"))
    if not candidate:
        return None
    conn = get_conn()
    if conn.execute("SELECT id FROM users WHERE id=? LIMIT 1", (int(candidate),)).fetchone():
        return int(candidate)
    row = conn.execute("SELECT user_id FROM staff WHERE id=? LIMIT 1", (int(candidate),)).fetchone()
    return _int(row["user_id"]) if row else None


def _record_contract_cost(contract_id: int, project_id: int, staff: dict[str, Any] | None, extraction: dict[str, Any]) -> dict[str, Any]:
    usage = extraction.get("usage_metadata") if isinstance(extraction.get("usage_metadata"), dict) else {}
    return budget_guard.record_cost(
        scope=CONTRACT_BUDGET_SCOPE,
        cron_task="vkpi_contract_extract",
        ai_provider="anthropic",
        model_name=str(extraction.get("model") or DEFAULT_CONTRACT_MODEL),
        cost_usd=float(extraction.get("cost_usd") or 0),
        tokens_in=int(usage.get("input_tokens") or 0),
        tokens_out=int(usage.get("output_tokens") or 0),
        staff_id=_ledger_staff_id(staff),
        metadata={
            "project_id": int(project_id),
            "contract_id": int(contract_id),
            "derive_method": CONTRACT_DERIVE_METHOD,
            "triggered_by_user_id": _triggered_by_user_id(staff),
        },
        triggered_by=None,
        extra_scopes=["monthly_total", "provider:claude"],
    )


def _existing_link_keys(conn: Any, contract_id: int, project_id: int) -> dict[str, Any]:
    """读 raw_extracted_json 里的签署版关联键(批E):提取/失败整体覆盖该列前 merge 回去,防丢链。"""
    row = conn.execute(
        "SELECT raw_extracted_json FROM vkpi_project_contracts WHERE id=? AND project_id=?",
        (int(contract_id), int(project_id)),
    ).fetchone()
    if not row:
        return {}
    raw = dict(row).get("raw_extracted_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            return {}
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in CONTRACT_LINK_KEYS if raw.get(key) is not None}


def _apply_extraction(conn: Any, contract_id: int, project_id: int, staff: dict[str, Any] | None, extraction: dict[str, Any]) -> dict[str, Any]:
    try:
        data, supported_fields, confidence = _validated_extraction_data(extraction)
    except ContractExtractionValidationError as exc:
        _mark_failed(conn, contract_id, project_id, str(exc))
        raise
    # 签署版关联键保留(批E):本函数整体覆盖 raw_extracted_json,merge 回关联键。
    data = {**data, **_existing_link_keys(conn, int(contract_id), int(project_id))}
    now = utcnow()
    # 批B #3(2026-06-12):异步提取不得覆盖人工确认——status='confirmed' 时只存提取结果
    # (raw/confidence 提取字段),不降级状态、不覆盖人工确认过的业务字段,留 audit。
    current = conn.execute(
        """
        SELECT status, manual_overrides_json, field_confirmed_json
        FROM vkpi_project_contracts WHERE id=? AND project_id=?
        """,
        (int(contract_id), int(project_id)),
    ).fetchone()
    current_data = dict(current) if current else {}
    is_confirmed = str(current_data.get("status") or "") == "confirmed"
    if is_confirmed:
        conn.execute(
            """
            UPDATE vkpi_project_contracts
            SET extraction_status='ready', raw_extracted_json=?::jsonb, field_confidence_json=?::jsonb, updated_at=?
            WHERE id=? AND project_id=?
            """,
            (_json(data), _json(confidence), now, int(contract_id), int(project_id)),
        )
        audit.log_business_event(
            staff_id=staff_id(staff) or None,
            action_type="contract_extraction_preserved_confirmed",
            target_type="project_contract",
            target_id=int(contract_id),
            detail="提取完成但合同已人工确认:状态保持 confirmed,仅存提取结果,不覆盖人工字段",
            metadata={"project_id": int(project_id), "contract_id": int(contract_id), "status": "confirmed"},
        )
    else:
        protected_fields = _protected_business_fields(current_data)
        updates = [
            "status=CASE WHEN status='confirmed' THEN status ELSE 'extracted' END",
            "extraction_status='ready'",
        ]
        params: list[Any] = []
        for field in CONTRACT_BUSINESS_FIELDS:
            if field in supported_fields and field not in protected_fields:
                _append_field_updates(updates, params, field, supported_fields[field])
        updates.extend(
            [
                "raw_extracted_json=?::jsonb",
                "field_confidence_json=?::jsonb",
                "updated_at=?",
            ]
        )
        params.extend((_json(data), _json(confidence), now, int(contract_id), int(project_id)))
        conn.execute(
            f"""
            UPDATE vkpi_project_contracts
            SET {", ".join(updates)}
            WHERE id=? AND project_id=?
            """,
            tuple(params),
        )
    cost = _record_contract_cost(contract_id, project_id, staff, extraction)
    conn.execute(
        """
        INSERT INTO vkpi_analysis_cache (
            target_type, target_id, model, derive_method, result, cost, status,
            triggered_by_user_id, created_at, updated_at
        ) VALUES ('contract', ?, ?, ?, ?::jsonb, ?, 'ready', ?, ?, ?)
        ON CONFLICT (target_type, target_id, derive_method)
        DO UPDATE SET model=EXCLUDED.model, result=EXCLUDED.result, cost=EXCLUDED.cost,
            status='ready', triggered_by_user_id=EXCLUDED.triggered_by_user_id, updated_at=EXCLUDED.updated_at
        """,
        (
            str(contract_id),
            str(extraction.get("model") or DEFAULT_CONTRACT_MODEL),
            CONTRACT_DERIVE_METHOD,
            _json({"schema_version": CONTRACT_DERIVE_METHOD, **extraction}),
            float(extraction.get("cost_usd") or 0),
            staff_id(staff) or None,
            now,
            now,
        ),
    )
    conn.commit()
    return {"extraction": extraction, "cost_ledger": cost}


def _mark_failed(conn: Any, contract_id: int, project_id: int, error: str) -> None:
    # 已人工确认的合同不因提取失败降级状态(批B #3),仅记 extraction_status='failed'
    # 签署版关联键保留(批E):error 覆盖 raw_extracted_json 时 merge 回关联键。
    error_payload = {"error": error[:1000], **_existing_link_keys(conn, int(contract_id), int(project_id))}
    conn.execute(
        """
        UPDATE vkpi_project_contracts
        SET status=CASE WHEN status='confirmed' THEN status ELSE 'needs_review' END,
            extraction_status='failed',
            raw_extracted_json=?::jsonb, updated_at=?
        WHERE id=? AND project_id=?
        """,
        (_json(error_payload), utcnow(), int(contract_id), int(project_id)),
    )
    conn.commit()


def _run_contract_extraction_core(
    conn: Any,
    project_id: int,
    contract_id: int,
    *,
    contract: dict[str, Any],
    context: dict[str, Any] | None,
    staff: dict[str, Any] | None,
    local_path: str | None = None,
) -> dict[str, Any]:
    """Heavy extraction body (R2 download → Claude subprocess → apply), called by the
    apify worker handler (run_contract_extraction_for_job). 同步路径已删(批B #9)。

    Caller guarantees the contract is a PDF and already marked extraction_status='processing'.
    Keeps the 120s subprocess timeout; on failure marks the contract failed and re-raises.
    """
    temp_path = local_path
    with tempfile.TemporaryDirectory(prefix="vkpi-contract-extract-") as tmpdir:
        if not temp_path:
            temp_path = str(Path(tmpdir) / Path(str(contract.get("file_name") or "contract.pdf")).name)
            from app.services.media.r2 import download_file

            download_file(str(contract.get("r2_key") or ""), temp_path)
        try:
            extraction = extract_contract_pdf_with_timeout(
                temp_path,
                context=context,
                model_name=os.getenv("VKPI_CONTRACT_CLAUDE_MODEL", DEFAULT_CONTRACT_MODEL),
                timeout_sec=int(os.getenv("VKPI_CONTRACT_EXTRACT_TIMEOUT_SEC", "120")),
            )
        except Exception as exc:
            _mark_failed(conn, contract_id, project_id, str(exc))
            raise
        return _apply_extraction(conn, contract_id, project_id, staff, extraction)
