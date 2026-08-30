"""import-time cyclic_module_count 棘轮:成环模块总数只减不增(W4 防倒退三棘轮之一)。

口径 = collector 新口径(contract v1.1 import-time 子图,scripts/
vkpi_engineering_health_graph.py 的 ``import_time_cycle_summary``):只统计
**模块导入期真正执行**的 import 边(模块顶层 / class body;函数体内的 lazy import
与 if-TYPE_CHECKING 保护块内的 import 不算边);``cyclic_module_count`` = 所有
成环 SCC(含自环)的模块数之和。扫描范围 = backend/app 生产码(tests 排除)。

规则:
- ``CYCLIC_MODULE_COUNT_BASELINE``:快照时的 cyclic_module_count;
- 当前值只许 ≤ 基线(打散环、缩小环、模块出环都通过;任何净增都拦);
- ``BASELINE_CYCLIC_MODULES`` 是快照时的成环模块清单,仅用于失败时定位
  「谁新进了环」,不单独作硬断言(环内改名不逼 refresh)。

修法:把制造环的 import 挪进函数体(lazy import,打断 import-time 边)、
用依赖反转(协议/回调),或把共享件下沉到 app/platform / app/shared;
包 __init__ 重导出(re-export)是常见环源,优先从 __init__ 摘掉重导出。

重拍快照(--refresh-baseline)会原地重写上面两个常量。
**refresh 动作须主会话/用户批准后才可执行**(棘轮的意义就是让"加环"成为
需要理由、需要人批的事),命令:
``.venv/bin/python tests/test_cycle_ratchet.py --refresh-baseline``
"""
from __future__ import annotations

import ast
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import vkpi_engineering_health_collect as collector  # noqa: E402
from scripts import vkpi_engineering_health_graph as graph_tools  # noqa: E402
from scripts import vkpi_engineering_health_snapshot as snapshot  # noqa: E402

FIX_HINT = (
    "修法:把制造环的 import 挪进函数体(lazy import)/依赖反转/共享件下沉 platform,"
    "或从包 __init__ 摘掉重导出。基线在 tests/test_cycle_ratchet.py 的 "
    "CYCLIC_MODULE_COUNT_BASELINE;确需抬高基线请经主会话/用户批准后运行 "
    ".venv/bin/python tests/test_cycle_ratchet.py --refresh-baseline 并在提交说明里解释。"
)

