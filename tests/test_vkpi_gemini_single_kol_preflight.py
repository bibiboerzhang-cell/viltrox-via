from __future__ import annotations

from app.services.vkpi import gemini_single_kol_preflight
from scripts import vkpi_gemini_single_kol_preflight as preflight_script


def _fake_item(raw_platform_data: dict) -> dict:
    return {
        "id": 123,
        "platform": "youtube",
        "handle": "creatorone",
        "display_name": "Creator One",
        "profile_url": "https://www.youtube.com/@creatorone",
        "raw_platform_data": raw_platform_data,
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


def test_gemini_single_kol_preflight_selects_top_youtube_video_without_calls(monkeypatch) -> None:
    raw = {
        "videos": [
            {
                "id": "low",
                "title": "General update",
                "url": "https://www.youtube.com/watch?v=low123456",
                "statistics": {"viewCount": 100},
            },
            {
                "id": "top123456",
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
    assert payload["top_candidate"]["post_uid"] == "top123456"
    assert payload["url_readiness"]["valid_video_url"] is True
    assert payload["url_readiness"]["provider_path"] == "youtube_direct_url_preflight"
    assert payload["go_no_go"]["candidate_ready_for_live_test"] is True
    assert payload["go_no_go"]["ready_for_manual_live_test"] is False
    assert payload["go_no_go"]["blocked_reason"] == "provider_gate:force_offline"


def test_gemini_single_kol_preflight_blocks_missing_video(monkeypatch) -> None:
    monkeypatch.setattr(gemini_single_kol_preflight.kol_pool, "get_item", lambda *_args, **_kwargs: {"item": _fake_item({"profile": {"title": "Creator"}})})
    monkeypatch.setattr(gemini_single_kol_preflight.llm_gateway, "budget_preflight", _fake_budget)

    payload = gemini_single_kol_preflight.build_kol_pool_gemini_preflight(123)

    assert payload["provider_calls"] is False
    assert payload["candidate_strategy"]["candidate_count"] == 0
    assert payload["url_readiness"]["valid_video_url"] is False
    assert payload["go_no_go"]["blocked_reason"] == "no_cached_video_candidates"
    assert payload["checks"]["candidate_evaluated"] is True


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
            "top_candidate": {"video_url": "https://www.youtube.com/watch?v=top123456"},
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
