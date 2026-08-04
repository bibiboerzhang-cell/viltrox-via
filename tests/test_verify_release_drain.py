from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "verify_release_drain.py"
SPEC = importlib.util.spec_from_file_location("verify_release_drain", SCRIPT)
assert SPEC and SPEC.loader
drain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drain)


class FakeRedis:
    def __init__(
        self,
        *,
        lag: int | None = 130,
        pending: int = 0,
        undelivered: bool = False,
        historical_consumers: int = 17,
    ) -> None:
        self.lag = lag
        self.pending = pending
        self.undelivered = undelivered
        self.historical_consumers = historical_consumers
        self.xrange_min = ""

    def ping(self) -> bool:
        return True

    def xinfo_groups(self, stream: str):
        assert stream == "vkpi:jobs"
        return [
            {
                "name": "vkpi-workers",
                "pending": self.pending,
                "last-delivered-id": "42-0",
                "lag": self.lag,
                "consumers": self.historical_consumers,
            }
        ]

    def xpending(self, stream: str, group: str):
        assert (stream, group) == ("vkpi:jobs", "vkpi-workers")
        return {"pending": self.pending, "min": None, "max": None, "consumers": []}

    def xrange(self, stream: str, *, min: str, max: str, count: int):
        assert stream == "vkpi:jobs"
        assert max == "+"
        assert count == 1
        self.xrange_min = min
        return [("43-0", {"task_id": "not-returned-by-probe"})] if self.undelivered else []

    def xinfo_consumers(self, stream: str, group: str):
        assert (stream, group) == ("vkpi:jobs", "vkpi-workers")
        return [{"name": f"old-{index}"} for index in range(self.historical_consumers)]


class FakeDbResult:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.row = row

    def fetchone(self) -> tuple[object, ...]:
        return self.row


class FakeDb:
    def __init__(
        self,
        *,
        current_migration: str,
        present_tables: set[str],
        counts: dict[str, int] | None = None,
        database_name: str = "viltrox2",
        search_path: str = "pg_catalog, public",
    ) -> None:
        self.current_migration = current_migration
        self.present_tables = present_tables
        self.counts = counts or {}
        self.database_name = database_name
        self.search_path = search_path
        self.rollback_calls = 0
        self.queries: list[str] = []
        self._count_queries = {
            " ".join(str(spec["sql"]).split()): str(spec["key"])
            for spec in drain.DB_COUNT_SPECS
        }

    def execute(
        self,
        statement: str,
        params: tuple[str, ...] | None = None,
    ) -> FakeDbResult:
        normalized = " ".join(statement.split())
        self.queries.append(normalized)
        if normalized == "SHOW transaction_read_only":
            return FakeDbResult(("on",))
        if normalized == "SHOW search_path":
            return FakeDbResult((self.search_path,))
        if normalized == "SELECT pg_catalog.current_database()":
            return FakeDbResult((self.database_name,))
        if normalized == "SELECT MAX(version_key) FROM public.schema_migrations":
            return FakeDbResult((self.current_migration,))
        if normalized == "SELECT pg_catalog.to_regclass(%s) IS NOT NULL":
            assert params and len(params) == 1
            table = params[0].removeprefix("public.")
            return FakeDbResult((table in self.present_tables,))
        if normalized in self._count_queries:
            key = self._count_queries[normalized]
            return FakeDbResult((self.counts.get(key, 0),))
        raise AssertionError(f"unexpected query: {normalized}")

    def rollback(self) -> None:
        self.rollback_calls += 1


def _collect(client: FakeRedis) -> dict:
    return drain.collect_redis_state(
        client,
        stream_key="vkpi:jobs",
        group_name="vkpi-workers",
    )


def test_raw_lag_130_and_historical_consumers_do_not_block_when_actually_drained() -> None:
    client = FakeRedis(lag=130, pending=0, undelivered=False, historical_consumers=23)
    result = _collect(client)

    assert result["passed"] is True
    assert result["pending_count"] == 0
    assert result["undelivered_count"] == 0
    assert client.xrange_min == "(42-0"
    assert result["diagnostics"] == {
        "raw_xinfo_lag": 130,
        "raw_xinfo_consumer_count": 23,
        "historical_consumer_count": 23,
        "lag_or_consumer_count_blocks_release": False,
    }


