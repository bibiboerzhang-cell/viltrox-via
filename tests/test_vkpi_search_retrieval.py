from __future__ import annotations

import inspect
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import vkpi_search as search_router  # noqa: E402
from app.db import connection as db_connection  # noqa: E402
from app.domains.dashboard import kol_distribution, summary_scope  # noqa: E402


_MANAGER = {"staff_id": 1, "role": "admin", "is_owner": 1}


def _sqlite_search_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            platform TEXT,
            handle TEXT,
            display_name TEXT,
            avatar_url TEXT,
            followers INTEGER,
            duplicate_of_id INTEGER
        );
        CREATE TABLE vkpi_projects (
            id INTEGER PRIMARY KEY,
            project_uid TEXT,
            project_name TEXT,
            stage TEXT,
            stage_status TEXT,
            platform TEXT
        );
        CREATE TABLE vkpi_events (
            id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT,
            start_date TEXT,
            end_date TEXT,
            team_ids TEXT,
            created_at TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "youtube", "literal100", "100% Camera", "a.jpg", 100, None),
            (2, "youtube", "other", "Other Creator", "b.jpg", 999, None),
            (3, "youtube", "scope-hidden", "100% Hidden", "c.jpg", 500, None),
            (4, "youtube", "duplicate100", "100% Duplicate", "d.jpg", 5000, 1),
        ],
    )
    conn.executemany(
        "INSERT INTO vkpi_projects VALUES (?, ?, ?, ?, ?, ?)",
        [
            (10, "p-visible", "100% Launch", "discovery", "active", "youtube"),
            (11, "p-hidden", "100% Hidden", "discovery", "active", "youtube"),
            (12, "p-deleted", "100% Deleted", "cancelled", "deleted", "youtube"),
        ],
    )
    conn.executemany(
        "INSERT INTO vkpi_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("evt-visible", "100% Expo", "planning", "2026-08-01", "2026-08-02", "[7]", "2026-07-02"),
            ("evt-hidden", "100% Private", "planning", "2026-09-01", "2026-09-02", "[8]", "2026-07-03"),
        ],
    )
    conn.commit()
    return conn


def _use_sqlite(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(db_connection, "get_conn", lambda: conn)
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: False)


def test_sqlite_like_fallback_treats_wildcards_as_literals_and_honors_limit(monkeypatch):
    conn = _sqlite_search_conn()
    _use_sqlite(monkeypatch, conn)

    payload = search_router.vkpi_global_search(q="100%", limit=1, staff=_MANAGER)

    assert payload["q"] == "100%"
    assert [row["id"] for row in payload["kols"]] == [3]
    assert [row["id"] for row in payload["projects"]] == [11]
    assert [row["id"] for row in payload["events"]] == ["evt-hidden"]
    assert set(payload["kols"][0]) == {
        "id", "platform", "handle", "display_name", "avatar_url", "followers"
    }
    assert set(payload["projects"][0]) == {
        "id", "project_uid", "project_name", "stage", "stage_status", "platform"
    }
    assert set(payload["events"][0]) == {
        "id", "title", "status", "start_date", "end_date"
    }
    assert payload["source_status"] == {
        "kols": {"status": "ready", "result_count": 1},
        "projects": {"status": "ready", "result_count": 1},
        "events": {"status": "ready", "result_count": 1},
    }


def test_deleted_projects_are_excluded_without_becoming_a_search_failure(monkeypatch):
    conn = _sqlite_search_conn()
    _use_sqlite(monkeypatch, conn)

    payload = search_router.vkpi_global_search(
        q="100% Deleted",
        limit=5,
        staff=_MANAGER,
    )

    assert payload["projects"] == []
    assert payload["source_status"]["projects"] == {
        "status": "ready",
        "result_count": 0,
    }


def test_non_manager_scope_contract_is_preserved_on_sqlite(monkeypatch):
    conn = _sqlite_search_conn()
    _use_sqlite(monkeypatch, conn)
    monkeypatch.setattr(kol_distribution, "_staff_visible_kols_sql", lambda staff_id: "SELECT 1")
    monkeypatch.setattr(summary_scope, "_actor_projects_sql", lambda staff_id: "SELECT 10")

    payload = search_router.vkpi_global_search(
        q="100%",
        limit=5,
        staff={"staff_id": 7, "role": "employee", "is_owner": 0},
    )

    assert [row["id"] for row in payload["kols"]] == [1]
    assert [row["id"] for row in payload["projects"]] == [10]
    assert [row["id"] for row in payload["events"]] == ["evt-visible"]
    assert "team_ids" not in payload["events"][0]


