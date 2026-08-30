from __future__ import annotations

import itertools
from typing import Any

from app.domains.projects import workflow, workflow_common
from app.shared.staff_identity import staff_id


def _legacy_staff_id(staff: dict[str, Any] | None) -> int:
    if not staff:
        return 0
    value = staff.get("id") or staff.get("staff_id") or staff.get("user_id")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def test_projects_public_staff_id_imports_remain_compatible() -> None:
    assert workflow_common.staff_id is staff_id
    assert workflow.staff_id is staff_id


def test_staff_identity_matches_legacy_precedence_and_coercion_exhaustively() -> None:
    values: tuple[Any, ...] = (None, "", 0, False, 7, "8", -2, "bad", 3.5, [], {})

    scenarios = 0
    for identity, staff_identity, user_identity in itertools.product(values, repeat=3):
        actor = {
            "id": identity,
            "staff_id": staff_identity,
            "user_id": user_identity,
        }
        assert staff_id(actor) == _legacy_staff_id(actor)
        scenarios += 1

    assert scenarios == 1331
    assert staff_id(None) == _legacy_staff_id(None) == 0
    assert staff_id({}) == _legacy_staff_id({}) == 0
