from __future__ import annotations

from typing import Any

import pytest

from app.domains.analysis.cache_repo import (
    _project_video_item_state,
    _video_platform,
    list_project_video_analysis_cache,
)


@pytest.mark.parametrize(
    ("derive_method", "platform", "entry", "job", "expected"),
    [
        ("video_analysis_final_v1", "youtube", {"status": "ready"}, None, "ready"),
        ("video_analysis_final_v1", "youtube", None, {"status": "queued"}, "queued"),
        ("video_analysis_final_v1", "youtube", None, {"status": "running"}, "running"),
        ("video_analysis_final_v1", "youtube", None, None, "not_requested"),
        ("video_analysis_final_v1_keyframe_qa", "instagram", None, {"status": "queued"}, "unsupported"),
        ("video_analysis_final_v1", "youtube", None, {"status": "failed", "last_error": "provider failed"}, "failed"),
        ("video_analysis_final_v1", "youtube", None, {"status": "done"}, "failed"),
    ],
)
def test_project_video_item_state_is_job_aware(
    derive_method: str,
    platform: str,
    entry: dict[str, Any] | None,
    job: dict[str, Any] | None,
    expected: str,
) -> None:
    state, _reason = _project_video_item_state(
        derive_method=derive_method,
        platform=platform,
        entry=entry,
        job=job,
    )
    assert state == expected


def test_video_platform_uses_url_host_before_stored_platform() -> None:
    assert _video_platform("youtube", "https://example.com/not-a-youtube-video") == "unsupported"
    assert _video_platform("youtube", "") == "youtube"


def _row(evidence_id: int, *, cache_ready: bool = False, job_status: str = "", platform: str = "youtube") -> dict[str, Any]:
    return {
        "assignment_id": evidence_id,
        "kol_pool_id": 100 + evidence_id,
        "kol_name": f"creator-{evidence_id}",
        "handle": f"creator-{evidence_id}",
        "platform": platform,
        "evidence_id": evidence_id,
        "content_url": (
            f"https://www.youtube.com/watch?v={evidence_id}"
            if platform == "youtube"
            else f"https://www.instagram.com/reel/{evidence_id}/"
        ),
        "title": f"video-{evidence_id}",
        "thumbnail_url": None,
        "view_count": 10,
        "like_count": 1,
        "comment_count": 0,
        "publish_date": None,
        "target_type": "video" if cache_ready else None,
        "target_id": str(evidence_id) if cache_ready else None,
        "derive_method": "video_analysis_final_v1" if cache_ready else None,
        "model": "model" if cache_ready else None,
        "cost": 0 if cache_ready else None,
        "status": "ready" if cache_ready else None,
        "triggered_by_user_id": None,
        "result": {} if cache_ready else None,
        "created_at": None,
        "updated_at": None,
        "job_id": evidence_id * 10 if job_status else None,
        "job_status": job_status or None,
        "job_last_error": "failed" if job_status == "failed" else None,
        "job_created_at": None,
        "job_started_at": None,
        "job_updated_at": None,
    }


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.params: tuple[Any, ...] | None = None

    def execute(self, _sql: str, params: tuple[Any, ...]) -> _Rows:
        self.params = params
        return _Rows(self.rows)


def test_project_video_cache_summary_counts_only_real_active_jobs_as_pending() -> None:
    conn = _Connection(
        [
            _row(1, cache_ready=True),
            _row(2, job_status="queued"),
            _row(3, job_status="running"),
            _row(4),
            _row(5, job_status="failed"),
        ]
    )

    result = list_project_video_analysis_cache(7, conn=conn)

    assert conn.params == ("video_analysis_final_v1", "video_analysis_final_v1", 7)
    assert [item["state"] for item in result["items"]] == [
        "ready",
        "queued",
        "running",
        "not_requested",
        "failed",
    ]
    assert result["summary"]["ready_count"] == 1
    assert result["summary"]["active_count"] == 2
    assert result["summary"]["pending_count"] == 2
    assert result["summary"]["not_requested_count"] == 1
    assert result["summary"]["failed_count"] == 1


def test_project_video_qa_summary_marks_non_youtube_as_unsupported_not_pending() -> None:
    conn = _Connection([_row(6, job_status="queued", platform="instagram")])

    result = list_project_video_analysis_cache(
        7,
        derive_method="video_analysis_final_v1_keyframe_qa",
        conn=conn,
    )

    assert result["items"][0]["state"] == "unsupported"
    assert result["items"][0]["active_job"] is None
    assert result["summary"]["pending_count"] == 0
    assert result["summary"]["unsupported_count"] == 1