def test_pending_entries_block_even_when_no_undelivered_entry_exists() -> None:
    result = _collect(FakeRedis(lag=0, pending=2, undelivered=False))

    assert result["passed"] is False
    assert result["pending_count"] == 2
    assert result["blocking_reasons"] == ["redis_pending_not_zero"]


def test_entry_after_last_delivered_blocks_even_when_pending_is_zero() -> None:
    result = _collect(FakeRedis(lag=0, pending=0, undelivered=True))

    assert result["passed"] is False
    assert result["undelivered_count"] == 1
    assert result["blocking_reasons"] == [
        "redis_undelivered_after_last_delivered"
    ]


def test_expired_workflows_and_durable_plan_states_are_diagnostic_only() -> None:
    all_tables = set(drain.TABLE_INTRODUCTION_MIGRATIONS)
    diagnostics = {
        "workflow_runs_expired_or_unleased": 7,
        "agent_plans_executing_plan_only": 3,
        "agent_tool_runs_approved_plan_only": 4,
        "llm_batches_in_progress_durable": 5,
    }
    connection = FakeDb(
        current_migration="275_vkpi_llm_cost_precision.sql",
        present_tables=all_tables,
        counts=diagnostics,
    )

    result = drain.collect_db_state(
        connection,
        expected_database="viltrox2",
        current_migration="275_vkpi_llm_cost_precision.sql",
    )

    assert result["passed"] is True
    assert all(value == 0 for value in result["active_counts"].values())
    assert result["diagnostic_counts"] == diagnostics
    assert result["diagnostic_nonzero"] == list(diagnostics)
    assert result["check_status"]["workflow_runs_unfenced"] == "superseded"
    assert connection.rollback_calls == 1


def test_only_running_workflow_with_a_live_lease_blocks_release() -> None:
    connection = FakeDb(
        current_migration="275_vkpi_llm_cost_precision.sql",
        present_tables=set(drain.TABLE_INTRODUCTION_MIGRATIONS),
        counts={
            "workflow_runs_live": 1,
            "workflow_runs_expired_or_unleased": 9,
        },
    )

    result = drain.collect_db_state(
        connection,
        expected_database="viltrox2",
        current_migration="275_vkpi_llm_cost_precision.sql",
    )

    assert result["passed"] is False
    assert result["active_counts"]["workflow_runs_live"] == 1
    assert result["blocking_reasons"] == [
        "database_workflow_runs_live_not_zero"
    ]
    assert result["diagnostic_counts"]["workflow_runs_expired_or_unleased"] == 9


def test_unresolved_provider_boundaries_block_after_local_workers_stop() -> None:
    connection = FakeDb(
        current_migration="275_vkpi_llm_cost_precision.sql",
        present_tables=set(drain.TABLE_INTRODUCTION_MIGRATIONS),
        counts={
            "advisor_turns_provider_started": 1,
            "apify_budget_reservations_open": 2,
            "llm_budget_reservations_open": 3,
        },
    )

    result = drain.collect_db_state(
        connection,
        expected_database="viltrox2",
        current_migration="275_vkpi_llm_cost_precision.sql",
    )

    assert result["passed"] is False
    assert result["blocking_reasons"] == [
        "database_advisor_turns_provider_started_not_zero",
        "database_apify_budget_reservations_open_not_zero",
        "database_llm_budget_reservations_open_not_zero",
    ]


def test_old_schema_absent_future_tables_are_zero_and_diagnostic() -> None:
    connection = FakeDb(
        current_migration="166_vkpi_llm_batches.sql",
        present_tables={
            "job_execution_ledger",
            "apify_jobs",
            "vkpi_action_inbox",
            "vkpi_llm_batches",
        },
        counts={"llm_batches_in_progress_durable": 6},
    )

    result = drain.collect_db_state(
        connection,
        expected_database="viltrox2",
        current_migration="166_vkpi_llm_batches.sql",
    )

    assert result["passed"] is True
    assert result["active_counts"]["workflow_runs_live"] == 0
    assert result["active_counts"]["provider_claims_live"] == 0
    assert result["diagnostic_counts"]["llm_batches_in_progress_durable"] == 6
    assert result["tables"]["vkpi_workflow_runs"] == {
        "introduced_by": "193_vkpi_workflow_runs.sql",
        "expected": False,
        "present": False,
        "status": "not_introduced",
    }
    assert result["check_status"]["workflow_runs_live"] == "not_introduced"
    assert connection.rollback_calls == 1


