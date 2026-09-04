from __future__ import annotations

import json

import pytest

from app.domains.kol import (
    evidence_side_effects,
    profile_basics,
    url_deep_crawl,
    url_deep_crawl_execute,
    url_deep_crawl_execute_profile_videos,
    video_evidence,
    video_tracking,
)


PROFILE_URL = "https://www.youtube.com/@creator"


def _youtube_fenced_body(channel_id: str) -> dict:
    return {
        "maintenance_refresh": True,
        "maintenance_target_fence": {
            "platform": "youtube",
            "stable_handle": "creator",
            "canonical_profile_url": PROFILE_URL,
            "stable_native_ids": {"channel_id": channel_id},
        },
    }


def test_profile_data_accepts_same_youtube_channel_locator_and_freezes_url():
    channel_id = "UCaaaaaaaaaaaaaaaaaaaaaa"
    profile_data = {
        "platform": "youtube",
        "handle": "creator",
        "profile_url": f"https://www.youtube.com/channel/{channel_id}",
        "raw_platform_data": json.dumps(
            {"profile": {"items": [{"id": channel_id}]}}
        ),
    }

    url_deep_crawl_execute._verify_maintenance_profile_data_identity(
        _youtube_fenced_body(channel_id),
        profile_data,
    )

    assert profile_data["handle"] == "creator"
    assert profile_data["profile_url"] == PROFILE_URL


@pytest.mark.parametrize(
    ("url_channel_id", "raw_channel_id"),
    [
        ("UCbbbbbbbbbbbbbbbbbbbbbb", "UCaaaaaaaaaaaaaaaaaaaaaa"),
        ("UCaaaaaaaaaaaaaaaaaaaaaa", "UCbbbbbbbbbbbbbbbbbbbbbb"),
        ("UCaaaaaaaaaaaaaaaaaaaaaa", None),
    ],
)
def test_profile_data_rejects_different_or_missing_youtube_channel_id(
    url_channel_id,
    raw_channel_id,
):
    channel_id = "UCaaaaaaaaaaaaaaaaaaaaaa"
    raw_items = [{"id": raw_channel_id}] if raw_channel_id else [{}]
    profile_data = {
        "platform": "youtube",
        "handle": "creator",
        "profile_url": f"https://www.youtube.com/channel/{url_channel_id}",
        "raw_platform_data": json.dumps({"profile": {"items": raw_items}}),
    }

    with pytest.raises(video_tracking.VideoTrackingError) as error:
        url_deep_crawl_execute._verify_maintenance_profile_data_identity(
            _youtube_fenced_body(channel_id),
            profile_data,
        )

    assert error.value.code == "maintenance_refresh_provider_identity_mismatch"


