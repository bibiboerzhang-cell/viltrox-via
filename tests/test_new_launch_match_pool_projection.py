"""New-launch candidate projection must not transfer provider payloads in PostgreSQL."""
from __future__ import annotations

import json
from typing import Any

from app.domains.recommendations import new_launch_match_helpers as helpers


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.sql = ""

    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
        self.sql = sql
        return _Rows(self.rows)


def test_pool_projection_extracts_only_low_reach_flag_in_postgres(monkeypatch) -> None:
    connection = _Connection(
        [{
            "id": 1,
            "platform": "youtube",
            "handle": "creator",
            "display_name": "Creator",
            "country": "US",
            "source_ref": "legacy:1",
            "sync_status": "ready",
            "low_reach_flagged": True,
            "contact_has_email": True,
            "contact_has_phone": False,
            "followers": 2,
            "avg_views": 0,
            "avg_comments": 0,
            "engagement_rate": 0,
        }]
    )
    monkeypatch.setattr(helpers, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(helpers, "get_conn", lambda: connection)

    result = helpers._pool_by_source_ref()

    assert result["legacy:1"]["low_reach_flagged"] is True
    assert helpers._contact_score("available_restricted", result["legacy:1"]) == (
        7,
        "email_available_restricted",
    )
    select_list = connection.sql.split("FROM vkpi_kol_pool", 1)[0]
    assert "sync_status, raw_platform_data," not in " ".join(select_list.split())
    assert "AS low_reach_flagged" in select_list
    assert "AS contact_has_email" in select_list
    assert "AS contact_has_phone" in select_list
    assert "POSITION" in select_list
    assert "sync_status, raw_platform_data," not in select_list


def test_pool_projection_keeps_portable_json_path_for_sqlite(monkeypatch) -> None:
    raw = '{"low_reach":{"flag":true}}'
    connection = _Connection(
        [{
            "id": 1,
            "platform": "youtube",
            "handle": "creator",
            "display_name": "Creator",
            "country": "US",
            "source_ref": "legacy:1",
            "sync_status": "ready",
            "raw_platform_data": raw,
            "followers": 2,
            "avg_views": 0,
            "avg_comments": 0,
            "engagement_rate": 0,
        }]
    )
    monkeypatch.setattr(helpers, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(helpers, "get_conn", lambda: connection)

    result = helpers._pool_by_source_ref()

    assert result["legacy:1"]["raw_platform_data"] == raw
    assert "sync_status, raw_platform_data," in connection.sql


def test_projected_contact_flags_match_raw_contact_score_contract() -> None:
    cases = [
        ({"contact_has_email": True, "contact_has_phone": True}, (10, "email_and_phone_available_restricted")),
        ({"contact_has_email": True, "contact_has_phone": False}, (7, "email_available_restricted")),
        ({"contact_has_email": False, "contact_has_phone": True}, (4, "available_restricted")),
        ({"contact_has_email": False, "contact_has_phone": False}, (4, "available_restricted")),
    ]
    for flags, expected in cases:
        raw_pool = {"raw_platform_data": json.dumps(flags)}
        assert helpers._contact_score("available_restricted", raw_pool) == expected
        assert helpers._contact_score("available_restricted", flags) == expected
