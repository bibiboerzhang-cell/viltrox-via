"""Hermetic tests for the fixed, owned PostgreSQL regression runner."""
import ast
import json
import os
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import pytest

from scripts.ops import run_synthetic_pg_regressions as runner


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('Hermetic runner test attempted external I/O')

    monkeypatch.setattr(runner.safety.psycopg, 'connect', refuse)
    monkeypatch.setattr(runner, '_command', refuse)
    monkeypatch.setattr(runner, 'trusted_runtime_binary', refuse)


def test_default_cli_is_zero_io_even_with_ambient_database(capsys, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'not-to-be-read-or-emitted')
    assert runner.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'plan_only'
    assert result['database_connections'] == 0
    assert result['requires_explicit_execute'] is True
    assert result['tests'] == list(runner.TESTS)


@pytest.mark.parametrize('name', ['PGHOST', 'PGSERVICE', 'PGPASSWORD', 'DATABASE_URL',
                                  'LOCAL_DATABASE_URL', 'DATABASE_POOL_URL'])
def test_execute_rejects_ambient_dsn_before_binary_or_cluster_access(name, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith('PG') or key in {'DATABASE_URL', 'LOCAL_DATABASE_URL', 'DATABASE_POOL_URL'}:
            monkeypatch.delenv(key)
    monkeypatch.setenv(name, 'not-to-be-read-or-emitted')
    result = runner.run_regressions()
    assert result == {'status': 'failed', 'failed_stage': 'clean_environment_required',
                      'database_connections': 0}


def test_child_environment_is_private_and_does_not_inherit_credentials(tmp_path, monkeypatch):
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'PGHOSTADDR', 'PGSERVICE',
                'VKPI_SAFE_PYTHON_PROFILE', 'VKPI_LLM_READINESS_OPERATOR_ACK'):
        monkeypatch.setenv(key, 'ambient-synthetic-secret')
    env = runner._environment(tmp_path, 'vkpi_migration_test_' + 'a' * 24)
    assert 'ambient-synthetic-secret' not in repr(env)
    assert env['HOME'] == str(tmp_path / 'home')
    assert env['RUNTIME_ROOT'] == str(tmp_path / 'runtime')
    assert env['DATABASE_URL'] == env['LOCAL_DATABASE_URL']
    uri = urlparse(env['DATABASE_URL'])
    assert uri.hostname is None
    assert parse_qs(uri.query) == {'host': [str(tmp_path / 'socket')],
                                    'port': ['5432'], 'passfile': ['/dev/null']}
    for key in ('ENABLE_SCHEDULER', 'ENABLE_LOCAL_ORCHESTRATOR', 'ENABLE_BROWSER', 'VKPI_ASYNC_ENABLED'):
        assert env[key] == '0'
    assert env['VKPI_SKIP_DOTENV'] == '1'
    assert env['VKPI_LLM_GATEWAY_FORCE_OFFLINE'] == '1'
    assert env['VKPI_EXTERNAL_AI_DISABLED'] == '1'
    assert env['REDIS_URL'] == ''


def junit(tmp_path, alteration='', tests=None):
    root = ElementTree.Element('testsuites')
    suite = ElementTree.SubElement(root, 'testsuite')
    for nodeid in runner.TESTS if tests is None else tests:
        module, name = nodeid.split('::')
        ElementTree.SubElement(suite, 'testcase', classname=module[:-3].replace('/', '.'), name=name)
    if alteration in {'skipped', 'failure', 'error'}:
        ElementTree.SubElement(suite[0], alteration)
    elif alteration == 'missing':
        suite.remove(suite[0])
    elif alteration == 'duplicate':
        suite[0].set('name', suite[1].get('name'))
    path = tmp_path / 'pytest.xml'
    ElementTree.ElementTree(root).write(path)
    return path


def test_junit_requires_exact_fixed_tests_without_skips(tmp_path):
    assert runner._pytest_summary(junit(tmp_path)) == {
        'passed': 5, 'failed': 0, 'errors': 0, 'skipped': 0}


@pytest.mark.parametrize('alteration', ['skipped', 'failure', 'error', 'missing', 'duplicate'])
def test_junit_rejects_green_exit_with_incomplete_scope(tmp_path, alteration):
    with pytest.raises(runner.safety.RehearsalError):
        runner._pytest_summary(junit(tmp_path, alteration))


