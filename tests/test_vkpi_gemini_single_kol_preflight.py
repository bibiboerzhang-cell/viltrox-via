from __future__ import annotations

import asyncio
import json

import pytest

from app.core.gemini_models import DEFAULT_FINAL_V1_CHAIN
from app.domains.costs import budget_guard
from app.domains.intelligence import gemini_single_kol_preflight
from scripts import vkpi_gemini_single_kol_preflight as preflight_script
from scripts import vkpi_gemini_go_no_go_report


def _fake_item(raw_platform_data: dict) -> dict:
    return {
        "id": 123,
        "platform": "youtube",
        "handle": "creatorone",
        "display_name": "Creator One",
        "profile_url": "https://www.youtube.com/@creatorone",
        "raw_platform_data": raw_platform_data,
        # Mirrors get_item(include_video_evidence=False): this public empty
        # placeholder must never suppress the separate bounded durable reader.
        "video_evidence": [],
        "sync_status": "synced",
        "last_seen_at": "2026-05-23T00:00:00Z",
    }


def _fake_budget(*_args, **_kwargs) -> dict:
    return {
        "mode": "llm_gateway_budget_preflight_v0",
        "provider_calls_allowed": False,
        "provider_gate_reason": "force_offline",
        "cost_scope": gemini_single_kol_preflight.GEMINI_SINGLE_KOL_SCOPE,
        "providers": [{"provider": "google", "provider_calls_allowed": False, "scopes": ["cron:p4_gemini_single_kol"]}],
    }


def _fake_budget_allowed(*_args, **_kwargs) -> dict:
    return {
        "mode": "llm_gateway_budget_preflight_v0",
        "provider_calls_allowed": True,
        "provider_gate_reason": "provider_calls_allowed",
        "cost_scope": gemini_single_kol_preflight.GEMINI_SINGLE_KOL_SCOPE,
        "providers": [
            {
                "provider": "google",
                "configured": True,
                "estimated_cost_usd": 0.02,
                "provider_calls_allowed": True,
                "scopes": ["monthly_total", "single_call", "provider:gemini", "cron:p4_gemini_single_kol"],
            }
        ],
    }


@pytest.fixture(autouse=True)
def _no_durable_video_evidence(monkeypatch):
    """Keep legacy raw-fixture tests isolated from the real database."""

    monkeypatch.setattr(
        gemini_single_kol_preflight.kol_pool,
        "_video_evidence_for_kol",
        lambda *_args, **_kwargs: [],
    )


def test_gemini_single_kol_preflight_selects_top_youtube_video_without_calls(monkeypatch) -> None:
    raw = {
        "videos": [
            {
                "id": "low",
                "title": "General update",
                "url": "https://www.youtube.com/watch?v=low12345678",
                "statistics": {"viewCount": 100},
            },
            {
                "id": "top12345678",
                "snippet": {"title": "Viltrox lens review"},
                "statistics": {"viewCount": 25000, "likeCount": 200, "commentCount": 80},
                "kind": "youtube#video",
            },
        ]
    }
    monkeypatch.setattr(gemini_single_kol_preflight.kol_pool, "get_item", lambda *_args, **_kwargs: {"item": _fake_item(raw)})
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget)

    payload = gemini_single_kol_preflight.build_kol_pool_gemini_preflight(123)

    assert payload["provider_calls"] is False
    assert payload["llm_calls"] is False
    assert payload["write_db"] is False
    assert payload["sync_triggered"] is False
    assert payload["task_enqueued"] is False
    assert payload["top_candidate"]["post_uid"] == "top12345678"
    assert payload["url_readiness"]["valid_video_url"] is True
    assert payload["url_readiness"]["provider_path"] == "youtube_direct_url_preflight"
    assert payload["go_no_go"]["candidate_ready_for_live_test"] is True
    assert payload["go_no_go"]["ready_for_manual_live_test"] is False
    assert payload["go_no_go"]["blocked_reason"] == "provider_gate:force_offline"


@pytest.mark.parametrize(
    "url",
    [
        "https://notyoutube.com/watch?v=abcdefghijk",
        "https://youtube.com.evil.test/watch?v=abcdefghijk",
        "https://evil.test/?next=youtube.com/watch?v=abcdefghijk&token=secret",
        "https://www.youtube.com/watch?v=abcdef",
        "https://www.youtube.com/embed/abcdefghijk/extra",
    ],
)
def test_gemini_preflight_strictly_rejects_malicious_or_invalid_youtube_urls(url: str) -> None:
    readiness = gemini_single_kol_preflight._url_readiness({"video_url": url})

    assert readiness["valid_video_url"] is False
    assert readiness["youtube_video_id"] == ""
    assert readiness["video_url"] == ""
    assert readiness["blocked_reason"] == "non_youtube_url"


