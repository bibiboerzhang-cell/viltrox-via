"""Smart-local activity gate: unknown is deferred, stale is still rejected.

The gate used to collapse "we never crawled this creator" and "this creator
stopped posting" into one hard rejection, which threw away 58% of the pool on
a data gap.  Splitting the verdict must never open the freshness thresholds
themselves, so every hard-rejection path is pinned here alongside the new
deferred bucket.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.kol import (
    profile_recall_activity_gate,
    profile_recall_qualification,
)


NOW = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


def _item(item_id: int, *, handle: str | None = None, rank: float = 1.0) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": handle or f"creator-{item_id}",
        "channel_name": f"Creator {item_id}",
        "platform": "youtube",
        "bucket": "creator",
        "display_rank_score": rank,
        "recall_rank_score": rank,
        "match_evidence": [{"field": "bio", "term": "lens"}],
    }


def _row(item_id: int, *, followers: int = 5_000) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "followers": followers,
        "country": "US",
        "language": "en",
        "profile_type": "creator",
        "platform": "youtube",
        "bio": "Independent photographer testing camera lenses in the field.",
        "raw_platform_data": {},
    }


def _fresh_evidence(
    *,
    age_days: float = 5,
    identity: bool = True,
    active: bool = True,
    evidence_type: str = "video",
) -> dict[str, Any]:
    latest: dict[str, Any] = {
        "posted_at": (NOW - timedelta(days=age_days)).isoformat(),
        "evidence_type": evidence_type,
        "is_active": active,
        "source": "vkpi_kol_video_evidence.posted_at",
    }
    if identity:
        latest["content_url"] = "https://www.youtube.com/watch?v=auditable"
    return {"latest_real_video": latest}


def _unknown_evidence() -> dict[str, Any]:
    """No video row at all — the pool simply never crawled this creator."""
    return {}


def _qualify(
    items: list[dict[str, Any]],
    rows: dict[int, dict[str, Any]],
    evidence: dict[int, dict[str, Any]],
    *,
    target_count: int | None = None,
    policy_overrides: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policy = profile_recall_qualification.smart_local_policy(
        market="US",
        platforms=["youtube"],
    )
    policy.update(policy_overrides or {})
    selected, _, contract = profile_recall_qualification.qualify_local_candidates(
        buckets={"creator": items, "reviewer": []},
        rows_by_id=rows,
        evidence_by_id=evidence,
        policy=policy,
        creator_quota=30,
        reviewer_quota=0,
        target_count=target_count,
        as_of=NOW,
    )
    return selected, contract


def _ids(items: list[dict[str, Any]]) -> list[int]:
    return [item["kol_pool_id"] for item in items]


def test_freshness_thresholds_are_untouched_by_the_split() -> None:
    assert profile_recall_qualification.SMART_LOCAL_FRESH_DAYS == 30
    assert profile_recall_qualification.SMART_LOCAL_MAX_VIDEO_AGE_DAYS == 45
    assert profile_recall_qualification.SMART_LOCAL_MIN_FOLLOWERS == 3_000
    assert profile_recall_qualification.SMART_LOCAL_TARGET == 30


def test_unknown_activity_is_deferred_and_labelled_instead_of_discarded() -> None:
    selected, contract = _qualify(
        [_item(1), _item(2)],
        {1: _row(1), 2: _row(2)},
        {1: _fresh_evidence(), 2: _unknown_evidence()},
    )
    assert _ids(selected) == [1, 2]
    deferred_item = selected[1]
    assert deferred_item["selection_tier"] == "deferred_activity_unknown"
    assert deferred_item["activity_status"] == "activity_unknown_pending_fetch"
    assert deferred_item["activity_status_reason"] == "latest_video_unknown"

    proof = deferred_item["qualification_evidence"]
    assert proof["deferred"] is True
    assert proof["passed"] is False
    assert proof["rejection_reasons"] == []
    assert proof["activity"]["known"] is False
    assert proof["activity"]["passed"] is False
    assert proof["activity"]["age_days"] is None
    assert proof["activity"]["status"] == "activity_unknown_pending_fetch"
    assert "latest_video_unknown" not in contract["rejected_by_reason"]
    assert contract["qualified_count"] == 1
    assert contract["returned_count"] == 2
    assert contract["qualified_returned_count"] == 1
    assert contract["deferred_activity"]["available"] == 1
    assert contract["deferred_activity"]["returned"] == 1


def test_stale_creator_is_still_hard_rejected_even_with_empty_slots() -> None:
    """The trap this lane must not fall into: 46 days is a verdict, not a gap."""
    selected, contract = _qualify(
        [_item(1), _item(2)],
        {1: _row(1), 2: _row(2)},
        {1: _fresh_evidence(), 2: _fresh_evidence(age_days=46)},
    )
    assert _ids(selected) == [1]
    assert contract["rejected_by_reason"] == {"latest_video_stale": 1}
    assert contract["deferred_activity"]["available"] == 0
    assert contract["shortfall"] == 29
    assert all(item.get("selection_tier") != "deferred_activity_unknown" for item in selected)


def test_forty_five_day_boundary_still_qualifies_and_forty_six_does_not() -> None:
    selected, contract = _qualify(
        [_item(1), _item(2)],
        {1: _row(1), 2: _row(2)},
        {1: _fresh_evidence(age_days=45), 2: _fresh_evidence(age_days=45.5)},
    )
    assert _ids(selected) == [1]
    assert selected[0]["qualification_evidence"]["activity"]["passed"] is True
    assert contract["rejected_by_reason"] == {"latest_video_stale": 1}


def test_future_inactive_and_unauditable_rows_stay_hard_rejections() -> None:
    items = [_item(1), _item(2), _item(3), _item(4)]
    selected, contract = _qualify(
        items,
        {item_id: _row(item_id) for item_id in range(1, 5)},
        {
            1: _fresh_evidence(age_days=-365),
            2: _fresh_evidence(active=False),
            3: _fresh_evidence(evidence_type="channel_created"),
            4: _fresh_evidence(identity=False),
        },
    )
    assert selected == []
    assert contract["rejected_by_reason"] == {
        "latest_video_in_future": 1,
        # An inactive video and a non-video evidence type share one verdict.
        "latest_video_not_active_video": 2,
        "latest_video_identity_missing": 1,
    }
    assert contract["deferred_activity"]["available"] == 0
    assert contract["deferred_activity"]["returned"] == 0


def test_backfill_never_exceeds_the_target() -> None:
    items = [_item(item_id) for item_id in range(1, 8)]
    rows = {item_id: _row(item_id) for item_id in range(1, 8)}
    evidence = {
        1: _fresh_evidence(),
        2: _fresh_evidence(),
        **{item_id: _unknown_evidence() for item_id in range(3, 8)},
    }
    selected, contract = _qualify(items, rows, evidence, target_count=3)
    assert len(selected) == 3
    assert _ids(selected)[:2] == [1, 2]
    assert contract["deferred_activity"]["available"] == 5
    assert contract["deferred_activity"]["returned"] == 1
    assert contract["returned_count"] == 3
    # The backfill fills leftover room; it does not *satisfy* the target.  Only
    # two creators cleared the activity gate, so one slot is still missing and
    # the contract has to keep saying so instead of reporting a closed gap.
    assert contract["qualified_returned_count"] == 2
    assert contract["shortfall"] == 1
    assert contract["status"] == "shortfall"


def test_deferred_rows_never_erase_the_gap_they_did_not_fill() -> None:
    """Zero qualified creators can never be reported as a satisfied target."""
    selected, contract = _qualify(
        [_item(item_id) for item_id in range(1, 4)],
        {item_id: _row(item_id) for item_id in range(1, 4)},
        {item_id: _unknown_evidence() for item_id in range(1, 4)},
        target_count=3,
    )
    assert len(selected) == 3
    assert contract["qualified_count"] == 0
    assert contract["qualified_returned_count"] == 0
    assert contract["shortfall"] == 3
    assert contract["status"] == "shortfall"
    assert contract["deferred_activity"]["counts_toward_target"] is False
    assert contract["deferred_activity"]["selectable"] is True


def test_deferred_rows_sort_behind_qualified_rows_even_when_ranked_higher() -> None:
    selected, _ = _qualify(
        [_item(1, rank=0.01), _item(2, rank=99.0)],
        {1: _row(1), 2: _row(2)},
        {1: _fresh_evidence(age_days=44), 2: _unknown_evidence()},
    )
    assert _ids(selected) == [1, 2]
    assert selected[0]["qualification_evidence"]["activity"]["passed"] is True
    assert selected[1]["qualification_evidence"]["deferred"] is True


def test_deferral_does_not_bypass_followers_market_or_platform_gates() -> None:
    items = [_item(1), _item(2), _item(3)]
    rows = {
        1: _row(1, followers=2_999),
        2: {**_row(2), "country": "GB"},
        3: {**_row(3), "platform": "tiktok"},
    }
    items[2]["platform"] = "tiktok"
    selected, contract = _qualify(
        items,
        rows,
        {item_id: _unknown_evidence() for item_id in (1, 2, 3)},
    )
    assert selected == []
    assert contract["rejected_by_reason"] == {
        "followers_below_3000": 1,
        "market_mismatch": 1,
        "platform_mismatch": 1,
    }
    assert contract["deferred_activity"]["available"] == 0


def test_deferred_row_never_shadows_a_qualified_row_for_the_same_creator() -> None:
    unknown_first = _item(1, handle="same-account", rank=99.0)
    fresh_later = _item(2, handle="same-account", rank=1.0)
    selected, contract = _qualify(
        [unknown_first, fresh_later],
        {1: _row(1), 2: _row(2)},
        {1: _unknown_evidence(), 2: _fresh_evidence()},
    )
    assert _ids(selected) == [2]
    assert contract["qualified_count"] == 1
    assert contract["deferred_activity"]["available"] == 0
    assert contract["rejected_by_reason"] == {"duplicate_canonical_identity": 1}


def test_two_unknown_rows_for_one_creator_take_a_single_slot() -> None:
    selected, contract = _qualify(
        [_item(1, handle="same-account"), _item(2, handle="same-account")],
        {1: _row(1), 2: _row(2)},
        {1: _unknown_evidence(), 2: _unknown_evidence()},
    )
    assert len(selected) == 1
    assert contract["rejected_by_reason"] == {"duplicate_canonical_identity": 1}


def test_policy_knob_is_live_in_both_directions() -> None:
    items_and_rows: dict[str, Any] = {
        "items": [_item(1)],
        "rows": {1: _row(1)},
        "evidence": {1: _unknown_evidence()},
    }
    rejecting, reject_contract = _qualify(
        items_and_rows["items"],
        items_and_rows["rows"],
        items_and_rows["evidence"],
        policy_overrides={"unknown_video_activity": "reject"},
    )
    assert rejecting == []
    assert reject_contract["rejected_by_reason"] == {"latest_video_unknown": 1}
    assert reject_contract["deferred_activity"]["policy"] == "reject"

    deferring, defer_contract = _qualify(
        [_item(1)],
        {1: _row(1)},
        {1: _unknown_evidence()},
        policy_overrides={"unknown_video_activity": "defer"},
    )
    assert _ids(deferring) == [1]
    assert defer_contract["deferred_activity"]["policy"] == "defer"


def test_unknown_knob_defaults_to_defer_but_fails_closed_on_garbage_values() -> None:
    policy = profile_recall_qualification.smart_local_policy(market="US")
    assert policy["unknown_video_activity"] == "defer"
    assert "allow_unknown_or_stale_video" not in policy
    assert profile_recall_activity_gate.unknown_activity_mode(policy) == "defer"
    assert profile_recall_activity_gate.unknown_activity_mode(
        {**policy, "unknown_video_activity": "allow_everything"}
    ) == "reject"
    assert profile_recall_activity_gate.unknown_activity_mode({}) == "reject"


def test_online_lane_keeps_hard_rejection_for_unknown_activity() -> None:
    policy = profile_recall_qualification.smart_local_policy(market="US")
    assert profile_recall_activity_gate.unknown_activity_mode(
        {**policy, "origin_lane": "online"}
    ) == "reject"
    selected, contract = _qualify(
        [_item(1)],
        {1: _row(1)},
        {1: _unknown_evidence()},
        policy_overrides={"origin_lane": "online"},
    )
    assert selected == []
    assert contract["rejected_by_reason"] == {"latest_video_unknown": 1}


def test_should_defer_activity_refuses_every_non_unknown_verdict() -> None:
    stale = profile_recall_activity_gate.evaluate_activity(
        latest=_fresh_evidence(age_days=200)["latest_real_video"],
        now=NOW,
        max_video_age_days=45,
        fresh_priority_days=30,
    )
    assert stale["known"] is True
    assert stale["reason"] == "latest_video_stale"
    assert profile_recall_activity_gate.should_defer_activity(stale, "defer") is False

    unknown = profile_recall_activity_gate.evaluate_activity(
        latest={},
        now=NOW,
        max_video_age_days=45,
        fresh_priority_days=30,
    )
    assert unknown["reason"] == "latest_video_unknown"
    assert profile_recall_activity_gate.should_defer_activity(unknown, "defer") is True
    assert profile_recall_activity_gate.should_defer_activity(unknown, "reject") is False

    # A hand-built verdict that lies about being unknown while carrying a
    # timestamp must still be refused — the locks are independent.
    assert profile_recall_activity_gate.should_defer_activity(
        {**stale, "reason": "latest_video_unknown", "known": False},
        "defer",
    ) is False


def test_deferred_rows_are_not_reported_as_rejected_evidence() -> None:
    _, contract = _qualify(
        [_item(1), _item(2)],
        {1: _row(1), 2: _row(2)},
        {1: _unknown_evidence(), 2: _fresh_evidence(age_days=99)},
    )
    sampled = contract["rejected_evidence_sample"]
    assert [entry["kol_pool_id"] for entry in sampled] == [2]
    assert contract["funnel"]["fresh_video_pass"] == 0
    assert contract["funnel"]["activity_unknown_deferred"] == 1
    assert contract["funnel"]["activity_stage_pass"] == 1


# ---------------------------------------------------------------------------
# 判定 → 返回 → 计数 → 落库回放 → 勾选入库:整条链必须自洽。
# 「占名额就要能选」是单向约束;活跃度未知桶不占 30 人目标数,却仍然可选,
# 因为一批看得见、点不动的行比不返回还糟。
# ---------------------------------------------------------------------------


def _deferred_proof() -> dict[str, Any]:
    """Take a *real* server proof from the qualifier, never a hand-built one."""
    selected, _ = _qualify([_item(1)], {1: _row(1)}, {1: _unknown_evidence()})
    return selected[0]["qualification_evidence"]


def _fresh_proof() -> dict[str, Any]:
    selected, _ = _qualify([_item(1)], {1: _row(1)}, {1: _fresh_evidence()})
    return selected[0]["qualification_evidence"]


def test_deferred_proof_predicate_accepts_only_the_never_crawled_bucket() -> None:
    from app.domains.kol.profile_recall_activity_gate import deferred_activity_proof

    proof = _deferred_proof()
    assert deferred_activity_proof(proof) is True
    assert deferred_activity_proof(_fresh_proof()) is False

    stale_selected, _ = _qualify(
        [_item(1)], {1: _row(1)}, {1: _fresh_evidence(age_days=46)}
    )
    assert stale_selected == []

    # Impostors: a stale verdict repainted as deferred, and a deferred row whose
    # followers gate did not actually pass.
    stale_repainted = {**_fresh_proof(), "deferred": True, "passed": False}
    assert deferred_activity_proof(stale_repainted) is False
    broken_gate = {**proof, "followers": {**proof["followers"], "passed": False}}
    assert deferred_activity_proof(broken_gate) is False
    assert deferred_activity_proof({**proof, "rejection_reasons": ["low_relevance"]}) is False
    assert deferred_activity_proof({**proof, "deferred_reason": "latest_video_stale"}) is False
    assert deferred_activity_proof({}) is False


def test_replay_keeps_the_marker_that_tells_unknown_apart_from_rejected() -> None:
    from app.domains.kol.search_sessions_attach import _safe_gate_evidence
    from app.domains.kol.search_sessions_recall_fields import _RECALL_SESSION_PAYLOAD_FIELDS

    safe = _safe_gate_evidence(_deferred_proof(), allowed_terms={"lens"})
    assert safe["deferred"] is True
    assert safe["deferred_reason"] == "latest_video_unknown"
    assert safe["passed"] is False
    assert safe["activity"]["known"] is False
    assert safe["activity"]["deferred"] is True
    assert safe["activity"]["status"] == "activity_unknown_pending_fetch"
    assert safe["activity"]["deferred_reason"] == "latest_video_unknown"

    stale_safe = _safe_gate_evidence(_fresh_proof(), allowed_terms={"lens"})
    assert stale_safe["deferred"] is False
    assert stale_safe["activity"]["known"] is True

    # The item-level labels the UI renders verbatim must survive the allowlist.
    for field in ("selection_tier", "activity_status", "activity_status_reason"):
        assert field in _RECALL_SESSION_PAYLOAD_FIELDS


def test_session_history_records_the_knob_and_the_bucket_it_produced() -> None:
    from app.domains.kol.search_sessions_attach import _safe_local_qualification

    _, contract = _qualify(
        [_item(1), _item(2)],
        {1: _row(1), 2: _row(2)},
        {1: _fresh_evidence(), 2: _unknown_evidence()},
    )
    safe = _safe_local_qualification(contract)
    assert safe["policy"]["unknown_video_activity"] == "defer"
    # The knob that no caller reads any more must not linger in the allowlist.
    assert "allow_unknown_or_stale_video" not in safe["policy"]
    assert safe["deferred_activity"] == {
        "counts_toward_target": False,
        "selectable": True,
        "policy": "defer",
        "reason_code": "latest_video_unknown",
        "status": "activity_unknown_pending_fetch",
        "available": 1,
        "returned": 1,
        "max_video_age_days": 45,
        "fresh_priority_days": 30,
    }
    assert safe["qualified_returned_count"] == 1
    assert safe["shortfall"] == 29