@pytest.mark.parametrize('invalid', ['cluster_inode', 'data_inode', 'postmaster_path'])
def test_stop_refuses_changed_owned_identity(tmp_path, invalid):
    (tmp_path / 'data').mkdir(mode=0o700)
    binding = SimpleNamespace(identity=runner.safety._private_directory(tmp_path),
                              data_identity=runner.safety._private_directory(tmp_path / 'data'))
    if invalid == 'cluster_inode':
        binding.identity = ('changed',)
    elif invalid == 'data_inode':
        binding.data_identity = ('changed',)
    else:
        (tmp_path / 'data/postmaster.pid').write_text('1234\n/some-other-cluster\n')
    report = {'cleanup': {}}
    with pytest.raises(runner.safety.RehearsalError):
        runner._stop_owned(tmp_path, binding, '/unused/pg_ctl', tmp_path, report)
    assert report['cleanup'] == {}


def test_stopped_owned_cluster_needs_no_stop_command(tmp_path):
    (tmp_path / 'data').mkdir(mode=0o700)
    binding = SimpleNamespace(identity=runner.safety._private_directory(tmp_path),
                              data_identity=runner.safety._private_directory(tmp_path / 'data'))
    report = {'cleanup': {}}
    runner._stop_owned(tmp_path, binding, '/unused/pg_ctl', tmp_path, report)
    assert report['cleanup'] == {'owned_cluster_stopped': True}


