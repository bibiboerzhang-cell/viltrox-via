"""Owner-scope and durable-provider gates for Industry Automation."""
from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pytest

from app.domains.industry import access
from app.domains.industry import data as industry_data
from app.workers.tasks import provider_workflows


MEMBER_ID = 11
OTHER_ID = 22
USER_ID = 101
PROJECT_ID = 7
ACCOUNT_ID = 70


def _actor(
    *,
    staff_id: int = MEMBER_ID,
    user_id: int = USER_ID,
    role: str = "employee",
    active: bool = True,
    suspended_at: str | None = None,
    status: str = "approved",
    permission: str = "write",
) -> dict[str, Any]:
    return {
        "id": staff_id,
        "user_id": user_id,
        "role": role,
        "active": active,
        "suspended_at": suspended_at,
        "user_status": status,
        "permissions_json": {"vkpi": permission},
        "is_owner": 0,
    }


class _Rows:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self.row) if self.row is not None else None


class _Conn:
    def __init__(self) -> None:
        self.actors = {
            MEMBER_ID: _actor(),
            OTHER_ID: _actor(staff_id=OTHER_ID, user_id=202),
        }
        self.projects = {
            PROJECT_ID: {"id": PROJECT_ID, "owner_staff_id": MEMBER_ID, "is_active": True},
            8: {"id": 8, "owner_staff_id": OTHER_ID, "is_active": True},
        }
        self.accounts = {
            ACCOUNT_ID: {
                "id": ACCOUNT_ID,
                "project_id": PROJECT_ID,
                "platform": "youtube",
                "platform_user_id": "UC-test",
                "handle": "lens-reviewer",
                "profile_url": "https://www.youtube.com/@lens-reviewer",
                "crawl_enabled": True,
                "is_active": True,
            },
            80: {
                "id": 80,
                "project_id": 8,
                "platform": "instagram",
                "platform_user_id": "other",
                "handle": "other-owner",
                "profile_url": "https://www.instagram.com/other-owner",
                "crawl_enabled": True,
                "is_active": True,
            },
        }

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        compact = " ".join(str(sql).split())
        if "FROM staff s JOIN users u" in compact:
            return _Rows(self.actors.get(int(params[0])))
        if "FROM vkpi_industry_accounts a" in compact and "JOIN vkpi_industry_projects p" in compact:
            account = self.accounts.get(int(params[0]))
            if account is None:
                return _Rows(None)
            project = self.projects.get(int(account["project_id"]))
            if project is None:
                return _Rows(None)
            return _Rows(
                {
                    **account,
                    "account_is_active": account.get("is_active"),
                    "owner_staff_id": project.get("owner_staff_id"),
                    "project_is_active": project.get("is_active"),
                }
            )
        if "FROM vkpi_industry_projects WHERE id=" in compact:
            return _Rows(self.projects.get(int(params[0])))
        raise AssertionError(compact)


def test_member_scope_rejects_arbitrary_project_and_account_ids() -> None:
    conn = _Conn()
    member = _actor()
    assert access.assert_project_access(PROJECT_ID, member, conn=conn)["id"] == PROJECT_ID
    assert access.assert_account_access(ACCOUNT_ID, member, conn=conn)["id"] == ACCOUNT_ID

    with pytest.raises(access.IndustryAccessError) as project_denied:
        access.assert_project_access(8, member, conn=conn)
    assert project_denied.value.code == "industry_project_read_forbidden"

    with pytest.raises(access.IndustryAccessError) as account_denied:
        access.assert_account_access(80, member, write=True, conn=conn)
    assert account_denied.value.code == "industry_project_write_forbidden"


def test_manager_scope_and_validated_owner_assignment_are_preserved() -> None:
    conn = _Conn()
    manager = _actor(role="manager")
    assert access.assert_account_access(80, manager, write=True, conn=conn)["id"] == 80
    assert access.resolve_create_owner(
        {"owner_staff_id": OTHER_ID},
        manager,
        conn=conn,
    ) == OTHER_ID

    with pytest.raises(access.IndustryAccessError) as forged:
        access.resolve_create_owner(
            {"owner_staff_id": OTHER_ID},
            _actor(),
            conn=conn,
        )
    assert forged.value.code == "industry_project_owner_forgery_forbidden"

    with pytest.raises(access.IndustryAccessError) as missing:
        access.resolve_create_owner(
            {"owner_staff_id": 9999},
            manager,
            conn=conn,
        )
    assert missing.value.code == "industry_project_owner_invalid"


