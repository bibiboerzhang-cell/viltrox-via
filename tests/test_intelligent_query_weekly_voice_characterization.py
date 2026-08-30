"""Frozen complete-return characterization for the weekly-voice CC split."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.domains.intelligent_query import contracts
from app.domains.intelligent_query.contracts import NormalizedRequest, QueryScope, QueryWindow
from app.domains.intelligent_query import weekly_voice


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
STAFF = {"id": 7, "staff_id": 7, "role": "manager", "organization_id": 1}
LEGACY_OUTPUT_SHA256 = {
    "en-US:available": "30f8e2d273706c36fb89914abb3f84c71edbc59110554ab8f13e8b785f541e1a",
    "zh-CN:available": "c5e3f1778fd90b24d43bcb024df4d4aaeb4b13994ecb2e7dff554e368791612e",
    "en-US:unavailable": "97d0d947a1a0203ed1eb17598df93109b87f136346646b4538e6a9bb66ac3925",
}


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return NOW.astimezone(tz) if tz is not None else NOW.replace(tzinfo=None)


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _request(locale: str) -> NormalizedRequest:
    return NormalizedRequest(
        query="weekly market feedback for Viltrox",
        locale=locale,
        thread_id="weekly-characterization",
        scope=QueryScope(mode="auto", requested_staff_id=None),
        window=QueryWindow(start=NOW - timedelta(days=7), end=NOW, preset="7d"),
        filters={"limit": 20},
        mode="deterministic",
        client_request_id="weekly-characterization-client",
        request_id="iq_weekly_characterization",
    )


def _available_docs() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "text": "Viltrox is excellent and sharp",
            "lower": "viltrox is excellent and sharp",
            "author": "",
            "platform": "youtube",
            "at": "2026-08-04T08:00:00Z",
            "likes": 10,
            "source": "vkpi_comments",
            "intent_tag": "",
        },
        {
            "id": 2,
            "text": "Viltrox autofocus is bad and hunting",
            "lower": "viltrox autofocus is bad and hunting",
            "author": "",
            "platform": "youtube",
            "at": "2026-08-03T08:00:00Z",
            "likes": 8,
            "source": "vkpi_comments",
            "intent_tag": "",
        },
        {
            "id": 3,
            "text": "Please make a Viltrox 40mm for Nikon Z",
            "lower": "please make a viltrox 40mm for nikon z",
            "author": "",
            "platform": "instagram",
            "at": "2026-08-02T08:00:00Z",
            "likes": 3,
            "source": "vkpi_comments",
            "intent_tag": "",
        },
        {
            "id": 4,
            "text": "Viltrox is not sharp",
            "lower": "viltrox is not sharp",
            "author": "",
            "platform": "youtube",
            "at": "2026-08-01T08:00:00Z",
            "likes": 2,
            "source": "vkpi_comments",
            "intent_tag": "",
        },
    ]


def _install_available_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = _available_docs()
    monkeypatch.setattr(weekly_voice, "_viltrox_market_terms", lambda _conn: ["viltrox", "唯卓仕"])
    monkeypatch.setattr(
        weekly_voice,
        "_voice_docs",
        lambda *_args: (
            docs,
            {
                "comments": {
                    "status": "ready",
                    "count": 4,
                    "matched_count": 4,
                    "candidate_count": 6,
                    "truncated": True,
                },
                "intent_queue": {
                    "status": "unavailable",
                    "reason": "ingestion_time_only_not_market_event_time",
                    "count": 0,
                },
            },
            "2026-08-04T08:00:00Z",
            2,
            True,
        ),
    )
    monkeypatch.setattr(
        weekly_voice,
        "_viltrox_video_mentions",
        lambda *_args: (
            1,
            [
                {
                    "id": 91,
                    "kol_pool_id": 11,
                    "content_url": "https://example.test/v/91",
                    "title": "Viltrox 40mm weekly review",
                    "observed_at": "2026-08-03T10:00:00Z",
                }
            ],
            "2026-08-03T10:00:00Z",
            {"status": "ready", "count": 1, "time_semantics": "content_publication_event"},
        ),
    )


def _install_unavailable_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weekly_voice, "_viltrox_market_terms", lambda _conn: ["viltrox"])
    monkeypatch.setattr(
        weekly_voice,
        "_voice_docs",
        lambda *_args: (
            [],
            {
                "comments": {"status": "absent", "count": 0},
                "intent_queue": {"status": "absent", "count": 0},
            },
            None,
            0,
            False,
        ),
    )
    monkeypatch.setattr(
        weekly_voice,
        "_viltrox_video_mentions",
        lambda *_args: (
            0,
            [],
            None,
            {"status": "absent", "count": 0, "reason": "video_source_absent"},
        ),
    )


@pytest.mark.parametrize(
    ("locale", "source_state"),
    [("en-US", "available"), ("zh-CN", "available"), ("en-US", "unavailable")],
)
def test_complete_weekly_voice_return_matches_pre_split_characterization(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
    source_state: str,
) -> None:
    monkeypatch.setattr(contracts, "datetime", _FrozenDatetime)
    if source_state == "available":
        _install_available_sources(monkeypatch)
    else:
        _install_unavailable_sources(monkeypatch)

    result = weekly_voice.market_weekly_voice(
        object(),
        _request(locale),
        STAFF,
        now=NOW,
    )

    assert _digest(result) == LEGACY_OUTPUT_SHA256[f"{locale}:{source_state}"]
    assert result["trace"]["scope"]["applied_mode"] == "shared_global"
    assert result["freshness"]["window_start"] == "2026-07-28T12:00:00Z"
    assert result["freshness"]["window_end"] == "2026-08-04T12:00:00Z"
    if source_state == "unavailable":
        assert result["status"] == "error"
        assert result["facts"] == []
        assert result["coverage"]["status"] == "unknown"
        assert [item["field"] for item in result["missing_fields"]] == [
            "weekly_market_event_sources"
        ]
    else:
        assert result["status"] == "partial"
        assert result["coverage"]["matched_entities"] == 5
        assert [item["field"] for item in result["missing_fields"]] == [
            "external_market_sources",
            "sentiment_gold_validation",
            "weekly_voice_retrieval_cap",
        ]