def test_profile_finalize_suppresses_reach_floor_only_when_requested(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(profile_basics, "_record_creator_identity_alias", lambda *_a, **_k: None)
    monkeypatch.setattr(profile_basics, "_commit", lambda _db: events.append("commit"))
    monkeypatch.setattr(
        "app.domains.kol.reach_floor_regate.reapply_reach_floor",
        lambda *_a, **_k: events.append("reach_floor"),
    )

    common = {
        "target_id": 7,
        "requested_identity": {},
        "canonical_match": False,
        "commit_write": True,
        "planned_values": {"followers": 10},
        "normalized": {},
        "existing": {},
        "avatar_landing_budget": None,
        "suppress_contact_acquisition": True,
        "suppress_avatar_landing": True,
    }
    profile_basics._finalize_profile_write(
        object(),
        **common,
        suppress_reach_floor_regate=True,
    )
    assert events == ["commit"]

    profile_basics._finalize_profile_write(
        object(),
        **common,
        suppress_reach_floor_regate=False,
    )
    assert events == ["commit", "commit", "reach_floor"]


def test_evidence_writer_suppresses_tracking_only_when_requested(monkeypatch):
    calls: list[tuple[int, int | None]] = []
    monkeypatch.setattr(video_evidence, "_load_kol", lambda _db, kid: {"id": kid})
    monkeypatch.setattr(
        video_evidence,
        "_table_columns",
        lambda _db, _table: {
            "id",
            "kol_pool_id",
            "content_url",
            "platform",
            "title",
            "video_title",
            "created_at",
            "updated_at",
            "source",
            "source_ref",
        },
    )
    monkeypatch.setattr(video_evidence, "_load_existing_evidence", lambda *_a, **_k: None)
    monkeypatch.setattr(
        video_evidence,
        "_fetch_video_metadata",
        lambda url: {"content_url": url, "platform": "youtube", "title": "t"},
    )
    monkeypatch.setattr(video_evidence, "_score_snapshot", lambda *_a, **_k: {})
    monkeypatch.setattr(video_evidence, "_insert_evidence", lambda *_a, **_k: 901)
    monkeypatch.setattr(video_evidence, "_commit", lambda _db: None)
    monkeypatch.setattr(video_evidence, "_rollback", lambda _db: None)
    monkeypatch.setattr(
        evidence_side_effects,
        "enroll_tracking_after_new_evidence",
        lambda kid, *, evidence_id=None, conn=None: calls.append((kid, evidence_id)) or {},
    )

    suppressed = video_evidence.ensure_video_evidence_from_url(
        9,
        "https://www.youtube.com/watch?v=suppressed",
        dry_run=False,
        conn=object(),
        suppress_tracking_enroll=True,
    )
    assert suppressed["status"] == "created"
    assert "tracking_enroll" not in suppressed
    assert calls == []

    normal = video_evidence.ensure_video_evidence_from_url(
        9,
        "https://www.youtube.com/watch?v=normal",
        dry_run=False,
        conn=object(),
    )
    assert normal["status"] == "created"
    assert calls == [(9, 901)]


def test_maintenance_profile_flow_requests_reach_floor_suppression(monkeypatch):
    observed: list[bool] = []
    conn = object()
    monkeypatch.setattr(url_deep_crawl_execute, "get_conn", lambda: conn)
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_profile_incremental_state",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_revalidate_maintenance_profile_target",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_crawl_profile_basics",
        lambda *_a, **_k: {"status": "ok", "profile_payload": {}},
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_verify_maintenance_crawl_identity",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_profile_data_from_crawl",
        lambda *_a, **_k: {
            "platform": "youtube",
            "handle": "creator",
            "profile_url": PROFILE_URL,
        },
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_verify_maintenance_profile_data_identity",
        lambda *_a, **_k: None,
    )

    def write_profile(_kid, _data, **kwargs):
        observed.append(kwargs["suppress_reach_floor_regate"])
        return {"kol_pool_id": 7, "fields_written": [], "viltrox_fit_score_changed_ids": []}

    monkeypatch.setattr(url_deep_crawl_execute, "write_kol_profile_basics", write_profile)
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_execute_profile_representative_video_analysis",
        lambda *_a, **_k: {"worker_touched": False, "viltrox_fit_score_changed_ids": []},
    )
    monkeypatch.setattr(
        url_deep_crawl_execute,
        "_execute_profile_history_video_evidence",
        lambda *_a, **_k: {"worker_touched": False, "viltrox_fit_score_changed_ids": []},
    )
    monkeypatch.setattr(url_deep_crawl_execute, "_record_deep_crawl_run", lambda *_a, **_k: 1)

    classified = url_deep_crawl.classify_url(PROFILE_URL)
    url_deep_crawl_execute._execute_profile_flow(
        classified,
        [{"kol_pool_id": 7}],
        {"maintenance_refresh": True, "mode": "account_deep"},
    )
    url_deep_crawl_execute._execute_profile_flow(
        classified,
        [{"kol_pool_id": 7}],
        {"mode": "account_deep", "suppress_profile_followups": True},
    )
    assert observed == [True, False]


def test_maintenance_history_requests_tracking_suppression(monkeypatch):
    observed: list[bool] = []
    module = url_deep_crawl_execute_profile_videos
    monkeypatch.setattr(module, "_profile_should_materialize_history_videos", lambda _body: True)
    monkeypatch.setattr(module, "_profile_history_video_limit", lambda _body: 1)
    monkeypatch.setattr(
        module,
        "_profile_representative_video_metadata",
        lambda *_a, **_k: [{"content_url": "https://www.youtube.com/watch?v=abc"}],
    )
    monkeypatch.setattr(module, "_filter_incremental_profile_videos", lambda videos, *_a, **_k: (videos, 0))
    monkeypatch.setattr(module, "_lock_maintenance_target_for_write", lambda *_a, **_k: None)

    def write_evidence(*_args, **kwargs):
        observed.append(kwargs["suppress_tracking_enroll"])
        return {"ok": True, "status": "created", "evidence_id": 1}

    monkeypatch.setattr(module, "ensure_video_evidence_from_url", write_evidence)
    classified = url_deep_crawl.classify_url(PROFILE_URL)
    common = {
        "classified": classified,
        "kol_pool_id": 7,
        "crawl": {},
        "incremental_state": {},
    }
    module._execute_profile_history_video_evidence(
        object(),
        body={"maintenance_refresh": True},
        **common,
    )
    module._execute_profile_history_video_evidence(
        object(),
        body={},
        **common,
    )
    assert observed == [True, False]
