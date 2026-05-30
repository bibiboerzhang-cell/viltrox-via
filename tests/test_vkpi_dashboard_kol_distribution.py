from app.api.routers import vkpi_dashboard_staff
from app.domains.dashboard import kol_distribution as dashboard_kol_distribution
from app.services.vkpi.country_coords import country_geo, resolve_country_code


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
    assert resolve_country_code("中国台湾") == "TW"
    assert country_geo("TW")["name"] == "China TW"
    assert country_geo("HK")["name"] == "China HK"
    assert resolve_country_code("请提供具体的正文内容") == ""


def test_dashboard_kol_distribution_maps_known_countries(monkeypatch):
    monkeypatch.setattr(dashboard_kol_distribution, "get_conn", lambda: _FakeConn())
    monkeypatch.setattr(
        dashboard_kol_distribution.kol_pool,
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


def test_dashboard_kol_distribution_pack_is_versioned_and_cached(monkeypatch):
    calls = {"count": 0}
    dashboard_kol_distribution._MAP_PACK_CACHE.clear()

    def fake_distribution(limit=250):
        calls["count"] += 1
        return {
            "mapped_kol_count": 2,
            "source_country_kol_count": 2,
            "total_pool_rows": 2,
            "missing_country_count": 0,
            "country_count": 1,
            "city_count": 1,
            "city_mapped_kol_count": 2,
            "unmapped_kol_count": 0,
            "countries": [
                {
                    "code": "US",
                    "name": "United States",
                    "lat": 39.8,
                    "lng": -98.6,
                    "count": 2,
                    "cities": [{"name": "Los Angeles", "lat": 34.05, "lng": -118.24, "count": 2}],
                }
            ],
            "data_source": "test",
            "is_real": True,
        }

    monkeypatch.setattr(dashboard_kol_distribution, "build_dashboard_kol_distribution", fake_distribution)

    first = dashboard_kol_distribution.build_dashboard_kol_distribution_pack(limit=250)
    second = dashboard_kol_distribution.build_dashboard_kol_distribution_pack(limit=250)

    assert first["schema_version"] == 1
    assert first["resource"] == "dashboard.kol_distribution_pack"
    assert first["stats"]["mapped_kol_count"] == 2
    assert first["countries"][0]["cities"][0]["name"] == "Los Angeles"
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert calls["count"] == 1