# --- snapshot begin (generated; do not edit by hand) ---
CYCLIC_MODULE_COUNT_BASELINE = 175
BASELINE_CYCLIC_MODULES: tuple[str, ...] = (
    "app.domains.access",
    "app.domains.advisor",
    "app.domains.alerts",
    "app.domains.alerts.service",
    "app.domains.alerts.triage",
    "app.domains.analysis",
    "app.domains.analysis.cache_repo",
    "app.domains.analytics",
    "app.domains.analytics.actions",
    "app.domains.analytics.digest",
    "app.domains.analytics.monitor",
    "app.domains.analytics.suggestions",
    "app.domains.attribution",
    "app.domains.attribution.links",
    "app.domains.audience",
    "app.domains.audit",
    "app.domains.channels",
    "app.domains.channels.common",
    "app.domains.channels.crud",
    "app.domains.channels.evidence",
    "app.domains.channels.media",
    "app.domains.channels.official",
    "app.domains.channels.official_summary",
    "app.domains.channels.post_metrics",
    "app.domains.channels.posts",
    "app.domains.channels.refill",
    "app.domains.comments",
    "app.domains.comments.collector",
    "app.domains.comments.intelligence",
    "app.domains.commerce",
    "app.domains.commerce.shopify_discounts",
    "app.domains.content",
    "app.domains.costs",
    "app.domains.costs.budget_guard",
    "app.domains.costs.budget_window_roll",
    "app.domains.costs.ledger",
    "app.domains.dashboard",
    "app.domains.dashboard.kol_distribution",
    "app.domains.dashboard.performance",
    "app.domains.dashboard.summary",
    "app.domains.dashboard.summary_campaigns",
    "app.domains.dashboard.summary_company",
    "app.domains.dashboard.summary_company_series",
    "app.domains.dashboard.summary_roster",
    "app.domains.dashboard.views",
    "app.domains.data_quality",
    "app.domains.data_quality.actions",
    "app.domains.data_quality.checks",
    "app.domains.data_quality.operational_issues",
    "app.domains.data_quality.service",
    "app.domains.events",
    "app.domains.events.service",
    "app.domains.evidence",
    "app.domains.evidence.content",
    "app.domains.evidence.messages",
    "app.domains.evidence.shipments",
    "app.domains.evidence.terms",
    "app.domains.experiments",
    "app.domains.feedback",
    "app.domains.industry",
    "app.domains.industry.data",
    "app.domains.industry.snapshot_collector",
    "app.domains.integrations",
    "app.domains.integrations.goaffpro_connect",
    "app.domains.intelligence",
    "app.domains.intelligence.brain_acceptance_use_case",
    "app.domains.intelligence.brief_use_case",
    "app.domains.intelligence.evidence_agent_use_case",
    "app.domains.intelligence.recommendation_use_case",
    "app.domains.intelligence.today_signals_use_case",
    "app.domains.intelligence.weekly_plan_use_case",
    "app.domains.intelligent_query",
    "app.domains.intelligent_query.common",
    "app.domains.intelligent_query.handlers",
    "app.domains.intelligent_query.intent",
    "app.domains.intelligent_query.repository",
    "app.domains.intelligent_query.service",
    "app.domains.intelligent_query.weekly_voice",
    "app.domains.intelligent_query.weekly_voice_response",
    "app.domains.launch",
    "app.domains.launch.acceptance_use_case",
    "app.domains.learning",
    "app.domains.lineage",
    "app.domains.lineage.compute",
    "app.domains.lineage.service",
    "app.domains.lineage.store",
    "app.domains.market",
    "app.domains.market.ai_today",
    "app.domains.market.ai_today_evidence",
    "app.domains.market.competitor_radar",
    "app.domains.market.external_signal_smoke",
    "app.domains.market.intelligence_cards",
    "app.domains.market.intelligence_use_case",
    "app.domains.market.signal_commit",
    "app.domains.market.signal_ingest_use_case",
    "app.domains.market.signal_review_package",
    "app.domains.market.signal_review_persistence",
    "app.domains.market.signal_write_package",
    "app.domains.market.source_design_use_case",
    "app.domains.media",
    "app.domains.media.cache",
    "app.domains.media.cache_migration",
    "app.domains.media.cache_ytdlp",
    "app.domains.memory",
    "app.domains.memory.feedback",
    "app.domains.memory.legacy",
    "app.domains.memory.market",
    "app.domains.memory.matching",
    "app.domains.memory.product",
    "app.domains.operations",
    "app.domains.ops",
    "app.domains.predictions",
    "app.domains.reports",
    "app.domains.reports.render_recovery",
    "app.domains.reports.report_appendices",
    "app.domains.reports.report_failure_recovery",
    "app.domains.reports.report_helpers",
    "app.domains.reports.report_rendering",
    "app.domains.reports.reports",
    "app.domains.reports.weekly_generator",
    "app.domains.scoring",
    "app.domains.scoring.rule_v0",
    "app.domains.search",
    "app.domains.settings",
    "app.domains.settings.notifications",
    "app.domains.settings.preferences",
    "app.domains.settings.use_cases",
    "app.domains.staff",
    "app.domains.staff.decision_staff",
    "app.domains.staff.kpi_ledger",
    "app.domains.staff.profile",
    "app.domains.staff_groups",
    "app.domains.sync",
    "app.domains.sync.daily_sync",
    "app.domains.sync.sentinel_use_case",
    "app.domains.sync.sync_status",
    "app.domains.trends",
    "app.domains.trends.trend_detection_use_case",
    "app.integrations.excel_import.schemas",
    "app.platform.industry_crawlers",
    "app.platform.industry_crawlers.reddit_crawler",
    "app.platform.models",
    "app.platform.models.adapters",
    "app.platform.models.evaluation_artifact",
    "app.platform.models.evaluation_artifact_verifier",
    "app.platform.models.readiness",
    "app.platform.models.router",
    "app.platform.models.runtime",
    "app.services.cache",
    "app.services.commerce",
    "app.services.memory",
    "app.services.memory.via_learning",
    "app.services.memory.via_learning_affiliate",
    "app.services.memory.via_learning_common",
    "app.services.memory.via_learning_daily",
    "app.services.memory.via_learning_evaluator",
    "app.services.memory.via_learning_internal",
    "app.services.memory.via_learning_rollout",
    "app.services.memory.via_learning_summaries",
    "app.services.scheduler",
    "app.services.scheduler.fleet_guard",
    "app.services.scheduler.jobs",
    "app.services.scheduler.jobs_fire_recovery",
    "app.services.scheduler.jobs_tasks",
    "app.services.scheduler.jobs_tasks_events",
    "app.services.scheduler.jobs_tasks_intel",
    "app.services.scheduler.jobs_tasks_kol",
    "app.services.scheduler.jobs_tasks_products",
    "app.services.security",
    "app.services.system",
    "app.services.system.staff",
    "app.services.verification",
    "app.services.verification.scanner",
    "app.services.via",
    "app.services.via.vector_memory",
)
# --- snapshot end ---


