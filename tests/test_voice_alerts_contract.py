"""市场之声「声量告警」(V0f)契约测试 —— 零 DB 依赖(mock conn)。

覆盖点(施工单验收口径):
  1. SQL 常量静态审查:参数化 ? / 零字面 percent / 窗口过滤下推 SQL;
  2. 窗口过滤:since/until 参数 = now-8h → now(下推到 vkpi_comments 查询);
  3. 阈值触发:3 条普通负面(×1)= 3 分 ≥ 3 触发;2 条 = 2 分不触发;
     好评(话题词命中但无负面线索)不计——lexicon_v0 双命中判据复用回归;
  4. 官号 ×2 权重:2 条官号帖负面(post_table='vkpi_employee_channels')= 4 分 ≥ 3 触发;
  5. 幂等:dedupe_key = voice_alert:{类别}:{UTC日期},同日同类别两次推送键完全一致
     (vkpi_action_inbox.dedupe_key UNIQUE + ON CONFLICT 只刷 suggested → 不重推);
     写入走现成 actions 通道 inbox.persist_suggestions,纯提醒(不需审批/零业务写/零 LLM);
  6. enabled 默认关:scheduler_tasks 注册表无行 → job 空跑即返回,不触 evaluate/push。
红线:纯读契约,不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.market import voice_alerts  # noqa: E402


# ── mock conn(仿 tests 现有 _FakeConn 风格,记录每次 execute 的 SQL+参数)──


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return _FakeResult(self.rows)


_NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


def _comment(idx: int, text: str, *, post_table: str = "vkpi_kol_video_evidence", likes: int = 0):
    return {
        "id": idx,
        "platform": "youtube",
        "comment_text": text,
        "post_table": post_table,
        "likes_count": likes,
        "at_ts": (_NOW - timedelta(hours=1)).isoformat(),
    }


# 负面对焦抱怨:话题词 "autofocus" + 负面线索 "terrible" 同条命中(lexicon_v0 双命中)。
_NEG_AF = "The autofocus is terrible on this lens"
# 好评:话题词命中("sharp" → 画质)但无负面线索 → 不计。
_PRAISE = "Beautiful sharp images, love it"


# ── 1. SQL 常量静态审查 ─────────────────────────────────────────────────


def test_sql_constant_parameterized_no_percent():
    sql = voice_alerts.ALERT_SELECT_SQL
    assert sql.count("?") == 3  # since / until / limit 全参数化
    assert "%" not in sql  # compat 红线:零字面 percent
    assert " LIKE " not in f" {sql.upper()} ".replace("\n", " ")
    # 窗口过滤下推 SQL,时间口径与 market_voice 同款(created_at 空用 fetched_at 兜底)
    assert "COALESCE(created_at, fetched_at) >= CAST(? AS TIMESTAMPTZ)" in sql
    assert "COALESCE(created_at, fetched_at) < CAST(? AS TIMESTAMPTZ)" in sql
    # 显示层宪法:个人字段不入 SELECT
    for forbidden in ("author_handle", "author_id", "raw_data_json"):
        assert forbidden not in sql


def test_default_constants():
    assert voice_alerts.WINDOW_HOURS_DEFAULT == 8
    assert voice_alerts.THRESHOLD_DEFAULT == 3
    assert voice_alerts.OWNED_WEIGHT == 2
    assert voice_alerts.OWNED_POST_TABLE == "vkpi_employee_channels"


# ── 2. 窗口过滤 ─────────────────────────────────────────────────────────


def test_window_filter_params_pushed_down():
    conn = _FakeConn(rows=[])
    result = voice_alerts.evaluate_voice_alerts(conn=conn, now=_NOW)
    assert len(conn.calls) == 1
    _, params = conn.calls[0]
    since, until, limit = params
    assert datetime.fromisoformat(until) == _NOW
    assert datetime.fromisoformat(until) - datetime.fromisoformat(since) == timedelta(hours=8)
    assert limit == voice_alerts.SCAN_LIMIT
    assert result["window_hours"] == 8 and result["threshold"] == 3
    assert result["since"] == since and result["until"] == until


# ── 3. 阈值触发 ─────────────────────────────────────────────────────────


def test_threshold_three_plain_negatives_trigger():
    rows = [_comment(i, _NEG_AF) for i in (1, 2, 3)] + [_comment(9, _PRAISE)]
    result = voice_alerts.evaluate_voice_alerts(conn=_FakeConn(rows=rows), now=_NOW)
    triggered = {c["key"]: c for c in result["triggered"]}
    assert "autofocus" in triggered
    cat = triggered["autofocus"]
    assert cat["score"] == 3 and cat["count"] == 3 and cat["owned_count"] == 0
    # 好评不计:image_quality(sharp 话题词命中但无负面线索)不得触发
    assert "image_quality" not in triggered


def test_below_threshold_no_trigger():
    rows = [_comment(i, _NEG_AF) for i in (1, 2)]
    result = voice_alerts.evaluate_voice_alerts(conn=_FakeConn(rows=rows), now=_NOW)
    assert result["triggered"] == []
    by_key = {c["key"]: c for c in result["categories"]}
    assert by_key["autofocus"]["score"] == 2 and by_key["autofocus"]["triggered"] is False


# ── 4. 官号帖 ×2 权重 ───────────────────────────────────────────────────


def test_owned_posts_double_weight_trigger():
    # 2 条官号负面 = 2×2 = 4 分 ≥ 3 → 触发(同样 2 条普通帖只有 2 分不触发,见上一测)
    rows = [
        _comment(1, _NEG_AF, post_table="vkpi_employee_channels"),
        _comment(2, _NEG_AF, post_table="vkpi_employee_channels", likes=5),
    ]
    result = voice_alerts.evaluate_voice_alerts(conn=_FakeConn(rows=rows), now=_NOW)
    triggered = {c["key"]: c for c in result["triggered"]}
    assert "autofocus" in triggered
    cat = triggered["autofocus"]
    assert cat["score"] == 4 and cat["count"] == 2 and cat["owned_count"] == 2
    # 引文样本带 owned 标记 + comment_id 溯源,零个人字段
    quote = cat["quotes"][0]
    assert quote["owned"] is True and quote["comment_id"] in (1, 2)
    assert "author" not in quote and "author_handle" not in quote


# ── 5. 推送幂等 + actions 现成通道 ──────────────────────────────────────


def test_push_idempotent_dedupe_key_same_day(monkeypatch):
    from app.domains.actions import inbox

    captured: list[list[dict]] = []

    def _fake_persist(suggestions):
        captured.append(list(suggestions))
        return len(suggestions)

    monkeypatch.setattr(inbox, "persist_suggestions", _fake_persist)

    rows = [_comment(i, _NEG_AF) for i in (1, 2, 3)]
    for _ in range(2):  # 同日两次推送
        out = voice_alerts.push_voice_alerts(conn=_FakeConn(rows=rows), now=_NOW)
        assert out["status"] == "ok" and out["triggered"] == 1 and out["pushed"] == 1
        assert out["triggered_categories"] == ["autofocus"]

    assert len(captured) == 2
    keys_run1 = [s["dedupe_key"] for s in captured[0]]
    keys_run2 = [s["dedupe_key"] for s in captured[1]]
    # 幂等口径:同类别同日 dedupe_key 完全一致 → UNIQUE upsert 只落一行,不重推
    assert keys_run1 == keys_run2 == ["voice_alert:autofocus:2026-07-11"]

    s = captured[0][0]
    assert s["category"] == voice_alerts.ALERT_CATEGORY == "voice_alert"
    # 纯提醒契约:零业务写 / 零 LLM / 不需审批 / 无执行端点 / 公司级
    assert s["writes_business_data"] is False
    assert s["uses_llm"] is False
    assert s["requires_approval"] is False
    assert s["suggested_endpoint"] == ""
    assert s["owner_staff_id"] is None
    assert s["payload"]["score"] == 3 and s["payload"]["owned_weight"] == 2
    assert s["evidence_refs"] and s["evidence_refs"][0]["type"] == "vkpi_comments"


def test_push_no_trigger_writes_nothing(monkeypatch):
    from app.domains.actions import inbox

    def _boom(_suggestions):  # 未触发绝不该走到写通道
        raise AssertionError("persist_suggestions must not be called when nothing triggered")

    monkeypatch.setattr(inbox, "persist_suggestions", _boom)
    out = voice_alerts.push_voice_alerts(conn=_FakeConn(rows=[_comment(1, _PRAISE)]), now=_NOW)
    assert out["triggered"] == 0 and out["pushed"] == 0 and out["status"] == "ok"


# ── 6. 调度 config-gate:enabled 默认关 ─────────────────────────────────


def test_scheduler_gate_default_off(monkeypatch):
    from app.services.scheduler import jobs_tasks_intel

    # 注册表无此行(表都不在)→ _scheduler_task_enabled 默认 False(保守、诚实)
    import app.db.connection as db_conn

    monkeypatch.delenv("OPS_SCHEDULER_FORCE_ENABLE", raising=False)
    monkeypatch.setattr(db_conn, "table_exists", lambda _t: False)

    called: list[str] = []
    monkeypatch.setattr(
        voice_alerts, "push_voice_alerts", lambda *a, **k: called.append("push") or {}
    )
    asyncio.run(jobs_tasks_intel.job_market_voice_alerts())
    assert called == []  # gate 关 → 空跑即返回,evaluate/push 全不触


def test_scheduler_gate_on_runs_push(monkeypatch):
    from app.services.scheduler import jobs_tasks_intel

    asked: list[str] = []

    def _gate(task_key: str) -> bool:
        asked.append(task_key)
        return True

    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", _gate)

    recorded: list[tuple] = []
    monkeypatch.setattr(
        jobs_tasks_intel, "_record_scheduler_result", lambda k, r: recorded.append((k, r))
    )
    monkeypatch.setattr(
        voice_alerts,
        "push_voice_alerts",
        lambda *a, **k: {"status": "ok", "triggered": 0, "pushed": 0, "scanned": 0},
    )
    asyncio.run(jobs_tasks_intel.job_market_voice_alerts())
    assert asked == ["market_voice_alerts"]
    assert recorded and recorded[0][0] == "market_voice_alerts"
    assert recorded[0][1]["status"] == "ok"