def test_event_scope_searches_beyond_legacy_five_x_candidate_window(monkeypatch):
    conn = _sqlite_search_conn()
    conn.executemany(
        "INSERT INTO vkpi_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                f"hidden-{index:03d}",
                "Camera Launch",
                "planning",
                "2099-01-01",
                "2099-01-02",
                "[8]",
                "2099-01-01",
            )
            for index in range(120)
        ]
        + [
            (
                "visible-after-legacy-cap",
                "Camera Launch",
                "planning",
                "2026-01-01",
                "2026-01-02",
                "[7]",
                "2026-01-01",
            )
        ],
    )
    conn.commit()
    _use_sqlite(monkeypatch, conn)
    monkeypatch.setattr(kol_distribution, "_staff_visible_kols_sql", lambda staff_id: "SELECT 1")
    monkeypatch.setattr(summary_scope, "_actor_projects_sql", lambda staff_id: "SELECT 10")

    payload = search_router.vkpi_global_search(
        q="Camera Launch",
        limit=5,
        staff={"id": 7, "role": "employee", "is_owner": 0},
    )

    assert [row["id"] for row in payload["events"]] == ["visible-after-legacy-cap"]
    assert payload["source_status"]["events"]["status"] == "ready"


def test_event_scope_candidate_cap_reports_degraded_not_true_zero(monkeypatch):
    conn = _sqlite_search_conn()
    conn.executemany(
        "INSERT INTO vkpi_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                f"cap-hidden-{index:03d}",
                "Capped Launch",
                "planning",
                "2099-01-01",
                "2099-01-02",
                "[8]",
                "2099-01-01",
            )
            for index in range(80)
        ],
    )
    conn.commit()
    _use_sqlite(monkeypatch, conn)
    monkeypatch.setattr(kol_distribution, "_staff_visible_kols_sql", lambda staff_id: "SELECT 1")
    monkeypatch.setattr(summary_scope, "_actor_projects_sql", lambda staff_id: "SELECT 10")

    payload = search_router.vkpi_global_search(
        q="Capped Launch",
        limit=1,
        staff={"id": 7, "role": "employee", "is_owner": 0},
    )

    assert payload["events"] == []
    assert payload["source_status"]["events"] == {
        "status": "degraded",
        "result_count": 0,
        "reason": "visibility_scope_candidate_cap_reached",
    }


def test_real_staff_context_id_shape_is_scoped_instead_of_blocked(monkeypatch):
    conn = _sqlite_search_conn()
    _use_sqlite(monkeypatch, conn)
    monkeypatch.setattr(kol_distribution, "_staff_visible_kols_sql", lambda staff_id: "SELECT 1")
    monkeypatch.setattr(summary_scope, "_actor_projects_sql", lambda staff_id: "SELECT 10")

    payload = search_router.vkpi_global_search(
        q="100%",
        limit=5,
        staff={"id": 7, "role": "employee", "is_owner": 0},
    )

    assert [row["id"] for row in payload["kols"]] == [1]
    assert [row["id"] for row in payload["projects"]] == [10]
    assert [row["id"] for row in payload["events"]] == ["evt-visible"]
    assert all(item["status"] != "blocked" for item in payload["source_status"].values())


def test_staff_missing_user_id_collision_is_blocked_before_search(monkeypatch):
    conn = _sqlite_search_conn()
    _use_sqlite(monkeypatch, conn)
    monkeypatch.setattr(
        kol_distribution,
        "_staff_visible_kols_sql",
        lambda _staff_id: (_ for _ in ()).throw(AssertionError("must not build a staff scope")),
    )

    payload = search_router.vkpi_global_search(
        q="100%",
        limit=5,
        staff={
            "user_id": 7,
            "role": "employee",
            "organization_scope_status": "staff_missing",
        },
    )

    assert payload["kols"] == []
    assert payload["projects"] == []
    assert payload["events"] == []
    assert all(item["status"] == "blocked" for item in payload["source_status"].values())


