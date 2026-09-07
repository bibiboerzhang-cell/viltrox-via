#!/usr/bin/env python3
"""Run a fixed PG regression suite on a newly owned, Unix-socket-only cluster.

Default: plan only. --execute creates no application service, uses no snapshot,
and retains evidence/data files after stopping only its disposable PostgreSQL.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import urlencode
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from psycopg import sql  # noqa: E402
from scripts.ops import rehearse_migrations_307_310 as safety  # noqa: E402
from scripts.ops.trusted_runtime_binary import trusted_runtime_binary  # noqa: E402

TESTS = (
    'tests/test_kol_inventory_scan_stability.py::test_real_postgres_scan_resume_and_cache_storage',
    'tests/test_llm_fleet_breaker.py::test_real_postgres_allows_one_half_open_probe_and_rejects_stale_fence',
    'tests/test_scheduler_fire_ledger_honesty.py::test_claim_failed_and_blocked_rows_pass_migration_294_on_real_postgres',
    'tests/test_skill_registry.py::test_record_then_readback_roundtrip',
    'tests/test_skill_registry.py::test_acceptance_stats_three_state',
)
PRECISION_COST_TESTS = (
    'tests/test_apify_cost_reconciliation_pg.py::test_first_record_late_charge_and_replay_are_atomic',
    'tests/test_apify_cost_reconciliation_pg.py::test_concurrent_run_observers_apply_one_delta',
    'tests/test_apify_cost_reconciliation_pg.py::test_nowait_cap_lock_rolls_back_initial_detail_then_existing_run_retries',
    'tests/test_apify_cost_reconciliation_pg.py::test_real_sql_failure_rolls_back_three_books_and_preserves_sqlstate',
    'tests/test_llm_cost_precision_pg.py::test_real_postgres_concurrent_settlement_has_no_lost_micro',
    'tests/test_budget_window_roll_concurrency_pg.py::test_roll_lock_serializes_reset_before_concurrent_cost_increment',
    'tests/test_budget_window_roll_concurrency_pg.py::test_record_cost_concurrent_scope_delete_fails_and_rolls_back_ledger',
    'tests/test_smart_search_draft_lock_pg.py::test_owner_session_row_lock_serializes_draft_reuse',
)
PRECISION_COST_SOURCES = (
    'backend/app/domains/costs/apify_cost_reconciliation.py',
    'backend/app/domains/costs/budget_guard.py',
    'backend/app/domains/costs/budget_guard_persistence.py',
    'backend/app/domains/costs/budget_window_roll.py',
    'backend/app/platform/llm_budget_reservations.py',
    'backend/app/db/connection.py',
    'backend/app/domains/projects/workflow_projects.py',
    'tests/conftest.py',
    'migrations/275_vkpi_llm_cost_precision.sql',
    'scripts/ops/run_synthetic_pg_regressions.py',
    'scripts/ops/rehearse_migrations_307_310.py',
)
SEARCH_LIFECYCLE_TESTS = (
    'tests/test_search_lifecycle_pg.py::test_summary_and_terminal_lane_serialize_with_item_foreign_keys',
    'tests/test_search_lifecycle_pg.py::test_ready_child_rebuild_waits_and_preserves_failed_execution',
    'tests/test_search_lifecycle_pg.py::test_old_attempt_failure_waits_and_cannot_change_new_attempt',
    'tests/test_search_lifecycle_pg.py::test_old_normal_finalization_waits_and_cannot_finish_new_attempt',
    'tests/test_search_lifecycle_pg.py::test_late_normal_finalization_preserves_current_failure',
)
SEARCH_LIFECYCLE_SOURCES = (
    'backend/app/domains/kol/search_execution_observation.py',
    'backend/app/domains/kol/search_execution_fence.py',
    'backend/app/domains/kol/profile_discovery_pipeline_stages.py',
    'backend/app/domains/kol/search_sessions.py',
    'backend/app/domains/kol/search_sessions_attachment_status.py',
    'backend/app/domains/kol/search_sessions_lanes.py',
    'backend/app/domains/kol/search_session_job_analysis.py',
    'backend/app/domains/kol/search_sessions_items.py',
    'backend/app/domains/kol/search_sessions_serde.py',
    'backend/app/db/connection.py',
    'tests/conftest.py',
    'scripts/ops/run_synthetic_pg_regressions.py',
    'scripts/ops/rehearse_migrations_307_310.py',
)
COMMUNICATION_TRUTH_TESTS = (
    'tests/test_recommendation_outcomes_pg.py::test_assignment_stage_sync_maps_device_sent_and_skips_missing_recommendation',
    'tests/test_gtm_outreach_truth_bridge_pg.py::test_migration_277_real_pg_up_down_fk_and_immutable_triggers',
    'tests/test_gtm_outreach_truth_bridge_pg.py::test_real_pg_parent_locks_block_pool_project_and_message_phantoms',
    'tests/test_message_capture_truth_pg.py::test_both_message_writers_commit_correct_member_and_project_unknown',
    'tests/test_message_capture_truth_pg.py::test_bad_identity_and_database_constraint_do_not_leave_partial_capture',
    'tests/test_communication_truth_pg.py::test_record_sync_refresh_and_legacy_reads_never_claim_transport',
    'tests/test_gtm_manual_truth_pg.py::test_domain_receipts_replay_and_event_rollback_stay_manager_attested',
)
COMMUNICATION_TRUTH_SOURCES = (
    'backend/app/shared/communication_truth.py',
    'backend/app/shared/message_truth.py',
    'backend/app/domains/kol/profile_detail.py',
    'backend/app/domains/evidence/messages.py',
    'backend/app/domains/evidence/message_truth.py',
    'backend/app/domains/evidence/common.py',
    'backend/app/domains/projects/workflow_evidence_project_writes.py',
    'backend/app/domains/projects/workflow_detail_sections.py',
    'backend/app/domains/recommendations/outcomes.py',
    'backend/app/domains/recommendations/outcome_sync.py',
    'backend/app/domains/recommendations/communication_evidence.py',
    'backend/app/domains/recommendations/outcome_refresh_project_links.py',
    'backend/app/domains/recommendations/rerank_fit.py',
    'backend/app/domains/recommendations/rerank_shadow.py',
    'backend/app/domains/market_brain/outreach_truth_bridge.py',
    'backend/app/domains/market_brain/outreach_reply_truth.py',
    'backend/app/domains/market_brain/outreach_reply_receipt_validation.py',
    'backend/app/domains/market_brain/outreach_truth_coverage.py',
    'backend/app/domains/market_brain/prediction_truth.py',
    'backend/app/domains/actions/approval_evidence.py',
    'backend/app/domains/platform/review_contract.py',
    'backend/app/domains/platform/event_ledger.py',
    'backend/app/db/connection.py',
    'migrations/277_vkpi_action_outreach_truth_bridge.sql',
    'migrations/277_vkpi_action_outreach_truth_bridge_down.sql',
    'migrations/288_vkpi_recommendation_feature_snapshot.sql',
    'tests/test_gtm_outreach_truth_bridge.py',
    'tests/test_message_capture_truth.py',
    'tests/conftest.py',
    'scripts/ops/run_synthetic_pg_regressions.py',
    'scripts/ops/rehearse_migrations_307_310.py',
    'scripts/ops/trusted_runtime_binary.py',
    'scripts/ops/safe_python.sh',
    'scripts/ops/safe_python_router.py',
)
KPI_EXPERIMENT_TRUTH_TESTS = (
    'tests/test_kpi_experiment_truth_pg.py::test_kpi_grouped_numeric_date_scope_preserves_recorded_and_unknown',
    'tests/test_kpi_experiment_truth_pg.py::test_staff_grouped_ledger_cannot_restore_legacy_credit',
    'tests/test_kpi_experiment_truth_pg.py::test_kpi_upsert_replay_retains_old_derived_and_excludes_transport',
    'tests/test_kpi_experiment_truth_pg.py::test_experiment_boolean_groups_recompute_labels_and_keep_pending',
    'tests/test_kpi_experiment_truth_pg.py::test_experiment_missing_outcomes_table_preserves_unknown_denominator',
    'tests/test_kpi_experiment_truth_pg.py::test_experiment_empty_window_and_missing_snapshot_do_not_invent_zero_rate',
)
KPI_EXPERIMENT_TRUTH_SOURCES = (
    'backend/app/shared/communication_truth.py',
    'backend/app/domains/kol/profile_detail.py',
    'backend/app/shared/vkpi_kpi_communication_truth.py',
    'backend/app/shared/vkpi_decision_common.py',
    'backend/app/shared/vkpi_kpi_evidence.py',
    'backend/app/shared/vkpi_kpi_evidence_enrichment.py',
    'backend/app/domains/staff/decision_staff_kpi.py',
    'backend/app/domains/staff/kpi_ledger.py',
    'backend/app/domains/staff/kpi_rollup.py',
    'backend/app/domains/experiments/scoring.py',
    'backend/app/domains/recommendations/communication_evidence.py',
    'backend/app/domains/recommendations/rerank_fit.py',
    'backend/app/domains/recommendations/rerank_shadow.py',
    'backend/app/domains/business_truth.py',
    'backend/app/db/connection.py',
    'tests/conftest.py',
    'scripts/ops/run_synthetic_pg_regressions.py',
    'scripts/ops/rehearse_migrations_307_310.py',
    'scripts/ops/trusted_runtime_binary.py',
    'scripts/ops/safe_python.sh',
    'scripts/ops/safe_python_router.py',
)
SHIPMENT_CONCURRENCY_TESTS = (
    'tests/test_shipment_dispatch_pg.py::test_same_tracking_concurrent_records_wait_and_reuse_one_receipt',
    'tests/test_shipment_dispatch_pg.py::test_concurrent_rejection_commits_before_dispatch_and_blocks_all_records',
)
SHIPMENT_CONCURRENCY_SOURCES = (
    'backend/app/shared/message_truth.py',
    'backend/app/domains/evidence/message_truth.py',
    'backend/app/domains/projects/workflow_evidence_project_writes.py',
    'backend/app/domains/projects/workflow_common.py',
    'backend/app/domains/projects/shipment_approval.py',
    'backend/app/domains/projects/shipment_write_guard.py',
    'backend/app/db/connection.py',
    'tests/conftest.py',
    'scripts/ops/run_synthetic_pg_regressions.py',
    'scripts/ops/rehearse_migrations_307_310.py',
    'scripts/ops/trusted_runtime_binary.py',
    'scripts/ops/safe_python.sh',
    'scripts/ops/safe_python_router.py',
)
PAYOUT_REQUEST_TESTS = (
    'tests/test_payout_request_pg.py::test_concurrent_accrual_waits_and_creates_one_pending_payout',
    'tests/test_payout_request_pg.py::test_processing_waits_for_accrual_and_cannot_complete_pending_payout',
)
PAYOUT_REQUEST_SOURCES = (
    'backend/app/services/commerce/payouts.py',
    'backend/app/db/connection.py',
    'backend/app/db/connection_sql_translation.py',
    'migrations/015_v5_commerce_admin_schema.sql',
    'tests/conftest.py',
    'scripts/ops/run_synthetic_pg_regressions.py',
    'scripts/ops/rehearse_migrations_307_310.py',
    'scripts/ops/trusted_runtime_binary.py',
    'scripts/ops/safe_python.sh',
    'scripts/ops/safe_python_router.py',
)
SUITES = {'default': TESTS, 'precision-cost': PRECISION_COST_TESTS,
          'search-lifecycle': SEARCH_LIFECYCLE_TESTS, 'communication-truth': COMMUNICATION_TRUTH_TESTS,
          'kpi-experiment-truth': KPI_EXPERIMENT_TRUTH_TESTS, 'shipment-concurrency': SHIPMENT_CONCURRENCY_TESTS,
          'payout-request': PAYOUT_REQUEST_TESTS}
SUITE_TIMEOUTS = {'default': 60, 'precision-cost': 120, 'search-lifecycle': 60, 'communication-truth': 120,
                  'kpi-experiment-truth': 120, 'shipment-concurrency': 60, 'payout-request': 60}
SUITE_SOURCES = {'precision-cost': PRECISION_COST_SOURCES, 'search-lifecycle': SEARCH_LIFECYCLE_SOURCES,
                 'communication-truth': COMMUNICATION_TRUTH_SOURCES,
                 'kpi-experiment-truth': KPI_EXPERIMENT_TRUTH_SOURCES,
                 'shipment-concurrency': SHIPMENT_CONCURRENCY_SOURCES,
                 'payout-request': PAYOUT_REQUEST_SOURCES}
SEED_MIGRATION = ROOT / 'migrations/199_vkpi_skill_runs.sql'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _environment(cluster: Path, target: str = '') -> dict[str, str]:
    env = {'PATH': '/opt/homebrew/bin:/usr/bin:/bin', 'HOME': str(cluster / 'home'),
           'LANG': 'C', 'LC_ALL': 'C', 'TMPDIR': str(cluster / 'tmp')}
    if target:
        dsn = f'postgresql://postgres@/{target}?' + urlencode(
            {'host': str(cluster / 'socket'), 'port': '5432', 'passfile': '/dev/null'})
        env.update(DATABASE_URL=dsn, LOCAL_DATABASE_URL=dsn,
                   VKPI_PYTEST_ALLOW_LIVE_SERVICES='1', VKPI_SKIP_DOTENV='1',
                   VKPI_LLM_GATEWAY_FORCE_OFFLINE='1', VKPI_EXTERNAL_AI_DISABLED='1',
                   ENABLE_SCHEDULER='0', ENABLE_LOCAL_ORCHESTRATOR='0', ENABLE_BROWSER='0',
                   VKPI_ASYNC_ENABLED='0', REDIS_URL='', PYTHONDONTWRITEBYTECODE='1',
                   RUNTIME_ROOT=str(cluster / 'runtime'),
                   PYTHONPATH=f'{ROOT}:{ROOT}/scripts:{ROOT}/backend')
    return env


def _command(arguments, output: Path, name: str, env, *, timeout=30):
    with (output / name).open('wb') as handle:
        return subprocess.run(arguments, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                              stdout=handle, stderr=subprocess.STDOUT, timeout=timeout, check=False)


def _pytest_summary(path: Path, tests: tuple[str, ...] = TESTS) -> dict[str, int]:
    cases = ElementTree.parse(path).getroot().findall('.//testcase')
    expected = {(t.split('::')[0][:-3].replace('/', '.'), t.split('::')[1]) for t in tests}
    actual = {(case.get('classname'), case.get('name')) for case in cases}
    safety._check(len(cases) == len(tests) and actual == expected, 'unexpected_pg_test_scope')
    counts = {name: sum(case.find(name) is not None for case in cases)
              for name in ('failure', 'error', 'skipped')}
    safety._check(not any(counts.values()), 'pg_regression_incomplete')
    return {'passed': len(cases), 'failed': counts['failure'],
            'errors': counts['error'], 'skipped': counts['skipped']}


def _stop_owned(cluster, binding, pg_ctl, output, report):
    safety._check(safety._private_directory(cluster) == binding.identity, 'cluster_identity_changed')
    safety._check(safety._private_directory(cluster / 'data') == binding.data_identity, 'cluster_data_identity_changed')
    pidfile = cluster / 'data' / 'postmaster.pid'
    if pidfile.exists():
        lines = pidfile.read_text().splitlines()
        safety._check(len(lines) > 1 and lines[0].isdigit() and Path(lines[1]).resolve() == cluster / 'data',
                      'owned_postmaster_identity_unconfirmed')
        stopped = _command([pg_ctl, '-D', str(cluster / 'data'), '-m', 'fast', '-w', '-t', '20', 'stop'],
                           output, 'pg-stop.log', _environment(cluster))
        safety._check(stopped.returncode == 0 and not pidfile.exists(), 'owned_postgres_stop_failed')
    report['cleanup']['owned_cluster_stopped'] = True


def _seed_suite(conn, suite: str, report: dict) -> None:
    if suite in {'precision-cost', 'search-lifecycle', 'communication-truth', 'kpi-experiment-truth', 'shipment-concurrency', 'payout-request'}:
        report['synthetic_seed'] = {'kind': 'test_owned_scratch_schemas_only',
                                    'schema_migrations_modified': False}
        return
    safety._check(SEED_MIGRATION.is_file() and not SEED_MIGRATION.is_symlink(),
                  'synthetic_seed_regular_file_required')
    payload = SEED_MIGRATION.read_bytes()
    conn.execute(payload.decode('utf-8'))
    report['synthetic_seed'] = {'migration': SEED_MIGRATION.name,
                                'sha256': hashlib.sha256(payload).hexdigest(),
                                'schema_migrations_modified': False}


def run_regressions(*, suite: str = 'default') -> dict:
    safety._check(suite in SUITES, 'unknown_fixed_pg_suite')
    tests = SUITES[suite]
    # The rehearsal's libpq guard also rejects PG* names before connecting.
    # Refuse ambient application DSNs rather than allowing accidental inheritance.
    if any(k.startswith('PG') or k in {'DATABASE_URL', 'LOCAL_DATABASE_URL', 'DATABASE_POOL_URL'} for k in os.environ):
        return {'status': 'failed', 'failed_stage': 'clean_environment_required', 'database_connections': 0}
    pg_ctl = trusted_runtime_binary('pg_ctl')
    initdb = trusted_runtime_binary('initdb')
    output = Path(tempfile.mkdtemp(prefix='vkpi-pg-regression-proof.', dir='/private/tmp'))
    cluster = Path(tempfile.mkdtemp(prefix=safety.CLUSTER_PREFIX, dir='/private/tmp'))
    for name in ('socket', 'home', 'tmp'):
        (cluster / name).mkdir(mode=0o700)
    report = {'synthetic_only': True, 'business_database_accessed': False, 'provider_calls': 0,
              'status': 'failed', 'suite': suite, 'tests': list(tests), 'started_at': _now(), 'cleanup': {},
              'output': str(output), 'cluster_root': str(cluster),
              'test_hashes': {t.split('::')[0]: hashlib.sha256((ROOT / t.split('::')[0]).read_bytes()).hexdigest() for t in tests},
              'source_hashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in SUITE_SOURCES.get(suite, ())}}
    stage, start_requested, binding, admin, target, target_oid = 'initdb', False, None, None, '', None
    try:
        result = _command([initdb, '-D', str(cluster / 'data'), '-U', 'postgres', '-A', 'trust',
                           '--no-locale', '--encoding=UTF8'], output, 'initdb.log', _environment(cluster))
        safety._check(result.returncode == 0, 'initdb_failed')
        binding = safety._cluster_binding(f'host={cluster}/socket port=5432 user=postgres dbname=postgres', cluster)
        stage, start_requested = 'start_owned_postgres', True
        result = _command([pg_ctl, '-D', str(cluster / 'data'), '-l', str(output / 'postgres.log'),
                           '-w', '-t', '20', '-o', f"-h '' -k {cluster}/socket -p 5432 -c unix_socket_permissions=0700", 'start'],
                          output, 'pg-start.log', _environment(cluster))
        safety._check(result.returncode == 0, 'owned_postgres_start_failed')
        stage = 'verify_private_identity'
        admin = safety._connect(binding, 'postgres')
        binding = safety._bind_admin(admin, binding)
        report.update(server_version=admin.execute('SHOW server_version').fetchone()[0],
                      unix_socket_only=admin.execute('SHOW listen_addresses').fetchone()[0] == '',
                      system_identifier=binding.system_identifier)
        safety._check(report['unix_socket_only'] is True, 'tcp_listener_forbidden')
        stage = 'create_synthetic_database'
        target = safety.TARGET_PREFIX + secrets.token_hex(12)
        admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(target)))
        target_oid = safety._database_oid(admin, target)
        report['target_database'] = target
        safety._check(target_oid is not None, 'synthetic_database_identity_missing')
        with safety._connect(binding, target) as conn:
            safety._verify_server(conn, binding, target)
            stage = 'seed_synthetic_suite'
            _seed_suite(conn, suite, report)
        stage = 'pytest'
        result = _command([str(ROOT / 'scripts/ops/safe_python.sh'), '-m', 'pytest', '-q', '-rA',
                           '--junitxml=' + str(output / 'pytest.xml'), *tests],
                          output, 'pytest.log', _environment(cluster, target),
                          timeout=SUITE_TIMEOUTS[suite])
        report.update(pytest_exit_code=result.returncode, pytest_log=str(output / 'pytest.log'),
                      pytest_junit=str(output / 'pytest.xml'))
        safety._check(result.returncode == 0, 'pg_regression_failed')
        report['test_counts'] = _pytest_summary(output / 'pytest.xml', tests)
        report['status'] = 'passed'
    except Exception as exc:
        report.update(failed_stage=stage, error_type=type(exc).__name__)
    finally:
        if admin is not None:
            try:
                if target_oid is not None:
                    safety._drop_owned_database(admin, binding, target, target_oid)
                    report['cleanup']['synthetic_database_dropped'] = True
                report['cleanup']['remaining_nonstandard_databases'] = admin.execute(
                    "SELECT COUNT(*) FROM pg_database WHERE datname NOT IN ('postgres','template0','template1')").fetchone()[0]
                safety._verify_server(admin, binding, 'postgres')
            except Exception as exc:
                report['status'] = 'failed'
                report['cleanup']['error_type'] = type(exc).__name__
            finally:
                admin.close()
        if start_requested and binding is not None:
            try:
                _stop_owned(cluster, binding, pg_ctl, output, report)
            except Exception as exc:
                report['status'] = 'failed'
                report['cleanup']['stop_error_type'] = type(exc).__name__
        report['completed_at'] = _now()
        (output / 'receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--suite', choices=tuple(SUITES), default='default')
    args = parser.parse_args(argv)
    report = run_regressions(suite=args.suite) if args.execute else {
        'status': 'plan_only', 'suite': args.suite, 'tests': list(SUITES[args.suite]), 'database_connections': 0,
        'synthetic_only': True, 'requires_explicit_execute': True,
    }
    sys.stdout.write(json.dumps(report, ensure_ascii=False) + '\n')
    return 0 if report['status'] in {'passed', 'plan_only'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
