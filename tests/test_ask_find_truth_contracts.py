from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.analytics import canned_queries  # noqa: E402


def _official_metrics_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_employee_channels (
            id INTEGER PRIMARY KEY,
            platform TEXT,
            account_handle TEXT
        );
        CREATE TABLE vkpi_channel_metrics_filled (
            channel_id INTEGER,
            snapshot_date TEXT,
            followers INTEGER,
            total_views INTEGER,
            engagement_rate REAL,
            source TEXT,
            confidence TEXT,
            reason TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO vkpi_employee_channels VALUES (?, ?, ?)",
        [
            (1, "youtube", "@viltrox"),
            (2, "instagram", "@viltrox_global"),
        ],
    )
    conn.executemany(
        "INSERT INTO vkpi_channel_metrics_filled VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "2026-08-03", 900, 9000, 0.03, "synced", "高", "source row"),
            (1, "2026-08-04", 1000, 10000, 0.04, "synced", "高", "source row"),
            (
                2,
                "2026-08-04",
                500,
                6000,
                0.02,
                "estimated_linear",
                "中",
                "interpolated between synced snapshots",
            ),
        ],
    )
    conn.commit()
    return conn


def test_official_performance_exposes_observed_vs_estimated_truth() -> None:
    result = canned_queries.run(
        _official_metrics_conn(),
        "official_performance",
    )

    assert result["columns"][:7] == [
        "channel_id",
        "platform",
        "handle",
        "snapshot_date",
        "followers",
        "total_views",
        "engagement_rate",
    ]
    assert result["columns"][7:] == [
        "source",
        "confidence",
        "truth_status",
        "source_reason",
    ]
    assert result["rows"] == [
        {
            "channel_id": 1,
            "platform": "youtube",
            "handle": "@viltrox",
            "snapshot_date": "2026-08-04",
            "followers": 1000,
            "total_views": 10000,
            "engagement_rate": 0.04,
            "source": "synced",
            "confidence": "高",
            "truth_status": "observed",
            "source_reason": "source row",
        },
        {
            "channel_id": 2,
            "platform": "instagram",
            "handle": "@viltrox_global",
            "snapshot_date": "2026-08-04",
            "followers": 500,
            "total_views": 6000,
            "engagement_rate": 0.02,
            "source": "estimated_linear",
            "confidence": "中",
            "truth_status": "estimated",
            "source_reason": "interpolated between synced snapshots",
        },
    ]
    assert "实测 1、估算 1" in result["summary"]


def test_official_performance_question_contract_keeps_legacy_columns_first() -> None:
    question = next(
        item
        for item in canned_queries.list_questions()
        if item["key"] == "official_performance"
    )

    assert question["columns"][:7] == [
        "channel_id",
        "platform",
        "handle",
        "snapshot_date",
        "followers",
        "total_views",
        "engagement_rate",
    ]
    assert "confidence" in question["columns"]
    assert "truth_status" in question["columns"]
