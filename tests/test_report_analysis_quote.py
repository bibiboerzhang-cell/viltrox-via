"""报告深度分析的**报价**路径(接线 2026-08-25)。

深度分析是花钱动作,红线要求「点击前报出预计成本」且「报价必须零成本(纯 SELECT)」。
所以这个文件钉的是三件事,一件比一件重要:

1. ``dry_run=True`` **绝不调用任何模型** —— 用一个会炸的假 ``_generate`` 当探针,
   只要报价路径碰了它,测试立刻失败。
2. 报价里的金额与真正送进预算闸的是**同一个常量**,不许各写一份(漂了的那份正好是
   给用户看的那个数)。
3. 三种结果各自诚实:当天缓存 = 0 成本、额度用尽 = 明说、其余 = 报出预估值。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.dashboard import report_analysis


REPORT = (
    "报告 RPT-2026-08\n周期 2026-08-01 至 2026-08-31\n"
    "管理摘要：本月投放集中在广角镜头，转化集中在两个头部账号。\n合作数：18\nGMV：待数据"
)


class _Cursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _Conn:
    """只回答缓存那条 SELECT,并记录所有 SQL 以便断言「报价期一条写都没有」。"""

    def __init__(self, cached_row: dict[str, Any] | None = None) -> None:
        self.cached_row = cached_row
        self.seen: list[str] = []

    def execute(self, sql: str, params: Any = ()) -> _Cursor:
        self.seen.append(sql)
        if "SELECT analysis_json" in sql:
            return _Cursor(self.cached_row)
        return _Cursor(None)

    def commit(self) -> None:  # pragma: no cover - 报价路径不该走到
        raise AssertionError("报价路径不许写库")


@pytest.fixture
def _no_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """探针:报价路径一旦调用模型就炸。"""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("dry_run 报价绝不许调用模型")

    monkeypatch.setattr(report_analysis, "_generate", _boom)
    monkeypatch.setattr(report_analysis, "_ensure_schema", lambda: None)


def _bind(monkeypatch: pytest.MonkeyPatch, conn: _Conn, *, budget_ok: bool = True) -> None:
    monkeypatch.setattr(report_analysis, "get_conn", lambda: conn)
    monkeypatch.setattr(
        report_analysis.budget_guard, "check_budget", lambda *_a, **_k: budget_ok
    )


def test_quote_never_calls_a_model_and_never_writes(
    monkeypatch: pytest.MonkeyPatch, _no_model_calls: None
) -> None:
    conn = _Conn()
    _bind(monkeypatch, conn)

    result = report_analysis.quote(REPORT)

    assert result["dry_run"] is True
    assert result["available"] is True
    assert result["will_spend"] is True
    # 零成本 = 只有 SELECT。
    assert conn.seen and all("SELECT" in sql.upper() for sql in conn.seen)
    assert not any(sql.strip().upper().startswith("INSERT") for sql in conn.seen)


def test_quoted_amount_is_the_same_constant_the_budget_gate_receives(
    monkeypatch: pytest.MonkeyPatch, _no_model_calls: None
) -> None:
    """报给用户的金额必须就是送进预算闸的那个数,不许各写一份。"""

    seen_estimates: list[float] = []

    def _record(scope: str, estimate: float) -> bool:
        seen_estimates.append(estimate)
        return True

    conn = _Conn()
    monkeypatch.setattr(report_analysis, "get_conn", lambda: conn)
    monkeypatch.setattr(report_analysis.budget_guard, "check_budget", _record)

    result = report_analysis.quote(REPORT)

    assert seen_estimates == [report_analysis._EST_COST]
    assert result["estimated_cost_usd"] == report_analysis._EST_COST


def test_same_day_cache_hit_is_quoted_as_zero_cost(
    monkeypatch: pytest.MonkeyPatch, _no_model_calls: None
) -> None:
    conn = _Conn(cached_row={"analysis_json": '{"executive_summary":"x"}', "model": "m"})
    _bind(monkeypatch, conn)

    result = report_analysis.quote(REPORT)

    assert result == {
        "available": True, "dry_run": True, "cached": True,
        "will_spend": False, "estimated_cost_usd": 0.0,
    }


def test_budget_blocked_is_quoted_honestly(
    monkeypatch: pytest.MonkeyPatch, _no_model_calls: None
) -> None:
    _bind(monkeypatch, _Conn(), budget_ok=False)

    result = report_analysis.quote(REPORT)

    assert result["available"] is False
    assert result["reason"] == "budget_blocked"
    assert result["will_spend"] is False
    assert result["estimated_cost_usd"] == 0.0


def test_too_short_report_is_rejected_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch, _no_model_calls: None
) -> None:
    conn = _Conn()
    _bind(monkeypatch, conn)

    result = report_analysis.quote("太短了")

    assert result == {"available": False, "dry_run": True, "reason": "report_too_short"}
    assert conn.seen == [], "长度不合格就该直接回绝,不必查库"


def test_analyze_dry_run_delegates_to_quote(
    monkeypatch: pytest.MonkeyPatch, _no_model_calls: None
) -> None:
    """端点传下来的 dry_run 必须真的走报价分支,而不是跑完再丢掉结果。"""

    _bind(monkeypatch, _Conn())

    result = report_analysis.analyze(REPORT, dry_run=True)

    assert result["dry_run"] is True
    assert result["will_spend"] is True


def test_analyze_defaults_to_spending_path_not_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """``dry_run`` 默认 False —— 报价分支绝不许悄悄变成默认行为,
    否则「执行」那一下会变成什么都不做,用户以为跑过了。"""

    monkeypatch.setattr(report_analysis, "_ensure_schema", lambda: None)
    _bind(monkeypatch, _Conn(), budget_ok=False)

    result = report_analysis.analyze(REPORT)

    # 走的是真执行路径(被预算闸拦下),而不是报价路径。
    assert "dry_run" not in result
    assert result["reason"] == "budget_blocked"
