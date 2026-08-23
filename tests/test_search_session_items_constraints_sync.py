"""search_sessions_online 写入的 item_type/stage 字面必须在最新 CHECK 约束内(迁移 293);
迁移 103 的取值集曾让严格 30 在线通道每次 CheckViolation(2026-08-23)。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _allowed(sql: str, name: str) -> set[str]:
    m = re.search(rf"{name}\s+CHECK \(\w+ IN \(([^)]*)\)\)", sql)
    assert m, name
    return {v.strip().strip("'") for v in m.group(1).split(",")}


def test_online_lane_literals_are_within_migration_293_checks():
    sql = (ROOT / "migrations/293_vkpi_search_session_items_online_stage.sql").read_text(encoding="utf-8")
    types = _allowed(sql, "chk_vkpi_kol_search_session_items_type")
    stages = _allowed(sql, "chk_vkpi_kol_search_session_items_stage")
    src = (ROOT / "backend/app/domains/kol/search_sessions_online.py").read_text(encoding="utf-8")
    for t in re.findall(r'"item_type":\s*"([a-z_]+)"', src):
        assert t in types, t
    for s in re.findall(r'"stage":\s*"([a-z_]+)"', src):
        assert s in stages, s
    assert "online_qualified_candidate" in types and "qualified" in stages
