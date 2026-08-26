"""The replay must make a recovered analysis *visible*, not merely re-labelled.

A degraded final_v1 row carries three separate facts, and readers need all
three: ``result.quality_status`` inside the JSON, ``status='quality_incomplete'``
on the row, and the isolated ``target_type='video_quality_triage'`` namespace
migration 299 parked it in (sometimes with a suffixed ``derive_method`` to dodge
the unique key).  Rewriting only the JSON produces a run that reports success
while the user still cannot find the analysis.

These tests pin the placement contract and the two refusals that bound it:
never demote a currently-complete row, never resolve a unique-key collision by
overwriting one of two paid records.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

replay = importlib.import_module("replay_final_v1_quality_gate")

FINAL = replay.FINAL_DERIVE_METHOD
COMPLETE = replay.FINAL_V1_QUALITY_COMPLETE
INCOMPLETE = replay.FINAL_V1_QUALITY_INCOMPLETE


def _complete_payload() -> dict:
    """A payload the repaired gate judges complete, in the shapes prod emits."""
    return {
        "layer1_visual_content": {
            "content_summary": "A creator demonstrates autofocus and flare behaviour.",
            "scene_timeline": [{"timestamp": "00:08", "what": "Lens demonstration."}],
            "brand_product_evidence": {
                "viltrox_status": "unknown",
                # int 1 from the compat adapter, not a real bool
                "inspection_complete": 1,
                "checked_modalities": ["visual", "audio"],
                "viltrox_evidence": [],
                "viltrox_products": [],
                "competitors": [],
            },
            "evidence": {"timestamps": ["00:08 lens demonstration"]},
        },
        "layer6_flags_and_scores": {
            "risk_flags": [],
            # bare integers, plus one numeric string
            "scores": {
                "content_quality_score": 82,
                "viewer_heart_score": 71,
                "channel_value_score": "68",
                "asset_reuse_score": 40,
                "product_proof_score": 55,
                "marketing_value_score": 77,
            },
            "final_verdict": "Evidence-bounded brand review.",
            "key_hook": "No attributable brand claim was found.",
        },
    }


def _incomplete_payload() -> dict:
    payload = _complete_payload()
    del payload["layer6_flags_and_scores"]["scores"]["marketing_value_score"]
    return payload


def _row(
    *,
    cache_id: int = 4211,
    target_id: str = "5829",
    payload: dict | None = None,
    quality_status: str | None = INCOMPLETE,
    quality_issues: list[str] | None = None,
    target_type: str = "video_quality_triage",
    status: str = "quality_incomplete",
    derive_method: str = FINAL,
) -> dict:
    result = {
        "analyzed": True,
        "video_analysis_final_v1": payload if payload is not None else _complete_payload(),
    }
    if quality_status is not None:
        result["quality_status"] = quality_status
        result["quality_issues"] = quality_issues or []
    return {
        "id": cache_id,
        "target_type": target_type,
        "target_id": target_id,
        "derive_method": derive_method,
        "model": "gemini-test",
        "status": status,
        "result": result,
    }


# --- the defect this file exists for --------------------------------------


def test_a_recovered_row_is_restored_to_the_namespace_readers_read():
    verdict = replay.judge(_row())

    assert verdict["action"] == "recovered"
    assert verdict["recomputed_quality_status"] == COMPLETE
    assert verdict["visible_before"] is False
    assert verdict["plan"]["desired"] == {
        "target_type": "video",
        "status": "ready",
        "derive_method": FINAL,
    }
    assert verdict["plan"]["changes"] == {
        "target_type": "video",
        "status": "ready",
    }


def test_the_write_carries_the_placement_and_not_only_the_json():
    """Regression: the previous UPDATE touched ``result`` alone."""
    row = _row()
    verdict = replay.judge(row)
    calls: list[tuple[str, dict]] = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params):
            calls.append((sql, params))

    class _Conn:
        def cursor(self):
            return _Cursor()

    replay.apply_verdict(_Conn(), row, verdict)

    sql, params = calls[0]
    assert "target_type = %(target_type)s" in sql
    assert "status = %(status)s" in sql
    assert "derive_method = %(derive_method)s" in sql
    assert params["target_type"] == "video"
    assert params["status"] == "ready"
    assert params["derive_method"] == FINAL
    assert params["cache_id"] == 4211
    # the paid analysis payload itself is never rewritten
    written = params["result"].obj
    assert written["video_analysis_final_v1"] == row["result"]["video_analysis_final_v1"]
    assert written["quality_status"] == COMPLETE
    assert written["quality_issues"] == []


def test_migration_299_derive_method_suffix_is_stripped_on_recovery():
    row = _row(derive_method=f"{FINAL}__quality_migrated_4211")
    verdict = replay.judge(row)

    assert verdict["plan"]["desired"]["derive_method"] == FINAL
    assert verdict["action"] == "recovered"


@pytest.mark.parametrize(
    "value",
    [
        FINAL,
        f"{FINAL}__quality_migrated_",
        f"{FINAL}__quality_migrated_abc",
        "video_analysis_v2",
        "",
    ],
)
def test_only_the_documented_suffix_is_stripped(value):
    assert replay.natural_derive_method(value) == (value or "")


def test_the_fetch_also_matches_suffixed_derive_methods():
    """The exact-match filter skipped the rows most in need of recovery."""
    captured: dict = {}

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cursor()

    replay.fetch_rows(_Conn(), target_ids=["5829"], cache_ids=[])

    assert "strpos(derive_method, %(migrated_prefix)s) = 1" in captured["sql"]
    assert captured["params"]["migrated_prefix"] == f"{FINAL}__quality_migrated_"
    # SQL compatibility: no LIKE, no literal percent-sign matching
    assert " LIKE " not in captured["sql"].upper()


def test_an_already_visible_row_is_left_where_it_is():
    row = _row(target_type="video", status="ready", quality_status=INCOMPLETE,
               quality_issues=["layer6_flags_and_scores.scores.marketing_value_score"])
    verdict = replay.judge(row)

    assert verdict["visible_before"] is True
    assert verdict["action"] == "recovered"
    assert verdict["plan"]["changes"] == {}


def test_an_unchanged_row_is_reported_as_unchanged():
    row = _row(target_type="video", status="ready", quality_status=COMPLETE, quality_issues=[])
    verdict = replay.judge(row)

    assert verdict["action"] == "unchanged"
    assert verdict["plan"]["changes"] == {}


# --- refusals -------------------------------------------------------------


def test_a_currently_complete_row_is_never_demoted():
    row = _row(payload=_incomplete_payload(), quality_status=COMPLETE, quality_issues=[],
               target_type="video", status="ready")
    verdict = replay.judge(row)

    assert verdict["action"] == "would_demote"
    assert verdict["plan"]["changes"] == {}


def test_a_still_incomplete_row_keeps_its_placement_and_its_reasons():
    row = _row(payload=_incomplete_payload())
    verdict = replay.judge(row)

    assert verdict["action"] == "still_incomplete"
    assert verdict["recomputed_quality_status"] == INCOMPLETE
    assert verdict["recomputed_issues"] == [
        "layer6_flags_and_scores.scores.marketing_value_score"
    ]
    assert verdict["plan"]["changes"] == {}


def test_this_replay_never_hides_a_readable_analysis():
    """An incomplete verdict may not push a visible row into triage."""
    row = _row(payload=_incomplete_payload(), target_type="video", status="ready",
               quality_status=INCOMPLETE)
    verdict = replay.judge(row)

    assert verdict["plan"]["desired"]["target_type"] == "video"
    assert verdict["plan"]["desired"]["status"] == "ready"


def test_a_restore_that_would_collide_with_another_paid_row_is_refused():
    verdicts = [replay.judge(_row(cache_id=4211, target_id="5829"))]
    replay.annotate_conflicts(verdicts, {("5829", FINAL): 9001})

    assert verdicts[0]["action"] == "blocked_conflict"
    assert verdicts[0]["conflict_with_cache_id"] == 9001
    assert verdicts[0]["conflict_scope"] == "stored"


def test_two_rows_in_one_run_may_not_claim_the_same_visible_key():
    """The collision the stored-slot check cannot see.

    A natural triage row and its migration-299-suffixed twin both normalise to
    ``(video, <target_id>, video_analysis_final_v1)``.  The slot is free when
    the run starts, so the first UPDATE took it and the second raised
    ``UniqueViolation`` -- mid-apply, with earlier writes already on the
    connection.  Both records are paid; neither is written.
    """
    verdicts = [
        replay.judge(_row(cache_id=4211, target_id="5829")),
        replay.judge(_row(cache_id=4212, target_id="5829",
                          derive_method=f"{FINAL}__quality_migrated_4212")),
    ]
    replay.annotate_conflicts(verdicts, {})

    assert [verdict["action"] for verdict in verdicts] == [
        "blocked_conflict",
        "blocked_conflict",
    ]
    assert verdicts[0]["conflict_with_cache_id"] == 4212
    assert verdicts[1]["conflict_with_cache_id"] == 4211
    assert {verdict["conflict_scope"] for verdict in verdicts} == {"batch"}
    assert all(verdict["action"] not in replay.WRITABLE_ACTIONS for verdict in verdicts)


def test_a_batch_conflict_refuses_every_contender_rather_than_picking_one():
    """Three claimants, three refusals: choosing between paid rows is not a
    script's call."""
    verdicts = [
        replay.judge(_row(cache_id=cache_id, target_id="5829",
                          derive_method=f"{FINAL}__quality_migrated_{cache_id}"))
        for cache_id in (7001, 7002, 7003)
    ]
    replay.annotate_conflicts(verdicts, {})

    assert {verdict["action"] for verdict in verdicts} == {"blocked_conflict"}


