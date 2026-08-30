"""Characterization lock for validate_human_labels (search_relevance_eval).

These tests freeze the observable contract before the CC-reduction knife:
- error codes byte-for-byte, including per-row issue ORDER;
- slot bookkeeping (an invalid row still claims its review slot);
- normalization output for valid rows (including unable_to_judge nulls);
- result envelope (issue_counts / unlabeled_template_count / manifest counts).
"""
from __future__ import annotations

from copy import deepcopy

from app.domains.kol.search_relevance_eval import (
    DEFAULT_QUERY_SUITE,
    HUMAN_LABEL_SOURCE,
    build_candidate_manifest,
    build_label_template,
    validate_human_labels,
)

BUILD_CONTEXT = {
    "code_version": "test-source-sha256:fixture-v1",
    "dataset_snapshot_id": "test-db-sha256:fixture-v1",
}


def _fake_search(**kwargs):
    query = kwargs["operator_query_text"]
    offset = next(
        index * 1000
        for index, spec in enumerate(DEFAULT_QUERY_SUITE, start=1)
        if spec.query_text == query
    )
    items = []
    for rank in range(1, 31):
        tier = "strict" if rank <= 15 else "relaxed"
        items.append(
            {
                "kol_pool_id": offset + rank,
                "platform": "youtube" if "YouTube" in query else "instagram",
                "handle": f"candidate-{offset + rank}",
                "display_name": f"Candidate {offset + rank}",
                "profile_url": f"https://example.test/{offset + rank}",
                "followers": 10_000 + rank,
                "match_tier": tier,
                "candidate_bucket": "core_vertical" if rank <= 18 else "expansion",
                "bucket": "reviewer" if rank % 2 else "creator",
                "retrieval_method": "lexical_idf_v1",
                "robust_rank_score": 1 - rank / 100,
                "retrieval_score": 1 - rank / 100,
                "ranking_confidence": {"score": 0.7, "level": "medium"},
                "evidence_quality": {
                    "video_evidence_count": 2,
                    "deep_analysis_count": 1,
                },
                "why_fit": "test evidence",
                "source_fields": {
                    "retrieval_meta": {
                        "matched_terms": ["camera"],
                        "factual_matched_terms": ["camera"],
                    }
                },
            }
        )
    return {
        "items": items,
        "ranking": {"robust_rank_method": "kol_robust_rank_v1"},
        "filters": {"hard_filters_relaxed": False},
        "diagnostics": {
            "final_count": 30,
            "strict_count": 15,
            "relaxed_count": 15,
            "backfill_count": 0,
            "result_contract_satisfied": True,
            "provider_free_initial": kwargs["provider_free"],
            "lane_selection": {"lane_contract_satisfied": True},
        },
    }


_MANIFEST = build_candidate_manifest(_fake_search, **BUILD_CONTEXT)
_TEMPLATE = build_label_template(_MANIFEST)


def _reviewed(template_row, **overrides):
    filled = deepcopy(template_row)
    filled.update(
        {
            "label_status": "reviewed",
            "label_source": HUMAN_LABEL_SOURCE,
            "labeler": (
                "human:reviewer-01"
                if template_row["review_slot"] == "A"
                else "human:reviewer-02"
            ),
            "reviewed_at": "2026-08-03T20:00:00Z",
            "unable_to_judge": False,
            "relevance": 2,
            "vertical_fit": True,
            "evidence_sufficient": True,
            "notes": "human review",
        }
    )
    filled.update(overrides)
    return filled


def _codes(report):
    return [issue["code"] for issue in report["issues"]]


def test_valid_rows_normalize_exactly_and_produce_no_issues():
    rows = [_reviewed(_TEMPLATE[0]), _reviewed(_TEMPLATE[1])]
    report = validate_human_labels(rows, manifest=_MANIFEST)
    assert report["issues"] == []
    assert report["issue_counts"] == {}
    assert report["unlabeled_template_count"] == 0
    assert report["manifest_candidate_count"] == 180
    first = _TEMPLATE[0]
    assert report["valid_labels"][0] == {
        "query_id": first["query"]["id"],
        "candidate_id": first["candidate"]["id"],
        "rank": first["candidate"]["rank"],
        "match_tier": first["candidate"]["match_tier"],
        "labeler": "human:reviewer-01",
        "reviewed_at": "2026-08-03T20:00:00Z",
        "review_role": "independent",
        "review_slot": "A",
        "unable_to_judge": False,
        "relevance": 2,
        "vertical_fit": True,
        "evidence_sufficient": True,
        "notes": "human review",
    }
    assert report["valid_labels"][1]["review_slot"] == "B"
    assert report["valid_labels"][1]["labeler"] == "human:reviewer-02"


