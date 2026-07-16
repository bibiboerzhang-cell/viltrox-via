from __future__ import annotations

import socket
from datetime import datetime, timezone

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
from app.domains.comments import collector as comments_collector
from app.domains.comments import intelligence as comment_intelligence
from app.platform import llm_gateway


MARKER = "vkpi-comment-intelligence-readonly-unit"


@pytest.fixture(autouse=True)
def _private_comment_intelligence_db(tmp_path, monkeypatch):
    """Keep the read-only contract on a fresh DB and fail external calls loudly."""
    db_connection.close_db_runtime_sync()
    db_path = (tmp_path / "comment-intelligence.db").resolve()
    assert db_path != db_connection._PRODUCTION_SQLITE_PATH
    monkeypatch.setattr(db_connection, "DB_PATH", db_path)
    monkeypatch.setattr(db_connection, "DB_RUNTIME_BACKEND", "sqlite")
    monkeypatch.setattr(db_connection, "DB_RUNTIME_URL", "")

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("read-only comment intelligence must not call providers")

    def fail_network(*_args, **_kwargs):
        raise AssertionError("read-only comment intelligence must not call the network")

    monkeypatch.setattr(llm_gateway, "invoke", fail_provider)
    monkeypatch.setattr(comments_collector, "get_crawler", fail_provider)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    try:
        yield
    finally:
        db_connection.close_db_runtime_sync()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _cleanup() -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM vkpi_comments WHERE external_comment_id LIKE ?", (f"{MARKER}-%",))
        conn.commit()
    except Exception:
        pass


def test_overview_exposes_readonly_rule_v0_comment_samples_without_provider_calls() -> None:
    comments_collector.ensure_vkpi_comments_schema()
    comment_intelligence.ensure_vkpi_comment_intelligence_schema()
    _cleanup()
    conn = get_conn()
    now = _now()
    rows = [
        ("positive", "I love this Viltrox lens. Where can I buy it?"),
        ("negative", "Autofocus issue after firmware update, is there a fix?"),
    ]
    try:
        for index, (_label, text) in enumerate(rows, start=1):
            conn.execute(
                """
                INSERT INTO vkpi_comments (
                  account_id, post_id, post_table, external_post_id, platform,
                  external_comment_id, comment_text, author_handle, likes_count,
                  reply_count, created_at, fetched_at, raw_data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    0,
                    9000 + index,
                    "industry_posts",
                    f"{MARKER}-post",
                    "youtube",
                    f"{MARKER}-{index}",
                    text,
                    "unit-viewer",
                    index,
                    0,
                    now,
                    now,
                    "{}",
                ),
            )
        conn.commit()

        rule = comment_intelligence._rule_v0_comment_summary(cutoff=now, sample_limit=1)
        overview = comment_intelligence.overview(days=1, recent_limit=1)

        assert overview["provider_calls"] is False
        assert overview["llm_calls"] is False
        assert overview["write_db"] is False
        assert "rule_v0" in overview
        assert rule["contract"] == {
            "declared": None,
            "cached": 2,
            "cap": 1,
            "status": "sampled_cached",
        }
        assert rule["provider_calls"] is False
        assert rule["llm_calls"] is False
        assert rule["write_db"] is False
        assert rule["counts"]["sampled_comments"] == 1
        assert rule["counts"]["questions"] == 1
        assert rule["samples"][0]["source"] == "vkpi_comments"
        assert rule["samples"][0]["rebuttal_supported"] is True
    finally:
        _cleanup()