def test_different_targets_in_one_run_do_not_collide():
    verdicts = [
        replay.judge(_row(cache_id=4211, target_id="5829")),
        replay.judge(_row(cache_id=4212, target_id="5830")),
    ]
    replay.annotate_conflicts(verdicts, {})

    assert [verdict["action"] for verdict in verdicts] == ["recovered", "recovered"]


def test_a_slot_held_by_the_row_itself_is_not_a_collision():
    verdicts = [replay.judge(_row(cache_id=4211, target_id="5829",
                                  derive_method=f"{FINAL}__quality_migrated_4211"))]
    replay.annotate_conflicts(verdicts, {("5829", FINAL): 4211})

    assert verdicts[0]["action"] == "recovered"


def test_a_row_that_needs_no_move_is_not_conflict_checked():
    verdicts = [replay.judge(_row(cache_id=4211, target_id="5829",
                                  target_type="video", status="ready"))]
    replay.annotate_conflicts(verdicts, {("5829", FINAL): 9001})

    assert verdicts[0]["action"] == "recovered"
    assert verdicts[0]["conflict_with_cache_id"] is None


def test_blocked_and_demoting_rows_are_outside_the_writable_set():
    assert "blocked_conflict" not in replay.WRITABLE_ACTIONS
    assert "would_demote" not in replay.WRITABLE_ACTIONS
    assert "legacy_skipped" not in replay.WRITABLE_ACTIONS
    assert replay.WRITABLE_ACTIONS == {"recovered", "still_incomplete"}


