"""Migration 296 budget registry, drift repair and rollback contracts."""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest

from app.domains.costs.budget_guard import _budget_payload


ROOT = Path(__file__).resolve().parents[1]
UP_PATH = ROOT / "migrations/296_vkpi_budget_scope_registry.sql"
DOWN_PATH = ROOT / "migrations/296_vkpi_budget_scope_registry_down.sql"
UP = UP_PATH.read_text(encoding="utf-8")
DOWN = DOWN_PATH.read_text(encoding="utf-8")

MONTHLY_FEATURE_SCOPES = (
    "kol_smart_search_query_plan",
    "vkpi_intelligent_ask",
    "vkpi_kol_outreach_draft",
    "vkpi_kol_outreach_optimize",
    "vkpi_sentiment",
    "vkpi_pillar",
)
DAILY_CRON_SCOPES = (
    "cron:vkpi_weekly_summary",
    "cron:vkpi_bio_translate",
    "cron:kol_outreach_pack",
    "cron:gemini_video_legacy",
    "cron:marketing_advisor",
)
REVIEWED_EXCEPTIONS = {
    "agent_skill": "40.00",
    "metric_tracking": "30.00",
    "agent_alert_explain": "5.00",
}


def _assert_seed(scope: str, cap: str, window: str) -> None:
    pattern = (
        rf"\('{re.escape(scope)}',\s*{re.escape(cap)},\s*0,\s*0\.80,\s*1\.00,"
        rf"\s*\(date_trunc\('{window}',\s*CURRENT_TIMESTAMP AT TIME ZONE 'UTC'\)"
    )
    assert re.search(pattern, UP), f"missing {scope} {cap} {window} seed"


def test_registry_seeds_only_reviewed_caps_and_never_overwrites_conflicts() -> None:
    normalized = " ".join(UP.lower().split())
    assert "on conflict (scope) do nothing" in normalized
    for scope in MONTHLY_FEATURE_SCOPES:
        _assert_seed(scope, "10.00", "month")
        assert f'"cost_tag":"{scope}"' in UP
    for scope in DAILY_CRON_SCOPES:
        _assert_seed(scope, "2.00", "day")
        assert f'"cost_tag":"{scope}"' in UP
    for scope, cap in REVIEWED_EXCEPTIONS.items():
        _assert_seed(scope, cap, "month")

    for intentionally_excluded in (
        "cron:dealer_web_verify",
        "cron:gwfix_smoke",
        "cron:stability_smoke",
        "agent_eval",
    ):
        assert intentionally_excluded not in UP


def test_registry_scopes_still_have_current_static_call_sites() -> None:
    call_sites = {
        "kol_smart_search_query_plan": (
            "backend/app/domains/kol/smart_query_planner.py",
            'cost_tag="kol_smart_search_query_plan"',
        ),
        "vkpi_intelligent_ask": (
            "backend/app/api/routers/vkpi_intelligent.py",
            '_SYNTH_BUDGET_SCOPE = "vkpi_intelligent_ask"',
        ),
        "vkpi_kol_outreach_draft": (
            "backend/app/domains/kol/outreach_draft.py",
            'cost_tag="vkpi_kol_outreach_draft"',
        ),
        "vkpi_kol_outreach_optimize": (
            "backend/app/api/routers/vkpi_kol_pool_jobs.py",
            'cost_tag="vkpi_kol_outreach_optimize"',
        ),
        "vkpi_sentiment": (
            "backend/app/domains/comments/sentiment.py",
            'cost_tag="vkpi_sentiment"',
        ),
        "vkpi_pillar": (
            "backend/app/domains/content/pillars.py",
            'cost_tag="vkpi_pillar"',
        ),
        "cron:vkpi_weekly_summary": (
            "backend/app/domains/reports/report_helpers.py",
            'purpose="vkpi_weekly_summary"',
        ),
        "cron:vkpi_bio_translate": (
            "backend/app/api/routers/vkpi_kol_pool_intel.py",
            'purpose="vkpi_bio_translate"',
        ),
        "cron:kol_outreach_pack": (
            "backend/app/domains/kol/outreach_pack.py",
            'COST_TAG = "cron:kol_outreach_pack"',
        ),
        "cron:gemini_video_legacy": (
            "backend/app/services/ai/analyzers/gemini_video.py",
            '"gemini_video_legacy"',
        ),
        "cron:marketing_advisor": (
            "backend/app/domains/advisor/service.py",
            '_COST_SCOPE = "cron:marketing_advisor"',
        ),
        "agent_skill": (
            "backend/app/domains/marketing_brain/skill_license_gate.py",
            'BUDGET_SCOPE = "agent_skill"',
        ),
        "metric_tracking": (
            "backend/app/domains/kol/video_tracking_budget.py",
            'BUDGET_SCOPE = "metric_tracking"',
        ),
        "agent_alert_explain": (
            "backend/app/domains/alerts/anomaly.py",
            'EXPLAIN_SCOPE = "agent_alert_explain"',
        ),
    }
    assert set(call_sites) == {
        *MONTHLY_FEATURE_SCOPES,
        *DAILY_CRON_SCOPES,
        *REVIEWED_EXCEPTIONS,
    }
    for scope, (relative_path, needle) in call_sites.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert needle in source, f"stale registry scope {scope}"


