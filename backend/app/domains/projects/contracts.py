"""Project contract archive and Claude PDF extraction."""
from __future__ import annotations

import json
import mimetypes
import re
import uuid
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import audit
from app.domains.access import scope
from app.domains.projects.workflow_common import _int, staff_id, utcnow
from app.platform.db.schema import ensure_vkpi_schema


logger = get_logger(__name__)
CONTRACT_ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
CONTRACT_EXTRACT_JOB_TYPE = "project_contract_extract"
CONTRACT_EXTRACT_DERIVE_METHOD = "project_contract_extract_v1"

# Behavior-preserving move (batch refactor): extraction internals + their constants live
# in contracts_extract.py; re-exported here (incl. private names) so call sites stay unchanged.
from app.domains.projects.contracts_extract import (  # noqa: E402
    CONTRACT_BUDGET_SCOPE,
    CONTRACT_DERIVE_METHOD,
    CONTRACT_LINK_KEYS,
    CONTRACT_SINGLE_CALL_SCOPE,
    _apply_extraction,
    _assignment_context,
    _budget_preflight,
    _date,
    _existing_link_keys,
    _float_or_none,
    _json,
    _ledger_staff_id,
    _list,
    _mark_failed,
    _record_contract_cost,
    _run_contract_extraction_core,
    _staff_id_by_user_id,
    _triggered_by_user_id,
    _valid_staff_id,
)


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
                # 残账修复(批E,2026-06-12):不再静默吞 JSON 错——带行 id/键名 warning,坏数据可追。
                logger.warning("contract.safe_row.json_malformed id=%s key=%s", item.get("id"), key)
                item[key] = [] if key.endswith("_json") and key not in {"raw_extracted_json", "field_confidence_json", "field_confirmed_json", "manual_overrides_json"} else {}
    # 签署版关联(批E):raw_extracted_json 里的关联键提到顶层,list/get 直接带出。
    raw = item.get("raw_extracted_json")
    if isinstance(raw, dict):
        for link_key in CONTRACT_LINK_KEYS:
            if raw.get(link_key) is not None:
                item[link_key] = raw.get(link_key)
    return item


def _safe_name(name: str) -> tuple[str, str]:
    path = Path(name or "contract.pdf")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem[:80]).strip("-") or "contract"
    return stem, path.suffix.lower()


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
    related_contract_id: int | None = None,
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
    # 签署版关联(批E):related_contract_id 指向同项目草稿行,先校验再建链。
    related_id = _int(related_contract_id) or None
    if related_id:
        related_row = conn.execute(
            "SELECT id FROM vkpi_project_contracts WHERE id=? AND project_id=?",
            (int(related_id), int(project_id)),
        ).fetchone()
        if not related_row:
            raise ValueError("related contract not found in this project")
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
    if related_id:
        # 签署版关联(批E):新行并入 signed_version_of,草稿行并入 superseded_by(同列 raw_extracted_json)。
        conn.execute(
            "UPDATE vkpi_project_contracts SET raw_extracted_json=COALESCE(raw_extracted_json, '{}'::jsonb) || ?::jsonb, updated_at=? WHERE id=? AND project_id=?",
            (_json({"signed_version_of": int(related_id)}), now, int(contract_id), int(project_id)),
        )
        conn.execute(
            "UPDATE vkpi_project_contracts SET raw_extracted_json=COALESCE(raw_extracted_json, '{}'::jsonb) || ?::jsonb, updated_at=? WHERE id=? AND project_id=?",
            (_json({"superseded_by": int(contract_id)}), now, int(related_id), int(project_id)),
        )
    conn.commit()
    contract = get_contract(project_id, contract_id, staff=staff)
    if ext != ".pdf":
        return {"contract": contract, "extraction": {"status": "skipped", "reason": "doc_docx_archive_only"}}
    # PDF 提取改异步:入队由 apify worker 执行(F2 裁决:无同步 fallback)。
    # worker 会从 R2 重新下载,upload 时的 local_path 不再透传。
    return enqueue_contract_extract_job(project_id, contract_id, staff=staff)