def test_precision_cost_default_is_plan_only_and_fixed_eight(capsys, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'ambient-business-dsn-must-not-be-read')
    assert runner.main(['--suite', 'precision-cost']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'plan_only' and report['database_connections'] == 0
    assert report['requires_explicit_execute'] is True
    assert report['tests'] == list(runner.PRECISION_COST_TESTS)
    assert len(report['tests']) == len(set(report['tests'])) == 8
    assert len(runner.TESTS) == 5  # The existing default suite is unchanged.
    assert runner.SUITE_TIMEOUTS['default'] == 60
    assert runner.SUITE_TIMEOUTS['precision-cost'] == 120
    assert 'ambient-business-dsn' not in repr(report)


@pytest.mark.parametrize('arguments', [
    ['--suite', 'tests/test_pg_concurrency.py'],
    ['--suite', 'precision-cost', '--dsn', 'postgresql://not-allowed/test'],
    ['--suite', 'precision-cost', '--nodeid', 'tests/test_pg_concurrency.py'],
])
def test_runner_refuses_arbitrary_suite_dsn_or_nodeid(arguments):
    with pytest.raises(SystemExit) as error:
        runner.main(arguments)
    assert error.value.code == 2


def test_direct_runner_refuses_unknown_suite_before_io():
    with pytest.raises(runner.safety.RehearsalError, match='unknown_fixed_pg_suite'):
        runner.run_regressions(suite='unreviewed')


def test_precision_cost_does_not_seed_a_migration_or_public_table():
    class NoSQL:
        def execute(self, *args):
            pytest.fail('precision-cost suite must rely on test-owned scratch schemas')
    report = {}
    runner._seed_suite(NoSQL(), 'precision-cost', report)
    assert report['synthetic_seed'] == {
        'kind': 'test_owned_scratch_schemas_only', 'schema_migrations_modified': False}


def test_precision_cost_junit_requires_exact_eight_real_passes(tmp_path):
    tests = runner.PRECISION_COST_TESTS
    assert runner._pytest_summary(junit(tmp_path, tests=tests), tests) == {
        'passed': 8, 'failed': 0, 'errors': 0, 'skipped': 0}


@pytest.mark.parametrize('alteration', ['skipped', 'failure', 'error', 'missing', 'duplicate'])
def test_precision_cost_junit_rejects_partial_or_mismatched_scope(tmp_path, alteration):
    tests = runner.PRECISION_COST_TESTS
    with pytest.raises(runner.safety.RehearsalError):
        runner._pytest_summary(junit(tmp_path, alteration, tests=tests), tests)


def test_precision_cost_manifest_covers_reviewed_runtime_and_fixture_sources():
    required = {
        'backend/app/domains/costs/apify_cost_reconciliation.py',
        'backend/app/platform/llm_budget_reservations.py',
        'backend/app/db/connection.py', 'tests/conftest.py',
    }
    assert required <= set(runner.PRECISION_COST_SOURCES)
    for name in runner.PRECISION_COST_SOURCES:
        assert (runner.ROOT / name).is_file() and not (runner.ROOT / name).is_symlink()


def test_search_lifecycle_is_plan_only_exact_five_and_keeps_cost_suite(capsys):
    cost_tests = runner.PRECISION_COST_TESTS
    assert runner.main(['--suite', 'search-lifecycle']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'plan_only' and report['database_connections'] == 0
    assert report['requires_explicit_execute'] is True
    assert report['tests'] == list(runner.SEARCH_LIFECYCLE_TESTS)
    assert len(set(report['tests'])) == 5
    assert runner.SUITES['precision-cost'] == cost_tests and len(cost_tests) == 8
    assert runner.SUITE_TIMEOUTS['search-lifecycle'] == 60


def test_search_lifecycle_has_no_runner_seed_sql_and_binds_changed_writers():
    class NoSQL:
        def execute(self, *args):
            pytest.fail('search-lifecycle must seed only its own test schema')
    report = {}
    runner._seed_suite(NoSQL(), 'search-lifecycle', report)
    assert report['synthetic_seed']['schema_migrations_modified'] is False
    assert {
        'backend/app/domains/kol/search_sessions.py',
        'backend/app/domains/kol/search_session_job_analysis.py',
        'backend/app/domains/kol/search_sessions_lanes.py',
        'backend/app/domains/kol/profile_discovery_pipeline_stages.py',
        'backend/app/domains/kol/search_execution_fence.py',
    } <= set(runner.SUITE_SOURCES['search-lifecycle'])


def test_search_lifecycle_junit_requires_exact_five_real_passes(tmp_path):
    tests = runner.SEARCH_LIFECYCLE_TESTS
    assert runner._pytest_summary(junit(tmp_path, tests=tests), tests) == {
        'passed': 5, 'failed': 0, 'errors': 0, 'skipped': 0}


@pytest.mark.parametrize('alteration', ['skipped', 'failure', 'error', 'missing', 'duplicate'])
def test_search_lifecycle_rejects_incomplete_or_wrong_scope(tmp_path, alteration):
    tests = runner.SEARCH_LIFECYCLE_TESTS
    with pytest.raises(runner.safety.RehearsalError):
        runner._pytest_summary(junit(tmp_path, alteration, tests=tests), tests)


def test_communication_truth_plan_is_exact_seven_without_changing_accepted_suites(capsys):
    assert runner.main(['--suite', 'communication-truth']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'plan_only' and report['database_connections'] == 0
    assert report['requires_explicit_execute'] is True
    assert report['tests'] == list(runner.COMMUNICATION_TRUTH_TESTS)
    assert len(set(report['tests'])) == 7
    assert {suite: len(nodes) for suite, nodes in runner.SUITES.items()} == {
        'default': 5, 'precision-cost': 8, 'search-lifecycle': 5, 'communication-truth': 7,
        'kpi-experiment-truth': 6, 'shipment-concurrency': 2, 'payout-request': 2}
    assert runner.SUITE_TIMEOUTS['communication-truth'] == 120


def test_communication_truth_has_no_runner_seed_or_arbitrary_dsn():
    class NoSQL:
        def execute(self, *args):
            pytest.fail('communication-truth must seed only its own test schemas')
    report = {}
    runner._seed_suite(NoSQL(), 'communication-truth', report)
    assert report['synthetic_seed'] == {
        'kind': 'test_owned_scratch_schemas_only', 'schema_migrations_modified': False}
    with pytest.raises(SystemExit) as error:
        runner.main(['--suite', 'communication-truth', '--dsn', 'postgresql://not-allowed/test'])
    assert error.value.code == 2


def test_communication_truth_junit_requires_exact_seven_real_passes(tmp_path):
    tests = runner.COMMUNICATION_TRUTH_TESTS
    assert runner._pytest_summary(junit(tmp_path, tests=tests), tests) == {
        'passed': 7, 'failed': 0, 'errors': 0, 'skipped': 0}


@pytest.mark.parametrize('alteration', ['skipped', 'failure', 'error', 'missing', 'duplicate'])
def test_communication_truth_rejects_incomplete_scope(tmp_path, alteration):
    tests = runner.COMMUNICATION_TRUTH_TESTS
    with pytest.raises(runner.safety.RehearsalError):
        runner._pytest_summary(junit(tmp_path, alteration, tests=tests), tests)


def test_communication_truth_sources_and_nodes_are_fixed_regular_files():
    assert {
        'backend/app/shared/communication_truth.py',
        'backend/app/shared/message_truth.py',
        'backend/app/domains/kol/profile_detail.py',
        'backend/app/domains/evidence/messages.py',
        'backend/app/domains/evidence/message_truth.py',
        'backend/app/domains/recommendations/outcomes.py',
        'backend/app/domains/recommendations/communication_evidence.py',
        'backend/app/domains/market_brain/outreach_truth_bridge.py',
        'backend/app/domains/market_brain/outreach_reply_truth.py',
        'backend/app/domains/platform/event_ledger.py',
        'backend/app/db/connection.py', 'tests/conftest.py',
        'scripts/ops/safe_python.sh', 'scripts/ops/trusted_runtime_binary.py',
    } <= set(runner.COMMUNICATION_TRUTH_SOURCES)
    names = {*runner.COMMUNICATION_TRUTH_SOURCES,
             *(node.split('::')[0] for node in runner.COMMUNICATION_TRUTH_TESTS)}
    for name in names:
        path = runner.ROOT / name
        assert path.is_file() and not path.is_symlink()
    # Parse, do not import/execute PG tests: this safety lane cannot open PG.
    for node in runner.COMMUNICATION_TRUTH_TESTS:
        filename, function_name = node.split('::')
        tree = ast.parse((runner.ROOT / filename).read_text(encoding='utf-8'))
        assert function_name in {item.name for item in tree.body if isinstance(item, ast.FunctionDef)}


def test_kpi_experiment_truth_plan_is_fixed_six_and_read_only_by_default(capsys):
    assert runner.main(['--suite', 'kpi-experiment-truth']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'plan_only' and report['database_connections'] == 0
    assert report['tests'] == list(runner.KPI_EXPERIMENT_TRUTH_TESTS)
    assert len(set(report['tests'])) == 6 and report['requires_explicit_execute'] is True
    assert runner.SUITE_TIMEOUTS['kpi-experiment-truth'] == 120
    class NoSQL:
        def execute(self, *args):
            pytest.fail('KPI suite must not seed public tables or migrations')
    runner._seed_suite(NoSQL(), 'kpi-experiment-truth', report)
    assert report['synthetic_seed'] == {
        'kind': 'test_owned_scratch_schemas_only', 'schema_migrations_modified': False}


def test_kpi_experiment_junit_requires_six_exact_real_passes(tmp_path):
    tests = runner.KPI_EXPERIMENT_TRUTH_TESTS
    assert runner._pytest_summary(junit(tmp_path, tests=tests), tests) == {
        'passed': 6, 'failed': 0, 'errors': 0, 'skipped': 0}


@pytest.mark.parametrize('alteration', ['skipped', 'failure', 'error', 'missing', 'duplicate'])
def test_kpi_experiment_rejects_partial_scope(tmp_path, alteration):
    tests = runner.KPI_EXPERIMENT_TRUTH_TESTS
    with pytest.raises(runner.safety.RehearsalError):
        runner._pytest_summary(junit(tmp_path, alteration, tests=tests), tests)


def test_kpi_experiment_source_manifest_and_nodes_bind_reviewed_code():
    assert {
        'backend/app/shared/communication_truth.py',
        'backend/app/domains/kol/profile_detail.py',
        'backend/app/domains/recommendations/communication_evidence.py',
        'backend/app/shared/vkpi_kpi_communication_truth.py',
        'backend/app/domains/staff/kpi_ledger.py',
        'backend/app/domains/staff/kpi_rollup.py',
        'backend/app/domains/staff/decision_staff_kpi.py',
        'backend/app/domains/experiments/scoring.py',
        'backend/app/db/connection.py', 'tests/conftest.py',
    } <= set(runner.KPI_EXPERIMENT_TRUTH_SOURCES)
    for name in runner.KPI_EXPERIMENT_TRUTH_SOURCES:
        path = runner.ROOT / name
        assert path.is_file() and not path.is_symlink()
    for node in runner.KPI_EXPERIMENT_TRUTH_TESTS:
        filename, function_name = node.split('::')
        path = runner.ROOT / filename
        assert path.is_file() and not path.is_symlink()
        tree = ast.parse(path.read_text(encoding='utf-8'))
        assert function_name in {item.name for item in tree.body if isinstance(item, ast.FunctionDef)}


def test_shipment_concurrency_fixed_two_plan_keeps_all_existing_nodes(capsys):
    assert runner.main(['--suite', 'shipment-concurrency']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'plan_only' and report['database_connections'] == 0
    assert report['tests'] == list(runner.SHIPMENT_CONCURRENCY_TESTS)
    assert len(set(report['tests'])) == 2 and report['requires_explicit_execute'] is True
    assert runner.SUITE_TIMEOUTS['shipment-concurrency'] == 60
    class NoSQL:
        def execute(self, *args):
            pytest.fail('shipment suite must not seed public tables or migrations')
    runner._seed_suite(NoSQL(), 'shipment-concurrency', report)
    assert report['synthetic_seed']['schema_migrations_modified'] is False
    assert {'backend/app/shared/message_truth.py',
            'backend/app/domains/evidence/message_truth.py'} <= set(runner.SHIPMENT_CONCURRENCY_SOURCES)
    for name in runner.SHIPMENT_CONCURRENCY_SOURCES:
        path = runner.ROOT / name
        assert path.is_file() and not path.is_symlink()
    for node in runner.SHIPMENT_CONCURRENCY_TESTS:
        filename, function_name = node.split('::')
        path = runner.ROOT / filename
        assert path.is_file() and not path.is_symlink()
        tree = ast.parse(path.read_text(encoding='utf-8'))
        assert function_name in {item.name for item in tree.body if isinstance(item, ast.FunctionDef)}


def test_shipment_junit_requires_exact_two_real_passes(tmp_path):
    tests = runner.SHIPMENT_CONCURRENCY_TESTS
    assert runner._pytest_summary(junit(tmp_path, tests=tests), tests) == {
        'passed': 2, 'failed': 0, 'errors': 0, 'skipped': 0}


@pytest.mark.parametrize('alteration', ['skipped', 'failure', 'error', 'missing', 'duplicate'])
def test_shipment_junit_rejects_partial_scope(tmp_path, alteration):
    tests = runner.SHIPMENT_CONCURRENCY_TESTS
    with pytest.raises(runner.safety.RehearsalError):
        runner._pytest_summary(junit(tmp_path, alteration, tests=tests), tests)


def test_payout_request_fixed_two_plan_has_no_database_or_dispatch(capsys):
    assert runner.main(['--suite', 'payout-request']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'plan_only' and report['database_connections'] == 0
    assert report['tests'] == list(runner.PAYOUT_REQUEST_TESTS)
    assert len(set(report['tests'])) == 2 and report['requires_explicit_execute'] is True
    assert runner.SUITE_TIMEOUTS['payout-request'] == 60
    class NoSQL:
        def execute(self, *args):
            pytest.fail('payout suite must not seed public tables or migrations')
    runner._seed_suite(NoSQL(), 'payout-request', report)
    assert report['synthetic_seed']['schema_migrations_modified'] is False
    for name in runner.PAYOUT_REQUEST_SOURCES:
        path = runner.ROOT / name
        assert path.is_file() and not path.is_symlink()
    for node in runner.PAYOUT_REQUEST_TESTS:
        filename, function_name = node.split('::')
        path = runner.ROOT / filename
        assert path.is_file() and not path.is_symlink()
        tree = ast.parse(path.read_text(encoding='utf-8'))
        assert function_name in {item.name for item in tree.body if isinstance(item, ast.FunctionDef)}


def test_payout_request_junit_requires_exact_two_real_passes(tmp_path):
    tests = runner.PAYOUT_REQUEST_TESTS
    assert runner._pytest_summary(junit(tmp_path, tests=tests), tests) == {
        'passed': 2, 'failed': 0, 'errors': 0, 'skipped': 0}


@pytest.mark.parametrize('alteration', ['skipped', 'failure', 'error', 'missing', 'duplicate'])
def test_payout_request_junit_rejects_partial_scope(tmp_path, alteration):
    tests = runner.PAYOUT_REQUEST_TESTS
    with pytest.raises(runner.safety.RehearsalError):
        runner._pytest_summary(junit(tmp_path, alteration, tests=tests), tests)