def test_gemini_preflight_canonicalizes_valid_youtube_url_without_query_secrets() -> None:
    readiness = gemini_single_kol_preflight._url_readiness(
        {
            "video_url": (
                "https://m.youtube.com/shorts/abcdefghijk"
                "?si=secret-token&utm_source=private"
            )
        }
    )

    assert readiness["valid_video_url"] is True
    assert readiness["youtube_video_id"] == "abcdefghijk"
    assert readiness["video_url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert "secret-token" not in json.dumps(readiness)


def test_gemini_preflight_prioritizes_viltrox_content_over_pure_traffic(monkeypatch) -> None:
    raw = {
        "videos": [
            {
                "id": "viral000001",
                "kind": "youtube#video",
                "title": "The biggest camera video this year",
                "description": "General camera news without a product review.",
                "statistics": {
                    "viewCount": 10_000_000,
                    "likeCount": 500_000,
                    "commentCount": 100_000,
                },
            },
            {
                "id": "viltrox0001",
                "kind": "youtube#video",
                "title": "My latest field test",
                "description": "Full hands-on coverage of the Viltrox 27mm lens.",
                "statistics": {"viewCount": 100},
            },
        ]
    }
    monkeypatch.setattr(
        gemini_single_kol_preflight.kol_pool,
        "get_item",
        lambda *_args, **_kwargs: {"item": _fake_item(raw)},
    )
    monkeypatch.setattr(
        gemini_single_kol_preflight.llm_gateway,
        "budget_preflight",
        _fake_budget,
    )

    payload = gemini_single_kol_preflight.build_kol_pool_gemini_preflight(123)

    assert payload["top_candidate"]["post_uid"] == "viltrox0001"
    assert "viltrox_text_match" in payload["top_candidate"]["reasons"]
    assert payload["candidate_sample"][0]["candidate_score"] > payload["candidate_sample"][1]["candidate_score"]


def test_gemini_single_kol_preflight_blocks_missing_video(monkeypatch) -> None:
    monkeypatch.setattr(gemini_single_kol_preflight.kol_pool, "get_item", lambda *_args, **_kwargs: {"item": _fake_item({"profile": {"title": "Creator"}})})
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget)

    payload = gemini_single_kol_preflight.build_kol_pool_gemini_preflight(123)

    assert payload["provider_calls"] is False
    assert payload["candidate_strategy"]["candidate_count"] == 0
    assert payload["url_readiness"]["valid_video_url"] is False
    assert payload["go_no_go"]["blocked_reason"] == "no_cached_video_candidates"
    assert payload["checks"]["candidate_evaluated"] is True


def test_gemini_preflight_consumes_private_raw_sibling_without_leaking_it(monkeypatch) -> None:
    calls: list[dict] = []
    raw = {
        "contact_email": "private@example.test",
        "videos": [
            {
                "id": "sibling123X",
                "kind": "youtube#video",
                "title": "Viltrox field test",
                "url": (
                    "https://www.youtube.com/watch?v=sibling123X"
                    "&token=private-url-token"
                ),
                "statistics": {"viewCount": 3210},
            }
        ],
    }
    public_item = _fake_item({})
    public_item.pop("raw_platform_data")

    def fake_get_item(*_args, **kwargs):
        calls.append(kwargs)
        return {
            "item": {**public_item, "video_evidence": []},
            "_raw_platform_data_for_derivation": json.dumps(raw),
        }

    monkeypatch.setattr(gemini_single_kol_preflight.kol_pool, "get_item", fake_get_item)
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget)

    payload = gemini_single_kol_preflight.build_kol_pool_gemini_preflight(123)
    serialized = json.dumps(payload)

    assert calls == [{"include_raw_for_derivation": True, "include_video_evidence": False}]
    assert payload["top_candidate"]["post_uid"] == "sibling123X"
    assert payload["top_candidate"]["source_kind"] == "vkpi_kol_pool.raw_platform_data"
    assert payload["provider_calls"] is False
    assert payload["write_db"] is False
    assert "_raw_platform_data_for_derivation" not in serialized
    assert "raw_platform_data" not in payload
    assert "raw_platform_data" not in payload["item"]
    assert "private@example.test" not in serialized
    assert "private-url-token" not in serialized


