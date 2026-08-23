"""镜头出镜证据 -> detected 产品边(scripts/ops/lens_detected_links.py):置信度映射 / 人工边优先 / 幂等 / 撤销。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.ops import lens_detected_links as script  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_products (sku TEXT PRIMARY KEY);
        INSERT INTO vkpi_products VALUES ('AF-75MM-F18-EVO-FE'), ('AF-135MM-F18-LAB-FE'), ('AF-50MM-F20-AIR-FE');
        CREATE TABLE vkpi_kol_lens_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT, cache_id INTEGER, evidence_id INTEGER, kol_pool_id INTEGER,
            resolution TEXT, product_sku TEXT, lens_key TEXT DEFAULT '', modalities TEXT DEFAULT '[]',
            source_fields TEXT DEFAULT '[]', mention_count INTEGER DEFAULT 1
        );
        CREATE TABLE vkpi_kol_video_product_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id INTEGER NOT NULL, product_sku TEXT NOT NULL,
            relation_type TEXT NOT NULL DEFAULT 'manual', source TEXT NOT NULL DEFAULT 'my_kol_video_tracking',
            confidence REAL NOT NULL DEFAULT 1.0, created_by_staff_id INTEGER, created_at TEXT, updated_at TEXT,
            UNIQUE (evidence_id, product_sku, relation_type)
        );
        -- 41: 显式模态 -> confirmed 0.9;42: 模态未指明 -> likely 0.6;43: 已有人工边 -> 跳过;
        -- 44: family 级(不写);45: 目录没有的 sku(不写);41 第二条 advisory 行与 confirmed 同组 -> 仍 0.9
        INSERT INTO vkpi_kol_lens_evidence (cache_id, evidence_id, kol_pool_id, resolution, product_sku, lens_key, modalities, source_fields) VALUES
            (500, 41, 9, 'sku', 'AF-75MM-F18-EVO-FE', 'af75mmf18evo', '["visual","voice"]', '["product_presence"]'),
            (500, 41, 9, 'sku', 'AF-75MM-F18-EVO-FE', 'af75mmf18evo', '["unspecified"]', '["product_contribution"]'),
            (501, 42, 9, 'sku', 'AF-135MM-F18-LAB-FE', 'af135mmf18lab', '["unspecified"]', '["scene_timeline"]'),
            (502, 43, 10, 'sku', 'AF-50MM-F20-AIR-FE', 'af50mmf20air', '["visual"]', '["product_presence"]'),
            (503, 44, 10, 'family', NULL, 'af85mmf18', '["visual"]', '["product_presence"]'),
            (504, 45, 10, 'sku', 'NOT-IN-CATALOG', 'x', '["visual"]', '["product_presence"]');
        INSERT INTO vkpi_kol_video_product_links (evidence_id, product_sku, relation_type, source, confidence, created_by_staff_id)
            VALUES (43, 'AF-50MM-F20-AIR-FE', 'manual', 'my_kol_video_tracking', 1.0, 7);
        """
    )
    return conn


def test_plan_maps_relevance_to_confidence_and_respects_manual_rows() -> None:
    conn = _conn()
    plan = script.plan_links(conn, limit=100)
    assert plan["evidence_rows"] == 5 and plan["groups"] == 4
    inserts = {(row["evidence_id"], row["product_sku"]): row["confidence"] for row in plan["insert"]}
    assert inserts == {(41, "AF-75MM-F18-EVO-FE"): 0.9, (42, "AF-135MM-F18-LAB-FE"): 0.6}
    assert plan["skipped_manual_or_confirmed"] == 1
    assert plan["skipped_unknown_sku"] == 1
    assert plan["relevance"] == {"confirmed": 2, "likely": 1}  # 含被人工边挡下的 43(口径统计在前)
    # dry-run 不写
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_video_product_links").fetchone()[0] == 1


def test_apply_is_idempotent_and_revert_only_removes_own_rows() -> None:
    conn = _conn()
    result = script.apply_plan(conn, script.plan_links(conn, limit=100))
    assert result == {"inserted": 2, "updated": 0}
    rows = conn.execute(
        "SELECT evidence_id, product_sku, relation_type, source, confidence FROM vkpi_kol_video_product_links ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        (43, "AF-50MM-F20-AIR-FE", "manual", "my_kol_video_tracking", 1.0),
        (41, "AF-75MM-F18-EVO-FE", "detected", "lens_evidence_v2", 0.9),
        (42, "AF-135MM-F18-LAB-FE", "detected", "lens_evidence_v2", 0.6),
    ]

    again = script.plan_links(conn, limit=100)
    assert again["insert"] == [] and again["update_confidence"] == [] and again["unchanged"] == 2
    assert script.apply_plan(conn, again) == {"inserted": 0, "updated": 0}

    # 证据升级(42 多了显式模态)-> 只校正自己写的边的 confidence
    conn.execute("UPDATE vkpi_kol_lens_evidence SET modalities='[\"visual\"]' WHERE evidence_id=42")
    upgraded = script.plan_links(conn, limit=100)
    assert [row["confidence"] for row in upgraded["update_confidence"]] == [0.9]
    assert script.apply_plan(conn, upgraded) == {"inserted": 0, "updated": 1}
    assert conn.execute("SELECT confidence FROM vkpi_kol_video_product_links WHERE evidence_id=42").fetchone()[0] == 0.9

    # 其它来源的 detected 边不覆盖
    conn.execute("UPDATE vkpi_kol_video_product_links SET source='someone_else' WHERE evidence_id=41")
    conn.execute("UPDATE vkpi_kol_lens_evidence SET modalities='[\"unspecified\"]' WHERE evidence_id=41")
    other = script.plan_links(conn, limit=100)
    assert other["skipped_other_detected_source"] == 1 and other["update_confidence"] == []

    dry_revert = script.revert(conn, apply=False)
    assert dry_revert == {"mode": "revert", "matched": 1, "deleted": 0}
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_video_product_links").fetchone()[0] == 3
    assert script.revert(conn, apply=True) == {"mode": "revert", "matched": 1, "deleted": 1}
    remaining = conn.execute("SELECT evidence_id, source FROM vkpi_kol_video_product_links ORDER BY id").fetchall()
    assert [tuple(r) for r in remaining] == [(43, "my_kol_video_tracking"), (41, "someone_else")]


def test_cli_defaults_to_dry_run_and_revert_needs_apply() -> None:
    args = script._parse([])
    assert args.apply is False and args.revert is False
    args = script._parse(["--revert"])
    assert args.revert is True and args.apply is False
    try:
        script._parse(["--apply", "--dry-run"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover
        raise AssertionError("--apply 与 --dry-run 应互斥")
