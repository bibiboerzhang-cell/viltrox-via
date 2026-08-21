"""合同辅助域(批E,2026-06-12):发票回填提取 + 合同条款 LLM 润色。

队列铁律:LLM 一律经 apify_jobs(泳道「发票提取」「合同润色」可见),不做同步内联。
产物落 vkpi_analysis_cache,derive_method 承载 llm_ 语义;失败不写 cache(仅 ready/stale)。

- 发票提取:job_type='contract_invoice_extract',文件已由 /evidence/uploads 落盘
  (/uploads/vkpi_evidence/...,根=UPLOAD_DIR/vkpi_evidence,见 vkpi_evidence_assets.py
  的 EVIDENCE_UPLOAD_DIR)。复用 claude_contract_extract 的 Claude 文件直传机制——其
  prompt 与合同 schema 深耦合,故仿其「子进程 + 超时」模式在本模块实现轻量发票调用。
  cache:target_type='invoice', target_id=extract_key, derive_method='invoice_extract_v1'
  (target_id 用随机 uuid,避免 (target_type,target_id,derive_method) 唯一约束下
  同项目多发票互相覆盖)。幂等按 file_url。
- 合同润色:job_type='contract_polish',只收文本类槽(content_req_*/deliverables/
  post_event_deliverables/travel_terms/onsite_* 等),经 llm_gateway(preferred openai)
  润色为更专业的英文合作条款;cache:target_type='contract_polish',
  target_id=polish_key, derive_method='contract_polish_v1'。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.config import OPENAI_MODEL, UPLOAD_DIR
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import audit
from app.domains.access import scope
from app.domains.costs import budget_guard
from app.domains.projects import ai_job_access
from app.domains.projects import contracts as contracts_domain
from app.domains.projects.workflow_common import _int, staff_id, utcnow
from app.platform import llm_gateway, llm_production
from app.services.ai.analyzers.claude_contract_extract import (
    DEFAULT_CONTRACT_MODEL,
    _anthropic_cost_usd,
    _json_loads,
)


logger = get_logger(__name__)

INVOICE_EXTRACT_JOB_TYPE = "contract_invoice_extract"
INVOICE_EXTRACT_DERIVE_METHOD = "invoice_extract_v1"
INVOICE_ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
INVOICE_FIELDS = (
    "party_name",
    "address",
    "account_name",
    "account_number_or_iban",
    "swift",
    "bank_name",
    "bank_address",
    "amount",
    "currency",
    # E2(2026-07-03)Events 报销回填需要日期:additive 追加,旧缓存产物无该键、消费方需容缺。
    "invoice_date",
)

CONTRACT_POLISH_JOB_TYPE = "contract_polish"
CONTRACT_POLISH_DERIVE_METHOD = "contract_polish_v1"
POLISH_MAX_OUTPUT_TOKENS = 1600
# 只收文本类槽:收款/金额/日期等事实槽不进润色(防 LLM 改写事实)。
POLISH_FIELD_PREFIXES = ("content_req_", "onsite_")
POLISH_FIELD_KEYS = {
    "deliverables",
    "post_event_deliverables",
    "travel_terms",
    "pre_event_materials",
    "demo_shooting",
}

# 文件由 /evidence/uploads 落盘的根目录(与 vkpi_evidence_assets.EVIDENCE_UPLOAD_DIR 同源;
# 不直接 import 路由模块,避免 domain→router 反向依赖)。
EVIDENCE_UPLOAD_DIR = UPLOAD_DIR / "vkpi_evidence"
EVIDENCE_URL_PREFIX = "/uploads/vkpi_evidence/"


def _evidence_path_from_url(file_url: str) -> Path:
    """把 /uploads/vkpi_evidence/... 的 file_url 映射回磁盘路径(防穿越)。"""
    url = str(file_url or "").strip().split("?", 1)[0]
    if not url.startswith(EVIDENCE_URL_PREFIX):
        raise ValueError("file_url must start with /uploads/vkpi_evidence/")
    rel = url[len(EVIDENCE_URL_PREFIX):].lstrip("/")
    if not rel:
        raise ValueError("file_url missing file path")
    root = EVIDENCE_UPLOAD_DIR.resolve()
    path = (root / rel).resolve()
    if path != root and not str(path).startswith(str(root) + os.sep):
        raise ValueError("invalid evidence file path")
    return path


# ---------------------------------------------------------------------------
# 发票提取:Claude 文件直传(仿 claude_contract_extract 子进程 + 超时模式)
# ---------------------------------------------------------------------------

def _invoice_prompt() -> str:
    return """