@pytest.mark.parametrize("role", ["manager", "admin"])
def test_unresolved_privileged_role_cannot_bypass_global_scope(monkeypatch, role):
    conn = _sqlite_search_conn()
    _use_sqlite(monkeypatch, conn)

    payload = search_router.vkpi_global_search(
        q="100%",
        limit=5,
        staff={
            "user_id": 7,
            "role": role,
            "organization_scope_status": "staff_missing",
        },
    )

    assert payload["kols"] == []
    assert payload["projects"] == []
    assert payload["events"] == []
    assert all(item["status"] == "blocked" for item in payload["source_status"].values())


def test_unresolved_privileged_role_cannot_use_cross_database_search():
    with pytest.raises(HTTPException) as exc_info:
        search_router.vkpi_search(
            q="camera",
            limit=20,
            staff={
                "user_id": 7,
                "role": "manager",
                "organization_scope_status": "staff_missing",
            },
        )

    assert exc_info.value.status_code == 403


def test_kol_search_excludes_merged_duplicate_rows(monkeypatch):
    conn = _sqlite_search_conn()
    _use_sqlite(monkeypatch, conn)

    payload = search_router.vkpi_global_search(q="100%", limit=20, staff=_MANAGER)

    assert 4 not in [row["id"] for row in payload["kols"]]


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _ProjectFailConn:
    def __init__(self, delegate: sqlite3.Connection):
        self.delegate = delegate

    def execute(self, sql, params=()):
        if "FROM vkpi_projects" in sql:
            raise RuntimeError("simulated project source failure")
        return self.delegate.execute(sql, params)


def test_source_status_distinguishes_zero_results_from_group_failure(monkeypatch):
    conn = _ProjectFailConn(_sqlite_search_conn())
    _use_sqlite(monkeypatch, conn)  # type: ignore[arg-type]

    payload = search_router.vkpi_global_search(
        q="does-not-exist",
        limit=5,
        staff=_MANAGER,
    )

    assert payload["kols"] == []
    assert payload["projects"] == []
    assert payload["events"] == []
    assert payload["source_status"]["kols"] == {
        "status": "ready",
        "result_count": 0,
    }
    assert payload["source_status"]["projects"] == {
        "status": "error",
        "result_count": 0,
        "reason": "query_failed",
    }
    assert payload["source_status"]["events"] == {
        "status": "ready",
        "result_count": 0,
    }


class _FailingOptimizedPgConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        params = tuple(params)
        self.calls.append((sql, params))
        if "FROM pg_extension" in sql:
            return _Rows([{"available": True}])
        if "WITH search_input" in sql:
            raise RuntimeError("simulated unavailable trigram operator")
        if "FROM vkpi_kol_pool" in sql:
            return _Rows(
                [
                    {
                        "id": 1,
                        "platform": "youtube",
                        "handle": "exact",
                        "display_name": "Exact",
                        "avatar_url": "",
                        "followers": 10,
                    }
                ]
            )
        if "FROM vkpi_projects" in sql:
            return _Rows(
                [
                    {
                        "id": 2,
                        "project_uid": "p2",
                        "project_name": "Exact",
                        "stage": "discovery",
                        "stage_status": "active",
                        "platform": "youtube",
                    }
                ]
            )
        if "FROM vkpi_events" in sql:
            return _Rows(
                [
                    {
                        "id": "e3",
                        "title": "Exact",
                        "status": "planning",
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-02",
                    }
                ]
            )
        raise AssertionError(sql)


def test_postgres_trigram_query_failure_retries_legacy_like(monkeypatch):
    conn = _FailingOptimizedPgConn()
    monkeypatch.setattr(search_router, "_PG_TRGM_CAPABILITY", None)
    monkeypatch.setattr(db_connection, "get_conn", lambda: conn)
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)

    payload = search_router.vkpi_global_search(q="Exact", limit=5, staff=_MANAGER)

    assert payload["kols"][0]["id"] == 1
    assert payload["projects"][0]["id"] == 2
    assert payload["events"][0]["id"] == "e3"
    assert all(
        payload["source_status"][key]["status"] == "degraded"
        for key in ("kols", "projects", "events")
    )
    optimized = [sql for sql, _ in conn.calls if "WITH search_input" in sql]
    fallbacks = [sql for sql, _ in conn.calls if "WITH search_input" not in sql]
    assert len(optimized) == 3
    assert all("%% input.exact_q" in sql and "SIMILARITY(" in sql for sql in optimized)
    assert sum("LIKE ? ESCAPE" in sql for sql in fallbacks) == 3


