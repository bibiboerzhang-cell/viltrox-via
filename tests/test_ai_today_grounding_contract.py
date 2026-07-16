from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domains.market import ai_today


def _grounding_source(url: str = "https://example.com/current") -> dict[str, str]:
    return {
        "title": "Grounded source",
        "url": url,
        "provider": "google_search",
        "relation_type": "grounding",
    }


def _strategy_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "headline": "Grounded strategy",
        "shooting_plans": ["Plan"],
        "hot_topics": ["Topic"],
        "product_recommendations": ["EVO family"],
        "content_recommendations": ["Publish the evidence-backed concept"],
        "video_recommendations": ["Candidate video"],
    }
    payload.update(overrides)
    return payload


def _pipeline_provenance(status: str = "success") -> dict[str, object]:
    return {
        "pipeline": "ai_today_evidence_strategy_v1",
        "provider": "composed",
        "status": status,
        "attempts": [],
        "stages": {},
        "fallback_used": False,
    }


def test_production_ai_today_uses_strict_pipeline_not_legacy_direct_generator(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("V2_PRODUCTION_MODE", "1")
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: [])
    monkeypatch.setattr(
        ai_today,
        "_generate_ai_today_two_stage",
        lambda *_args, **_kwargs: (
            "",
            "gemini-2.5-pro->claude-opus-4-7",
            [],
            _pipeline_provenance("discovery_unavailable"),
        ),
    )
    monkeypatch.setattr(
        ai_today,
        "_generate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("production must not call the legacy direct SDK path")
        ),
    )

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "degraded"
    assert result["reason"] == "discovery_unavailable"
    assert result["provenance"]["pipeline"] == "ai_today_evidence_strategy_v1"


def test_extract_grounding_sources_supports_current_sdk_objects() -> None:
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[
                        SimpleNamespace(web=SimpleNamespace(uri="https://example.com/current", title="Current SDK"))
                    ]
                )
            )
        ]
    )

    assert ai_today._extract_grounding_sources(response) == [
        {
            "title": "Current SDK",
            "url": "https://example.com/current",
            "provider": "google_search",
            "relation_type": "grounding",
        }
    ]


def test_extract_grounding_sources_supports_legacy_wrapped_response() -> None:
    response = SimpleNamespace(
        _result={
            "candidates": [
                {
                    "groundingAttributions": [
                        {"source": {"url": "https://example.org/legacy", "title": "Legacy SDK"}}
                    ]
                }
            ]
        }
    )

    assert ai_today._extract_grounding_sources(response) == [
        {
            "title": "Legacy SDK",
            "url": "https://example.org/legacy",
            "provider": "google_search",
            "relation_type": "grounding",
        }
    ]


def test_extract_grounding_sources_does_not_borrow_from_later_candidate() -> None:
    response = {
        "candidates": [
            {"groundingMetadata": {"groundingChunks": []}},
            {
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"uri": "https://example.com/wrong-candidate", "title": "Wrong candidate"}}
                    ]
                }
            },
        ]
    }

    assert ai_today._extract_grounding_sources(response) == []


def test_discovery_failure_is_degraded_and_does_not_write_latest(monkeypatch) -> None:
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: [])
    monkeypatch.setattr(
        ai_today,
        "_generate_ai_today_two_stage",
        lambda *_args, **_kwargs: (
            "",
            "gemini-2.5-pro->claude-opus-4-7",
            [],
            _pipeline_provenance("discovery_unavailable"),
        ),
    )
    monkeypatch.setattr(
        ai_today,
        "_ensure_schema",
        lambda: (_ for _ in ()).throw(AssertionError("ungrounded fallback must not write latest")),
    )

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "degraded"
    assert result["grounding_status"] == "ungrounded"
    assert result["reason"] == "discovery_unavailable"
    assert result["sources"] == []