# --- only the namespace migration 299 filled is ever emptied --------------


@pytest.mark.parametrize(
    "target_type",
    ["kol_profile", "video_keyframe_qa", "channel", "VIDEO", "video_quality_triage_v2"],
)
def test_a_row_from_another_namespace_is_never_dragged_into_the_visible_one(target_type):
    """Sharing a derive method is not evidence of having been degraded.

    Migration 299 moved rows out of ``video`` and into ``video_quality_triage``
    -- nowhere else.  A row under any other ``target_type`` was never touched by
    it, so restoring it would not be a restore: it would be an invention, aimed
    at a unique key the row does not own.  Note ``VIDEO``: the visible namespace
    is matched exactly, because readers match it exactly.
    """
    row = _row(target_type=target_type, status="ready")
    verdict = replay.judge(row)

    assert verdict["action"] == "foreign_namespace"
    assert verdict["plan"]["changes"] == {}
    assert verdict["plan"]["desired"]["target_type"] == target_type
    assert verdict["action"] not in replay.WRITABLE_ACTIONS


def test_a_foreign_namespace_row_is_counted_not_silently_dropped():
    """Reported honestly: the operator gets to see that the row exists."""
    rows = [
        _row(cache_id=1, target_id="5829"),
        _row(cache_id=2, target_id="5830", target_type="kol_profile", status="ready"),
    ]
    verdicts = [replay.judge(row) for row in rows]

    assert [verdict["action"] for verdict in verdicts] == ["recovered", "foreign_namespace"]
    assert verdicts[1]["recomputed_quality_status"] == COMPLETE  # judged, just not moved


def test_the_two_judged_namespaces_are_exactly_the_ones_299_touched():
    assert replay.JUDGED_TARGET_TYPES == {"video", "video_quality_triage"}
    assert replay.RELOCATABLE_SOURCE_TARGET_TYPE == "video_quality_triage"


def test_a_foreign_namespace_row_is_not_conflict_checked_either():
    verdicts = [replay.judge(_row(cache_id=4211, target_id="5829",
                                  target_type="kol_profile", status="ready"))]
    replay.annotate_conflicts(verdicts, {("5829", FINAL): 9001})

    assert verdicts[0]["action"] == "foreign_namespace"
    assert verdicts[0]["conflict_with_cache_id"] is None


# --- legacy rows ----------------------------------------------------------


def test_a_visible_pre_gate_row_is_skipped_by_default():
    row = _row(target_type="video", status="ready", quality_status=None)
    assert replay.judge(row)["action"] == "legacy_skipped"


def test_a_verdictless_row_sitting_in_triage_is_still_judged():
    """Only the gate could have put it there, so it is not a pre-gate row."""
    row = _row(quality_status=None)
    verdict = replay.judge(row)

    assert verdict["action"] == "recovered"
    assert verdict["plan"]["changes"]["target_type"] == "video"


def test_include_legacy_opts_a_visible_pre_gate_row_back_in():
    row = _row(target_type="video", status="ready", quality_status=None)
    assert replay.judge(row, include_legacy=True)["action"] == "recovered"


# --- dry run is still the default -----------------------------------------


def test_apply_is_off_unless_asked_for():
    assert replay._parse_args([]).apply is False
    assert replay._parse_args(["--apply"]).apply is True
    assert replay._parse_args([]).include_legacy is False
