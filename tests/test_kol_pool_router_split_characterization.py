"""KOL Pool 路由 fan-out 拆分——characterization(先拍照后动刀)。

背景:``app.api.routers.vkpi_kol_pool`` 是 M4 豁免落地后的非豁免 fan-out 最大值
(68)。按资源子域把端点簇拆到静态 include 的子 router(禁 importlib 动态注册,
否则 collect.py 把全图判 partial、四指标变 unknown)。

本文件三道闸:

1. **路由表逐字节**——(methods, path, name) 三元组全集与拆分前完全一致,一条不多
   一条不少;release_validation 只读 GET 白名单里的路径因此一个都不会变。
2. **首匹配等价**——对拆分前路由表上每条可探测 URL 重放 starlette 首个 FULL 匹配,
   命中的 endpoint name 必须与拆分前完全一致(钉死 /kol-pool/resolve 这类静态路径
   必须先于 /kol-pool/{kol_pool_id} 动态路由的吞路由陷阱)。
3. **父模块属性面**——测试群通过 ``vkpi_kol_pool.<name>`` 直呼/monkeypatch 的名字
   必须继续存在于父模块命名空间(re-export 兜底也算数)。

拆完后追加第四道闸:父模块与新子 router 模块的内部 fan-out(含祖先包,口径对齐
scripts/vkpi_engineering_health_collect.py)必须 < 40。
"""
from __future__ import annotations

import ast
from pathlib import Path

from starlette.routing import Match

from app.api.routers import vkpi_kol_pool


ROOT = Path(__file__).resolve().parents[1]
BACKEND_APP = ROOT / "backend" / "app"

