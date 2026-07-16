"""大脑夜报 —— 记录基线 / 跑出"大脑昨夜干了什么"增量报告。

用法:
  .venv/bin/python scripts/brain_report.py snapshot   # 记当前基线(今晚睡前已自动记)
  .venv/bin/python scripts/brain_report.py report     # 跑增量(明早跑这个)
基线存 runtime/brain_baseline.json。零写业务表,纯只读 + 一个 JSON 快照。
"""
from stdout_utils import out

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
BASELINE = ROOT / "runtime" / "brain_baseline.json"

from app.db.connection import get_conn, table_exists  # noqa: E402

# (展示名, 表, 计数列表达式)
TARGETS = [
    ("LLM/Apify 成本$", "vkpi_ai_cost_ledger", "COALESCE(SUM(cost_usd),0)"),
    ("竞品信号", "vkpi_competitor_signals", "COUNT(*)"),
    ("竞品雷达", "vkpi_competitor_radar", "COUNT(*)"),
    ("市场提及", "vkpi_market_mentions", "COUNT(*)"),
    ("今日行动建议", "vkpi_action_inbox", "COUNT(*)"),
    ("观察窗口", "vkpi_project_content_observation_windows", "COUNT(*)"),
    ("内容帖候选", "vkpi_project_content_posts", "COUNT(*)"),
    ("durable workflow_runs", "vkpi_workflow_runs", "COUNT(*)"),
    ("事件总线", "vkpi_event_ledger", "COUNT(*)"),
    ("KOL 推荐", "vkpi_kol_recommendations", "COUNT(*)"),
    ("推荐结局", "vkpi_recommendation_outcomes", "COUNT(*)"),
    ("押注", "vkpi_bet_ledger", "COUNT(*)"),
]


def _snapshot() -> dict:
    snap = {}
    c = get_conn()
    for _, tbl, expr in TARGETS:
        if not table_exists(tbl):
            snap[tbl] = None
            continue
        try:
            snap[tbl] = float(dict(c.execute(f"SELECT {expr} AS v FROM {tbl}").fetchone())["v"] or 0)
        except Exception:
            snap[tbl] = None
    return snap


def snapshot() -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(_snapshot(), ensure_ascii=False, indent=2))
    out(f"基线已记 → {BASELINE}")


def report() -> None:
    if not BASELINE.exists():
        out("无基线,先跑 snapshot")
        return
    base = json.loads(BASELINE.read_text())
    now = _snapshot()
    out("=== 大脑昨夜干了什么(增量)===")
    for name, tbl, _ in TARGETS:
        b, n = base.get(tbl), now.get(tbl)
        if b is None or n is None:
            continue
        delta = n - b
        mark = "  " if delta == 0 else "+ "
        if tbl == "vkpi_ai_cost_ledger":
            out(f"{mark}{name}: 花了 ${delta:.2f}(累计 ${n:.2f})")
        elif delta != 0:
            out(f"{mark}{name}: +{int(delta)}(共 {int(n)})")
    out("\n(零增量的项已隐藏。要看全量再跑 snapshot 重置基线。)")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"
    (snapshot if mode == "snapshot" else report)()