class _NoTrgmFillConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        params = tuple(params)
        self.calls.append((sql, params))
        if "FROM pg_extension" in sql:
            return _Rows([{"available": False}])
        optimized = "WITH search_input" in sql
        if "FROM vkpi_kol_pool" in sql:
            rows = [
                {
                    "id": 1,
                    "platform": "youtube",
                    "handle": "exact",
                    "display_name": "Exact",
                    "avatar_url": "",
                    "followers": 10,
                }
            ]
            if not optimized:
                rows.append({**rows[0], "id": 2, "handle": "infix"})
            return _Rows(rows)
        if "FROM vkpi_projects" in sql:
            if optimized:
                return _Rows([])
            return _Rows(
                [
                    {
                        "id": 3,
                        "project_uid": "p3",
                        "project_name": "Infix",
                        "stage": "discovery",
                        "stage_status": "active",
                        "platform": "youtube",
                    }
                ]
            )
        if "FROM vkpi_events" in sql:
            if optimized:
                return _Rows([])
            return _Rows(
                [
                    {
                        "id": "e4",
                        "title": "Infix",
                        "status": "planning",
                        "start_date": "2026-09-01",
                        "end_date": "2026-09-02",
                    }
                ]
            )
        raise AssertionError(sql)


def test_postgres_without_trigram_uses_like_only_as_second_stage(monkeypatch):
    conn = _NoTrgmFillConn()
    monkeypatch.setattr(search_router, "_PG_TRGM_CAPABILITY", None)
    monkeypatch.setattr(db_connection, "get_conn", lambda: conn)
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)

    payload = search_router.vkpi_global_search(q="Exact", limit=2, staff=_MANAGER)

    assert [row["id"] for row in payload["kols"]] == [1, 2]
    assert [row["id"] for row in payload["projects"]] == [3]
    assert [row["id"] for row in payload["events"]] == ["e4"]
    optimized = [sql for sql, _ in conn.calls if "WITH search_input" in sql]
    assert len(optimized) == 3
    assert all("LIKE input.like_q" not in sql for sql in optimized)
    assert all("%% input.exact_q" not in sql for sql in optimized)
    project_sql = [sql for sql, _ in conn.calls if "FROM vkpi_projects" in sql]
    assert project_sql
    assert all("COALESCE(stage_status, '') <> 'deleted'" in sql for sql in project_sql)


def test_postgres_explain_and_index_expression_contract_is_hermetic():
    document = "LOWER(COALESCE(display_name, '') || ' ' || COALESCE(handle, ''))"
    sql = search_router._postgres_ranked_search_sql(
        table="vkpi_kol_pool",
        select_columns="id, platform, handle, display_name, avatar_url, followers",
        text_expressions=(
            "LOWER(COALESCE(display_name, ''))",
            "LOWER(COALESCE(handle, ''))",
        ),
        document_expression=document,
        stable_order="COALESCE(followers, 0) DESC, id DESC",
        use_trigram=True,
    )
    explain_sql = f"EXPLAIN (COSTS OFF, FORMAT TEXT) {sql}"
    normalized = " ".join(explain_sql.split())
    migration = (REPO_ROOT / "migrations/236_vkpi_search_retrieval_perf.sql").read_text()
    normalized_migration = " ".join(migration.split())

    assert normalized.startswith("EXPLAIN (COSTS OFF, FORMAT TEXT) WITH search_input")
    assert normalized.count("?") == 5
    assert "WEBSEARCH_TO_TSQUERY('simple'::regconfig, ?)" in normalized
    assert "WHEN (LOWER(COALESCE(display_name, '')) = input.exact_q" in normalized
    assert "LIKE input.prefix_q" in normalized
    assert "TO_TSVECTOR('simple'::regconfig" in normalized
    assert "%% input.exact_q" in normalized
    assert "ORDER BY _match_tier ASC" in normalized
    assert "COALESCE(followers, 0) DESC, id DESC" in normalized
    assert document in normalized_migration
    assert "idx_vkpi_kol_pool_search_fts" in migration
    assert "idx_vkpi_kol_pool_search_trgm" in migration


