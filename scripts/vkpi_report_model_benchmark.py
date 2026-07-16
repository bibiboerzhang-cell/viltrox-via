#!/usr/bin/env python3
"""Repeatable Report model benchmark with an explicit live-call gate.

The default is a zero-call dry-run. ``--live`` is the only CLI switch that can
reach a provider, and the Report model policy can still block those calls when
data readiness, source provenance, or sample thresholds are insufficient.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.model_registry import split_binding  # noqa: E402
from app.domains.reports.model_policy import (  # noqa: E402
    REPORT_CHALLENGER_MODEL,
    REPORT_PRIMARY_MODEL,
    evaluate_report_model_policy,
)
from app.platform.models.readiness import (  # noqa: E402
    MODEL_PROBE_EVIDENCE_VERSION,
    assess_model_readiness,
)
from app.platform.models.evaluation_artifact import (  # noqa: E402
    build_model_evaluation_artifact,
)
from app.platform.models.runtime import (  # noqa: E402
    resolve_model_binding,
    response_model_matches,
)


BENCHMARK_VERSION = "vkpi_report_model_benchmark_v1"
SIGNING_BUNDLE_VERSION = "vkpi_report_model_signing_bundle_v1"
_SAFE_RESULT_STATUSES = {
    "blocked_by_policy",
    "dry_run",
    "empty_response",
    "failed",
    "invalid_invoker_result",
    "invoker_exception",
    "not_configured",
    "refusal",
    "success",
    "unsupported_provider",
}
PRICING_AS_OF = "2026-07-13"
OPENAI_MODEL_DOCS = "https://developers.openai.com/api/docs/models"
ANTHROPIC_MODEL_DOCS = (
    "https://platform.claude.com/docs/en/about-claude/models/overview"
)
def _model_run(role: str, exact_binding: str, pricing_source: str) -> dict[str, Any]:
    provider, model_id = split_binding(exact_binding)
    resolved = resolve_model_binding(provider, model_id)
    if not resolved.pricing_known:
        raise RuntimeError(f"exact model pricing missing: {exact_binding}")
    return {
        "role": role,
        "binding": exact_binding,
        "input_usd_per_million": float(resolved.input_cents_per_million or 0) / 100,
        "output_usd_per_million": float(resolved.output_cents_per_million or 0) / 100,
        "pricing_source": pricing_source,
        "pricing_version": resolved.pricing_version,
    }


MODEL_RUNS = (
    _model_run("primary", REPORT_PRIMARY_MODEL, OPENAI_MODEL_DOCS),
    _model_run(
        "challenger_and_judge_candidate",
        REPORT_CHALLENGER_MODEL,
        ANTHROPIC_MODEL_DOCS,
    ),
)

DEFAULT_FIXTURE: dict[str, Any] = {
    "fixture_id": "synthetic_weekly_report_v1",
    "evaluation_dataset": {
        "version": "synthetic_weekly_report_v1",
        "as_of": "2026-07-13T00:00:00Z",
        "provenance": "repository_fixture:scripts/vkpi_report_model_benchmark.py",
        "actual": False,
        "synthetic": True,
    },
    "data_readiness": {
        "version": "market_brain_data_readiness_v1",
        "status": "ready",
        "ready": True,
        "claimable": True,
        "claim_level": "validated",
        "checks": {
            "weekly_report_fixture": {
                "status": "ready",
                "observed": 20,
                "minimum": 10,
            }
        },
        "blockers": [],
    },
    "sources": [
        {
            "key": "commerce_orders",
            "label": "commerce order rows",
            "observed": 12,
            "minimum": 10,
            "source_count": 12,
            "data_status": "real",
        },
        {
            "key": "finance_spend",
            "label": "finance spend rows",
            "observed": 8,
            "minimum": 5,
            "source_count": 8,
            "data_status": "real",
        },
    ],
    "records": [
        {
            "source_id": "commerce:orders:2026-W27",
            "metric": "orders",
            "value": 12,
            "unit": "count",
        },
        {
            "source_id": "commerce:revenue:2026-W27",
            "metric": "revenue_usd",
            "value": 1200.0,
            "unit": "USD",
        },
        {
            "source_id": "finance:spend:2026-W27",
            "metric": "spend_usd",
            "value": 300.0,
            "unit": "USD",
        },
        {
            "source_id": "derived:roas:2026-W27",
            "metric": "roas",
            "value": 4.0,
            "unit": "ratio",
            "derived_from": [
                "commerce:revenue:2026-W27",
                "finance:spend:2026-W27",
            ],
        },
    ],
    "expected": {
        "metrics": {
            "orders": 12,
            "revenue_usd": 1200.0,
            "spend_usd": 300.0,
            "roas": 4.0,
        },
        "source_ids": [
            "commerce:orders:2026-W27",
            "commerce:revenue:2026-W27",
            "finance:spend:2026-W27",
            "derived:roas:2026-W27",
        ],
    },
}

LiveInvoker = Callable[..., Mapping[str, Any]]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _fixture_digest(fixture: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(fixture).encode("utf-8")).hexdigest()


def build_prompt(fixture: Mapping[str, Any]) -> str:
    records = fixture.get("records") if isinstance(fixture.get("records"), list) else []
    expected = fixture.get("expected") if isinstance(fixture.get("expected"), Mapping) else {}
    metrics = expected.get("metrics") if isinstance(expected.get("metrics"), Mapping) else {}
    metric_keys = list(metrics.keys())
    return (
        "You are evaluating a reporting model. Use only the supplied synthetic records. "
        "Return exactly one JSON object with no markdown and this shape: "
        '{"summary":"string","metrics":{...},"source_ids":["..."]}. '
        f"metrics must contain exactly these keys: {_canonical_json(metric_keys)}. "
        "Copy factual values from the records, do not add facts, and include every "
        "source_id used. Records: "
        f"{_canonical_json(records)}"
    )


def _extract_json_object(text: str) -> tuple[dict[str, Any] | None, str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return None, "empty_response"

    def reject_constant(value: str) -> None:
        raise ValueError(f"non_finite_number:{value}")

    try:
        value = json.loads(cleaned, parse_constant=reject_constant, object_pairs_hook=_unique_json_object)
    except json.JSONDecodeError as exc:
        return None, f"invalid_json:{exc.msg}"
    except ValueError as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "response_json_is_not_object"
    return value, ""


def _schema_result(
    output: dict[str, Any] | None,
    expected_metrics: Mapping[str, Any],
    parse_error: str,
) -> dict[str, Any]:
    checks = {
        "json_object": output is not None,
        "top_level_keys_exact": bool(output)
        and set(output) == {"summary", "metrics", "source_ids"},
        "summary_nonempty_string": bool(output)
        and isinstance(output.get("summary"), str)
        and bool(output["summary"].strip()),
        "metrics_object": bool(output) and isinstance(output.get("metrics"), dict),
        "metric_keys_exact": bool(output)
        and isinstance(output.get("metrics"), dict)
        and set(output["metrics"].keys()) == set(expected_metrics.keys()),
        "source_ids_strings": bool(output)
        and isinstance(output.get("source_ids"), list)
        and all(isinstance(item, str) for item in output["source_ids"]),
        "finite_numbers_only": bool(output) and _all_numbers_finite(output),
    }
    passed_count = sum(bool(value) for value in checks.values())
    return {
        "passed": all(checks.values()),
        "score": round(passed_count / len(checks), 4),
        "checks": checks,
        "error": parse_error or None,
    }


def _facts_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if not math.isfinite(float(expected)) or not math.isfinite(float(actual)):
            return False
        return abs(float(expected) - float(actual)) <= 1e-9
    return expected == actual


def _all_numbers_finite(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, list):
        return all(_all_numbers_finite(item) for item in value)
    if isinstance(value, Mapping):
        return all(_all_numbers_finite(item) for item in value.values())
    return False


def _factual_result(
    output: dict[str, Any] | None,
    expected_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    actual_metrics = output.get("metrics") if isinstance(output, dict) else None
    actual_metrics = actual_metrics if isinstance(actual_metrics, dict) else {}
    matches = {
        key: _facts_equal(expected, actual_metrics.get(key))
        for key, expected in expected_metrics.items()
    }
    total = len(matches)
    matched = sum(bool(value) for value in matches.values())
    return {
        "passed": bool(total) and matched == total,
        "score": round(matched / total, 4) if total else 0.0,
        "matched": matched,
        "total": total,
        "checks": matches,
    }


def _source_result(
    output: dict[str, Any] | None,
    expected_source_ids: list[str],
) -> dict[str, Any]:
    raw_ids = output.get("source_ids") if isinstance(output, dict) else None
    actual_ids = [str(item) for item in raw_ids] if isinstance(raw_ids, list) else []
    expected_set = set(expected_source_ids)
    actual_set = set(actual_ids)
    matched = expected_set & actual_set
    missing = sorted(expected_set - actual_set)
    unexpected = sorted(actual_set - expected_set)
    recall = len(matched) / len(expected_set) if expected_set else 1.0
    precision = len(matched) / len(actual_set) if actual_set else 0.0
    return {
        "passed": not missing and not unexpected and len(actual_ids) == len(actual_set),
        "score": round((precision + recall) / 2, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "missing_count": len(missing),
        "unexpected_count": len(unexpected),
        "missing_sha256": [
            hashlib.sha256(value.encode("utf-8")).hexdigest() for value in missing
        ],
        "unexpected_sha256": [
            hashlib.sha256(value.encode("utf-8")).hexdigest() for value in unexpected
        ],
        "duplicate_ids": len(actual_ids) != len(actual_set),
    }


def _empty_dimension(reason: str) -> dict[str, Any]:
    return {"passed": None, "score": None, "reason": reason}


def _cost_result(
    model_run: Mapping[str, Any],
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    request_sent: bool,
) -> dict[str, Any]:
    input_rate = float(model_run["input_usd_per_million"])
    output_rate = float(model_run["output_usd_per_million"])
    usage_valid = input_tokens is not None and output_tokens is not None
    estimated_usd = (
        (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
        if usage_valid
        else None
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_usd_per_million": input_rate,
        "output_usd_per_million": output_rate,
        "estimated_usd": round(estimated_usd, 8) if estimated_usd is not None else None,
        "basis": (
            "official_list_price_estimate"
            if request_sent and usage_valid
            else "usage_missing_or_invalid"
            if request_sent
            else "not_invoked"
        ),
        "pricing_as_of": PRICING_AS_OF,
        "pricing_source": model_run["pricing_source"],
        "pricing_version": model_run["pricing_version"],
        "provider_invoice_verified": False,
    }


def _post_json(
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            **dict(headers),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ViltroxMarketingReportBenchmark/1.0",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(  # nosec B310 - fixed official provider URLs
            request,
            timeout=max(1.0, float(timeout_seconds)),
        ) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
        return {
            "status": "success",
            "body": body if isinstance(body, dict) else {},
            "request_sent": True,
            "provider_response_received": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        return {
            "status": "failed",
            "error": f"http_{exc.code}:{detail}",
            "request_sent": True,
            "provider_response_received": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - benchmark records transport failures
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}:{str(exc)[:300]}",
            "request_sent": True,
            "provider_response_received": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }


def _openai_text(body: Mapping[str, Any]) -> str:
    direct = str(body.get("output_text") or "").strip()
    if direct:
        return direct
    parts: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, Mapping):
            continue
        for content in item.get("content") or []:
            if isinstance(content, Mapping) and content.get("type") in {
                "output_text",
                "text",
            }:
                parts.append(str(content.get("text") or ""))
    return "".join(parts).strip()


def _invoke_openai(
    model: str,
    prompt: str,
    max_output_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    api_key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return {
            "status": "not_configured",
            "error": "missing OPENAI_API_KEY",
            "request_sent": False,
            "provider_response_received": False,
            "latency_ms": 0,
        }
    transport = _post_json(
        "https://api.openai.com/v1/responses",
        {
            "model": model,
            "input": prompt,
            "max_output_tokens": max(16, int(max_output_tokens)),
            "reasoning": {"effort": "low"},
        },
        {"Authorization": f"Bearer {api_key}"},
        timeout_seconds,
    )
    if transport.get("status") != "success":
        return transport
    body = transport.get("body") if isinstance(transport.get("body"), Mapping) else {}
    usage = body.get("usage") if isinstance(body.get("usage"), Mapping) else {}
    text = _openai_text(body)
    return {
        **transport,
        "evidence_origin": "provider_live",
        "synthetic": False,
        "status": "success" if text else "empty_response",
        "response_model": str(body.get("model") or ""),
        "text": text,
        "input_tokens": _nonnegative_int(usage.get("input_tokens")),
        "output_tokens": _nonnegative_int(usage.get("output_tokens")),
    }


def _invoke_anthropic(
    model: str,
    prompt: str,
    max_output_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    api_key = str(os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        return {
            "status": "not_configured",
            "error": "missing ANTHROPIC_API_KEY",
            "request_sent": False,
            "provider_response_received": False,
            "latency_ms": 0,
        }
    transport = _post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "model": model,
            "max_tokens": max(16, int(max_output_tokens)),
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": "low"},
        },
        {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout_seconds,
    )
    if transport.get("status") != "success":
        return transport
    body = transport.get("body") if isinstance(transport.get("body"), Mapping) else {}
    usage = body.get("usage") if isinstance(body.get("usage"), Mapping) else {}
    text = "".join(
        str(block.get("text") or "")
        for block in (body.get("content") or [])
        if isinstance(block, Mapping) and block.get("type") == "text"
    ).strip()
    stop_reason = str(body.get("stop_reason") or "")
    return {
        **transport,
        "evidence_origin": "provider_live",
        "synthetic": False,
        "status": "refusal"
        if stop_reason == "refusal"
        else ("success" if text else "empty_response"),
        "response_model": str(body.get("model") or ""),
        "stop_reason": stop_reason or None,
        "text": text,
        "input_tokens": _nonnegative_int(usage.get("input_tokens")),
        "output_tokens": _nonnegative_int(usage.get("output_tokens")),
    }


def invoke_live_model(
    binding: str,
    prompt: str,
    *,
    max_output_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    provider, model = split_binding(binding)
    if provider == "openai":
        return _invoke_openai(model, prompt, max_output_tokens, timeout_seconds)
    if provider == "anthropic":
        return _invoke_anthropic(model, prompt, max_output_tokens, timeout_seconds)
    return {
        "status": "unsupported_provider",
        "error": f"unsupported provider: {provider or 'missing'}",
        "request_sent": False,
        "provider_response_received": False,
        "latency_ms": 0,
    }


def _model_matches(requested_binding: str, response_model: str) -> bool:
    _, requested_model = split_binding(requested_binding)
    return response_model_matches(requested_model, response_model)


def _evaluate_model_run(
    model_run: Mapping[str, Any],
    result: Mapping[str, Any],
    fixture: Mapping[str, Any],
    *,
    live_requested: bool,
    evaluation_as_of: str | None = None,
) -> dict[str, Any]:
    binding = str(model_run["binding"])
    raw_status = str(result.get("status") or "failed")
    status = (
        raw_status if raw_status in _SAFE_RESULT_STATUSES else "invalid_result_status"
    )
    request_sent = result.get("request_sent") is True
    response_received = result.get("provider_response_received") is True
    response_model = str(result.get("response_model") or "")
    provider_live_evidence = (
        live_requested
        and str(result.get("evidence_origin") or "") == "provider_live"
        and result.get("synthetic") is not True
    )
    exact_model_probed = (
        provider_live_evidence
        and request_sent
        and response_received
        and status == "success"
        and _model_matches(binding, response_model)
    )
    expected = fixture.get("expected") if isinstance(fixture.get("expected"), Mapping) else {}
    expected_metrics = expected.get("metrics") if isinstance(expected.get("metrics"), Mapping) else {}
    expected_sources = expected.get("source_ids") if isinstance(expected.get("source_ids"), list) else []

    if status in {"dry_run", "blocked_by_policy"}:
        reason = "dry_run_no_provider_call" if status == "dry_run" else "policy_blocked"
        schema = _empty_dimension(reason)
        factual = _empty_dimension(reason)
        source = _empty_dimension(reason)
        output = None
        response_hash = None
    else:
        text = str(result.get("text") or "")
        output, parse_error = _extract_json_object(text)
        schema = _schema_result(output, expected_metrics, parse_error)
        factual = _factual_result(output, expected_metrics)
        source = _source_result(output, [str(item) for item in expected_sources])
        response_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None

    row = {
        "role": model_run["role"],
        "model": binding,
        "status": status,
        "invoked": request_sent,
        "availability": {
            "status": "probed" if exact_model_probed else "unverified",
            "response_model": response_model if exact_model_probed else None,
            "observed_response_model_sha256": (
                hashlib.sha256(response_model.encode("utf-8")).hexdigest()
                if response_model
                else None
            ),
            "evidence": "provider_response_model_match" if exact_model_probed else None,
        },
        "schema": schema,
        "factual": factual,
        "source": source,
        "safety": {
            "passed": result.get("safety_passed") is True,
            "status": (
                "passed"
                if result.get("safety_passed") is True
                else "not_evaluated_or_failed"
            ),
        },
        "latency": {
            "milliseconds": _nonnegative_int(result.get("latency_ms"))
            if request_sent
            else None,
        },
        "cost": _cost_result(
            model_run,
            input_tokens=_nonnegative_int(result.get("input_tokens")),
            output_tokens=_nonnegative_int(result.get("output_tokens")),
            request_sent=request_sent,
        ),
        "response_sha256": response_hash,
    }
    if raw_status != status:
        row["provider_status_sha256"] = hashlib.sha256(
            raw_status.encode("utf-8")
        ).hexdigest()
    if result.get("error"):
        row["error"] = {
            "type": "provider_or_transport_error",
            "sha256": hashlib.sha256(
                str(result["error"]).encode("utf-8")
            ).hexdigest(),
        }
    if result.get("stop_reason"):
        row["stop_reason_sha256"] = hashlib.sha256(
            str(result["stop_reason"]).encode("utf-8")
        ).hexdigest()
    provider, model_id = split_binding(binding)
    dataset = fixture.get("evaluation_dataset") if isinstance(fixture.get("evaluation_dataset"), Mapping) else {}
    evaluated_at = str(evaluation_as_of or "") or (
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if request_sent
        else str(dataset.get("as_of") or "1970-01-01T00:00:00Z")
    )
    case_id = str(fixture.get("fixture_id") or "custom")
    sample_failures: list[str] = []
    if status != "success":
        sample_failures.append(f"status:{status}")
    for dimension in ("schema", "factual", "source"):
        if row[dimension].get("passed") is not True:
            sample_failures.append(f"{dimension}_failed")
    if result.get("safety_passed") is not True:
        sample_failures.append("safety_not_evaluated_or_failed")
    evaluation_artifact = build_model_evaluation_artifact(
        binding=binding,
        benchmark_version=BENCHMARK_VERSION,
        dataset_version=str(dataset.get("version") or ""),
        dataset_sha256=_fixture_digest(fixture),
        dataset_as_of=str(dataset.get("as_of") or ""),
        dataset_provenance=str(dataset.get("provenance") or ""),
        dataset_actual=dataset.get("actual") is True,
        dataset_synthetic=dataset.get("synthetic") is not False,
        evaluated_at=evaluated_at,
        provenance=f"benchmark:{BENCHMARK_VERSION}:{binding}",
        samples=[
            {
                "sample_id": f"{case_id}:{binding}",
                "case_id": case_id,
                "binding": binding,
                "provider": provider,
                "model": model_id,
                "response_model": response_model,
                "evidence_origin": result.get("evidence_origin") or "not_provider_live",
                "synthetic": not provider_live_evidence,
                "request_sent": request_sent,
                "provider_response_received": response_received,
                "status": status,
                "schema_passed": row["schema"].get("passed") is True,
                "factual_passed": row["factual"].get("passed") is True,
                "source_passed": row["source"].get("passed") is True,
                "safety_passed": result.get("safety_passed") is True,
                "latency_ms": row["latency"]["milliseconds"],
                "response_sha256": response_hash,
                "failure_reasons": sample_failures,
            }
        ],
    )
    readiness_evidence = {
        "probe": {
            "version": MODEL_PROBE_EVIDENCE_VERSION,
            "status": "success" if exact_model_probed else status,
            "live": bool(provider_live_evidence and request_sent),
            "synthetic": not provider_live_evidence,
            "request_sent": request_sent,
            "provider_response_received": response_received,
            "provider": provider,
            "model": model_id,
            "response_model": response_model,
            "response_sha256": response_hash,
            "evaluation_artifact_sha256": evaluation_artifact["integrity"]["sha256"],
            "as_of": evaluated_at if request_sent else None,
            "provenance": f"benchmark_response_sha256:{response_hash}" if response_hash else None,
        },
        "evaluation": {"artifact": evaluation_artifact},
    }
    row["readiness"] = assess_model_readiness(
        resolve_model_binding(provider, model_id, runtime_availability={}),
        configured=request_sent,
        evidence=readiness_evidence,
        as_of=evaluated_at,
    )
    row["_unsigned_evidence"] = readiness_evidence
    return row


def run_benchmark(
    fixture: Mapping[str, Any] | None = None,
    *,
    live: bool = False,
    max_output_tokens: int = 512,
    timeout_seconds: float = 90.0,
    live_invoker: LiveInvoker | None = None,
    evaluation_as_of: str | None = None,
) -> dict[str, Any]:
    source_fixture = DEFAULT_FIXTURE if fixture is None else fixture
    benchmark_fixture = deepcopy(dict(source_fixture))
    readiness = benchmark_fixture.get("data_readiness")
    readiness = readiness if isinstance(readiness, Mapping) else {}
    sources = benchmark_fixture.get("sources")
    sources = sources if isinstance(sources, list) else []
    dataset_metadata = (
        benchmark_fixture.get("evaluation_dataset")
        if isinstance(benchmark_fixture.get("evaluation_dataset"), Mapping)
        else {}
    )
    # This script is the explicit runtime-verification lane. ``--live`` may
    # probe registered/priced models whose availability is not known yet; the
    # benchmark response-model check then creates the evidence. Normal report
    # generation never receives this exception.
    policy = evaluate_report_model_policy(
        readiness,
        sources,
        evidence_as_of=dataset_metadata.get("as_of"),
        allow_runtime_probe=bool(live),
    )
    policy_payload = policy.to_dict()
    prompt = build_prompt(benchmark_fixture)
    invoker = live_invoker or invoke_live_model

    rows: list[dict[str, Any]] = []
    for model_run in MODEL_RUNS:
        if not live:
            raw_result: Mapping[str, Any] = {
                "status": "dry_run",
                "request_sent": False,
                "provider_response_received": False,
            }
        elif (
            not policy.provider_calls_allowed
            or str(model_run["binding"]) not in policy.selected_models
        ):
            raw_result = {
                "status": "blocked_by_policy",
                "request_sent": False,
                "provider_response_received": False,
            }
        else:
            try:
                invoked_result = invoker(
                    str(model_run["binding"]),
                    prompt,
                    max_output_tokens=max(16, int(max_output_tokens)),
                    timeout_seconds=max(1.0, float(timeout_seconds)),
                )
                raw_result = (
                    invoked_result
                    if isinstance(invoked_result, Mapping)
                    else {
                        "status": "invalid_invoker_result",
                        "error": "live invoker returned a non-object result",
                        "request_sent": False,
                        "provider_response_received": False,
                        "latency_ms": 0,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - keep both model rows auditable
                raw_result = {
                    "status": "invoker_exception",
                    "error": f"{type(exc).__name__}:{str(exc)[:300]}",
                    "request_sent": False,
                    "provider_response_received": False,
                    "latency_ms": 0,
                }
        rows.append(
            _evaluate_model_run(
                model_run,
                raw_result,
                benchmark_fixture,
                live_requested=bool(live),
                evaluation_as_of=evaluation_as_of,
            )
        )

    unsigned_evidence_by_binding = {
        str(row["model"]): row.pop("_unsigned_evidence") for row in rows
    }

    probed_models = [
        row["model"]
        for row in rows
        if row["readiness"]["probed"] is True
    ]
    production_ready_models = [
        row["model"] for row in rows if row["readiness"]["production_ready"] is True
    ]
    provider_calls = sum(1 for row in rows if row["invoked"])
    quality_passed = all(
        row[dimension].get("passed") is True
        for row in rows
        for dimension in ("schema", "factual", "source", "safety")
    )
    probe_quality_passed = bool(
        live
        and policy.provider_calls_allowed
        and len(probed_models) == len(MODEL_RUNS)
        and quality_passed
    )
    benchmark_passed = (
        None
        if not live
        else bool(
            probe_quality_passed
            and len(production_ready_models) == len(MODEL_RUNS)
        )
    )
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "fixture_id": str(benchmark_fixture.get("fixture_id") or "custom"),
        "fixture_sha256": _fixture_digest(benchmark_fixture),
        "mode": "live" if live else "dry_run",
        "live_requested": bool(live),
        "provider_calls": provider_calls,
        "policy": policy_payload,
        "models": rows,
        "probed_models": probed_models,
        "all_models_probed": len(probed_models) == len(MODEL_RUNS),
        "probe_quality_passed": probe_quality_passed,
        "production_ready_models": production_ready_models,
        "all_models_production_ready": len(production_ready_models) == len(MODEL_RUNS),
        "claim_status": (
            "validated"
            if live and len(production_ready_models) == len(MODEL_RUNS)
            else "descriptive_only"
        ),
        "benchmark_passed": benchmark_passed,
        "offline_signing_bundle": {
            "version": SIGNING_BUNDLE_VERSION,
            "benchmark_version": BENCHMARK_VERSION,
            "fixture_sha256": _fixture_digest(benchmark_fixture),
            "attestation_status": "unsigned",
            "required_roles": {
                "evaluation": "evaluation",
                "exact_probe": "exact_probe",
            },
            "requires_distinct_key_ids": True,
            "requires_distinct_public_keys": True,
            "evidence_by_binding": unsigned_evidence_by_binding,
        },
        "required_result_fields": [
            "schema",
            "factual",
            "source",
            "safety",
            "latency",
            "cost",
            "readiness",
        ],
    }


from scripts.vkpi_report_model_benchmark_bundle import (  # noqa: E402
    verify_signed_evidence_bundle,
)


from scripts.vkpi_report_model_benchmark_io import (  # noqa: E402
    _load_json_object,
    _unique_json_object,
    _write_private_text,
    load_fixture,
    load_signed_evidence_bundle,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live",
        action="store_true",
        help="Call both registered provider models after all policy gates pass",
    )
    mode.add_argument(
        "--signed-evidence",
        default="",
        help="Verify an externally dual-signed offline bundle without provider calls",
    )
    parser.add_argument("--fixture", default="", help="Optional JSON fixture path")
    parser.add_argument("--json-out", default="", help="Optional JSON result path")
    parser.add_argument(
        "--evaluation-as-of",
        default="",
        help="Optional deterministic evaluation timestamp for an offline signing round",
    )
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    fixture = load_fixture(args.fixture)
    report = (
        verify_signed_evidence_bundle(
            fixture,
            load_signed_evidence_bundle(args.signed_evidence),
            verification_as_of=None,
        )
        if args.signed_evidence
        else run_benchmark(
            fixture,
            live=args.live,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout_seconds,
            evaluation_as_of=args.evaluation_as_of or None,
        )
    )
    rendered = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    if args.json_out:
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_private_text(output_path, rendered + "\n")
    stdout_out(rendered)
    if not args.live and not args.signed_evidence:
        return 0
    return 0 if report["benchmark_passed"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