def test_member_project_list_is_owner_filtered_but_manager_list_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    class _ListConn:
        def execute(self, sql: str, params: tuple[Any, ...]) -> Any:
            calls.append((" ".join(str(sql).split()), tuple(params)))

            class _Many:
                @staticmethod
                def fetchall() -> list[Any]:
                    return []

            return _Many()

    monkeypatch.setattr(industry_data, "ensure_vkpi_product_industry_schema", lambda: None)
    monkeypatch.setattr(industry_data, "get_conn", lambda: _ListConn())

    industry_data.list_projects(staff=_actor())
    assert "owner_staff_id=?" in calls[-1][0]
    assert calls[-1][1][0] == MEMBER_ID

    industry_data.list_projects(staff=_actor(role="manager"))
    assert "owner_staff_id=?" not in calls[-1][0]

    industry_data.list_accounts(staff=_actor())
    assert "ip.owner_staff_id=?" in calls[-1][0]
    assert calls[-1][1][0] == MEMBER_ID

    industry_data.list_accounts(staff=_actor(role="manager"))
    assert "ip.owner_staff_id=?" not in calls[-1][0]


def test_refresh_payload_contains_only_ids_and_signed_target_binding() -> None:
    conn = _Conn()
    payload = access.build_refresh_payload(ACCOUNT_ID, staff=_actor(), conn=conn)

    assert payload["account_id"] == ACCOUNT_ID
    assert payload["project_id"] == PROJECT_ID
    assert payload["staff_id"] == MEMBER_ID
    assert payload["user_id"] == USER_ID
    assert "staff" not in payload
    assert "permissions_json" not in json.dumps(payload)
    actor = access.revalidate_refresh_payload(payload, conn=conn)
    assert actor["id"] == MEMBER_ID
    assert actor["user_id"] == USER_ID


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda data: data.update(account_id=80), "industry_refresh_payload_drifted"),
        (lambda data: data.update(project_id=8), "industry_refresh_payload_drifted"),
        (lambda data: data.update(staff_id=OTHER_ID), "industry_refresh_payload_drifted"),
        (lambda data: data.update(user_id=202), "industry_refresh_payload_drifted"),
        (
            lambda data: data[access.FENCE_KEY].update(signature="forged"),
            "industry_refresh_fence_invalid",
        ),
    ],
)
def test_refresh_payload_tampering_is_rejected(
    mutate,
    expected: str,
) -> None:
    conn = _Conn()
    payload = access.build_refresh_payload(ACCOUNT_ID, staff=_actor(), conn=conn)
    mutate(payload)
    with pytest.raises(access.IndustryAccessError) as raised:
        access.revalidate_refresh_payload(payload, conn=conn)
    assert raised.value.code == expected


def test_refresh_blocks_account_move_owner_transfer_and_actor_revocation() -> None:
    conn = _Conn()
    payload = access.build_refresh_payload(ACCOUNT_ID, staff=_actor(), conn=conn)

    moved = copy.deepcopy(conn.accounts[ACCOUNT_ID])
    moved["project_id"] = 8
    conn.accounts[ACCOUNT_ID] = moved
    with pytest.raises(access.IndustryAccessError) as moved_error:
        access.revalidate_refresh_payload(payload, conn=conn)
    assert moved_error.value.code == "industry_refresh_account_moved"

    conn = _Conn()
    payload = access.build_refresh_payload(ACCOUNT_ID, staff=_actor(), conn=conn)
    conn.projects[PROJECT_ID]["owner_staff_id"] = OTHER_ID
    with pytest.raises(access.IndustryAccessError) as owner_error:
        access.revalidate_refresh_payload(payload, conn=conn)
    assert owner_error.value.code == "industry_refresh_owner_revoked"

    conn = _Conn()
    payload = access.build_refresh_payload(ACCOUNT_ID, staff=_actor(), conn=conn)
    conn.actors[MEMBER_ID]["active"] = False
    with pytest.raises(access.IndustryAccessError) as actor_error:
        access.revalidate_refresh_payload(payload, conn=conn)
    assert actor_error.value.code == "industry_refresh_actor_inactive"

    conn = _Conn()
    payload = access.build_refresh_payload(ACCOUNT_ID, staff=_actor(), conn=conn)
    conn.accounts[ACCOUNT_ID]["handle"] = "tampered-target"
    with pytest.raises(access.IndustryAccessError) as identity_error:
        access.revalidate_refresh_payload(payload, conn=conn)
    assert identity_error.value.code == "industry_refresh_account_identity_drifted"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"active": False}, "industry_refresh_actor_inactive"),
        ({"suspended_at": "2026-08-21T00:00:00Z"}, "industry_refresh_actor_inactive"),
        ({"user_status": "disabled"}, "industry_refresh_actor_inactive"),
        ({"permissions_json": {"vkpi": "read"}}, "industry_refresh_permission_revoked"),
        ({"user_id": 999}, "industry_refresh_actor_changed"),
    ],
)
def test_refresh_rechecks_live_actor_state(
    changes: dict[str, Any],
    expected: str,
) -> None:
    conn = _Conn()
    payload = access.build_refresh_payload(ACCOUNT_ID, staff=_actor(), conn=conn)
    conn.actors[MEMBER_ID].update(changes)
    with pytest.raises(access.IndustryAccessError) as raised:
        access.revalidate_refresh_payload(payload, conn=conn)
    assert raised.value.code == expected


