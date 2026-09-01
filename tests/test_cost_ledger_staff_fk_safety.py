"""成本台账 staff 外键安全:调用方传 user id / 过期 staff id 时,台账必须仍能落行(staff_id=NULL + 留痕),
而不是 ForeignKeyViolation → forced_ai_cost_ledger_write_failed(2026-08-22 复盘:owner 从 UI 点的视频深析
全部因 triggered_by_user_id=1 被当 staff id 写台账而失败;worker 侧亦改为优先 payload.staff_id)。"""
from __future__ import annotations

import uuid

import pytest
from psycopg import sql

from app.db.connection import PostgresCompatConnection
from app.domains.costs import budget_guard

pytestmark = pytest.mark.pg


def _schema(pg_dsn: str, schema: str) -> PostgresCompatConnection:
    import psycopg

    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        conn.execute("CREATE TABLE staff (id BIGSERIAL PRIMARY KEY, user_id BIGINT)")
        conn.execute("INSERT INTO staff (id, user_id) VALUES (40, 1)")
        conn.execute(
            """
            CREATE TABLE vkpi_ai_cost_ledger (
              id BIGSERIAL PRIMARY KEY, cron_task TEXT, ai_provider TEXT, model_name TEXT,
              cost_usd NUMERIC(18,6), tokens_in INT, tokens_out INT, kol_pool_id BIGINT,
              staff_id BIGINT REFERENCES staff(id), task_item_id BIGINT, metadata_json TEXT,
              occurred_at TIMESTAMPTZ
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE vkpi_provider_budget_caps (
              scope TEXT PRIMARY KEY, cap_usd NUMERIC(18,6), current_spend NUMERIC(18,6),
              warning_at NUMERIC(18,6), hard_stop_at NUMERIC(18,6), metadata_json TEXT,
              updated_at TIMESTAMPTZ
            )
            """
        )
    raw = psycopg.connect(pg_dsn, connect_timeout=5)
    raw.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
    raw.commit()
    return PostgresCompatConnection(raw, pool=None)


def _drop(pg_dsn: str, schema: str) -> None:
    import psycopg

    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.parametrize("bad_staff", [1, 999_999])
def test_record_cost_survives_unknown_staff_id(pg_dsn: str, monkeypatch: pytest.MonkeyPatch, bad_staff: int) -> None:
    schema = f"t_ledger_fk_{uuid.uuid4().hex[:8]}"
    conn = _schema(pg_dsn, schema)
    monkeypatch.setattr(budget_guard, "get_conn", lambda: conn)
    monkeypatch.setattr(budget_guard, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(budget_guard, "ensure_budget_schema", lambda: None)
    try:
        receipt = budget_guard.record_cost(
            scope="video_analysis", cron_task="audit_video_analysis", ai_provider="google",
            model_name="gemini-3.6-flash", cost_usd=0.0123, tokens_in=10, tokens_out=5,
            triggered_by=bad_staff, metadata={"k": "v"}, update_budget_scopes=False,
        )
        assert receipt["recorded"] is True and int(receipt["ledger_id"]) > 0
        row = dict(conn.execute("SELECT staff_id, metadata_json FROM vkpi_ai_cost_ledger WHERE id=?", (receipt["ledger_id"],)).fetchone())
        assert row["staff_id"] is None
        assert f'"unresolved_staff_id": {bad_staff}' in str(row["metadata_json"])
        # 真实 staff 仍正常落 FK
        ok = budget_guard.record_cost(
            scope="video_analysis", cron_task="audit_video_analysis", ai_provider="google",
            model_name="gemini-3.6-flash", cost_usd=0.01, tokens_in=1, tokens_out=1,
            staff_id=40, update_budget_scopes=False,
        )
        assert dict(conn.execute("SELECT staff_id FROM vkpi_ai_cost_ledger WHERE id=?", (ok["ledger_id"],)).fetchone())["staff_id"] == 40
    finally:
        conn.close()
        _drop(pg_dsn, schema)


def test_worker_llm_context_prefers_staff_id_over_user_id() -> None:
    from pathlib import Path

    # 源码口径守卫(不 import:apify_jobs_worker_gemini 必须经 apify_jobs_worker 先加载,直接 import 会循环)
    src = (
        Path(__file__).resolve().parents[1]
        / "backend/app/workers/apify_jobs_worker_gemini_runtime.py"
    ).read_text(encoding="utf-8")
    # 口径守卫:llm_context.triggered_by 必须先取 payload.staff_id(staff 外键),再退 user id。
    assert '"triggered_by": payload.get("staff_id")' in src
    assert 'or payload.get("triggered_by_user_id", payload.get("user_id"))' in src
