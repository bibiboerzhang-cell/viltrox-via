#!/usr/bin/env python3
"""One approved LLM review of a public KOL request and sample summary.

Preparation is offline. This does not start Apify, read creator inventory,
promote production readiness, or change global budgets. Execution reuses the
reviewed probe and its exact model, output, reservation and accounting limits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.ops.kol_live_query_eval import (  # noqa: E402
    BASE, digest, evaluation_budget, fcntl, save, validate_environment,
)

SOURCES = (
    "scripts/ops/kol_precision_review.py",
    "scripts/ops/kol_live_query_eval.py",
    "scripts/ops/kol_live_llm_probe.py",
    "backend/app/domains/kol/search_plan_semantics.py",
    "backend/app/platform/llm_budget_reservations.py",
)
MAX_INPUT_BYTES = 2048
FIXED = {
    "version": 1, "execution_class": "local_kol_precision_review",
    "review_only": True, "llm_binding": "openai/gpt-5.6-luna",
    "llm_max_calls": 1, "llm_max_output_tokens": 1024,
    "llm_reserve_usd": "0.050000", "llm_scope_cap_usd": "0.10",
    "max_input_bytes": MAX_INPUT_BYTES, "apify_max_starts": 0,
    "db_target": "127.0.0.1:54329/viltrox2",
    "production_authorized": False, "business_inventory_reads": 0,
    "business_inventory_writes": 0,
}


def implementation() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def _input_hash(query: object) -> str:
    if not isinstance(query, str) or not query.strip() or len(query.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("input_out_of_bounds")
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def prepare(input_path: Path) -> Path:
    with input_path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input_out_of_bounds")
    query = raw.decode("utf-8")
    query_hash = _input_hash(query)
    now = int(time.time())
    plan = {**FIXED, "nonce": secrets.token_hex(16), "created_at": now,
            "expires_at": now + 900, "query": query, "input_sha256": query_hash,
            "implementation": implementation()}
    plan_id = digest(plan)
    folder = BASE / plan_id
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    save(folder / "plan.json", plan)
    return folder / "plan.json"


def validate_plan(path: Path, approval: str) -> tuple[dict, str]:
    if (BASE.is_symlink() or path.is_symlink() or path.parent.is_symlink()
            or path.stat().st_size > 32768):
        raise ValueError("plan_path_invalid")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("plan_contract_or_source_changed")
    plan_id = digest(plan)
    expected_path = BASE / plan_id / "plan.json"
    if approval != plan_id or path.absolute() != expected_path.absolute():
        raise ValueError("plan_authorization_mismatch")
    expected = {**FIXED, "implementation": implementation()}
    dynamic = {"nonce", "created_at", "expires_at", "query", "input_sha256"}
    if (set(plan) != set(expected) | dynamic
            or any(type(plan.get(key)) is not type(value) or plan[key] != value
                   for key, value in expected.items())):
        raise ValueError("plan_contract_or_source_changed")
    if not isinstance(plan["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", plan["nonce"]):
        raise ValueError("plan_nonce_invalid")
    if (type(plan["created_at"]) is not int or type(plan["expires_at"]) is not int
            or not plan["created_at"] <= time.time() < plan["expires_at"]
            or plan["expires_at"] != plan["created_at"] + 900):
        raise ValueError("plan_expired_or_invalid")
    if plan["input_sha256"] != _input_hash(plan["query"]):
        raise ValueError("input_hash_mismatch")
    return plan, plan_id


def execute(path: Path, approval: str) -> dict:
    plan, plan_id = validate_plan(path, approval)
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from app.core import config
    validate_environment(config)
    from app.db.connection import db_connection_sync_scope, get_conn
    from scripts.ops.kol_live_llm_probe import run_probe

    folder = path.parent
    report = {"plan_id": plan_id, "input_sha256": plan["input_sha256"],
              "claim_status": "descriptive_only", "status": "started",
              "review_only": True, "production_authorized": False,
              "business_inventory_reads": 0, "business_inventory_writes": 0,
              "apify_starts": 0, "started_at": time.time()}
    lock_fd = os.open(BASE / "scope.lock", os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Recheck expiry/source after obtaining the shared scope lock.
        validate_plan(path, approval)
        marker = os.open(folder / "execution.started", os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(marker, "w") as stream:
            stream.write(plan_id + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        save(folder / "receipt.json", report)
        try:
            with db_connection_sync_scope():
                with evaluation_budget(get_conn(), report):
                    report["llm"] = run_probe(plan["query"], folder, plan_id)
                    save(folder / "receipt.json", report)
                    llm = report["llm"]
                    if (llm.get("status") != "success" or llm.get("accounting_verified") is not True
                            or llm.get("provider_calls_performed") != 1
                            or llm.get("reservation_state") != "settled"):
                        raise RuntimeError("llm_review_not_verified")
                    report["status"] = "completed"
        except Exception as exc:
            report.update(status="stopped", error_type=type(exc).__name__)
            # Provider exception messages may contain credentials or URLs.
            code = str(exc)
            if code in {"llm_review_not_verified", "micro_usd_schema_required",
                        "evaluation_scope_not_closed_requires_reconciliation",
                        "prior_evaluation_charge_unresolved", "evaluation_budget_close_failed",
                        "evaluation_budget_close_readback_failed"}:
                report["error_code"] = code
        finally:
            report["finished_at"] = time.time()
            save(folder / "receipt.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--approve", default="")
    args = parser.parse_args()
    if args.prepare:
        if args.input is None or args.approve:
            parser.error("--prepare requires --input and no --approve")
        path = prepare(args.input)
        sys.stdout.write(json.dumps({"plan": str(path), "approval": path.parent.name}) + "\n")
        return 0
    if args.input is not None or not args.approve:
        parser.error("--execute requires --approve and no --input")
    result = execute(args.execute, args.approve)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
