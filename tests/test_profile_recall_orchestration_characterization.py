"""Characterization pins for the phase-oriented profile recall facade."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from app.domains.kol import profile_recall


def test_facade_preserves_inputs_and_operator_facets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filters = {
        "platforms": ["YouTube"],
        "countries": ["US"],
        "languages": ["EN"],
        "followers_min": 5_000,
        "followers_max": 500_000,
    }
    bucket_policy = {"core_vertical": 2, "expansion": 1, "exploration": 1}
    local_policy = {"market": "US", "platforms": ["youtube"]}
    before_filters = deepcopy(filters)
    before_bucket_policy = deepcopy(bucket_policy)
    before_local_policy = deepcopy(local_policy)
    captured: dict[str, object] = {}

    def _capture(request, *, deps):
        captured["request"] = request
        captured["deps"] = deps
        return {"sentinel": "prepared"}

    monkeypatch.setattr(profile_recall, "run_recall_pipeline", _capture)
    result = profile_recall.recall_kol_profiles(
        query_text="55mm portrait creator",
        candidate_limit=12,
        limit=4,
        filters=filters,
        bucket_policy=bucket_policy,
        local_qualification_policy=local_policy,
        server_candidate_limit_override=40,
    )

    assert result == {"sentinel": "prepared"}
    assert filters == before_filters
    assert bucket_policy == before_bucket_policy
    assert local_policy == before_local_policy
    request = captured["request"]
    assert captured["deps"] is profile_recall
    assert request.safe_candidate_limit == 40
    assert request.server_candidate_limit_override_applied is True
    assert request.normalized_filters == {
        "platforms": ["YouTube"],
        "countries": ["US"],
        "languages": ["EN"],
        "followers_min": 5_000,
        "followers_max": 500_000,
    }
    assert request.retrieval_filters["_country_values"] == ["us"]
    assert request.retrieval_filters["_language_values"] == ["en"]
    with pytest.raises(FrozenInstanceError):
        request.safe_limit = 99


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"ratio_policy": "hard"}, "only ratio_policy=soft is supported"),
        ({"mixed_policy": "split"}, "only mixed_policy=dominant is supported"),
        (
            {"creator_quota": 0, "reviewer_quota": 0},
            "creator_quota + reviewer_quota must be greater than 0",
        ),
        (
            {"vector_weight": 0, "type_weight": 0},
            "vector_weight + type_weight must be greater than 0",
        ),
    ],
)
def test_facade_validation_errors_precede_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
    message: str,
) -> None:
    monkeypatch.setattr(
        profile_recall,
        "run_recall_pipeline",
        lambda *_args, **_kwargs: pytest.fail("invalid input reached pipeline"),
    )
    with pytest.raises(ValueError, match=message.replace("+", r"\+")):
        profile_recall.recall_kol_profiles(**kwargs)
