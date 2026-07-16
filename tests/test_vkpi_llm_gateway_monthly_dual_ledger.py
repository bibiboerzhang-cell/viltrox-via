"""月度预算闸双表口径:vkpi_llm_calls + vkpi_ai_cost_ledger 合并与去重。

_current_month_spent_cents 原先只 SUM vkpi_llm_calls,Apify/视频批注等
非网关 AI 成本(只落 vkpi_ai_cost_ledger)全被月度闸漏看。改双表后:
- ledger 侧 SUM(cost_usd) x 100 折 cents;
- 去重:metadata_json 含 'llm_call_uid' 的 ledger 行是网关镜像行
  (llm_gateway_ledger.record_call 写入),已计入 vkpi_llm_calls,必须排除;
- 月窗:只算本月(occurred_at >= 本月一号)。

建表手法仿 tests/test_vkpi_llm_gateway_budget.py:模块私有 SQLite,
绝不碰仓库 submissions.db。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.platform import llm_gateway
from app.platform.db import schema_product_industry


MARKER = "vkpi-llm-monthly-dual-ledger-test"


@pytest.fixture(scope="module", autouse=True)
def _dual_ledger_test_db(tmp_path_factory: pytest.TempPathFactory):
    """Keep dual-ledger monthly gate tests on a module-private SQLite database."""
    db_path = (tmp_path_factory.mktemp("llm-monthly-dual") / "llm-monthly-dual.db").resolve()
    repository_db = (Path(__file__).resolve().parents[1] / "submissions.db").resolve()
    assert db_path != repository_db
    old_db_path = db_connection.DB_PATH
    old_runtime_backend = db_connection.DB_RUNTIME_BACKEND
    old_runtime_url = db_connection.DB_RUNTIME_URL
    old_schema_ready = schema_product_industry._SCHEMA_READY
    db_connection.close_db_runtime_sync()
    db_connection.DB_PATH = db_path
    db_connection.DB_RUNTIME_BACKEND = "sqlite"
    db_connection.DB_RUNTIME_URL = ""
    schema_product_industry._SCHEMA_READY = False
    try:
        schema_product_industry.ensure_vkpi_product_industry_schema()
        budget_guard.ensure_budget_schema()
        conn = get_conn()
        actual_path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
        assert actual_path == db_path
        yield db_path
    finally:
        db_connection.close_db_runtime_sync()
        db_connection.DB_PATH = old_db_path
        db_connection.DB_RUNTIME_BACKEND = old_runtime_backend
        db_connection.DB_RUNTIME_URL = old_runtime_url
        schema_product_industry._SCHEMA_READY = old_schema_ready


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _last_month() -> datetime:
    first = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first - timedelta(days=2)


def _cleanup() -> None:
    conn = get_conn()
    like = f"%{MARKER}%"
    conn.execute("DELETE FROM vkpi_llm_calls WHERE purpose LIKE ? OR metadata_json LIKE ?", (like, like))
    conn.execute("DELETE FROM vkpi_ai_cost_ledger WHERE cron_task LIKE ? OR metadata_json LIKE ?", (like, like))
    conn.commit()


def _insert_llm_call(uid_suffix: str, cost_micro_usd: int, created_at: str) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_llm_calls
            (call_uid, provider, model, purpose, cost_cents, cost_micro_usd, status, created_at, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            f"{MARKER}-{uid_suffix}",
            "openai",
            "fake-model",
            MARKER,
            cost_micro_usd // 10000,
            cost_micro_usd,
            "success",
            created_at,
            json.dumps({"marker": MARKER}),
        ),
    )
    conn.commit()


def _insert_ledger_row(cost_usd: float, occurred_at: str, *, with_call_uid: bool) -> None:
    metadata: dict[str, object] = {"marker": MARKER}
    if with_call_uid:
        # 网关镜像行的真实形状:record_call 往 metadata 里塞 llm_call_uid。
        metadata["llm_call_uid"] = f"{MARKER}-mirrored"
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_ai_cost_ledger
            (cron_task, ai_provider, model_name, cost_usd, tokens_in, tokens_out, metadata_json, occurred_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (MARKER, "gemini", "fake-model", cost_usd, 10, 20, json.dumps(metadata), occurred_at),
    )
    conn.commit()


def test_monthly_spent_merges_llm_calls_and_ledger() -> None:
    try:
        _cleanup()
        now_iso = _iso(_now())
        # 网关侧:$1.23 => 123 cents(cost_micro_usd 精度源)。
        _insert_llm_call("call-a", 1_230_000, now_iso)
        # ledger 侧非网关成本:$0.50 => +50 cents,必须计入。
        _insert_ledger_row(0.50, now_iso, with_call_uid=False)
        assert llm_gateway._current_month_spent_cents() == 123 + 50
    finally:
        _cleanup()


def test_monthly_spent_excludes_gateway_mirrored_ledger_rows() -> None:
    try:
        _cleanup()
        now_iso = _iso(_now())
        _insert_llm_call("call-b", 2_000_000, now_iso)  # $2.00 => 200 cents
        # 网关镜像行($0.70):metadata_json 含 llm_call_uid,已计入 vkpi_llm_calls,
        # ledger 侧必须排除 —— 计入即双计。
        _insert_ledger_row(0.70, now_iso, with_call_uid=True)
        # 非网关行($0.30)照常计入。
        _insert_ledger_row(0.30, now_iso, with_call_uid=False)
        assert llm_gateway._current_month_spent_cents() == 200 + 30
    finally:
        _cleanup()


def test_monthly_spent_ledger_respects_month_window() -> None:
    try:
        _cleanup()
        # 上月的 ledger 大额($9.99)不得漏进本月闸。
        _insert_ledger_row(9.99, _iso(_last_month()), with_call_uid=False)
        _insert_ledger_row(0.25, _iso(_now()), with_call_uid=False)
        assert llm_gateway._current_month_spent_cents() == 25
    finally:
        _cleanup()


def test_ledger_month_spent_cents_rounds_float_sum() -> None:
    try:
        _cleanup()
        now_iso = _iso(_now())
        # 0.1 + 0.2 浮点和 = 0.30000000000000004,x100 后必须 round 成 30 而非 30.000000000000004 截断出错。
        _insert_ledger_row(0.1, now_iso, with_call_uid=False)
        _insert_ledger_row(0.2, now_iso, with_call_uid=False)
        first_of_month = _iso(_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0))
        assert llm_gateway._ledger_month_spent_cents(first_of_month) == 30
    finally:
        _cleanup()
