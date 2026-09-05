#!/usr/bin/env python3
"""Run five fixed PG regressions on a newly owned, Unix-socket-only cluster.

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


def _pytest_summary(path: Path) -> dict[str, int]:
    cases = ElementTree.parse(path).getroot().findall('.//testcase')
    expected = {(t.split('::')[0][:-3].replace('/', '.'), t.split('::')[1]) for t in TESTS}
    actual = {(case.get('classname'), case.get('name')) for case in cases}
    safety._check(len(cases) == len(TESTS) and actual == expected, 'unexpected_pg_test_scope')
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


def run_regressions() -> dict:
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
              'status': 'failed', 'tests': list(TESTS), 'started_at': _now(), 'cleanup': {},
              'output': str(output), 'cluster_root': str(cluster),
              'test_hashes': {t.split('::')[0]: hashlib.sha256((ROOT / t.split('::')[0]).read_bytes()).hexdigest() for t in TESTS}}
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
            stage = 'seed_synthetic_skill_ledger'
            safety._check(SEED_MIGRATION.is_file() and not SEED_MIGRATION.is_symlink(),
                          'synthetic_seed_regular_file_required')
            payload = SEED_MIGRATION.read_bytes()
            conn.execute(payload.decode('utf-8'))
            report['synthetic_seed'] = {'migration': SEED_MIGRATION.name,
                                        'sha256': hashlib.sha256(payload).hexdigest(),
                                        'schema_migrations_modified': False}
        stage = 'pytest'
        result = _command([str(ROOT / 'scripts/ops/safe_python.sh'), '-m', 'pytest', '-q', '-rA',
                           '--junitxml=' + str(output / 'pytest.xml'), *TESTS],
                          output, 'pytest.log', _environment(cluster, target), timeout=60)
        report.update(pytest_exit_code=result.returncode, pytest_log=str(output / 'pytest.log'),
                      pytest_junit=str(output / 'pytest.xml'))
        safety._check(result.returncode == 0, 'pg_regression_failed')
        report['test_counts'] = _pytest_summary(output / 'pytest.xml')
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
    args = parser.parse_args(argv)
    report = run_regressions() if args.execute else {
        'status': 'plan_only', 'tests': list(TESTS), 'database_connections': 0,
        'synthetic_only': True, 'requires_explicit_execute': True,
    }
    sys.stdout.write(json.dumps(report, ensure_ascii=False) + '\n')
    return 0 if report['status'] in {'passed', 'plan_only'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
