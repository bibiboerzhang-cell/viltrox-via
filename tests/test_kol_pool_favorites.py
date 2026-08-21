"""C2 收藏域单测(战役第一段备稿;migration 107 表结构以 sqlite 等价建表模拟)。"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture()
def favorites_conn(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, platform TEXT, handle TEXT, display_name TEXT, followers INTEGER, viltrox_fit_score REAL, profile_url TEXT, avatar_url TEXT, duplicate_of_id INTEGER DEFAULT NULL)")
    conn.execute(
        """
        CREATE TABLE vkpi_kol_pool_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL REFERENCES vkpi_kol_pool(id),
            staff_id INTEGER NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (kol_pool_id, staff_id)
        )
        """
    )
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, handle, display_name, followers) VALUES (1,'youtube','@a','A',1000)")
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, handle, display_name, followers) VALUES (2,'instagram','@b','B',2000)")
    # projects_json 子查询依赖(PG json_agg/json_build_object 的 sqlite 等价物 + 两张表)
    conn.execute("CREATE TABLE vkpi_projects (id INTEGER PRIMARY KEY, project_name TEXT, restricted INTEGER DEFAULT 0, assigned_staff_id INTEGER, created_by_staff_id INTEGER, is_public INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE vkpi_project_kol_assignments (id INTEGER PRIMARY KEY, project_id INTEGER, kol_pool_id INTEGER, stage TEXT, stage_status TEXT)")
    conn.execute("CREATE TABLE vkpi_project_members (id INTEGER PRIMARY KEY, project_id INTEGER, staff_id INTEGER, role TEXT)")
    import json as _json

    class _JsonAgg:
        def __init__(self):
            self.items = []
        def step(self, value):
            self.items.append(_json.loads(value) if isinstance(value, str) else value)
        def finalize(self):
            return _json.dumps(self.items) if self.items else None

    conn.create_function("json_build_object", -1, lambda *args: _json.dumps({str(args[i]): args[i + 1] for i in range(0, len(args), 2)}))
    conn.create_aggregate("json_agg", 1, _JsonAgg)
    conn.commit()

    from app.domains.kol import pool_favorites

    monkeypatch.setattr(pool_favorites, "get_conn", lambda: conn)
    return conn


def test_favorite_unfavorite_idempotent(favorites_conn):
    from app.domains.kol import pool_favorites

    staff = {"id": 84, "user_id": 108}
    first = pool_favorites.add_favorite(1, staff=staff)
    assert first["status"] == "favorited"
    again = pool_favorites.add_favorite(1, staff=staff)
    assert again["status"] == "already_favorited"

    removed = pool_favorites.remove_favorite(1, staff=staff)
    assert removed["status"] == "unfavorited"
    removed_again = pool_favorites.remove_favorite(1, staff=staff)
    assert removed_again["status"] == "not_favorited"


def test_concurrent_favorite_is_unique_and_returns_created_plus_already(monkeypatch, tmp_path):
    """Two real connections crossing at INSERT must not leak a unique conflict as 500."""
    db_path = tmp_path / "favorite-race.sqlite3"
    setup = sqlite3.connect(db_path)
    setup.execute("CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY)")
    setup.execute(
        """
        CREATE TABLE vkpi_kol_pool_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL REFERENCES vkpi_kol_pool(id),
            staff_id INTEGER NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (kol_pool_id, staff_id)
        )
        """
    )
    setup.execute("INSERT INTO vkpi_kol_pool (id) VALUES (1)")
    setup.commit()
    setup.close()

    from app.domains.kol import pool_favorites

    insert_barrier = threading.Barrier(2)
    local = threading.local()
    opened: list[sqlite3.Connection] = []
    opened_lock = threading.Lock()

    class BarrierConnection:
        def __init__(self, inner: sqlite3.Connection):
            self.inner = inner

        def execute(self, sql, params=()):
            if "INSERT INTO vkpi_kol_pool_favorites" in " ".join(str(sql).split()):
                insert_barrier.wait(timeout=5)
            return self.inner.execute(sql, params)

        def commit(self):
            self.inner.commit()

    def get_thread_conn():
        if not hasattr(local, "conn"):
            raw = sqlite3.connect(db_path, timeout=10, isolation_level=None, check_same_thread=False)
            raw.row_factory = sqlite3.Row
            local.conn = BarrierConnection(raw)
            with opened_lock:
                opened.append(raw)
        return local.conn

    monkeypatch.setattr(pool_favorites, "get_conn", get_thread_conn)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: pool_favorites.add_favorite(1, staff={"id": 84}), range(2)))

    assert sorted(result["status"] for result in results) == ["already_favorited", "favorited"]
    assert len({result["favorite_id"] for result in results}) == 1
    check = sqlite3.connect(db_path)
    assert check.execute(
        "SELECT COUNT(*) FROM vkpi_kol_pool_favorites WHERE kol_pool_id=1 AND staff_id=84"
    ).fetchone()[0] == 1
    check.close()
    for connection in opened:
        connection.close()


def test_list_is_staff_isolated(favorites_conn):
    from app.domains.kol import pool_favorites

    pool_favorites.add_favorite(1, staff={"id": 84})
    pool_favorites.add_favorite(2, staff={"id": 7676})

    mine = pool_favorites.list_favorites(staff={"id": 84})
    assert mine["total"] == 1
    assert mine["items"][0]["kol_pool_id"] == 1

    theirs = pool_favorites.list_favorites(staff={"id": 7676})
    assert theirs["total"] == 1
    assert theirs["items"][0]["kol_pool_id"] == 2


def test_requires_staff_identity_and_existing_kol(favorites_conn):
    from app.domains.kol import pool_favorites

    with pytest.raises(PermissionError):
        pool_favorites.add_favorite(1, staff=None)
    with pytest.raises(LookupError):
        pool_favorites.add_favorite(999, staff={"id": 84})


def test_list_hides_another_staff_private_project_but_keeps_own_and_public(favorites_conn):
    from app.domains.kol import pool_favorites

    favorites_conn.executemany(
        "INSERT INTO vkpi_projects (id, project_name, assigned_staff_id, created_by_staff_id, is_public) VALUES (?, ?, ?, ?, ?)",
        [
            (11, "Mine", 84, 84, 0),
            (12, "Another staff private", 7676, 7676, 0),
            (13, "Company public", 7676, 7676, 1),
        ],
    )
    favorites_conn.executemany(
        "INSERT INTO vkpi_project_kol_assignments (id, project_id, kol_pool_id, stage, stage_status) VALUES (?, ?, 1, 'discovered', 'active')",
        [(101, 11), (102, 12), (103, 13)],
    )
    favorites_conn.commit()
    pool_favorites.add_favorite(1, staff={"id": 84, "role": "staff"})

    item = pool_favorites.list_favorites(staff={"id": 84, "role": "staff"})["items"][0]
    projects = json.loads(item["projects_json"])
    assert {project["project_id"] for project in projects} == {11, 13}
    assert "Another staff private" not in item["projects_json"]