def test_ratio_repairs_are_exact_seed_and_legacy_value_guarded() -> None:
    for scope, warning, hard_stop, seeded_by in (
        ("dashboard:report_analysis", "2.40", "3.00", "migration_153"),
        ("cron:official_daily_report", "3.20", "4.00", "migration_157"),
        ("cron:official_visual", "6.40", "8.00", "migration_158"),
    ):
        assert f"scope = '{scope}'" in UP
        assert f"warning_at = {warning}" in UP
        assert f"hard_stop_at = {hard_stop}" in UP
        assert f"'seeded_by' = '{seeded_by}'" in UP
        assert f"scope = '{scope}'" in DOWN
        assert f"'legacy_warning_at' = '{warning}'" in DOWN
        assert f"'legacy_hard_stop_at' = '{hard_stop}'" in DOWN
    assert "'thresholds_repaired_by', 'migration_296'" in UP
    assert "warning_at = 0.80" in UP
    assert "hard_stop_at = 1.00" in UP


def test_down_is_operator_and_spend_guarded() -> None:
    normalized = " ".join(DOWN.lower().split())
    assert "using ( values" in normalized
    assert (
        "pg_temp.vkpi_296_try_parse_jsonb(budget.metadata_json) = seed.metadata_json"
        in normalized
    )
    assert "budget.reset_at = case seed.reset_window" in normalized
    assert "budget.current_spend = 0" in normalized
    assert "budget.fallback_action = seed.fallback_action" in normalized
    assert "10.00::numeric" in normalized
    assert "2.00::numeric" in normalized
    assert "delete from schema_migrations" in normalized
    assert "296_vkpi_budget_scope_registry.sql" in normalized


def test_metadata_json_drift_repairs_fail_safe_without_rewriting_bad_text() -> None:
    for source in (UP, DOWN):
        normalized = " ".join(source.lower().split())
        assert "create or replace function pg_temp.vkpi_296_try_parse_jsonb" in normalized
        assert "when invalid_text_representation then return null" in normalized
        assert "jsonb_typeof(parsed) <> 'object'" in source
        assert "metadata_json::jsonb" not in source
        assert "drop function pg_temp.vkpi_296_try_parse_jsonb(text)" in normalized


def test_budget_payload_clamps_legacy_ratios_and_exposes_runtime_window() -> None:
    legacy = _budget_payload(
        {
            "scope": "dashboard:report_analysis",
            "cap_usd": 3,
            "current_spend": 2.5,
            "warning_at": 2.4,
            "hard_stop_at": 3.0,
        }
    )
    assert legacy["warning_at"] == 1.0
    assert legacy["hard_stop_at"] == 1.0
    assert legacy["window"] == "daily"

    inverted = _budget_payload(
        {
            "scope": "metric_tracking",
            "cap_usd": 30,
            "current_spend": 24,
            "warning_at": 0.8,
            "hard_stop_at": 0.5,
        }
    )
    assert inverted["warning_at"] == 0.8
    assert inverted["hard_stop_at"] == 0.8
    assert inverted["warning"] is True
    assert inverted["hard_stopped"] is True
    assert inverted["window"] == "monthly"


