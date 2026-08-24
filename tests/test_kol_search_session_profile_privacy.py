from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.kol import search_sessions
from app.domains.kol.search_sessions_items import update_item_profile_execution
from app.domains.kol.search_sessions_serde import (
    _compact_audience_preview,
    _compact_contact_preview,
    _compact_public_profile_data,
    _public_session_item_source_url,
    _row_to_session,
    _row_to_item,
    _sanitize_session_payload,
)


_CANARY_EMAIL = "session-pii-canary@example.test"
_CANARY_PHONE = "+1 202 555 0199"
_CANARY_WECHAT = "WeChat: session-pii-canary"
_CANARY_PROVIDER = "provider-payload-canary"
_CANARY_MASKED = "s***@example.test"


def test_url_video_source_keeps_numeric_platform_id_but_rejects_contact_route() -> None:
    assert _public_session_item_source_url(
        "https://vimeo.com/12345678?utm_source=history",
        item_type="url_video",
    ) == "https://vimeo.com/12345678"
    assert _public_session_item_source_url(
        "https://wa.me/12345678",
        item_type="url_video",
    ) == ""


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.rows[0]) if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]


class _ProfileUpdateConn:
    def __init__(self) -> None:
        self.item = {
            "id": 41,
            "session_id": 7,
            "dedupe_key": "youtube:privacy-canary",
            "item_type": "recall_candidate",
            "status": "running",
            "stage": "profile",
            "rank": 1,
            "score": 82.0,
            "kol_pool_id": None,
            "evidence_id": None,
            "job_id": None,
            "source_url": "https://www.youtube.com/@privacy-canary",
            "payload_json": json.dumps(
                {
                    "display_name": "Privacy Canary",
                    "profile_text": f"Contact {_CANARY_EMAIL}",
                    "provider_payload": {"secret": _CANARY_PROVIDER},
                    "contact_preview": {"status": "pending", "email": _CANARY_MASKED},
                }
            ),
        }
        self.session = {
            "status": "running",
            "result_summary_json": json.dumps({"phase": "profile", "progress": {"total": 1}}),
        }
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("select * from vkpi_kol_search_session_items"):
            assert params == (7, 41)
            return _Cursor([self.item])
        if normalized.startswith("update vkpi_kol_search_session_items"):
            status, kol_pool_id, payload_json, session_id, item_id = params
            assert (session_id, item_id) == (7, 41)
            self.item.update(
                {
                    "status": status,
                    "stage": "profile",
                    "kol_pool_id": kol_pool_id or self.item.get("kol_pool_id"),
                    "payload_json": payload_json,
                }
            )
            return _Cursor([self.item])
        if normalized.startswith("select status, result_summary_json from vkpi_kol_search_sessions"):
            assert params == (7,)
            return _Cursor([self.session])
        if normalized.startswith("select status, payload_json from vkpi_kol_search_session_items"):
            assert params == (7,)
            return _Cursor(
                [{"status": self.item["status"], "payload_json": self.item["payload_json"]}]
            )
        raise AssertionError(f"unexpected SQL: {normalized}")

    def commit(self) -> None:
        self.commits += 1


@pytest.mark.parametrize(
    "unsafe_bio",
    [
        f"Business inquiries: {_CANARY_EMAIL}",
        f"Call me {_CANARY_PHONE}",
        _CANARY_WECHAT,
        "LINE ID: privacy-canary",
        "Signal: privacy-canary",
        "Discord https://discord.gg/privacy-canary",
        "Telegram: privacy-canary",
        "Messenger: privacy-canary",
        "Instagram DM: @privacy-canary",
        "TikTok DM @privacy-canary",
        "X DM: @privacy-canary",
        "Phone: 12345678",
        "Contact https://wa.me/12025550199",
    ],
)
def test_public_profile_dto_drops_contact_bearing_bio(unsafe_bio: str) -> None:
    projected = _compact_public_profile_data(
        {
            "platform": "youtube",
            "handle": "privacy-canary",
            "followers": 12_345,
            "bio": unsafe_bio,
            "raw_platform_data": {"provider_payload": _CANARY_PROVIDER},
            "email": _CANARY_EMAIL,
        }
    )

    assert projected == {
        "platform": "youtube",
        "handle": "privacy-canary",
        "followers": 12_345,
    }


