"""Contracts for the authorization-safe account snapshot decomposition."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.domains.industry import snapshot_collector
from app.platform import industry_crawlers
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/app/domains/industry/snapshot_collector.py"
NEW_FUNCTIONS = {
    "_authorization_checkpoint",
    "_start_provider_call",
    "_scope_authorization_checkpoint",
    "_gate_rejection_records_platform_status",
    "_finish_gate_rejection",
    "_crawl_profile",
    "_profile_channel_id",
    "_crawl_videos",
    "_build_crawler_payload",
    "_collect_live_raw_data",
    "_record_live_platform_status",
    "_persist_account_snapshot",
    "collect_account_snapshot",
}


class _Cursor:
    def __init__(self, row: Any = None):
        self.row = row

    def fetchone(self) -> Any:
        return self.row


class _Conn:
    def __init__(self, account: dict[str, Any], events: list[Any]):
        self.account = account
        self.events = events

    def execute(self, sql: str, params: Any = ()) -> _Cursor:
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT * FROM vkpi_industry_accounts"):
            self.events.append(("select-account", tuple(params)))
            return _Cursor(dict(self.account))
        if "crawl_error_count=crawl_error_count+1" in normalized:
            self.events.append(("update-rejected", tuple(params)))
            return _Cursor()
        if "last_successful_at" in normalized:
            self.events.append(("update-success", tuple(params)))
            return _Cursor()
        raise AssertionError(normalized)

    def commit(self) -> None:
        self.events.append("commit")


def _account(platform: str = "youtube") -> dict[str, Any]:
    return {
        "id": 41,
        "platform": platform,
        "handle": "@creator",
        "profile_url": "https://profile.test/creator",
        "platform_user_id": "UC41",
        "crawl_enabled": 1,
        "sync_status": "idle",
    }


def _patch_persistence(monkeypatch: pytest.MonkeyPatch, events: list[Any], account: dict[str, Any]) -> None:
    conn = _Conn(account, events)
    monkeypatch.setattr(snapshot_collector, "ensure_vkpi_product_industry_schema", lambda: events.append("ensure-schema"))
    monkeypatch.setattr(snapshot_collector, "get_conn", lambda: events.append("get-conn") or conn)
    monkeypatch.setattr(snapshot_collector, "_utcnow", lambda: "2026-08-29T12:45:00Z")
    monkeypatch.setattr(snapshot_collector, "_today", lambda: "2026-08-29")
    monkeypatch.setattr(
        snapshot_collector,
        "_record_platform_test_status",
        lambda platform, status, metadata=None: events.append(("record-status", platform, status, metadata)),
    )
    monkeypatch.setattr(
        snapshot_collector,
        "calculate_kpis",
        lambda raw: events.append(("calculate", raw)) or {"youtube_kpi_status": "synced"},
    )
    monkeypatch.setattr(
        snapshot_collector,
        "_insert_snapshot",
        lambda account_id, payload: events.append(("snapshot", account_id, payload)) or {"id": 9},
    )
    monkeypatch.setattr(
        snapshot_collector,
        "_insert_posts",
        lambda acc, raw, limit: events.append(("posts", acc["id"], raw, limit)) or len(raw.get("videos") or []),
    )
    monkeypatch.setattr(
        snapshot_collector,
        "_sync_account_profile_fields",
        lambda acc, raw: events.append(("sync-profile", raw)) or dict(acc),
    )
    monkeypatch.setattr(snapshot_collector, "resolve_staff_id", lambda staff: events.append(("resolve-staff", staff)) or 77)
    monkeypatch.setattr(
        snapshot_collector,
        "_platform_config",
        lambda platform: events.append(("platform-config", platform)) or {"posts_per_account": 25},
    )


def test_snapshot_decomposition_stays_under_complexity_and_file_limits() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 800
    rows = collect_complexity({str(SOURCE): ast.parse(source)})
    observed = {row.qualified_name: row.cc for row in rows if row.qualified_name in NEW_FUNCTIONS}
    assert set(observed) == NEW_FUNCTIONS
    assert max(observed.values()) <= 25
    assert observed["collect_account_snapshot"] <= 10


def test_gate_rejection_preserves_authorization_status_write_and_commit_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []
    account = _account()
    _patch_persistence(monkeypatch, events, account)
    monkeypatch.setattr(
        snapshot_collector,
        "provider_gate",
        lambda acc, force=False: events.append(("gate", force)) or {
            "allowed": False,
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "message": "API key 未配置。",
        },
    )
    result = snapshot_collector.collect_account_snapshot(
        41,
        authorization_checkpoint=lambda: events.append("authorization"),
        provider_call_started=lambda: events.append("unexpected-provider-start"),
    )
    assert events == [
        "ensure-schema", "get-conn", ("select-account", (41,)), ("gate", False),
        "authorization",
        ("record-status", "youtube", "not_configured", {"last_gate_message": "API key 未配置。", "account_id": 41}),
        "authorization",
        ("update-rejected", ("not_configured", "2026-08-29T12:45:00Z", 41)),
        "commit",
    ]
    assert result == {
        "account": account,
        "allowed": False,
        "provider_status": "not_configured",
        "sync_status": "not_configured",
        "message": "API key 未配置。",
    }


def test_supplied_raw_data_bypasses_gate_and_preserves_write_fence_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []
    account = _account()
    _patch_persistence(monkeypatch, events, account)
    monkeypatch.setattr(
        snapshot_collector,
        "provider_gate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("raw data must bypass provider gate")),
    )
    raw = {"source": "manual", "kpi_status": "ok", "videos": [{"id": "v1"}]}
    result = snapshot_collector.collect_account_snapshot(
        41,
        raw_data=raw,
        staff={"id": 77},
        authorization_checkpoint=lambda: events.append("authorization"),
        authorization_scope_checkpoint=lambda: events.append("scope-authorization"),
        provider_call_started=lambda: events.append("unexpected-provider-start"),
    )
    assert events == [
        "ensure-schema", "get-conn", ("select-account", (41,)),
        "authorization",
        ("record-status", "youtube", "synced", {"last_live_account_id": 41, "last_live_source": "manual"}),
        ("calculate", raw),
        "authorization", ("snapshot", 41, {"youtube_kpi_status": "synced", "snapshot_date": "2026-08-29"}),
        "authorization", ("platform-config", "youtube"), ("posts", 41, raw, 25),
        "authorization", ("sync-profile", raw), "scope-authorization",
        ("update-success", ("synced", "2026-08-29T12:45:00Z", "2026-08-29T12:45:00Z", snapshot_collector._json(raw), 41)),
        "commit", ("resolve-staff", {"id": 77}),
    ]
    assert result["sync_status"] == "synced"
    assert result["posts_written"] == 1
    assert result["updated_by_staff_id"] == 77


class _YouTubeCrawler:
    configured = True

    def __init__(self, events: list[Any], *, fail_profile: bool = False):
        self.events = events
        self.fail_profile = fail_profile

    def crawl_channel_profile(self, handle: str, **kwargs: Any) -> dict[str, Any]:
        self.events.append(("provider-profile", handle, kwargs))
        if self.fail_profile:
            raise RuntimeError("profile-provider-failed")
        return {"items": [{"id": "UC41"}], "sync_status": "success", "provider_source": "api"}

    def crawl_channel_videos(self, channel_id: str, **kwargs: Any) -> dict[str, Any]:
        self.events.append(("provider-videos", channel_id, kwargs))
        return {"items": [{"id": "v1"}], "provider_source": "apify", "fallback_from": "api"}


def test_youtube_provider_calls_start_only_after_each_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []
    account = _account()
    _patch_persistence(monkeypatch, events, account)
    monkeypatch.setattr(snapshot_collector, "provider_gate", lambda *_args, **_kwargs: {"allowed": True})
    monkeypatch.setattr(industry_crawlers, "get_crawler", lambda platform: events.append(("get-crawler", platform)) or _YouTubeCrawler(events))
    snapshot_collector.collect_account_snapshot(
        41,
        authorization_checkpoint=lambda: events.append("authorization"),
        authorization_scope_checkpoint=lambda: events.append("scope-authorization"),
        provider_call_started=lambda: events.append("provider-started"),
    )
    profile_at = events.index(("provider-profile", "https://profile.test/creator", {"channel_id": "UC41"}))
    videos_at = events.index(("provider-videos", "UC41", {"max_results": 25}))
    assert events[profile_at - 2:profile_at] == ["authorization", "provider-started"]
    assert events[videos_at - 2:videos_at] == ["authorization", "provider-started"]
    calculated_raw = next(event[1] for event in events if isinstance(event, tuple) and event[0] == "calculate")
    assert calculated_raw == {
        "source": "youtube_api",
        "profile": {"items": [{"id": "UC41"}], "sync_status": "success", "provider_source": "api"},
        "videos": [{"id": "v1"}],
        "kpi_status": "success",
        "youtube_kpi_status": "success",
        "youtube_provider_source": "api",
        "youtube_fallback_from": "api",
    }


def test_other_platform_and_unsupported_paths_do_not_start_extra_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []
    account = _account("instagram")
    _patch_persistence(monkeypatch, events, account)
    monkeypatch.setattr(snapshot_collector, "provider_gate", lambda *_args, **_kwargs: {"allowed": True})

    class InstagramCrawler:
        configured = True

        def crawl_channel_profile(self, handle: str, **kwargs: Any) -> dict[str, Any]:
            events.append(("provider-profile", handle, kwargs))
            return {"items": [{"username": "creator", "latestPosts": [{"id": "ig1"}]}]}

        def crawl_channel_videos(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("non-YouTube path must not call crawl_channel_videos")

    monkeypatch.setattr(industry_crawlers, "get_crawler", lambda _platform: InstagramCrawler())
    snapshot_collector.collect_account_snapshot(
        41,
        authorization_checkpoint=lambda: events.append("authorization"),
        provider_call_started=lambda: events.append("provider-started"),
    )
    calculated_raw = next(event[1] for event in events if isinstance(event, tuple) and event[0] == "calculate")
    assert calculated_raw["source"] == "instagram_crawler"
    assert calculated_raw["videos"] == [{"id": "ig1"}]
    assert events.count("provider-started") == 1

    events.clear()
    monkeypatch.setattr(industry_crawlers, "get_crawler", lambda _platform: None)
    snapshot_collector.collect_account_snapshot(
        41,
        authorization_checkpoint=lambda: events.append("authorization"),
        provider_call_started=lambda: events.append("provider-started"),
    )
    unsupported = next(event[1] for event in events if isinstance(event, tuple) and event[0] == "calculate")
    assert unsupported == {
        "source": "unsupported",
        "platform": "instagram",
        "message": "instagram 适配器未注册",
    }
    assert "provider-started" not in events


def test_authorization_or_provider_exception_stops_before_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []
    account = _account()
    _patch_persistence(monkeypatch, events, account)
    monkeypatch.setattr(snapshot_collector, "provider_gate", lambda *_args, **_kwargs: {"allowed": True})
    monkeypatch.setattr(industry_crawlers, "get_crawler", lambda _platform: _YouTubeCrawler(events, fail_profile=True))
    with pytest.raises(RuntimeError, match="^profile-provider-failed$"):
        snapshot_collector.collect_account_snapshot(
            41,
            authorization_checkpoint=lambda: events.append("authorization"),
            provider_call_started=lambda: events.append("provider-started"),
        )
    assert events[-3:] == ["authorization", "provider-started", ("provider-profile", "https://profile.test/creator", {"channel_id": "UC41"})]
    assert not any(isinstance(event, tuple) and event[0] in {"snapshot", "update-success"} for event in events)

    events.clear()
    with pytest.raises(RuntimeError, match="^authorization-revoked$"):
        snapshot_collector.collect_account_snapshot(
            41,
            authorization_checkpoint=lambda: (_ for _ in ()).throw(RuntimeError("authorization-revoked")),
            provider_call_started=lambda: events.append("unexpected-provider-start"),
        )
    assert "unexpected-provider-start" not in events
    assert not any(isinstance(event, tuple) and event[0] == "provider-profile" for event in events)
