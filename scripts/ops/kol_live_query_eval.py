#!/usr/bin/env python3
"""Explicit, one-shot local LLM -> fresh Apify discovery; never pool enrollment.

Prepare is offline. Execute requires the exact unexpired plan digest, an
unchanged implementation, a one-use marker and real local budget accounting.
This command does not grant production model readiness or enable schedulers.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import sys
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "runtime/ops/kol-live-eval"
SCOPE = "cron:kol_live_query_eval"
QUERY = "Find English-speaking YouTube creators for US-market street and night photography collaborations. Prioritize practical photography tutorials and field reviews, not retailers or brand channels. No particular SKU is required. Do not infer a creator's country or audience geography from a search query."
SOURCES = (
    "scripts/ops/kol_live_query_eval.py",
    "scripts/ops/kol_live_llm_probe.py",
    "backend/app/domains/kol/search_plan_semantics.py",
    "scripts/ops/kol_live_apify_probe.py",
    "backend/app/domains/costs/budget_guard.py",
    "backend/app/domains/costs/apify_cost_reconciliation.py",
    "backend/app/domains/costs/apify_cost_reconciliation_job.py",
    "backend/app/platform/llm_budget_reservations.py",
)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def implementation() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def save(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def prepare() -> Path:
    now = int(time.time())
    plan = {
        "version": 1, "nonce": secrets.token_hex(16), "created_at": now,
        "expires_at": now + 900, "query": QUERY,
        "llm_binding": "openai/gpt-5.6-luna", "llm_max_calls": 1,
        "llm_scope_cap_usd": "0.10", "apify_max_starts": 1,
        "apify_actor": "streamers/youtube-scraper", "apify_build": "0.0.290",
        "apify_max_total_charge_usd": "0.10", "apify_timeout_seconds": 120,
        "max_queries": 2, "max_videos_per_query": 5,
        "db_target": "127.0.0.1:54329/viltrox2",
        "production_authorized": False, "business_inventory_writes": False,
        "implementation": implementation(),
    }
    plan_id = digest(plan)
    folder = BASE / plan_id
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    save(folder / "plan.json", plan)
    return folder / "plan.json"


def validate_plan(path: Path, approval: str) -> tuple[dict, str]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan_id = digest(plan)
    if approval != plan_id or path.resolve() != (BASE / plan_id / "plan.json").resolve():
        raise ValueError("plan_authorization_mismatch")
    now = time.time()
    if not plan["created_at"] <= now < plan["expires_at"] <= plan["created_at"] + 900:
        raise ValueError("plan_expired_or_invalid")
    expected = {
        "version": 1, "query": QUERY, "llm_binding": "openai/gpt-5.6-luna",
        "llm_max_calls": 1, "llm_scope_cap_usd": "0.10", "apify_max_starts": 1,
        "apify_actor": "streamers/youtube-scraper", "apify_build": "0.0.290",
        "apify_max_total_charge_usd": "0.10", "apify_timeout_seconds": 120,
        "max_queries": 2, "max_videos_per_query": 5,
        "db_target": "127.0.0.1:54329/viltrox2", "production_authorized": False,
        "business_inventory_writes": False, "implementation": implementation(),
    }
    if any(plan.get(key) != value for key, value in expected.items()):
        raise ValueError("plan_contract_or_source_changed")
    return plan, plan_id


def validate_environment(config: object) -> None:
    url = urlparse(config.DB_RUNTIME_URL)
    if config.IS_PRODUCTION or config.DB_RUNTIME_BACKEND != "postgres":
        raise RuntimeError("local_postgres_evaluation_only")
    if (url.hostname, url.port, url.path) != ("127.0.0.1", 54329, "/viltrox2"):
        raise RuntimeError("evaluation_database_target_mismatch")
    if os.environ.get("VKPI_LLM_GATEWAY_FORCE_OFFLINE", "").lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("force_offline_is_enabled")
    if Decimal(os.environ.get("LLM_MONTHLY_BUDGET_USD", "0")) <= 0:
        raise RuntimeError("monthly_environment_budget_unavailable")


def close_budget(conn: object) -> dict:
    conn.rollback()
    cursor = conn.execute(
        "UPDATE vkpi_provider_budget_caps SET cap_usd=0.000001,hard_stop_at=1 WHERE scope=?", (SCOPE,))
    if cursor.rowcount != 1:
        conn.rollback()
        raise RuntimeError("evaluation_budget_close_failed")
    conn.commit()
    row = dict(conn.execute(
        "SELECT scope,cap_usd,hard_stop_at,current_spend,reset_at FROM vkpi_provider_budget_caps WHERE scope=?",
        (SCOPE,)).fetchone())
    if Decimal(str(row["cap_usd"])) != Decimal("0.000001") or Decimal(str(row["hard_stop_at"])) != 1:
        raise RuntimeError("evaluation_budget_close_readback_failed")
    conn.rollback()
    return row


@contextmanager
def evaluation_budget(conn: object, report: dict):
    columns = conn.execute(
        "SELECT column_name,numeric_scale FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='vkpi_provider_budget_caps' "
        "AND column_name IN ('cap_usd','current_spend')").fetchall()
    if {r["column_name"]: r["numeric_scale"] for r in columns} != {"cap_usd": 6, "current_spend": 6}:
        raise RuntimeError("micro_usd_schema_required")
    conn.execute(
        "INSERT INTO vkpi_provider_budget_caps(scope,cap_usd,hard_stop_at,fallback_action,metadata_json) "
        "VALUES (?,0.000001,1,'block',?) ON CONFLICT(scope) DO NOTHING",
        (SCOPE, '{"execution_class":"local_kol_evaluation","claim_status":"descriptive_only"}'))
    row = dict(conn.execute("SELECT * FROM vkpi_provider_budget_caps WHERE scope=? FOR UPDATE", (SCOPE,)).fetchone())
    if Decimal(str(row["cap_usd"])) != Decimal("0.000001") or Decimal(str(row["hard_stop_at"])) != 1:
        conn.rollback()
        raise RuntimeError("evaluation_scope_not_closed_requires_reconciliation")
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_llm_budget_reservations WHERE cost_scope=? "
        "AND state IN ('reserved','provider_started','unknown')", (SCOPE,)).fetchone()["n"]
    if pending:
        conn.rollback()
        raise RuntimeError("prior_evaluation_charge_unresolved")
    try:
        conn.execute("UPDATE vkpi_provider_budget_caps SET cap_usd=0.10 WHERE scope=?", (SCOPE,))
        conn.commit()
        report["budget_opened"] = True
        yield
    finally:
        try:
            report["budget_closed"] = close_budget(conn)
        except Exception as exc:
            report["budget_close_error_type"] = type(exc).__name__
            report["budget_requires_reconciliation"] = True
            raise


def execute(path: Path, approval: str) -> dict:
    plan, plan_id = validate_plan(path, approval)
    for source in (ROOT, ROOT / "backend"):
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
    from app.core import config
    validate_environment(config)
    from app.db.connection import db_connection_sync_scope, get_conn
    from scripts.ops.kol_live_llm_probe import run_probe as llm_probe
    from scripts.ops.kol_live_apify_probe import run_probe as apify_probe

    folder = path.parent
    report = {"plan_id": plan_id, "claim_status": "descriptive_only", "status": "started",
              "business_inventory_reads": 0, "business_inventory_writes": 0,
              "production_authorized": False, "started_at": time.time()}
    # This process-wide scope lock and durable per-plan marker also reject
    # concurrent/replayed invocations after a timeout or a killed process.
    with (BASE / "scope.lock").open("a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = os.open(folder / "execution.started", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(marker)
        save(folder / "receipt.json", report)
        try:
            with db_connection_sync_scope():
                conn = get_conn()
                with evaluation_budget(conn, report):
                    report["llm"] = llm_probe(plan["query"], folder, plan_id)
                    save(folder / "receipt.json", report)
                    if report["llm"].get("status") != "success":
                        raise RuntimeError("llm_stage_not_successful")
                    apify_started = time.monotonic()
                    report["apify"] = apify_probe(report["llm"]["queries"], folder, plan_id)
                    report["apify"]["elapsed_ms"] = round((time.monotonic() - apify_started) * 1000)
                    report["status"] = "completed" if report["apify"].get("status") == "completed" else "partial"
        except Exception as exc:
            report["status"] = "stopped"
            # Exception messages can contain provider URLs or credentials.
            report["error_type"] = type(exc).__name__
            code = str(exc)
            if code in {
                "micro_usd_schema_required", "evaluation_scope_not_closed_requires_reconciliation",
                "prior_evaluation_charge_unresolved", "llm_stage_not_successful",
                "evaluation_budget_close_failed", "evaluation_budget_close_readback_failed",
            }:
                report["error_code"] = code
        finally:
            report["finished_at"] = time.time()
            save(folder / "receipt.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--execute", type=Path)
    parser.add_argument("--approve", default="")
    args = parser.parse_args()
    if args.prepare and not args.execute:
        path = prepare()
        sys.stdout.write(json.dumps({"plan": str(path), "approval": path.parent.name}) + "\n")
        return 0
    if args.execute and not args.prepare:
        result = execute(args.execute, args.approve)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
        return 0 if result["status"] == "completed" else 2
    parser.error("choose --prepare or --execute with --approve")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
