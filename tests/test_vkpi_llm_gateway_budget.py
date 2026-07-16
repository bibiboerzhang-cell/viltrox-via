from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import connection as db_connection
from app.db.connection import get_conn
from app.platform import llm_gateway
from app.platform.db import schema_product_industry
from app.domains.costs import budget_guard
from scripts import vkpi_llm_budget_acceptance


MARKER = "vkpi-llm-budget-test"


@pytest.fixture(scope="module", autouse=True)
def _llm_budget_test_db(tmp_path_factory: pytest.TempPathFactory):
    """Keep budget/gateway ledger tests on a module-private SQLite database."""
    db_path = (tmp_path_factory.mktemp("llm-budget") / "llm-budget.db").resolve()
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


def _scope(scope: str) -> str:
    return f"{scope}:{MARKER}".replace("-", "_")


def _delete_scope(scope: str) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_provider_budget_caps WHERE scope=?", (scope,))
    conn.commit()


def _snapshot_scope(scope: str) -> dict | None:
    budget_guard.ensure_budget_schema()
    row = get_conn().execute("SELECT * FROM vkpi_provider_budget_caps WHERE scope=?", (scope,)).fetchone()
    return dict(row) if row else None


def _restore_scope(scope: str, snapshot: dict | None) -> None:
    conn = get_conn()
    if snapshot is None:
        conn.execute("DELETE FROM vkpi_provider_budget_caps WHERE scope=?", (scope,))
    else:
        conn.execute(
            """
            INSERT INTO vkpi_provider_budget_caps
                (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(scope) DO UPDATE SET
                cap_usd=excluded.cap_usd,
                current_spend=excluded.current_spend,
                warning_at=excluded.warning_at,
                hard_stop_at=excluded.hard_stop_at,
                reset_at=excluded.reset_at,
                fallback_action=excluded.fallback_action,
                metadata_json=excluded.metadata_json
            """,
            (
                scope,
                snapshot.get("cap_usd"),
                snapshot.get("current_spend"),
                snapshot.get("warning_at"),
                snapshot.get("hard_stop_at"),
                snapshot.get("reset_at"),
                snapshot.get("fallback_action"),
                snapshot.get("metadata_json") or "{}",
            ),
        )
    conn.commit()


def _cleanup(marker: str = MARKER) -> None:
    conn = get_conn()
    like = f"%{marker}%"
    conn.execute("DELETE FROM vkpi_llm_calls WHERE purpose LIKE ? OR metadata_json LIKE ?", (like, like))
    conn.execute("DELETE FROM vkpi_ai_cost_ledger WHERE cron_task LIKE ? OR metadata_json LIKE ?", (like, like))
    conn.execute("DELETE FROM vkpi_provider_budget_caps WHERE scope LIKE ?", (f"%{marker.replace('-', '_')}%",))
    conn.commit()


def test_budget_scopes_require_configuration_and_single_call_cap() -> None:
    budget_guard.ensure_budget_schema()
    required = [_scope("monthly_total"), _scope("single_call"), _scope("provider_openai"), _scope("cron_evidence")]
    try:
        _cleanup()
        plan = budget_guard.check_budget_scopes(required, 0.01, require_configured=True)
        assert plan["allowed"] is False
        assert all(check["configured"] is False for check in plan["checks"])

        for scope in required:
            budget_guard.update_budget(scope, {"cap_usd": 1.0, "current_spend": 0, "fallback_action": "fallback_to_rule_v0"})
        allowed = budget_guard.check_budget_scopes(required, 0.01, require_configured=True)
        assert allowed["allowed"] is True

        budget_guard.update_budget(required[1], {"cap_usd": 0.01, "current_spend": 0.01})
        blocked = budget_guard.check_budget_scopes(required, 0.01, require_configured=True)
        assert blocked["allowed"] is False
        single = {check["scope"]: check for check in blocked["checks"]}[required[1]]
        assert single["hard_stopped"] is True
    finally:
        _cleanup()


def test_market_provider_smoke_budget_scope_contract_exists() -> None:
    scope = "cron:market_provider_smoke"
    snapshot = _snapshot_scope(scope)
    try:
        budget_guard.update_budget(scope, {"cap_usd": 1.0, "current_spend": 0, "fallback_action": "fallback_to_preflight_only"})
        row = _snapshot_scope(scope)
        assert row is not None
        assert float(row["cap_usd"]) == 1.0
        assert row["fallback_action"] == "fallback_to_preflight_only"

        migration_sql = Path("migrations/082_vkpi_market_provider_smoke_budget.sql").read_text(encoding="utf-8")
        assert "cron:market_provider_smoke" in migration_sql
        assert "fallback_to_preflight_only" in migration_sql
    finally:
        _restore_scope(scope, snapshot)