def test_gemini_preflight_prefers_durable_video_evidence_without_raw(monkeypatch) -> None:
    public_item = _fake_item({})
    public_item.pop("raw_platform_data")
    public_item["platform"] = "instagram"
    durable_rows = [
        {
            "evidence_id": 902,
            "evidence_type": "image",
            "platform": "instagram",
            "content_url": "https://www.instagram.com/p/not-a-video/",
        },
        {
            "evidence_id": 903,
            "evidence_type": "video",
            "is_active": False,
            "platform": "youtube",
            "content_url": "https://www.youtube.com/watch?v=inactive903",
        },
        {
            "evidence_id": 904,
            "evidence_type": "image",
            "platform": "instagram",
            "content_url": "https://www.instagram.com/p/also-not-video/",
        },
        {
            "evidence_id": 901,
            "evidence_type": "video",
            "platform": "youtube",
            "title": "Persisted Viltrox review",
            "content_url": "https://www.youtube.com/watch?v=durable901X",
            "view_count": 8000,
        },
    ]
    reader_calls: list[dict] = []

    def fake_durable_reader(kol_pool_id, **kwargs):
        reader_calls.append({"kol_pool_id": kol_pool_id, **kwargs})
        return durable_rows[: int(kwargs.get("limit") or 0)]

    monkeypatch.setattr(
        gemini_single_kol_preflight.kol_pool,
        "get_item",
        lambda *_args, **_kwargs: {"item": public_item},
    )
    monkeypatch.setattr(
        gemini_single_kol_preflight.kol_pool,
        "_video_evidence_for_kol",
        fake_durable_reader,
    )
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget)

    payload = gemini_single_kol_preflight.build_kol_pool_gemini_preflight(
        123,
        candidate_limit=1,
    )

    assert payload["candidate_strategy"]["candidate_count"] == 1
    assert reader_calls == [
        {
            "kol_pool_id": 123,
            "limit": gemini_single_kol_preflight.DURABLE_EVIDENCE_SCAN_LIMIT,
            "include_inactive": False,
        }
    ]
    assert payload["candidate_strategy"]["limit"] == 1
    assert payload["top_candidate"]["post_uid"] == "durable901X"
    assert payload["top_candidate"]["source_kind"] == "vkpi_kol_video_evidence"
    assert payload["top_candidate"]["platform"] == "youtube"
    assert payload["url_readiness"]["valid_video_url"] is True
    assert payload["provider_calls"] is False
    assert payload["write_db"] is False


def test_gemini_preflight_merges_raw_relevance_into_same_durable_video(monkeypatch) -> None:
    public_item = _fake_item({})
    public_item.pop("raw_platform_data")
    raw = {
        "videos": [
            {
                "id": "mergevid001",
                "kind": "youtube#video",
                "title": "My latest field test",
                "description": "Complete Viltrox autofocus and image-quality review.",
                "statistics": {"viewCount": 100},
            },
            {
                "id": "viral000001",
                "kind": "youtube#video",
                "title": "General camera news",
                "statistics": {
                    "viewCount": 10_000_000,
                    "likeCount": 500_000,
                    "commentCount": 100_000,
                },
            },
        ]
    }
    durable_rows = [
        {
            "evidence_id": 905,
            "evidence_type": "video",
            "platform": "youtube",
            "title": "My latest field test",
            "content_url": "https://www.youtube.com/watch?v=mergevid001",
            "view_count": 50,
        }
    ]
    monkeypatch.setattr(
        gemini_single_kol_preflight.kol_pool,
        "get_item",
        lambda *_args, **_kwargs: {
            "item": public_item,
            "_raw_platform_data_for_derivation": raw,
        },
    )
    monkeypatch.setattr(
        gemini_single_kol_preflight.kol_pool,
        "_video_evidence_for_kol",
        lambda *_args, **_kwargs: durable_rows,
    )
    monkeypatch.setattr(
        gemini_single_kol_preflight.llm_gateway,
        "budget_preflight",
        _fake_budget,
    )

    payload = gemini_single_kol_preflight.build_kol_pool_gemini_preflight(123)

    assert payload["candidate_strategy"]["candidate_count"] == 2
    assert payload["top_candidate"]["post_uid"] == "mergevid001"
    assert payload["top_candidate"]["source_kind"] == "vkpi_kol_video_evidence"
    assert payload["top_candidate"]["merged_source_kinds"] == [
        "vkpi_kol_video_evidence",
        "vkpi_kol_pool.raw_platform_data",
    ]
    assert "viltrox_text_match" in payload["top_candidate"]["reasons"]