You are a finance operations assistant for Viltrox marketing.
Read the attached invoice / payment document and extract only facts supported by the document.
Return ONLY valid JSON. Do not wrap in markdown.

Required JSON shape:
{
  "party_name": "",
  "address": "",
  "account_name": "",
  "account_number_or_iban": "",
  "swift": "",
  "bank_name": "",
  "bank_address": "",
  "amount": null,
  "currency": "",
  "invoice_date": ""
}

Rules:
- If a field is not in the document, use "" for text fields and null for amount.
- Never guess or infer bank details; copy them exactly as written.
- amount is a plain number (no thousands separators); currency is the ISO-like code or symbol as written (e.g. USD, EUR).
- invoice_date is the invoice/receipt issue date printed on the document, formatted YYYY-MM-DD; leave "" when no date is printed or the date is ambiguous.
"""


def _invoice_media_block(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    if ext == ".pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext)
    if not media_type:
        raise RuntimeError(f"unsupported invoice file type: {ext or 'unknown'}")
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def extract_invoice_file(file_path: str, *, model_name: str = DEFAULT_CONTRACT_MODEL) -> dict[str, Any]:
    """单次 Claude 发票提取(子进程入口直接调用;主进程请走 _extract_invoice_with_timeout)。"""
    api_key = llm_gateway._get_api_key("anthropic")
    if not api_key:
        raise RuntimeError("missing ANTHROPIC_API_KEY")
    try:
        import anthropic
    except Exception as exc:
        raise RuntimeError(f"Anthropic SDK unavailable: {exc}") from exc

    path = Path(file_path)
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError("invoice file missing")
    client = anthropic.Anthropic(api_key=api_key)
    started = time.monotonic()
    response = llm_production.generate_anthropic_messages(
        client=client,
        model=model_name,
        purpose="project_invoice_extract",
        max_output_tokens=1200,
        cost_tag="projects:invoice_extract",
        triggered_by="project_invoice_extract_worker",
        metadata={"task_binding": "invoice_extract"},
        messages=[
            {
                "role": "user",
                "content": [
                    _invoice_media_block(path),
                    {"type": "text", "text": _invoice_prompt()},
                ],
            }
        ],
    )
    text = "\n".join(str(block.text) for block in getattr(response, "content", []) if getattr(block, "type", "") == "text").strip()
    usage = getattr(response, "usage", None)
    usage_metadata: dict[str, Any] = {}
    if usage:
        usage_metadata = usage.model_dump(mode="json", exclude_none=True) if hasattr(usage, "model_dump") else {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
    model = str(getattr(response, "model", None) or model_name)
    return {
        "ok": True,
        "model": model,
        "usage_metadata": usage_metadata,
        "cost_usd": _anthropic_cost_usd(model, usage_metadata),
        "latency_ms": int((time.monotonic() - started) * 1000),
        "raw_text": text,
        "extracted": _json_loads(text),
    }


def _extract_invoice_with_timeout(
    file_path: str,
    *,
    model_name: str = DEFAULT_CONTRACT_MODEL,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    """子进程 + 超时执行(同 claude_contract_extract.extract_contract_pdf_with_timeout 模式)。"""
    backend_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{backend_root}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    cmd = [
        sys.executable,
        "-m",
        "app.domains.projects.contract_assist",
        "--invoice-file",
        str(file_path),
        "--model",
        str(model_name or DEFAULT_CONTRACT_MODEL),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(backend_root.parent),
            env=env,
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_sec or 120)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("invoice_extract_timeout") from exc
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        error = (proc.stderr or "").strip()[:1000]
        raise RuntimeError(error or f"invoice extractor exited with {proc.returncode}")
    try:
        payload = json.loads(lines[-1])
    except Exception as exc:
        raise RuntimeError(f"invoice extractor returned invalid JSON: {lines[-1][:300]}") from exc
    if payload.get("status") != "ok":
        raise RuntimeError(str(payload.get("error") or "invoice extraction failed")[:1000])
    return payload.get("payload") or {}


def enqueue_invoice_extract_job(
    project_id: int,
    file_url: str,
    *,
    file_name: str = "",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """发票提取入队(幂等按 file_url;预算前置同步失败映射 400)。"""
    scope.assert_project_access(int(project_id), staff, write=True)
    path = _evidence_path_from_url(file_url)
    if path.suffix.lower() not in INVOICE_ALLOWED_EXTENSIONS:
        raise ValueError("unsupported invoice file type")
    if not path.exists() or path.stat().st_size <= 0:
        raise ValueError("invoice file missing on disk")
    conn = get_conn()
    clean_url = str(file_url or "").strip().split("?", 1)[0]
    name = str(file_name or "").strip() or path.name
    # 幂等 1:同 file_url 已有 ready 产物,直接复用(避免同一份发票重复付费)。
    cached = conn.execute(
        """
        SELECT target_id FROM vkpi_analysis_cache
        WHERE target_type='invoice' AND derive_method=? AND status='ready'
          AND result->>'file_url'=?
        ORDER BY updated_at DESC LIMIT 1
        """,
        (INVOICE_EXTRACT_DERIVE_METHOD, clean_url),
    ).fetchone()
    if cached:
        return {"status": "already_extracted", "extract_key": str(dict(cached)["target_id"]), "file_url": clean_url}
    # 幂等 2:同 file_url 已有活跃任务,返回其 extract_key。
    active = conn.execute(
        """
        SELECT id, payload FROM apify_jobs
        WHERE job_type=? AND status IN ('queued','running')
          AND payload->>'file_url'=?
        ORDER BY created_at DESC LIMIT 1
        """,
        (INVOICE_EXTRACT_JOB_TYPE, clean_url),
    ).fetchone()
    if active:
        item = dict(active)
        existing_payload = item.get("payload")
        if isinstance(existing_payload, str):
            try:
                existing_payload = json.loads(existing_payload)
            except Exception:
                existing_payload = {}
        existing_payload = existing_payload if isinstance(existing_payload, dict) else {}
        return {
            "status": "already_queued",
            "job_id": int(item["id"]),
            "extract_key": str(existing_payload.get("extract_key") or ""),
            "file_url": clean_url,
        }
    budget = contracts_domain._budget_preflight(int(path.stat().st_size))
    if not budget.get("allowed"):
        raise RuntimeError("budget_guard_blocked")
    extract_key = uuid.uuid4().hex
    payload = {
        "target_type": "project",
        "target_id": int(project_id),
        "project_id": int(project_id),
        "extract_key": extract_key,
        "file_url": clean_url,
        "file_name": name,
        "derive_method": INVOICE_EXTRACT_DERIVE_METHOD,
        "query_text": f"发票提取 · {name}"[:96],
        "triggered_by_user_id": contracts_domain._triggered_by_user_id(staff),
        "staff_id": contracts_domain._ledger_staff_id(staff),
    }
    payload[ai_job_access.FILE_IDENTITY_KEY] = ai_job_access.capture_file_identity(
        path, root=EVIDENCE_UPLOAD_DIR
    )
    payload[ai_job_access.FENCE_KEY] = ai_job_access.build_job_fence(
        payload, action=ai_job_access.INVOICE_EXTRACT, staff=staff
    )
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id
        """,
        (INVOICE_EXTRACT_JOB_TYPE, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    job_id = int(dict(job)["id"]) if job else None
    audit.log_business_event(
        staff_id=staff_id(staff) or None,
        action_type="invoice_extract_enqueued",
        target_type="project",
        target_id=int(project_id),
        detail=f"发票提取入队 · {name}",
        metadata={"project_id": int(project_id), "file_url": clean_url, "extract_key": extract_key, "job_id": job_id},
    )
    return {
        "status": "queued",
        "job_id": job_id,
        "extract_key": extract_key,
        "file_url": clean_url,
        "budget": budget,
        "diagnostics": {"provider_calls": False, "llm_calls": False, "worker_touched": False},
    }


def enqueue_event_invoice_extract_job(
    event_id: str,
    file_url: str,
    *,
    file_name: str = "",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Events 报销发票提取入队(E2,2026-07-03):镜像项目版,scope 换活动级、payload 带 event_id。

    复用同一 job_type(contract_invoice_extract)与 cache 形状 → worker 零改动;
    幂等口径与项目版一致(同 file_url 的 ready 产物直接复用、活跃任务返回其 extract_key)。
    读端复用 GET /projects/invoice-extract/{extract_key}(event 产物 result 无 project_id,
    但必须按 event_id 做活动 read scope)。红线:与评分域无关,绝不写 viltrox_fit_score。"""
    event_key = str(event_id or "").strip()
    if not event_key:
        raise ValueError("event_id required")
    scope.assert_event_access(event_key, staff, write=True)
    path = _evidence_path_from_url(file_url)
    if path.suffix.lower() not in INVOICE_ALLOWED_EXTENSIONS:
        raise ValueError("unsupported invoice file type")
    if not path.exists() or path.stat().st_size <= 0:
        raise ValueError("invoice file missing on disk")
    conn = get_conn()
    clean_url = str(file_url or "").strip().split("?", 1)[0]
    name = str(file_name or "").strip() or path.name
    # 幂等 1:同 file_url 已有 ready 产物,直接复用(同一份发票不重复付费)。
    cached = conn.execute(
        """
        SELECT target_id FROM vkpi_analysis_cache
        WHERE target_type='invoice' AND derive_method=? AND status='ready'
          AND result->>'file_url'=?
        ORDER BY updated_at DESC LIMIT 1
        """,
        (INVOICE_EXTRACT_DERIVE_METHOD, clean_url),
    ).fetchone()
    if cached:
        return {"status": "already_extracted", "extract_key": str(dict(cached)["target_id"]), "file_url": clean_url}
    # 幂等 2:同 file_url 已有活跃任务,返回其 extract_key。
    active = conn.execute(
        """
        SELECT id, payload FROM apify_jobs
        WHERE job_type=? AND status IN ('queued','running')
          AND payload->>'file_url'=?
        ORDER BY created_at DESC LIMIT 1
        """,
        (INVOICE_EXTRACT_JOB_TYPE, clean_url),
    ).fetchone()
    if active:
        item = dict(active)
        existing_payload = item.get("payload")
        if isinstance(existing_payload, str):
            try:
                existing_payload = json.loads(existing_payload)
            except Exception:
                existing_payload = {}
        existing_payload = existing_payload if isinstance(existing_payload, dict) else {}
        return {
            "status": "already_queued",
            "job_id": int(item["id"]),
            "extract_key": str(existing_payload.get("extract_key") or ""),
            "file_url": clean_url,
        }
    budget = contracts_domain._budget_preflight(int(path.stat().st_size))
    if not budget.get("allowed"):
        raise RuntimeError("budget_guard_blocked")
    extract_key = uuid.uuid4().hex
    payload = {
        "target_type": "event",
        "target_id": event_key,
        "event_id": event_key,
        "extract_key": extract_key,
        "file_url": clean_url,
        "file_name": name,
        "derive_method": INVOICE_EXTRACT_DERIVE_METHOD,
        "query_text": f"活动报销发票提取 · {name}"[:96],
        "triggered_by_user_id": contracts_domain._triggered_by_user_id(staff),
        "staff_id": contracts_domain._ledger_staff_id(staff),
    }
    payload[ai_job_access.FILE_IDENTITY_KEY] = ai_job_access.capture_file_identity(
        path, root=EVIDENCE_UPLOAD_DIR
    )
    payload[ai_job_access.FENCE_KEY] = ai_job_access.build_job_fence(
        payload, action=ai_job_access.INVOICE_EXTRACT, staff=staff
    )
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id
        """,
        (INVOICE_EXTRACT_JOB_TYPE, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    job_id = int(dict(job)["id"]) if job else None
    audit.log_business_event(
        staff_id=staff_id(staff) or None,
        action_type="invoice_extract_enqueued",
        target_type="event",
        target_id=event_key,
        detail=f"活动报销发票提取入队 · {name}",
        metadata={"event_id": event_key, "file_url": clean_url, "extract_key": extract_key, "job_id": job_id},
    )
    return {
        "status": "queued",
        "job_id": job_id,
        "extract_key": extract_key,
        "file_url": clean_url,
        "budget": budget,
        "diagnostics": {"provider_calls": False, "llm_calls": False, "worker_touched": False},
    }


def run_invoice_extract_for_job(
    payload: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    enforce_access_fence: bool = False,
) -> dict[str, Any]:
    """worker 入口:读本地文件 → Claude 提取 → 成功写 cache;失败不写(状态回 apify_jobs)。

    E2(2026-07-03):同时支持项目发票(payload 带 project_id)与活动报销发票(payload 带
    event_id,无 project_id)。两者共用 job_type/cache 形状;event 产物 result 不写 project_id,
    读端 GET /projects/invoice-extract/{key} 按 event_id 执行活动 read scope。"""
    extract_key = str(payload.get("extract_key") or "").strip()
    event_id = str(payload.get("event_id") or "").strip()
    # event payload 绝不从 target_id 反推 project_id(防御:万一 event id 是纯数字串也不会误挂项目)。
    project_id = _int(payload.get("project_id") or (None if event_id else payload.get("target_id"))) or 0
    file_url = str(payload.get("file_url") or "").strip()
    if not extract_key or not file_url or (not project_id and not event_id):
        return {"status": "failed", "reason": "payload_missing_fields"}
    try:
        path = _evidence_path_from_url(file_url)
    except ValueError as exc:
        return {"status": "failed", "reason": str(exc)[:300]}
    if not path.exists() or path.stat().st_size <= 0:
        return {"status": "failed", "reason": "invoice_file_missing"}
    if enforce_access_fence:
        try:
            ai_job_access.revalidate_job_fence(
                payload,
                action=ai_job_access.INVOICE_EXTRACT,
                file_path=path,
                file_root=EVIDENCE_UPLOAD_DIR,
            )
        except ai_job_access.ProjectAiAccessError as exc:
            return ai_job_access.blocked_result(exc)
    try:
        extraction = _extract_invoice_with_timeout(
            str(path),
            model_name=os.getenv("VKPI_INVOICE_CLAUDE_MODEL", DEFAULT_CONTRACT_MODEL),
            timeout_sec=int(os.getenv("VKPI_INVOICE_EXTRACT_TIMEOUT_SEC", "120")),
        )
    except Exception as exc:
        logger.warning("invoice_extract.failed extract_key=%s error=%s", extract_key, str(exc)[:300])
        return {"status": "failed", "reason": str(exc)[:300]}
    if enforce_access_fence:
        try:
            ai_job_access.revalidate_job_fence(
                payload,
                action=ai_job_access.INVOICE_EXTRACT,
                file_path=path,
                file_root=EVIDENCE_UPLOAD_DIR,
            )
        except ai_job_access.ProjectAiAccessError as exc:
            return ai_job_access.blocked_result(exc, provider_called=True)
    data = extraction.get("extracted") if isinstance(extraction.get("extracted"), dict) else {}
    extracted: dict[str, Any] = {}
    for field in INVOICE_FIELDS:
        value = data.get(field)
        if field == "amount":
            try:
                extracted[field] = float(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                extracted[field] = None
        else:
            extracted[field] = str(value or "").strip()[:500]
    if not any(v not in (None, "") for v in extracted.values()):
        return {"status": "failed", "reason": "llm_empty_extraction"}
    try:
        usage = extraction.get("usage_metadata") if isinstance(extraction.get("usage_metadata"), dict) else {}
        cost_metadata: dict[str, Any] = {
            "extract_key": extract_key,
            "derive_method": INVOICE_EXTRACT_DERIVE_METHOD,
            "triggered_by_user_id": contracts_domain._triggered_by_user_id(staff),
        }
        # 项目/活动二选一:只写真实存在的归属键,不给 event 产物记 project_id=0 的假账。
        if project_id:
            cost_metadata["project_id"] = int(project_id)
        if event_id:
            cost_metadata["event_id"] = event_id
        budget_guard.record_cost(
            scope=contracts_domain.CONTRACT_BUDGET_SCOPE,
            cron_task="vkpi_invoice_extract",
            ai_provider="anthropic",
            model_name=str(extraction.get("model") or DEFAULT_CONTRACT_MODEL),
            cost_usd=float(extraction.get("cost_usd") or 0),
            tokens_in=int(usage.get("input_tokens") or 0),
            tokens_out=int(usage.get("output_tokens") or 0),
            staff_id=contracts_domain._ledger_staff_id(staff),
            metadata=cost_metadata,
            triggered_by=None,
            extra_scopes=["monthly_total", "provider:claude"],
        )
    except Exception:
        logger.warning("invoice_extract.record_cost_failed extract_key=%s", extract_key)
    now = utcnow()
    result = {
        "schema_version": INVOICE_EXTRACT_DERIVE_METHOD,
        "extracted": extracted,
        "llm_model": extraction.get("model"),
        "llm_provider": "anthropic",
        "llm_latency_ms": extraction.get("latency_ms"),
        "file_url": file_url,
        "file_name": str(payload.get("file_name") or path.name),
        "generated_at": now,
    }
    # project 产物保持原形状(读端按 result.project_id 做项目 scope 把关);
    # event 产物写 event_id、不写 project_id(避免 require_project_read(0) 误拒)。
    if project_id:
        result["project_id"] = int(project_id)
    if event_id:
        result["event_id"] = event_id
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_analysis_cache (
            target_type, target_id, model, derive_method, result, cost, status,
            triggered_by_user_id, created_at, updated_at
        ) VALUES ('invoice', ?, ?, ?, ?::jsonb, ?, 'ready', ?, ?, ?)
        ON CONFLICT (target_type, target_id, derive_method)
        DO UPDATE SET model=EXCLUDED.model, result=EXCLUDED.result, cost=EXCLUDED.cost,
            status='ready', triggered_by_user_id=EXCLUDED.triggered_by_user_id, updated_at=EXCLUDED.updated_at
        """,
        (
            extract_key,
            str(extraction.get("model") or DEFAULT_CONTRACT_MODEL),
            INVOICE_EXTRACT_DERIVE_METHOD,
            json.dumps(result, ensure_ascii=False),
            float(extraction.get("cost_usd") or 0),
            contracts_domain._triggered_by_user_id(staff),
            now,
            now,
        ),
    )
    conn.commit()
    done: dict[str, Any] = {"status": "ready", "extract_key": extract_key}
    if project_id:
        done["project_id"] = int(project_id)
    if event_id:
        done["event_id"] = event_id
    return done


def get_invoice_extract(extract_key: str) -> dict[str, Any]:
    """读端:发票提取产物(cache);未就绪返回 state=pending(任务可能仍在队列)。"""
    key = str(extract_key or "").strip()
    if not key:
        raise ValueError("extract_key required")
    row = get_conn().execute(
        """
        SELECT result, model, updated_at FROM vkpi_analysis_cache
        WHERE target_type='invoice' AND target_id=? AND derive_method=? AND status='ready'
        ORDER BY updated_at DESC LIMIT 1
        """,
        (key, INVOICE_EXTRACT_DERIVE_METHOD),
    ).fetchone()
    if not row:
        return {"state": "pending", "extract_key": key}
    data = dict(row)
    result = data.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {}
    return {
        "state": "ready",
        "extract_key": key,
        "result": result if isinstance(result, dict) else {},
        "model": data.get("model"),
        "updated_at": str(data.get("updated_at") or ""),
    }


# ---------------------------------------------------------------------------
# 合同润色:llm_gateway(preferred openai)
# ---------------------------------------------------------------------------

def _filter_polish_fields(fields: Any) -> dict[str, str]:
    """白名单过滤:只收文本类槽,收款/金额/日期等事实槽一律剔除。"""
    out: dict[str, str] = {}
    if not isinstance(fields, dict):
        return out
    for key, value in fields.items():
        name = str(key or "").strip()
        text = str(value or "").strip()
        if not name or not text:
            continue
        if name in POLISH_FIELD_KEYS or name.startswith(POLISH_FIELD_PREFIXES):
            out[name] = text[:2000]
    return out


def _polish_prompt(template_key: str, fields: dict[str, str]) -> str:
    return (
        "你是 VILTROX(唯卓仕,相机镜头品牌)的合同条款编辑。"
        "把下列合作条款草稿润色为更专业的英文合作条款描述(professional contract English)。\n\n"
        f"合同模板:{template_key or '-'}\n\n"
        "要求:\n"
        "1. 只润色表达与措辞,绝不新增/修改义务、数量、金额、日期、权利等事实;缺失信息不要编造。\n"
        "2. 中文草稿翻译为英文;英文草稿改写为正式商务合同语气。\n"
        "3. 只返回 JSON,键与输入字段完全一致,值为润色后的英文文本,不要包裹 markdown。\n\n"
        "输入字段 JSON:\n" + json.dumps(fields, ensure_ascii=False, indent=2)
    )


def _valid_polish_payload(value: Any, field_names: set[str]) -> bool:
    if not isinstance(value, dict) or set(value) != field_names:
        return False
    return all(
        isinstance(value.get(name), str)
        and bool(str(value.get(name) or "").strip())
        and len(str(value.get(name) or "").strip()) <= 4000
        for name in field_names
    )


def _polish_failure_code(response: Any) -> str:
    result = response if isinstance(response, dict) else {}
    failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    latest = errors[-1] if errors and isinstance(errors[-1], dict) else {}
    return str(
        failure.get("code")
        or result.get("failure_code")
        or result.get("reason")
        or latest.get("status")
        or result.get("status")
        or "llm_unavailable"
    )[:120]


def enqueue_contract_polish_job(
    project_id: int,
    *,
    template_key: str = "",
    fields: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """合同润色入队(job_type='contract_polish';只收文本类槽)。"""
    scope.assert_project_access(int(project_id), staff, write=True)
    key = str(template_key or "").strip()
    if key:
        from app.domains.projects.contract_templates import TEMPLATES

        if key not in TEMPLATES:
            raise LookupError(f"unknown contract template: {key}")
    clean = _filter_polish_fields(fields)
    if not clean:
        raise ValueError(
            "no polishable text fields provided "
            "(accepted: content_req_*/onsite_*/deliverables/post_event_deliverables/travel_terms/pre_event_materials/demo_shooting)"
        )
    polish_key = uuid.uuid4().hex
    payload = {
        "target_type": "contract_polish",
        "target_id": polish_key,
        "project_id": int(project_id),
        "template_key": key,
        "fields": clean,
        "polish_key": polish_key,
        "derive_method": CONTRACT_POLISH_DERIVE_METHOD,
        "query_text": f"合同润色 · {key or '/'.join(sorted(clean)[:3])}"[:96],
        "triggered_by_user_id": contracts_domain._triggered_by_user_id(staff),
        "staff_id": contracts_domain._ledger_staff_id(staff),
    }
    payload[ai_job_access.FENCE_KEY] = ai_job_access.build_job_fence(
        payload, action=ai_job_access.CONTRACT_POLISH, staff=staff
    )
    conn = get_conn()
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id
        """,
        (CONTRACT_POLISH_JOB_TYPE, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    job_id = int(dict(job)["id"]) if job else None
    audit.log_business_event(
        staff_id=staff_id(staff) or None,
        action_type="contract_polish_enqueued",
        target_type="project",
        target_id=int(project_id),
        detail=f"合同润色入队 · {key or 'fields'} · {len(clean)} 字段",
        metadata={"project_id": int(project_id), "template_key": key, "polish_key": polish_key, "fields": sorted(clean), "job_id": job_id},
    )
    return {"status": "queued", "job_id": job_id, "polish_key": polish_key, "fields": sorted(clean)}


def run_contract_polish_for_job(
    payload: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    enforce_access_fence: bool = False,
) -> dict[str, Any]:
    """worker 入口:LLM 润色并写 cache(失败不写,状态回 apify_jobs)。"""
    polish_key = str(payload.get("polish_key") or payload.get("target_id") or "").strip()
    clean = _filter_polish_fields(payload.get("fields"))
    if not polish_key or not clean:
        return {"status": "failed", "reason": "payload_missing_fields"}
    template_key = str(payload.get("template_key") or "").strip()
    if enforce_access_fence:
        try:
            ai_job_access.revalidate_job_fence(payload, action=ai_job_access.CONTRACT_POLISH)
        except ai_job_access.ProjectAiAccessError as exc:
            return ai_job_access.blocked_result(exc)
    try:
        resp = llm_production.generate_json(
            _polish_prompt(template_key, clean),
            provider="openai",
            model=OPENAI_MODEL,
            purpose="vkpi_contract_polish",
            max_output_tokens=POLISH_MAX_OUTPUT_TOKENS,
            cost_tag="vkpi_contract_polish",
            triggered_by=contracts_domain._triggered_by_user_id(staff) or "projects.contract_polish",
            staff=staff or {},
            required_keys=tuple(clean),
            validator=lambda value: _valid_polish_payload(value, set(clean)),
            deadline_seconds=75.0,
            metadata={
                "project_id": payload.get("project_id"),
                "polish_key": polish_key,
                "template_key": template_key,
                "phase": "project_contract",
                "subphase": "polish_terms",
                "attempt_index": 1,
                "total": 1,
                "target_label": f"project:{payload.get('project_id') or '-'}",
            },
        )
    except Exception as exc:  # AI-off/readiness/provider failure: never write cache
        logger.warning("contract polish strict LLM unavailable", exc_info=True)
        resp = {"status": "failed", "reason": str(exc)[:120] or type(exc).__name__}
    if enforce_access_fence:
        try:
            ai_job_access.revalidate_job_fence(payload, action=ai_job_access.CONTRACT_POLISH)
        except ai_job_access.ProjectAiAccessError as exc:
            return ai_job_access.blocked_result(exc, provider_called=True)
    parsed = resp.get("json") if isinstance(resp, dict) else None
    if not (
        resp.get("status") == "success"
        and str(resp.get("provider") or "").strip().lower() == "openai"
        and str(resp.get("model") or "").strip().startswith(OPENAI_MODEL)
        and _valid_polish_payload(parsed, set(clean))
    ):
        return {"status": "failed", "reason": _polish_failure_code(resp)}
    polished = {
        name: str(parsed.get(name) or "").strip()[:4000]
        for name in clean
        if str(parsed.get(name) or "").strip()
    }
    if not polished:
        return {"status": "failed", "reason": "llm_missing_polished_fields"}
    now = utcnow()
    result = {
        "schema_version": CONTRACT_POLISH_DERIVE_METHOD,
        "fields": polished,
        "source_fields": clean,
        "llm_model": resp.get("model"),
        "llm_provider": resp.get("provider"),
        "project_id": payload.get("project_id"),
        "template_key": template_key,
        "generated_at": now,
    }
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_analysis_cache (
            target_type, target_id, model, derive_method, result, cost, status,
            triggered_by_user_id, created_at, updated_at
        ) VALUES ('contract_polish', ?, ?, ?, ?::jsonb, ?, 'ready', ?, ?, ?)
        ON CONFLICT (target_type, target_id, derive_method)
        DO UPDATE SET model=EXCLUDED.model, result=EXCLUDED.result, cost=EXCLUDED.cost,
            status='ready', triggered_by_user_id=EXCLUDED.triggered_by_user_id, updated_at=EXCLUDED.updated_at
        """,
        (
            polish_key,
            str(resp.get("model") or "llm_gateway"),
            CONTRACT_POLISH_DERIVE_METHOD,
            json.dumps(result, ensure_ascii=False),
            float(resp.get("cost_cents") or 0) / 100.0,
            contracts_domain._triggered_by_user_id(staff),
            now,
            now,
        ),
    )
    conn.commit()
    return {"status": "ready", "polish_key": polish_key}


def get_contract_polish(polish_key: str) -> dict[str, Any]:
    """读端:合同润色产物(cache);未就绪返回 state=pending。"""
    key = str(polish_key or "").strip()
    if not key:
        raise ValueError("polish_key required")
    row = get_conn().execute(
        """
        SELECT result, model, updated_at FROM vkpi_analysis_cache
        WHERE target_type='contract_polish' AND target_id=? AND derive_method=? AND status='ready'
        ORDER BY updated_at DESC LIMIT 1
        """,
        (key, CONTRACT_POLISH_DERIVE_METHOD),
    ).fetchone()
    if not row:
        return {"state": "pending", "polish_key": key}
    data = dict(row)
    result = data.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {}
    return {
        "state": "ready",
        "polish_key": key,
        "result": result if isinstance(result, dict) else {},
        "model": data.get("model"),
        "updated_at": str(data.get("updated_at") or ""),
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="Run one Claude invoice extraction and print JSON.")
    parser.add_argument("--invoice-file", required=True)
    parser.add_argument("--model", default=DEFAULT_CONTRACT_MODEL)
    args = parser.parse_args()
    try:
        payload = extract_invoice_file(args.invoice_file, model_name=args.model)
        sys.stdout.write(json.dumps({"status": "ok", "payload": payload}, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        sys.stdout.write(json.dumps({"status": "error", "error": str(exc)[:1000]}, ensure_ascii=False, default=str) + "\n")
        raise SystemExit(1)


if __name__ == "__main__":
    _main()