def test_llm_budget_acceptance_report_is_readonly(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_LLM_GATEWAY_FORCE_OFFLINE", "1")
    monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "0")
    budget_guard.ensure_budget_schema()
    before_calls = int(get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_llm_calls").fetchone()["n"])
    before_ledger = int(get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()["n"])

    report = vkpi_llm_budget_acceptance.build_report(prompt="Read-only budget preflight", max_output_tokens=120)

    after_calls = int(get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_llm_calls").fetchone()["n"])
    after_ledger = int(get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()["n"])
    assert report["provider_calls"] is False
    assert report["write_db"] is False
    assert report["checks"]["provider_calls_blocked"] is True
    assert report["checks"]["single_call_in_every_provider_plan"] is True
    assert before_calls == after_calls
    assert before_ledger == after_ledger


def test_invoke_budget_block_records_zero_cost_ledger_without_calling_provider(monkeypatch) -> None:
    snapshots = {scope: _snapshot_scope(scope) for scope in ("monthly_total", "single_call", "provider:openai")}
    cost_scope = "cron:p4_evidence_summary"
    cost_snapshot = _snapshot_scope(cost_scope)
    called = {"openai": 0}

    def fake_openai(_prompt: str, _max_output_tokens: int) -> dict:
        called["openai"] += 1
        return {"status": "success", "provider": "openai", "model": "fake", "text": "should not run", "cost_cents": 1}

    try:
        _cleanup()
        monkeypatch.delenv("VKPI_LLM_GATEWAY_FORCE_OFFLINE", raising=False)
        monkeypatch.setenv("LLM_MONTHLY_BUDGET_USD", "999")
        # The production gateway now fail-closes every provider default unless
        # runtime verification evidence names the exact provider/model binding.
        # This test is about the downstream budget gate, so provide only that
        # narrow, synthetic binding while the provider transport remains mocked.
        monkeypatch.setenv(
            "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
            f"openai/{llm_gateway.PROVIDER_CONFIG['openai']['model']}",
        )
        allowed_binding = f"openai/{llm_gateway.PROVIDER_CONFIG['openai']['model']}"
        monkeypatch.setattr(
            llm_gateway,
            "exact_binding_readiness_from_environment",
            lambda binding: (
                {"binding": binding, "production_ready": binding == allowed_binding},
                {"source": "test_signed_readiness_fixture"},
            ),
        )
        monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider == "openai")
        monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", fake_openai)
        budget_guard.update_budget("monthly_total", {"cap_usd": 999, "current_spend": 0, "fallback_action": "fallback_to_rule_v0"})
        budget_guard.update_budget("provider:openai", {"cap_usd": 999, "current_spend": 0, "fallback_action": "fallback_to_rule_v0"})
        budget_guard.update_budget(cost_scope, {"cap_usd": 999, "current_spend": 0, "fallback_action": "fallback_to_rule_v0"})
        # single_call 是"每次调用上限":按 estimated_cost<=cap 硬拦,current_spend 不计入
        # (见 budget_guard._is_single_call_ceiling_scope 文档)。cap_usd 列精度 2 位小数,
        # 故最小可设上限=$0.01;只能靠"真实超额请求"触发——下方 max_output_tokens=10000
        # 让 openai 估算成本=$0.02 > cap $0.01,真正越线。
        # 关键:即便 skip_budget_check=True,invoke 的 per-provider 硬上限仍照常执行,
        # 成本护栏不被绕过(skip_budget_check 只跳过月度 env 预检,不跳 provider/single_call 上限)。
        budget_guard.update_budget("single_call", {"cap_usd": 0.01, "current_spend": 0, "fallback_action": "fallback_to_rule_v0"})

        result = llm_gateway.invoke(
            "Budget blocked test",
            purpose=f"{MARKER}-blocked",
            preferred_provider="openai",
            skip_budget_check=True,
            max_output_tokens=10000,
            cost_tag=cost_scope,
            metadata={"marker": MARKER},
        )

        assert called["openai"] == 0
        assert result["provider"] == "rule_v0"
        assert any(item.get("provider") == "openai" and item.get("status") == "budget_blocked" for item in result.get("errors") or [])
        rows = get_conn().execute(
            "SELECT * FROM vkpi_ai_cost_ledger WHERE metadata_json LIKE ?",
            (f"%{MARKER}%",),
        ).fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["ai_provider"] == "openai"
        assert float(row["cost_usd"] or 0) == 0.0
        metadata = json.loads(row["metadata_json"])
        assert metadata["status"] == "budget_blocked"
        assert metadata["budget_checks"]
    finally:
        _cleanup()
        for scope, snapshot in snapshots.items():
            _restore_scope(scope, snapshot)
        _restore_scope(cost_scope, cost_snapshot)