def test_unable_to_judge_row_normalizes_null_judgments():
    row = _reviewed(
        _TEMPLATE[0],
        unable_to_judge=True,
        relevance=None,
        vertical_fit=None,
        evidence_sufficient=None,
        notes="cannot verify from evidence",
    )
    report = validate_human_labels([row], manifest=_MANIFEST)
    assert report["issues"] == []
    normalized = report["valid_labels"][0]
    assert normalized["unable_to_judge"] is True
    assert normalized["relevance"] is None
    assert normalized["vertical_fit"] is None
    assert normalized["evidence_sufficient"] is None
    assert normalized["notes"] == "cannot verify from evidence"


def test_kitchen_sink_row_reports_every_issue_in_frozen_order():
    row = {
        "schema_version": "bogus",
        "label_status": "draft",
        "label_source": "llm",
        "labeler": "",
        "reviewed_at": "not-a-timestamp",
        "review_role": "robot",
        "review_slot": "Z",
        "query": {"suite_version": "wrong_suite", "id": "", "text": "mismatch"},
        "candidate": {"id": "", "rank": 1, "match_tier": "strict"},
        "unable_to_judge": "yes",
        "relevance": None,
        "vertical_fit": "x",
        "evidence_sufficient": None,
        "notes": 123,
    }
    report = validate_human_labels([row], manifest=_MANIFEST)
    assert report["issues"] == [
        {"code": "invalid_label_schema_version", "row": 1},
        {"code": "label_status_not_reviewed", "row": 1},
        {"code": "label_source_not_human_review", "row": 1},
        {"code": "missing_labeler", "row": 1},
        {"code": "reviewed_at_must_be_timezone_iso8601", "row": 1},
        {"code": "missing_query_id", "row": 1},
        {"code": "missing_candidate_id", "row": 1},
        {"code": "invalid_review_role", "row": 1},
        {"code": "candidate_not_in_manifest", "row": 1},
        {"code": "query_suite_version_mismatch", "row": 1},
        {"code": "query_text_mismatch", "row": 1},
        {"code": "unable_to_judge_must_be_boolean", "row": 1},
        {"code": "relevance_must_be_integer_0_to_3", "row": 1},
        {"code": "vertical_fit_must_be_boolean", "row": 1},
        {"code": "evidence_sufficient_must_be_boolean", "row": 1},
        {"code": "notes_must_be_string", "row": 1},
    ]
    assert report["valid_labels"] == []


def test_labeler_and_slot_and_timezone_variants():
    rows = [
        _reviewed(_TEMPLATE[0], labeler="reviewer-01"),
        _reviewed(_TEMPLATE[1], labeler="human:gpt-reviewer"),
        _reviewed(_TEMPLATE[2], reviewed_at="2026-08-03T20:00:00"),
        _reviewed(_TEMPLATE[3], review_slot="C"),
        _reviewed(_TEMPLATE[4], review_role="adjudication"),
        _reviewed(
            _TEMPLATE[5],
            review_role="adjudication",
            review_slot="adjudication",
            labeler="human:adjudicator-09",
        ),
    ]
    report = validate_human_labels(rows, manifest=_MANIFEST)
    assert report["issues"] == [
        {"code": "labeler_must_use_human_reviewer_id", "row": 1},
        {"code": "non_human_labeler_forbidden", "row": 2},
        {"code": "reviewed_at_must_be_timezone_iso8601", "row": 3},
        {"code": "independent_review_slot_must_be_a_or_b", "row": 4},
        {"code": "adjudication_review_slot_invalid", "row": 5},
    ]
    assert [item["review_role"] for item in report["valid_labels"]] == ["adjudication"]
    assert report["valid_labels"][0]["review_slot"] == "adjudication"


