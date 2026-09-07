from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
from typing import Any

import pytest

from app.domains.kol import profile_online_qualification as online
from app.domains.kol import targeted_query_execution as execution
from app.domains.kol.profile_candidate_observations import (
    MAX_CONTENT_OBSERVATIONS,
    OnlineObservationCache,
    candidate_observation_fingerprint,
    content_observations,
    merge_candidate_observations,
    qualification_cell_observations,
)


def _cell(name: str) -> dict[str, Any]:
    return {"query_cell_id": name, "primary_query": f"{name} photographer", "round": 1,
            "segment": name, "platforms": ["youtube"], "raw_limit": 10}


def _raw(cell: str, video: str, *, handle: str = "shared", ready: bool = True) -> dict[str, Any]:
    return {"platform": "youtube", "handle": handle,
            "profile_url": f"https://www.youtube.com/@{handle}",
            "sample_title": video, "published": "2026-09-01T00:00:00Z",
            "content_url": f"https://www.youtube.com/watch?v={video}",
            "matched_query_cells": [_cell(cell)], "fixture_ready": ready}


def test_exact_cells_preserve_distinct_video_observations_and_input_ownership() -> None:
    receipts = {name: _raw(name, f"video_{name}") for name in ("food", "motorsport")}
    before = deepcopy(receipts)

    async def discover(**kwargs: Any) -> dict[str, Any]:
        name = kwargs["query_text"].split()[0]
        return {"new_creators": [receipts[name]], "provider_calls": True, "platforms": ["youtube"]}

    result = asyncio.run(execution.execute_first_round_query_cells(
        query_cells=[_cell("food"), _cell("motorsport")],
        base_kwargs={"platforms": ["youtube"]}, discover=discover,
    ))
    [creator] = result["new_creators"]
    observed = creator["video_evidence"]
    assert {row["title"] for row in observed} == {"video_food", "video_motorsport"}
    assert {row["discovery_observations"][0]["query_cell_id"] for row in observed} == {"food", "motorsport"}
    assert {row["discovery_observations"][0]["executed_query"] for row in observed} == {"food photographer", "motorsport photographer"}
    assert all(row["platform"] == "youtube" for row in observed)
    observed[0]["title"] = "mutated output"
    assert receipts == before


def test_same_video_merges_lineage_without_counting_two_independent_videos() -> None:
    merged = merge_candidate_observations(_raw("food", "samevideo"), _raw("motorsport", "samevideo"))
    [record] = merged["video_evidence"]
    assert {cell["query_cell_id"] for cell in record["discovery_observations"]} == {"food", "motorsport"}
    assert len(content_observations(merged)) == 1


def test_observation_storage_is_bounded_and_retains_legacy_content_aliases() -> None:
    records = [{"title": f"video-{index}"} for index in range(100)]
    assert len(content_observations({"video_evidence": records})) == MAX_CONTENT_OBSERVATIONS
    [record] = content_observations({"sample_caption": "caption", "content_description": "description", "sample_transcript": "transcript"})
    assert record == {"caption": "caption", "description": "description", "transcript": "transcript", "platform": None}


def test_replay_fingerprint_ignores_fetch_time_but_detects_new_video_or_cell() -> None:
    raw = _raw("food", "firstvideo")
    fingerprint = candidate_observation_fingerprint(raw, creator_key="shared")
    assert candidate_observation_fingerprint({**raw, "fetched_at": "later"}, creator_key="shared") == fingerprint
    assert candidate_observation_fingerprint(_raw("food", "newvideo"), creator_key="shared") != fingerprint
    assert candidate_observation_fingerprint(_raw("motorsport", "firstvideo"), creator_key="shared") != fingerprint


def test_cross_round_native_identity_upgrade_keeps_prior_video_by_shared_alias() -> None:
    cache = OnlineObservationCache()
    cache.prepare(_raw("food", "firstvideo"), creator_key="handle:shared", aliases={"handle:shared"}, accepted_aliases=set())
    merged = cache.prepare(_raw("motorsport", "newvideo"), creator_key="native:UCshared",
                           aliases={"native:UCshared", "handle:shared"}, accepted_aliases=set())
    assert {row["title"] for row in merged["video_evidence"]} == {"firstvideo", "newvideo"}


@pytest.fixture
def qualification_spy(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, Any]]]:
    seen: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(online, "_identity_probe", lambda raw: raw)
    monkeypatch.setattr(online.profile_recall_qualification, "canonical_creator_key", lambda raw: str(raw.get("handle") or ""))
    monkeypatch.setattr(online.profile_recall_qualification, "canonical_creator_aliases", lambda raw: {str(raw.get("handle"))})

    def qualify(candidates: list[dict[str, Any]], **_kwargs: Any) -> dict[str, Any]:
        seen.append(deepcopy(candidates))
        outcomes = []
        for raw in candidates:
            ready = raw["fixture_ready"]
            item = {"handle": raw["handle"], "canonical_fingerprint": hashlib.sha256(raw["handle"].encode()).hexdigest(),
                    "qualification_evidence": {"passed": True},
                    "cell_qualification": [{"query_cell_id": cell["query_cell_id"], "passed": True}
                                           for cell in raw["matched_query_cells"]],
                    "matched_query_cells": deepcopy(raw["matched_query_cells"])}
            outcomes.append({"status": "selected" if ready else "pending", "item": item if ready else None,
                             "source": raw if ready else None})
        return {"outcomes": outcomes, "strict_contract": {}, "qualification_stats": {}}

    monkeypatch.setattr(online, "_qualify_online_candidates_internal", qualify)
    return seen


