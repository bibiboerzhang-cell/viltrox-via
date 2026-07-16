from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
from app.domains.search import natural_search
import app.platform.db.schema_product_industry as product_industry_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


MARKER = "vkpi-natural-search-enrichment-unit"


_OPTIONAL_SEARCH_SCHEMA = """
CREATE TABLE vkpi_memory_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_uid TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    display_name TEXT DEFAULT '',
    source_table TEXT DEFAULT '',
    source_id TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    confidence_score REAL DEFAULT 1.0,
    identity_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, identity_key)
);

CREATE TABLE vkpi_memory_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_uid TEXT NOT NULL UNIQUE,
    entity_id INTEGER NOT NULL,
    fact_type TEXT NOT NULL,
    fact_key TEXT NOT NULL DEFAULT '',
    fact_value_text TEXT DEFAULT '',
    confidence_score REAL DEFAULT 1.0,
    source_ref TEXT NOT NULL DEFAULT '',
    source_table TEXT DEFAULT '',
    source_id TEXT DEFAULT '',
    fact_json TEXT NOT NULL DEFAULT '{}',
    source_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    valid_from TEXT,
    valid_to TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, fact_type, fact_key, source_ref)
);

CREATE TABLE vkpi_competitor_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_uid TEXT NOT NULL UNIQUE,
    brand TEXT DEFAULT '',
    normalized_brand TEXT DEFAULT '',
    signal_type TEXT DEFAULT '',
    severity TEXT DEFAULT '',
    score REAL,
    product_hints_json TEXT NOT NULL DEFAULT '[]',
    source_table TEXT DEFAULT '',
    source_id TEXT DEFAULT '',
    source_sheet TEXT DEFAULT '',
    source_row INTEGER,
    source_url TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    review_status TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE vkpi_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL DEFAULT 'info',
    status TEXT NOT NULL DEFAULT 'open',
    target_type TEXT DEFAULT '',
    target_id TEXT DEFAULT '',
    title TEXT DEFAULT '',
    body TEXT DEFAULT '',
    rule_key TEXT DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture(scope="module", autouse=True)
def _natural_search_test_db(tmp_path_factory: pytest.TempPathFactory):
    """Run the cross-table search against an empty, private source catalog."""
    db_path = (tmp_path_factory.mktemp("natural-search") / "natural-search.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db

    old_db_path = db_connection.DB_PATH
    old_runtime_backend = db_connection.DB_RUNTIME_BACKEND
    old_runtime_url = db_connection.DB_RUNTIME_URL
    old_schema_ready = product_industry_schema._SCHEMA_READY

    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    product_industry_schema._SCHEMA_READY = False

    try:
        ensure_vkpi_product_industry_schema()
        conn = get_conn()
        actual_path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
        assert actual_path == db_path
        conn.executescript(_OPTIONAL_SEARCH_SCHEMA)
        conn.commit()
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_runtime_backend
        db_connection.DB_RUNTIME_URL = old_runtime_url
        product_industry_schema._SCHEMA_READY = old_schema_ready


def _cleanup() -> None:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_kol_pool WHERE source_ref=?", (MARKER,))
    conn.commit()


def test_natural_search_kol_pool_includes_avatar_and_recent_posts_without_raw_payload():
    ensure_vkpi_product_industry_schema()
    _cleanup()
    conn = get_conn()
    now = "2026-05-23T10:00:00Z"
    raw = {
        "profile": {
            "snippet": {
                "thumbnails": {
                    "high": {"url": "https://yt3.ggpht.com/natural-search-avatar=s800-c-k-c0x00ffffff-no-rj"}
                }
            }
        },
        "videos": [
            {
                "kind": "youtube#video",
                "id": "natural-search-video",
                "snippet": {
                    "title": "Natural Search Viltrox field test",
                    "publishedAt": "2026-05-22T10:00:00Z",
                },
                "statistics": {
                    "viewCount": "2234",
                    "likeCount": "65",
                    "commentCount": "8",
                },
                "large_unused_field": "x" * 5000,
            }
        ],
    }
    try:
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
              (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
               followers, following, posts_count, avg_views, avg_likes, avg_comments,
               engagement_rate, source_type, source_ref, raw_platform_data, created_by_staff_id,
               last_seen_at, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"{MARKER}-uid",
                "youtube",
                "naturalsearchavatar",
                "https://youtube.com/@naturalsearchavatar",
                "Natural Search Avatar",
                "",
                "Viltrox sample creator for natural search",
                "",
                1200,
                None,
                1,
                2234,
                65,
                8,
                0.061,
                "legacy_excel_p2d",
                MARKER,
                json.dumps(raw),
                None,
                now,
                now,
                now,
            ),
        )
        conn.commit()

        payload = natural_search.search("naturalsearchavatar", limit=5)
        result = next(item for item in payload["items"] if item.get("source_table") == "vkpi_kol_pool")

        assert payload["provider_calls"] is False
        assert payload["write_db"] is False
        assert result["avatar_url"] == "https://yt3.ggpht.com/natural-search-avatar=s800-c-k-c0x00ffffff-no-rj"
        assert result["recent_posts"][0]["post_url"] == "https://www.youtube.com/watch?v=natural-search-video"
        assert result["recent_posts"][0]["views"] == 2234
        assert "raw_platform_data" not in result["evidence"]
        assert "large_unused_field" not in json.dumps(result["evidence"], default=str)
    finally:
        _cleanup()