def test_invalid_row_still_claims_its_review_slot():
    rows = [
        _reviewed(_TEMPLATE[0], labeler=""),
        _reviewed(_TEMPLATE[0]),
        _reviewed(_TEMPLATE[0]),
    ]
    report = validate_human_labels(rows, manifest=_MANIFEST)
    assert report["issues"] == [
        {"code": "missing_labeler", "row": 1},
        {"code": "duplicate_candidate_review_slot", "row": 2},
        {"code": "duplicate_candidate_review_slot", "row": 3},
    ]
    assert report["valid_labels"] == []
    assert report["issue_counts"] == {
        "duplicate_candidate_review_slot": 2,
        "missing_labeler": 1,
    }


def test_manifest_tie_mismatches_are_reported_per_field_in_order():
    tampered_rank = _reviewed(_TEMPLATE[0])
    tampered_rank["candidate"]["rank"] = tampered_rank["candidate"]["rank"] + 7
    tampered_tier = _reviewed(_TEMPLATE[1])
    tampered_tier["candidate"]["match_tier"] = "bogus"
    tampered_fp = _reviewed(_TEMPLATE[2])
    tampered_fp["candidate"]["manifest_fingerprint"] = "deadbeef"
    everything = _reviewed(_TEMPLATE[3])
    everything["candidate"]["rank"] = "not-a-number"
    everything["candidate"]["match_tier"] = "bogus"
    everything["candidate"]["manifest_fingerprint"] = "deadbeef"
    report = validate_human_labels(
        [tampered_rank, tampered_tier, tampered_fp, everything],
        manifest=_MANIFEST,
    )
    assert report["issues"] == [
        {"code": "candidate_rank_mismatch", "row": 1},
        {"code": "candidate_match_tier_mismatch", "row": 2},
        {"code": "manifest_fingerprint_mismatch", "row": 3},
        {"code": "candidate_rank_mismatch", "row": 4},
        {"code": "candidate_match_tier_mismatch", "row": 4},
        {"code": "manifest_fingerprint_mismatch", "row": 4},
    ]


def test_judgment_and_notes_rules_keep_exact_codes():
    unable_with_values = _reviewed(
        _TEMPLATE[0],
        unable_to_judge=True,
        relevance=2,
        vertical_fit=None,
        evidence_sufficient=None,
        notes="",
    )
    boolean_relevance = _reviewed(_TEMPLATE[1], relevance=True)
    out_of_range = _reviewed(_TEMPLATE[2], relevance=4)
    long_notes = _reviewed(_TEMPLATE[3], notes="x" * 4001)
    report = validate_human_labels(
        [unable_with_values, boolean_relevance, out_of_range, long_notes],
        manifest=_MANIFEST,
    )
    assert report["issues"] == [
        {"code": "unable_to_judge_requires_null_judgments", "row": 1},
        {"code": "notes_required_when_unable_to_judge", "row": 1},
        {"code": "relevance_must_be_integer_0_to_3", "row": 2},
        {"code": "relevance_must_be_integer_0_to_3", "row": 3},
        {"code": "notes_too_long", "row": 4},
    ]


def test_non_object_rows_and_templates_are_counted_not_normalized():
    rows = ["not-an-object", _TEMPLATE[0], _TEMPLATE[1], _reviewed(_TEMPLATE[2])]
    report = validate_human_labels(rows, manifest=_MANIFEST)
    assert report["issues"] == [{"code": "label_row_not_object", "row": 1}]
    assert report["unlabeled_template_count"] == 2
    assert len(report["valid_labels"]) == 1
    assert report["issue_counts"] == {"label_row_not_object": 1}


def test_manifest_level_issues_precede_label_row_issues():
    tampered = deepcopy(_MANIFEST)
    tampered["manifest_fingerprint"] = "0" * 64
    report = validate_human_labels(
        [_reviewed(_TEMPLATE[0], labeler="")],
        manifest=tampered,
    )
    assert _codes(report) == [
        "manifest_fingerprint_mismatch",
        "missing_labeler",
        "manifest_fingerprint_mismatch",
    ]
    assert "row" not in report["issues"][0]
    assert report["issues"][1]["row"] == 1
