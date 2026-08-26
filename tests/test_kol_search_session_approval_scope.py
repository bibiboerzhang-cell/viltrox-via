from __future__ import annotations

import json
from typing import Any

import pytest

from app.api.routers import vkpi_kol_pool_search
from app.domains.kol import search_sessions
from app.domains.kol.search_sessions_approval import _strict_gate_passed
from app.domains.kol.search_sessions_attach import _safe_gate_evidence
from app.domains.projects import workflow_projects


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.rows[0]) if self.rows else None


def _session_row(*, strict: bool = False, online: bool = False) -> dict[str, Any]:
    summary = {
        "local_qualification": {
            "schema": "smart_local_qualified_v2",
            "policy": {"policy_version": 2, "server_owned": True},
        }
    } if strict else {}
    if online:
        summary["online_qualification"] = {
            "schema": "smart_online_net_new_qualified_v1",
            "policy_version": 1,
            "server_owned": True,
            "terminal": True,
            "snapshot_complete": True,
            "snapshot_id": "snapshotabc123",
            "snapshot_revision": 1,
            "target_count": 30,
        }
    return {
        "id": 51,
        "query_text": "portrait creators",
        "query_type": "text_recall",
        "source": "test",
        "status": "ready",
        "created_by": 7,
        "input_payload_json": "{}",
        "result_summary_json": json.dumps(summary),
        "approved_kol_ids": "[]",
    }


class _ApprovalConn:
    def __init__(self, *, session: dict[str, Any], items: list[dict[str, Any]]) -> None:
        self.session = dict(session)
        self.items = list(items)
        self.updated_params: tuple[Any, ...] | None = None
        self.committed = False

    def execute(self, sql: str, params: tuple[Any, ...]) -> _Cursor:
        compact = " ".join(sql.split())
        if compact.startswith("SELECT * FROM vkpi_kol_search_sessions"):
            return _Cursor([self.session])
        if "FROM vkpi_kol_search_session_items i" in compact:
            return _Cursor(self.items)
        if compact.startswith("UPDATE vkpi_kol_search_sessions"):
            self.updated_params = params
            updated = dict(self.session)
            updated["approved_kol_ids"] = params[0]
            updated["result_summary_json"] = params[1]
            return _Cursor([updated])
        raise AssertionError(f"unexpected SQL: {compact}")

    def commit(self) -> None:
        self.committed = True


def _candidate(kol_pool_id: int, *, passed: bool | None = True, status: str = "matched") -> dict[str, Any]:
    gate = {
        "schema": "smart_local_gate_evidence_v2",
        "passed": passed,
        **{
            field: {"passed": passed}
            for field in (
                "account_quality", "followers", "activity", "market",
                "language", "profile_type", "platform", "relevance",
            )
        },
    }
    gate["relevance"]["evidence"] = [
        {"field": "bio", "term": "lens", "source": "server_profile_evidence"}
    ]
    proof = {} if passed is None else {"qualification_evidence": gate}
    return {
        "kol_pool_id": kol_pool_id,
        "status": status,
        "payload_json": json.dumps(proof),
    }


def _online_candidate(kol_pool_id: int, *, passed: bool = True) -> dict[str, Any]:
    candidate = _candidate(kol_pool_id, passed=passed, status="ready")
    payload = json.loads(candidate["payload_json"])
    payload.update({
        "origin_lane": "online",
        "source": "platform_discovery_strict",
        "qualification_status": "accepted",
        "canonical_fingerprint": f"{kol_pool_id:064x}",
        "snapshot_id": "snapshotabc123",
        "snapshot_revision": 1,
        "server_rank": min(kol_pool_id, 30),
        "global_unique_rank": min(kol_pool_id + 30, 60),
    })
    proof = payload["qualification_evidence"]
    proof.update({
        "kol_pool_id": kol_pool_id,
        "canonical_fingerprint": payload["canonical_fingerprint"],
        "snapshot_id": payload["snapshot_id"],
        "snapshot_revision": payload["snapshot_revision"],
        "server_rank": payload["server_rank"],
        "global_unique_rank": payload["global_unique_rank"],
    })
    candidate["item_type"] = "online_qualified_candidate"
    candidate["payload_json"] = json.dumps(payload)
    return candidate


def test_strict_approval_rejects_relevance_that_disappears_during_safe_projection() -> None:
    raw = json.loads(_candidate(11)["payload_json"])["qualification_evidence"]
    raw["relevance"]["evidence"] = [
        {"field": "bio", "term": "private", "source": "unknown"}
    ]
    safe = _safe_gate_evidence(raw, allowed_terms={"lens"})
    assert safe["relevance"] == {
        "passed": False,
        "evidence": [],
        "source": "",
    }
    assert safe["passed"] is False
    assert "low_relevance" in safe["rejection_reasons"]
    assert _strict_gate_passed(safe) is False


