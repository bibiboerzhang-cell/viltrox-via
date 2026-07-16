"""V6 Fit Top 快照管线测试 —— 重点守红线:capture 只读源 fit_score,绝不写回(指纹不变)。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import connection as db_connection
from app.db.connection import get_conn
import app.platform.db.schema_product_industry as product_industry_schema
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


@pytest.fixture(scope="module", autouse=True)
def _fit_snapshot_test_db(tmp_path_factory: pytest.TempPathFactory):
    """Exercise the snapshot pipeline against a private, migration-shaped DB."""
    db_path = (tmp_path_factory.mktemp("fit-snapshot") / "fit-snapshot.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db

    old_db_path = db_connection.DB_PATH
    old_runtime_backend = db_connection.DB_RUNTIME_BACKEND
    old_runtime_url = db_connection.DB_RUNTIME_URL
    old_schema_ready = product_industry_schema._SCHEMA_READY

    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    product_industry_schema._SCHEMA_READY = False

    try:
        ensure_vkpi_product_industry_schema()
        conn = get_conn()
        actual_path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
        assert actual_path == db_path
        conn.executescript(
            """
            CREATE TABLE vkpi_kol_fit_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                kol_pool_id INTEGER NOT NULL,
                pool_uid TEXT,
                platform TEXT,
                handle TEXT,
                display_name TEXT,
                fit_score NUMERIC,
                followers INTEGER,
                captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(snapshot_date, kol_pool_id)
            );
            CREATE INDEX idx_vkpi_kol_fit_snapshot_date
                ON vkpi_kol_fit_snapshot(snapshot_date);
            CREATE INDEX idx_vkpi_kol_fit_snapshot_kol
                ON vkpi_kol_fit_snapshot(kol_pool_id);
            """
        )
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
              (pool_uid, platform, handle, display_name, followers,
               viltrox_fit_score, source_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "fit-snapshot-unit-1",
                "youtube",
                "fit_snapshot_unit",
                "Fit Snapshot Unit",
                1234,
                82.375,
                "unit_test",
                "2026-05-23T00:00:00Z",
                "2026-05-23T00:00:00Z",
            ),
        )
        conn.commit()
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_runtime_backend
        db_connection.DB_RUNTIME_URL = old_runtime_url
        product_industry_schema._SCHEMA_READY = old_schema_ready


def _fingerprint(conn):
    r = dict(
        conn.execute(
            "SELECT ROUND(SUM(viltrox_fit_score), 3) s, COUNT(*) c "
            "FROM vkpi_kol_pool WHERE viltrox_fit_score IS NOT NULL"
        ).fetchone()
    )
    return (str(r["s"]), int(r["c"]))


def test_capture_idempotent_and_fingerprint_unchanged():
    from app.domains.dashboard import fit_snapshot

    conn = get_conn()
    before = _fingerprint(conn)

    r1 = fit_snapshot.capture_daily_snapshot()
    assert r1["status"] == "ok"
    assert r1["rows"] > 0
    # 幂等:ON CONFLICT DO NOTHING → 同日重跑行数不变
    r2 = fit_snapshot.capture_daily_snapshot()
    assert r2["rows"] == r1["rows"]

    # 红线:快照只读源,绝不改 vkpi_kol_pool.viltrox_fit_score → 指纹不变
    assert _fingerprint(conn) == before


def test_compute_top_movers_shape_never_crashes():
    from app.domains.dashboard import fit_snapshot

    res = fit_snapshot.compute_top_movers(limit=5)
    assert isinstance(res, dict)
    assert "available" in res
    assert isinstance(res.get("movers"), list)
    # 不足两天 → warming_up;有两天 → 真 movers。两种都不崩、形状一致。
    if not res["available"]:
        assert res.get("reason") == "fit_history_warming_up"
