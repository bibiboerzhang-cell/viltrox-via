"""波 D·D2 raw 字段提列日任务合同:只处理账本过期行、幂等、单行失败不中断、列未迁移诚实 blocked、零入队。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import app.domains.kol.pool  # noqa: E402,F401 — pool_enrich 单独先导会触发既有循环导入
from app.domains.kol import pool_enrich, pool_raw_fields_job as job  # noqa: E402
from tests.test_backfill_raw_fields_and_language_scripts import TT_RAW, _patch_columns  # noqa: E402


def _conn(*, with_ledger: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ledger_cols = ", raw_fields_extracted_at TEXT, raw_fields_extractor_version TEXT" if with_ledger else ""
    conn.executescript(
        f"""
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY, platform TEXT, raw_platform_data TEXT, updated_at TEXT, last_scrape_at TEXT,
            viltrox_fit_score REAL, is_verified INTEGER, is_tt_seller INTEGER, is_commerce_user INTEGER,
            topic_details_json TEXT, tagged_brands_json TEXT{ledger_cols}
        );
        CREATE TABLE vkpi_kol_contact_acquisition_queue (kol_pool_id INTEGER PRIMARY KEY, status TEXT);
        """
    )
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, raw_platform_data, updated_at, viltrox_fit_score) VALUES (1, 'tiktok', ?, '2026-08-01T00:00:00+00:00', 70.0)", (json.dumps(TT_RAW),))
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, raw_platform_data, updated_at) VALUES (2, 'youtube', '{}', '2026-08-01T00:00:00+00:00')")
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, raw_platform_data, updated_at) VALUES (3, 'instagram', ?, '2026-08-01T00:00:00+00:00')",
                 (json.dumps({"profile": {"items": [{"id": "acc", "verified": False, "isBusinessAccount": True, "biography": "no contacts here"}]}}),))
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, raw_platform_data, updated_at) VALUES (4, 'tiktok', 'not json at all', '2026-08-01T00:00:00+00:00')")
    conn.commit()
    return conn


def test_job_processes_only_stale_rows_and_is_idempotent(monkeypatch) -> None:
    _patch_columns(monkeypatch)
    import app.domains.kol.contact_acquisition_queue as queue_mod

    monkeypatch.setattr(queue_mod, "enqueue_contact_acquisition", lambda *a, **k: (_ for _ in ()).throw(AssertionError("job must never enqueue contacts")))
    conn = _conn()

    dry = job.run_raw_fields_backfill(limit=10, conn=conn, dry_run=True)
    assert dry["status"] == "ok" and dry["dry_run"] is True and dry["candidates"] == 3  # '{}' 行不是候选
    assert dry["written_rows"] == 0 and dry["remaining_after"] == 3 and dry["errors"] == 0
    assert conn.execute("SELECT is_verified FROM vkpi_kol_pool WHERE id=1").fetchone()[0] is None

    applied = job.run_raw_fields_backfill(limit=10, conn=conn)
    assert applied["status"] == "ok" and applied["candidates"] == 3 and applied["written_rows"] == 3 and applied["errors"] == 0
    assert applied["field_fill"]["is_verified"] == 2 and applied["field_fill"]["tagged_brands_json"] == 1
    assert applied["remaining_after"] == 0 and applied["provider_calls_performed"] is False and applied["contacts_enqueued"] == 0
    row = dict(conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=1").fetchone())
    assert row["is_verified"] == 1 and row["viltrox_fit_score"] == 70.0
    assert row["raw_fields_extractor_version"] == pool_enrich.RAW_FIELDS_EXTRACTOR_VERSION and row["raw_fields_extracted_at"]
    assert json.loads(row["tagged_brands_json"])[0]["handle"] == "sonyalpha"
    # 非法 raw 的行也记了账本(提列出空字段),不会每天重试
    assert dict(conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=4").fetchone())["raw_fields_extracted_at"]

    again = job.run_raw_fields_backfill(limit=10, conn=conn)
    assert again["status"] == "empty" and again["candidates"] == 0 and again["written_rows"] == 0

    # 新抓(last_scrape_at 晚于账本)→ 只重提那一行;解析器升版 → 全部重提
    conn.execute("UPDATE vkpi_kol_pool SET raw_fields_extracted_at='2026-08-10T00:00:00+00:00', last_scrape_at='2026-08-11T00:00:00+00:00' WHERE id=3")
    conn.commit()
    stale = job.run_raw_fields_backfill(limit=10, conn=conn)
    assert stale["candidates"] == 1 and stale["written_rows"] == 1 and stale["remaining_after"] == 0
    conn.execute("UPDATE vkpi_kol_pool SET raw_fields_extractor_version='raw_fields_v0'")
    conn.commit()
    upgraded = job.run_raw_fields_backfill(limit=2, conn=conn)
    assert upgraded["candidates"] == 2 and upgraded["remaining_after"] == 1  # limit 截断,下一轮接着
    assert job.run_raw_fields_backfill(limit=10, conn=conn)["candidates"] == 1


def test_job_isolates_row_failures_and_blocks_without_migration(monkeypatch) -> None:
    _patch_columns(monkeypatch)
    conn = _conn()
    real_apply = pool_enrich.apply_raw_fields

    def flaky(db, kol_id, raw, *, platform=""):
        if int(kol_id) == 1:
            raise RuntimeError("boom")
        return real_apply(db, kol_id, raw, platform=platform)

    monkeypatch.setattr(pool_enrich, "apply_raw_fields", flaky)
    out = job.run_raw_fields_backfill(limit=10, conn=conn)
    assert out["status"] == "ok" and out["errors"] == 1 and out["written_rows"] == 2 and out["remaining_after"] == 1

    blocked = job.run_raw_fields_backfill(limit=10, conn=_conn(with_ledger=False))
    assert blocked["status"] == "blocked" and blocked["reason"] == "migration_291_not_applied"
    assert set(blocked["missing_columns"]) == {"raw_fields_extracted_at", "raw_fields_extractor_version"}