def test_pre_fencing_running_workflow_fails_closed_as_active() -> None:
    connection = FakeDb(
        current_migration="254_vkpi_provider_execution_fencing.sql",
        present_tables=set(drain.TABLE_INTRODUCTION_MIGRATIONS),
        counts={"workflow_runs_unfenced": 2},
    )

    result = drain.collect_db_state(
        connection,
        expected_database="viltrox2",
        current_migration="254_vkpi_provider_execution_fencing.sql",
    )

    assert result["passed"] is False
    assert result["active_counts"]["workflow_runs_unfenced"] == 2
    assert result["active_counts"]["workflow_runs_live"] == 0
    assert result["check_status"]["workflow_runs_live"] == "not_introduced"
    assert result["blocking_reasons"] == [
        "database_workflow_runs_unfenced_not_zero"
    ]


def test_expected_table_missing_fails_closed_and_rolls_back() -> None:
    connection = FakeDb(
        current_migration="275_vkpi_llm_cost_precision.sql",
        present_tables=(
            set(drain.TABLE_INTRODUCTION_MIGRATIONS)
            - {"vkpi_provider_execution_claims"}
        ),
    )

    try:
        drain.collect_db_state(
            connection,
            expected_database="viltrox2",
            current_migration="275_vkpi_llm_cost_precision.sql",
        )
    except drain.DrainProbeError as exc:
        assert str(exc) == "expected reviewed drain table is missing"
    else:
        raise AssertionError("missing expected table did not fail closed")
    assert connection.rollback_calls == 1


def test_explicit_migration_must_match_source_database() -> None:
    connection = FakeDb(
        current_migration="274_vkpi_data_quality_actions.sql",
        present_tables=set(drain.TABLE_INTRODUCTION_MIGRATIONS),
    )

    try:
        drain.collect_db_state(
            connection,
            expected_database="viltrox2",
            current_migration="275_vkpi_llm_cost_precision.sql",
        )
    except drain.DrainProbeError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("mismatched migration did not fail closed")
    assert connection.rollback_calls == 1


def test_database_query_contract_separates_live_from_diagnostic_state() -> None:
    specs = {str(spec["key"]): spec for spec in drain.DB_COUNT_SPECS}

    assert specs["workflow_runs_live"]["blocking"] is True
    assert specs["workflow_runs_unfenced"]["blocking"] is True
    assert "status='running' AND lease_expires_at>NOW()" in specs[
        "workflow_runs_live"
    ]["sql"]
    for key in (
        "workflow_runs_expired_or_unleased",
        "agent_plans_executing_plan_only",
        "agent_tool_runs_approved_plan_only",
        "llm_batches_in_progress_durable",
    ):
        assert specs[key]["blocking"] is False
    assert specs["agent_tool_runs_approved_plan_only"]["sql"].endswith(
        "status='approved'"
    )
    assert specs["llm_batches_in_progress_durable"]["sql"].endswith(
        "status='in_progress'"
    )
    assert specs["advisor_turns_provider_started"]["blocking"] is True
    assert specs["advisor_turns_provider_started"]["sql"].endswith(
        "state='provider_started'"
    )
    for key in (
        "apify_budget_reservations_open",
        "llm_budget_reservations_open",
    ):
        assert specs[key]["blocking"] is True
        assert "state IN ('reserved','provider_started','unknown')" in specs[key][
            "sql"
        ]
    assert all(" FROM public." in " ".join(str(spec["sql"]).split()) for spec in specs.values())


