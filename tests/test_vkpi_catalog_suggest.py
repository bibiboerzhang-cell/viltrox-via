"""顶栏 Ask P1:GET /catalog/suggest 契约(员工可读 / ≤20 行 / 三列无敏感 / compat 占位)。"""
from __future__ import annotations

import inspect
import sqlite3
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import vkpi_catalog_suggest as router_mod  # noqa: E402
from app.core import release_validation  # noqa: E402
from app.db import connection as db_connection  # noqa: E402
from app.domains.products import catalog_suggest  # noqa: E402


_EMPLOYEE = {"staff_id": 7, "role": "employee", "is_owner": 0}


def _conn(*, with_lens_table: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_products (
            sku TEXT PRIMARY KEY,
            model_name TEXT NOT NULL,
            marketing_name TEXT,
            price_usd REAL,
            category_main TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO vkpi_products VALUES (?, ?, ?, ?, ?)",
        [
            ("AF-75MM-F18-EVO-FE", "Viltrox AF 75mm F1.8 EVO APS-C Lens for Sony E-Mount", "AF 75mm F1.8 EVO APS-C Lens for Sony E-Mount", 399.0, "Lens"),
            ("AF-75MM-F18-EVO-Z", "Viltrox AF 75mm F1.8 EVO APS-C Lens for Nikon Z-Mount", "AF 75mm F1.8 EVO APS-C Lens for Nikon Z-Mount", 399.0, "Lens"),
            ("AF-85MM-F14-PRO-FE", "Viltrox AF 85mm F1.4 Pro Full-Frame Lens", "", 599.0, "Lens"),
            ("DC-A1", "Viltrox DC-A1 Monitor", None, 199.0, "Monitor"),
        ]
        + [(f"BULK-{i:02d}", f"Bulk 75 Lens padding padding padding padding {i:02d}", "", 1.0, "Lens") for i in range(30)],
    )
    if with_lens_table:
        conn.executescript(
            """
            CREATE TABLE vkpi_kol_lens_evidence (
                id INTEGER PRIMARY KEY,
                resolution TEXT NOT NULL,
                product_sku TEXT,
                lens_key TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                mention_count INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        conn.executemany(
            "INSERT INTO vkpi_kol_lens_evidence (resolution, product_sku, lens_key, display_name, mention_count) VALUES (?, ?, ?, ?, ?)",
            [
                ("family", None, "af75mmf18evo", "AF 75mm F1.8 EVO", 9),
                ("family", None, "af75mmf18evo", "AF 75mm F1.8 EVO", 4),
                ("family", None, "af85mmf14pro", "AF 85mm F1.4 Pro", 30),
                ("sku", "AF-85MM-F14-PRO-FE", "af85mmf14pro", "AF 85mm F1.4 Pro", 20),
                ("unresolved", None, "", "75mm mystery", 99),
            ],
        )
    conn.commit()
    return conn


def _use_sqlite(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, *, lens_exists: bool = True) -> None:
    monkeypatch.setattr(db_connection, "get_conn", lambda: conn)
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(db_connection, "table_exists", lambda name: lens_exists)


def test_employee_gets_three_columns_only_and_lens_family_first(monkeypatch):
    _use_sqlite(monkeypatch, _conn())

    payload = router_mod.catalog_suggest_endpoint(q="75", limit=20, staff=_EMPLOYEE)

    assert payload["status"] == "ready"
    assert payload["q"] == "75"
    assert all(set(item) == {"sku", "display_name", "lens_key"} for item in payload["items"])
    assert payload["items"][0] == {"sku": "", "display_name": "AF 75mm F1.8 EVO", "lens_key": "af75mmf18evo"}
    names = [item["display_name"] for item in payload["items"]]
    assert names.count("AF 75mm F1.8 EVO") == 1
    assert "75mm mystery" not in names
    assert "AF 75mm F1.8 EVO APS-C Lens for Sony E-Mount" in names
    assert payload["source_status"]["lens_evidence"] == {"status": "ready", "result_count": 1}


def test_limit_is_capped_at_twenty(monkeypatch):
    _use_sqlite(monkeypatch, _conn())

    payload = catalog_suggest.suggest_catalog(db_connection.get_conn(), "75", limit=500, postgres=False)

    assert len(payload["items"]) == 20
    assert catalog_suggest.LIMIT_MAX == 20


def test_exact_sku_and_prefix_rank_before_substring(monkeypatch):
    _use_sqlite(monkeypatch, _conn())

    payload = router_mod.catalog_suggest_endpoint(q="AF 85mm F1.4 Pro", limit=5, staff=_EMPLOYEE)

    assert payload["items"][0] == {"sku": "AF-85MM-F14-PRO-FE", "display_name": "AF 85mm F1.4 Pro", "lens_key": "af85mmf14pro"}
    assert [item["display_name"] for item in payload["items"]].count("AF 85mm F1.4 Pro") == 1
    sku_hit = router_mod.catalog_suggest_endpoint(q="dc-a1", limit=5, staff=_EMPLOYEE)
    assert sku_hit["items"] == [{"sku": "DC-A1", "display_name": "Viltrox DC-A1 Monitor", "lens_key": ""}]


def test_missing_lens_table_is_absent_not_zero(monkeypatch):
    _use_sqlite(monkeypatch, _conn(with_lens_table=False), lens_exists=False)

    payload = router_mod.catalog_suggest_endpoint(q="zzz-nothing", limit=20, staff=_EMPLOYEE)

    assert payload["items"] == []
    assert payload["status"] == "partial"
    assert payload["source_status"]["lens_evidence"]["status"] == "absent"
    ready = router_mod.catalog_suggest_endpoint(q="75", limit=20, staff=_EMPLOYEE)
    assert ready["status"] == "partial"
    assert ready["items"]


def test_blank_query_is_empty_and_query_failure_is_error(monkeypatch):
    conn = _conn()
    _use_sqlite(monkeypatch, conn)
    assert router_mod.catalog_suggest_endpoint(q="   ", limit=20, staff=_EMPLOYEE)["status"] == "empty"

    conn.execute("DROP TABLE vkpi_products")
    conn.execute("DROP TABLE vkpi_kol_lens_evidence")
    payload = router_mod.catalog_suggest_endpoint(q="75", limit=20, staff=_EMPLOYEE)
    assert payload["status"] == "error"
    assert payload["items"] == []
    assert payload["source_status"]["products"]["status"] == "error"


def test_sql_uses_compat_placeholders_without_percent_literals():
    source = inspect.getsource(catalog_suggest)
    sql_blocks = [block for block in source.split('"""') if "SELECT" in block]
    assert sql_blocks
    for block in sql_blocks:
        assert "%" not in block
        assert "?" in block
        assert "--" not in block
    assert "STRPOS" in source and "INSTR" in source


def test_endpoint_is_registered_read_only_and_requires_vkpi_read():
    from app.api import routers as registry

    assert "vkpi_catalog_suggest" in registry.ADMIN_ROUTER_MODULES
    assert release_validation.release_validation_request_allowed("GET", "/api/admin/vkpi/catalog/suggest")
    routes = {route.path for route in router_mod.router.routes}
    assert routes == {"/api/admin/vkpi/catalog/suggest"}
    dependency_names = {
        getattr(dep.call, "__qualname__", "")
        for route in router_mod.router.routes
        for dep in route.dependant.dependencies
    }
    assert any(name.startswith("require_tab") for name in dependency_names)
