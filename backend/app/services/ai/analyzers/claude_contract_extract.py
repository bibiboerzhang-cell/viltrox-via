"""Claude PDF contract extraction for project contract archives."""
from __future__ import annotations

import base64
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.core.model_registry import CLAUDE_OPUS_EXACT_MODEL, is_selectable_model
from app.core.model_pricing import estimate_cost_usd
from app.platform import llm_gateway, llm_production


logger = get_logger(__name__)


def _registered_anthropic_model(model_name: str | None) -> str:
    model = str(model_name or "").strip()
    if not is_selectable_model(f"anthropic/{model}"):
        raise RuntimeError(
            "Anthropic model must be an exact id registered in model_registry: "
            f"{model or '<empty>'}"
        )
    return model


DEFAULT_CONTRACT_MODEL = _registered_anthropic_model(
    os.getenv("VKPI_CONTRACT_CLAUDE_MODEL", CLAUDE_OPUS_EXACT_MODEL)
)


def _json_loads(text: str) -> dict[str, Any]:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if match:
        raw = match.group(0)
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _contract_prompt(context: dict[str, Any] | None = None) -> str:
    ctx = context or {}
    return f"""
You are a senior contract operations reviewer for Viltrox marketing.
Read the attached PDF contract and extract only facts supported by the document.
Return ONLY valid JSON. Do not wrap in markdown.

Project context:
- project_id: {ctx.get("project_id") or ""}
- assignment_id: {ctx.get("assignment_id") or ""}
- kol_pool_id: {ctx.get("kol_pool_id") or ""}
- kol_name: {ctx.get("kol_name") or ""}

Required JSON shape:
{{
  "summary": "one concise Chinese summary",
  "fee_amount": 0,
  "fee_currency": "USD",
  "contract_duration": "",
  "start_date": null,
  "end_date": null,
  "platforms": [],
  "deliverable_count": null,
  "deliverables": [
    {{"platform":"", "content_type":"", "quantity": 1, "deadline": null, "notes": ""}}
  ],
  "must_include": [],
  "usage_rights": "",
  "exclusivity": "",
  "buyout_rights": "",
  "breach_terms": "",
  "payment_terms": "",
  "cancellation_terms": "",
  "revision_terms": "",
  "promised_publish_deadline": null,
  "risk_flags": [],
  "missing_or_unclear_fields": [],
  "field_confidence": {{
    "fee_amount": 0.0,
    "fee_currency": 0.0,
    "contract_duration": 0.0,
    "start_date": 0.0,
    "end_date": 0.0,
    "platforms": 0.0,
    "deliverable_count": 0.0,
    "deliverables": 0.0,
    "must_include": 0.0,
    "usage_rights": 0.0,
    "exclusivity": 0.0,
    "buyout_rights": 0.0,
    "breach_terms": 0.0,
    "payment_terms": 0.0,
    "cancellation_terms": 0.0,
    "revision_terms": 0.0
  }},
  "evidence": {{
    "fee_amount": "quote source text or page",
    "deliverables": "quote source text or page",
    "usage_rights": "quote source text or page"
  }}
}}

Rules:
- If a field is not in the contract, use null for numbers/dates and "" or [] for text/list fields.
- Never infer legal terms from templates or habit.
- Confidence is 0 to 1. Use 0 when unsupported.
- Chinese summaries are OK, but keep extracted legal terms faithful to the PDF wording.
"""


def estimate_contract_extract_cost(file_size_bytes: int, *, prompt_chars: int = 6000) -> float:
    # Conservative guard estimate for Opus PDF extraction. Actual cost is recorded from usage.
    size_mb = max(0.0, float(file_size_bytes or 0) / (1024 * 1024))
    return round(min(4.75, max(0.25, 0.20 + size_mb * 0.35 + prompt_chars / 100_000)), 4)


def _anthropic_cost_usd(model: str, usage: dict[str, Any]) -> float:
    tokens_in = int(usage.get("input_tokens") or 0)
    tokens_out = int(usage.get("output_tokens") or 0)
    return round(estimate_cost_usd(model, tokens_in, tokens_out), 6)


def extract_contract_pdf(pdf_path: str, *, context: dict[str, Any] | None = None, model_name: str = DEFAULT_CONTRACT_MODEL) -> dict[str, Any]:
    model_name = _registered_anthropic_model(model_name)
    api_key = llm_gateway._get_api_key("anthropic")
    if not api_key:
        raise RuntimeError("missing ANTHROPIC_API_KEY")
    try:
        import anthropic
    except Exception as exc:
        raise RuntimeError(f"Anthropic SDK unavailable: {exc}") from exc

    path = Path(pdf_path)
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError("contract PDF missing")
    pdf_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    prompt = _contract_prompt(context)
    client = anthropic.Anthropic(api_key=api_key)
    started = time.monotonic()
    response = llm_production.generate_anthropic_messages(
        client=client,
        model=model_name,
        purpose="project_contract_extract",
        max_output_tokens=4000,
        cost_tag="projects:contract_extract",
        triggered_by="project_contract_extract_worker",
        metadata={"task_binding": "contract_pdf_extract"},
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                    {"type": "text", "text": prompt},
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


def extract_contract_pdf_with_timeout(
    pdf_path: str,
    *,
    context: dict[str, Any] | None = None,
    model_name: str = DEFAULT_CONTRACT_MODEL,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    model_name = _registered_anthropic_model(model_name)
    backend_root = Path(__file__).resolve().parents[4]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{backend_root}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    cmd = [
        sys.executable,
        "-m",
        "app.services.ai.analyzers.claude_contract_extract",
        "--pdf",
        str(pdf_path),
        "--model",
        str(model_name or DEFAULT_CONTRACT_MODEL),
        "--context-json",
        json.dumps(context or {}, ensure_ascii=False, default=str),
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
        raise TimeoutError("contract_extract_timeout") from exc
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        if proc.returncode != 0:
            error = (proc.stderr or "").strip()[:1000]
            raise RuntimeError(error or f"contract extractor exited with {proc.returncode}")
        raise RuntimeError("contract extractor exited without stdout")
    try:
        payload = json.loads(lines[-1])
    except Exception as exc:
        raise RuntimeError(f"contract extractor returned invalid JSON: {lines[-1][:300]}") from exc
    if payload.get("status") != "ok":
        raise RuntimeError(str(payload.get("error") or "contract extraction failed")[:1000])
    if proc.returncode != 0:
        error = (proc.stderr or "").strip()[:1000]
        raise RuntimeError(error or f"contract extractor exited with {proc.returncode}")
    return payload.get("payload") or {}


def _main() -> None:
    parser = argparse.ArgumentParser(description="Run one Claude contract PDF extraction and print JSON.")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--model", default=DEFAULT_CONTRACT_MODEL)
    parser.add_argument("--context-json", default="{}")
    args = parser.parse_args()
    try:
        context = json.loads(args.context_json or "{}")
        payload = extract_contract_pdf(args.pdf, context=context if isinstance(context, dict) else {}, model_name=args.model)
        print(json.dumps({"status": "ok", "payload": payload}, ensure_ascii=False, default=str))
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)[:1000]}, ensure_ascii=False, default=str))
        raise SystemExit(1)


if __name__ == "__main__":
    _main()
