from __future__ import annotations

from typing import Any

from app.api.routers import vkpi_weekly_reports
from app.domains.reports import weekly_generator


class _Cursor:
    def fetchone(self) -> None:
        return None


class _Connection:
    def execute(self, _sql: str, _params: tuple[Any, ...]) -> _Cursor:
        return _Cursor()


def test_weekly_domain_missing_report_returns_strict_typed_empty(monkeypatch) -> None:
    monkeypatch.setattr(weekly_generator, "ensure_vkpi_weekly_reports_schema", lambda: None)
    monkeypatch.setattr(weekly_generator, "get_conn", lambda: _Connection())

    result = weekly_generator.get_report(0, staff={"id": 1, "role": "admin"})

    assert result == {
        "status": "not_found",
        "data_status": "no_data",
        "report_id": 0,
        "reason": "report_not_found",
    }
    assert not {"title", "body_md", "staff_id", "template_key"}.intersection(result)


def test_weekly_router_preserves_strict_typed_empty(monkeypatch) -> None:
    expected = {
        "status": "not_found",
        "data_status": "no_data",
        "report_id": 0,
        "reason": "report_not_found",
    }
    captured: dict[str, Any] = {}

    def fake_get_report(report_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
        captured.update({"report_id": report_id, "staff": staff})
        return dict(expected)

    monkeypatch.setattr(vkpi_weekly_reports.weekly_report_generator, "get_report", fake_get_report)
    staff = {"id": 1, "role": "admin"}

    result = vkpi_weekly_reports.api_get_report(0, staff=staff)

    assert result == expected
    assert captured == {"report_id": 0, "staff": staff}
