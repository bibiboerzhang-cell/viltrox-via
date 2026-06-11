"""Project contract archive and Claude PDF extraction."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import audit
from app.domains.access import scope
from app.domains.costs import budget_guard
from app.domains.projects.workflow_common import _int, staff_id, utcnow
from app.platform.db.schema import ensure_vkpi_schema
from app.services.ai.analyzers.claude_contract_extract import (
    DEFAULT_CONTRACT_MODEL,
    estimate_contract_extract_cost,
    extract_contract_pdf_with_timeout,
)


logger = get_logger(__name__)
CONTRACT_DERIVE_METHOD = "claude_contract_extract_v1"
CONTRACT_BUDGET_SCOPE = "cron:vkpi_contract_extract"
CONTRACT_SINGLE_CALL_SCOPE = "single_call_contract"
CONTRACT_ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
CONTRACT_EXTRACT_JOB_TYPE = "project_contract_extract"
CONTRACT_EXTRACT_DERIVE_METHOD = "project_contract_extract_v1"


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _safe_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    for key in (
        "platforms_json",
        "deliverables_json",
        "must_include_json",
        "raw_extracted_json",
        "field_confidence_json",
        "field_confirmed_json",
        "manual_overrides_json",
        "promised_platforms_json",
        "promised_deliverables_json",
        "promised_must_include_json",
    ):
        value = item.get(key)
        if isinstance(value, str):
            try:
                item[key] = json.loads(value or "{}")
            except Exception:
                item[key] = [] if key.endswith("_json") and key not in {"raw_extracted_json", "field_confidence_json", "field_confirmed_json", "manual_overrides_json"} else {}
    return item


def _safe_name(name: str) -> tuple[str, str]:
    path = Path(name or "contract.pdf")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem[:80]).strip("-") or "contract"
    return stem, path.suffix.lower()


def _date(value: Any) -> str | None:
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


def _apply_extraction(conn: Any, contract_id: int, project_id: int, staff: dict[str, Any] | None, extraction: dict[str, Any]) -> dict[str, Any]:
    data = extraction.get("extracted") if isinstance(extraction.get("extracted"), dict) else {}
    confidence = data.get("field_confidence") if isinstance(data.get("field_confidence"), dict) else {}
    deliverables = _list(data.get("deliverables"))
    platforms = _list(data.get("platforms"))
    must_include = _list(data.get("must_include"))
    fee_amount = _float_or_none(data.get("fee_amount"))
    now = utcnow()
    conn.execute(
        """
        UPDATE vkpi_project_contracts
        SET status='extracted', extraction_status='ready',
            fee_amount=?, fee_currency=?, contract_duration=?, start_date=?, end_date=?,
            platforms_json=?::jsonb, deliverable_count=?, deliverables_json=?::jsonb,
            must_include_json=?::jsonb, usage_rights=?, exclusivity=?, buyout_rights=?,
            breach_terms=?, payment_terms=?, cancellation_terms=?, revision_terms=?,
            raw_extracted_json=?::jsonb, field_confidence_json=?::jsonb,
            promised_platforms_json=?::jsonb, promised_deliverables_json=?::jsonb,
            promised_publish_deadline=?, promised_must_include_json=?::jsonb,
            updated_at=?
        WHERE id=? AND project_id=?
        """,
        (
            fee_amount,
            str(data.get("fee_currency") or "USD"),
            str(data.get("contract_duration") or ""),
            _date(data.get("start_date")),
            _date(data.get("end_date")),
            _json(platforms),
            _int(data.get("deliverable_count")) or (len(deliverables) if deliverables else None),
            _json(deliverables),
            _json(must_include),
            str(data.get("usage_rights") or ""),
            str(data.get("exclusivity") or ""),
            str(data.get("buyout_rights") or ""),
            str(data.get("breach_terms") or ""),
            str(data.get("payment_terms") or ""),
            str(data.get("cancellation_terms") or ""),
            str(data.get("revision_terms") or ""),
            _json(data),
            _json(confidence),
            _json(platforms),
            _json(deliverables),
            data.get("promised_publish_deadline") or None,
            _json(must_include),
            now,
            int(contract_id),
            int(project_id),
        ),
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
    conn.execute(
        """
        UPDATE vkpi_project_contracts
        SET status='needs_review', extraction_status='failed',
            raw_extracted_json=?::jsonb, updated_at=?
        WHERE id=? AND project_id=?
        """,
        (_json({"error": error[:1000]}), utcnow(), int(contract_id), int(project_id)),
    )
    conn.commit()


def list_contracts(project_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(int(project_id), staff, write=False)
    rows = [
        _safe_row(row)
        for row in get_conn()
        .execute("SELECT * FROM vkpi_project_contracts WHERE project_id=? ORDER BY created_at DESC, id DESC", (int(project_id),))
        .fetchall()
    ]
    return {"project_id": int(project_id), "count": len(rows), "items": rows}


def get_contract(project_id: int, contract_id: int, *, staff: dict[str, Any] | None = None, write: bool = False) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(int(project_id), staff, write=write)
    row = get_conn().execute(
        "SELECT * FROM vkpi_project_contracts WHERE id=? AND project_id=?",
        (int(contract_id), int(project_id)),
    ).fetchone()
    if not row:
        raise LookupError("contract not found")
    return _safe_row(row)


def create_contract_from_file(
    project_id: int,
    local_path: str,
    *,
    file_name: str,
    mime_type: str = "",
    assignment_id: int | None = None,
    kol_pool_id: int | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(int(project_id), staff, write=True)
    stem, ext = _safe_name(file_name)
    if ext not in CONTRACT_ALLOWED_EXTENSIONS:
        raise ValueError("unsupported contract file type")
    path = Path(local_path)
    if not path.exists() or path.stat().st_size <= 0:
        raise ValueError("contract file missing")
    conn = get_conn()
    context = _assignment_context(conn, int(project_id), assignment_id, kol_pool_id)
    effective_kol_pool_id = _int(context.get("kol_pool_id"), _int(kol_pool_id)) or None
    r2_key = f"contracts/projects/{int(project_id)}/{uuid.uuid4().hex}-{stem}{ext}"
    from app.services.media.r2 import upload_file

    upload_file(str(path), r2_key, mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream")
    now = utcnow()
    extraction_status = "processing" if ext == ".pdf" else "skipped"
    status = "draft" if ext != ".pdf" else "needs_review"
    conn.execute(
        """
        INSERT INTO vkpi_project_contracts (
            project_id, assignment_id, kol_pool_id, file_name, r2_key, file_size_bytes, mime_type,
            uploaded_by_user_id, status, extraction_status, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            int(assignment_id) if assignment_id else None,
            effective_kol_pool_id,
            file_name,
            r2_key,
            path.stat().st_size,
            mime_type or mimetypes.guess_type(file_name)[0] or "",
            staff_id(staff) or None,
            status,
            extraction_status,
            now,
            now,
        ),
    )
    contract_id = int(conn.execute("SELECT id FROM vkpi_project_contracts WHERE r2_key=? ORDER BY id DESC LIMIT 1", (r2_key,)).fetchone()["id"])
    conn.commit()
    contract = get_contract(project_id, contract_id, staff=staff)
    if ext != ".pdf":
        return {"contract": contract, "extraction": {"status": "skipped", "reason": "doc_docx_archive_only"}}
    return {**run_contract_extraction(project_id, contract_id, local_path=str(path), context=context, staff=staff), "contract": get_contract(project_id, contract_id, staff=staff)}