def _collect(rounds: list[list[dict[str, Any]]], *, observer: Any = None) -> tuple[dict[str, Any], list[int], list[str]]:
    calls: list[int] = []
    enrolled: list[str] = []

    async def fetch_batch(*, round_no: int, limit: int, **_kwargs: Any) -> dict[str, Any]:
        calls.append(limit)
        return {"new_creators": deepcopy(rounds[round_no - 1]), "provider_calls": True,
                "has_more": round_no < len(rounds), "next_cursor": {"round": round_no}}

    def enroll(raw: dict[str, Any]) -> dict[str, Any]:
        enrolled.append(raw["handle"])
        return {"kol_pool_id": len(enrolled)}

    result = asyncio.run(online.collect_strict_online_candidates(
        query_text="photographer", policy={}, local_canonical_keys=set(), fetch_batch=fetch_batch,
        enroll_candidate=enroll, candidate_budget=150, max_provider_rounds=3,
        round_observer=observer,
    ))
    return result, calls, enrolled


def test_same_creator_new_video_is_requalified_after_pending(qualification_spy: list) -> None:
    result, calls, enrolled = _collect([[_raw("food", "firstvideo", ready=False)], [_raw("food", "newvideo")]])
    assert len(calls) == len(qualification_spy) == 2
    assert enrolled == ["shared"]
    assert result["returned_count"] == 1
    assert {row["title"] for row in qualification_spy[1][0]["video_evidence"]} == {"firstvideo", "newvideo"}


def test_accepted_creator_new_cell_enriches_proof_without_second_enrollment(qualification_spy: list) -> None:
    result, calls, enrolled = _collect([[_raw("food", "firstvideo")], [_raw("motorsport", "newvideo")]])
    assert len(calls) == len(qualification_spy) == 2
    assert enrolled == ["shared"]
    assert result["returned_count"] == 1
    [item] = result["items"]
    assert {cell["query_cell_id"] for cell in item["cell_qualification"]} == {"food", "motorsport"}
    assert item["kol_pool_id"] == 1 and item["server_rank"] == 1


def test_exact_replay_stops_without_extra_round_or_budget_expansion(qualification_spy: list) -> None:
    raw = _raw("food", "firstvideo", ready=False)
    result, calls, enrolled = _collect([[raw], [{**raw, "fetched_at": "later"}], [_raw("food", "unusedvideo")]])
    assert calls == [150, 149]
    assert len(qualification_spy) == 1 and not enrolled
    assert result["candidate_budget_used"] == 2  # both fetched raw observations consume the existing cap
    assert result["duplicate_online_count"] == 1


def test_changed_observations_still_obey_three_round_150_candidate_cap(qualification_spy: list) -> None:
    rounds = [[_raw("food", f"v{round_no}-{index}", handle=f"creator-{index}", ready=False)
               for index in range(50)] for round_no in range(4)]
    result, calls, enrolled = _collect(rounds)
    assert calls == [150, 100, 50]
    assert result["candidate_budget_used"] == 150
    assert result["provider_rounds"] == 3
    assert not enrolled


def test_observer_failure_stops_next_dispatch_without_touching_accepted_card(qualification_spy: list) -> None:
    seen = []

    def observer(payload: dict[str, Any]) -> None:
        seen.append(deepcopy(payload))
        payload["accepted"][0]["handle"] = "cannot mutate accepted card"
        raise RuntimeError("fixture observer failure")

    result, calls, enrolled = _collect([[_raw("food", "firstvideo")], [_raw("food", "unusedvideo")]], observer=observer)
    assert len(calls) == len(seen) == 1
    assert enrolled == ["shared"] and result["items"][0]["handle"] == "shared"
    assert result["round_gate"]["stopped_by"] == "observer_failed"
    assert result["exhausted"] is False
    assert seen[0]["candidate_budget"] == 150 and seen[0]["budget_used"] == 1


def test_cell_observer_uses_actual_adjudication_not_matched_query_tags() -> None:
    adapted = [{"matched_query_cells": [_cell("food")]},
               {"cell_qualification": [{"query_cell_id": "food", "passed": True, "reasons": []},
                                        {"query_cell_id": "motorsport", "passed": False, "reasons": ["product_scene_evidence_missing"]}]}]
    outcomes = [{"canonical_key": "first", "status": "pending", "eight_gates_passed": False},
                {"canonical_key": "second", "status": "rejected", "eight_gates_passed": False}]
    projected = qualification_cell_observations(adapted, outcomes)
    assert projected[0]["cells"] == []
    assert projected[1]["cells"][0]["passed"] is True
    assert projected[1]["eight_gates_passed"] is False
    assert projected[1]["cells"][1]["reasons"] == ["product_scene_evidence_missing"]
