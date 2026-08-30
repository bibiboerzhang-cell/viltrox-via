from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.domains.memory import agent_memory_writer
from app.domains.projects import workflow_projects_kols as workflow


class _Result:
    def __init__(
        self,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.row = row
        self.rows = list(rows or [])

    def fetchone(self) -> dict[str, Any] | None:
        return self.row

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.rows)


class _Connection:
    def __init__(self, events: list[Any], *, fail_touch: bool = False) -> None:
        self.events = events
        self.fail_touch = fail_touch
        self.commits = 0

    def execute(self, sql: str, params: Any = ()) -> _Result:
        compact = " ".join(str(sql).split())
        bound = tuple(params)
        if compact.startswith("SELECT id, stage_status FROM vkpi_projects"):
            self.events.append("project:lock")
            return _Result(row={"id": 3, "stage_status": "active"})
        if compact.startswith("SELECT id FROM vkpi_kol_pool WHERE id IN"):
            self.events.append(("pool:existing", bound))
            return _Result(rows=[{"id": 11}, {"id": 12}])
        if compact.startswith("SELECT p.id AS kol_pool_id, c.staff_id AS staff_id"):
            self.events.append(("claims:read", bound))
            return _Result(rows=[{"kol_pool_id": 11, "staff_id": 7}])
        if compact.startswith("SELECT id, COALESCE(display_name, handle, '') AS label"):
            self.events.append(("pool:labels", bound))
            return _Result(rows=[{"id": 11, "label": "Creator Eleven"}])
        if compact.startswith("INSERT INTO vkpi_project_kol_assignments"):
            self.events.append((f"assignment:{bound[1]}", bound[2]))
            return _Result(row={"id": 101}) if bound[1] == 11 else _Result()
        if compact == "SAVEPOINT vkpi_project_touch":
            self.events.append("touch:savepoint")
            return _Result()
        if compact.startswith("INSERT INTO vkpi_kol_pool_touches"):
            self.events.append(("touch:insert", bound[0], bound[1]))
            if self.fail_touch:
                raise RuntimeError("touch table unavailable")
            return _Result()
        if compact == "ROLLBACK TO SAVEPOINT vkpi_project_touch":
            self.events.append("touch:rollback")
            return _Result()
        if compact == "RELEASE SAVEPOINT vkpi_project_touch":
            self.events.append("touch:release")
            return _Result()
        if compact.startswith("UPDATE vkpi_projects SET updated_at"):
            self.events.append("project:update")
            return _Result()
        if compact.startswith("INSERT INTO vkpi_kol_pool_favorites"):
            self.events.append(("favorite", bound[0], bound[1]))
            return _Result()
        raise AssertionError(f"unexpected SQL: {compact}")

    def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")


class _FeedbackSink:
    def __init__(self, conn: _Connection, events: list[Any]) -> None:
        self.conn = conn
        self.events = events

    def record_pool_action(
        self,
        kol_pool_id: Any,
        action: str,
        *,
        staff: dict[str, Any] | None = None,
        note: str = "",
        payload: dict[str, Any] | None = None,
        source: str = "",
    ) -> dict[str, Any]:
        assert self.conn.commits == 1
        self.events.append(
            (
                "feedback",
                int(kol_pool_id),
                action,
                payload,
                source,
                staff,
                note,
            )
        )
        return {"recorded": True}


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_touch: bool = False,
    occupancy: dict[int, dict[str, Any]] | None = None,
    can_view_all: bool = False,
) -> tuple[_Connection, list[Any], _FeedbackSink]:
    events: list[Any] = []
    conn = _Connection(events, fail_touch=fail_touch)
    monkeypatch.setattr(
        workflow,
        "ensure_vkpi_schema",
        lambda: events.append("schema"),
    )
    monkeypatch.setattr(
        workflow.scope,
        "assert_project_access",
        lambda *_args, **_kwargs: events.append("access"),
    )
    monkeypatch.setattr(
        workflow.scope,
        "can_view_all",
        lambda *_args: can_view_all,
    )
    monkeypatch.setattr(workflow, "get_conn", lambda: conn)
    monkeypatch.setattr(
        workflow,
        "_locked_pool_claim_occupancy",
        lambda _conn, ids: (
            events.append(("occupancy", tuple(sorted(ids))))
            or dict(occupancy or {})
        ),
    )
    monkeypatch.setattr(
        workflow,
        "_staff_display_names",
        lambda _conn, ids: events.append(("staff:names", tuple(ids)))
        or {9: "Occupied Owner"},
    )
    monkeypatch.setattr(
        workflow,
        "utcnow",
        lambda: datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        agent_memory_writer,
        "record_kol_signal",
        lambda kol_pool_id, action, **kwargs: (
            events.append(("memory", kol_pool_id, action, kwargs)),
            {"recorded": True},
        )[1],
    )
    monkeypatch.setattr(
        workflow,
        "_log_project_audit",
        lambda **kwargs: events.append(("audit", kwargs)),
    )
    return conn, events, _FeedbackSink(conn, events)


