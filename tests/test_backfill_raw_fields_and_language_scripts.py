"""两个回填脚本的合同:默认 dry-run、--apply 才写、账本幂等、联系方式只入队不直写、覆盖率报告口径。"""
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
from app.domains.kol import pool_enrich  # noqa: E402
from scripts.ops import backfill_comment_language as lang_script  # noqa: E402
from scripts.ops import backfill_pool_raw_fields as raw_script  # noqa: E402

TT_RAW = {
    "profile": {"items": [{
        "id": "v1",
        "authorMeta": {"verified": True, "ttSeller": False, "signature": "business: hello@studio.com",
                       "commerceUserInfo": {"commerceUser": True, "category": "Electronics"}},
        "mentions": ["@sonyalpha"], "detailedMentions": [],
    }]},
    "videos": [],
}


def _pool_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY, platform TEXT, raw_platform_data TEXT, updated_at TEXT, viltrox_fit_score REAL,
            is_verified INTEGER, is_tt_seller INTEGER, is_commerce_user INTEGER,
            topic_details_json TEXT, tagged_brands_json TEXT, raw_fields_extracted_at TEXT, raw_fields_extractor_version TEXT
        );
        CREATE TABLE vkpi_kol_contact_acquisition_queue (kol_pool_id INTEGER PRIMARY KEY, status TEXT);
        """
    )
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, raw_platform_data, updated_at, viltrox_fit_score) VALUES (1, 'tiktok', ?, '2026-08-01T00:00:00+00:00', 70.0)", (json.dumps(TT_RAW),))
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, raw_platform_data, updated_at) VALUES (2, 'youtube', '{}', '2026-08-01T00:00:00+00:00')")
    conn.execute("INSERT INTO vkpi_kol_pool (id, platform, raw_platform_data, updated_at) VALUES (3, 'instagram', ?, '2026-08-01T00:00:00+00:00')",
                 (json.dumps({"profile": {"items": [{"id": "acc", "verified": False, "isBusinessAccount": True, "biography": "no contacts here"}]}}),))
    return conn


def _patch_columns(monkeypatch) -> None:
    monkeypatch.setattr(pool_enrich, "_table_columns", lambda conn, table: {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    })


def test_raw_fields_backfill_dry_run_then_apply_then_ledger_skip(monkeypatch) -> None:
    _patch_columns(monkeypatch)
    enqueued: list[int] = []
    import app.domains.kol.contact_acquisition_queue as queue_mod

    monkeypatch.setattr(queue_mod, "enqueue_contact_acquisition", lambda kol_id, *, trigger_source, conn=None: enqueued.append(kol_id))
    conn = _pool_conn()

    dry = raw_script.run(conn, raw_script._parse([]))
    assert dry["candidates"] == 2 and dry["processed"] == 2  # '{}' 的行不是候选
    assert dry["field_fill"] == {"is_verified": 2, "is_tt_seller": 1, "is_commerce_user": 2, "topic_details_json": 1, "tagged_brands_json": 1}
    assert dry["written_rows"] == 0 and dry["contacts"]["would_enqueue"] == 1 and dry["contacts"]["enqueued"] == 0
    assert dry["contacts"]["by_type"] == {"email": 1}
    assert enqueued == []
    assert conn.execute("SELECT is_verified FROM vkpi_kol_pool WHERE id=1").fetchone()[0] is None

    applied = raw_script.run(conn, raw_script._parse(["--apply"]))
    assert applied["written_rows"] == 2 and applied["contacts"]["enqueued"] == 1 and enqueued == [1]
    row = dict(conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=1").fetchone())
    assert row["is_verified"] == 1 and row["is_commerce_user"] == 1 and row["is_tt_seller"] == 0
    assert json.loads(row["tagged_brands_json"])[0]["handle"] == "sonyalpha"
    assert row["viltrox_fit_score"] == 70.0 and row["raw_fields_extractor_version"] == pool_enrich.RAW_FIELDS_EXTRACTOR_VERSION
    # 联系方式不直写派生列:只经队列 -> contact_ingest(raw 本身当然还含邮箱)
    derived = {k: v for k, v in row.items() if k != "raw_platform_data"}
    assert "hello@studio.com" not in json.dumps(derived)

    # 队列行已存在 -> 不重复入队;账本未过期 -> 跳过
    conn.execute("INSERT INTO vkpi_kol_contact_acquisition_queue VALUES (1, 'ready')")
    again = raw_script.run(conn, raw_script._parse(["--apply"]))
    assert again["skipped_fresh_ledger"] == 2 and again["processed"] == 0 and again["written_rows"] == 0
    forced = raw_script.run(conn, raw_script._parse(["--force"]))
    assert forced["processed"] == 2 and forced["contacts"]["already_queued"] == 1 and forced["contacts"]["would_enqueue"] == 0
    conn.execute("UPDATE vkpi_kol_contact_acquisition_queue SET status='suppressed'")
    suppressed = raw_script.run(conn, raw_script._parse(["--force", "--requeue"]))
    assert suppressed["contacts"]["suppressed"] == 1 and suppressed["contacts"]["would_enqueue"] == 0

    # raw 更新(updated_at 晚于账本)-> 重新提列
    conn.execute("UPDATE vkpi_kol_pool SET updated_at='2099-01-01T00:00:00+00:00' WHERE id=1")
    stale = raw_script.run(conn, raw_script._parse(["--skip-contacts"]))
    assert stale["processed"] == 1 and stale["skipped_fresh_ledger"] == 1


def _comments_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_comments (id INTEGER PRIMARY KEY, platform TEXT, comment_text TEXT, language_detected TEXT);
        INSERT INTO vkpi_comments VALUES
            (1, 'youtube', 'This lens is amazing for portraits, love it', NULL),
            (2, 'youtube', '👏👏👏', NULL),
            (3, 'tiktok', '这个镜头太棒了', ''),
            (4, 'tiktok', 'lol', 'und'),
            (5, 'instagram', 'Muy buen video gracias', 'unknown'),
            (6, 'instagram', 'already tagged', 'en'),
            (7, 'instagram', 'good', 'so');
        """
    )
    return conn


