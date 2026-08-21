from __future__ import annotations

from fastapi import HTTPException
import pytest

from app.api.routers import vkpi_launch
from app.domains.access import scope


def test_launch_plan_checks_project_read_scope_before_loading(monkeypatch):
    calls: list[tuple[int, object, bool]] = []
    loaded: list[int] = []
    staff = {"id": 7, "role": "employee"}

    def deny(project_id, actor, *, write=False):
        calls.append((project_id, actor, write))
        raise scope.ScopeDenied("project scope denied")

    monkeypatch.setattr(vkpi_launch.scope, "assert_project_access", deny)
    monkeypatch.setattr(
        vkpi_launch,
        "generate_launch_plan",
        lambda project_id, **_kwargs: loaded.append(project_id),
    )

    with pytest.raises(HTTPException) as caught:
        vkpi_launch.post_launch_plan(41, {}, staff=staff)

    assert caught.value.status_code == 403
    assert caught.value.detail == "launch_project_read_forbidden"
    assert calls == [(41, staff, False)]
    assert loaded == []


def test_launch_plan_uses_scoped_project_and_bounded_limit(monkeypatch):
    calls: list[tuple[int, object, bool]] = []
    staff = {"id": 7, "role": "employee"}

    monkeypatch.setattr(
        vkpi_launch.scope,
        "assert_project_access",
        lambda project_id, actor, *, write=False: calls.append((project_id, actor, write)),
    )
    monkeypatch.setattr(
        vkpi_launch,
        "generate_launch_plan",
        lambda project_id, *, candidate_limit: {
            "project_id": project_id,
            "candidate_limit": candidate_limit,
        },
    )

    result = vkpi_launch.post_launch_plan(41, {"candidate_limit": "9"}, staff=staff)

    assert calls == [(41, staff, False)]
    assert result == {"project_id": 41, "candidate_limit": 9}
