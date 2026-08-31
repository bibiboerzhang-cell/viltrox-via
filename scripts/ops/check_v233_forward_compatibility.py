#!/usr/bin/env python3
"""Fail-closed app rollback audit for migration 233 -> a newer V-KPI schema.

The atomic release layout restores application bytes, units and environment;
it deliberately does not restore the database.  A migration can therefore be
declared forward-compatible only when the v233 application can safely serve on
the migrated schema without losing truth, privacy or tenant boundaries.

This tool is read-only.  It emits aggregate counts only and never prints the
database URL, credentials, row values or user content.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

import psycopg

if __package__:
    from .atomic_release_shared import (
        FORWARD_COMPATIBILITY_POLICY_ID,
        _forward_compatibility_evidence,
    )
    from .atomic_release_units import LayoutError
else:
    from atomic_release_shared import (  # type: ignore[no-redef]
        FORWARD_COMPATIBILITY_POLICY_ID,
        _forward_compatibility_evidence,
    )
    from atomic_release_units import LayoutError  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "migrations"
V233 = "233_vkpi_gtm_outcomes_inbox_unique.sql"
TRANSACTION_CONTROL = re.compile(
    r"(?mi)^\s*(?:BEGIN(?:\s+TRANSACTION)?|COMMIT(?:\s+TRANSACTION)?)\s*;"
)

# ``compatible`` means the v233 application can keep serving on the new schema.
# ``conditional`` requires a fresh check immediately before restoring v233.
# ``incompatible`` requires a truth-aware fallback release or database restore.
POLICY: dict[str, dict[str, Any]] = {
    "234_vkpi_market_prd_referrals.sql": {"rollback": "compatible"},
    "235_vkpi_official_catalog_sync.sql": {"rollback": "compatible"},
    "236_vkpi_search_retrieval_perf.sql": {"rollback": "compatible"},
    "237_vkpi_action_execution_claim.sql": {
        "rollback": "conditional",
        "conditions": ["executing_actions_zero"],
        "reason": "v233 cannot reconcile an executing side-effect claim",
    },
    "238_vkpi_official_catalog_sync_compat.sql": {"rollback": "compatible"},
    "239_vkpi_kol_search_history_archive.sql": {
        "rollback": "conditional",
        "conditions": ["archived_search_sessions_zero"],
        "reason": "v233 does not filter archived history",
    },
    "240_vkpi_inventory_quantity_truth.sql": {
        "rollback": "incompatible",
        "reason": "v233 treats unverified inventory quantity as operational truth",
    },
    "241_vkpi_dealer_source_truth.sql": {
        "rollback": "incompatible",
        "reason": "v233 cannot preserve public-listing versus authorization truth",
    },
    "242_vkpi_dealer_public_contact.sql": {"rollback": "compatible"},
    "243_vkpi_event_radar.sql": {"rollback": "compatible"},
    "244_vkpi_event_radar_truth_scope.sql": {
        "rollback": "conditional",
        "conditions": [
            "nonlegacy_event_rows_zero",
            "memberships_outside_legacy_org_zero",
        ],
        "reason": "v233 event reads are not organization scoped",
    },
    "245_vkpi_staff_organization_membership_backfill.sql": {
        "rollback": "compatible"
    },
    "246_vkpi_worker_runtime_identity.sql": {"rollback": "compatible"},
    "247_apify_jobs_active_idempotency.sql": {
        "rollback": "conditional",
        "conditions": ["active_idempotency_conflicts_zero"],
        "reason": "the partial unique index cannot be created over conflicts",
    },
    "248_vkpi_dealer_event_source_passports.sql": {"rollback": "compatible"},
    "249_vkpi_scheduler_fleet_guard.sql": {"rollback": "compatible"},
    "250_vkpi_marketing_advisor_memory.sql": {"rollback": "compatible"},
    "251_vkpi_scheduler_fire_recovery.sql": {"rollback": "compatible"},
    "252_vkpi_advisor_turn_claims.sql": {"rollback": "compatible"},
    "253_vkpi_product_cost_truth.sql": {
        "rollback": "incompatible",
        "reason": "v233 can treat unverified reference product costs as actual truth",
    },
    "254_vkpi_provider_execution_fencing.sql": {
        "rollback": "conditional",
        "conditions": ["open_apify_reservations_zero", "active_provider_claims_zero"],
        "reason": "v233 cannot resume or fence active paid-provider execution state",
    },
    "255_vkpi_shopify_business_truth.sql": {
        "rollback": "incompatible",
        "reason": "v233 treats legacy or non-native Shopify snapshots and stale financial materializations as actual truth",
    },
    "256_vkpi_financial_artifact_invalidation.sql": {
        "rollback": "incompatible",
        "reason": "v233 can restore or download financial artifacts withdrawn by the current truth contract",
    },
    "305_vkpi_kol_pool_language_inferred.sql": {
        "rollback": "compatible",
        "structural_policy": FORWARD_COMPATIBILITY_POLICY_ID,
        "reason": "only reviewed nullable/defaultless inferred-language columns are added",
    },
    "306_vkpi_product_persona_term_performance.sql": {
        "rollback": "compatible",
        "structural_policy": FORWARD_COMPATIBILITY_POLICY_ID,
        "reason": "only the reviewed nullable/defaultless persona evidence column is added",
    },
}

CONDITION_FACTS = {
    "executing_actions_zero": "executing_actions",
    "archived_search_sessions_zero": "archived_search_sessions",
    "nonlegacy_event_rows_zero": "nonlegacy_event_rows",
    "memberships_outside_legacy_org_zero": "memberships_outside_legacy_org",
    "active_idempotency_conflicts_zero": "active_idempotency_conflict_groups",
    "open_apify_reservations_zero": "open_apify_reservations",
    "active_provider_claims_zero": "active_provider_claims",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _has_column(conn: psycopg.Connection[Any], table: str, column: str) -> bool:
    row = conn.execute(
        """
        SELECT EXISTS(
          SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name=%s AND column_name=%s
        )
        """,
        (table, column),
    ).fetchone()
    return bool(row and row[0])


def _has_table(conn: psycopg.Connection[Any], table: str) -> bool:
    row = conn.execute(
        "SELECT to_regclass(%s) IS NOT NULL",
        (f"public.{table}",),
    ).fetchone()
    return bool(row and row[0])


def _count(conn: psycopg.Connection[Any], statement: str) -> int:
    row = conn.execute(statement).fetchone()
    return int(row[0] if row else 0)


def collect_facts(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """Collect only bounded aggregate facts required by the rollback policy."""

    conn.execute("SET TRANSACTION READ ONLY")
    facts = {
        "executing_actions": _count(
            conn, "SELECT COUNT(*) FROM vkpi_action_inbox WHERE status='executing'"
        ),
        "archived_search_sessions": 0,
        "nonlegacy_event_rows": 0,
        "memberships_outside_legacy_org": _count(
            conn,
            "SELECT COUNT(*) FROM organization_members WHERE organization_id<>1",
        ),
        "active_idempotency_conflict_groups": _count(
            conn,
            """
            SELECT COUNT(*) FROM (
              SELECT idempotency_key
              FROM apify_jobs
              WHERE idempotency_key IS NOT NULL
                AND idempotency_key<>''
                AND status IN ('queued','running')
              GROUP BY idempotency_key HAVING COUNT(*)>1
            ) AS conflicts
            """,
        ),
        "inventory_rows": _count(conn, "SELECT COUNT(*) FROM vkpi_inventory"),
        "dealer_rows": _count(conn, "SELECT COUNT(*) FROM vkpi_dealers"),
        "open_apify_reservations": 0,
        "active_provider_claims": 0,
    }
    if _has_column(conn, "vkpi_kol_search_sessions", "archived_at"):
        facts["archived_search_sessions"] = _count(
            conn,
            "SELECT COUNT(*) FROM vkpi_kol_search_sessions WHERE archived_at IS NOT NULL",
        )
    if _has_column(conn, "vkpi_events", "organization_id"):
        facts["nonlegacy_event_rows"] = _count(
            conn, "SELECT COUNT(*) FROM vkpi_events WHERE organization_id<>1"
        )
    if _has_table(conn, "vkpi_apify_budget_reservations"):
        facts["open_apify_reservations"] = _count(
            conn,
            "SELECT COUNT(*) FROM vkpi_apify_budget_reservations WHERE state IN ('reserved','provider_started','unknown')",
        )
    if _has_table(conn, "vkpi_provider_execution_claims"):
        facts["active_provider_claims"] = _count(
            conn,
            "SELECT COUNT(*) FROM vkpi_provider_execution_claims WHERE state='active'",
        )
    conn.rollback()
    return facts


def evaluate(
    pending: Iterable[str],
    *,
    facts: dict[str, int],
    phase: str,
) -> dict[str, Any]:
    names = list(pending)
    checks: list[dict[str, Any]] = []
    migrations: list[dict[str, Any]] = []
    safe = True

    if len(names) != len(set(names)) or names != sorted(names):
        checks.append({"check": "ordered_unique_pending_manifest", "passed": False})
        safe = False
    else:
        checks.append({"check": "ordered_unique_pending_manifest", "passed": True})

    for name in names:
        policy = POLICY.get(name)
        path = MIGRATIONS_DIR / name
        row: dict[str, Any] = {
            "migration": name,
            "known_policy": policy is not None,
            "file_present": path.is_file(),
        }
        if policy is None or not path.is_file():
            row["verdict"] = "blocked"
            row["reason"] = "unknown_or_missing_migration"
            migrations.append(row)
            safe = False
            continue
        has_control = bool(
            TRANSACTION_CONTROL.search(path.read_text(encoding="utf-8"))
        )
        row["runner_owned_transaction"] = not has_control
        row["policy"] = policy["rollback"]
        row["reason"] = str(policy.get("reason") or "")
        if has_control:
            row["verdict"] = "blocked"
            row["reason"] = "forward_file_contains_transaction_control"
            safe = False
        elif policy.get("structural_policy"):
            try:
                evidence = _forward_compatibility_evidence(name)
            except LayoutError:
                row["verdict"] = "blocked"
                row["reason"] = "forward_structural_policy_failed"
                safe = False
            else:
                row["verdict"] = "compatible"
                row["structural_evidence"] = evidence
        elif policy["rollback"] == "incompatible":
            row["verdict"] = "blocked"
            safe = False
        elif policy["rollback"] == "conditional":
            condition_rows = []
            conditions_pass = True
            for condition in policy.get("conditions") or []:
                fact = CONDITION_FACTS[condition]
                observed = int(facts.get(fact, -1))
                passed = observed == 0
                condition_rows.append(
                    {
                        "condition": condition,
                        "fact": fact,
                        "observed": observed,
                        "passed": passed,
                    }
                )
                conditions_pass = conditions_pass and passed
            row["conditions"] = condition_rows
            # A pre-deploy zero cannot prove the state immediately before a
            # later rollback.  The same gate must pass in prerollback phase.
            row["verdict"] = (
                "compatible"
                if conditions_pass and phase == "prerollback"
                else "conditional_unproven"
            )
            if row["verdict"] != "compatible":
                safe = False
        else:
            row["verdict"] = "compatible"
        migrations.append(row)

    incompatible = [
        row["migration"] for row in migrations if row.get("verdict") == "blocked"
    ]
    conditional = [
        row["migration"]
        for row in migrations
        if row.get("verdict") == "conditional_unproven"
    ]
    return {
        "schema_version": 1,
        "gate": "v233_application_forward_compatibility",
        "generated_at": _utc_now(),
        "phase": phase,
        "from_migration": V233,
        "pending_migrations": names,
        "facts": {key: int(value) for key, value in sorted(facts.items())},
        "checks": checks,
        "migrations": migrations,
        "decision": {
            "safe_to_declare_forward_compatible": bool(safe and names),
            "incompatible_migrations": incompatible,
            "conditional_migrations": conditional,
            "claim_status": "verified_compatible" if safe and names else "blocked",
        },
    }


def _env_value(path: Path, key: str) -> str:
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pending-csv", required=True)
    parser.add_argument("--phase", choices=("predeploy", "prerollback"), default="predeploy")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--database-url-key", default="DATABASE_URL")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    dsn = _env_value(args.env_file, args.database_url_key)
    if not dsn:
        parser.error("database URL is not configured")
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        facts = collect_facts(conn)
    report = evaluate(_csv(args.pending_csv), facts=facts, phase=args.phase)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if report["decision"]["safe_to_declare_forward_compatible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