def test_gemini_single_kol_preflight_acceptance_script_is_readonly(monkeypatch) -> None:
    monkeypatch.setattr(
        preflight_script.natural_search,
        "search",
        lambda *_args, **_kwargs: {
            "provider_calls": False,
            "write_db": False,
            "total": 1,
            "items": [{"source_table": "vkpi_kol_pool", "source_id": 123, "title": "Creator One"}],
        },
    )
    monkeypatch.setattr(
        preflight_script.gemini_single_kol_preflight,
        "build_kol_pool_gemini_preflight",
        lambda *_args, **_kwargs: {
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "sync_triggered": False,
            "task_enqueued": False,
            "checks": {
                "preflight_completed": True,
                "candidate_evaluated": True,
                "url_readiness_checked": True,
                "budget_preflight_readonly": True,
            },
            "candidate_strategy": {"candidate_count": 1},
            "top_candidate": {"video_url": "https://www.youtube.com/watch?v=top12345678"},
            "url_readiness": {"valid_video_url": True, "provider_path": "youtube_direct_url_preflight"},
            "budget_preflight": {"provider_gate_reason": "force_offline"},
            "go_no_go": {"ready_for_manual_live_test": False, "blocked_reason": "provider_gate:force_offline"},
        },
    )

    report = preflight_script.build_report(query="viltrox")

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["task_enqueued"] is False
    assert report["checks"]["budget_preflight_readonly"] is True
    assert "Gemini Single-KOL Preflight" in preflight_script.render_markdown(report)


def test_gemini_single_kol_live_run_blocks_without_execute(monkeypatch) -> None:
    raw = {"videos": [{"id": "top12345678", "kind": "youtube#video", "title": "Viltrox lens review"}]}
    monkeypatch.setattr(gemini_single_kol_preflight.kol_pool, "get_item", lambda *_args, **_kwargs: {"item": _fake_item(raw)})
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget_allowed)
    called = {"n": 0}

    async def fake_analyzer(*_args, **_kwargs) -> dict:
        called["n"] += 1
        return {"analyzed": True}

    payload = asyncio.run(
        gemini_single_kol_preflight.run_kol_pool_gemini_single(
            123,
            execute=False,
            allow_provider_calls=True,
            analyzer=fake_analyzer,
        )
    )

    assert payload["executed"] is False
    assert payload["reason"] == "execute_not_requested"
    assert payload["provider_calls"] is False
    assert payload["write_db"] is False
    assert called["n"] == 0


def test_gemini_single_kol_live_run_blocks_when_budget_blocks(monkeypatch) -> None:
    raw = {"videos": [{"id": "top12345678", "kind": "youtube#video", "title": "Viltrox lens review"}]}
    monkeypatch.setattr(gemini_single_kol_preflight.kol_pool, "get_item", lambda *_args, **_kwargs: {"item": _fake_item(raw)})
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget)
    called = {"n": 0}

    async def fake_analyzer(*_args, **_kwargs) -> dict:
        called["n"] += 1
        return {"analyzed": True}

    payload = asyncio.run(
        gemini_single_kol_preflight.run_kol_pool_gemini_single(
            123,
            execute=True,
            allow_provider_calls=True,
            analyzer=fake_analyzer,
        )
    )

    assert payload["executed"] is False
    assert payload["reason"] == "provider_gate:force_offline"
    assert payload["provider_calls"] is False
    assert payload["write_db"] is False
    assert called["n"] == 0


