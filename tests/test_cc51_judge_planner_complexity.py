"""CC51 双刀复杂度红线 —— 壳 ≤10、helper ≤12(_DecisionCounter 口径).

evaluate_final_v1_quality 与 build_identity_reconciliation_plan 改刀后不许反弹。
final_v1_quality_eval.py 必须保持单文件可独立加载
(scripts/eval_gemini_final_v1_quality.py 用 spec_from_file_location 裸载,
不许引入跨文件依赖),且两份产物文件都压在 800 行守卫之下。
(validate_gold / _candidate_alias_plan 是既有存量,不在本刀名下,不设新线。)
"""
from __future__ import annotations

import ast
from pathlib import Path

from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
QUALITY_EVAL = ROOT / "backend/app/domains/kol/final_v1_quality_eval.py"
IDENTITY_PLAN = ROOT / "backend/app/domains/kol/identity_reconciliation_plan.py"

SHELL_LIMIT = 10
HELPER_LIMIT = 12
QUALITY_HELPERS = (
    "_new_eval_state",
    "_case_provenance",
    "_non_title_evidence",
    "_claim_evidence_flags",
    "_claim_support",
    "_accumulate_case",
    "_build_report",
)
IDENTITY_HELPERS = (
    "_pool_alias_index",
    "_duplicate_group_details",
    "_folded_session_stats",
    "_session_official_scan",
    "_pool_official_plan",
    "_pool_section",
    "_session_projection_section",
    "_official_isolation_section",
    "_estimated_impact_section",
)


def _cc_by_name(path: Path) -> dict[str, int]:
    source = path.read_text(encoding="utf-8")
    rows = collect_complexity({str(path): ast.parse(source)})
    return {row.qualified_name: row.cc for row in rows}


def test_quality_eval_shell_and_helpers_stay_under_the_redline() -> None:
    cc = _cc_by_name(QUALITY_EVAL)
    assert cc["evaluate_final_v1_quality"] <= SHELL_LIMIT
    over = {name: cc[name] for name in QUALITY_HELPERS if cc[name] > HELPER_LIMIT}
    assert not over, f"helpers over CC {HELPER_LIMIT}: {over}"


def test_identity_plan_shell_and_helpers_stay_under_the_redline() -> None:
    cc = _cc_by_name(IDENTITY_PLAN)
    assert cc["build_identity_reconciliation_plan"] <= SHELL_LIMIT
    over = {name: cc[name] for name in IDENTITY_HELPERS if cc[name] > HELPER_LIMIT}
    assert not over, f"helpers over CC {HELPER_LIMIT}: {over}"


def test_quality_eval_stays_standalone_loadable_without_app_package() -> None:
    """scripts/eval_gemini_final_v1_quality.py 裸载合同:文件内不许 import app.*。"""
    tree = ast.parse(QUALITY_EVAL.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.extend(a.name for a in node.names if a.name.split(".")[0] == "app")
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "app":
            offenders.append(node.module or "")
    assert not offenders, f"final_v1_quality_eval.py must stay self-contained: {offenders}"


def test_touched_files_stay_under_the_800_line_guard() -> None:
    for path in (QUALITY_EVAL, IDENTITY_PLAN):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 800, path.name
