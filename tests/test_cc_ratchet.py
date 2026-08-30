"""函数圈复杂度(CC)棘轮:CC>40 存量白名单只减不增(W4 防倒退三棘轮之一)。

口径 = collector 的 ``_DecisionCounter``(scripts/vkpi_engineering_health_collect.py,
经公开入口 ``collect_complexity``):CC = 1 + If/For/AsyncFor/While/Assert/IfExp
+ BoolOp 操作数-1 + Try handlers/else + comprehension 生成器与过滤 + 非默认 Match 分支
与 guard;嵌套函数/类/lambda 独立计数。扫描范围 = collector 的 PYTHON_ROOTS
(backend/app,生产码;tests/migrations/fixtures 等按 collector 口径排除)。

规则:
- ``CC_WHITELIST``:快照时全部 CC>40 的函数(``path::qualified_name`` -> 当时 CC);
- 白名单外不许出现 CC>40 的函数(新函数零豁免,老函数不得被撑过线);
- 白名单内函数 CC 不得超过快照值(只许变好);
- 修到 ≤40 / 改名 / 删除后必须同步从白名单删条目——白名单只减不增,
  绝不许手工往里加新条目给新函数放行。

重拍快照(--refresh-baseline)会原地重写 CC_WHITELIST。
**refresh 动作须主会话/用户批准后才可执行**(棘轮的意义就是让"变复杂"成为
需要理由、需要人批的事),命令:
``.venv/bin/python tests/test_cc_ratchet.py --refresh-baseline``
"""
from __future__ import annotations

import ast
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import vkpi_engineering_health_collect as collector  # noqa: E402
from scripts import vkpi_engineering_health_snapshot as snapshot  # noqa: E402

CC_LIMIT = 40
FIX_HINT = (
    "修法(CC 刀统一配方):把分支块提取成 helper(每个 ≤12,壳 ≤10),"
    "helper 放同目录兄弟文件(≤700 行);try/rollback 边界与异常行为逐字保持。"
    "白名单在 tests/test_cc_ratchet.py 的 CC_WHITELIST;确需放行请经主会话/用户批准后 "
    "运行 .venv/bin/python tests/test_cc_ratchet.py --refresh-baseline 并在提交说明里解释。"
)