def test_approval_rejects_arbitrary_and_cross_session_pool_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _ApprovalConn(session=_session_row(), items=[_candidate(11), _candidate(12)])
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    result = search_sessions.approve_session(
        51,
        kol_pool_ids=[11, 999, 12, 11],
        staff={"id": 7},
    )

    assert result["approved_kol_ids"] == [11, 12]
    assert result["skipped_not_in_session"] == [999]
    assert result["skipped_not_in_pool"] == [999]
    assert conn.committed is True


def test_strict_local_approval_requires_server_passed_qualification_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _ApprovalConn(
        session=_session_row(strict=True),
        items=[_candidate(11, passed=True), _candidate(12, passed=False), _candidate(13, passed=None)],
    )
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    result = search_sessions.approve_session(51, kol_pool_ids=[11, 12, 13], staff={"id": 7})

    assert result["approved_kol_ids"] == [11]
    assert result["skipped_failed_qualification"] == [12, 13]
    approval = result["result_summary"]["approval"]
    assert approval["strict_local_proof_required"] is True


def test_online_approval_requires_owned_strict_contract_and_full_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_origin = _online_candidate(14)
    invalid_payload = json.loads(invalid_origin["payload_json"])
    invalid_payload["source"] = "platform_discovery"
    invalid_origin["payload_json"] = json.dumps(invalid_payload)
    conn = _ApprovalConn(
        session=_session_row(online=True),
        items=[_online_candidate(11), _online_candidate(12, passed=False), invalid_origin],
    )
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    result = search_sessions.approve_session(51, kol_pool_ids=[11, 12, 14, 999], staff={"id": 7})

    assert result["approved_kol_ids"] == [11]
    assert result["skipped_failed_qualification"] == [12, 14]
    assert result["skipped_not_in_session"] == [999]
    assert result["result_summary"]["approval"]["strict_online_proof_required"] is True


def test_online_approval_rejects_copied_or_snapshot_mismatched_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [_online_candidate(kol_pool_id) for kol_pool_id in range(11, 17)]

    def mutate(index: int, section: str, field: str, value: Any) -> None:
        payload = json.loads(candidates[index]["payload_json"])
        target = payload if section == "payload" else payload["qualification_evidence"]
        target[field] = value
        candidates[index]["payload_json"] = json.dumps(payload)

    mutate(0, "proof", "kol_pool_id", 99)
    mutate(1, "proof", "canonical_fingerprint", "f" * 64)
    mutate(2, "payload", "snapshot_id", "copied-snapshot")
    mutate(3, "proof", "snapshot_revision", 2)
    mutate(4, "proof", "server_rank", 1)
    copied = json.loads(candidates[5]["payload_json"])
    copied["qualification_evidence"] = json.loads(candidates[0]["payload_json"])["qualification_evidence"]
    candidates[5]["payload_json"] = json.dumps(copied)

    conn = _ApprovalConn(session=_session_row(online=True), items=candidates)
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    result = search_sessions.approve_session(
        51,
        kol_pool_ids=list(range(11, 17)),
        staff={"id": 7},
    )

    assert result["approved_kol_ids"] == []
    assert result["skipped_failed_qualification"] == list(range(11, 17))


def test_project_draft_ignores_request_body_candidate_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attached: dict[str, Any] = {}
    monkeypatch.setattr(
        search_sessions,
        "get_session",
        lambda *_args, **_kwargs: {
            "id": 51,
            "status": "ready",
            "query_text": "portrait",
            "approved_kol_ids": [11],
            "input_payload": {},
            "result_summary": {},
        },
    )
    monkeypatch.setattr(workflow_projects, "create_project", lambda *_args, **_kwargs: {"id": 8, "project_uid": "P8", "stage": "discovery"})
    monkeypatch.setattr(
        workflow_projects,
        "add_project_kols",
        lambda _project_id, body, **_kwargs: attached.update(body) or {"inserted": 1},
    )
    monkeypatch.setattr(search_sessions, "update_session_result_summary", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "app.domains.projects.cost_estimate.estimate_cost_for_kols",
        lambda *_args, **_kwargs: {},
    )

    result = workflow_projects.create_project_draft_from_session(
        51,
        {"kol_pool_ids": [999]},
        staff={"id": 7},
    )

    assert attached["kol_pool_ids"] == [11]
    assert result["requested_kol_count"] == 1