def test_gemini_single_kol_live_run_uses_final_v1_p4_context_and_single_accounting(monkeypatch) -> None:
    from app.services.ai.analyzers import gemini_video

    raw = {"videos": [{"id": "top12345678", "kind": "youtube#video", "title": "Viltrox lens review"}]}
    monkeypatch.setattr(gemini_single_kol_preflight.kol_pool, "get_item", lambda *_args, **_kwargs: {"item": _fake_item(raw)})
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget_allowed)
    wrapper_record_calls: list[dict] = []
    calls = {"n": 0}

    async def fake_analyzer(url: str, title: str, handle: str, **kwargs) -> dict:
        calls["n"] += 1
        assert url == "https://www.youtube.com/watch?v=top12345678"
        assert title == "Viltrox lens review"
        assert handle == "creatorone"
        assert kwargs["schema_version"] == "final_v1"
        assert kwargs["final_v1_models"] == list(DEFAULT_FINAL_V1_CHAIN)
        llm_context = kwargs["llm_context"]
        assert llm_context["purpose"] == "p4_gemini_single_kol"
        assert llm_context["cost_tag"] == gemini_single_kol_preflight.GEMINI_SINGLE_KOL_SCOPE
        assert llm_context["metadata"]["task_binding"] == "audit_video_analysis"
        assert llm_context["metadata"]["phase"] == "video_analysis"
        assert llm_context["metadata"]["schema_version"] == "final_v1"
        return {
            "analyzed": True,
            "method": "gemini_direct_test",
            "quality_overall": 7,
            "cost_authority": gemini_single_kol_preflight.STRICT_GOOGLE_COST_AUTHORITY,
            "llm_attempts": [
                {
                    "authority": gemini_single_kol_preflight.STRICT_GOOGLE_COST_AUTHORITY,
                    "state": "settled",
                    "actual_cost_usd": 0.0042,
                }
            ],
        }

    def fake_record_cost(**kwargs) -> dict:
        wrapper_record_calls.append(kwargs)
        return {"recorded": True, "scope": kwargs.get("scope"), "cost_usd": kwargs.get("cost_usd")}

    monkeypatch.setattr(budget_guard, "record_cost", fake_record_cost)
    monkeypatch.setattr(gemini_video, "analyze_youtube_with_gemini", fake_analyzer)

    payload = asyncio.run(
        gemini_single_kol_preflight.run_kol_pool_gemini_single(
            123,
            execute=True,
            allow_provider_calls=True,
        )
    )

    assert calls["n"] == 1
    assert payload["executed"] is True
    assert payload["provider_calls"] is True
    assert payload["llm_calls"] is True
    assert payload["business_write_db"] is False
    assert payload["ledger_write_db"] is True
    assert payload["checks"]["provider_call_was_explicit"] is True
    assert payload["checks"]["ledger_recorded"] is True
    assert payload["checks"]["single_accounting_authority"] is True
    assert payload["checks"]["wrapper_cost_recorded"] is False
    assert payload["ledger"]["settled_attempt_count"] == 1
    assert payload["ledger"]["actual_cost_usd"] == pytest.approx(0.0042)
    assert wrapper_record_calls == []


def test_gemini_single_kol_custom_three_arg_analyzer_stays_compatible_without_false_ledger(monkeypatch) -> None:
    raw = {"videos": [{"id": "top12345678", "kind": "youtube#video", "title": "Viltrox lens review"}]}
    monkeypatch.setattr(
        gemini_single_kol_preflight.kol_pool,
        "get_item",
        lambda *_args, **_kwargs: {"item": _fake_item(raw)},
    )
    monkeypatch.setattr(
        gemini_single_kol_preflight.llm_gateway,
        "budget_preflight",
        _fake_budget_allowed,
    )
    calls: list[tuple[str, str, str]] = []

    async def custom_analyzer(url: str, title: str, handle: str) -> dict:
        calls.append((url, title, handle))
        return {
            "analyzed": False,
            "method": "custom_three_arg_analyzer",
            "cost_authority": gemini_single_kol_preflight.STRICT_GOOGLE_COST_AUTHORITY,
            "llm_attempts": [],
            "error": "no_provider_attempt",
        }

    payload = asyncio.run(
        gemini_single_kol_preflight.run_kol_pool_gemini_single(
            123,
            execute=True,
            allow_provider_calls=True,
            analyzer=custom_analyzer,
        )
    )

    assert calls == [
        (
            "https://www.youtube.com/watch?v=top12345678",
            "Viltrox lens review",
            "creatorone",
        )
    ]
    assert payload["executed"] is True
    assert payload["execution_status"] == "provider_error"
    assert payload["ledger_write_db"] is False
    assert payload["write_db"] is False
    assert payload["ledger"]["recorded"] is False
    assert payload["ledger"]["authoritative"] is False
    assert payload["checks"]["ledger_recorded"] is False


def test_strict_ledger_summary_rejects_attempt_missing_authority() -> None:
    ledger = gemini_single_kol_preflight._strict_ledger_summary(
        {
            "cost_authority": gemini_single_kol_preflight.STRICT_GOOGLE_COST_AUTHORITY,
            "llm_attempts": [
                {"state": "settled", "actual_cost_usd": 0.0042},
                {
                    "authority": gemini_single_kol_preflight.STRICT_GOOGLE_COST_AUTHORITY,
                    "state": "settled",
                    "actual_cost_usd": 0.0031,
                },
            ],
        }
    )

    assert ledger["authoritative"] is False
    assert ledger["recorded"] is False


