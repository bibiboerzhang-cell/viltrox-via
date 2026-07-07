"""GTM preview · market_opportunity 段接线 signal_ledger 兜底单测。

不连真 DB:_build_market_opportunity 是纯函数;signal_ledger 由并行车道在建,
按共同契约以 sys.modules 桩模块注入(monkeypatch 边界)。覆盖:
  1) 台账 ready → 段落 ready 且 items/sources_count/sample_size/freshest_at 透传;
  2) 台账抛异常 → 不炸,段落回落 empty(本页失败安静宪法);
  3) 台账 data_missing → 维持 empty 现状;
  4) 台账模块尚未落地(ImportError)→ 不炸,维持 empty;
  5) 赛道矩阵已有行 → 走既有 ready 路径,不触发台账兜底。
红线:纯读,本测试零写库零 LLM 零外部网络。
"""
from __future__ import annotations

import sys
import types

import app.domains.market_brain as market_brain_pkg
from app.domains.market_brain import gtm_plan_preview

_LEDGER_MODULE = "app.domains.market_brain.signal_ledger"

_READY_SUMMARY = {
    "status": "ready",
    "items": [
        {"signal": "reddit 讨论 85mm 人像定焦升温", "source": "reddit"},
        {"signal": "trends 焦段搜索量环比上行", "source": "trends"},
    ],
    "sources_count": 2,
    "sample_size": 17,
    "freshest_at": "2026-07-06T00:00:00+00:00",
}


def _install_ledger_stub(monkeypatch, summarize):
    """把 summarize 装成 signal_ledger 桩模块(真模块可能尚未落地,双点覆盖保险)。"""
    stub = types.ModuleType(_LEDGER_MODULE)
    stub.summarize_for_preview = summarize
    monkeypatch.setitem(sys.modules, _LEDGER_MODULE, stub)
    monkeypatch.setattr(market_brain_pkg, "signal_ledger", stub, raising=False)
    return stub


def _build(**overrides):
    kwargs = {"tracks_payload": {}, "bench": {}, "focal": "85", "sku": "SKU-X", "country": "US"}
    kwargs.update(overrides)
    return gtm_plan_preview._build_market_opportunity(**kwargs)


def test_ledger_ready_marks_section_ready_and_passes_summary(monkeypatch):
    """台账 ready:段落 status 置 ready,契约四字段原样透传,sku/market 入参正确。"""
    calls: list[tuple] = []

    def fake_summarize(sku, market=None, limit=20):
        calls.append((sku, market))
        return dict(_READY_SUMMARY)

    _install_ledger_stub(monkeypatch, fake_summarize)
    out = _build()
    assert out["status"] == "ready"
    assert out["items"] == _READY_SUMMARY["items"]
    assert out["sources_count"] == 2
    assert out["sample_size"] == 17
    assert out["freshest_at"] == "2026-07-06T00:00:00+00:00"
    assert out["focal"] == "85mm"
    assert calls == [("SKU-X", "US")]


def test_ledger_exception_falls_back_to_empty_without_raising(monkeypatch):
    """台账抛异常:段落不炸,回落既有 empty 空态(失败安静宪法)。"""

    def boom(sku, market=None, limit=20):
        raise RuntimeError("ledger table missing")

    _install_ledger_stub(monkeypatch, boom)
    out = _build()
    assert out["status"] == "empty"
    assert "85" in out["reason"]
    assert "items" not in out


def test_ledger_data_missing_keeps_empty(monkeypatch):
    """台账诚实 data_missing:维持现状,不假装 ready。"""
    _install_ledger_stub(monkeypatch, lambda sku, market=None, limit=20: {
        "status": "data_missing", "items": [], "sources_count": 0,
        "sample_size": 0, "freshest_at": None})
    out = _build()
    assert out["status"] == "empty"


def test_ledger_module_absent_keeps_empty(monkeypatch):
    """并行车道模块尚未落地(import 失败):段落不炸,维持 empty。"""
    monkeypatch.setitem(sys.modules, _LEDGER_MODULE, None)  # import 该名即 ImportError
    monkeypatch.delattr(market_brain_pkg, "signal_ledger", raising=False)
    out = _build()
    assert out["status"] == "empty"


def test_track_present_uses_existing_path_without_ledger(monkeypatch):
    """赛道矩阵有行:走既有 ready 骨架(track/focal_landscape),台账不被调用。"""
    called: list[int] = []
    _install_ledger_stub(monkeypatch, lambda *a, **k: called.append(1))
    tracks_payload = {
        "opportunities": [{
            "track_id": "focal:85mm", "label": "85mm 人像",
            "opportunity": {"confidence": "high"}, "demand": {"total": 3},
            "coverage": {}, "competitors": {},
        }],
        "windows": {"days": 30},
    }
    out = _build(tracks_payload=tracks_payload)
    assert out["status"] == "ready"
    assert out["track"]["track_id"] == "focal:85mm"
    assert "items" not in out  # 既有骨架不变
    assert called == []