# --- snapshot begin (generated; do not edit by hand) ---
CC_WHITELIST: dict[str, int] = {
    "backend/app/api/routers/admin_submissions.py::manual_approve": 45,
    "backend/app/api/routers/vkpi_goaffpro.py::goaffpro_summary": 47,
    "backend/app/db/migrations.py::init_db": 42,
    "backend/app/domains/actions/inbox.py::reconcile_executing_action": 41,
    "backend/app/domains/advisor/service.py::create_message_turn": 43,
    "backend/app/domains/agents/step_execution.py::resolve_action_contract": 46,
    "backend/app/domains/commerce/dealer_management.py::update_dealer_impl": 44,
    "backend/app/domains/costs/budget_guard.py::record_cost": 43,
    "backend/app/domains/dashboard/recent_content.py::_dashboard_recent_official_content": 50,
    "backend/app/domains/events/radar_catalog_preview.py::build_preview": 42,
    "backend/app/domains/events/radar_summary.py::build_summary": 43,
    "backend/app/domains/integrations/goaffpro_connect_affiliates.py::resolve_affiliate": 46,
    "backend/app/domains/intelligent_query/handlers.py::kol_video_topic_count": 46,
    "backend/app/domains/kol/analysis_precision_eval.py::evaluate_analysis_precision": 43,
    "backend/app/domains/kol/audience_stats_age.py::_age_ensemble": 41,
    "backend/app/domains/kol/business_contact_extract.py::_apify_scrape_about": 46,
    "backend/app/domains/kol/candidate_selection_rationale.py::build_candidate_selection_rationale": 44,
    "backend/app/domains/kol/comment_intel.py::analyze_comments": 44,
    "backend/app/domains/kol/comment_intel.py::compute_audience_overlap": 50,
    "backend/app/domains/kol/contact_acquisition_queue.py::_candidate_source": 45,
    "backend/app/domains/kol/contact_reveal.py::view_kol_contact": 46,
    "backend/app/domains/kol/content_fit_analysis.py::_video_analyses": 46,
    "backend/app/domains/kol/content_fit_enqueue.py::enqueue_content_fit_for_session": 49,
    "backend/app/domains/kol/data_completion_priority.py::_priority_item": 41,
    "backend/app/domains/kol/focal_matrix.py::focal_matrix": 43,
    "backend/app/domains/kol/my_kol_board_ext.py::_recent_videos": 42,
    "backend/app/domains/kol/natural_search.py::_natural_search_payload": 44,
    "backend/app/domains/kol/pool.py::detail_bundle": 50,
    "backend/app/domains/kol/profile_basics.py::write_kol_profile_basics": 50,
    "backend/app/domains/kol/profile_discovery_evidence.py::build_term_evidence": 49,
    "backend/app/domains/kol/profile_online_qualification.py::_qualify_online_candidates_internal": 44,
    "backend/app/domains/kol/profile_recall_projection.py::_format_item": 42,
    "backend/app/domains/kol/profile_recall_qualification.py::qualify_local_candidates": 43,
    "backend/app/domains/kol/search_session_job_sync.py::sync_search_session_job_impl": 44,
    "backend/app/domains/kol/search_sessions_attach.py::_safe_local_qualification": 41,
    "backend/app/domains/kol/smart_query_planner.py::_fallback_plan": 47,
    "backend/app/domains/kol/url_deep_crawl.py::dry_run_url_deep_crawl": 46,
    "backend/app/domains/kol/video_data_watch.py::data_watch": 44,
    "backend/app/domains/legacy_import/legacy_import_audit.py::audit_legacy_file": 43,
    "backend/app/domains/market/category_tracks.py::tracks": 45,
    "backend/app/domains/market/competitor_radar.py::generate_competitor_radar": 46,
    "backend/app/domains/market/competitor_radar.py::get_competitor_radar": 48,
    "backend/app/domains/market/hashtag_trends.py::build_hashtag_trends_v0": 50,
    "backend/app/domains/market/sentiment_annotate.py::annotate_batch": 43,
    "backend/app/domains/market_brain/outreach_reply_truth.py::verify_reply": 50,
    "backend/app/domains/market_brain/outreach_truth_bridge.py::outreach_prediction_coverage": 45,
    "backend/app/domains/market_brain/weekly_answers.py::weekly_report": 44,
    "backend/app/domains/media/cache_migration.py::migrate_local_video_cache_to_r2": 45,
    "backend/app/domains/memory/feedback_backlog.py::_entity_backlog_item": 42,
    "backend/app/domains/memory/legacy.py::build_memory_from_legacy_batch": 44,
    "backend/app/domains/memory/market.py::build_market_memory_from_legacy_batch": 46,
    "backend/app/domains/projects/contract_assist.py::run_invoice_extract_for_job": 42,
    "backend/app/domains/projects/contracts_extract.py::_normalized_business_field": 45,
    "backend/app/domains/projects/retrospective_content.py::reconcile_retrospective_content": 43,
    "backend/app/domains/projects/workflow_projects.py::create_project": 44,
    "backend/app/domains/recommendations/evidence.py::get_recommendation_evidence": 46,
    "backend/app/domains/recommendations/feature_store.py::get_features_at_time": 41,
    "backend/app/domains/sync/apify_batch_refresh.py::summarize_batch_execution": 48,
    "backend/app/main_worker_trust.py::trust_worker_impl": 46,
    "backend/app/platform/llm_gateway_preflight.py::budget_preflight_impl": 46,
    "backend/app/platform/llm_production_anthropic.py::generate_anthropic_messages": 43,
    "backend/app/services/ai/orchestrator.py::DBWriter.write": 43,
    "backend/app/services/deepsight/repository.py::fetch_submissions_window": 47,
    "backend/app/services/ingestion/pipeline.py::normalize_ingest_payload": 48,
    "backend/app/services/intelligence/account_search_youtube_metrics.py::youtube_channel_activation_summary": 44,
    "backend/app/services/intelligence/dashboard.py::build_bh_dashboard": 43,
    "backend/app/services/intelligence/market.py::list_observations": 41,
    "backend/app/services/intelligence/viltrox_matrix.py::_build_scan_payload": 47,
    "backend/app/services/kol/account_dossier.py::_persist_scan": 44,
    "backend/app/services/memory/via_learning_affiliate.py::_sync_affiliate_order_reward_traces": 48,
    "backend/app/services/via/knowledge_seed.py::extract_workspace_docx_product_line_catalog": 42,
    "backend/app/services/via/session_reply_orchestration.py::_promote_memory": 44,
    "backend/app/services/via/session_reply_orchestration.py::_record_initial_decisions": 50,
    "backend/app/services/via/session_reply_orchestration.py::_record_learning_and_outcome": 44,
    "backend/app/services/via/session_reward.py::_build_retrieval_evidence": 42,
    "backend/app/services/via/shadow_learning.py::evaluate_shadow_model_choice": 41,
    "backend/app/shared/vkpi_kpi_evidence.py::enrich_kpi_source_row": 45,
    "backend/app/workers/apify_jobs_worker_gemini_result.py::bind_execution_authorization_to_selected_model": 45,
    "backend/app/workers/apify_jobs_worker_handlers.py::_process_kol_profile_deep_crawl": 46,
    "backend/app/workers/redis_worker_health.py::redis_worker_fleet_health": 47,
    "backend/app/workers/tasks/analyze.py::process_full_audit": 41,
}
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
def _current_over_limit() -> dict[str, int]:
    """当前所有 CC>40 的函数:"path::qualified_name" -> CC(重名取最大)。"""
    rows = collector.collect_complexity(_parse_production_trees())
    over: dict[str, int] = {}
    for row in rows:
        if row.cc <= CC_LIMIT:
            continue
        key = f"{row.path}::{row.qualified_name}"
        over[key] = max(over.get(key, 0), row.cc)
    return over


