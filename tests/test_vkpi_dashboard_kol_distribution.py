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
        # C3 员工轻隔离:签名新增 kol_ids_sql(staff={} 走全局,值恒 None)
        lambda _conn, limit=200, kol_ids_sql=None: [
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

    def fake_distribution(limit=250, staff_scope_id=None):
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


def test_map_staff_scope_id_is_server_derived():
    """C3 员工轻隔离:地图 scope 全由服务端从鉴权 staff 推导,客户端无参可传。"""
    employee = {"id": 7, "role": "member", "is_owner": 0}
    owner = {"id": 1, "role": "manager", "is_owner": 1}
    assert vkpi_dashboard_staff._map_staff_scope_id(employee) == 7
    assert vkpi_dashboard_staff._map_staff_scope_id(owner) is None


def test_dashboard_kol_distribution_pack_cache_isolated_per_scope(monkeypatch):
    """C3 员工轻隔离:员工包与全局包各自缓存,绝不互相串包。"""
    calls = {"count": 0}
    dashboard_kol_distribution._MAP_PACK_CACHE.clear()

    def fake_distribution(limit=250, staff_scope_id=None):
        calls["count"] += 1
        return {
            "mapped_kol_count": 1 if staff_scope_id else 5,
            "countries": [],
            "data_source": "test",
            "is_real": True,
            "scope": {
                "mode": "staff" if staff_scope_id else "global",
                "staff_scope_id": staff_scope_id,
            },
        }

    monkeypatch.setattr(dashboard_kol_distribution, "build_dashboard_kol_distribution", fake_distribution)

    global_pack = dashboard_kol_distribution.build_dashboard_kol_distribution_pack(limit=250)
    staff_pack = dashboard_kol_distribution.build_dashboard_kol_distribution_pack(limit=250, staff_scope_id=7)

    assert calls["count"] == 2
    assert global_pack["scope"]["mode"] == "global"
    assert staff_pack["scope"]["staff_scope_id"] == 7
    assert staff_pack["snapshot_id"].endswith("-s7")
    assert global_pack["stats"]["mapped_kol_count"] == 5
    assert staff_pack["stats"]["mapped_kol_count"] == 1

    staff_again = dashboard_kol_distribution.build_dashboard_kol_distribution_pack(limit=250, staff_scope_id=7)
    assert staff_again["cache"]["hit"] is True
    assert calls["count"] == 2