# ---------------------------------------------------------------------------
# 拆分前(2026-08-30)vkpi_kol_pool.router 的 (methods, path, name) 全量表。
# 注意:这是「集合契约」——include 位置调整允许改变注册顺序,但任何一条的
# 路径/方法/名字都不许变,也不许增删。
# ---------------------------------------------------------------------------
FROZEN_ROUTES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("GET",), "/api/admin/vkpi/task-queue", "get_vkpi_task_queue"),
    (("GET",), "/api/admin/vkpi/task-queue/compact", "get_vkpi_task_queue_compact"),
    (("GET",), "/api/admin/vkpi/kol-search-sessions/team-status", "get_kol_search_team_status"),
    (("POST",), "/api/admin/vkpi/kol-search-sessions", "create_kol_search_session"),
    (("GET",), "/api/admin/vkpi/kol-search-sessions", "list_kol_search_sessions"),
    (("GET",), "/api/admin/vkpi/kol-search-history", "list_kol_search_history"),
    (("DELETE",), "/api/admin/vkpi/kol-search-history", "archive_kol_search_history"),
    (("DELETE",), "/api/admin/vkpi/kol-search-history/{session_id}", "archive_kol_search_history_session"),
    (("POST",), "/api/admin/vkpi/kol-search-history/{session_id}/restore", "restore_kol_search_history_session"),
    (("GET",), "/api/admin/vkpi/kol-search-sessions/{session_id}", "get_kol_search_session"),
    (("POST",), "/api/admin/vkpi/kol-search-sessions/{session_id}/approve", "approve_kol_search_session"),
    (("POST",), "/api/admin/vkpi/kol-search-sessions/{session_id}/create-project-draft", "create_project_draft_from_kol_search_session"),
    (("POST",), "/api/admin/vkpi/kol-search-sessions/{session_id}/cost-estimate", "estimate_kol_search_session_cost"),
    (("POST",), "/api/admin/vkpi/kol-search-sessions/{session_id}/generate-outreach", "generate_kol_search_session_outreach"),
    (("POST",), "/api/admin/vkpi/kol-search-sessions/{session_id}/items/{item_id}/profile-crawl", "execute_kol_search_session_item_profile_crawl"),
    (("POST",), "/api/admin/vkpi/kol-search-sessions/{session_id}/advance", "advance_kol_search_session_items"),
    (("POST",), "/api/admin/vkpi/kol-search-sessions/{session_id}/advance-job", "enqueue_kol_search_session_advance"),
    (("POST",), "/api/admin/vkpi/kol-search-sessions/{session_id}/advance-job/cancel", "cancel_kol_search_session_advance"),
    (("POST",), "/api/admin/vkpi/kol-smart-search", "smart_kol_search"),
    (("POST",), "/api/admin/vkpi/kol-smart-search/profile-advance-job", "smart_kol_search_profile_advance_job"),
    (("POST",), "/api/admin/vkpi/kol-url-deep-crawl", "dry_run_kol_url_deep_crawl"),
    (("GET",), "/api/admin/vkpi/kol-recall", "recall_kol_profiles"),
    (("GET",), "/api/admin/vkpi/kol-pool/yield-estimate", "estimate_kol_pool_yield"),
    (("GET",), "/api/admin/vkpi/kol-pool", "list_pool"),
    (("GET",), "/api/admin/vkpi/kol-pool/summary", "get_pool_summary"),
    (("GET",), "/api/admin/vkpi/kol-pool/suspect-inflation", "get_suspect_inflation_review"),
    (("GET",), "/api/admin/vkpi/kol-pool/workspace", "get_pool_workspace"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/recommendation-card", "kol_recommendation_card"),
    (("GET",), "/api/admin/vkpi/kol-pool/unified-search", "kol_unified_search"),
    (("GET",), "/api/admin/vkpi/kol-pool/discovery/providers", "kol_pool_discovery_providers"),
    (("GET",), "/api/admin/vkpi/kol-pool/discovery/federated-search", "kol_pool_federated_search"),
    (("POST",), "/api/admin/vkpi/kol-pool/discovery/federated-search/refresh", "kol_pool_federated_search_refresh"),
    (("POST",), "/api/admin/vkpi/kol-pool/onboarding-sweep", "kol_onboarding_sweep"),
    (("POST",), "/api/admin/vkpi/kol-pool/discovery/enroll", "kol_pool_discovery_enroll"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/enrichment", "kol_pool_enrichment"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/enrich-via-apify", "kol_pool_enrich_via_apify"),
    (("GET",), "/api/admin/vkpi/kol-pool/auto-poll/status", "kol_pool_auto_poll_status"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/twin", "kol_twin"),
    (("GET",), "/api/admin/vkpi/kol-pool/available", "list_available_for_project"),
    (("GET",), "/api/admin/vkpi/kol-pool/competitors/dashboard", "get_pool_competitor_dashboard"),
    (("GET",), "/api/admin/vkpi/kol-pool/competitors/poach-targets", "get_competitor_poach_targets"),
    (("GET",), "/api/admin/vkpi/kol-pool/competitors/avoid-brands", "get_competitor_avoid_brands"),
    (("POST",), "/api/admin/vkpi/kol-pool/batch-enrich", "batch_enrich_pool_items"),
    (("POST",), "/api/admin/vkpi/kol-pool/profile-deep-crawl/enqueue", "enqueue_kol_profile_deep_crawl"),
    (("POST",), "/api/admin/vkpi/kol-pool/comments-collect/enqueue", "enqueue_kol_pool_comments_collect"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/video-comments", "get_kol_pool_video_comments"),
    (("POST",), "/api/admin/vkpi/kol-pool/outreach-draft/enqueue", "enqueue_kol_outreach_draft"),
    (("POST",), "/api/admin/vkpi/kol-pool/outreach-optimize", "optimize_kol_outreach"),
    (("GET",), "/api/admin/vkpi/kol-pool/favorites", "list_kol_pool_favorites"),
    (("GET",), "/api/admin/vkpi/kol-pool/needs-analysis", "list_kol_pool_needs_analysis"),
    (("GET",), "/api/admin/vkpi/kol-pool/resolve", "resolve_kol_pool"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}", "get_item"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/detail-bundle", "get_item_detail_bundle"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/account-dossier", "get_pool_item_account_dossier"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/account-dossier-extract-job", "enqueue_pool_item_account_dossier_extract"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/refresh", "refresh_pool_item"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/enqueue-video-analysis", "enqueue_pool_item_video_analysis"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/enqueue-video-keyframe-qa", "enqueue_pool_item_video_keyframe_qa"),
    (("POST",), "/api/admin/vkpi/kol-pool/enqueue-video-analysis-batch", "enqueue_pool_video_analysis_batch"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/enqueue-all-videos", "enqueue_pool_all_videos"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/main-candidates", "get_main_candidates"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/competitors", "get_pool_item_competitors"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/contacts", "add_kol_manual_contact"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/contacts/reveal", "reveal_kol_contact"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/audience-stats/refresh", "refresh_kol_audience_stats"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/cooperation", "get_kol_cooperation"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/cooperation", "record_kol_cooperation"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/outreach-draft", "get_kol_outreach_draft"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/outreach-pack", "get_kol_outreach_pack"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/outreach-pack", "generate_kol_outreach_pack"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/dimensions11", "get_pool_item_dimensions11"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/llm-deep-analysis", "get_pool_item_llm_deep_analysis"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/content-fit", "get_pool_item_content_fit"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/content-fit/analyze", "analyze_pool_item_content_fit"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/intelligence-card", "get_pool_item_intelligence_card"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/videos", "list_kol_pool_videos"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/competitor-exposure", "get_pool_item_competitor_exposure"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/evidence-summary", "get_pool_item_evidence_summary"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/ai-brief", "get_pool_item_ai_brief"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/gemini-preflight", "get_pool_item_gemini_preflight"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/gemini-go-no-go", "get_pool_item_gemini_go_no_go"),
    (("GET",), "/api/admin/vkpi/kol-pool-dimensions11/preview", "get_pool_dimensions11_preview"),
    (("POST",), "/api/admin/vkpi/kol-pool/translate-bio", "translate_bio"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/build-full-profile", "build_full_profile_endpoint"),
    (("GET",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/video-analysis-progress", "get_pool_item_video_analysis_progress"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/promote", "promote_to_main_kol"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/enrich", "enrich_pool_item"),
    (("POST",), "/api/admin/vkpi/kol-pool/import", "import_pool"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/link", "link_to_main_kol"),
    (("POST",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/favorite", "favorite_kol_pool_item"),
    (("DELETE",), "/api/admin/vkpi/kol-pool/{kol_pool_id}/favorite", "unfavorite_kol_pool_item"),
)

#: 既有测试群直接通过父模块命名空间调用 / monkeypatch 的名字——一个都不许丢。
PARENT_NAMESPACE_SURFACE = (
    "router",
    "list_pool",
    "get_pool_workspace",
    "get_item",
    "get_item_detail_bundle",
    "refresh_pool_item",
    "enrich_pool_item",
    "kol_onboarding_sweep",
    "kol_pool_enrich_via_apify",
    "kol_pool_federated_search_refresh",
    "enqueue_pool_item_video_analysis",
    "enqueue_pool_item_video_keyframe_qa",
    "enqueue_pool_video_analysis_batch",
    "enqueue_pool_all_videos",
    "list_kol_pool_needs_analysis",
    "promote_to_main_kol",
    "favorite_kol_pool_item",
    "unfavorite_kol_pool_item",
    "release_validation_active",
    "_record_pool_feedback_signal",
    "_maybe_enqueue_refresh",
    "_kol_operation_error",
    "kol_pool",
    "kol_video_analysis_enqueue",
    "kol_video_keyframe_qa_enqueue",
    "task_enqueue",
)


def _first_full_match(method: str, path: str) -> str | None:
    scope = {"type": "http", "method": method, "path": path, "root_path": "", "headers": []}
    for route in vkpi_kol_pool.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "name", "")
    return None


def _probe_url(path: str) -> str:
    return (
        path.replace("{kol_pool_id}", "123")
        .replace("{session_id}", "s1")
        .replace("{item_id}", "77")
    )


def test_route_table_is_byte_identical_to_pre_split_snapshot() -> None:
    actual = [
        (tuple(sorted(getattr(route, "methods", []) or [])), route.path, getattr(route, "name", ""))
        for route in vkpi_kol_pool.router.routes
    ]
    expected = list(FROZEN_ROUTES)
    missing = set(expected) - set(actual)
    extra = set(actual) - set(expected)
    assert not missing, f"拆分丢了路由(路径/方法/名字契约被破坏):{sorted(missing)}"
    assert not extra, f"拆分多出计划外路由:{sorted(extra)}"
    # 顺序也逐条钉死:test_router_package_lazy_import_contract 对全 app 路由表
    # (type, path, methods, name) 有序 sha 上了锁,子 router include 点必须原位。
    assert actual == expected, "路由注册顺序与拆分前不一致(全表 sha 锁会破)"


def test_first_match_resolution_identical_to_pre_split_snapshot() -> None:
    """静态路径绝不许被 /kol-pool/{kol_pool_id} 这类动态路由吞掉(422 陷阱)。"""
    mismatches: list[str] = []
    for methods, path, name in FROZEN_ROUTES:
        for method in methods:
            url = _probe_url(path)
            assert "{" not in url, f"探测 URL 还有未替换参数:{url}"
            resolved = _first_full_match(method, url)
            if resolved != name:
                mismatches.append(f"{method} {url} -> {resolved!r} (期望 {name!r})")
    assert not mismatches, "首匹配行为与拆分前不一致:\n" + "\n".join(mismatches)


def test_parent_module_namespace_surface_survives_split() -> None:
    missing = [name for name in PARENT_NAMESPACE_SURFACE if not hasattr(vkpi_kol_pool, name)]
    assert not missing, f"父模块命名空间丢名字(既有测试直呼/monkeypatch 目标):{missing}"


# ---------------------------------------------------------------------------
# fan-out 棘轮:口径对齐 vkpi_engineering_health_collect(叶子模块 + 祖先包,
# 只认 backend/app 下真实存在的模块)。
# ---------------------------------------------------------------------------


def _is_internal_module(dotted: str) -> bool:
    if dotted != "app" and not dotted.startswith("app."):
        return False
    parts = dotted.split(".")[1:]
    base = BACKEND_APP.joinpath(*parts) if parts else BACKEND_APP
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists()


def _internal_fan_out(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_internal_module(alias.name):
                    targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ""
            if module != "app" and not module.startswith("app."):
                continue
            for alias in node.names:
                candidate = f"{module}.{alias.name}"
                targets.add(candidate if _is_internal_module(candidate) else module)
    expanded: set[str] = set()
    for target in targets:
        parts = target.split(".")
        for index in range(1, len(parts) + 1):
            expanded.add(".".join(parts[:index]))
    return {target for target in expanded if _is_internal_module(target)}


FAN_OUT_LIMIT = 40


def test_kol_pool_router_fan_out_under_limit() -> None:
    routers_dir = BACKEND_APP / "api" / "routers"
    files = [
        routers_dir / "vkpi_kol_pool.py",
        routers_dir / "vkpi_kol_pool_discovery.py",
        routers_dir / "vkpi_kol_pool_profile.py",
        routers_dir / "vkpi_kol_pool_item.py",
    ]
    offenders: list[str] = []
    for file in files:
        assert file.exists(), f"缺文件:{file}"
        fan_out = len(_internal_fan_out(file))
        if fan_out >= FAN_OUT_LIMIT:
            offenders.append(f"{file.name}: fan-out {fan_out} >= {FAN_OUT_LIMIT}")
    assert not offenders, "fan-out 棘轮破线:\n" + "\n".join(offenders)