def test_public_profile_dto_keeps_safe_bio_with_iso_date() -> None:
    projected = _compact_public_profile_data(
        {
            "handle": "safe-camera-creator",
            "bio": "Camera reviews and field tests. New video 2026-08-01.",
        }
    )

    assert projected["bio"] == "Camera reviews and field tests. New video 2026-08-01."


def test_public_profile_dto_keeps_public_identity_handle() -> None:
    projected = _compact_public_profile_data(
        {
            "handle": "safe-camera-creator",
            "bio": "Follow @safe-camera-creator on Instagram for camera reviews.",
        }
    )

    assert projected["bio"] == "Follow @safe-camera-creator on Instagram for camera reviews."


def test_contact_preview_is_status_and_bounded_count_only() -> None:
    assert _compact_contact_preview(
        {
            "status": "READY",
            "channel_count": 999_999,
            "email": _CANARY_EMAIL,
            "contact_masked": True,
            "async": False,
        }
    ) == {"status": "ready", "channel_count": 10_000}


def test_profile_update_persists_only_public_profile_and_progress_state() -> None:
    conn = _ProfileUpdateConn()
    session_updates: list[dict[str, Any]] = []

    result = update_item_profile_execution(
        7,
        41,
        profile_result={
            "status": "ready",
            "profile_flow": {
                "status": "ready",
                "operation": "reuse",
                "kol_pool_id": 9001,
                "run_id": 77,
                "profile_data": {
                    "platform": "youtube",
                    "handle": "privacy-canary",
                    "followers": 12_345,
                    "bio": f"Email {_CANARY_EMAIL}",
                    "profile_text": f"Phone {_CANARY_PHONE}",
                    "raw_platform_data": {"secret": _CANARY_PROVIDER},
                    "business_email": _CANARY_EMAIL,
                },
                "write_result": {
                    "status": "written",
                    "detail": f"wrote {_CANARY_EMAIL}",
                    "provider_payload": {"secret": _CANARY_PROVIDER},
                    "contact_email": _CANARY_EMAIL,
                },
                "representative_video_analysis": {
                    "status": "ready",
                    "queued": 1,
                    "items": [
                        {
                            "status": "ready",
                            "error": f"Telegram: {_CANARY_PROVIDER}",
                            "metadata": {
                                "title": "Camera field test",
                                "description": f"Telegram: {_CANARY_PROVIDER}",
                            },
                        }
                    ],
                },
            },
            "contact_enrichment": {
                "status": "ready",
                "job_id": 501,
                "count": 2,
                "email": _CANARY_EMAIL,
                "contacts": [{"value": _CANARY_PHONE}],
                "provider_payload": {"secret": _CANARY_PROVIDER},
            },
            "audience_enrichment": {
                "status": "ready",
                "sample_size": 100,
                "raw_record": {"value": _CANARY_PHONE},
                "description": f"Telegram: {_CANARY_PROVIDER}",
            },
        },
        get_conn_fn=lambda: conn,
        update_session_fn=lambda _conn, _session_id, **kwargs: session_updates.append(kwargs),
    )

    assert conn.commits == 1
    assert session_updates[-1]["status"] == "running"
    stored = json.loads(conn.item["payload_json"])
    assert stored["profile_execute"]["profile_data"] == {
        "platform": "youtube",
        "handle": "privacy-canary",
        "followers": 12_345,
    }
    assert stored["profile_execute"]["contact_enrichment"] == {
        "status": "ready",
        "job_id": 501,
        "count": 2,
    }
    assert stored["profile_execute"]["write_result"] == {"status": "written"}
    assert stored["profile_execute"]["audience_enrichment"] == {
        "status": "ready",
        "sample_size": 100,
    }
    assert stored["profile_execute"]["representative_video_analysis"] == {
        "status": "ready",
        "queued": 1,
        "items": [{"status": "ready", "title": "Camera field test"}],
    }
    assert stored["contact_preview"] == {"status": "pending"}
    assert result["payload"] == stored
    serialized = json.dumps(stored, ensure_ascii=False)
    for canary in (
        _CANARY_EMAIL,
        _CANARY_PHONE,
        _CANARY_PROVIDER,
        _CANARY_MASKED,
    ):
        assert canary not in serialized


