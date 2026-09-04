from __future__ import annotations

from app.domains.kol import search_sessions
from app.domains.kol.search_sessions_items import project_session_result_summary


def _candidate(index: int, *, item_type: str = "new_creator") -> dict[str, object]:
    return {
        "id": index,
        "item_type": item_type,
        "status": "identified",
        "rank": index,
        "source_url": f"https://youtube.com/@creator-{index}",
        "payload": {
            "platform": "youtube",
            "handle": f"creator-{index}",
        },
    }


def test_discovery_rows_replace_stale_empty_recall_headline() -> None:
    summary = project_session_result_summary(
        {
            "kind": "kol_recall",
            "match_status": "empty",
            "result_state": "empty",
            "diagnostics": {"returned_count": 0, "result_state": "empty"},
            "new_discovery": {"status": "ready", "counts": {"new_creators": 16}},
        },
        [_candidate(index) for index in range(1, 17)],
        status="ready",
    )

    assert summary["items_count"] == 16
    assert summary["returned_count"] == 16
    assert summary["match_status"] == "matched"
    assert summary["result_state"] == "ready"
    assert summary["diagnostics"]["returned_count"] == 16
    assert summary["diagnostics"]["result_state"] == "ready"
    assert summary["result_projection"] == {
        "schema": "kol_search_session_result_v1",
        "source": "persisted_session_items",
        "items_count": 16,
        "returned_count": 16,
        "match_status": "matched",
        "result_state": "ready",
        "terminal": True,
        "by_lane": {"recall": 0, "discovery": 16, "online": 0},
    }


def test_empty_running_search_is_waiting_not_terminal_empty() -> None:
    summary = project_session_result_summary(
        {
            "kind": "kol_recall",
            "recall_snapshot_attached": True,
            "result_state": "empty",
            "diagnostics": {"returned_count": 0},
        },
        [],
        status="running",
    )

    assert summary["items_count"] == 0
    assert summary["match_status"] == "empty"
    assert summary["result_state"] == "running"
    assert summary["diagnostics"]["result_state"] == "running"
    assert summary["result_projection"]["terminal"] is False


def test_empty_terminal_search_is_explicitly_empty() -> None:
    summary = project_session_result_summary(
        {"kind": "kol_recall", "recall_snapshot_attached": True},
        [],
        status="partial",
    )

    assert summary["returned_count"] == 0
    assert summary["result_state"] == "empty"
    assert summary["result_projection"]["terminal"] is True


def test_projection_counts_each_ui_lane_without_counting_duplicate_lane_rows() -> None:
    duplicate = _candidate(1)
    duplicate_case_variant = {
        **_candidate(2),
        "source_url": "https://youtube.com/@CREATOR-1",
        "payload": {"platform": "youtube", "handle": "CREATOR-1"},
    }
    recall = _candidate(3, item_type="recall_candidate")
    online = _candidate(4, item_type="online_qualified_candidate")

    summary = project_session_result_summary(
        {"kind": "kol_recall"},
        [duplicate, duplicate_case_variant, recall, online],
        status="partial",
    )

    assert summary["items_count"] == 3
    assert summary["result_projection"]["by_lane"] == {
        "recall": 1,
        "discovery": 1,
        "online": 1,
    }
    assert summary["result_state"] == "partial"


def test_non_search_summary_is_left_unchanged() -> None:
    original = {"kind": "url_video", "result_state": "ready"}
    assert project_session_result_summary(original, [], status="ready") == original


def test_read_projection_keeps_local_strict_count_separate_from_session_total() -> None:
    session = {
        "status": "ready",
        "result_summary": {
            "kind": "kol_recall",
            "recall_snapshot_attached": True,
            "diagnostics": {"returned_count": 0},
            "local_qualification": {
                "schema": "smart_local_qualified_v2",
                "policy": {"target_count": 30},
                "qualified_count": 0,
                "returned_count": 0,
            },
        },
    }
    strict_recall = _candidate(1, item_type="recall_candidate")
    strict_recall["payload"]["counts_toward_target"] = True
    visible = [
        strict_recall,
        _candidate(2, item_type="new_creator"),
        _candidate(3, item_type="new_creator"),
    ]

    search_sessions._refresh_visible_recall_summary(session, visible)

    summary = session["result_summary"]
    assert summary["items_count"] == 3
    assert summary["returned_count"] == 3
    assert summary["diagnostics"]["returned_count"] == 3
    assert summary["local_qualification"]["returned_count"] == 1
    assert summary["local_qualification"]["qualified_count"] == 1


def test_read_projection_never_counts_growth_supplements_as_strict_qualified() -> None:
    session = {
        "status": "ready",
        "result_summary": {
            "kind": "kol_recall",
            "recall_snapshot_attached": True,
            "local_qualification": {
                "schema": "smart_local_qualified_v2",
                "policy": {"target_count": 30},
                "status": "shortfall",
                "qualified_count": 0,
                "qualified_returned_count": 0,
                "returned_count": 30,
                "shortfall": 30,
                "funnel": {"qualified": 0, "returned": 30},
            },
        },
    }
    visible = []
    for item_id in range(1, 31):
        item = _candidate(item_id, item_type="recall_candidate")
        item["payload"].update(
            {
                "counts_toward_target": False,
                "precision_match": False,
                "growth_qualification_pass": False,
                "growth_qualification_state": "evidence_pending",
            }
        )
        visible.append(item)

    search_sessions._refresh_visible_recall_summary(session, visible)

    summary = session["result_summary"]
    contract = summary["local_qualification"]
    assert summary["returned_count"] == 30
    assert contract["returned_count"] == 30
    assert contract["qualified_count"] == 0
    assert contract["qualified_returned_count"] == 0
    assert contract["shortfall"] == 30
    assert contract["status"] == "shortfall"
    assert contract["funnel"] == {"qualified": 0, "returned": 30}


def test_read_projection_preserves_legacy_strict_rows_beside_explicit_supplement() -> None:
    session = {
        "status": "ready",
        "result_summary": {
            "kind": "kol_recall",
            "recall_snapshot_attached": True,
            "local_qualification": {
                "schema": "smart_local_qualified_v2",
                "policy": {"target_count": 30},
                "qualified_count": 29,
                "qualified_returned_count": 29,
                "returned_count": 30,
                "funnel": {"qualified": 29, "returned": 30},
            },
        },
    }
    visible = [_candidate(item_id, item_type="recall_candidate") for item_id in range(1, 30)]
    supplement = _candidate(30, item_type="recall_candidate")
    supplement["payload"]["counts_toward_target"] = False
    visible.append(supplement)

    search_sessions._refresh_visible_recall_summary(session, visible)

    contract = session["result_summary"]["local_qualification"]
    assert contract["returned_count"] == 30
    assert contract["qualified_count"] == 29
    assert contract["qualified_returned_count"] == 29
    assert contract["shortfall"] == 1
