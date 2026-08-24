from __future__ import annotations

from app.domains.kol.pool_read_projection import build_pool_read_selection
from test_discovery_pool_read_projection import (
    _cloud_duplicate_rows,
    _cloud_official_rows,
    _luke_bridge,
    _row,
)


def test_cloud_seven_groups_fold_all_when_luke_has_one_strong_account_bridge() -> None:
    reviewer = _row(
        6001,
        "alex-films",
        "https://youtube.com/@alex-films",
        "Viltrox",
        bio="I'm an independent filmmaker reviewing Viltrox and other lenses.",
    )
    selection = build_pool_read_selection(
        [*_cloud_duplicate_rows(), *_cloud_official_rows(), reviewer],
        session_items=_luke_bridge(),
        bridge_evidence_available=True,
    )

    assert selection.folded_ids == frozenset({3505, 3533, 3571, 4946, 4948, 4950, 4952})
    assert 3505 not in selection.visible_ids
    assert 4062 in selection.visible_ids
    assert selection.official_ids == frozenset({4561, 4581})
    assert {1534, 4515}.issubset(selection.visible_ids)
    assert 6001 in selection.visible_ids
    assert selection.canonical_by_id[3533] == 3971
    assert selection.canonical_by_id[3571] == 3572
    assert selection.canonical_by_id[4946] == 4997
    assert selection.canonical_by_id[3505] == 4062
    assert selection.audit_by_id[4062]["canonical_identity_status"] == "canonical_read_folded"
    assert selection.audit_by_id[4062]["canonical_duplicate_ids"] == [3505]
    assert selection.diagnostics == {
        "method": "canonical_pool_read_projection_v1",
        "physical_master_rows": 19,
        "visible_rows": 10,
        "canonical_folded_groups": 7,
        "canonical_folded_rows": 7,
        "canonical_manual_review_groups": 0,
        "excluded_confirmed_official": 2,
        "official_verdict_counts": {"own_brand": 2},
        "bridge_evidence_available": True,
        "history_rows_deleted": 0,
        "pool_rows_deleted": 0,
        "duplicate_pointer_rows_written": 0,
        "writes_performed": 0,
    }


def test_shared_profile_never_overrides_two_native_account_ids() -> None:
    rows = _cloud_duplicate_rows()[:2]
    conflicting_bridges = [
        *_luke_bridge(),
        {
            "id": 1848,
            "kol_pool_id": 3505,
            "item_type": "existing_kol",
            "source_url": "https://youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa",
            "payload_json": {
                "platform": "youtube",
                "handle": "lukewtcleland",
                "channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa",
            },
        },
    ]

    selection = build_pool_read_selection(
        rows,
        session_items=conflicting_bridges,
        bridge_evidence_available=True,
    )

    assert selection.visible_ids == frozenset({3505, 4062})
    assert selection.folded_ids == frozenset()
    assert selection.diagnostics["canonical_manual_review_groups"] == 1
    assert {
        selection.audit_by_id[pool_id]["canonical_identity_status"]
        for pool_id in selection.visible_ids
    } == {"manual_review_conflict"}
