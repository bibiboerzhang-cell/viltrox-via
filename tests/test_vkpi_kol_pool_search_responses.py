from __future__ import annotations

import pytest

from app.api.routers.vkpi_kol_pool_search_responses import (
    _body_bool,
    _pending_enrichment_state,
    _text_response_status,
    _url_response_status,
)


@pytest.mark.parametrize("value", ["", "0", "false", "False", "no", "off"])
def test_body_bool_preserves_false_string_contract(value: str) -> None:
    assert _body_bool({"execute": value}, "execute", default=True) is False


def test_body_bool_uses_default_only_when_key_is_missing() -> None:
    assert _body_bool({}, "execute", default=True) is True
    assert _body_bool({"execute": None}, "execute", default=True) is False
    assert _body_bool({"execute": "yes"}, "execute") is True


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"profile_flow": {"status": "queued"}}, "queued"),
        ({"video_flow": {"status": "running"}}, "running"),
        ({"execute": True, "profile_flow": {"status": "crawl_failed"}}, "failed"),
        ({"execute": False, "url_type": "profile", "profile_flow": {"status": "dry_run_ready"}}, "ready"),
        ({"execute": False, "url_type": "unknown", "status": "unsupported"}, "partial"),
        ({"execute": True, "url_type": "video", "video_flow": {"status": "done"}}, "ready"),
        ({"execute": True, "url_type": "video", "video_flow": {"status": "unknown"}}, "partial"),
    ],
)
def test_url_response_status(result: dict, expected: str) -> None:
    assert _url_response_status(result) == expected


@pytest.mark.parametrize(
    ("recall", "discovery", "expected"),
    [
        ({"items": [], "buckets": {"creator": [], "reviewer": []}}, None, "empty"),
        ({"items": [{"id": 1}]}, None, "ready"),
        ({"items": [], "buckets": {"creator": [{"id": 1}]}}, None, "ready"),
        ({"status": "failed", "items": []}, None, "failed"),
        ({"status": "failed", "items": [{"id": 1}]}, None, "partial"),
        ({"status": "partial", "items": [{"id": 1}]}, None, "partial"),
        ({"items": []}, {"status": "provider_error", "items": [{"id": 2}]}, "partial"),
    ],
)
def test_text_response_status(recall: dict, discovery: dict | None, expected: str) -> None:
    assert _text_response_status(recall, discovery) == expected


def test_pending_enrichment_state_returns_fresh_payloads() -> None:
    first = _pending_enrichment_state()
    second = _pending_enrichment_state()

    first["contacts"]["status"] = "ready"

    assert second["contacts"]["status"] == "pending"
    assert second["audience"]["async"] is True