def run_contract_extraction(
    project_id: int,
    contract_id: int,
    *,
    local_path: str | None = None,
    context: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = get_contract(project_id, contract_id, staff=staff, write=True)
    if not str(contract.get("file_name") or "").lower().endswith(".pdf"):
        return {"contract": contract, "extraction": {"status": "skipped", "reason": "not_pdf"}}
    conn = get_conn()
    file_size = _int(contract.get("file_size_bytes"))
    budget = _budget_preflight(file_size)
    if not budget.get("allowed"):
        _mark_failed(conn, contract_id, project_id, "budget_guard_blocked")
        raise RuntimeError("budget_guard_blocked")
    if not context:
        context = _assignment_context(conn, int(project_id), _int(contract.get("assignment_id")) or None, _int(contract.get("kol_pool_id")) or None)
    conn.execute(
        "UPDATE vkpi_project_contracts SET extraction_status='processing', status='needs_review', updated_at=? WHERE id=? AND project_id=?",
        (utcnow(), int(contract_id), int(project_id)),
    )
    conn.commit()
    result = _run_contract_extraction_core(
        conn, project_id, contract_id, contract=contract, context=context, staff=staff, local_path=local_path
    )
    return {"contract": get_contract(project_id, contract_id, staff=staff), "budget": budget, **result}


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
    """Heavy extraction body (R2 download → Claude subprocess → apply), shared by the sync
    path (run_contract_extraction) and the apify worker handler.

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


def enqueue_contract_extract_job(
    project_id: int,
    contract_id: int,
    *,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue one async contract-extraction job. Does NOT run the LLM here.

    - Non-PDF returns skipped (no job enqueued).
    - Dedups against an active (queued/running) job for the same contract.
    - Budget preflight stays synchronous so callers get an early failure (mapped to 400).
    - payload deliberately omits search_session_id so the KOL session sync no-ops.
    The actual extraction is run later by the apify worker via _run_contract_extraction_core.
    """
    contract = get_contract(project_id, contract_id, staff=staff, write=True)
    if not str(contract.get("file_name") or "").lower().endswith(".pdf"):
        return {
            "status": "skipped",
            "contract": contract,
            "extraction": {"status": "skipped", "reason": "not_pdf"},
        }
    conn = get_conn()
    active = conn.execute(
        """
        SELECT id, job_type, status, payload, created_at, updated_at
        FROM apify_jobs
        WHERE job_type=?
          AND status IN ('queued', 'running')
          AND payload->>'target_type'='project_contract'
          AND payload->>'target_id'=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (CONTRACT_EXTRACT_JOB_TYPE, str(int(contract_id))),
    ).fetchone()
    if active:
        item = dict(active)
        return {
            "status": "already_queued" if item.get("status") == "queued" else "already_running",
            "contract": contract,
            "job": item,
            "diagnostics": {"provider_calls": False, "llm_calls": False, "worker_touched": False, "write_viltrox_fit_score": False},
        }
    budget = _budget_preflight(_int(contract.get("file_size_bytes")))
    if not budget.get("allowed"):
        _mark_failed(conn, contract_id, project_id, "budget_guard_blocked")
        raise RuntimeError("budget_guard_blocked")
    staff = staff or {}
    payload = {
        "target_type": "project_contract",
        "target_id": str(int(contract_id)),
        "project_id": int(project_id),
        "assignment_id": _int(contract.get("assignment_id")) or None,
        "kol_pool_id": _int(contract.get("kol_pool_id")) or None,
        "r2_key": contract.get("r2_key"),
        "file_name": contract.get("file_name"),
        "derive_method": CONTRACT_EXTRACT_DERIVE_METHOD,
        "query_text": contract.get("file_name") or f"contract:{int(contract_id)}",
        "triggered_by_user_id": _triggered_by_user_id(staff),
        "staff_id": _ledger_staff_id(staff),
    }
    conn.execute(
        "UPDATE vkpi_project_contracts SET extraction_status='processing', status='needs_review', updated_at=? WHERE id=? AND project_id=?",
        (utcnow(), int(contract_id), int(project_id)),
    )
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id, job_type, status, payload, created_at, updated_at
        """,
        (CONTRACT_EXTRACT_JOB_TYPE, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    return {
        "status": "queued",
        "contract": get_contract(project_id, contract_id, staff=staff),
        "job": dict(job) if job else None,
        "budget": budget,
        "diagnostics": {"provider_calls": False, "llm_calls": False, "worker_touched": False, "write_viltrox_fit_score": False},
    }


def update_contract(project_id: int, contract_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    get_contract(project_id, contract_id, staff=staff, write=True)
    allowed = {
        "fee_amount": body.get("fee_amount"),
        "fee_currency": body.get("fee_currency"),
        "contract_duration": body.get("contract_duration"),
        "start_date": _date(body.get("start_date")),
        "end_date": _date(body.get("end_date")),
        "platforms_json": _json(body.get("platforms") or body.get("platforms_json") or []),
        "deliverable_count": body.get("deliverable_count"),
        "deliverables_json": _json(body.get("deliverables") or body.get("deliverables_json") or []),
        "must_include_json": _json(body.get("must_include") or body.get("must_include_json") or []),
        "usage_rights": body.get("usage_rights"),
        "exclusivity": body.get("exclusivity"),
        "buyout_rights": body.get("buyout_rights"),
        "breach_terms": body.get("breach_terms"),
        "payment_terms": body.get("payment_terms"),
        "cancellation_terms": body.get("cancellation_terms"),
        "revision_terms": body.get("revision_terms"),
        "manual_overrides_json": _json(body.get("manual_overrides") or body),
    }
    updates: list[str] = []
    params: list[Any] = []
    for key, value in allowed.items():
        if key not in body and key.replace("_json", "") not in body:
            continue
        updates.append(f"{key}=?{'::jsonb' if key.endswith('_json') else ''}")
        params.append(value)
    if not updates:
        return get_contract(project_id, contract_id, staff=staff)
    updates.append("updated_at=?")
    params.append(utcnow())
    params.extend([int(contract_id), int(project_id)])
    get_conn().execute(f"UPDATE vkpi_project_contracts SET {', '.join(updates)} WHERE id=? AND project_id=?", tuple(params))
    get_conn().commit()
    return get_contract(project_id, contract_id, staff=staff)


def confirm_contract(project_id: int, contract_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    if body:
        update_contract(project_id, contract_id, body, staff=staff)
    conn = get_conn()
    now = utcnow()
    conn.execute(
        """
        UPDATE vkpi_project_contracts
        SET status='confirmed', field_confirmed_json=?::jsonb,
            confirmed_by_user_id=?, confirmed_at=?, updated_at=?
        WHERE id=? AND project_id=?
        """,
        (_json(body.get("field_confirmed") or {"all": True}), staff_id(staff) or None, now, now, int(contract_id), int(project_id)),
    )
    conn.commit()
    return get_contract(project_id, contract_id, staff=staff)


def contract_download_url(project_id: int, contract_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = get_contract(project_id, contract_id, staff=staff)
    from app.services.media.r2 import get_presigned_url

    return {"contract_id": int(contract_id), "r2_key": contract.get("r2_key"), "url": get_presigned_url(str(contract.get("r2_key") or ""))}


def delete_contract(project_id: int, contract_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = get_contract(project_id, contract_id, staff=staff, write=True)
    conn = get_conn()
    conn.execute(
        "DELETE FROM vkpi_analysis_cache WHERE target_type='contract' AND target_id=? AND derive_method=?",
        (str(int(contract_id)), CONTRACT_DERIVE_METHOD),
    )
    conn.execute(
        "DELETE FROM vkpi_project_contracts WHERE id=? AND project_id=?",
        (int(contract_id), int(project_id)),
    )
    conn.commit()
    # R2 对象删除放在 commit 之后:对象存储失败不应回滚已删的归档行,残留对象可由后续清理任务回收
    r2_key = str(contract.get("r2_key") or "")
    r2_deleted = False
    if r2_key:
        from app.services.media.r2 import delete_object

        try:
            r2_deleted = delete_object(r2_key)
        except Exception:
            logger.warning("contract.delete.r2_cleanup_failed contract_id=%s", contract_id)
    audit.log_business_event(
        staff_id=staff_id(staff),
        action_type="contract_deleted",
        target_type="project_contract",
        target_id=contract_id,
        detail=f"删除合同归档 {contract.get('file_name') or contract_id}",
        metadata={
            "project_id": int(project_id),
            "contract_id": int(contract_id),
            "file_name": contract.get("file_name"),
            "r2_key": r2_key,
            "r2_deleted": r2_deleted,
            "status_before": contract.get("status"),
        },
    )
    return {"deleted": True, "contract_id": int(contract_id), "r2_deleted": r2_deleted}
