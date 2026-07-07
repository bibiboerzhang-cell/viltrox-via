#!/usr/bin/env python3
"""Smoke:inventory_low 动作必须能过 validate_action 的 entity_exists 检查。

回归背景(2026-07-07):_ENTITY_TABLES 曾把 "inventory" 标成 as_str=False(按 int 反查),
但 vkpi_inventory.id 是 VARCHAR(64)(如 s_135lab_sample / s_1781575593180_0),
int() 必炸 → 所有 inventory_low 动作永远 entity_missing,批准后也执行不了。

验证三件事:
1. 映射钉死:_ENTITY_TABLES["inventory"] 必须按 str 反查(as_str=True)。
2. 正例:approved + 字符串 entity_id(库里真存在)→ ok=True 且 checks.entity_exists=True。
3. 负例:不存在的 entity_id → entity_missing(证明检查真在查,不是被跳过)。

只读校验路径 + sqlite 离线自建表;不碰 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

from app.db.connection import get_conn  # noqa: E402
from app.domains.actions.validators import _ENTITY_TABLES, validate_action  # noqa: E402

INV_ID = "s_smoke_low_stock"


def _setup_inventory() -> None:
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_inventory (
            id VARCHAR(64) PRIMARY KEY,
            sku TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'lens',
            qty INTEGER NOT NULL DEFAULT 0,
            location TEXT DEFAULT '',
            note TEXT DEFAULT '',
            is_sample BOOLEAN NOT NULL DEFAULT FALSE
        )
        """
    )
    conn.execute("DELETE FROM vkpi_inventory WHERE id = ?", (INV_ID,))
    conn.execute(
        "INSERT INTO vkpi_inventory (id, sku, name, category, qty) VALUES (?, ?, ?, ?, ?)",
        (INV_ID, "SMOKE-SKU-LOW", "Smoke 低库存样品", "lens", 1),
    )
    conn.commit()


def _make_action(entity_id: str) -> dict:
    # 与 producers.produce_inventory_low 同口径:字符串 entity_id、不烧 LLM、零成本。
    return {
        "status": "approved",
        "category": "inventory_low",
        "entity_type": "inventory",
        "entity_id": entity_id,
        "estimated_cost_cents": 0,
        "uses_llm": False,
        "touches_v6_fit": False,
    }


def main() -> int:
    failures: list[str] = []

    # 1. 映射钉死:inventory 必须按 str 反查
    table, as_str = _ENTITY_TABLES.get("inventory", ("", None))
    if table != "vkpi_inventory" or as_str is not True:
        failures.append(f"_ENTITY_TABLES['inventory'] 应为 ('vkpi_inventory', True),实际 {(table, as_str)!r}")

    _setup_inventory()

    # 2. 正例:字符串 id 真存在 → 放行
    verdict = validate_action(_make_action(INV_ID))
    if not (verdict.get("ok") is True and verdict.get("checks", {}).get("entity_exists") is True):
        failures.append(f"正例应 ok=True/entity_exists=True,实际 {verdict!r}")

    # 3. 负例:不存在的 id → entity_missing(证明反查真执行了)
    verdict_missing = validate_action(_make_action("s_smoke_ghost_never_exists"))
    if not (verdict_missing.get("ok") is False and verdict_missing.get("reason") == "entity_missing"):
        failures.append(f"负例应 entity_missing,实际 {verdict_missing!r}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("PASS: inventory_low 动作可过 validate_action entity_exists(str 反查),负例仍被拦。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
