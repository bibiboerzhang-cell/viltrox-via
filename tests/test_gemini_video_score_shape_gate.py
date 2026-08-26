"""The final_v1 quality gate must not be stricter than its own consumers.

Two boundary bugs shipped together and hid every fresh deep analysis from the
user: the gate demanded ``{"score": N}`` while the current model family emits
bare integers, and it read ``inspection_complete`` with ``is not True`` while
both the provider and the DB compat adapter hand back non-``bool`` truth.

A later pass closed the two remaining seams between the gate and the projection
that reads the same value: a numeric string was readable everywhere *except* the
gate (so one such field degraded a whole paid analysis), and ``NaN`` cleared the
gate and then clamped to a perfect 100 on the way out.

All four are read-side defects.  Nothing here relaxes a quality standard: a
score key the provider never returned is still a gap, ``bool`` is still not a
number, free text is still not a number, and a falsy inspection flag is still
rejected.
"""
from __future__ import annotations

import json
import math

import pytest

from app.domains.kol.final_v1_extract import _score_from_value
from app.services.ai.analyzers.gemini_video_results import (
    FINAL_V1_LAYER6_SCORE_KEYS,
    FINAL_V1_QUALITY_COMPLETE,
    FINAL_V1_QUALITY_INCOMPLETE,
    _apply_final_v1_result,
    _normalise_brand_product_evidence,
    _score_value,
    ensure_final_v1_result_cacheable,
    final_v1_quality_issues,
)
from app.services.ai.analyzers.gemini_video_scores import _final_v1_score_returned

SCORE_ISSUE_PREFIX = "layer6_flags_and_scores.scores."
INSPECTION_ISSUE = "brand_product_evidence.inspection_complete"


def _payload(*, scores: dict, inspection_complete=True) -> dict:
    """A payload that is quality-complete except for what a test varies."""
    return {
        "layer1_visual_content": {
            "content_summary": "A creator demonstrates autofocus and flare behaviour.",
            "scene_timeline": [{"timestamp": "00:08", "what": "Lens demonstration."}],
            "brand_product_evidence": {
                "viltrox_status": "unknown",
                "inspection_complete": inspection_complete,
                "checked_modalities": ["visual", "audio"],
                "viltrox_evidence": [],
                "viltrox_products": [],
                "competitors": [],
            },
            "evidence": {"timestamps": ["00:08 lens demonstration"]},
        },
        "layer6_flags_and_scores": {
            "risk_flags": [],
            "scores": scores,
            "final_verdict": "Evidence-bounded brand review.",
            "key_hook": "No attributable brand claim was found.",
        },
    }


def _scores(value) -> dict:
    return {key: value for key in FINAL_V1_LAYER6_SCORE_KEYS}


def _score_issues(payload: dict) -> list[str]:
    return [
        issue
        for issue in final_v1_quality_issues(payload)
        if issue.startswith(SCORE_ISSUE_PREFIX)
    ]


def _apply(parsed: dict) -> dict:
    result: dict = {"analyzed": False, "error": None}
    _apply_final_v1_result(
        result,
        parsed,
        method="gemini_fileapi_gemini-test",
        model="gemini-test",
        usage_metadata={"total_token_count": 123},
        subtitle_used=False,
    )
    return result


# --- score shape: the three legal shapes all pass -------------------------


@pytest.mark.parametrize(
    "value",
    [78, 0, 100, 78.5, 0.0, "85", "0", " 42 ", {"score": 78}, {"score": None}, None],
    ids=[
        "bare-int",
        "bare-zero",
        "bare-hundred",
        "bare-float",
        "bare-float-zero",
        "numeric-string",
        "numeric-string-zero",
        "padded-numeric-string",
        "dict-with-score",
        "dict-with-null-score",
        "explicit-none",
    ],
)
def test_every_legal_score_shape_clears_the_gate(value):
    assert _score_issues(_payload(scores=_scores(value))) == []


def test_bare_integer_scores_reach_ready_end_to_end():
    """The exact shape that degraded 10/10 fresh analyses in production."""
    result = _apply(_payload(scores=_scores(82)))

    assert result["quality_status"] == FINAL_V1_QUALITY_COMPLETE
    assert result["quality_issues"] == []
    assert ensure_final_v1_result_cacheable(result) == "ready"


def test_mixed_shapes_in_one_payload_all_clear():
    scores = dict(zip(FINAL_V1_LAYER6_SCORE_KEYS, [70, {"score": 80}, None, 90.5, 0, 55]))
    assert _score_issues(_payload(scores=scores)) == []