class _RowsResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _ReadConn:
    def __init__(
        self,
        rows: list[dict[str, object]],
        scheduler_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._rows = rows
        self._scheduler_rows = scheduler_rows or []

    def execute(self, query: str) -> _RowsResult:
        if "scheduler_tasks" in query:
            return _RowsResult(self._scheduler_rows)
        return _RowsResult(self._rows)


class _WriteConn:
    def __init__(self) -> None:
        self.params: tuple[object, ...] | None = None
        self.committed = False

    def execute(self, _query: str, params: tuple[object, ...]) -> None:
        self.params = params

    def commit(self) -> None:
        self.committed = True


def test_grounded_generation_persists_provenance_metadata(monkeypatch) -> None:
    source = _grounding_source()
    conn = _WriteConn()
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: ["Sony"])
    monkeypatch.setattr(
        ai_today,
        "_generate_ai_today_two_stage",
        lambda *_args, **_kwargs: (
            json.dumps(_strategy_payload(headline="Grounded")),
            "gemini-2.5-pro->claude-opus-4-7",
            [source],
            _pipeline_provenance(),
        ),
    )
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: conn)

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "ok"
    assert conn.committed is True
    assert conn.params is not None
    payload = json.loads(str(conn.params[0]))
    assert payload["sources"] == [source]
    assert payload["grounding_status"] == "grounded"
    assert payload["status"] == "ready"
    assert payload["evidence"] == [source]
    assert payload["product_recommendations"] == ["EVO family"]
    assert payload["content_recommendations"] == ["Publish the evidence-backed concept"]
    assert payload["video_recommendations"] == ["Candidate video"]
    assert payload["generated_at"].endswith("Z")


def test_latest_response_suppresses_claim_when_no_grounded_citation(monkeypatch) -> None:
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    rows = [
        {
            "snapshot_date": "2026-07-10",
            "content_json": json.dumps(
                {
                    "headline": "Unsupported latest claim",
                    "shooting_plans": ["Plan"],
                    "sources": [],
                    "grounding_status": "ungrounded",
                    "generated_at": generated_at,
                }
            ),
            "model": "claude:test-model",
            "created_at": generated_at,
        }
    ]
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _ReadConn(rows))

    result = ai_today.get_ai_today_hot()

    assert result["available"] is False
    assert result["reason"] == "no_grounded_latest"
    assert result["grounding_status"] == "ungrounded"
    assert result["sources"] == []
    assert "headline" not in result["content"]


def test_latest_response_exposes_grounding_and_freshness_compatibly(monkeypatch) -> None:
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    source = _grounding_source()
    rows = [
        {
            "snapshot_date": "2026-07-10",
            "content_json": json.dumps(
                {
                    "headline": "Grounded latest claim",
                    "shooting_plans": ["Plan"],
                    "hot_topics": ["Topic"],
                    "sources": [source],
                    "grounding_status": "grounded",
                    "generated_at": generated_at,
                }
            ),
            "model": "gemini:test+google_search",
            "created_at": generated_at,
        }
    ]
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _ReadConn(rows))
    monkeypatch.setattr(ai_today, "_market_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ai_today, "_recommended_video_rows", lambda: [])

    result = ai_today.get_ai_today_hot()

    assert result["available"] is True
    assert result["grounding_status"] == "grounded"
    assert result["sources"] == [source]
    assert result["generated_at"].endswith("Z")
    assert result["freshness_status"] == "fresh"
    assert result["content"]["grounding_status"] == "grounded"
    assert result["content"]["sources"] == [source]
    assert result["content"]["freshness_status"] == "fresh"


def test_legacy_bare_source_is_grounding_but_brand_context_is_not() -> None:
    assert ai_today._stored_grounding_sources([{"url": "https://example.com/legacy"}]) == [
        {"url": "https://example.com/legacy", "relation_type": "grounding"}
    ]
    assert ai_today._stored_grounding_sources(
        [{"url": "https://example.com/context", "relation_type": "brand_context"}]
    ) == []


def test_generate_rejects_string_shooting_plans_without_character_coercion(monkeypatch) -> None:
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: [])
    monkeypatch.setattr(
        ai_today,
        "_generate_ai_today_two_stage",
        lambda *_args, **_kwargs: (
            json.dumps(
                _strategy_payload(
                    headline="Typed contract",
                    shooting_plans="must-not-split",
                )
            ),
            "gemini-2.5-pro->claude-opus-4-7",
            [_grounding_source()],
            _pipeline_provenance(),
        ),
    )
    monkeypatch.setattr(
        ai_today,
        "_ensure_schema",
        lambda: (_ for _ in ()).throw(AssertionError("invalid result must not write latest")),
    )

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "invalid"
    assert result["reason"] == "invalid_result_contract"
    assert "shooting_plans:expected_list" in result["validation_errors"]


