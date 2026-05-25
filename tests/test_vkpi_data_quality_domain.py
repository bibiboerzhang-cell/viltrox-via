from app.api.routers import vkpi_data_quality
from app.domains import data_quality as data_quality_domain
from app.domains.data_quality import service as data_quality_service


def test_data_quality_domain_delegates_read_only_summary(monkeypatch):
    calls = {}

    def fake_list_issues(*, limit, staff):
        calls["limit"] = limit
        calls["staff"] = staff
        return {"status": "ok", "total_count": 0, "issues": []}

    staff = {"id": 7, "role": "manager"}
    monkeypatch.setattr(data_quality_service.checks, "list_issues", fake_list_issues)

    assert data_quality_domain.list_quality_issues(limit=12, staff=staff) == {
        "status": "ok",
        "total_count": 0,
        "issues": [],
    }
    assert calls == {"limit": 12, "staff": staff}


def test_data_quality_get_route_uses_domain_facade(monkeypatch):
    calls = {}

    def fake_list_quality_issues(*, limit, staff):
        calls["limit"] = limit
        calls["staff"] = staff
        return {"status": "ok", "total_count": 2, "issues": [{"id": "dq-1"}, {"id": "dq-2"}]}

    staff = {"id": 8, "role": "manager"}
    monkeypatch.setattr(vkpi_data_quality.data_quality_domain, "list_quality_issues", fake_list_quality_issues)

    assert vkpi_data_quality.data_quality_issues(limit=25, staff=staff)["total_count"] == 2
    assert calls == {"limit": 25, "staff": staff}
