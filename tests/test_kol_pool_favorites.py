"""C2 收藏域单测(战役第一段备稿;migration 107 表结构以 sqlite 等价建表模拟)。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture()
def favorites_conn(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, platform TEXT, handle TEXT, display_name TEXT, followers INTEGER, viltrox_fit_score REAL, profile_url TEXT, avatar_url TEXT)")
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