# --- score shape: real gaps are still gaps --------------------------------


def test_a_score_key_the_provider_never_returned_is_still_rejected():
    scores = _scores(78)
    del scores["marketing_value_score"]

    assert _score_issues(_payload(scores=scores)) == [
        f"{SCORE_ISSUE_PREFIX}marketing_value_score"
    ]


def test_every_missing_score_key_is_reported_individually():
    issues = _score_issues(_payload(scores={}))
    assert issues == [f"{SCORE_ISSUE_PREFIX}{key}" for key in FINAL_V1_LAYER6_SCORE_KEYS]


@pytest.mark.parametrize("value", [True, False], ids=["true", "false"])
def test_bool_is_not_a_score(value):
    """``True`` is an ``int`` in Python; scoring a video 1 would invent a number."""
    assert _score_issues(_payload(scores=_scores(value))) == [
        f"{SCORE_ISSUE_PREFIX}{key}" for key in FINAL_V1_LAYER6_SCORE_KEYS
    ]


@pytest.mark.parametrize(
    "value",
    ["", "n/a", "high", [], {}, {"confidence": 0.9}],
    ids=["empty", "not-available", "free-text", "list", "empty-dict", "dict-without-score"],
)
def test_shapes_outside_the_contract_stay_rejected(value):
    assert _score_issues(_payload(scores=_scores(value))) == [
        f"{SCORE_ISSUE_PREFIX}{key}" for key in FINAL_V1_LAYER6_SCORE_KEYS
    ]


def test_a_numeric_string_is_a_returned_score_because_every_reader_reads_it():
    """The gate was the only consumer that could not read ``"85"``.

    ``_score_value`` and ``_score_from_value`` both return 85 for it; the gate
    alone called it missing and degraded the whole analysis over a field that
    was, in fact, returned.
    """
    assert _score_value("85") == 85
    assert _score_from_value("85")[0] is not None
    assert _score_issues(_payload(scores=_scores("85"))) == []


# --- NaN / Infinity are not numbers, and above all not a perfect score -----


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "inf", "-inf"],
)
def test_non_finite_values_never_become_a_score(value):
    assert _score_value(value) is None
    assert _score_value({"score": value}) is None


def test_nan_is_reported_as_a_gap_instead_of_clearing_the_gate():
    """``max(0, min(100, nan))`` is 100: the clamp used to hand back a perfect
    score for a value the model never gave."""
    assert math.isnan(float("nan"))
    assert _score_issues(_payload(scores=_scores(float("nan")))) == [
        f"{SCORE_ISSUE_PREFIX}{key}" for key in FINAL_V1_LAYER6_SCORE_KEYS
    ]


def test_nan_scores_are_absent_from_the_projection_not_present_as_100():
    result = _apply(_payload(scores=_scores(float("nan"))))

    assert result["quality_scores"] == {}
    assert result["quality_overall"] == 0
    assert result["quality_status"] == FINAL_V1_QUALITY_INCOMPLETE


def test_nan_arriving_as_json_infinity_tokens_is_still_a_gap():
    """``json.loads`` accepts bare ``NaN``/``Infinity``; this is the real path."""
    parsed = json.loads('{"content_quality_score": NaN, "viewer_heart_score": Infinity}')

    assert _score_value(parsed["content_quality_score"]) is None
    assert _score_value(parsed["viewer_heart_score"]) is None


def test_finite_out_of_range_values_still_clamp():
    """Only non-finite values are discarded; a real 150 is still a real 100."""
    assert _score_value(150) == 100
    assert _score_value(-5) == 0
    assert _score_issues(_payload(scores=_scores(150))) == []