def test_no_new_functions_over_cc_limit() -> None:
    current = _current_over_limit()
    newcomers = {key: cc for key, cc in current.items() if key not in CC_WHITELIST}
    assert not newcomers, (
        f"CC 棘轮:白名单外出现 CC>{CC_LIMIT} 的函数(谁越线见下,值=当前 CC)。{FIX_HINT} "
        f"越线函数:{dict(sorted(newcomers.items()))}"
    )


def test_whitelisted_functions_do_not_get_worse() -> None:
    current = _current_over_limit()
    grown = {
        key: {"now": current[key], "snapshot": CC_WHITELIST[key]}
        for key in CC_WHITELIST
        if key in current and current[key] > CC_WHITELIST[key]
    }
    assert not grown, (
        f"CC 棘轮:白名单内函数比快照时更复杂了(只许变好)。{FIX_HINT} 变差函数:{grown}"
    )


def test_whitelist_has_no_stale_entries() -> None:
    """修到 ≤40 / 改名 / 删除的函数必须同步从白名单删掉——白名单只减不增。"""
    current = _current_over_limit()
    stale = sorted(key for key in CC_WHITELIST if key not in current)
    assert not stale, (
        "CC 棘轮:白名单有过期条目(函数已修好/改名/删除),请从 "
        f"tests/test_cc_ratchet.py 的 CC_WHITELIST 删除(只删不加):{stale}"
    )


def test_whitelist_is_well_formed() -> None:
    assert CC_WHITELIST, "白名单为空:请经主会话/用户批准后运行 --refresh-baseline 生成"
    for key, cc in CC_WHITELIST.items():
        assert "::" in key and key.startswith("backend/app/"), key
        assert isinstance(cc, int) and cc > CC_LIMIT, (key, cc)


def _refresh_baseline() -> None:
    """原地重拍 CC_WHITELIST。**须主会话/用户批准后才可运行**(见模块 docstring)。"""
    me = Path(__file__)
    text = me.read_text(encoding="utf-8")
    _current_over_limit.cache_clear()
    body = "\n".join(f'    "{key}": {cc},' for key, cc in sorted(_current_over_limit().items()))
    block = "CC_WHITELIST: dict[str, int] = {\n" + body + "\n}"
    pattern = re.compile(r"CC_WHITELIST: dict\[str, int\] = \{.*?\n\}", re.S)
    assert pattern.search(text), "snapshot block missing"
    me.write_text(pattern.sub(lambda _m: block, text, count=1), encoding="utf-8")
    print(f"refreshed {len(_current_over_limit())} entries into {me.relative_to(ROOT)}")


if __name__ == "__main__":  # pragma: no cover - 维护入口
    if "--refresh-baseline" in sys.argv:
        _refresh_baseline()
    else:
        print("usage: python tests/test_cc_ratchet.py --refresh-baseline  # 须主会话/用户批准")
