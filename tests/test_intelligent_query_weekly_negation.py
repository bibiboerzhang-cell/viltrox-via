from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from app.domains.intelligent_query import execute_query
from app.domains.intelligent_query.weekly_voice import _has_affirmed_positive_cue


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
MANAGER = {"id": 7, "staff_id": 7, "role": "manager", "organization_id": 1}


@pytest.mark.parametrize(
    "comment",
    [
        "I do not recommend Viltrox",
        "Viltrox is not sharp",
        "Viltrox isn't really great",
        "Viltrox 不推荐",
        "Viltrox 不是很好",
        "Viltrox 没那么锐利",
        "Viltrox 性价比不高",
    ],
)
def test_negated_positive_cues_are_not_positive(comment: str) -> None:
    assert _has_affirmed_positive_cue(comment) is False


@pytest.mark.parametrize(
    "comment",
    [
        "I recommend Viltrox",
        "Viltrox is not only sharp but amazing",
        "I can't recommend Viltrox highly enough",
        "Viltrox is not bad and actually sharp",
        "Viltrox has no issues and is excellent",
        "Viltrox 不但很锐利，而且值得推荐",
        "这支 Viltrox 不得不推荐",
        "Viltrox 特别锐利",
        "Viltrox 无比锐利",
        "Viltrox 不错，很锐利",
    ],
)
def test_affirmative_negation_idioms_and_real_positive_cues_survive(comment: str) -> None:
    assert _has_affirmed_positive_cue(comment) is True


def test_a_real_mixed_comment_keeps_its_affirmed_positive_cue() -> None:
    assert _has_affirmed_positive_cue(
        "Viltrox is not sharp, but I love the color rendering"
    ) is True


def _weekly_voice_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vkpi_comments (
          id INTEGER PRIMARY KEY,
          platform TEXT,
          comment_text TEXT,
          likes_count INTEGER,
          created_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO vkpi_comments VALUES (?, ?, ?, ?, ?)",
        [
            (
                1,
                "youtube",
                "Viltrox autofocus is bad and I do not recommend it",
                9,
                "2026-08-04T08:00:00Z",
            ),
            (2, "youtube", "Viltrox is not sharp", 7, "2026-08-03T08:00:00Z"),
            (3, "weibo", "Viltrox 不推荐，对焦不好", 5, "2026-08-02T08:00:00Z"),
            (4, "bilibili", "Viltrox 不是很好，画质不锐利", 4, "2026-08-01T08:00:00Z"),
            (5, "youtube", "Viltrox is excellent", 3, "2026-07-31T08:00:00Z"),
        ],
    )
    return conn


def test_weekly_contract_counts_only_affirmed_positive_docs_and_keeps_caveats() -> None:
    conn = _weekly_voice_connection()
    try:
        result = execute_query(
            {
                "query": "weekly market feedback for Viltrox",
                "locale": "en-US",
                "time_range": "7d",
            },
            staff=MANAGER,
            conn=conn,
            now=NOW,
        )
    finally:
        conn.close()

    facts = {item["key"]: item for item in result["facts"]}
    positive = facts["market.positive_rule_signals"]
    assert positive["value"] == 1
    assert positive["confidence"] == "low"
    assert "local negation windows" in positive["basis"]
    assert any(
        item["field"] == "sentiment_gold_validation"
        for item in result["missing_fields"]
    )

    positive_evidence = [
        item for item in result["evidence"] if item["kind"] == "positive_rule_signal"
    ]
    assert [item["snippet"] for item in positive_evidence] == ["Viltrox is excellent"]
