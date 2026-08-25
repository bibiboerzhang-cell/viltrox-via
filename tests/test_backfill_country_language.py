"""国家码回填的安全契约(2026-08-25)。

守三条:干跑绝不写库、只填空值、解析保守到「拿不准就不填」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
import backfill_country_language as bf  # noqa: E402


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 0):
        self._rows = rows
        self.rowcount = rowcount

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Conn:
    """记账用的假连接:只认本脚本发的两条 SQL。"""

    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.updates: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()) -> _Cursor:
        if sql.strip().upper().startswith("SELECT"):
            return _Cursor(self.rows)
        self.updates.append((sql, params))
        return _Cursor([], rowcount=1)

    def commit(self) -> None:
        self.commits += 1


def _row(rid: int, raw: Any, followers: int = 1000) -> dict[str, Any]:
    return {"id": rid, "followers": followers, "raw": json.dumps(raw) if raw is not None else None}


# ── 解析口径 ────────────────────────────────────────────────────────────────


def test_plain_country_key_is_resolved() -> None:
    code, reason = bf.resolve_country_code(json.dumps({"country": "US"}))
    assert code == "US"
    assert reason == "key:country"


def test_country_code_variant_is_resolved() -> None:
    # 实测 raw 里 227 处用的是 countryCode 这个键名。
    code, _ = bf.resolve_country_code(json.dumps({"profile": {"countryCode": "JP"}}))
    assert code == "JP"


def test_value_nested_inside_a_list_is_found() -> None:
    # 只遍历 dict 会漏掉七成 —— 这条钉死必须走 list。
    payload = {"items": [{"meta": {"countryCode": "DE"}}]}
    assert bf.resolve_country_code(json.dumps(payload))[0] == "DE"


def test_lowercase_code_is_normalised_to_upper() -> None:
    assert bf.resolve_country_code(json.dumps({"country": "gb"}))[0] == "GB"


@pytest.mark.parametrize("value", ["Unknown", "Worldwide", "USA", "United States", "", "  "])
def test_non_standard_values_are_never_guessed(value: str) -> None:
    """拿不准一律不填 —— 猜错国家比留空危害大得多。"""
    code, reason = bf.resolve_country_code(json.dumps({"country": value}))
    assert code == ""
    assert reason in {"non_alpha2_value", "no_country_key"}


def test_missing_or_broken_raw_is_reported_not_crashed() -> None:
    assert bf.resolve_country_code(None) == ("", "raw_empty")
    assert bf.resolve_country_code("not json at all") == ("", "raw_not_json")
    assert bf.resolve_country_code(json.dumps({"handle": "x"})) == ("", "no_country_key")


# ── 计划与写入 ──────────────────────────────────────────────────────────────


def test_plan_counts_writable_and_skipped_separately() -> None:
    conn = _Conn([
        _row(1, {"country": "US"}, followers=90_000),
        _row(2, {"countryCode": "CA"}, followers=1_000),
        _row(3, {"country": "Unknown"}),
        _row(4, {"handle": "no-country-here"}),
    ])
    plan = bf.build_plan(conn)
    assert plan["writable"] == 2
    assert plan["high_reach_writable"] == 1
    assert plan["skipped_by_reason"]["non_alpha2_value"] == 1
    assert plan["skipped_by_reason"]["no_country_key"] == 1


def test_dry_run_writes_nothing_at_all() -> None:
    conn = _Conn([_row(1, {"country": "US"}), _row(2, {"countryCode": "FR"})])
    monkey = bf.get_conn if hasattr(bf, "get_conn") else None
    assert monkey is None  # get_conn 是函数内 import,不该挂在模块上
    plan = bf.build_plan(conn)
    assert plan["writable"] == 2
    assert conn.updates == [], "干跑必须一行不写"
    assert conn.commits == 0


def test_apply_only_touches_empty_country_column() -> None:
    """SQL 侧必须自带「只填空值」兜底,防止干跑与写入之间有并发补上。"""
    conn = _Conn([_row(1, {"country": "US"})])
    plan = bf.build_plan(conn)
    bf.apply_plan(conn, plan["plan"])
    assert len(conn.updates) == 1
    sql, params = conn.updates[0]
    assert "(country IS NULL OR country=?)" in sql
    assert params[0] == "US" and params[1] == 1
    assert conn.commits == 1


def test_language_is_deliberately_out_of_scope() -> None:
    """raw 里实测零条 language 值;脚本不得假装能填。"""
    source = Path(bf.__file__).read_text(encoding="utf-8")
    assert "SET country=?" in source
    assert "SET language" not in source