def test_explicit_server_capability_is_target_bound_and_http_dict_is_invalid() -> None:
    conn = _Conn()
    capability = access.issue_server_refresh_capability(
        account_id=ACCOUNT_ID,
        project_id=PROJECT_ID,
    )
    payload = access.build_refresh_payload(
        ACCOUNT_ID,
        server_capability=capability,
        conn=conn,
    )
    assert payload["staff_id"] is None
    assert payload["user_id"] is None
    assert access.revalidate_refresh_payload(payload, conn=conn)["server_owned"] is True

    with pytest.raises(access.IndustryAccessError) as forged:
        access.build_refresh_payload(
            ACCOUNT_ID,
            server_capability={"account_id": ACCOUNT_ID},  # type: ignore[arg-type]
            conn=conn,
        )
    assert forged.value.code == "industry_refresh_server_capability_invalid"


class _Queue:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, str, dict[str, Any]]] = []

    async def set_status(self, task_id: str, status: str, **extra: Any) -> None:
        self.statuses.append((task_id, status, extra))


def test_worker_revocation_is_terminal_and_provider_bomb_stays_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue()
    provider_calls: list[str] = []

    monkeypatch.setattr(
        access,
        "revalidate_refresh_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            access.IndustryAccessError("industry_refresh_permission_revoked")
        ),
    )
    monkeypatch.setattr(
        provider_workflows,
        "persist_job_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no result write")),
    )
    from app.domains.industry import snapshot_collector

    monkeypatch.setattr(
        snapshot_collector,
        "collect_account_snapshot",
        lambda *_args, **_kwargs: provider_calls.append("provider"),
    )

    asyncio.run(
        provider_workflows.process_industry_account_refresh_job(
            queue,
            {
                "task_id": "industry-denied",
                "job_type": "industry_account_refresh",
                "payload": {"account_id": ACCOUNT_ID},
            },
        )
    )

    assert provider_calls == []
    assert [status for _, status, _ in queue.statuses] == ["processing", "failed"]
    terminal = queue.statuses[-1][2]
    assert terminal["stage"] == "authorization_blocked"
    result = json.loads(terminal["result_json"])
    assert result["provider_calls_performed"] is False
    assert result["retryable"] is False


def test_worker_normal_mock_revalidates_checkpoints_and_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue()
    checkpoints: list[str] = []
    from app.domains.industry import snapshot_collector

    def revalidate(*_args, **_kwargs) -> dict[str, Any]:
        checkpoints.append("checked")
        return _actor()

    def collect(account_id: int, **kwargs: Any) -> dict[str, Any]:
        assert account_id == ACCOUNT_ID
        kwargs["authorization_checkpoint"]()
        kwargs["provider_call_started"]()
        kwargs["authorization_checkpoint"]()
        kwargs["authorization_scope_checkpoint"]()
        return {"status": "done", "sync_status": "synced", "account": {"id": ACCOUNT_ID}}

    monkeypatch.setattr(access, "revalidate_refresh_payload", revalidate)
    monkeypatch.setattr(snapshot_collector, "collect_account_snapshot", collect)
    monkeypatch.setattr(provider_workflows, "persist_job_result", lambda *_args: "/tmp/mock-result.json")

    asyncio.run(
        provider_workflows.process_industry_account_refresh_job(
            queue,
            {
                "task_id": "industry-normal",
                "job_type": "industry_account_refresh",
                "payload": {"account_id": ACCOUNT_ID},
            },
        )
    )

    assert len(checkpoints) == 4
    assert [status for _, status, _ in queue.statuses] == ["processing", "done"]
    assert queue.statuses[-1][2]["summary"] == "account=70 status=synced"