def test_migration_keeps_pg_trgm_optional_and_down_does_not_drop_extension():
    forward = (REPO_ROOT / "migrations/236_vkpi_search_retrieval_perf.sql").read_text()
    down = (REPO_ROOT / "migrations/236_vkpi_search_retrieval_perf_down.sql").read_text()

    assert "EXECUTE 'CREATE EXTENSION IF NOT EXISTS pg_trgm'" in forward
    assert forward.count("EXCEPTION WHEN OTHERS") >= 2
    assert "IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm')" in forward
    assert forward.count("text_pattern_ops") == 4
    assert forward.count("TO_TSVECTOR(") == 3
    for index_name in (
        "idx_vkpi_kol_pool_search_name_prefix",
        "idx_vkpi_kol_pool_search_handle_prefix",
        "idx_vkpi_kol_pool_search_fts",
        "idx_vkpi_kol_pool_search_trgm",
        "idx_vkpi_projects_search_name_prefix",
        "idx_vkpi_projects_search_fts",
        "idx_vkpi_projects_search_trgm",
        "idx_vkpi_events_search_title_prefix",
        "idx_vkpi_events_search_fts",
        "idx_vkpi_events_search_trgm",
    ):
        assert index_name in forward
        assert f"DROP INDEX IF EXISTS {index_name}" in down
    assert "DROP EXTENSION" not in down


def test_query_and_limit_caps_apply_to_direct_calls(monkeypatch):
    conn = _sqlite_search_conn()
    _use_sqlite(monkeypatch, conn)

    with pytest.raises(HTTPException, match="q 最长"):
        search_router.vkpi_global_search(
            q="x" * (search_router.GLOBAL_SEARCH_QUERY_MAX_LENGTH + 1),
            limit=5,
            staff=_MANAGER,
        )

    payload = search_router.vkpi_global_search(q="100%", limit=999, staff=_MANAGER)
    assert all(
        len(payload[key]) <= search_router.GLOBAL_SEARCH_LIMIT_MAX
        for key in ("kols", "projects", "events")
    )
    assert "natural_search" not in inspect.getsource(search_router.vkpi_global_search)


def test_cross_dataset_search_denies_non_manager_before_domain_call(monkeypatch):
    monkeypatch.setattr(
        search_router.natural_search,
        "search",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("denied request must not reach natural search")
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        search_router.vkpi_search(
            q="camera",
            limit=20,
            staff={"staff_id": 7, "role": "employee", "is_owner": 0},
        )

    assert exc_info.value.status_code == 403


def test_cross_dataset_search_passes_manager_staff_to_domain(monkeypatch):
    captured: dict = {}

    def fake_search(query: str, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return {"query": query, "items": []}

    monkeypatch.setattr(search_router.natural_search, "search", fake_search)

    result = search_router.vkpi_search(q="camera", limit=7, staff=_MANAGER)

    assert result["items"] == []
    assert captured == {"query": "camera", "limit": 7, "staff": _MANAGER}


def test_cross_dataset_query_and_token_expansion_are_bounded():
    with pytest.raises(HTTPException) as exc_info:
        search_router.vkpi_search(
            q="x" * (search_router.natural_search.MAX_QUERY_LENGTH + 1),
            limit=20,
            staff=_MANAGER,
        )
    assert exc_info.value.status_code == 400

    varied_cjk = "".join(chr(0x4E00 + index) for index in range(60))
    tokens = search_router.natural_search._tokens(varied_cjk)
    assert len(tokens) == search_router.natural_search.MAX_SEARCH_TOKENS


def test_domain_rejects_explicit_unscoped_staff_without_touching_db(monkeypatch):
    monkeypatch.setattr(
        search_router.natural_search,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("permission check must run first")),
    )

    with pytest.raises(PermissionError):
        search_router.natural_search.search(
            "camera",
            staff={"staff_id": 7, "role": "employee", "is_owner": 0},
        )