# 批B #9(2026-06-12):同步路径 run_contract_extraction 已删——全仓无调用方
# (F2 裁决后提取统一走 enqueue_contract_extract_job → apify worker → run_contract_extraction_for_job)。


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
    # 已确认合同重提取:保持 confirmed,不降级为 needs_review(批B #3)
    conn.execute(
        """
        UPDATE vkpi_project_contracts
        SET extraction_status='processing',
            status=CASE WHEN status='confirmed' THEN status ELSE 'needs_review' END,
            updated_at=?
        WHERE id=? AND project_id=?
        """,
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


def run_contract_extraction_for_job(
    project_id: int,
    contract_id: int,
    *,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """apify worker entry: run extraction for a contract already marked 'processing' by enqueue.

    System context: NO scope check here — the API enqueue already enforced project scope
    with the real session staff; the payload-rebuilt staff dict cannot pass ScopeDenied
    and is only used for cost-ledger attribution. Uses contracts-local get_conn()
    (sqlite-compat '?'); the worker wraps this in db_connection_sync_scope().
    Any failure marks the contract failed and re-raises so the worker's _fail_job records it.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM vkpi_project_contracts WHERE id=? AND project_id=?",
            (int(contract_id), int(project_id)),
        ).fetchone()
        if not row:
            raise LookupError("contract not found")
        contract = _safe_row(row)
        if not str(contract.get("file_name") or "").lower().endswith(".pdf"):
            return {"contract": contract, "extraction": {"status": "skipped", "reason": "not_pdf"}}
        context = _assignment_context(
            conn,
            int(project_id),
            _int(contract.get("assignment_id")) or None,
            _int(contract.get("kol_pool_id")) or None,
        )
        return _run_contract_extraction_core(
            conn, project_id, contract_id, contract=contract, context=context, staff=staff
        )
    except Exception as exc:
        # 兜底:任何 pre-core 异常也要把合同翻 failed,避免卡死在 processing(前端会一直轮询)
        try:
            _mark_failed(conn, contract_id, project_id, str(exc))
        except Exception:
            logger.warning("contract.job.mark_failed_failed contract_id=%s", contract_id)
        raise


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
    body = body or {}
    if body:
        update_contract(project_id, contract_id, body, staff=staff)
    # 批B #4(2026-06-12):空 body 绕写闸修复——确认前校验 fee_amount>0,
    # 或显式 confirm_no_fee=true(置换/无费合同),否则 400 语义 ValueError。
    contract = get_contract(project_id, contract_id, staff=staff, write=True)
    try:
        fee = float(contract.get("fee_amount") or 0)
    except (TypeError, ValueError):
        fee = 0.0
    confirm_no_fee = str(body.get("confirm_no_fee")).strip().lower() in {"1", "true", "yes"}
    if fee <= 0 and not confirm_no_fee:
        raise ValueError("fee_amount must be > 0 to confirm contract (or set confirm_no_fee=true for a no-fee contract)")
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
    _record_contract_fee_to_ledger(int(project_id), int(contract_id), staff=staff)
    return get_contract(project_id, contract_id, staff=staff)


def _record_contract_fee_to_ledger(project_id: int, contract_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """合同人工确认后,把签约费(creator fee)幂等写入成本账本。

    cost_type 用 'cash_fee'(前端合同费列统计的真实类型);source_ref='contract:{id}' 去重,
    防重复确认双记。fee<=0 不写。这是审计 F 缺口的修复:合同费此前无真实账本来源。
    """
    contract = get_contract(project_id, contract_id, staff=staff)
    try:
        fee = float(contract.get("fee_amount") or 0)
    except (TypeError, ValueError):
        fee = 0.0
    if fee <= 0:
        return None
    source_ref = f"contract:{int(contract_id)}"
    conn = get_conn()
    # KOL 归属键:前端 costRowAmount 按 metadata.assignment_id/kol_pool_id 匹配 KOL 明细行——
    # 不带键的合同费只进顶部汇总卡、进不了明细行(全盘扫描 #4)。
    assignment_id = _int(contract.get("assignment_id")) or None
    kol_pool_id = _int(contract.get("kol_pool_id")) or None
    metadata: dict[str, Any] = {"contract_id": int(contract_id), "from": "contract_confirm"}
    if assignment_id:
        metadata["assignment_id"] = assignment_id
    if kol_pool_id:
        metadata["kol_pool_id"] = kol_pool_id
    existing = conn.execute(
        "SELECT id, metadata_json FROM vkpi_cost_ledger WHERE project_id=? AND cost_type='cash_fee' AND source_ref=? LIMIT 1",
        (int(project_id), source_ref),
    ).fetchone()
    if existing:
        row = _safe_row(existing)
        old_meta = row.get("metadata_json")
        if isinstance(old_meta, str):
            try:
                old_meta = json.loads(old_meta)
            except Exception:
                old_meta = {}
        old_meta = old_meta if isinstance(old_meta, dict) else {}
        # 历史行缺 KOL 归属键:借再次确认归档幂等补键(应用路径触发,非裸 UPDATE)。
        if (assignment_id or kol_pool_id) and not (old_meta.get("assignment_id") or old_meta.get("kol_pool_id")):
            conn.execute(
                "UPDATE vkpi_cost_ledger SET metadata_json=?, updated_at=? WHERE id=?",
                (json.dumps({**old_meta, **metadata}, ensure_ascii=False), utcnow(), int(row["id"])),
            )
            # 全盘扫描 P0(2026-06-12):scoped 连接退出即 rollback,缺 commit 致补键永不落库
            conn.commit()
        return None
    from app.domains.costs import ledger as cost_ledger

    return cost_ledger.add_cost(
        {
            "project_id": int(project_id),
            "cost_type": "cash_fee",
            "amount_usd": fee,
            "currency": str(contract.get("fee_currency") or "USD"),
            "status": "actual",
            "source_ref": source_ref,
            "note": "合同人工确认 · 签约费自动入账",
            "metadata": metadata,
        },
        staff=staff,
    )


def contract_download_url(project_id: int, contract_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = get_contract(project_id, contract_id, staff=staff)
    from app.services.media.r2 import get_presigned_url

    return {"contract_id": int(contract_id), "r2_key": contract.get("r2_key"), "url": get_presigned_url(str(contract.get("r2_key") or ""))}


def delete_contract(project_id: int, contract_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = get_contract(project_id, contract_id, staff=staff, write=True)
    conn = get_conn()
    # 批B #2(2026-06-12):删除合同(含已确认)时联动作废其签约费账本行——
    # 标记 voided(经 void_cost 应用路径,自动 audit)+ metadata 留痕,防幽灵 cash_fee 留在账本。
    from app.domains.costs import ledger as cost_ledger

    fee_source_ref = f"contract:{int(contract_id)}"
    fee_rows = conn.execute(
        "SELECT id, metadata_json FROM vkpi_cost_ledger WHERE project_id=? AND cost_type='cash_fee' AND source_ref=? AND status!='void'",
        (int(project_id), fee_source_ref),
    ).fetchall()
    voided_cost_ids: list[int] = []
    for fee_row in fee_rows:
        fee_item = dict(fee_row)
        cost_ledger.void_cost(int(fee_item["id"]), {"reason": f"contract_deleted:{int(contract_id)}"}, staff=staff)
        old_meta = fee_item.get("metadata_json")
        if isinstance(old_meta, str):
            try:
                old_meta = json.loads(old_meta or "{}")
            except Exception:
                old_meta = {}
        old_meta = old_meta if isinstance(old_meta, dict) else {}
        conn.execute(
            "UPDATE vkpi_cost_ledger SET metadata_json=?, updated_at=? WHERE id=?",
            (
                json.dumps(
                    {**old_meta, "voided_reason": "contract_deleted", "voided_contract_id": int(contract_id)},
                    ensure_ascii=False,
                ),
                utcnow(),
                int(fee_item["id"]),
            ),
        )
        conn.commit()
        voided_cost_ids.append(int(fee_item["id"]))
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
            "voided_cost_ids": voided_cost_ids,
        },
    )
    return {"deleted": True, "contract_id": int(contract_id), "r2_deleted": r2_deleted, "voided_cost_ids": voided_cost_ids}
