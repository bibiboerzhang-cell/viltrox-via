from __future__ import annotations

import ast
from pathlib import Path

from app.domains.kol.my_kol_board_ext_sql import VILTROX_TOKEN
from app.domains.market import ai_today
from app.shared.brand_identity import VILTROX_BRAND_TOKEN


ROOT = Path(__file__).resolve().parents[1]


def test_viltrox_brand_token_keeps_exact_compatibility_aliases() -> None:
    assert VILTROX_BRAND_TOKEN == "viltrox"
    assert VILTROX_TOKEN is VILTROX_BRAND_TOKEN
    assert ai_today.VILTROX_TOKEN is VILTROX_BRAND_TOKEN


def test_ai_today_no_longer_imports_the_kol_sql_module() -> None:
    source = ROOT / "backend/app/domains/market/ai_today.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.domains.kol.my_kol_board_ext_sql" not in imported
    assert "app.shared.brand_identity" in imported