def test_outreach_ignores_request_body_candidate_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_search_sessions,
        "get_session",
        lambda *_args, **_kwargs: {
            "query_text": "portrait",
            "approved_kol_ids": [11],
            "input_payload": {},
            "result_summary": {},
        },
    )

    def generate(ids: list[int], **kwargs: Any) -> dict[str, Any]:
        captured.update(ids=ids, **kwargs)
        return {"messages": []}

    monkeypatch.setattr(vkpi_kol_pool_search.project_outreach, "generate_outreach", generate)

    vkpi_kol_pool_search.generate_kol_search_session_outreach(
        51,
        body={"kol_pool_ids": [999]},
        staff={"id": 7},
    )

    assert captured["ids"] == [11]


def _deferred_candidate(kol_pool_id: int) -> dict[str, Any]:
    """A real "we never crawled this creator" proof, taken from the qualifier."""
    from datetime import datetime, timezone

    from app.domains.kol import profile_recall_qualification

    item = {
        "kol_pool_id": kol_pool_id,
        "handle": f"creator-{kol_pool_id}",
        "platform": "youtube",
        "bucket": "creator",
        "display_rank_score": 1.0,
        "match_evidence": [{"field": "bio", "term": "lens", "source": "server_profile_evidence"}],
    }
    row = {
        "kol_pool_id": kol_pool_id,
        "followers": 5_000,
        "country": "US",
        "language": "en",
        "profile_type": "creator",
        "platform": "youtube",
        "bio": "Independent photographer testing camera lenses in the field.",
        "raw_platform_data": {},
    }
    selected, _, _ = profile_recall_qualification.qualify_local_candidates(
        buckets={"creator": [item], "reviewer": []},
        rows_by_id={kol_pool_id: row},
        evidence_by_id={kol_pool_id: {}},
        policy=profile_recall_qualification.smart_local_policy(market="US", platforms=["youtube"]),
        creator_quota=30,
        reviewer_quota=0,
        as_of=datetime(2026, 8, 25, 12, tzinfo=timezone.utc),
    )
    proof = _safe_gate_evidence(selected[0]["qualification_evidence"], allowed_terms={"lens"})
    assert proof["deferred"] is True and proof["passed"] is False
    return {
        "kol_pool_id": kol_pool_id,
        "status": "matched",
        "payload_json": json.dumps({"qualification_evidence": proof}),
    }


def test_strict_local_approval_accepts_the_activity_unknown_bucket_it_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """占名额或不占名额都行,唯独不许「返回了却点不动」。

    活跃度未知的候选不计入 30 人目标数,但它确实被返回给了操作员,所以必须
    能勾选入库——并且在批准记录里单独记账,不与真·合格者混同。
    """
    conn = _ApprovalConn(
        session=_session_row(strict=True),
        items=[_candidate(11, passed=True), _deferred_candidate(12), _candidate(13, passed=False)],
    )
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    result = search_sessions.approve_session(51, kol_pool_ids=[11, 12, 13], staff={"id": 7})

    assert result["approved_kol_ids"] == [11, 12]
    assert result["skipped_failed_qualification"] == [13]
    assert result["approved_activity_unknown_ids"] == [12]
    approval = result["result_summary"]["approval"]
    assert approval["approved_activity_unknown_ids"] == [12]
    assert approval["approved_activity_unknown_count"] == 1
    assert approval["approved_activity_unknown_status"] == "activity_unknown_pending_fetch"
    assert approval["strict_local_proof_required"] is True


def test_activity_unknown_branch_never_launders_a_stale_or_broken_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one open gate is ``activity``; every other failure still fails closed."""
    from app.domains.kol.search_sessions_approval import _strict_gate_deferred

    deferred = json.loads(_deferred_candidate(12)["payload_json"])["qualification_evidence"]
    assert _strict_gate_deferred(deferred) is True

    stale = {
        **deferred,
        "activity": {**deferred["activity"], "known": True, "age_days": 200.0, "deferred": False},
    }
    assert _strict_gate_deferred(stale) is False

    broken_market = {**deferred, "market": {**deferred["market"], "passed": False}}
    assert _strict_gate_deferred(broken_market) is False

    no_evidence = {**deferred, "relevance": {**deferred["relevance"], "evidence": []}}
    assert _strict_gate_deferred(no_evidence) is False

    # A plain failed row must not be approvable just because someone stamps it.
    plain_failure = json.loads(_candidate(13, passed=False)["payload_json"])["qualification_evidence"]
    assert _strict_gate_deferred({**plain_failure, "deferred": True}) is False

    conn = _ApprovalConn(
        session=_session_row(strict=True),
        items=[
            {**_deferred_candidate(12), "payload_json": json.dumps({"qualification_evidence": stale})},
        ],
    )
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    result = search_sessions.approve_session(51, kol_pool_ids=[12], staff={"id": 7})
    assert result["approved_kol_ids"] == []
    assert result["skipped_failed_qualification"] == [12]