def _parse_production_trees() -> dict[str, ast.Module]:
    """collector 同口径快照 + 解析(backend/app 生产 Python,tests 排除)。"""
    captured = snapshot.snapshot_sources(
        ROOT,
        collector.PYTHON_ROOTS,
        {".py"},
        skip_parts=collector.SKIP_PARTS,
        test_directory_names=collector.TEST_DIRECTORY_NAMES,
        test_filename_markers=collector.TEST_FILENAME_MARKERS,
    )
    assert captured.complete, (
        f"源快照不完整,棘轮口径失真:symlinks={list(captured.symlink_sources)} "
        f"errors={list(captured.read_errors)}"
    )
    trees, failures = collector.parse_python_sources(list(captured.files))
    assert not failures, f"生产 Python 解析失败,棘轮口径失真:{failures}"
    return trees


@lru_cache(maxsize=1)
def _current_summary() -> dict[str, Any]:
    trees = _parse_production_trees()
    build = graph_tools.build_backend_import_graph(trees)
    assert not build.collisions, (
        f"模块名冲突,import 图不可信(棘轮拒绝在失真图上报数):{build.collisions}"
    )
    return graph_tools.import_time_cycle_summary(build.import_time_graph, valid=True)


def _current_cyclic_modules(summary: dict[str, Any]) -> set[str]:
    return {module for scc in summary["cyclic_sccs"] for module in scc["members"]}


def test_import_time_cyclic_module_count_only_decreases() -> None:
    summary = _current_summary()
    current = summary["cyclic_module_count"]
    newcomers = sorted(_current_cyclic_modules(summary) - set(BASELINE_CYCLIC_MODULES))
    culprit_sccs = [
        {"size": scc["size"], "members": sorted(scc["members"])}
        for scc in summary["cyclic_sccs"]
        if any(module in newcomers for module in scc["members"])
    ]
    assert current <= CYCLIC_MODULE_COUNT_BASELINE, (
        f"cyclic_module_count 棘轮:import-time 成环模块数 {current} 超过基线 "
        f"{CYCLIC_MODULE_COUNT_BASELINE}(只减不增)。新进环的模块:{newcomers};"
        f"它们所在的环:{culprit_sccs}。{FIX_HINT}"
    )


def test_baseline_not_left_stale_after_big_wins() -> None:
    """当前值低于基线只是「更严」,不强制立刻 refresh;这里只挡明显腐坏的基线。

    基线一旦大幅高于现实(此处阈值:高出 50+),等于给未来偷偷加环留了额度,
    请经主会话/用户批准后 --refresh-baseline 把战果锁死。
    """
    current = _current_summary()["cyclic_module_count"]
    slack = CYCLIC_MODULE_COUNT_BASELINE - current
    assert slack < 50, (
        f"cyclic_module_count 基线过期:当前 {current},基线 {CYCLIC_MODULE_COUNT_BASELINE},"
        f"富余 {slack} 已够藏进一整片新环。请经主会话/用户批准后运行 "
        ".venv/bin/python tests/test_cycle_ratchet.py --refresh-baseline 锁定战果。"
    )


def test_baseline_is_well_formed() -> None:
    assert CYCLIC_MODULE_COUNT_BASELINE > 0, (
        "基线未生成:请经主会话/用户批准后运行 --refresh-baseline"
    )
    assert len(BASELINE_CYCLIC_MODULES) == CYCLIC_MODULE_COUNT_BASELINE, (
        "基线自相矛盾(清单长度 != 计数),疑似手改快照;请经主会话/用户批准后 "
        "--refresh-baseline 重拍"
    )
    assert list(BASELINE_CYCLIC_MODULES) == sorted(set(BASELINE_CYCLIC_MODULES))
    assert all(module.startswith("app") for module in BASELINE_CYCLIC_MODULES)


def _refresh_baseline() -> None:
    """原地重拍基线。**须主会话/用户批准后才可运行**(见模块 docstring)。"""
    me = Path(__file__)
    text = me.read_text(encoding="utf-8")
    _current_summary.cache_clear()
    summary = _current_summary()
    members = sorted(_current_cyclic_modules(summary))
    body = "\n".join(f'    "{module}",' for module in members)
    block = (
        f"CYCLIC_MODULE_COUNT_BASELINE = {summary['cyclic_module_count']}\n"
        "BASELINE_CYCLIC_MODULES: tuple[str, ...] = (\n" + body + "\n)"
    )
    pattern = re.compile(
        r"CYCLIC_MODULE_COUNT_BASELINE = \d+\n"
        r"BASELINE_CYCLIC_MODULES: tuple\[str, \.\.\.\] = \((?:.*?\n\)|\))",
        re.S,
    )
    assert pattern.search(text), "snapshot block missing"
    me.write_text(pattern.sub(lambda _m: block, text, count=1), encoding="utf-8")
    print(
        f"refreshed baseline={summary['cyclic_module_count']} "
        f"({len(members)} modules) into {me.relative_to(ROOT)}"
    )


if __name__ == "__main__":  # pragma: no cover - 维护入口
    if "--refresh-baseline" in sys.argv:
        _refresh_baseline()
    else:
        print("usage: python tests/test_cycle_ratchet.py --refresh-baseline  # 须主会话/用户批准")
