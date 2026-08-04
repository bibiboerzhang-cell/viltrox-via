from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import local_release_acceptance as acceptance  # noqa: E402


NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
MIGRATION = "239_vkpi_kol_search_history_archive.sql"
TOKEN = "fixture-token-must-never-be-emitted"


class FixtureTransport:
    def __init__(self, responses: dict[Any, tuple[int, Any, dict[str, str] | None, float]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str | None, float]] = []
        self.post_calls: list[tuple[str, dict[str, Any], str | None, float]] = []

    def _response(self, key: Any) -> acceptance.HttpResponse:
        if key not in self.responses:
            raise AssertionError(f"unexpected offline HTTP request: {key}")
        status, payload, headers, latency = self.responses[key]
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return acceptance.HttpResponse(
            status=status,
            body=body,
            headers=headers or {"content-type": "application/json"},
            latency_ms=latency,
        )

    def get(self, path: str, *, token: str | None, timeout_seconds: float) -> acceptance.HttpResponse:
        self.calls.append((path, token, timeout_seconds))
        return self._response(path)

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any],
        token: str | None,
        timeout_seconds: float,
    ) -> acceptance.HttpResponse:
        body = deepcopy(json_body)
        self.post_calls.append((path, body, token, timeout_seconds))
        filters = body.get("filters") if isinstance(body.get("filters"), dict) else {}
        intent_key = (path, filters.get("intent"))
        key = intent_key if intent_key in self.responses else path
        return self._response(key)


def _runner(
    manifest: dict[str, Any],
    transport: FixtureTransport,
    *,
    head: str = HEAD,
    migration: str = MIGRATION,
) -> acceptance.AcceptanceRunner:
    return acceptance.AcceptanceRunner(
        base_url="http://127.0.0.1:8102",
        manifest=manifest,
        auth=acceptance.AuthContext(token=TOKEN, role="admin", expires_in_seconds=300),
        transport=transport,
        local_head=head,
        latest_migration=migration,
        now_fn=lambda: NOW,
    )


def _ask_find_response(intent: str, *, status: str = "ready") -> dict[str, Any]:
    request_id = "iq_fixture_request"
    missing = [{"field": "transcript_topic_index", "reason": "not indexed", "impact": "recall gap"}] if status == "partial" else []
    is_empty = status == "empty"
    return {
        "schema_version": "ask_find_v2",
        "request_id": request_id,
        "status": status,
        "intent": intent,
        "answer": "fixture answer",
        "facts": [
            {
                "key": "kol.confirmed_topic_match",
                "label": "Confirmed matching KOLs",
                "value": 0 if is_empty else 2,
                "value_type": "integer",
                "basis": "COUNT(DISTINCT KOL) from verified evidence",
                "confidence": "high",
            }
        ],
        "evidence": [] if is_empty else [
            {
                "id": "kol-7",
                "kind": "video_topic_match",
                "source": "vkpi_kol_video_evidence",
                "title": "Verified fixture evidence",
                "entity_id": 7,
                "observed_at": "2026-07-13T11:59:00Z",
                "confidence": "high",
            }
        ],
        # evidence_count is an aggregate over the underlying rows and need not
        # equal the bounded evidence examples returned in this response.
        "coverage": {
            "status": "complete",
            "matched_entities": 0 if is_empty else 2,
            "evidence_count": 0 if is_empty else 4_280,
            "notes": [],
        },
        "freshness": {
            "status": "unknown" if is_empty else "fresh",
            "generated_at": "2026-07-13T12:00:00Z",
            "timezone": "UTC",
            **({} if is_empty else {"data_updated_at": "2026-07-13T11:59:00Z"}),
        },
        "missing_fields": missing,
        "actions": [{"type": "navigate", "requires_approval": False}],
        "trace": {
            "request_id": request_id,
            "query_version": "ask_find_v2",
            "execution_mode": "deterministic",
            "deterministic": True,
            "search_executed": False,
        },
    }


