from __future__ import annotations

from pathlib import Path

from scripts.ops import check_v233_forward_compatibility as gate


ROOT = Path(__file__).resolve().parents[1]
PENDING = sorted(
    name for name in gate.POLICY if 234 <= int(name[:3]) <= 256
)
STRUCTURAL_FORWARD = [
    "305_vkpi_kol_pool_language_inferred.sql",
    "306_vkpi_product_persona_term_performance.sql",
]
ZERO_FACTS = {
    "executing_actions": 0,
    "archived_search_sessions": 0,
    "nonlegacy_event_rows": 0,
    "memberships_outside_legacy_org": 0,
    "active_idempotency_conflict_groups": 0,
    "inventory_rows": 11,
    "dealer_rows": 20,
    "open_apify_reservations": 0,
    "active_provider_claims": 0,
}


def test_policy_exactly_covers_forward_migrations_234_through_256():
    discovered = sorted(
        path.name
        for path in (ROOT / "migrations").glob("*.sql")
        if not path.name.endswith("_down.sql")
        and path.name[:3].isdigit()
        and 234 <= int(path.name[:3]) <= 256
    )
    assert PENDING == discovered


def test_operator_policy_and_deploy_policy_share_305_306_structural_evidence():
    assert sorted(set(gate.POLICY) - set(PENDING)) == STRUCTURAL_FORWARD
    report = gate.evaluate(STRUCTURAL_FORWARD, facts=ZERO_FACTS, phase="predeploy")
    assert report["decision"]["safe_to_declare_forward_compatible"] is True
    for row in report["migrations"]:
        evidence = row["structural_evidence"]
        assert evidence["policy_id"] == "vkpi-additive-nullable-defaultless-v1"
        assert [item["name"] for item in evidence["migrations"]] == [
            row["migration"]
        ]


def test_all_policy_files_leave_transaction_to_runner():
    for name in PENDING:
        sql = (ROOT / "migrations" / name).read_text(encoding="utf-8")
        assert gate.TRANSACTION_CONTROL.search(sql) is None, name


def test_full_v233_rollback_declaration_fails_closed_on_truth_contracts():
    report = gate.evaluate(PENDING, facts=ZERO_FACTS, phase="prerollback")
    assert report["decision"]["safe_to_declare_forward_compatible"] is False
    assert report["decision"]["incompatible_migrations"] == [
        "240_vkpi_inventory_quantity_truth.sql",
        "241_vkpi_dealer_source_truth.sql",
        "253_vkpi_product_cost_truth.sql",
        "255_vkpi_shopify_business_truth.sql",
        "256_vkpi_financial_artifact_invalidation.sql",
    ]


def test_predeploy_cannot_preapprove_conditions_that_may_change_after_cutover():
    report = gate.evaluate(
        [
            "237_vkpi_action_execution_claim.sql",
            "239_vkpi_kol_search_history_archive.sql",
            "244_vkpi_event_radar_truth_scope.sql",
            "247_apify_jobs_active_idempotency.sql",
        ],
        facts=ZERO_FACTS,
        phase="predeploy",
    )
    assert report["decision"]["safe_to_declare_forward_compatible"] is False
    assert len(report["decision"]["conditional_migrations"]) == 4


def test_prerollback_zero_state_can_satisfy_conditional_migrations():
    report = gate.evaluate(
        [
            "237_vkpi_action_execution_claim.sql",
            "239_vkpi_kol_search_history_archive.sql",
            "244_vkpi_event_radar_truth_scope.sql",
            "247_apify_jobs_active_idempotency.sql",
        ],
        facts=ZERO_FACTS,
        phase="prerollback",
    )
    assert report["decision"]["safe_to_declare_forward_compatible"] is True
    assert report["decision"]["conditional_migrations"] == []


def test_prerollback_nonzero_state_blocks_conditional_migration():
    facts = dict(ZERO_FACTS, nonlegacy_event_rows=1)
    report = gate.evaluate(
        ["244_vkpi_event_radar_truth_scope.sql"],
        facts=facts,
        phase="prerollback",
    )
    assert report["decision"]["safe_to_declare_forward_compatible"] is False
    row = report["migrations"][0]
    assert row["verdict"] == "conditional_unproven"
    assert any(not condition["passed"] for condition in row["conditions"])


