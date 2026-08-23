"""Ask 建议种子必须 ≤80 字(global-search / catalog-suggest 的 q 上限),否则整条 Ask 旅程 422(2026-08-23 哨兵长告警标题事故)。"""
from __future__ import annotations

from app.api.routers import vkpi_intelligent as v


def test_clip_seed_keeps_short_and_bounds_long():
    short = "告警「x」是什么原因,该怎么处理?"
    assert v._clip_seed(short) == short
    long_title = "官号帖子异常衰减:@viltrox.official《#Giveaway Viltrox 9/15/25mm Air Silver Finish Giveaway. Follow and win》"
    out = v._clip_seed(f"告警「{long_title}」是什么原因,该怎么处理?")
    assert len(out) <= v.SUGGESTION_SEED_MAX_LENGTH
    assert out.endswith("」是什么原因,该怎么处理?") and out.startswith("告警「官号帖子异常衰减")


def test_alert_seeds_are_clipped(monkeypatch):
    class _Cur:
        def fetchall(self):
            return [{"title": "T" * 150, "severity": "warning"}]

    class _Conn:
        def execute(self, *a, **k):
            return _Cur()

    import app.db.connection as dbc
    monkeypatch.setattr(dbc, "get_conn", lambda: _Conn())
    monkeypatch.setattr(dbc, "table_exists", lambda name: True)
    seeds = v._recent_alert_seeds()
    assert seeds and all(len(s) <= v.SUGGESTION_SEED_MAX_LENGTH for s in seeds)
