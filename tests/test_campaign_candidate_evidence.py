"""Offline provenance and unique sample coverage; no DB or provider access."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.marketing_brain.skills import campaign_plan

_INPUT = {"product": "Example lens family", "market": "US", "budget_cents": 100_000, "goal": "launch"}


def _wire(monkeypatch, rows):
    from app.domains.marketing_brain import skill_registry

    monkeypatch.setattr(campaign_plan, "_candidate_pool", lambda *_: {"status": "ready" if rows else "empty", "items": deepcopy(rows)})
    monkeypatch.setattr(campaign_plan, "_market_signals", lambda: {"status": "ok", "coverage": "1/5", "sections": {
        "competitor_moves": {"items": [{"brand": "Example", "signal_type": "launch"}]},
    }})
    receipts = []
    monkeypatch.setattr(skill_registry, "record_skill_run", lambda **kwargs: receipts.append(deepcopy(kwargs)))
    return receipts


def _rows(count):
    return [{"id": index + 1, "handle": f"creator-{index + 1}", "platform": "youtube"} for index in range(count)]


def _samples(result):
    return [sample for group in result["plan"]["creator_mix"] for sample in group["sample_creators"]]


def _assert_contract(sample):
    evidence = sample["evidence"]
    reference = f"kol_pool:{sample['id']}"
    assert type(sample["id"]) is int and sample["id"] > 0
    assert set(evidence) == {"schema_version", "status", "claim_status", "match_status", "facts", "source_refs", "observed_at", "gaps"}
    assert evidence["schema_version"] == "campaign_candidate_evidence.v1"
    assert evidence["status"] in {"partial", "unknown"}
    assert evidence["claim_status"] == "descriptive_only"
    assert evidence["match_status"] == "unverified"
    assert evidence["observed_at"] is None
    assert evidence["source_refs"][0]["ref"] == reference
    assert evidence["source_refs"][0]["kind"] == "kol_pool"
    assert evidence["source_refs"][0]["record_id"] == sample["id"]
    assert all(fact["source_ref"] == reference for fact in evidence["facts"])
    assert {"match_reason_unverified", "collection_time_unknown"} <= {gap["code"] for gap in evidence["gaps"]}
    return evidence


@pytest.mark.parametrize("goal", ["awareness", "launch", "conversion"])
@pytest.mark.parametrize("size", [0, 1, 2, 3, 4, 8, 12])
def test_rule_samples_are_globally_unique_stable_and_honest_about_shortfall(monkeypatch, size, goal):
    rows = _rows(size)
    _wire(monkeypatch, rows)
    result = campaign_plan.run({**_INPUT, "goal": goal}, record=False)
    assert result == campaign_plan.run({**_INPUT, "goal": goal}, record=False)
    samples = _samples(result)
    ids = [row["id"] for row in samples]
    capacity = sum(min(group["count"], 3) for group in result["plan"]["creator_mix"])
    assert ids == list(range(1, min(size, capacity) + 1))
    assert len(ids) == len(set(ids))
    for sample in samples:
        _assert_contract(sample)
    for group in result["plan"]["creator_mix"]:
        coverage = group["candidate_coverage"]
        shown = len(group["sample_creators"])
        assert coverage["planned_count"] == group["count"]
        assert coverage["unique_sample_count"] == shown
        assert bool(coverage["gaps"]) is (shown < group["count"])
        if shown < group["count"]:
            assert coverage["status"] == ("empty" if shown == 0 else "insufficient")
            assert coverage["gaps"][0]["code"] == "candidate_samples_insufficient"
        else:
            assert coverage["status"] == "count_met"
    assert result["planning_readiness"]["executable"] is False
    assert result["planning_readiness"]["approval_status"] == "not_requested"


def test_recorded_fields_have_local_provenance_not_personal_match_claims(monkeypatch):
    row = {**_rows(1)[0], "bio": "Night street photographs", "country": "US", "language": "English",
           "primary_topic": "Photography", "content_style": "Tutorial", "profile_url": "https://example.com/creator-1",
           "updated_at": "2026-09-04T10:00:00Z", "last_seen_at": "2026-09-05T10:00:00Z",
           "viltrox_fit_score": 99, "viltrox_fit_reason": "FIT_REASON_MUST_NOT_BECOME_EVIDENCE",
           "email": "private@example.com", "raw_platform_data": {"token": "PRIVATE_BLOB"}}
    receipts = _wire(monkeypatch, [row])
    result = campaign_plan.run(deepcopy(_INPUT))
    sample = _samples(result)[0]
    evidence = _assert_contract(sample)
    assert sample["fit"] == 99  # Existing read-only display retained, not placed in evidence.
    assert evidence["status"] == "partial"
    assert {fact["field"]: fact["value"] for fact in evidence["facts"]} == {
        key: row[key] for key in ("bio", "country", "language", "primary_topic", "content_style")
    }
    assert evidence["source_refs"] == [{"ref": "kol_pool:1", "kind": "kol_pool", "record_id": 1,
                                        "url": row["profile_url"], "record_updated_at": row["updated_at"]}]
    serialized = json.dumps(evidence)
    assert "FIT_REASON_MUST_NOT_BECOME_EVIDENCE" not in serialized
    assert "PRIVATE_BLOB" not in serialized and "private@example.com" not in serialized
    assert row["last_seen_at"] not in serialized
    assert receipts[0]["output"] == result


def test_no_profile_material_is_unknown_even_with_high_fit_or_old_reason(monkeypatch):
    _wire(monkeypatch, [{**_rows(1)[0], "viltrox_fit_score": 100, "viltrox_fit_reason": "Perfect match",
                         "last_seen_at": "2026-09-05T10:00:00Z"}])
    evidence = _assert_contract(_samples(campaign_plan.run(deepcopy(_INPUT), record=False))[0])
    assert evidence["status"] == "unknown"
    assert evidence["facts"] == []
    assert evidence["source_refs"][0]["url"] is None
    assert evidence["source_refs"][0]["record_updated_at"] is None
    assert {gap["code"] for gap in evidence["gaps"]} == {
        "match_reason_unverified", "collection_time_unknown", "profile_facts_missing",
        "source_url_missing", "record_updated_at_unknown",
    }


def _model(mix):
    return {"_model": "offline-model", "creator_mix": mix,
            "budget_allocation": [{"bucket": "creator_fees", "amount_cents": 100_000}],
            "timeline": [{"phase": "seed", "week": 1, "focus": "Review"}],
            "content_angles": [{"angle": "Example", "why": "Review", "market_signal": "Example"}]}


def test_model_cross_tier_duplicates_removed_with_shortfall_and_forged_evidence_discarded(monkeypatch):
    _wire(monkeypatch, [{**_rows(1)[0], "bio": "Actual stored biography"}, _rows(2)[1]])
    forged = {"id": 1, "reason": "MODEL_REASON", "citations": ["https://example.com/model-only"],
              "evidence": {"status": "verified", "match_status": "matched", "facts": [{"value": "MODEL_FACT"}]}}
    model = _model([
        {"tier": "micro", "share": 0.5, "count": 2, "sample_creators": [forged, {"id": 2}]},
        {"tier": "nano", "share": 0.5, "count": 2, "sample_creators": [forged, {"id": 2}]},
    ])
    result = campaign_plan.run(deepcopy(_INPUT), model_fn=lambda _: model, record=False)
    assert [row["id"] for row in _samples(result)] == [1, 2]
    first, second = result["plan"]["creator_mix"]
    assert first["candidate_coverage"]["status"] == "count_met"
    assert second["candidate_coverage"]["status"] == "empty"
    assert second["candidate_coverage"]["unique_sample_count"] == 0
    assert second["candidate_coverage"]["gaps"][0]["code"] == "candidate_samples_insufficient"
    serialized = json.dumps(result["plan"]["creator_mix"])
    assert "MODEL_REASON" not in serialized and "MODEL_FACT" not in serialized and "model-only" not in serialized
    for sample in _samples(result):
        _assert_contract(sample)


@pytest.mark.parametrize("use_model", [False, True])
def test_duplicate_pool_id_uses_first_record_stably_without_alias_merging(monkeypatch, use_model):
    rows = [{"id": "7", "handle": "same-handle", "platform": "youtube", "bio": "FIRST"},
            {"id": 7, "handle": "later", "platform": "x", "bio": "SECOND"},
            {"id": 8, "handle": "same-handle", "platform": "youtube", "bio": "OTHER_RECORD"}]
    _wire(monkeypatch, rows)
    model = _model([{"tier": "micro", "share": 1, "count": 2, "sample_creators": [{"id": 7}, {"id": 8}]}])
    result = campaign_plan.run(deepcopy(_INPUT), model_fn=(lambda _: model) if use_model else None, record=False)
    samples = _samples(result)
    assert [row["id"] for row in samples] == [7, 8]
    assert samples[0]["handle"] == "same-handle" and samples[0]["platform"] == "youtube"
    assert samples[0]["evidence"]["facts"][0]["value"] == "FIRST"
    assert "SECOND" not in json.dumps(samples)


@pytest.mark.parametrize("url", [None, "", "relative/profile", "ftp://example.com/creator",
    "https://user:password@example.com/creator", "https://user@example.com/creator",
    "https://example.com/creator?token=private", "https://example.com/creator#private",
    "https://example.com:invalid/creator", "https://example.com:99999/creator", "https://example.com/ bad"])
def test_invalid_or_sensitive_source_urls_are_missing_not_rewritten(monkeypatch, url):
    _wire(monkeypatch, [{**_rows(1)[0], "profile_url": url}])
    evidence = _assert_contract(_samples(campaign_plan.run(deepcopy(_INPUT), record=False))[0])
    assert evidence["source_refs"][0]["url"] is None
    assert "source_url_missing" in {gap["code"] for gap in evidence["gaps"]}


@pytest.mark.parametrize("updated_at", [None, "", "recent", "2026-02-31", 123, {"date": "2026-09-05"}])
def test_missing_or_malformed_record_date_never_becomes_collection_time(monkeypatch, updated_at):
    _wire(monkeypatch, [{**_rows(1)[0], "updated_at": updated_at}])
    evidence = _assert_contract(_samples(campaign_plan.run(deepcopy(_INPUT), record=False))[0])
    assert evidence["source_refs"][0]["record_updated_at"] is None
    assert evidence["observed_at"] is None


@pytest.mark.parametrize("updated_at", ["2026-09-04", "2026-09-04 10:00:00", "2026-09-04T10:00:00+02:00"])
def test_record_date_preserves_source_precision_and_timezone(monkeypatch, updated_at):
    _wire(monkeypatch, [{**_rows(1)[0], "updated_at": updated_at}])
    evidence = _assert_contract(_samples(campaign_plan.run(deepcopy(_INPUT), record=False))[0])
    assert evidence["source_refs"][0]["record_updated_at"] == updated_at
    assert evidence["observed_at"] is None
