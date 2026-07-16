from __future__ import annotations

from pathlib import Path

from app.domains.tasks.queue_view import _multi_lane_eta_info


def _rows() -> list[dict[str, object]]:
    return [
        {"id": 1, "job_type": "crawl", "status": "running"},
        {"id": 2, "job_type": "crawl", "status": "queued"},
        {"id": 3, "job_type": "crawl", "status": "queued"},
        {"id": 4, "job_type": "crawl", "status": "queued"},
    ]


def test_single_lane_matches_the_previous_serial_shape() -> None:
    eta = _multi_lane_eta_info(
        _rows(), duration_for_job_type=lambda _job_type: 120.0, worker_count=1
    )
    assert [eta[index]["eta_seconds"] for index in (2, 3, 4)] == [60, 180, 300]
    assert [eta[index]["queue_position"] for index in (2, 3, 4)] == [1, 2, 3]
    assert {eta[index]["eta_worker_lanes"] for index in (2, 3, 4)} == {1}


def test_four_observed_workers_reduce_queue_start_without_reordering() -> None:
    eta = _multi_lane_eta_info(
        _rows(), duration_for_job_type=lambda _job_type: 120.0, worker_count=4
    )
    # One running task occupies one lane; the first three queued tasks can
    # start immediately on the other three lanes.
    assert [eta[index]["eta_seconds"] for index in (2, 3, 4)] == [0, 0, 0]
    assert [eta[index]["queue_position"] for index in (2, 3, 4)] == [1, 2, 3]
    assert {eta[index]["eta_worker_lanes"] for index in (2, 3, 4)} == {4}


def test_running_jobs_define_a_floor_when_heartbeat_count_lags() -> None:
    rows = [
        {"id": 1, "job_type": "crawl", "status": "running"},
        {"id": 2, "job_type": "crawl", "status": "running"},
        {"id": 3, "job_type": "crawl", "status": "queued"},
    ]
    eta = _multi_lane_eta_info(
        rows, duration_for_job_type=lambda _job_type: 100.0, worker_count=1
    )
    assert eta[3]["eta_worker_lanes"] == 2
    assert eta[3]["eta_seconds"] == 50


def test_queue_projection_uses_the_same_lane_priority_source_as_worker_claim() -> None:
    root = Path(__file__).resolve().parents[1]
    queue_source = (root / "backend/app/domains/tasks/queue_view.py").read_text(encoding="utf-8")
    worker_source = (root / "backend/app/workers/apify_jobs_worker.py").read_text(encoding="utf-8")
    for fragment in (
        "queue_priority_sql_expression",
        "QUEUE_PRIORITY_SQL",
        "queue_service_priority_sql_expression",
        "QUEUE_SERVICE_PRIORITY_SQL",
    ):
        assert fragment in queue_source
        assert fragment in worker_source
    assert "COALESCE(next_retry_at, created_at), created_at, id" in queue_source
    assert "COALESCE(next_retry_at, created_at), created_at, id" in worker_source