def test_comment_language_backfill_reports_honest_coverage_and_is_idempotent() -> None:
    conn = _comments_conn()
    dry = lang_script.run(conn, lang_script._parse(["--sample", "2"]))
    assert dry["coverage_before"] == {"total": 7, "missing": 5, "detected": 2, "coverage_pct": 28.6}
    assert dry["scanned"] == 5 and dry["detected"] == 3 and dry["written"] == 0
    assert dry["by_language"] == {"en": 1, "zh": 1, "es": 1}
    assert dry["undetermined_reason"] == {"no_letters": 1, "too_short": 1, "ambiguous": 0}
    assert dry["coverage_projected"]["detected"] == 5 and dry["coverage_projected"]["coverage_pct"] == 71.4
    assert dry["coverage_projected"]["text_bearing_total"] == 6 and dry["coverage_projected"]["text_bearing_coverage_pct"] == 83.3
    assert len(dry["samples_detected"]) == 2
    assert conn.execute("SELECT language_detected FROM vkpi_comments WHERE id=1").fetchone()[0] is None

    applied = lang_script.run(conn, lang_script._parse(["--apply", "--batch", "2"]))
    assert applied["written"] == 3 and applied["coverage_after"]["detected"] == 5
    rows = {r["id"]: r["language_detected"] for r in conn.execute("SELECT id, language_detected FROM vkpi_comments").fetchall()}
    assert rows == {1: "en", 2: None, 3: "zh", 4: "und", 5: "es", 6: "en", 7: "so"}  # 判不出的不动,既有噪声码不抹

    again = lang_script.run(conn, lang_script._parse(["--apply"]))
    assert again["scanned"] == 2 and again["detected"] == 0 and again["written"] == 0
    forced = lang_script.run(conn, lang_script._parse(["--force"]))
    assert forced["scanned"] == 7 and forced["unchanged"] == 3 and forced["detected"] == 0  # 'good' 保守仍判不出,不改 so
