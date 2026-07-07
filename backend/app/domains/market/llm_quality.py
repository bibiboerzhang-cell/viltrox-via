"""Deterministic quality gate for V-KPI market LLM smoke outputs."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
NUMBER_RE = re.compile(r"\d")
REQUIRED_LABELS = ("判断", "量化依据", "限制")
TRAILING_FRAGMENT_RE = re.compile(r"(；|：|,|，|、|/|\\|-|=)$")


from app.core.coerce import _text


def _line_count(text: str) -> int:
    return len([line for line in text.splitlines() if line.strip()])


def evaluate_market_llm_output(llm_result: dict[str, Any] | None) -> dict[str, Any]:
    """Return a no-side-effect quality verdict for one LLM smoke result."""
    result = llm_result if isinstance(llm_result, dict) else {}
    text = _text(result.get("text"))
    provider = _text(result.get("provider"))
    status = _text(result.get("status"))
    reasons: list[str] = []

    if status != "success":
        reasons.append("llm_status_not_success")
    if provider == "rule_v0" or not provider:
        reasons.append("provider_not_live_llm")
    if len(text) < 80:
        reasons.append("too_short")
    if not CHINESE_RE.search(text):
        reasons.append("not_chinese")
    missing_labels = [label for label in REQUIRED_LABELS if label not in text]
    if missing_labels:
        reasons.append("missing_required_labels:" + ",".join(missing_labels))
    if not NUMBER_RE.search(text):
        reasons.append("missing_quantitative_evidence")
    if "编造" in text and "不要编造" not in text:
        reasons.append("meta_instruction_leak")
    stripped = text.rstrip()
    if stripped and TRAILING_FRAGMENT_RE.search(stripped):
        reasons.append("trailing_fragment")
    if _line_count(text) > 4:
        reasons.append("too_verbose_for_ui_card")

    usable = not reasons
    return {
        "usable_for_ui": usable,
        "status": "usable" if usable else "unusable",
        "provider": provider,
        "model": _text(result.get("model")),
        "text_chars": len(text),
        "line_count": _line_count(text),
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "reasons": reasons,
        "checks": {
            "live_llm_success": status == "success" and provider not in {"", "rule_v0"},
            "has_chinese": bool(CHINESE_RE.search(text)),
            "has_required_labels": not missing_labels,
            "has_quantitative_evidence": bool(NUMBER_RE.search(text)),
            "not_too_short": len(text) >= 80,
            "not_trailing_fragment": not bool(stripped and TRAILING_FRAGMENT_RE.search(stripped)),
            "compact_for_ui_card": _line_count(text) <= 4,
        },
    }


def evaluate_market_llm_report(report: dict[str, Any]) -> dict[str, Any]:
    llm_result = report.get("llm_single_result") if isinstance(report.get("llm_single_result"), dict) else {}
    gate = evaluate_market_llm_output(llm_result)
    return {
        "mode": "market_llm_quality_gate_v0",
        "source_mode": report.get("mode"),
        "source_generated_at": report.get("generated_at"),
        "provider_calls": bool(report.get("provider_calls")),
        "llm_calls": bool(report.get("llm_calls")),
        "write_db": False,
        "usable_for_ui": gate["usable_for_ui"],
        "quality_gate": gate,
        "policy": {
            "no_model_call": True,
            "no_db_write": True,
            "ui_must_hide_unusable_llm_output": True,
        },
    }


def evaluate_market_llm_report_file(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("LLM report must be a JSON object")
    return evaluate_market_llm_report(payload)
