"""Owner-scope regressions for search-session continuation and recovery."""
from __future__ import annotations

from typing import Any

import pytest

from app.api.routers import vkpi_kol_pool_helpers
from app.domains.kol import lookup_recovery, search_sessions


@pytest.mark.parametrize(
    "attach,query_text",
    [
        (vkpi_kol_pool_helpers._attach_smart_url_session, "https://youtube.com/@other"),
        (vkpi_kol_pool_helpers._attach_smart_recall_session, "26mm lens reviewer"),
    ],
)
def test_url_and_recall_cannot_attach_to_another_staff_session(
    monkeypatch: pytest.MonkeyPatch,
    attach: Any,
    query_text: str,
) -> None:
    calls: list[dict[str, Any]] = []

    def _deny(
        session_id: int,
        *,
        staff: dict[str, Any] | None = None,
        scope_to_staff: bool = False,
    ) -> dict[str, Any]:
        calls.append(
            {
                "session_id": session_id,
                "staff": staff,
                "scope_to_staff": scope_to_staff,
            }
        )
        raise LookupError(f"search session not found: {session_id}")

    monkeypatch.setattr(search_sessions, "get_session", _deny)

    with pytest.raises(LookupError, match="search session not found"):
        attach(
            body={"session_id": 91, "create_session": False},
            result={},
            query_text=query_text,
            staff={"id": 7, "role": "staff"},
        )

    assert calls == [
        {
            "session_id": 91,
            "staff": {"id": 7, "role": "staff"},
            "scope_to_staff": True,
        }
    ]


def test_lookup_recovery_resolves_only_current_staff_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def _deny(
        session_id: int,
        *,
        staff: dict[str, Any] | None = None,
        scope_to_staff: bool = False,
    ) -> dict[str, Any]:
        calls.append(
            {
                "session_id": session_id,
                "staff": staff,
                "scope_to_staff": scope_to_staff,
            }
        )
        raise LookupError(f"search session not found: {session_id}")

    monkeypatch.setattr(search_sessions, "get_session", _deny)

    with pytest.raises(LookupError, match="search session not found"):
        lookup_recovery.recover_session(
            91,
            staff={"id": 7, "role": "staff"},
        )

    assert calls == [
        {
            "session_id": 91,
            "staff": {"id": 7, "role": "staff"},
            "scope_to_staff": True,
        }
    ]