def test_declared_read_only_json_post_executes_ask_contract_without_emitting_body() -> None:
    intent = "kol.video_topic.count"
    body = {
        "query": "多少 KOL 做过 26mm EVO 视频？",
        "filters": {"intent": intent, "topic": "26mm EVO"},
        "mode": "deterministic",
    }
    manifest = {
        "name": "ask-post",
        "version": 2,
        "board_families": [],
        "endpoints": [
            acceptance._ep(
                "ask-topic",
                "intelligent",
                "/api/admin/vkpi/intelligent/query",
                method="POST",
                read_only_post=True,
                json_body=body,
                contract="ask_find_v2",
                expected_intent=intent,
                data_paths=["facts", "evidence"],
                state_paths=["status"],
                allowed_states=["real", "empty", "pending"],
            )
        ],
    }
    transport = FixtureTransport({("/api/admin/vkpi/intelligent/query", intent): (200, _ask_find_response(intent, status="partial"), None, 4.0)})

    report = _runner(manifest, transport).run()

    assert report["overall"]["pass"] is True
    assert report["endpoints"][0]["method"] == "POST"
    assert report["endpoints"][0]["data_state"] == "pending"
    assert report["safety"]["http_methods"] == ["POST"]
    assert report["safety"]["read_only_post_requests"] == 1
    assert report["safety"]["request_bodies_emitted"] is False
    assert transport.post_calls == [("/api/admin/vkpi/intelligent/query", body, TOKEN, 15.0)]
    assert "26mm EVO" not in json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (lambda payload: payload.update(intent="project.search"), "intent mismatch"),
        (lambda payload: payload.pop("coverage"), "missing Ask & Find v2 field: coverage"),
        (lambda payload: payload["trace"].update(request_id="iq_other"), "trace.request_id"),
    ],
)
def test_ask_contract_rejects_wrong_intent_or_incomplete_evidence_schema(mutate, expected_error: str) -> None:
    intent = "kol.pool.overview"
    payload = _ask_find_response(intent)
    mutate(payload)
    spec = acceptance._ep(
        "ask-overview",
        "intelligent",
        "/api/admin/vkpi/intelligent/query",
        method="POST",
        read_only_post=True,
        json_body={"query": "KOL 数量", "filters": {"intent": intent}},
        contract="ask_find_v2",
        expected_intent=intent,
    )

    validation = acceptance._validate_contract(payload, spec, None)

    assert any(expected_error in error for error in validation.errors)


def _ask_spec(intent: str = "kol.pool.overview") -> dict[str, Any]:
    return acceptance._ep(
        "ask-overview",
        "intelligent",
        "/api/admin/vkpi/intelligent/query",
        method="POST",
        read_only_post=True,
        json_body={"query": "KOL 数量", "filters": {"intent": intent}},
        contract="ask_find_v2",
        expected_intent=intent,
    )


@pytest.mark.parametrize(
    ("status", "mutate", "expected_error"),
    [
        ("ready", lambda payload: payload["facts"][0].pop("label"), "facts[0] missing fields: label"),
        ("ready", lambda payload: payload["facts"][0].update(key=" "), "facts[0].key is empty"),
        ("ready", lambda payload: payload["evidence"][0].pop("kind"), "evidence[0].kind is empty"),
        (
            "ready",
            lambda payload: payload.update(
                evidence=[{"kind": "aggregate", "confidence": "high"}]
            ),
            "evidence[0] has no source locator",
        ),
        ("ready", lambda payload: payload["coverage"].update(matched_entities=-1), "matched_entities is negative"),
        ("ready", lambda payload: payload["coverage"].update(status="ready"), "coverage.status is invalid"),
        ("ready", lambda payload: payload["freshness"].update(generated_at="2026-07-13T12:00:00"), "generated_at is not an ISO UTC"),
        ("ready", lambda payload: payload["freshness"].update(timezone="America/New_York"), "timezone is not UTC"),
        ("ready", lambda payload: payload["freshness"].pop("data_updated_at"), "lacks data_updated_at evidence"),
        (
            "ready",
            lambda payload: payload.update(missing_fields=[{"field": "x", "reason": "r", "impact": "i"}]),
            "ready status contains missing_fields",
        ),
        ("ready", lambda payload: payload["coverage"].update(status="partial"), "ready status requires complete coverage"),
        ("partial", lambda payload: payload.update(missing_fields=[]), "partial status has no explicit missing_fields"),
        ("partial", lambda payload: payload["coverage"].update(status="empty"), "partial status has incompatible coverage"),
        (
            "empty",
            lambda payload: payload["coverage"].update(matched_entities=1, evidence_count=1),
            "empty status has non-zero coverage counts",
        ),
        ("empty", lambda payload: payload["coverage"].update(status="partial"), "empty status has incompatible coverage"),
    ],
)
def test_ask_contract_rejects_malformed_truth_fields_and_inconsistent_states(
    status: str,
    mutate,
    expected_error: str,
) -> None:
    payload = _ask_find_response("kol.pool.overview", status=status)
    mutate(payload)

    validation = acceptance._validate_contract(payload, _ask_spec(), None)

    assert any(expected_error in error for error in validation.errors), validation.errors