def test_profile_update_keeps_ready_status_while_optional_enrichment_is_pending() -> None:
    conn = _ProfileUpdateConn()
    session_updates: list[dict[str, Any]] = []

    result = update_item_profile_execution(
        7,
        41,
        profile_result={
            "status": "ready",
            "profile_flow": {
                "status": "ready",
                "kol_pool_id": 9001,
                "profile_data": {"platform": "youtube", "handle": "privacy-canary"},
            },
            "contact_enrichment": {"status": "pending_l0", "async": True},
            "audience_enrichment": {"status": "queued", "async": True, "job_id": 501},
        },
        get_conn_fn=lambda: conn,
        update_session_fn=lambda _conn, _session_id, **kwargs: session_updates.append(kwargs),
    )

    assert result["status"] == "ready"
    assert session_updates[-1]["status"] == "running"
    stored = json.loads(conn.item["payload_json"])
    assert stored["profile_execute"]["status"] == "ready"
    assert stored["profile_execute"]["contact_enrichment"]["status"] == "pending_l0"
    assert stored["profile_execute"]["audience_enrichment"] == {
        "status": "queued",
        "async": True,
        "job_id": 501,
    }


def test_row_mapper_redacts_legacy_dirty_profile_and_contact_preview() -> None:
    restored = _row_to_item(
        {
            "id": 41,
            "session_id": 7,
            "payload_json": json.dumps(
                {
                    "profile_execute": {
                        "profile_data": {
                            "platform": "youtube",
                            "handle": "privacy-canary",
                            "bio": _CANARY_WECHAT,
                            "raw_platform_data": {"secret": _CANARY_PROVIDER},
                            "email": _CANARY_EMAIL,
                        },
                        "contact_enrichment": {
                            "status": "ready",
                            "job_id": 501,
                            "count": 2,
                            "contact_value": _CANARY_PHONE,
                        },
                        "write_result": {
                            "status": "written",
                            "detail": f"legacy {_CANARY_EMAIL}",
                        },
                        "audience_enrichment": {
                            "status": "ready",
                            "raw_record": {"value": _CANARY_PHONE},
                        },
                        "representative_video_analysis": {
                            "status": "ready",
                            "items": [
                                {
                                    "metadata": {
                                        "description": f"Telegram: {_CANARY_PROVIDER}"
                                    }
                                }
                            ],
                        },
                    },
                    "contact_preview": {
                        "status": "ready",
                        "channel_count": 2,
                        "email": _CANARY_MASKED,
                        "available": True,
                    },
                    "source_fields": {
                        "profile_text": f"Reach me at {_CANARY_EMAIL}",
                        "provider_response": {"raw": _CANARY_PROVIDER},
                        "businessEmail": _CANARY_EMAIL,
                        "providerPayload": {"raw": _CANARY_PROVIDER},
                    },
                }
            ),
        }
    )

    assert restored["payload"]["profile_execute"]["profile_data"] == {
        "platform": "youtube",
        "handle": "privacy-canary",
    }
    assert restored["payload"]["profile_execute"]["contact_enrichment"] == {
        "status": "ready",
        "job_id": 501,
        "count": 2,
    }
    assert restored["payload"]["contact_preview"] == {
        "status": "ready",
        "channel_count": 2,
    }
    assert restored["payload"]["source_fields"] == {}
    serialized = json.dumps(restored, ensure_ascii=False)
    for canary in (
        _CANARY_EMAIL,
        _CANARY_PHONE,
        _CANARY_WECHAT,
        _CANARY_PROVIDER,
        _CANARY_MASKED,
    ):
        assert canary not in serialized


@pytest.mark.parametrize(
    "value",
    [
        "4155552671",
        "12345678",
        "reach me 4155552671",
        "Messenger private_handle",
        "DM me on Instagram @private",
        "message me on TikTok @private",
        "DM me on X @private",
        "send me a DM on Twitter @private",
    ],
)
def test_generic_legacy_session_string_drops_unlabeled_contact_values(value: str) -> None:
    assert _sanitize_session_payload({"arbitrary": value}) == {}


def test_generic_session_sanitizer_keeps_safe_date_and_messenger_product_text() -> None:
    assert _sanitize_session_payload(
        {"description": "Messenger app review published 20260818"}
    ) == {"description": "Messenger app review published 20260818"}