@pytest.mark.pg
def test_migration_296_real_postgres_preserves_operator_rows_and_rolls_back(pg_dsn: str) -> None:
    import psycopg
    from psycopg import sql

    schema = f"vkpi_budget_296_{uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.execute(
                """
                CREATE TABLE vkpi_provider_budget_caps (
                    scope TEXT PRIMARY KEY,
                    cap_usd NUMERIC(10,2),
                    current_spend NUMERIC(10,4) DEFAULT 0,
                    warning_at NUMERIC(3,2) DEFAULT 0.80,
                    hard_stop_at NUMERIC(3,2) DEFAULT 1.00,
                    reset_at TIMESTAMPTZ,
                    fallback_action TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE schema_migrations (version_key TEXT PRIMARY KEY);
                """
            )
            conn.execute(
                """
                INSERT INTO vkpi_provider_budget_caps
                    (scope, cap_usd, current_spend, warning_at, hard_stop_at,
                     reset_at, fallback_action, metadata_json)
                VALUES
                    ('kol_smart_search_query_plan', 77, 1, 0.70, 0.95, NULL,
                     'operator_fallback', '{"seeded_by":"operator"}'),
                    ('agent_skill', 45, 2, 0.80, 1.00, NULL,
                     'rule_mode_dry_run',
                     '{"seeded_by":"migration_292","window":"manual_monthly"}'),
                    ('agent_alert_explain', 19, 0.5, 0.61, 0.91, NULL,
                     'operator_bad_json', '{not-json'),
                    ('metric_tracking', 31, 2, 0.75, 0.90, NULL,
                     'pause_tracking_enqueue', '{"seeded_by":"enroll_metric_tracking"}'),
                    ('dashboard:report_analysis', 3, 0, 2.40, 3.00, NULL,
                     'skip_llm_keep_last', '{"seeded_by":"migration_153"}'),
                    ('cron:official_daily_report', 4, 0, 0.80, 1.00, NULL,
                     'skip_llm_keep_last', '{"seeded_by":"migration_157"}'),
                    ('cron:official_visual', 8, 0, 6.40, 8.00, NULL,
                     'skip_llm_keep_last', '{"seeded_by":"operator"}')
                """
            )

            conn.execute(UP)
            conn.execute(UP)

            operator = conn.execute(
                "SELECT cap_usd, current_spend, warning_at, hard_stop_at, fallback_action "
                "FROM vkpi_provider_budget_caps WHERE scope='kol_smart_search_query_plan'"
            ).fetchone()
            assert tuple(map(float, operator[:4])) == (77.0, 1.0, 0.7, 0.95)
            assert operator[4] == "operator_fallback"

            dashboard = conn.execute(
                "SELECT warning_at, hard_stop_at, metadata_json::jsonb "
                "FROM vkpi_provider_budget_caps WHERE scope='dashboard:report_analysis'"
            ).fetchone()
            assert tuple(map(float, dashboard[:2])) == (0.8, 1.0)
            assert dashboard[2]["thresholds_repaired_by"] == "migration_296"
            assert dashboard[2]["window"] == "daily"

            unchanged = conn.execute(
                "SELECT scope, warning_at, hard_stop_at, metadata_json::jsonb "
                "FROM vkpi_provider_budget_caps "
                "WHERE scope IN ('cron:official_daily_report','cron:official_visual') "
                "ORDER BY scope"
            ).fetchall()
            assert [tuple(map(float, row[1:3])) for row in unchanged] == [
                (0.8, 1.0),
                (6.4, 8.0),
            ]
            assert all("thresholds_repaired_by" not in row[3] for row in unchanged)

            agent = conn.execute(
                "SELECT cap_usd, current_spend, metadata_json::jsonb "
                "FROM vkpi_provider_budget_caps WHERE scope='agent_skill'"
            ).fetchone()
            assert tuple(map(float, agent[:2])) == (45.0, 2.0)
            assert agent[2]["window"] == "monthly"
            metric = conn.execute(
                "SELECT cap_usd, current_spend, metadata_json::jsonb "
                "FROM vkpi_provider_budget_caps WHERE scope='metric_tracking'"
            ).fetchone()
            assert tuple(map(float, metric[:2])) == (31.0, 2.0)
            assert metric[2]["window"] == "monthly"

            invalid_json_operator = conn.execute(
                "SELECT cap_usd, current_spend, warning_at, hard_stop_at, "
                "fallback_action, metadata_json FROM vkpi_provider_budget_caps "
                "WHERE scope='agent_alert_explain'"
            ).fetchone()
            assert tuple(map(float, invalid_json_operator[:4])) == (19.0, 0.5, 0.61, 0.91)
            assert invalid_json_operator[4:] == ("operator_bad_json", "{not-json")

            conn.execute(
                "UPDATE vkpi_provider_budget_caps SET cap_usd=12 "
                "WHERE scope='vkpi_pillar'"
            )
            conn.execute(
                "UPDATE vkpi_provider_budget_caps SET current_spend=0.25 "
                "WHERE scope='vkpi_intelligent_ask'"
            )
            conn.execute(
                "UPDATE vkpi_provider_budget_caps SET fallback_action='operator_fallback' "
                "WHERE scope='cron:vkpi_bio_translate'"
            )
            conn.execute(
                "UPDATE vkpi_provider_budget_caps SET reset_at=reset_at + INTERVAL '2 days' "
                "WHERE scope='vkpi_sentiment'"
            )
            conn.execute(
                "UPDATE vkpi_provider_budget_caps "
                "SET metadata_json=(metadata_json::jsonb || '{\"operator_note\":\"keep\"}'::jsonb)::text "
                "WHERE scope='vkpi_kol_outreach_optimize'"
            )
            conn.execute(
                "INSERT INTO schema_migrations VALUES ('296_vkpi_budget_scope_registry.sql')"
            )
            conn.execute(DOWN)

            preserved = conn.execute(
                "SELECT scope FROM vkpi_provider_budget_caps WHERE scope IN "
                "('vkpi_pillar','vkpi_intelligent_ask','cron:vkpi_bio_translate') "
                "ORDER BY scope"
            ).fetchall()
            assert [row[0] for row in preserved] == [
                "cron:vkpi_bio_translate",
                "vkpi_intelligent_ask",
                "vkpi_pillar",
            ]
            extra_preserved = conn.execute(
                "SELECT scope FROM vkpi_provider_budget_caps WHERE scope IN "
                "('vkpi_sentiment','vkpi_kol_outreach_optimize') ORDER BY scope"
            ).fetchall()
            assert [row[0] for row in extra_preserved] == [
                "vkpi_kol_outreach_optimize",
                "vkpi_sentiment",
            ]
            assert conn.execute(
                "SELECT count(*) FROM vkpi_provider_budget_caps "
                "WHERE scope='vkpi_kol_outreach_draft'"
            ).fetchone()[0] == 0

            dashboard_after = conn.execute(
                "SELECT warning_at, hard_stop_at, metadata_json::jsonb "
                "FROM vkpi_provider_budget_caps WHERE scope='dashboard:report_analysis'"
            ).fetchone()
            assert tuple(map(float, dashboard_after[:2])) == (2.4, 3.0)
            assert "thresholds_repaired_by" not in dashboard_after[2]
            assert "window" not in dashboard_after[2]

            agent_after = conn.execute(
                "SELECT cap_usd, current_spend, metadata_json::jsonb "
                "FROM vkpi_provider_budget_caps WHERE scope='agent_skill'"
            ).fetchone()
            assert tuple(map(float, agent_after[:2])) == (45.0, 2.0)
            assert agent_after[2]["window"] == "manual_monthly"
            metric_after = conn.execute(
                "SELECT cap_usd, current_spend, metadata_json::jsonb "
                "FROM vkpi_provider_budget_caps WHERE scope='metric_tracking'"
            ).fetchone()
            assert tuple(map(float, metric_after[:2])) == (31.0, 2.0)
            assert "window" not in metric_after[2]
            invalid_json_after = conn.execute(
                "SELECT cap_usd, current_spend, warning_at, hard_stop_at, "
                "fallback_action, metadata_json FROM vkpi_provider_budget_caps "
                "WHERE scope='agent_alert_explain'"
            ).fetchone()
            assert tuple(map(float, invalid_json_after[:4])) == (19.0, 0.5, 0.61, 0.91)
            assert invalid_json_after[4:] == ("operator_bad_json", "{not-json")
            assert conn.execute(
                "SELECT count(*) FROM schema_migrations "
                "WHERE version_key='296_vkpi_budget_scope_registry.sql'"
            ).fetchone()[0] == 0
        finally:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