def test_ask_contract_accepts_bounded_evidence_examples_with_larger_aggregate_count() -> None:
    payload = _ask_find_response("kol.pool.overview")

    validation = acceptance._validate_contract(payload, _ask_spec(), None)

    assert len(payload["evidence"]) == 1
    assert payload["coverage"]["evidence_count"] == 4_280
    assert validation.errors == []


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_url_transport_never_follows_redirects_or_replays_bearer(method: str) -> None:
    sink_requests: list[dict[str, str | None]] = []

    class SinkHandler(BaseHTTPRequestHandler):
        def _capture(self) -> None:
            sink_requests.append({"method": self.command, "authorization": self.headers.get("Authorization")})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        do_GET = _capture
        do_POST = _capture

        def log_message(self, format: str, *args: Any) -> None:
            return

    sink = ThreadingHTTPServer(("127.0.0.1", 0), SinkHandler)
    sink_thread = Thread(target=sink.serve_forever, daemon=True)
    sink_thread.start()

    class RedirectHandler(BaseHTTPRequestHandler):
        def _redirect(self) -> None:
            content_length = int(self.headers.get("Content-Length") or 0)
            if content_length:
                self.rfile.read(content_length)
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/sink")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"redirect":true}')

        do_GET = _redirect
        do_POST = _redirect

        def log_message(self, format: str, *args: Any) -> None:
            return

    source = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    source_thread = Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    try:
        transport = acceptance.UrlLibTransport(f"http://127.0.0.1:{source.server_port}")
        if method == "POST":
            response = transport.post(
                "/redirect",
                json_body={"query": "safe read"},
                token=TOKEN,
                timeout_seconds=2,
            )
        else:
            response = transport.get("/redirect", token=TOKEN, timeout_seconds=2)

        assert response.status == 302
        assert sink_requests == []
    finally:
        source.shutdown()
        source.server_close()
        source_thread.join(timeout=2)
        sink.shutdown()
        sink.server_close()
        sink_thread.join(timeout=2)


def test_read_only_post_requires_json_response_content_type() -> None:
    intent = "kol.pool.overview"
    manifest = {
        "name": "ask-content-type",
        "version": 2,
        "board_families": [],
        "endpoints": [
            acceptance._ep(
                "ask-overview",
                "intelligent",
                "/api/admin/vkpi/intelligent/query",
                method="POST",
                read_only_post=True,
                json_body={"query": "KOL 数量", "filters": {"intent": intent}},
                contract="ask_find_v2",
                expected_intent=intent,
            )
        ],
    }
    transport = FixtureTransport(
        {("/api/admin/vkpi/intelligent/query", intent): (200, _ask_find_response(intent), {"content-type": "text/plain"}, 1.0)}
    )

    report = _runner(manifest, transport).run()

    assert report["overall"]["pass"] is False
    assert "read-only POST response content-type is not JSON" in report["endpoints"][0]["errors"]
