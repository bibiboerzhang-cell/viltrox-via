"""No PostgreSQL, application database, provider, or ambient credential access."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from threading import RLock

import pytest

from scripts.ops import rehearse_migrations_307_310 as rehearsal


@pytest.fixture(autouse=True)
def no_live_connections(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("PG"):
            monkeypatch.delenv(name)
    def refuse(*args, **kwargs):
        raise AssertionError("A hermetic test attempted a real PostgreSQL connection")
    monkeypatch.setattr(rehearsal.psycopg, "connect", refuse)


@pytest.fixture
def private_cluster():
    with tempfile.TemporaryDirectory(prefix=rehearsal.CLUSTER_PREFIX, dir="/tmp") as directory:
        root = Path(directory).resolve()
        root.chmod(0o700)
        (root / "data").mkdir(mode=0o700)
        (root / "socket").mkdir(mode=0o700)
        yield root


def binding(root, **overrides):
    params = dict(host=str(root), port="55433", user="postgres", dbname="postgres")
    params.update(overrides)
    return rehearsal._cluster_binding(" ".join(f"{key}={value}" for key, value in params.items()), root)


class Rows:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class ContextConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_cli_defaults_to_zero_connection_plan(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "must-not-be-used")
    assert rehearsal.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["database_connections"] == 0
    assert report["migration_names"] == list(rehearsal.MIGRATIONS)
    assert report["starting_versions"] == [306, 307]
    assert report["overall_application_rollback_proven"] is False
    assert rehearsal.main(["--execute"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


@pytest.mark.parametrize("socket_child", [False, True])
def test_binding_accepts_only_explicit_private_socket(private_cluster, socket_child):
    host = private_cluster / "socket" if socket_child else private_cluster
    selected = binding(private_cluster, host=str(host))
    assert selected.params["host"] == str(host)
    assert selected.identity == rehearsal._private_directory(private_cluster)
    assert selected.data_identity == rehearsal._private_directory(private_cluster / "data")


@pytest.mark.parametrize("overrides", [
    {"host": "127.0.0.1"}, {"host": "/tmp"}, {"dbname": "production"},
    {"user": "app"}, {"port": "0"}, {"port": "65536"}, {"port": "abc"},
    {"password": "never-read"}, {"service": "hidden"}, {"hostaddr": "127.0.0.1"},
    {"options": "redirect"},
])
def test_binding_rejects_ambiguous_or_nonprivate_destinations(private_cluster, overrides):
    with pytest.raises(rehearsal.RehearsalError):
        binding(private_cluster, **overrides)


@pytest.mark.parametrize("invalid", ["missing_port", "mode", "data_symlink", "root_symlink"])
def test_binding_rejects_missing_explicit_identity(private_cluster, invalid):
    if invalid == "missing_port":
        dsn = f"host={private_cluster} user=postgres dbname=postgres"
        with pytest.raises(rehearsal.RehearsalError):
            rehearsal._cluster_binding(dsn, private_cluster)
        return
    if invalid == "mode":
        private_cluster.chmod(0o755)
    elif invalid == "data_symlink":
        (private_cluster / "data").rename(private_cluster / "old-data")
        (private_cluster / "data").symlink_to(private_cluster / "old-data", target_is_directory=True)
    else:
        alias = private_cluster / "alias"
        alias.symlink_to(private_cluster, target_is_directory=True)
        with pytest.raises(rehearsal.RehearsalError):
            rehearsal._cluster_binding("", alias)
        return
    with pytest.raises(rehearsal.RehearsalError):
        binding(private_cluster)


def test_connect_disables_passfile_and_checks_data_identity(private_cluster, monkeypatch):
    selected = binding(private_cluster)
    calls = []
    monkeypatch.setattr(rehearsal.psycopg, "connect", lambda **kwargs: calls.append(kwargs))
    rehearsal._connect(selected, "postgres")
    assert calls[0]["passfile"] == "/dev/null"
    assert calls[0]["autocommit"] is True
    assert calls[0]["connect_timeout"] == 5
    assert "statement_timeout=120000" in calls[0]["options"]
    assert "lock_timeout=10000" in calls[0]["options"]
    (private_cluster / "data").rename(private_cluster / "old-data")
    (private_cluster / "data").mkdir(mode=0o700)
    with pytest.raises(rehearsal.RehearsalError, match="data_identity_changed"):
        rehearsal._connect(selected, "postgres")
    assert len(calls) == 1


@pytest.mark.parametrize("variable", ["PGHOSTADDR", "PGSERVICE", "PGPASSFILE", "PGPASSWORD"])
def test_connect_rejects_ambient_libpq_configuration(private_cluster, monkeypatch, variable):
    monkeypatch.setenv(variable, "not-to-be-read-or-emitted")
    with pytest.raises(rehearsal.RehearsalError, match="ambient_libpq_configuration"):
        rehearsal._connect(binding(private_cluster), "postgres")


class AdminIdentity:
    def __init__(self, root, *, locked=True, others=(), sessions=0, identifier="123456789"):
        self.row = ("postgres", None, str(root / "data"), identifier)
        self.locked, self.others, self.sessions = locked, others, sessions
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        if "current_database()" in statement:
            return Rows([self.row])
        if "pg_try_advisory_lock" in statement:
            return Rows([(self.locked,)])
        if "FROM pg_database" in statement:
            return Rows(self.others)
        if "FROM pg_stat_activity" in statement:
            return Rows([(self.sessions,)])
        raise AssertionError(statement)


def test_admin_binds_system_id_and_holds_session_lock(private_cluster):
    conn = AdminIdentity(private_cluster)
    selected = rehearsal._bind_admin(conn, binding(private_cluster))
    assert selected.system_identifier == "123456789"
    assert "pg_try_advisory_lock" in conn.statements[1]
    assert all("unlock" not in statement for statement in conn.statements)
    with pytest.raises(rehearsal.RehearsalError, match="system_identifier_changed"):
        rehearsal._verify_server(conn, replace(selected, system_identifier="9"), "postgres")


@pytest.mark.parametrize("kwargs,code", [
    ({"locked": False}, "another_rehearsal"),
    ({"others": [("existing",)]}, "other_databases"),
    ({"sessions": 1}, "other_client_sessions"),
])
def test_admin_rejects_nonexclusive_cluster(private_cluster, kwargs, code):
    with pytest.raises(rehearsal.RehearsalError, match=code):
        rehearsal._bind_admin(AdminIdentity(private_cluster, **kwargs), binding(private_cluster))


class TransactionModel:
    """Tracks transaction boundaries; this is not a substitute for real PG proof."""
    def __init__(self, applied=()):
        self.state = {"ledger": set(applied), "schema": [], "enabled": True}
        self.statements = []
        self.commits = self.rollbacks = 0

    @contextmanager
    def transaction(self):
        previous = deepcopy(self.state)
        try:
            yield
        except Exception:
            self.state = previous
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def commit(self):
        raise AssertionError("No per-migration commit is allowed")

    def execute(self, statement, params=()):
        self.statements.append(statement)
        if statement == "SELECT version_key FROM schema_migrations":
            return Rows((name,) for name in self.state["ledger"])
        if statement == rehearsal.MARK_SQL:
            self.state["ledger"].add(params[0])
        elif statement.startswith("migration:"):
            self.state["schema"].append(statement)
            if statement == "migration:310":
                self.state["enabled"] = False
        elif statement == "SELECT 1 / 0":
            raise rehearsal.psycopg.errors.DivisionByZero("synthetic")
        else:
            assert statement in (rehearsal.LEDGER_SQL, rehearsal.LOCK_SQL)
        return Rows()


def migration_models():
    return tuple(rehearsal.Migration(name, f"migration:{name[:3]}", "synthetic")
                 for name in rehearsal.MIGRATIONS)


def test_pending_migrations_share_one_transaction_and_production_lock():
    conn = TransactionModel()
    assert rehearsal._apply_pending(conn, migration_models()) == list(rehearsal.MIGRATIONS)
    assert conn.statements[:2] == [rehearsal.LEDGER_SQL, rehearsal.LOCK_SQL]
    assert conn.commits == 1 and conn.rollbacks == 0
    assert conn.state["ledger"] == set(rehearsal.MIGRATIONS)
    assert conn.state["enabled"] is False


@pytest.mark.parametrize("fail_after", rehearsal.MIGRATIONS)
def test_each_injected_failure_rolls_back_entire_batch(fail_after):
    conn = TransactionModel()
    before = deepcopy(conn.state)
    with pytest.raises(rehearsal.psycopg.errors.DivisionByZero):
        rehearsal._apply_pending(conn, migration_models(), fail_after=fail_after)
    assert conn.state == before
    assert conn.commits == 0 and conn.rollbacks == 1


def test_already307_and_both_replay_contracts():
    conn = TransactionModel(rehearsal.MIGRATIONS[:1])
    assert rehearsal._apply_pending(conn, migration_models()) == list(rehearsal.MIGRATIONS[1:])
    assert "migration:307" not in conn.statements
    conn.state["enabled"] = True
    assert rehearsal._apply_pending(conn, migration_models()) == []
    assert conn.state["enabled"] is True
    assert rehearsal._apply_pending(conn, migration_models(), replay=True) == list(rehearsal.MIGRATIONS)
    assert conn.state["enabled"] is False


def test_real_source_plan_has_exact_pending_files_without_transaction_control():
    prefix, pending = rehearsal._source_plan(rehearsal.ROOT)
    assert prefix[-1].name == rehearsal.BASELINE
    assert [item.name for item in pending] == list(rehearsal.MIGRATIONS)
    assert all(len(item.sha256) == 64 for item in pending)
    assert len(rehearsal._prefix_hashes(prefix)) == len(prefix)


class CleanupAdmin:
    def __init__(self, oids):
        self.oids = iter(oids)
        self.statements = []

    def execute(self, statement, params=()):
        text = statement if isinstance(statement, str) else statement.as_string()
        self.statements.append((text, params))
        if text.startswith("SELECT oid"):
            oid = next(self.oids)
            return Rows([] if oid is None else [(oid,)])
        return Rows()


@pytest.mark.parametrize("expected,oids,code", [
    (None, [], "oid_unknown"), (41, [42], "oid_changed"), (41, [41, 42], "oid_changed"),
])
def test_cleanup_never_drops_unowned_or_replaced_database(private_cluster, monkeypatch, expected, oids, code):
    monkeypatch.setattr(rehearsal, "_verify_server", lambda *args: "123")
    conn = CleanupAdmin(oids)
    with pytest.raises(rehearsal.RehearsalError, match=code):
        rehearsal._drop_owned_database(conn, binding(private_cluster), rehearsal.TARGET_PREFIX + "a" * 24, expected)
    assert not any(text.startswith("DROP DATABASE") for text, _ in conn.statements)


def test_cleanup_rechecks_oid_before_drop_and_uses_oid_for_sessions(private_cluster, monkeypatch):
    monkeypatch.setattr(rehearsal, "_verify_server", lambda *args: "123")
    conn = CleanupAdmin([41, 41, None])
    target = rehearsal.TARGET_PREFIX + "a" * 24
    rehearsal._drop_owned_database(conn, binding(private_cluster), target, 41)
    termination = [params for text, params in conn.statements if "pg_terminate_backend" in text]
    assert termination == [(41, target)]
    assert len([text for text, _ in conn.statements if text.startswith("DROP DATABASE")]) == 1


@pytest.mark.parametrize("failure", ["none", "create", "upgrade", "cleanup"])
def test_owned_scenario_reports_cleanup_and_preserves_failure_stage(private_cluster, monkeypatch, failure):
    events = []
    class Admin:
        def execute(self, *args):
            events.append("create")
            if failure == "create":
                raise RuntimeError("credential-like-secret-must-not-appear")
    def scenario(*args, **kwargs):
        if failure == "upgrade":
            raise RuntimeError("credential-like-secret-must-not-appear")
        return {"starting_version": 306}
    def cleanup(*args):
        events.append("cleanup")
        assert args[-1] == 41
        if failure == "cleanup":
            raise rehearsal.RehearsalError("cleanup_target_oid_changed")
    monkeypatch.setattr(rehearsal, "_database_oid", lambda *args: 41)
    monkeypatch.setattr(rehearsal, "_connect", lambda *args: ContextConnection())
    monkeypatch.setattr(rehearsal, "_verify_server", lambda *args: "123")
    monkeypatch.setattr(rehearsal, "_scenario", scenario)
    monkeypatch.setattr(rehearsal, "_slot_concurrency", lambda *args: {"inserted": 5})
    monkeypatch.setattr(rehearsal, "_drop_owned_database", cleanup)
    result = rehearsal._run_owned_scenario(Admin(), binding(private_cluster), [], (), already_307=False)
    assert "credential-like-secret" not in json.dumps(result)
    assert result["status"] == ("passed" if failure == "none" else "failed")
    if failure == "create":
        assert events == ["create"]
        assert result["cleanup_state"] == "creation_unconfirmed_no_drop_attempted"
        assert result["failed_stage"] == "create_synthetic_database"
    else:
        assert events == ["create", "cleanup"]
        assert result["cleanup_state"] == ("blocked" if failure == "cleanup" else "dropped")
        if failure == "upgrade":
            assert result["failed_stage"] == "upgrade_306_base"


def test_slot_harness_uses_eight_distinct_connections_and_day_scoped_slots(private_cluster, monkeypatch):
    lock, entries, connections = RLock(), set(), []
    class SlotConnection(ContextConnection):
        @contextmanager
        def transaction(self):
            with lock:
                yield

        def execute(self, statement, params=()):
            if statement == rehearsal.SLOT_SQL:
                key = params[:2]
                if key in entries:
                    return Rows()
                entries.add(key)
                return Rows([(params[1],)])
            if statement.startswith("SELECT COUNT"):
                return Rows([(sum(day == "2026-09-04" for day, _ in entries),)])
            if statement.startswith("INSERT INTO apify_jobs"):
                return Rows([(99,)])
            if statement.startswith("SELECT job_id IS NULL"):
                return Rows([(True,)])
            assert statement.startswith(("UPDATE vkpi_kol_search", "DELETE FROM apify_jobs"))
            return Rows()
    def connect(*args):
        conn = SlotConnection()
        connections.append(conn)
        return conn
    monkeypatch.setattr(rehearsal, "_connect", connect)
    monkeypatch.setattr(rehearsal, "_verify_server", lambda *args: "123")
    result = rehearsal._slot_concurrency(binding(private_cluster), rehearsal.TARGET_PREFIX + "a" * 24)
    assert len(connections) == 9  # Eight contenders plus one verification connection.
    assert result == {"connections": 8, "requested_total": 40, "inserted": 5,
                      "next_day_independent": True, "job_delete_sets_null": True}
    assert len(entries) == 10


def test_preflight_failure_never_emits_dsn_or_claims_rollback(tmp_path):
    report = rehearsal.run_rehearsal("password=do-not-emit-secret", tmp_path)
    assert report["status"] == "failed"
    assert report["failed_stage"] == "preflight"
    assert report["business_database_accessed"] is False
    assert report["overall_application_rollback_proven"] is False
    assert "do-not-emit-secret" not in json.dumps(report)
