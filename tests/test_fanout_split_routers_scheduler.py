"""M8 fan-out 拆分 characterization:jobs_tasks / vkpi_projects / vkpi_agents 三簇。

守三件事(行为保持 + 指标不回潮):
1. re-export 面:搬迁后的任务函数/端点仍可从原模块按原名取到,且与新叶子是同一对象
   (调用点/监控/测试 monkeypatch 语义不变);
2. 路由契约:vkpi_agents 30 条路由的 (methods, path, name) 有序清单逐字节不变,
   vkpi_projects 的 materials 两端点路径原样;
3. 架构棘轮:三个被拆模块 fan-out < 40(collector 同口径),新叶子零环
   (绝不 import app.services.scheduler 包内模块,防 SCC 回吸)。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "backend")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

FAN_OUT_CEILING = 40

SPLIT_MODULES = (
    "app.services.scheduler.jobs_tasks",
    "app.api.routers.vkpi_projects",
    "app.api.routers.vkpi_agents",
)

NEW_LEAVES = (
    "app.services.scheduler.jobs_tasks_maintenance",
    "app.services.scheduler.jobs_tasks_learning",
    "app.api.routers.vkpi_projects_materials",
    "app.api.routers.vkpi_agents_brain",
)

MAINTENANCE_NAMES = (
    "_enqueue_provider_job",
    "_with_durable_queue",
    "job_cache_cleanup",
    "job_confirm_partial_awards",
    "job_pending_asset_cleanup",
    "job_provider_health_check",
    "job_rate_limit_cleanup",
    "job_token_broker_reset_daily",
    "job_verification_scan_check",
    "job_vkpi_apify_reconcile",
    "job_vkpi_cost_snapshot",
    "job_vkpi_goaffpro_metrics_sync",
    "job_vkpi_health_sentinel",
    "job_worker_lease_expire_stale",
)

LEARNING_NAMES = (
    "job_vkpi_agent_cycle",
    "job_vkpi_bet_review_due",
    "job_vkpi_fulfillment_sweep",
    "job_vkpi_outcomes_refresh",
    "job_vkpi_recommendation_outcomes",
)

AGENTS_BRAIN_NAMES = (
    "data_catalog",
    "marketing_brain_daily",
    "marketing_brain_refresh",
    "skills_orchestrate",
    "skills_plan",
    "skills_evals",
    "_require_legacy_agent_scope",
)

# 2026-08 收敛快照:vkpi_agents 全量路由 (methods, path, name),顺序=注册顺序。
AGENTS_ROUTES_SNAPSHOT = [
    (("POST",), "/api/admin/vkpi/agents/plan", "plan"),
    (("GET",), "/api/admin/vkpi/agents/tools", "tools"),
    (("GET",), "/api/admin/vkpi/agents/plan/{plan_id}", "read_plan"),
    (("GET",), "/api/admin/vkpi/agents/recall", "unified_recall"),
    (("GET",), "/api/admin/vkpi/agents/tenant/current", "tenant_current"),
    (("GET",), "/api/admin/vkpi/agents/organizations", "organizations_list"),
    (("POST",), "/api/admin/vkpi/agents/evals/run", "evals_run"),
    (("GET",), "/api/admin/vkpi/agents/marketing-brain/scorecard", "marketing_brain_scorecard"),
    (("GET",), "/api/admin/vkpi/agents/data-catalog", "data_catalog"),
    (("GET",), "/api/admin/vkpi/agents/marketing-brain/daily", "marketing_brain_daily"),
    (("POST",), "/api/admin/vkpi/agents/marketing-brain/refresh", "marketing_brain_refresh"),
    (("POST",), "/api/admin/vkpi/agents/skills/orchestrate", "skills_orchestrate"),
    (("GET",), "/api/admin/vkpi/agents/skills/plan", "skills_plan"),
    (("GET",), "/api/admin/vkpi/agents/skills/evals", "skills_evals"),
    (("POST",), "/api/admin/vkpi/agents/bets", "bet_create"),
    (("POST",), "/api/admin/vkpi/agents/bets/{action_id}/approve", "bet_approve_from_inbox"),
    (("GET",), "/api/admin/vkpi/agents/bets", "bet_list"),
    (("POST",), "/api/admin/vkpi/agents/bets/{bet_id}/resolve", "bet_resolve"),
    (("POST",), "/api/admin/vkpi/agents/cycle/run", "agent_cycle_run"),
    (("POST",), "/api/admin/vkpi/agents/cycle/{run_id}/resume", "agent_cycle_resume"),
    (("GET",), "/api/admin/vkpi/agents/workflow/{run_id}", "workflow_run"),
    (("GET",), "/api/admin/vkpi/agents/event-ledger", "event_ledger_recent"),
    (("GET",), "/api/admin/vkpi/agents/token-broker/status", "token_broker_status"),
    (("GET",), "/api/admin/vkpi/agents/worker-lease/status", "worker_lease_status"),
    (("GET",), "/api/admin/vkpi/agents/classify-input", "classify_input"),
    (("POST",), "/api/admin/vkpi/agents/plan/{plan_id}/materialize", "materialize_plan"),
    (("GET",), "/api/admin/vkpi/agents/capabilities", "capabilities"),
    (("GET",), "/api/admin/vkpi/agents/learning-status", "learning_status"),
    (("GET",), "/api/admin/vkpi/agents/workspace-digest", "workspace_digest"),
    (("GET",), "/api/admin/vkpi/agents/kol/{kol_pool_id}/provenance", "kol_provenance"),
]


def test_jobs_tasks_reexports_are_same_objects() -> None:
    from app.services.scheduler import (
        jobs_tasks,
        jobs_tasks_learning,
        jobs_tasks_maintenance,
    )

    for name in MAINTENANCE_NAMES:
        assert getattr(jobs_tasks, name) is getattr(jobs_tasks_maintenance, name), name
    for name in LEARNING_NAMES:
        assert getattr(jobs_tasks, name) is getattr(jobs_tasks_learning, name), name


def test_router_reexports_are_same_objects() -> None:
    from app.api.routers import (
        vkpi_agents,
        vkpi_agents_brain,
        vkpi_projects,
        vkpi_projects_materials,
    )

    for name in AGENTS_BRAIN_NAMES:
        assert getattr(vkpi_agents, name) is getattr(vkpi_agents_brain, name), name
    assert vkpi_projects.list_project_materials is vkpi_projects_materials.list_project_materials
    assert vkpi_projects.add_project_material is vkpi_projects_materials.add_project_material


def test_agents_route_table_unchanged() -> None:
    from app.api.routers import vkpi_agents

    got = [(tuple(sorted(r.methods)), r.path, r.name) for r in vkpi_agents.router.routes]
    assert got == AGENTS_ROUTES_SNAPSHOT


def test_projects_materials_routes_present_in_place() -> None:
    from app.api.routers import vkpi_projects

    rows = [(tuple(sorted(r.methods)), r.path, r.name) for r in vkpi_projects.router.routes]
    assert (("GET",), "/api/admin/vkpi/projects/{project_id}/materials", "list_project_materials") in rows
    assert (("POST",), "/api/admin/vkpi/projects/{project_id}/materials", "add_project_material") in rows


def test_new_leaves_never_import_scheduler_package() -> None:
    """SCC 回吸红线:scheduler 包整体在既有环里,新叶子 import 包内任何名字即入环。"""
    import ast

    for rel in (
        "backend/app/services/scheduler/jobs_tasks_maintenance.py",
        "backend/app/services/scheduler/jobs_tasks_learning.py",
    ):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"{rel}: 相对 import 会回指 scheduler 包"
                assert not (node.module or "").startswith("app.services.scheduler"), f"{rel}: {node.module}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("app.services.scheduler"), f"{rel}: {alias.name}"


def test_split_modules_fan_out_below_ceiling_and_leaves_acyclic() -> None:
    from scripts import vkpi_engineering_health_graph as graph_tools
    from scripts.vkpi_engineering_health_collect import (
        PYTHON_ROOTS,
        _take_source_snapshot,
        parse_python_sources,
    )

    captured = _take_source_snapshot(ROOT)
    python_files = [
        item
        for item in captured.files
        if item.path.suffix == ".py"
        and any(
            item.relative_path == prefix or item.relative_path.startswith(prefix + "/")
            for prefix in PYTHON_ROOTS
        )
    ]
    trees, _failures = parse_python_sources(python_files)
    build = graph_tools.build_backend_import_graph(trees)

    for module in SPLIT_MODULES + NEW_LEAVES:
        assert module in build.graph, f"{module} missing from import graph"
        fan_out = len(build.graph[module])
        assert fan_out < FAN_OUT_CEILING, f"{module} fan-out {fan_out} >= {FAN_OUT_CEILING}"

    components = graph_tools.strongly_connected_components(build.graph)
    cyclic_modules = {
        module
        for component in components
        if graph_tools.is_cycle(component, build.graph)
        for module in component
    }
    for module in NEW_LEAVES:
        assert module not in cyclic_modules, f"{module} joined an import cycle"