# --- one rule for both contract shapes ------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        78, 0, 78.5, "85", " 42 ", 150, -5, None, True, False, "", "n/a", [],
        {"nested": 1}, float("nan"), float("inf"), float("-inf"),
    ],
    ids=[
        "int", "zero", "float", "numeric-string", "padded-string", "above-range",
        "below-range", "none", "true", "false", "empty-string", "free-text",
        "list", "nested-dict", "nan", "inf", "-inf",
    ],
)
def test_the_wrapped_shape_is_judged_exactly_like_the_bare_one(value):
    """``{"score": X}`` and a bare ``X`` must reach the same verdict, always.

    The gate ran two rules: a bare value had to be *readable*, while a mapping
    only had to *contain the key*.  ``{"score": NaN}`` therefore counted as a
    returned score while a bare ``NaN`` counted as a gap -- same non-number,
    same model, opposite verdicts.  One rule, one verdict.
    """
    bare = _final_v1_score_returned({"content_quality_score": value}, "content_quality_score")
    wrapped = _final_v1_score_returned(
        {"content_quality_score": {"score": value}}, "content_quality_score"
    )
    assert bare == wrapped

    assert _score_issues(_payload(scores=_scores(value))) == _score_issues(
        _payload(scores=_scores({"score": value}))
    )


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "inf", "-inf"],
)
def test_a_wrapped_non_finite_score_is_a_gap_not_a_returned_field(value):
    """The shape gemini-2.5-flash actually emits.  It used to clear the gate."""
    assert _final_v1_score_returned({"k": {"score": value}}, "k") is False
    assert _score_issues(_payload(scores=_scores({"score": value}))) == [
        f"{SCORE_ISSUE_PREFIX}{key}" for key in FINAL_V1_LAYER6_SCORE_KEYS
    ]

    result = _apply(_payload(scores=_scores({"score": value})))
    assert result["quality_scores"] == {}
    assert result["quality_overall"] == 0
    assert result["quality_status"] == FINAL_V1_QUALITY_INCOMPLETE


def test_a_mapping_that_never_carried_a_score_is_still_a_gap():
    assert _final_v1_score_returned({"k": {"confidence": 0.9}}, "k") is False
    assert _final_v1_score_returned({"k": {}}, "k") is False


def test_a_wrapped_honest_null_is_still_a_returned_field():
    """The one asymmetry the gate keeps on purpose: returned, but not readable."""
    assert _final_v1_score_returned({"k": {"score": None}}, "k") is True
    assert _final_v1_score_returned({"k": None}, "k") is True
    assert _score_issues(_payload(scores=_scores({"score": None}))) == []


# --- the gate may never outrank the projection that reads the same value ---


@pytest.mark.parametrize("value", [78, 78.5, 0, {"score": 78}, {"score": None}, None])
def test_gate_never_rejects_what_the_extract_projection_can_read(value):
    """Red line: this gate is aligned to ``final_v1_extract._score_from_value``."""
    accepted = _score_issues(_payload(scores=_scores(value))) == []
    readable = _score_from_value(value)[0] is not None
    assert accepted or not readable


def test_bare_scores_survive_into_the_projection_not_just_the_gate():
    """A passing gate must not hand the projection an empty score map."""
    result = _apply(_payload(scores=_scores(82)))

    assert result["quality_overall"] == 82
    assert result["quality_scores"] == {key: 82 for key in FINAL_V1_LAYER6_SCORE_KEYS}


def test_projection_drops_only_unreadable_entries():
    scores = dict(zip(FINAL_V1_LAYER6_SCORE_KEYS, [70, {"score": 80}, None, 90.5, 0, 55]))
    result = _apply(_payload(scores=scores))

    assert result["quality_scores"] == {
        "content_quality_score": 70,
        "viewer_heart_score": 80,
        "asset_reuse_score": 90.5,
        "product_proof_score": 0,
        "marketing_value_score": 55,
    }
    assert result["quality_overall"] == 70


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (78, 78),
        (78.5, 78.5),
        ({"score": 78}, 78),
        ("85", 85),
        ({"score": "85"}, 85),
        (150, 100),
        (-5, 0),
        (True, None),
        (None, None),
        ("", None),
        ("high", None),
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_score_value_reads_both_contract_shapes(entry, expected):
    assert _score_value(entry) == expected


@pytest.mark.parametrize("value", [78, 78.5, 0, 150, -5, "85", " 42 ", {"score": 78}])
def test_gate_and_projection_agree_on_every_finite_readable_value(value):
    """The documented alignment, asserted rather than asserted-in-prose."""
    assert _score_from_value(value)[0] is not None
    assert _score_issues(_payload(scores=_scores(value))) == []


# --- inspection_complete: tolerant read, unchanged verdict ----------------


@pytest.mark.parametrize(
    "value",
    [True, 1, "true", "True", " true ", "yes", "1", "pass"],
    ids=["bool", "compat-int-1", "lower", "mixed-case", "padded", "yes", "str-1", "pass"],
)
def test_truthy_inspection_flags_all_clear_the_gate(value):
    assert INSPECTION_ISSUE not in final_v1_quality_issues(
        _payload(scores=_scores(78), inspection_complete=value)
    )