def test_partial_ai_today_result_is_degraded_and_not_persisted(monkeypatch) -> None:
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: [])
    monkeypatch.setattr(
        ai_today,
        "_generate_ai_today_two_stage",
        lambda *_args, **_kwargs: (
            json.dumps(_strategy_payload(headline="Partial", hot_topics=None)),
            "gemini-2.5-pro->claude-opus-4-7",
            [_grounding_source()],
            _pipeline_provenance(),
        ),
    )
    monkeypatch.setattr(
        ai_today,
        "_ensure_schema",
        lambda: (_ for _ in ()).throw(AssertionError("partial result must not write latest")),
    )

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "degraded"
    assert result["result_status"] == "degraded"
    assert result["reason"] == "partial_result_contract"
    assert "hot_topics:missing" in result["validation_errors"]


def test_provider_failures_reject_contract_without_overwriting_latest(monkeypatch) -> None:
    provenance = {
        **_pipeline_provenance("strategy_unavailable"),
        "model": "gemini-2.5-pro->claude-opus-4-7",
        "attempts": [
            {
                "stage": "grounded_discovery",
                "provider": "google",
                "model": "gemini-2.5-pro",
                "status": "ready",
            },
            {
                "stage": "evidence_strategy",
                "provider": "anthropic",
                "model": "claude-opus-4-7",
                "status": "degraded",
                "reason": "readiness_not_production_ready",
            },
        ],
    }
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: [])
    monkeypatch.setattr(
        ai_today,
        "_generate_ai_today_two_stage",
        lambda *_args, **_kwargs: (
            "",
            "gemini-2.5-pro->claude-opus-4-7",
            [_grounding_source()],
            provenance,
        ),
    )
    monkeypatch.setattr(
        ai_today,
        "_ensure_schema",
        lambda: (_ for _ in ()).throw(AssertionError("failed providers must not overwrite latest")),
    )

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "degraded"
    assert result["reason"] == "strategy_unavailable"
    assert result["grounding_status"] == "grounded"
    assert result["provenance"]["status"] == "strategy_unavailable"
    assert [attempt["provider"] for attempt in result["provenance"]["attempts"]] == [
        "google",
        "anthropic",
    ]


