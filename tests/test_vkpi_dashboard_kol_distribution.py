from app.api.routers import vkpi_dashboard_staff
from app.services.vkpi.country_coords import resolve_country_code


class _FakeResult:
    def fetchone(self):
        return {"n": 4}


class _FakeConn:
    def execute(self, *_args, **_kwargs):
        return _FakeResult()


def test_resolve_country_code_handles_noisy_values():
    assert resolve_country_code("美国/英国/波兰") == "US"
    assert resolve_country_code("Toronto") == "CA"
    assert resolve_country_code("迪拜") == "AE"
    assert resolve_country_code("请提供具体的正文内容") == ""


def test_dashboard_kol_distribution_maps_known_countries(monkeypatch):
    monkeypatch.setattr(vkpi_dashboard_staff, "get_conn", lambda: _FakeConn())
    monkeypatch.setattr(
        vkpi_dashboard_staff.kol_pool,
        "_country_distribution",
        lambda _conn, limit=200: [
            {"country_code": "US", "country_name": "United States", "kol_count": 2, "raw_values": ["美国"]},
            {"country_code": "迪拜", "country_name": "迪拜", "kol_count": 1, "raw_values": ["迪拜"]},
            {"country_code": "未知", "country_name": "未知", "kol_count": 1, "raw_values": ["未知"]},
        ],
    )

    payload = vkpi_dashboard_staff.dashboard_kol_distribution(limit=200, staff={})

    assert payload["is_real"] is True
    assert payload["mapped_kol_count"] == 3
    assert payload["source_country_kol_count"] == 4
    assert payload["missing_country_count"] == 0
    assert payload["unmapped_kol_count"] == 1
    assert [row["code"] for row in payload["countries"]] == ["US", "AE"]
