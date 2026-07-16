from __future__ import annotations

import pytest

from app.domains.analysis import cache_repo
from app.domains.kol import audience_language, eleven_dimensions, llm_deep_analysis
from app.domains.kol import pool as kol_pool


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (24, 24),
        (200, 200),
        (999, 200),
    ],
)
def test_detail_bundle_honors_route_video_limit_contract(
    monkeypatch,
    requested: int,
    expected: int,
):
    requested_limits: list[int] = []

    def fake_video_evidence(_kol_pool_id: int, *, limit: int, **kwargs):
        if kwargs.get("only_with_cache"):
            return []
        requested_limits.append(limit)
        return [
            {
                "id": index + 1,
                "content_url": f"https://example.com/video/{index + 1}",
            }
            for index in range(limit)
        ]

    monkeypatch.setattr(kol_pool, "get_item", lambda _kol_pool_id: {"item": {"id": _kol_pool_id}})
    monkeypatch.setattr(kol_pool, "_video_evidence_for_kol", fake_video_evidence)
    monkeypatch.setattr(eleven_dimensions, "load_persisted_dimensions_11", lambda _kol_pool_id: None)
    monkeypatch.setattr(
        llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda _kol_pool_id, *, limit: {"status": "empty", "count": 0, "limit": limit},
    )
    monkeypatch.setattr(cache_repo, "get_analysis_cache_entries_for_targets", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        audience_language,
        "audience_language_for_kol",
        lambda _kol_pool_id: {"sample_size": 0, "languages": []},
    )

    result = kol_pool.detail_bundle(13053, video_limit=requested, llm_limit=20)

    assert requested_limits == [expected]
    assert len(result["item"]["video_evidence"]) == expected


def test_detail_bundle_batches_analysis_cache_reads(monkeypatch):
    calls: list[tuple[list[str], tuple[str, ...]]] = []

    def fake_video_evidence(_kol_pool_id: int, *, limit: int, **kwargs):
        if kwargs.get("only_with_cache"):
            return [{"id": index} for index in range(1, 51)]
        return []

    def fake_batch(_target_type, target_ids, *, derive_methods, conn=None):
        del conn
        calls.append((list(target_ids), tuple(derive_methods)))
        return {
            (str(evidence_id), "video_analysis_final_v1"): {
                "status": "ready",
                "result": {},
            }
            for evidence_id in target_ids
        }

    monkeypatch.setattr(kol_pool, "get_item", lambda _kol_pool_id: {"item": {"id": _kol_pool_id}})
    monkeypatch.setattr(kol_pool, "_video_evidence_for_kol", fake_video_evidence)
    monkeypatch.setattr(eleven_dimensions, "load_persisted_dimensions_11", lambda _kol_pool_id: None)
    monkeypatch.setattr(
        llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda _kol_pool_id, *, limit: {"status": "empty", "count": 0, "limit": limit},
    )
    monkeypatch.setattr(cache_repo, "get_analysis_cache_entries_for_targets", fake_batch)
    monkeypatch.setattr(
        audience_language,
        "audience_language_for_kol",
        lambda _kol_pool_id: {"sample_size": 0, "languages": []},
    )

    result = kol_pool.detail_bundle(13053, video_limit=24, llm_limit=20)

    assert len(calls) == 1
    assert calls[0][0] == [str(index) for index in range(1, 51)]
    assert calls[0][1] == (
        "video_analysis_final_v1",
        "video_analysis_final_v1_keyframe_qa",
    )
    assert result["video_analysis"]["summary"]["ready_count"] == 50