def test_strict_ledger_summary_counts_released_reservation_as_ledger_write() -> None:
    ledger = gemini_single_kol_preflight._strict_ledger_summary(
        {
            "cost_authority": gemini_single_kol_preflight.STRICT_GOOGLE_COST_AUTHORITY,
            "llm_attempts": [
                {
                    "authority": gemini_single_kol_preflight.STRICT_GOOGLE_COST_AUTHORITY,
                    "state": "released",
                    "actual_cost_usd": None,
                }
            ],
        }
    )

    assert ledger["authoritative"] is True
    assert ledger["recorded"] is True
    assert ledger["released_attempt_count"] == 1
    assert ledger["actual_cost_usd"] == 0.0


def test_gemini_go_no_go_holds_when_budget_blocks(monkeypatch) -> None:
    raw = {"videos": [{"id": "top12345678", "kind": "youtube#video", "title": "Viltrox lens review"}]}
    monkeypatch.setattr(gemini_single_kol_preflight.kol_pool, "get_item", lambda *_args, **_kwargs: {"item": _fake_item(raw)})
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget)

    payload = gemini_single_kol_preflight.build_kol_pool_gemini_go_no_go(123)

    assert payload["decision"] == "hold"
    assert payload["provider_calls"] is False
    assert payload["llm_calls"] is False
    assert payload["write_db"] is False
    assert payload["summary"]["valid_video_url"] is True
    assert payload["summary"]["ready_for_manual_live_test"] is False
    assert "provider_gate:force_offline" in payload["blockers"]


def test_gemini_go_no_go_rejects_kol_without_video(monkeypatch) -> None:
    monkeypatch.setattr(gemini_single_kol_preflight.kol_pool, "get_item", lambda *_args, **_kwargs: {"item": _fake_item({"profile": {"title": "Creator"}})})
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget_allowed)

    payload = gemini_single_kol_preflight.build_kol_pool_gemini_go_no_go(123)

    assert payload["decision"] == "no_go_for_this_kol"
    assert payload["decision_reason"] == "candidate_not_ready"
    assert "no_cached_video_candidates" in payload["blockers"]
    assert payload["provider_calls"] is False


def test_gemini_go_no_go_allows_manual_single_call_when_ready(monkeypatch) -> None:
    raw = {"videos": [{"id": "top12345678", "kind": "youtube#video", "title": "Viltrox lens review"}]}
    monkeypatch.setattr(gemini_single_kol_preflight.kol_pool, "get_item", lambda *_args, **_kwargs: {"item": _fake_item(raw)})
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget_allowed)

    payload = gemini_single_kol_preflight.build_kol_pool_gemini_go_no_go(123)

    assert payload["decision"] == "go_manual_single_call"
    assert payload["summary"]["ready_for_manual_live_test"] is True
    assert payload["operator_gates"]["batch_allowed"] is False
    assert payload["provider_calls"] is False


def test_gemini_go_no_go_acceptance_script_is_readonly(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_gemini_go_no_go_report.natural_search,
        "search",
        lambda *_args, **_kwargs: {
            "provider_calls": False,
            "write_db": False,
            "total": 1,
            "items": [{"source_table": "vkpi_kol_pool", "source_id": 123, "title": "Creator One"}],
        },
    )
    monkeypatch.setattr(
        vkpi_gemini_go_no_go_report.gemini_single_kol_preflight,
        "build_kol_pool_gemini_go_no_go",
        lambda *_args, **_kwargs: {
            "decision": "hold",
            "decision_reason": "provider_or_budget_gate_not_ready",
            "blockers": ["provider_gate:force_offline"],
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "sync_triggered": False,
            "task_enqueued": False,
            "summary": {
                "candidate_count": 1,
                "valid_video_url": True,
                "provider_path": "youtube_direct_url_preflight",
                "top_video_url": "https://www.youtube.com/watch?v=top12345678",
                "provider_gate_reason": "force_offline",
                "ready_for_manual_live_test": False,
            },
            "checks": {
                "preflight_completed": True,
                "candidate_evaluated": True,
                "budget_gate_checked": True,
                "decision_recorded": True,
                "batch_still_blocked": True,
            },
        },
    )

    report = vkpi_gemini_go_no_go_report.build_report(query="viltrox")

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["summary"]["decision"] == "hold"
    assert "Gemini Go/No-Go" in vkpi_gemini_go_no_go_report.render_markdown(report)