def test_database_url_rejects_query_parameters_that_can_override_identity() -> None:
    for query in (
        "dbname=other",
        "db%6Eame=other",
        "host=attacker.invalid",
        "hostaddr=203.0.113.10",
        "port=6543",
        "user=other",
        "password=do-not-print",
        "passfile=%2Ftmp%2Fother.pass",
        "service=other",
        "servicefile=%2Ftmp%2Fother.conf",
        "options=-c%20search_path%3Dattacker",
        "sslcert=%2Ftmp%2Fother.crt",
        "sslkey=%2Ftmp%2Fother.key",
        "replication=database",
        "target_session_attrs=read-write",
        "load_balance_hosts=random",
    ):
        try:
            drain._validated_database_url(
                f"postgresql://app:top-secret@db.internal:5432/viltrox2?{query}",
                "viltrox2",
            )
        except drain.DrainProbeError as exc:
            assert str(exc) == "DATABASE_URL query parameters may alter connection identity"
            assert "top-secret" not in str(exc)
        else:
            raise AssertionError(f"identity-changing query was accepted: {query.split('=', 1)[0]}")


def test_database_url_allows_reviewed_transport_only_query_parameters() -> None:
    url = (
        "postgresql://app:top-secret@db.internal:5432/viltrox2"
        "?sslmode=require&application_name=release-drain&connect_timeout=5"
    )
    assert drain._validated_database_url(url, "viltrox2") == url


def test_query_override_cli_failure_emits_no_database_credentials(
    tmp_path: Path,
    capsys,
) -> None:
    env_file = tmp_path / "release.env"
    env_file.write_text(
        "DATABASE_URL=postgresql://app:top-secret@db.internal:5432/viltrox2?dbname=other\n"
        "REDIS_URL=redis://:redis-secret@redis.internal:6379/0\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    exit_code = drain.main(
        [
            "--env-file",
            str(env_file),
            "--expected-database",
            "viltrox2",
            "--current-migration",
            "275_vkpi_llm_cost_precision.sql",
        ]
    )
    captured = capsys.readouterr()
    emitted = f"{captured.out}\n{captured.err}"
    assert exit_code == 2
    assert "top-secret" not in emitted
    assert "redis-secret" not in emitted
    assert "db.internal" not in emitted


def test_actual_database_identity_mismatch_fails_before_migration_or_counts() -> None:
    connection = FakeDb(
        current_migration="275_vkpi_llm_cost_precision.sql",
        present_tables=set(drain.TABLE_INTRODUCTION_MIGRATIONS),
        database_name="other",
    )

    try:
        drain.collect_db_state(
            connection,
            expected_database="viltrox2",
            current_migration="275_vkpi_llm_cost_precision.sql",
        )
    except drain.DrainProbeError as exc:
        assert str(exc) == "connected PostgreSQL database does not match expected database"
    else:
        raise AssertionError("actual database mismatch did not fail closed")
    assert connection.rollback_calls == 1
    assert not any("schema_migrations" in query or "COUNT(*)" in query for query in connection.queries)


def test_user_schema_search_path_shadow_fails_before_identity_or_counts() -> None:
    connection = FakeDb(
        current_migration="275_vkpi_llm_cost_precision.sql",
        present_tables=set(drain.TABLE_INTRODUCTION_MIGRATIONS),
        search_path='"$user", public',
    )

    try:
        drain.collect_db_state(
            connection,
            expected_database="viltrox2",
            current_migration="275_vkpi_llm_cost_precision.sql",
        )
    except drain.DrainProbeError as exc:
        assert str(exc) == "PostgreSQL search_path is not fixed to pg_catalog,public"
    else:
        raise AssertionError("user-schema search_path did not fail closed")
    assert connection.rollback_calls == 1
    assert connection.queries == ["SHOW transaction_read_only", "SHOW search_path"]


def test_helper_has_no_queue_or_history_cleanup_command() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()
    for forbidden in (".xack(", ".xdel(", ".xtrim(", ".flushdb(", ".flushall("):
        assert forbidden not in source
    assert "default_transaction_read_only=on" in source
    assert "search_path=pg_catalog,public" in source
    assert "from public.schema_migrations" in source
    assert "select pg_catalog.current_database()" in source
    assert 'parser.add_argument("--current-migration", required=true)' in source
    assert '"history_mutated": false' in source