def test_stale_grounded_ai_today_cannot_report_ready(monkeypatch) -> None:
    generated_at = (datetime.now(tz=timezone.utc) - timedelta(hours=48)).isoformat()
    source = _grounding_source()
    rows = [
        {
            "snapshot_date": generated_at[:10],
            "content_json": json.dumps(
                {
                    "headline": "Stale but grounded",
                    "shooting_plans": ["Plan"],
                    "hot_topics": ["Topic"],
                    "sources": [source],
                    "generated_at": generated_at,
                }
            ),
            "model": "gemini:test+google_search",
            "created_at": generated_at,
        }
    ]
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _ReadConn(rows))
    monkeypatch.setattr(ai_today, "_market_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ai_today, "_recommended_video_rows", lambda: [])

    result = ai_today.get_ai_today_hot()

    assert result["available"] is True
    assert result["freshness_status"] == "stale"
    assert result["status"] == "degraded"
    assert result["is_ready"] is False
    assert result["content"]["status"] == "degraded"


def test_two_stage_pipeline_uses_strict_google_then_exact_opus_and_preserves_sources(monkeypatch) -> None:
    import app.services.ai.clients.gemini_client as gemini_module
    from app.platform import llm_production

    sentinel_client = object()
    calls: list[tuple[str, dict[str, object]]] = []
    source_url = "https://example.com/grounded-video"

    def fake_google(**kwargs: object) -> object:
        calls.append(("google", dict(kwargs)))
        assert kwargs["client"] is sentinel_client
        assert kwargs["model"] == "gemini-2.5-pro"
        assert kwargs["purpose"] == "ai_today.grounded_discovery"
        assert kwargs["cost_tag"] == "cron:ai_today_hot"
        assert kwargs["metadata"]["pipeline_stage"] == "grounded_discovery"  # type: ignore[index]
        assert kwargs["metadata"]["task_binding"] == "ai_today_grounded_discovery"  # type: ignore[index]
        return SimpleNamespace(
            text=json.dumps(
                {
                    "market_signals": ["Current external camera-market signal"],
                    "video_candidates": ["Grounded creator video candidate"],
                }
            ),
            candidates=[
                SimpleNamespace(
                    grounding_metadata=SimpleNamespace(
                        grounding_chunks=[
                            SimpleNamespace(
                                web=SimpleNamespace(uri=source_url, title="Grounded video")
                            )
                        ]
                    )
                )
            ],
        )

    def fake_claude(prompt: str, **kwargs: object) -> dict[str, object]:
        calls.append(("anthropic", dict(kwargs)))
        assert kwargs["provider"] == "anthropic"
        assert kwargs["model"] == "claude-opus-4-7"
        assert kwargs["purpose"] == "ai_today.evidence_strategy"
        assert kwargs["cost_tag"] == "cron:ai_today_hot"
        assert kwargs["metadata"]["pipeline_stage"] == "evidence_strategy"  # type: ignore[index]
        assert kwargs["metadata"]["task_binding"] == "ai_today_evidence_strategy"  # type: ignore[index]
        assert "EVIDENCE_BUNDLE=" in prompt
        assert source_url in prompt
        assert "Grounded creator video candidate" in prompt
        return {
            "status": "success",
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "json": _strategy_payload(),
        }

    monkeypatch.setattr(gemini_module, "gemini_client", sentinel_client)
    monkeypatch.setattr(llm_production, "generate_google_content", fake_google)
    monkeypatch.setattr(llm_production, "generate_json", fake_claude)

    raw, model, sources, provenance = ai_today._generate_ai_today_two_stage(
        "ground current external market evidence"
    )

    assert [provider for provider, _kwargs in calls] == ["google", "anthropic"]
    assert model == "gemini-2.5-pro->claude-opus-4-7"
    assert json.loads(raw) == _strategy_payload()
    assert sources == [
        {
            "title": "Grounded video",
            "url": source_url,
            "provider": "google_search",
            "relation_type": "grounding",
            "source_id": "S1",
        }
    ]
    assert provenance["status"] == "success"
    assert provenance["fallback_used"] is False
    assert provenance["readiness_enforced"] is True
    assert provenance["atomic_budget_enforced"] is True
    assert provenance["fleet_breaker_enforced"] is True
    assert provenance["evidence_only_strategy"] is True
    assert provenance["evidence_bundle"]["sources"] == sources


def test_two_stage_pipeline_never_calls_claude_when_grounded_discovery_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_today,
        "_run_ai_today_grounded_discovery",
        lambda _prompt: {
            "status": "degraded",
            "reason": "readiness_not_production_ready",
            "model": "gemini-2.5-pro",
            "content": {},
            "sources": [],
            "validation_errors": [],
            "attempt_log": [],
        },
    )
    monkeypatch.setattr(
        ai_today,
        "_run_ai_today_evidence_strategy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("strategy must not run without grounded evidence")
        ),
    )

    raw, model, sources, provenance = ai_today._generate_ai_today_two_stage("discover")

    assert raw == ""
    assert model == "gemini-2.5-pro->claude-opus-4-7"
    assert sources == []
    assert provenance["status"] == "discovery_unavailable"
    assert provenance["stages"]["evidence_strategy"]["status"] == "not_attempted"


