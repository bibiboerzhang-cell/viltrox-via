from __future__ import annotations

import pytest

from app.domains.kol.video_analysis_enqueue import _video_analysis_queue_lane


@pytest.mark.parametrize(
    ("batch", "expected"),
    [
        ("", "interactive"),
        ("on_demand", "interactive"),
        ("on_demand_batch", "batch"),
        ("recent", "batch"),
        ("remaining", "batch"),
        ("url_profile_representative", "batch"),
    ],
)
def test_video_analysis_lane_distinguishes_single_request_from_batch_wrappers(
    batch: str,
    expected: str,
) -> None:
    assert _video_analysis_queue_lane(batch=batch, local_evaluation=False) == expected


@pytest.mark.parametrize("batch", ["on_demand", "on_demand_batch", "url_existing_creator"])
def test_explicit_local_evaluation_always_uses_reserved_interactive_lane(batch: str) -> None:
    assert _video_analysis_queue_lane(batch=batch, local_evaluation=True) == "interactive"