def test_unknown_or_unordered_manifest_is_rejected():
    report = gate.evaluate(
        ["999_unknown.sql", "234_vkpi_market_prd_referrals.sql"],
        facts=ZERO_FACTS,
        phase="prerollback",
    )
    assert report["decision"]["safe_to_declare_forward_compatible"] is False
    assert report["checks"][0] == {
        "check": "ordered_unique_pending_manifest",
        "passed": False,
    }
    assert report["migrations"][0]["reason"] == "unknown_or_missing_migration"


def test_254_requires_no_live_paid_provider_state_before_v233_rollback():
    report = gate.evaluate(
        ["254_vkpi_provider_execution_fencing.sql"],
        facts=dict(ZERO_FACTS, open_apify_reservations=1),
        phase="prerollback",
    )
    assert report["decision"]["safe_to_declare_forward_compatible"] is False
    assert report["migrations"][0]["verdict"] == "conditional_unproven"


def test_254_through_256_forward_and_down_leave_transactions_to_release_runner():
    for name in (
        "254_vkpi_provider_execution_fencing.sql",
        "254_vkpi_provider_execution_fencing_down.sql",
        "255_vkpi_shopify_business_truth.sql",
        "255_vkpi_shopify_business_truth_down.sql",
        "256_vkpi_financial_artifact_invalidation.sql",
        "256_vkpi_financial_artifact_invalidation_down.sql",
    ):
        sql = (ROOT / "migrations" / name).read_text(encoding="utf-8")
        assert gate.TRANSACTION_CONTROL.search(sql) is None, name


def test_255_fails_legacy_shopify_and_financial_materializations_closed():
    sql = (ROOT / "migrations" / "255_vkpi_shopify_business_truth.sql").read_text(encoding="utf-8")
    assert "DEFAULT 'legacy_unverified'" in sql
    assert "provider_auth_mode='shopify-hmac'" in sql
    assert "NULLIF(BTRIM(raw_payload_hash),'') IS NOT NULL" in sql
    assert "SET value_numeric=NULL" in sql
    assert "confidence='stale'" in sql
    assert "superseded_metric_value=metric_value" in sql
    demotion_sql = sql.split("-- Old lineage values", 1)[0]
    assert "sa.shopify_order_snapshot_id IS NOT NULL" not in demotion_sql
    assert "AND NOT EXISTS" in demotion_sql


def test_255_zeros_every_generated_financial_kpi_for_unfiltered_readers():
    sql = (ROOT / "migrations" / "255_vkpi_shopify_business_truth.sql").read_text(encoding="utf-8")
    stale_clause = sql.split("UPDATE vkpi_kpi_ledger", 1)[1].split("CREATE INDEX", 1)[0]
    generated_financial_keys = {
        "revenue_cents",
        "estimated_revenue_cents",
        "cost_cents",
        "net_contribution_cents",
        "roi",
        "net_roi",
        "workload_score",
        "kpi_credit",
        "recommendation_order_attributed",
        "recommendation_gmv_cents",
        "recommendation_cost_cents",
        "recommendation_roi",
    }
    assert "metric_value=0" in stale_clause
    assert "confidence='stale'" in stale_clause
    for metric_key in generated_financial_keys:
        assert f"'{metric_key}'" in stale_clause


def test_256_revokes_old_financial_artifacts_without_deleting_audit_files():
    sql = (ROOT / "migrations" / "256_vkpi_financial_artifact_invalidation.sql").read_text(
        encoding="utf-8"
    )
    assert "truth_invalidated_at TIMESTAMPTZ" in sql
    assert "truth_invalidation_reason TEXT" in sql
    assert "truth_invalidation_migration INTEGER" in sql
    assert "truth_restorable BOOLEAN" in sql
    assert "SET status='archived'" in sql
    assert "report_type='weekly'" in sql
    assert "truth_restorable=FALSE" in sql
    assert "UPDATE vkpi_weekly_reports" in sql
    assert "SET status='invalidated'" in sql
    assert "WHERE COALESCE(status, '') <> 'invalidated'" in sql
    assert "SET status='invalidated'" in sql
    for export_type in (
        "weekly",
        "attribution",
        "finance",
        "cost",
        "costs",
        "kpi_ledger",
        "staff_kpi",
    ):
        assert f"'{export_type}'" in sql
    normalized = sql.lower()
    assert "metadata_json::jsonb" not in normalized
    assert "delete from vkpi_report_files" not in normalized
    assert "delete from vkpi_export_jobs" not in normalized
    assert "file_path=''" not in normalized
    assert "expires_at=" not in normalized
