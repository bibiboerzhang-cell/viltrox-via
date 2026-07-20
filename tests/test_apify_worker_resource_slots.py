from __future__ import annotations

import pytest

from app.workers import apify_job_resource_slots as slots
from app.workers import apify_jobs_worker as worker


def test_reviewed_job_families_map_to_separate_resource_groups() -> None:
    assert slots.resource_group_for_job({"job_type": "kol_profile_deep_crawl"}) == "profile_media"
    assert slots.resource_group_for_job({"job_type": "kol_pool_comments_collect"}) == "comments_pipeline"
    assert slots.resource_group_for_job({"job_type": "official_channel_comments_collect"}) == "comments_pipeline"
    assert slots.resource_group_for_job(
        {"job_type": "video", "payload": {"derive_method": "video_analysis_final_v1"}}
    ) == "gemini_video"
    assert slots.resource_group_for_job(
        {"job_type": "video", "payload": {"derive_method": "mock"}}
    ) is None
    assert slots.resource_group_for_job({"job_type": "session_advance"}) is None


def test_resource_limits_default_to_one_and_opt_in_capped_at_sixteen() -> None:
    assert slots.resource_slot_limits({}) == {
        "profile_media": 1,
        "comments_pipeline": 1,
        "gemini_video": 1,
    }
    configured = slots.resource_slot_limits(
        {
            "APIFY_WORKER_PROFILE_MEDIA_CONCURRENCY": "2",
            "APIFY_WORKER_COMMENTS_CONCURRENCY": "2",
            "APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY": "2",
        }
    )
    assert set(configured.values()) == {2}
    wide = slots.resource_slot_limits({"APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY": "16"})
    assert wide["gemini_video"] == 16
    for bad in ("0", "17", "many"):
        with pytest.raises(ValueError):
            slots.resource_slot_limits({"APIFY_WORKER_COMMENTS_CONCURRENCY": bad})


def test_slot_acquisition_is_stable_and_stops_at_reviewed_limit() -> None:
    attempts: list[tuple[str, str]] = []

    def try_lock(scope: str, key: str) -> bool:
        attempts.append((scope, key))
        return key.endswith(":1")

    acquired = slots.acquire_resource_slot("comments_pipeline", 2, try_lock=try_lock)
    assert acquired == "comments_pipeline:1"
    assert attempts == [
        (slots.RESOURCE_SLOT_SCOPE, "comments_pipeline:0"),
        (slots.RESOURCE_SLOT_SCOPE, "comments_pipeline:1"),
    ]


def test_claimed_job_requeues_before_handler_when_resource_is_full(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(worker, "RESOURCE_SLOT_LIMITS", {**worker.RESOURCE_SLOT_LIMITS, "comments_pipeline": 2})
    monkeypatch.setattr(
        worker,
        "_advisory_lock",
        lambda _conn, scope, _key: scope == "vkpi_apify_job_execution",
    )
    monkeypatch.setattr(worker, "_advisory_unlock", lambda *_args: None)
    monkeypatch.setattr(worker, "_process_job", lambda *_args: calls.append("processed"))
    monkeypatch.setattr(
        worker,
        "_requeue_job",
        lambda _conn, job_id, reason, **kwargs: calls.append((job_id, reason, kwargs)),
    )

    worker._process_claimed_job(
        object(), {"id": 17, "job_type": "kol_pool_comments_collect", "payload": {}}
    )

    assert calls[0][0] == 17
    assert "comments_pipeline concurrency limit reached" in calls[0][1]
    assert 5.0 <= calls[0][2]["retry_delay_seconds"] <= 10.0
    assert "processed" not in calls


def test_claimed_job_always_releases_acquired_slot(monkeypatch) -> None:
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(worker, "_advisory_lock", lambda *_args: True)
    monkeypatch.setattr(
        worker,
        "_advisory_unlock",
        lambda _conn, scope, key: released.append((scope, key)),
    )

    def fail_handler(_conn: object, _job: object) -> None:
        raise RuntimeError("handler failed")

    monkeypatch.setattr(worker, "_process_job", fail_handler)
    with pytest.raises(RuntimeError, match="handler failed"):
        worker._process_claimed_job(
            object(), {"id": 18, "job_type": "kol_profile_deep_crawl", "payload": {}}
        )
    assert released == [
        (slots.RESOURCE_SLOT_SCOPE, "profile_media:0"),
        ("vkpi_apify_job_execution", "18"),
    ]


def test_claimed_job_requeues_when_execution_lock_is_still_held(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(worker, "_advisory_lock", lambda *_args: False)
    monkeypatch.setattr(
        worker,
        "_requeue_job",
        lambda _conn, job_id, reason, **kwargs: calls.append((job_id, reason, kwargs)),
    )
    monkeypatch.setattr(worker, "_process_job", lambda *_args: calls.append("processed"))

    worker._process_claimed_job(object(), {"id": 19, "job_type": "session_advance", "payload": {}})

    assert calls[0][0] == 19
    assert "execution lease" in calls[0][1]
    assert "processed" not in calls