@pytest.mark.parametrize(
    "value",
    [False, 0, "false", "no", "", None],
    ids=["bool", "compat-int-0", "false-str", "no", "empty", "missing"],
)
def test_falsy_inspection_flags_are_still_rejected(value):
    assert INSPECTION_ISSUE in final_v1_quality_issues(
        _payload(scores=_scores(78), inspection_complete=value)
    )


def test_inspection_flag_absent_from_the_block_is_still_rejected():
    payload = _payload(scores=_scores(78))
    del payload["layer1_visual_content"]["brand_product_evidence"]["inspection_complete"]

    assert INSPECTION_ISSUE in final_v1_quality_issues(payload)


def test_compat_int_one_plus_bare_scores_is_the_production_shape():
    """Both defects at once — the combination that stranded 6 of the 10 rows."""
    payload = _payload(scores=_scores(82), inspection_complete=1)

    assert final_v1_quality_issues(payload) == []


@pytest.mark.parametrize("inspection_complete", [True, 1, "true"])
def test_fresh_analysis_reaches_ready_for_every_truthy_provider_shape(inspection_complete):
    """The user-facing fix: a fresh analysis is no longer degraded to invisible."""
    result = _apply(_payload(scores=_scores(82), inspection_complete=inspection_complete))

    assert result["quality_status"] == FINAL_V1_QUALITY_COMPLETE
    assert result["quality_issues"] == []


def test_a_truthy_provider_flag_now_round_trips_through_storage():
    """Closes the asymmetry that used to cap what the replay could recover.

    ``_normalise_brand_product_evidence`` read ``inspection_complete`` with
    ``is True``, so a provider that answered ``1`` was *stored* as ``False`` --
    the fresh verdict was right, but re-judging that stored row failed again.
    Both sides now read the flag the same way.
    """
    for flag in (True, 1, "true", "yes"):
        result = _apply(_payload(scores=_scores(82), inspection_complete=flag))
        stored = result["video_analysis_final_v1"]["layer1_visual_content"]["brand_product_evidence"]

        assert stored["inspection_complete"] is True
        assert (
            final_v1_quality_issues(
                {"video_analysis_final_v1": result["video_analysis_final_v1"]}
            )
            == []
        )


@pytest.mark.parametrize("flag", [False, 0, "false", "no", "", None])
def test_a_falsy_provider_flag_is_still_stored_as_incomplete(flag):
    result = _apply(_payload(scores=_scores(82), inspection_complete=flag))
    stored = result["video_analysis_final_v1"]["layer1_visual_content"]["brand_product_evidence"]

    assert stored["inspection_complete"] is False
    assert final_v1_quality_issues(
        {"video_analysis_final_v1": result["video_analysis_final_v1"]}
    ) == [INSPECTION_ISSUE]


# --- the tolerant flag read may not widen the brand/equipment verdict ------


def _brand_block(**overrides):
    block = {
        "viltrox_status": "unknown",
        "inspection_complete": True,
        "checked_modalities": ["visual", "audio"],
        "viltrox_evidence": [],
        "viltrox_products": [],
        "competitors": [],
    }
    block.update(overrides)
    return _normalise_brand_product_evidence(block)


@pytest.mark.parametrize("flag", [True, 1, "true", "yes", False, 0, None])
def test_the_inspection_flag_can_never_produce_a_present_verdict(flag):
    """``present`` is decided by timed evidence alone; the flag is not consulted."""
    assert _brand_block(viltrox_status="present", inspection_complete=flag)["viltrox_status"] == "unknown"


@pytest.mark.parametrize("flag", [1, "true", "yes"])
def test_absent_still_demands_the_full_inspection_even_with_a_truthy_flag(flag):
    """The only movement the tolerant read unlocks is ``unknown -> absent``, and
    only when every other absence requirement is already met."""
    assert _brand_block(viltrox_status="absent", inspection_complete=flag)["viltrox_status"] == "absent"

    # ...but not without both modalities inspected,
    assert (
        _brand_block(
            viltrox_status="absent", inspection_complete=flag, checked_modalities=["visual"]
        )["viltrox_status"]
        == "unknown"
    )
    # ...and not while any positive evidence exists,
    assert (
        _brand_block(
            viltrox_status="absent",
            inspection_complete=flag,
            viltrox_evidence=[
                {"modality": "metadata", "detail": "Viltrox named in the description"}
            ],
        )["viltrox_status"]
        == "unknown"
    )
    # ...and not when the model itself did not claim absence.
    assert (
        _brand_block(viltrox_status="unknown", inspection_complete=flag)["viltrox_status"]
        == "unknown"
    )