def test_two_stage_pipeline_preserves_grounding_when_opus_is_blocked(monkeypatch) -> None:
    source = {**_grounding_source(), "source_id": "S1"}
    monkeypatch.setattr(
        ai_today,
        "_run_ai_today_grounded_discovery",
        lambda _prompt: {
            "status": "ready",
            "reason": "",
            "model": "gemini-2.5-pro",
            "content": {
                "market_signals": ["Signal"],
                "video_candidates": ["Video"],
            },
            "sources": [source],
            "validation_errors": [],
            "attempt_log": [],
        },
    )
    monkeypatch.setattr(
        ai_today,
        "_run_ai_today_evidence_strategy",
        lambda *_args, **_kwargs: {
            "status": "degraded",
            "reason": "readiness_not_production_ready",
            "model": "claude-opus-4-7",
            "content": {},
            "validation_errors": [],
        },
    )

    raw, _model, sources, provenance = ai_today._generate_ai_today_two_stage("discover")

    assert raw == ""
    assert sources == [source]
    assert provenance["status"] == "strategy_unavailable"
    assert provenance["source_urls"] == [source["url"]]
    assert provenance["fallback_used"] is False


def test_legacy_grounded_entrypoint_uses_strict_google_boundary_without_claude_fallback(monkeypatch) -> None:
    import app.services.ai.clients.gemini_client as gemini_module
    from app.platform import llm_production

    sentinel_client = object()
    calls: list[dict[str, object]] = []

    def fake_google(**kwargs: object) -> object:
        calls.append(dict(kwargs))
        return SimpleNamespace(
            text=json.dumps(
                {
                    "market_signals": ["Grounded signal"],
                    "video_candidates": ["Grounded video"],
                }
            ),
            candidates=[
                SimpleNamespace(
                    grounding_metadata=SimpleNamespace(
                        grounding_chunks=[
                            SimpleNamespace(
                                web=SimpleNamespace(
                                    uri="https://example.com/bounded",
                                    title="Bounded source",
                                )
                            )
                        ]
                    )
                )
            ],
        )

    monkeypatch.setattr(gemini_module, "gemini_client", sentinel_client)
    monkeypatch.setattr(llm_production, "generate_google_content", fake_google)

    raw, model, sources, provenance = ai_today._generate(
        "return grounded JSON",
        validator=ai_today._validate_ai_today_discovery,
    )

    assert json.loads(raw)["market_signals"] == ["Grounded signal"]
    assert model == "gemini:gemini-2.5-pro+google_search"
    assert sources[0]["url"] == "https://example.com/bounded"
    assert len(calls) == 1
    call = calls[0]
    assert call["client"] is sentinel_client
    assert call["model"] == "gemini-2.5-pro"
    assert call["purpose"] == "ai_today.grounded_discovery"
    assert call["cost_tag"] == "cron:ai_today_hot"
    assert call["metadata"]["pipeline_stage"] == "grounded_discovery"  # type: ignore[index]
    assert call["metadata"]["task_binding"] == "ai_today_grounded_discovery"  # type: ignore[index]
    assert provenance["provider"] == "google"
    assert provenance["status"] == "success"
    assert provenance["fallback_used"] is False


def test_legacy_grounded_entrypoint_fails_closed_when_strict_boundary_blocks(monkeypatch) -> None:
    import app.services.ai.clients.gemini_client as gemini_module
    from app.platform import llm_production

    monkeypatch.setattr(gemini_module, "gemini_client", object())
    monkeypatch.setattr(
        llm_production,
        "generate_google_content",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("readiness_blocked")),
    )

    raw, model, sources, provenance = ai_today._generate(
        "return grounded JSON",
        validator=ai_today._validate_ai_today_discovery,
    )

    assert raw == ""
    assert model == "gemini:gemini-2.5-pro+google_search"
    assert sources == []
    assert provenance["provider"] == "google"
    assert provenance["status"] == "RuntimeError"
    assert provenance["fallback_used"] is False


def test_video_evidence_requires_a_real_list_and_public_url() -> None:
    assert ai_today._validate_video_evidence("https://example.com/video")["status"] == "invalid"
    invalid_url = ai_today._validate_video_evidence(
        [{"title": "bad", "source_url": "javascript:alert(1)"}]
    )
    assert invalid_url["status"] == "invalid"
    assert ai_today._public_http_url("http://127.0.0.1/internal") == ""
    assert ai_today._public_http_url("http://localhost/internal") == ""