def test_audience_preview_is_strictly_typed() -> None:
    assert _compact_audience_preview(
        {
            "status": "READY",
            "method": f"email {_CANARY_EMAIL}",
            "confidence": "Telegram: private_handle",
            "sample_size": "4155552671",
            "async": False,
        }
    ) == {"status": "ready", "async": False}
    assert _compact_audience_preview(
        {
            "status": "ready",
            "method": "ensemble_v1",
            "confidence": 0.82,
            "sample_size": 321,
            "async": False,
        }
    ) == {
        "status": "ready",
        "method": "ensemble_v1",
        "confidence": 0.82,
        "sample_size": 321,
        "async": False,
    }


def test_session_row_mapper_scrubs_legacy_input_summary_and_query() -> None:
    restored = _row_to_session(
        {
            "id": 7,
            "query_text": f"find {_CANARY_EMAIL}",
            "query_type": "text_recall",
            "source": "test",
            "status": "ready",
            "created_by": 9,
            "input_payload_json": json.dumps(
                {
                    "market": "US",
                    "bio": _CANARY_EMAIL,
                    "raw_payload": {"value": "4155552671"},
                }
            ),
            "result_summary_json": json.dumps(
                {"phase": "profile", "arbitrary": "Messenger private_handle"}
            ),
            "approved_kol_ids": json.dumps([9, _CANARY_EMAIL, {"arbitrary": _CANARY_PHONE}, 9]),
            "archive_reason": "Telegram: private_handle",
        }
    )
    assert restored["query_text"] == ""
    assert restored["input_payload"] == {"market": "US"}
    assert restored["result_summary"] == {"phase": "profile"}
    assert restored["approved_kol_ids"] == [9]
    assert restored["archive_reason"] == ""


def test_get_session_resanitizes_pool_backfill_after_legacy_row_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_row = {
        "id": 7,
        "query_text": "camera creators",
        "query_type": "text_recall",
        "source": "test",
        "status": "ready",
        "created_by": 9,
        "input_payload_json": "{}",
        "result_summary_json": "{}",
        "approved_kol_ids": "[]",
    }
    item_row = {
        "id": 41,
        "session_id": 7,
        "dedupe_key": "recall:41",
        "item_type": "recall_candidate",
        "status": "matched",
        "stage": "identified",
        "rank": 1,
        "kol_pool_id": 41,
        "payload_json": json.dumps({"handle": "safe-creator", "followers": 5000}),
    }
    pool_row = {
        "id": 41,
        "display_name": f"Creator {_CANARY_EMAIL}",
        "email": "",
        "contact_channels": {},
        "other_contacts_json": [],
        "audience_estimated_json": {
            "method": f"email {_CANARY_EMAIL}",
            "confidence": "Telegram: private_handle",
            "sample_size": "4155552671",
        },
    }

    class _GetSessionConn:
        def execute(self, sql: str, _params: tuple[Any, ...]) -> _Cursor:
            normalized = " ".join(sql.split()).lower()
            if normalized.startswith("select * from vkpi_kol_search_sessions"):
                return _Cursor([session_row])
            if normalized.startswith("select * from vkpi_kol_search_session_items"):
                return _Cursor([item_row])
            if normalized.startswith("select id, display_name, email"):
                return _Cursor([pool_row])
            raise AssertionError(f"unexpected SQL: {normalized}")

    monkeypatch.setattr(search_sessions, "get_conn", lambda: _GetSessionConn())
    monkeypatch.setattr(search_sessions, "_refresh_enrichment_queue_states", lambda *_args: None)
    monkeypatch.setattr(
        search_sessions,
        "_apply_reach_display_gate",
        lambda _conn, items: (items, {"hidden_low_reach": 0, "hidden_analyzing": 0, "by_type": {}}),
    )
    restored = search_sessions.get_session(7, staff={"id": 9}, scope_to_staff=True)
    payload = restored["items"][0]["payload"]
    assert "display_name" not in payload
    assert payload["audience_preview"] == {"status": "ready", "async": False}
    serialized = json.dumps(restored, ensure_ascii=False)
    assert _CANARY_EMAIL not in serialized
    assert "private_handle" not in serialized
    assert "4155552671" not in serialized
