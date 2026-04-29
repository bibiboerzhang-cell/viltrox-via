from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import Response
from starlette.requests import Request


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import auth as auth_mod  # noqa: E402
from app.api.routers import creator as creator_mod  # noqa: E402
from app.schemas.auth import RegisterRequest  # noqa: E402


SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    creator_code TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved',
    role TEXT NOT NULL DEFAULT 'creator',
    email_verified INTEGER DEFAULT 1,
    note TEXT DEFAULT '',
    points_balance INTEGER DEFAULT 0,
    points_pending INTEGER DEFAULT 0,
    points_total INTEGER DEFAULT 0,
    avatar_url TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    signature TEXT DEFAULT '',
    tier_status TEXT DEFAULT 'pending',
    trust_score REAL DEFAULT 30,
    trust_updated_at TEXT DEFAULT '',
    last_login TEXT DEFAULT ''
);

CREATE TABLE reward_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    points_cost INTEGER NOT NULL,
    stock INTEGER,
    status TEXT NOT NULL DEFAULT 'published'
);

CREATE TABLE redemptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    reward_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    item_category TEXT NOT NULL,
    points_cost INTEGER NOT NULL,
    address_id INTEGER,
    address_snapshot TEXT,
    status TEXT NOT NULL
);

CREATE TABLE points_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    balance_after INTEGER NOT NULL
);
"""


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


class CreatorBusinessFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "creator_flow.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.patches = [
            patch.object(auth_mod, "get_conn", return_value=self.conn),
            patch.object(auth_mod, "IS_PRODUCTION", False),
            patch.object(creator_mod, "get_conn", return_value=self.conn),
            patch.object(creator_mod, "is_postgres_runtime", return_value=False),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.conn.close()
        self.tmp.cleanup()

    def test_register_to_redeem_happy_path(self) -> None:
        register_result = auth_mod.auth_register(
            _request("/api/auth/register"),
            RegisterRequest(email="creator@example.com", password="strong-password", name="Creator One"),
            Response(),
        )
        self.assertEqual(register_result["status"], "success")
        user_id = int(register_result["user"]["id"])

        self.conn.execute(
            "UPDATE users SET points_balance=120, points_total=120 WHERE id=?",
            (user_id,),
        )
        self.conn.execute(
            """
            INSERT INTO reward_catalog (title, category, points_cost, stock, status)
            VALUES ('Lens cloth', 'gear', 60, 3, 'published')
            """
        )
        reward_id = int(self.conn.execute("SELECT id FROM reward_catalog").fetchone()["id"])
        self.conn.commit()

        redeem_result = creator_mod._redeem_reward_sync(user_id, reward_id, None)

        self.assertEqual(redeem_result["status"], "success")
        self.assertEqual(
            self.conn.execute("SELECT points_balance FROM users WHERE id=?", (user_id,)).fetchone()["points_balance"],
            60,
        )
        self.assertEqual(
            self.conn.execute("SELECT stock FROM reward_catalog WHERE id=?", (reward_id,)).fetchone()["stock"],
            2,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM redemptions WHERE user_id=?", (user_id,)).fetchone()["n"],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT delta FROM points_log WHERE user_id=?", (user_id,)).fetchone()["delta"],
            -60,
        )


if __name__ == "__main__":
    unittest.main()
