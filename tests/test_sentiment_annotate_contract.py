"""V0g 情绪批注管线契约测试(零 DB、零 LLM)。

覆盖任务书五门:
  1. dry_run 绝不触 LLM、绝不写库(invoke 装地雷,INSERT/UPDATE 零出现);
  2. 打包 prompt 含全部 id(build_packed_prompt + 真跑捕获的 prompt 双向验);
  3. 解析→落库→sentiment_id 回填映射逐 id 正确(乱序/围栏/陌生 id/非法 label 容错);
  4. 每次运行硬上限(env VKPI_SENTIMENT_ANNOTATE_MAX_PER_RUN)真生效(LIMIT 下推);
  5. 调度闸默认关(注册表无行 → job 直接 return,不碰 annotate_batch)。
附加:gateway 被闸(fallback_to_rule)→ 停跑零落库,绝不写假中性占坑。
红线:mock conn 全程,不触真库;零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.market import sentiment_annotate as sa  # noqa: E402


# ── mock conn(仿 tests 现有 _FakeConn 风格,按 SQL 关键词路由)──


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, pending_rows=None, pending_total=None, existing_results=None):
        self.pending_rows = list(pending_rows or [])
        self.pending_total = pending_total if pending_total is not None else len(self.pending_rows)
        self.existing_results = dict(existing_results or {})  # comment_id -> result_id(结果缓存)
        self.calls: list[tuple[str, tuple]] = []
        self.inserted: dict[int, dict] = {}  # comment_id -> insert params dict
        self.links: list[tuple[int, int]] = []  # (sentiment_id, comment_id)
        self.committed = False
        self._next_result_id = 9000

    def execute(self, sql, params=()):
        flat = " ".join(str(sql).split())
        low = flat.lower()
        self.calls.append((flat, tuple(params)))
        if "count(*)" in low and "vkpi_comments" in low:
            return _FakeResult([{"n": self.pending_total}])
        if low.startswith("select") and "from vkpi_comments" in low:
            limit = int(params[-1])
            return _FakeResult(self.pending_rows[:limit])
        if low.startswith("select comment_id, id from vkpi_sentiment_results"):
            hits = [
                {"comment_id": cid, "id": rid}
                for cid, rid in self.existing_results.items()
                if cid in {int(p) for p in params}
            ]
            return _FakeResult(hits)
        if low.startswith("insert into vkpi_sentiment_results"):
            cid = int(params[0])
            self.inserted[cid] = {
                "sentiment": params[1],
                "confidence": params[2],
                "provider": params[3],
                "model": params[4],
                "prompt_version": params[5],
            }
            if cid not in self.existing_results:
                self.existing_results[cid] = self._next_result_id
                self._next_result_id += 1
            return _FakeResult([])
        if low.startswith("select id from vkpi_sentiment_results"):
            cid = int(params[0])
            rid = self.existing_results.get(cid)
            return _FakeResult([{"id": rid}] if rid else [])
        if low.startswith("update vkpi_comments set sentiment_id"):
            self.links.append((int(params[0]), int(params[1])))
            return _FakeResult([])
        return _FakeResult([])

    def commit(self):
        self.committed = True

    # ── 断言辅助 ──
    def write_calls(self):
        return [c for c in self.calls if c[0].lower().startswith(("insert", "update", "delete"))]


def _rows(n, start=100):
    return [
        {"id": start + i, "comment_text": f"comment number {start + i} about the lens", "language_detected": "en"}
        for i in range(n)
    ]


def _mine_invoke(monkeypatch):
    """把 llm_gateway.invoke 换成地雷:被碰即爆。"""

    def _boom(*args, **kwargs):  # pragma: no cover - 爆了就是 bug
        raise AssertionError("llm_gateway.invoke must NOT be called")

    monkeypatch.setattr(sa.llm_gateway, "invoke", _boom)


# ── 1. dry_run:零 LLM、零落库,返回预估 ─────────────────────────────


def test_dry_run_never_touches_llm_or_db(monkeypatch):
    _mine_invoke(monkeypatch)
    conn = _FakeConn(pending_rows=_rows(10), pending_total=12703)

    result = sa.annotate_batch(10, dry_run=True, conn=conn)

    assert result["mode"] == "dry_run"
    assert result["pending_total"] == 12703
    assert result["selected"] == 10
    assert result["llm_calls_planned"] == 1
    assert result["estimated_input_tokens"] > 0
    assert result["estimated_output_tokens"] > 0
    assert result["estimated_cost_usd"] > 0
    assert conn.write_calls() == []  # 零 INSERT/UPDATE
    assert "annotated" not in result  # dry-run 不假装跑过


def test_dry_run_is_the_default(monkeypatch):
    _mine_invoke(monkeypatch)
    conn = _FakeConn(pending_rows=_rows(3))
    result = sa.annotate_batch(3, conn=conn)  # 不传 dry_run
    assert result["mode"] == "dry_run"
    assert conn.write_calls() == []


# ── 2. 打包 prompt 含全部 id ─────────────────────────────────────────


def test_packed_prompt_contains_every_id_and_contract():
    items = _rows(37)
    prompt = sa.build_packed_prompt(items)
    for it in items:
        assert f"[{it['id']}]" in prompt
    assert "JSON array" in prompt
    assert '"score"' in prompt and '"label"' in prompt and '"aspects"' in prompt


def test_live_run_prompt_packs_all_selected_ids(monkeypatch):
    captured: list[str] = []

    def fake_invoke(prompt, **kwargs):
        captured.append(prompt)
        ids = [it["id"] for it in _rows(5)]
        payload = [{"id": i, "score": 0.5, "label": "pos", "aspects": []} for i in ids]
        return {
            "status": "success",
            "text": json.dumps(payload),
            "provider": "google",
            "model": "gemini-flash-latest",
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_micro_usd": 210,
        }

    monkeypatch.setattr(sa.llm_gateway, "invoke", fake_invoke)
    conn = _FakeConn(pending_rows=_rows(5))
    result = sa.annotate_batch(5, dry_run=False, conn=conn)

    assert len(captured) == 1  # 5 条 < pack_size → 一次调用打包
    for it in _rows(5):
        assert f"[{it['id']}]" in captured[0]
    assert result["annotated"] == 5


# ── 3. 解析回填映射正确(乱序/围栏/陌生 id/非法 label)──────────────


def test_parse_batch_response_tolerates_noise():
    expected = {101, 102, 103}
    text = (
        "```json\n"
        "[\n"
        '  {"id": 103, "score": -0.8, "label": "neg", "aspects": ["autofocus", " price "]},\n'
        '  {"id": 999, "score": 1.0, "label": "pos", "aspects": []},\n'
        '  {"id": 102, "score": 2.5, "label": "positive", "aspects": "notalist"},\n'
        '  {"id": 101, "score": 0.0, "label": "banana", "aspects": []}\n'
        "]\n"
        "```"
    )
    parsed = sa.parse_batch_response(text, expected)
    assert set(parsed.keys()) == {102, 103}  # 999 陌生 id 丢弃;101 非法 label 丢弃
    assert parsed[103]["sentiment"] == "negative"
    assert parsed[103]["aspects"] == ["autofocus", "price"]
    assert parsed[102]["sentiment"] == "positive"
    assert parsed[102]["score"] == 1.0  # 2.5 钳制到 1.0
    assert parsed[102]["aspects"] == []  # 非 list 的 aspects 归空


def test_live_run_persists_and_links_per_id(monkeypatch):
    rows = _rows(3)  # ids 100,101,102
    payload = [
        {"id": 102, "score": -0.9, "label": "neg", "aspects": ["price"]},
        {"id": 100, "score": 0.7, "label": "pos", "aspects": ["autofocus"]},
        {"id": 101, "score": 0.05, "label": "neu", "aspects": []},
    ]

    def fake_invoke(prompt, **kwargs):
        return {
            "status": "success",
            "text": "```json\n" + json.dumps(payload) + "\n```",
            "provider": "google",
            "model": "gemini-flash-latest",
            "input_tokens": 300,
            "output_tokens": 120,
            "cost_micro_usd": 570,
        }

    monkeypatch.setattr(sa.llm_gateway, "invoke", fake_invoke)
    conn = _FakeConn(pending_rows=rows)
    result = sa.annotate_batch(3, dry_run=False, conn=conn)

    assert result["annotated"] == 3
    assert conn.inserted[100]["sentiment"] == "positive"
    assert conn.inserted[101]["sentiment"] == "neutral"
    assert conn.inserted[102]["sentiment"] == "negative"
    assert conn.inserted[102]["confidence"] == 0.9  # |score| 作强度
    assert all(v["prompt_version"] == sa.PROMPT_VERSION for v in conn.inserted.values())
    # 回填映射:每条评论链到自己那行结果(guard: sentiment_id IS NULL 在 SQL 里)
    linked = {cid: rid for rid, cid in conn.links}
    assert set(linked.keys()) == {100, 101, 102}
    assert linked[100] == conn.existing_results[100]
    assert linked[102] == conn.existing_results[102]
    assert result["aspects_top"] == {"price": 1, "autofocus": 1}
    assert conn.committed is True


def test_cache_reuse_links_without_llm(monkeypatch):
    _mine_invoke(monkeypatch)  # 全部命中缓存 → 一次 invoke 都不许有
    rows = _rows(2)  # ids 100,101
    conn = _FakeConn(pending_rows=rows, existing_results={100: 501, 101: 502})

    result = sa.annotate_batch(2, dry_run=False, conn=conn)

    assert result["linked_from_cache"] == 2
    assert result["annotated"] == 0
    assert result["llm_calls_planned"] == 0
    assert sorted(conn.links) == [(501, 100), (502, 101)]
    assert conn.inserted == {}  # 缓存复用只回链,不重写结果行


# ── 4. 硬上限生效 ────────────────────────────────────────────────────


def test_hard_cap_env_clamps_batch_size(monkeypatch):
    _mine_invoke(monkeypatch)
    monkeypatch.setenv(sa.HARD_CAP_ENV, "5")
    conn = _FakeConn(pending_rows=_rows(50), pending_total=50)

    result = sa.annotate_batch(50, dry_run=True, conn=conn)

    assert result["hard_cap"] == 5
    assert result["selected"] == 5
    select_calls = [c for c in conn.calls if "from vkpi_comments" in c[0].lower() and "limit" in c[0].lower()]
    assert select_calls and select_calls[0][1] == (5,)  # LIMIT 参数化下推


def test_pack_size_env_splits_calls(monkeypatch):
    _mine_invoke(monkeypatch)
    monkeypatch.setenv(sa.PACK_SIZE_ENV, "25")
    conn = _FakeConn(pending_rows=_rows(60), pending_total=60)
    result = sa.annotate_batch(60, dry_run=True, conn=conn)
    assert result["selected"] == 60
    assert result["pack_size"] == 25
    assert result["llm_calls_planned"] == 3  # ceil(60/25)


# ── 5. 调度闸默认关 ──────────────────────────────────────────────────


def test_job_gate_default_off_without_registry(monkeypatch):
    """注册表缺失(table_exists=False)→ _scheduler_task_enabled 默认 False。"""
    monkeypatch.delenv("OPS_SCHEDULER_FORCE_ENABLE", raising=False)
    import app.db.connection as db_conn
    from app.services.scheduler import jobs_tasks

    monkeypatch.setattr(db_conn, "table_exists", lambda name: False)
    assert jobs_tasks._scheduler_task_enabled("vkpi_sentiment_annotate") is False


def test_job_returns_early_when_gate_off(monkeypatch):
    from app.services.scheduler import jobs_tasks_intel

    called = {}
    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", lambda key: False)
    monkeypatch.setattr(sa, "annotate_batch", lambda *a, **k: called.setdefault("ran", True))

    asyncio.run(jobs_tasks_intel.job_sentiment_annotate())

    assert called == {}  # 闸关 → 碰都不碰 annotate_batch


def test_job_runs_live_capped_when_gate_on(monkeypatch):
    from app.services.scheduler import jobs_tasks_intel

    seen = {}
    monkeypatch.setattr(jobs_tasks_intel, "_scheduler_task_enabled", lambda key: key == "vkpi_sentiment_annotate")
    monkeypatch.setattr(jobs_tasks_intel, "_record_scheduler_run", lambda *a, **k: None)
    monkeypatch.setenv(sa.HARD_CAP_ENV, "200")

    def fake_annotate(batch_size, *, dry_run=True, **kwargs):
        seen["batch_size"] = batch_size
        seen["dry_run"] = dry_run
        return {"selected": 0, "annotated": 0, "halted_reason": ""}

    monkeypatch.setattr(sa, "annotate_batch", fake_annotate)
    asyncio.run(jobs_tasks_intel.job_sentiment_annotate())

    assert seen == {"batch_size": 200, "dry_run": False}


# ── 附加:gateway 被闸 → 停跑零落库 ─────────────────────────────────


def test_gateway_fallback_halts_without_writes(monkeypatch):
    def fake_invoke(prompt, **kwargs):
        return {"status": "fallback_to_rule", "reason": "budget_disabled", "provider": "rule_v0", "text": ""}

    monkeypatch.setattr(sa.llm_gateway, "invoke", fake_invoke)
    conn = _FakeConn(pending_rows=_rows(4))
    result = sa.annotate_batch(4, dry_run=False, conn=conn)

    assert result["annotated"] == 0
    assert result["halted_reason"] == "budget_disabled"
    assert conn.inserted == {}
    assert conn.links == []  # 绝不写假中性占坑