def test_fallback_to_older_valid_row_is_degraded_when_newer_row_is_invalid(monkeypatch) -> None:
    now = datetime.now(tz=timezone.utc)
    source = _grounding_source()
    rows = [
        {
            "snapshot_date": now.date().isoformat(),
            "content_json": json.dumps(
                {
                    "headline": "New but invalid",
                    "shooting_plans": "not-a-list",
                    "hot_topics": ["Topic"],
                    "sources": [source],
                    "generated_at": now.isoformat(),
                }
            ),
            "model": "gemini:new",
            "created_at": now.isoformat(),
        },
        {
            "snapshot_date": (now - timedelta(days=1)).date().isoformat(),
            "content_json": json.dumps(
                {
                    "headline": "Older valid row",
                    "shooting_plans": ["Plan"],
                    "hot_topics": ["Topic"],
                    "sources": [source],
                    "generated_at": (now - timedelta(hours=1)).isoformat(),
                }
            ),
            "model": "gemini:older",
            "created_at": (now - timedelta(hours=1)).isoformat(),
        },
    ]
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _ReadConn(rows))
    monkeypatch.setattr(ai_today, "_market_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ai_today, "_recommended_video_rows", lambda: [])

    result = ai_today.get_ai_today_hot()

    assert result["available"] is True
    assert result["freshness_status"] == "fresh"
    assert result["status"] == "degraded"
    assert result["is_ready"] is False
    assert "newer_rows_rejected" in result["content"]["validation_errors"]


def test_ready_snapshot_is_preserved_while_latest_failed_attempt_is_exposed(monkeypatch) -> None:
    now = datetime.now(tz=timezone.utc)
    source = _grounding_source()
    snapshot_rows = [
        {
            "snapshot_date": now.date().isoformat(),
            "content_json": json.dumps(
                {
                    "headline": "Previously ready",
                    "shooting_plans": ["Plan"],
                    "hot_topics": ["Topic"],
                    "sources": [source],
                    "generated_at": (now - timedelta(hours=1)).isoformat(),
                }
            ),
            "model": "gemini:ready+google_search",
            "created_at": (now - timedelta(hours=1)).isoformat(),
        }
    ]
    last_error = json.dumps(
        {
            "kind": "ai_today_attempt_v1",
            "status": "invalid",
            "reason": "invalid_result_contract",
            "provider": "anthropic",
            "provider_status": "transient_error",
            "generation_status": "all_providers_failed",
            "model": "claude-sonnet-4-5",
            "providers_attempted": ["google", "anthropic"],
        },
        separators=(",", ":"),
    )
    scheduler_rows = [
        {
            "last_run_at": now.isoformat(),
            "last_success_at": (now - timedelta(hours=1)).isoformat(),
            "last_error": last_error,
        }
    ]
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _ReadConn(snapshot_rows, scheduler_rows))
    monkeypatch.setattr(ai_today, "_market_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ai_today, "_recommended_video_rows", lambda: [])

    result = ai_today.get_ai_today_hot()

    assert result["available"] is True
    assert result["is_ready"] is True
    assert result["content"]["headline"] == "Previously ready"
    assert result["latest_attempt"] == result["content"]["latest_attempt"]
    assert result["latest_attempt"] == {
        "attempted_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "status": "invalid",
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "reason": "invalid_result_contract",
        "provider_status": "transient_error",
        "generation_status": "all_providers_failed",
        "providers_attempted": ["google", "anthropic"],
    }


def test_source_string_is_invalid_not_a_character_list(monkeypatch) -> None:
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: [])
    monkeypatch.setattr(
        ai_today,
        "_generate_ai_today_two_stage",
        lambda *_args, **_kwargs: (
            json.dumps(_strategy_payload(headline="Valid content")),
            "gemini-2.5-pro->claude-opus-4-7",
            "https://example.com/not-a-list",
            _pipeline_provenance(),
        ),
    )
    monkeypatch.setattr(
        ai_today,
        "_ensure_schema",
        lambda: (_ for _ in ()).throw(AssertionError("invalid sources must not write latest")),
    )

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "ungrounded"
    assert result["result_status"] == "invalid"
    assert result["reason"] == "invalid_grounding_contract"
    assert result["validation_errors"] == ["sources:expected_list"]
