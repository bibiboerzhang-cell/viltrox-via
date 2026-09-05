"""Hermetic tests for the fixed, owned PostgreSQL regression runner."""
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


def junit(tmp_path, alteration=''):
    root = ElementTree.Element('testsuites')
    suite = ElementTree.SubElement(root, 'testsuite')
    for nodeid in runner.TESTS:
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