def test_add_project_kols_preserves_transaction_and_post_commit_effect_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, events, feedback = _install(monkeypatch)
    staff = {"id": 5, "role": "staff"}

    result = workflow.add_project_kols(
        3,
        {"kol_pool_ids": [11, "12", 11, 13]},
        staff=staff,
        feedback_sink=feedback,
    )

    assert result == {
        "project_id": 3,
        "requested": 3,
        "inserted": 1,
        "skipped_existing": 1,
        "missing_kol_pool_ids": [13],
        "forced_claim_conflicts": [],
    }
    assert conn.commits == 1
    assert events[:13] == [
        "schema",
        "access",
        "project:lock",
        ("pool:existing", (11, 12, 13)),
        ("claims:read", (11, 12)),
        ("occupancy", (11, 12)),
        ("assignment:11", 7),
        "touch:savepoint",
        ("touch:insert", 11, 7),
        "touch:release",
        ("assignment:12", 5),
        "project:update",
        ("favorite", 11, 5),
    ]
    assert events[13:15] == [("favorite", 12, 5), "commit"]
    assert events[15][0:3] == ("memory", 11, "add_to_project")
    assert events[16][0:3] == ("feedback", 11, "touch")
    assert events[17][0:3] == ("feedback", 11, "favorite")
    assert events[18][0:3] == ("feedback", 12, "favorite")
    assert events[19][0] == "audit"
    audit = events[19][1]
    assert audit["metadata"] == {
        "kol_pool_ids": [11, 12],
        "assigned_staff_id_fallback": 5,
        "claim_owner_by_pool": {"11": 7},
        "missing_kol_pool_ids": [13],
        "skipped_existing": 1,
        "force": False,
        "forced_claim_conflicts": [],
    }


def test_touch_failure_rolls_back_savepoint_but_commits_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, events, feedback = _install(monkeypatch, fail_touch=True)

    result = workflow.add_project_kols(
        3,
        {"kol_pool_ids": [11]},
        staff={"id": 5, "role": "staff"},
        feedback_sink=feedback,
    )

    assert result["inserted"] == 1
    assert conn.commits == 1
    touch_events = [
        event
        for event in events
        if (isinstance(event, str) and event.startswith("touch:"))
        or (isinstance(event, tuple) and str(event[0]).startswith("touch:"))
    ]
    assert touch_events == [
        "touch:savepoint",
        ("touch:insert", 11, 7),
        "touch:rollback",
        "touch:release",
    ]
    assert events.index("commit") < next(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "feedback"
    )


def test_claim_conflict_rejects_before_assignment_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, events, _feedback = _install(
        monkeypatch,
        occupancy={
            11: {
                "staff_id": 9,
                "source": "claim",
                "claim_id": 44,
                "project_id": 8,
            }
        },
    )

    with pytest.raises(
        ValueError,
        match="Creator Eleven.*Occupied Owner.*force=true",
    ):
        workflow.add_project_kols(
            3,
            {"kol_pool_ids": [11]},
            staff={"id": 5, "role": "staff"},
        )

    assert conn.commits == 0
    assert not any(
        isinstance(event, tuple) and str(event[0]).startswith("assignment:")
        for event in events
    )
    assert ("pool:labels", (11,)) in events


def test_manager_force_preserves_conflict_evidence_and_claim_owner_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, events, feedback = _install(
        monkeypatch,
        occupancy={
            11: {
                "staff_id": 9,
                "source": "claim",
                "claim_id": 44,
                "project_id": 8,
            }
        },
        can_view_all=True,
    )

    result = workflow.add_project_kols(
        3,
        {"kol_pool_ids": [11], "force": True},
        staff={"id": 5, "role": "manager"},
        feedback_sink=feedback,
    )

    assert result["forced_claim_conflicts"] == [
        {
            "kol_pool_id": 11,
            "occupied_by_staff_id": 9,
            "occupied_by_name": "Occupied Owner",
            "claim_source": "claim",
            "claim_id": 44,
            "occupied_project_id": 8,
        }
    ]
    assert ("assignment:11", 7) in events
    audit = next(event[1] for event in events if event[0] == "audit")
    assert audit["metadata"]["force"] is True
    assert audit["metadata"]["forced_claim_conflicts"] == result["forced_claim_conflicts"]
