"""vkpi_kol_pool_intel 子 router 拆分契约(2026-08-31 架构债·fan-out 车道)。

合同 ``internal_fan_out_max`` target=20 / ceiling=40。拆分前本模块模块级 fan-out=48,
单独把该指标压成 0 分。本测试把三件事钉死,防止拆分被悄悄回流:

1. 三个模块的 collector 口径 fan-out(模块 import 的仓内不同模块数,含祖先包边)
   只减不增,且父模块严格 < ceiling;
2. 迁出端点的 path/method/name 三元组与**挂载顺序**逐条不变——顺序是
   ``tests/test_router_package_lazy_import_contract.py`` 路由签名 SHA 的输入,
   子 router 挂在原定义位置才不会动那个钉子;
3. 子模块不反向 import 父模块(反向边会把父模块拽回 SCC,环棘轮报警),
   且迁出的端点函数在父模块保名 re-export(既有 import/monkeypatch 路径逐字可用)。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import vkpi_engineering_health_graph as graph_tools  # noqa: E402

from app.api.routers import vkpi_kol_pool_intel  # noqa: E402
from app.api.routers import vkpi_kol_pool_intel_reports  # noqa: E402
from app.api.routers import vkpi_kol_pool_profile_build  # noqa: E402

PARENT = "app.api.routers.vkpi_kol_pool_intel"
REPORTS = "app.api.routers.vkpi_kol_pool_intel_reports"
PROFILE_BUILD = "app.api.routers.vkpi_kol_pool_profile_build"

# 合同 ceiling;父模块必须严格小于它,该项才不是 0 分。
FAN_OUT_CEILING = 40

# 拆分落地时的实测值,只许降不许升。
FAN_OUT_MAX = {
    PARENT: 38,
    REPORTS: 17,
    PROFILE_BUILD: 12,
}

# 拆分前 vkpi_kol_pool_intel.router 的完整挂载顺序(路径/方法/name 逐字节)。
EXPECTED_ROUTE_SIGNATURE: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("/kol-pool/{kol_pool_id}/contacts", ("POST",), "add_kol_manual_contact"),
    ("/kol-pool/{kol_pool_id}/contacts/reveal", ("POST",), "reveal_kol_contact"),
    ("/kol-pool/{kol_pool_id}/audience-stats/refresh", ("POST",), "refresh_kol_audience_stats"),
    ("/kol-pool/{kol_pool_id}/cooperation", ("GET",), "get_kol_cooperation"),
    ("/kol-pool/{kol_pool_id}/cooperation", ("POST",), "record_kol_cooperation"),
    ("/kol-pool/{kol_pool_id}/outreach-draft", ("GET",), "get_kol_outreach_draft"),
    ("/kol-pool/{kol_pool_id}/outreach-pack", ("GET",), "get_kol_outreach_pack"),
    ("/kol-pool/{kol_pool_id}/outreach-pack", ("POST",), "generate_kol_outreach_pack"),
    ("/kol-pool/{kol_pool_id}/dimensions11", ("GET",), "get_pool_item_dimensions11"),
    ("/kol-pool/{kol_pool_id}/llm-deep-analysis", ("GET",), "get_pool_item_llm_deep_analysis"),
    ("/kol-pool/{kol_pool_id}/content-fit", ("GET",), "get_pool_item_content_fit"),
    ("/kol-pool/{kol_pool_id}/content-fit/analyze", ("POST",), "analyze_pool_item_content_fit"),
    ("/kol-pool/{kol_pool_id}/intelligence-card", ("GET",), "get_pool_item_intelligence_card"),
    ("/kol-pool/{kol_pool_id}/videos", ("GET",), "list_kol_pool_videos"),
    ("/kol-pool/{kol_pool_id}/competitor-exposure", ("GET",), "get_pool_item_competitor_exposure"),
    ("/kol-pool/{kol_pool_id}/evidence-summary", ("GET",), "get_pool_item_evidence_summary"),
    ("/kol-pool/{kol_pool_id}/ai-brief", ("GET",), "get_pool_item_ai_brief"),
    ("/kol-pool/{kol_pool_id}/gemini-preflight", ("GET",), "get_pool_item_gemini_preflight"),
    ("/kol-pool/{kol_pool_id}/gemini-go-no-go", ("GET",), "get_pool_item_gemini_go_no_go"),
    ("/kol-pool-dimensions11/preview", ("GET",), "get_pool_dimensions11_preview"),
    ("/kol-pool/translate-bio", ("POST",), "translate_bio"),
    ("/kol-pool/{kol_pool_id}/build-full-profile", ("POST",), "build_full_profile_endpoint"),
)

# 迁出后仍必须在父模块命名空间里保名的端点函数。
REEXPORTED_ENDPOINTS = {
    "get_pool_item_dimensions11": vkpi_kol_pool_intel_reports,
    "get_pool_item_intelligence_card": vkpi_kol_pool_intel_reports,
    "list_kol_pool_videos": vkpi_kol_pool_intel_reports,
    "get_pool_item_competitor_exposure": vkpi_kol_pool_intel_reports,
    "get_pool_item_evidence_summary": vkpi_kol_pool_intel_reports,
    "get_pool_item_ai_brief": vkpi_kol_pool_intel_reports,
    "get_pool_item_gemini_preflight": vkpi_kol_pool_intel_reports,
    "get_pool_item_gemini_go_no_go": vkpi_kol_pool_intel_reports,
    "get_pool_dimensions11_preview": vkpi_kol_pool_intel_reports,
    "build_full_profile_endpoint": vkpi_kol_pool_profile_build,
}


def _import_graph() -> dict[str, set[str]]:
    trees: dict[str, ast.Module] = {}
    for path in sorted((ROOT / "backend" / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(ROOT))
        trees[relative] = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    return graph_tools.build_backend_import_graph(trees).graph


def _router_signature(router) -> tuple[tuple[str, tuple[str, ...], str], ...]:
    return tuple(
        (route.path, tuple(sorted(route.methods)), route.name)
        for route in router.routes
    )


def test_intel_router_fan_out_stays_under_contract_ceiling() -> None:
    graph = _import_graph()
    actual = {module: len(graph.get(module, ())) for module in FAN_OUT_MAX}

    assert actual[PARENT] < FAN_OUT_CEILING, (
        "vkpi_kol_pool_intel 模块级 fan-out 回到 ceiling 及以上,"
        f" internal_fan_out_max 该项重新归零:{actual[PARENT]}"
    )
    grown = {
        module: {"actual": count, "maximum": FAN_OUT_MAX[module]}
        for module, count in actual.items()
        if count > FAN_OUT_MAX[module]
    }
    assert not grown, f"fan-out 棘轮只减不增,以下模块变胖:{grown}"


def test_split_modules_never_import_the_parent_back() -> None:
    """反向边会把子模块和父模块拽进同一个 SCC,环棘轮会红。"""
    graph = _import_graph()
    for module in (REPORTS, PROFILE_BUILD):
        assert PARENT not in graph.get(module, ()), (
            f"{module} 反向 import 了父模块 {PARENT}"
        )


def test_mounted_route_triples_and_order_are_unchanged() -> None:
    """path/method/name 三元组连同挂载顺序逐条不变(路由签名钉子的输入)。"""
    assert _router_signature(vkpi_kol_pool_intel.router) == EXPECTED_ROUTE_SIGNATURE


def test_moved_endpoints_keep_their_name_in_the_original_module() -> None:
    for name, owner in REEXPORTED_ENDPOINTS.items():
        assert hasattr(vkpi_kol_pool_intel, name), f"父模块丢了保名 re-export:{name}"
        assert getattr(vkpi_kol_pool_intel, name) is getattr(owner, name), (
            f"{name} 在父模块与子模块不是同一个函数对象"
        )


def test_parent_no_longer_carries_the_moved_domain_imports() -> None:
    """迁出的域模块必须真的离开父模块的 import 面,否则拆分只是搬了行数。"""
    graph = _import_graph()
    parent_edges = graph.get(PARENT, set())
    for module in (
        "app.domains.evidence.summary",
        "app.domains.intelligence.ai_brief",
        "app.domains.intelligence.gemini_single_kol_preflight",
        "app.domains.kol.competitor_exposure",
        "app.domains.kol.eleven_dimensions",
        "app.domains.kol.intelligence_card",
        "app.domains.kol.pool",
        "app.domains.discovery.buildout",
        "app.domains.kol.video_tracking",
    ):
        assert module not in parent_edges, f"{module} 又被 import 回父模块了"
