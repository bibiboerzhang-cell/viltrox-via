"""Characterization lock for shadow_eval._run_forecast_backtest (CC 刀).

冻结口径(原码全绿,动刀后必须原样绿):
  - 样本筛选:is_active 宽容判真 + 必须有真实播放数 + evidence_id 去重 + MAX_SAMPLES 截断;
  - 留一回测:窗口切分(排除自身 evidence / 排除自身 actual)与分位数算式逐位保持;
  - verdict 三分支(challenger_wins / baseline_wins / mixed)与 recommendation 文案逐字;
  - 三种诚实态 payload(unavailable / 无合格样本 / 全部历史不足)逐键相等;
  - fingerprint 决定性:两次运行逐位一致,且可在测试内独立复算;
  - SQL 兼容:全部 ? 占位符、零字面 percent。

golden sha256 由动刀前原码实跑冻结;凡改变任何键/值/顺序/文案都会在此炸开。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from app.domains.learning import shadow_eval

FROZEN_NOW = "2026-08-30T12:00:00+00:00"


# ── 决定性摘要(house style:canonical json → sha256)──────────────────


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ── 假连接(只读三查询:cache 候选 / evidence 历史 / pool handle)──────


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConn:
    def __init__(
        self,
        candidates: list[dict[str, Any]],
        history_rows: list[dict[str, Any]],
        handle_rows: list[dict[str, Any]],
    ) -> None:
        self.candidates = candidates
        self.history_rows = history_rows
        self.handle_rows = handle_rows
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        self.queries.append((sql, tuple(params)))
        if "vkpi_analysis_cache" in sql:
            rows = list(self.candidates)[: int(params[-1])]
        elif "vkpi_kol_video_evidence" in sql:
            wanted = set(params)
            rows = sorted(
                (r for r in self.history_rows if r["kol_pool_id"] in wanted),
                key=lambda r: (r["kol_pool_id"], r["evidence_id"]),
            )
        elif "vkpi_kol_pool" in sql:
            wanted = set(params)
            rows = [r for r in self.handle_rows if r["id"] in wanted]
        else:  # pragma: no cover - 拼错查询直接炸,零静默
            raise AssertionError(f"unexpected sql: {sql}")
        return _Rows([dict(r) for r in rows])


def _cand(
    eid: Any,
    kol: Any,
    views: Any,
    active: Any = 1,
    title: str = "vid",
    platform: str = "youtube",
) -> dict[str, Any]:
    return {
        "evidence_id": eid,
        "kol_pool_id": kol,
        "title": title,
        "platform": platform,
        "view_count": views,
        "is_active": active,
    }


def _hist(kol: int, eid: int, views: Any, active: Any = 1) -> dict[str, Any]:
    return {"kol_pool_id": kol, "evidence_id": eid, "view_count": views, "is_active": active}


# ── 场景数据 ──────────────────────────────────────────────────────────


def _rich_conn() -> _FakeConn:
    """混合场景:去重 + 过滤(inactive/无播放/坏 id)+ 历史不足跳过 + handle 兜底。"""
    candidates = [
        _cand(1, 10, 1000, title="first"),
        _cand(1, 10, 1000, title="dup-must-lose"),  # 同 evidence 第二行:去重后不生效
        _cand(2, 10, 1100),
        _cand(3, 10, 1200),
        _cand(4, 10, 900),
        _cand(6, 20, 50000),  # KOL20 留一历史只剩 1 条 → 诚实跳过
        _cand(8, 30, 200),
        _cand(9, 30, 220),
        _cand(11, 30, 20000),
        _cand(12, 10, 500, active=0),  # inactive → 过滤
        _cand(13, 10, None),  # 无播放数 → 过滤
        _cand(14, 10, 0),  # 播放数 0 → 过滤
        _cand(None, 10, 700),  # evidence_id 坏 → 过滤
        _cand(15, None, 800),  # kol_pool_id 坏 → 过滤
    ]
    history_rows = [
        _hist(10, 1, 1000),
        _hist(10, 2, 1100),
        _hist(10, 3, 1200),
        _hist(10, 4, 900),
        _hist(10, 5, 1000),
        _hist(10, 12, 500, active=0),  # inactive 历史 → 不入池
        _hist(10, 13, None),  # 无播放历史 → 不入池
        _hist(20, 6, 50000),
        _hist(20, 7, 60000),
        _hist(30, 8, 200),
        _hist(30, 9, 220),
        _hist(30, 10, 240),
        _hist(30, 11, 20000),
    ]
    handle_rows = [
        {"id": 10, "handle": "alpha", "display_name": "Alpha Name"},
        {"id": 20, "handle": "", "display_name": "Beta Display"},
        {"id": 30, "handle": None, "display_name": ""},  # 双空 → per_kol handle=None
    ]
    return _FakeConn(candidates, history_rows, handle_rows)


def _challenger_wins_conn() -> _FakeConn:
    """各 KOL 历史紧贴自身量级、全局池横跨四个数量级 → 挑战者双赢。"""
    candidates = [_cand(1, 1, 100), _cand(2, 2, 10000), _cand(3, 3, 1000000)]
    history_rows = [
        _hist(1, 1, 100),
        _hist(1, 11, 90),
        _hist(1, 12, 95),
        _hist(1, 13, 105),
        _hist(1, 14, 110),
        _hist(2, 2, 10000),
        _hist(2, 21, 9000),
        _hist(2, 22, 9500),
        _hist(2, 23, 10500),
        _hist(2, 24, 11000),
        _hist(3, 3, 1000000),
        _hist(3, 31, 900000),
        _hist(3, 32, 950000),
        _hist(3, 33, 1050000),
        _hist(3, 34, 1100000),
    ]
    handle_rows = [
        {"id": 1, "handle": "kol-a", "display_name": ""},
        {"id": 2, "handle": "kol-b", "display_name": ""},
        {"id": 3, "handle": "kol-c", "display_name": ""},
    ]
    return _FakeConn(candidates, history_rows, handle_rows)


def _baseline_wins_conn() -> _FakeConn:
    """全部样本实际播放一致(全局池完美)、各 KOL 其余历史误导 → 对照组双赢。"""
    candidates = [_cand(1, 1, 1000), _cand(2, 2, 1000), _cand(3, 3, 1000)]
    history_rows = [
        _hist(1, 1, 1000),
        _hist(1, 11, 10),
        _hist(1, 12, 20),
        _hist(1, 13, 30),
        _hist(2, 2, 1000),
        _hist(2, 21, 5),
        _hist(2, 22, 15),
        _hist(2, 23, 25),
        _hist(3, 3, 1000),
        _hist(3, 31, 2000000),
        _hist(3, 32, 3000000),
        _hist(3, 33, 4000000),
    ]
    handle_rows = [
        {"id": 1, "handle": "a", "display_name": ""},
        {"id": 2, "handle": "b", "display_name": ""},
        {"id": 3, "handle": "c", "display_name": ""},
    ]
    return _FakeConn(candidates, history_rows, handle_rows)


def _mixed_conn() -> _FakeConn:
    """全体播放数相同 → 两法指标完全打平 → 未达「双赢才上线」→ mixed。"""
    candidates = []
    history_rows = []
    handle_rows = []
    eid = 0
    for kol in (1, 2, 3):
        handle_rows.append({"id": kol, "handle": f"k{kol}", "display_name": ""})
        for _ in range(4):
            eid += 1
            candidates.append(_cand(eid, kol, 1000))
            history_rows.append(_hist(kol, eid, 1000))
    return _FakeConn(candidates, history_rows, handle_rows)


def _all_skipped_conn() -> _FakeConn:
    """每个 KOL 只有 2 条证据 → 留一后历史 1 条 < MIN_HISTORY → 全部诚实跳过。"""
    candidates = [_cand(1, 1, 500), _cand(3, 2, 600)]
    history_rows = [_hist(1, 1, 500), _hist(1, 2, 700), _hist(2, 3, 600), _hist(2, 4, 800)]
    handle_rows = [
        {"id": 1, "handle": "x", "display_name": ""},
        {"id": 2, "handle": "y", "display_name": ""},
    ]
    return _FakeConn(candidates, history_rows, handle_rows)


@pytest.fixture()
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(shadow_eval, "_utcnow_iso", lambda: FROZEN_NOW)
    return FROZEN_NOW


def _run(conn: _FakeConn) -> dict[str, Any]:
    return shadow_eval._run_forecast_backtest(conn=conn)


# ── golden sha256(动刀前原码实跑冻结)────────────────────────────────

GOLDEN_SHA256 = {
    "rich": "a6332654a680850afb5fa846f95348cfef9a96b6c2683d8626d8daf6fb5fd896",
    "challenger_wins": "acfa626b34154930d0fc571b7167514cf725bb23645bd37100db2ded33cca1f9",
    "baseline_wins": "e11d464f30c46d0e4df527b119056679b55150e167b57bab122b11853907e51e",
    "mixed": "432ee6c8f12c11e67ee106385c07e303a4e3c5b918925a4740b6623155082e79",
    "all_skipped": "602fc0667ccbbc60273989ff409f3c2aea4ec8d23d287787cf9deec1bfe1f18b",
    "empty": "cdf986b6ff8013c768fb2688fa1a1a81b15498431aeef5ae0db31d868543ec5b",
    "unavailable": "acb85be35b93c9bf0af234fb468fbf509a60a8c32a27bdd382571d9b0eb7453b",
}


# ── 诚实态三分支 ──────────────────────────────────────────────────────


def test_unavailable_when_forecast_module_missing(
    frozen_now: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shadow_eval, "_forecast_tools", lambda: None)
    result = _run(_rich_conn())
    assert result == {
        "status": "unavailable",
        "eval": "forecast_backtest",
        "reason": "performance_forecast 模块不可用,分位数口径无从复算 — 本轮评测不出结论。",
        "generated_at": frozen_now,
    }
    assert _digest(result) == GOLDEN_SHA256["unavailable"]


def test_empty_when_no_candidate_survives_filters(frozen_now: str) -> None:
    conn = _FakeConn(
        [_cand(1, 10, 500, active=0), _cand(2, 10, None), _cand(3, 10, 0)],
        [],
        [],
    )
    result = _run(conn)
    assert result == {
        "status": "empty",
        "eval": "forecast_backtest",
        "reason": (
            "扫描 3 条 final_v1 深析记录,没有一条同时满足"
            "「evidence 有效 + 有真实播放数」— 等抓取回填 view_count 后再评。"
        ),
        "samples": {"candidates_scanned": 3, "eligible": 0},
        "generated_at": frozen_now,
    }
    assert _digest(result) == GOLDEN_SHA256["empty"]
    # 无合格样本时绝不该发历史/handle 查询(只扫了一次 cache)。
    assert len(conn.queries) == 1


def test_empty_when_every_sample_lacks_history(frozen_now: str) -> None:
    result = _run(_all_skipped_conn())
    assert result == {
        "status": "empty",
        "eval": "forecast_backtest",
        "reason": (
            "2 条合格样本全部因「留一后 KOL 历史有播放样本 <3」被跳过,"
            "分位数回测无从谈起 — 等各 KOL 证据量长起来再评。"
        ),
        "samples": {
            "candidates_scanned": 2,
            "eligible": 2,
            "evaluated": 0,
            "skipped_insufficient_history": 2,
        },
        "generated_at": frozen_now,
    }
    assert _digest(result) == GOLDEN_SHA256["all_skipped"]


# ── ready 主路径:窗口切分 + 评分算式 + verdict 三分支 ────────────────


def test_rich_payload_locks_filters_dedup_and_skip_accounting(frozen_now: str) -> None:
    result = _run(_rich_conn())
    assert result["status"] == "ready"
    assert result["eval"] == "forecast_backtest"
    assert result["method"] == "loo_backtest_v1"
    assert result["verdict"] == "mixed"  # 带内率输、误差赢 → 各有胜负
    assert result["samples"] == {
        "candidates_scanned": 14,
        "eligible": 8,
        "evaluated": 7,
        "skipped_insufficient_history": 1,
        "kol_count": 2,
        "cap": 200,
        "min_history": 3,
        "ordering": "evidence_id ASC(决定性遍历,禁止随机抽样)",
    }
    # per_kol 决定性排序:样本数降序 → kol_pool_id 升序;handle 双空兜底为 None。
    assert [(row["kol_pool_id"], row["handle"], row["sample_count"]) for row in result["per_kol"]] == [
        (10, "alpha", 4),
        (30, None, 3),
    ]
    assert result["challenger"]["name"] == "evidence_quantile_v1(分 KOL 留一 p10/p50/p90)"
    assert result["baseline"]["name"] == "global_median_v0(全局留一中位数点预测 + 全局 p10~p90 带)"
    assert result["challenger"]["evaluated"] == 7
    assert result["evidence"]["win_rule"] == (
        "挑战者带内率≥对照组 且 中位相对误差≤对照组,且至少一项严格更优,才判 challenger_wins。"
    )
    assert result["generated_at"] == frozen_now
    assert _digest(result) == GOLDEN_SHA256["rich"]


def test_challenger_wins_verdict_and_metrics(frozen_now: str) -> None:
    result = _run(_challenger_wins_conn())
    assert result["verdict"] == "challenger_wins"
    assert result["recommendation"] == (
        "建议上线:分位数法带内率与中位相对误差双赢全局中位数对照组(影子结论,仍需人工确认切换)。"
    )
    assert result["challenger"]["band_hit_rate"] == 1.0
    assert result["challenger"]["hits"] == 3
    assert result["challenger"]["median_rel_error"] == 0.0
    assert result["baseline"]["hits"] == 0
    assert result["evidence"]["band_hit_rate_delta"] == 1.0
    assert _digest(result) == GOLDEN_SHA256["challenger_wins"]


def test_baseline_wins_verdict(frozen_now: str) -> None:
    result = _run(_baseline_wins_conn())
    assert result["verdict"] == "baseline_wins"
    assert result["recommendation"] == (
        "维持旧版:对照组两项指标均不差于挑战者,分位数法没有拿出胜绩。"
    )
    assert result["baseline"]["band_hit_rate"] == 1.0
    assert result["baseline"]["median_rel_error"] == 0.0
    assert result["challenger"]["hits"] == 0
    assert _digest(result) == GOLDEN_SHA256["baseline_wins"]


def test_tied_metrics_fall_to_mixed_and_keep_old_version(frozen_now: str) -> None:
    result = _run(_mixed_conn())
    assert result["verdict"] == "mixed"
    assert result["recommendation"] == (
        "维持旧版:两项指标各有胜负,未达「双赢才上线」门槛 — 不建议切换。"
    )
    assert result["challenger"]["band_hit_rate"] == result["baseline"]["band_hit_rate"] == 1.0
    assert result["challenger"]["median_rel_error"] == result["baseline"]["median_rel_error"] == 0.0
    assert result["evidence"]["band_hit_rate_delta"] == 0.0
    assert result["evidence"]["median_rel_error_delta"] == 0.0
    assert _digest(result) == GOLDEN_SHA256["mixed"]


# ── 决定性 / 截断 / SQL 兼容 ─────────────────────────────────────────


def test_fingerprint_is_deterministic_and_recomputable(frozen_now: str) -> None:
    first = _run(_rich_conn())
    second = _run(_rich_conn())
    assert first == second
    assert first["fingerprint"] == second["fingerprint"]
    body = {k: v for k, v in first.items() if k not in {"generated_at", "fingerprint"}}
    expected = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    assert first["fingerprint"] == expected


def test_max_samples_cap_truncates_deterministically(
    frozen_now: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shadow_eval, "MAX_SAMPLES", 3)
    result = _run(_rich_conn())
    assert result["samples"]["eligible"] == 3
    assert result["samples"]["cap"] == 3
    # 截断保留 evidence_id ASC 的前三条(全归 KOL 10)。
    assert result["samples"]["evaluated"] == 3
    assert [row["kol_pool_id"] for row in result["per_kol"]] == [10]


def test_sql_stays_compat_qmark_and_percent_free(frozen_now: str) -> None:
    conn = _rich_conn()
    _run(conn)
    assert conn.queries, "backtest must read through the provided conn"
    for sql, params in conn.queries:
        assert "%" not in sql
        assert sql.count("?") == len(params)


# ── 注册表通用入口(同文件行为一并锁住)───────────────────────────────


def test_registry_metadata_and_unknown_eval() -> None:
    assert shadow_eval.list_shadow_evals() == [
        {
            "name": "forecast_backtest",
            "label": "预测战绩留一回测",
            "description": (
                "对有真实播放数的 final_v1 深析视频做留一回测:分位数预测法(evidence_quantile_v1)"
                "vs 全局中位数对照组,输出带内率/中位相对误差/分 KOL 明细 — 双赢才建议上线。"
            ),
            "challenger": "evidence_quantile_v1",
            "baseline": "global_median_v0",
        }
    ]
    with pytest.raises(LookupError):
        shadow_eval.run_shadow_eval("not_registered")


def test_run_shadow_eval_dispatches_to_backtest(frozen_now: str) -> None:
    result = shadow_eval.run_shadow_eval("forecast_backtest", conn=_mixed_conn())
    assert result["verdict"] == "mixed"
    assert _digest(result) == GOLDEN_SHA256["mixed"]
