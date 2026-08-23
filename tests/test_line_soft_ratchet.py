"""800 行软棘轮(2026-08-23 优化波 B·A 车道 C 架构债)。

口径:≤700 软 / ≤1000 硬(scripts/verify.sh 千行卫兵已守硬线)。本测试守 800 这一档:
- ``LINE_SNAPSHOT``:快照时所有 >800 行的 .py 文件(backend/app + scripts,
  tests 不入账)及其行数;
- 清单内文件**不许变长**(行数 ≤ 快照值;缩短/拆掉/降到 800 以下都通过);
- 清单外文件**不许超过 800**(新文件、被撑大的老文件一律拦)。

改动让清单内文件变短后,快照不必立刻更新(只会更严);确需重拍快照请运行
``.venv/bin/python tests/test_line_soft_ratchet.py --refresh``(原地重写 LINE_SNAPSHOT),
并在提交说明里解释为什么某个文件必须变长——棘轮的意义就是让"变长"成为需要理由的事。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOFT_LIMIT = 800
ROOTS = ("backend/app", "scripts")
SKIP_PARTS = {"__pycache__", "node_modules", ".venv", "generated", "fixtures", "build", "dist"}

# --- snapshot begin (generated; do not edit by hand) ---
LINE_SNAPSHOT: dict[str, int] = {
    "backend/app/api/routers/vkpi_goaffpro.py": 999,
    "backend/app/api/routers/vkpi_kol_pool.py": 997,
    "backend/app/api/routers/vkpi_kol_pool_intel.py": 917,
    "backend/app/api/routers/vkpi_kol_pool_search.py": 997,
    "backend/app/api/routers/vkpi_my_kol.py": 988,
    "backend/app/api/routers/vkpi_projects.py": 923,
    "backend/app/db/connection.py": 968,
    "backend/app/domains/actions/executors.py": 999,
    "backend/app/domains/actions/inbox.py": 880,
    "backend/app/domains/channels/official.py": 864,
    "backend/app/domains/comments/channel.py": 854,
    "backend/app/domains/comments/collector.py": 988,
    "backend/app/domains/comments/reply_queue.py": 815,
    "backend/app/domains/commerce/dealer_candidate_quarantine.py": 893,
    "backend/app/domains/commerce/dealer_directory_adapters.py": 946,
    "backend/app/domains/commerce/dealer_directory_view.py": 828,
    "backend/app/domains/commerce/dealer_scrape.py": 972,
    "backend/app/domains/commerce/shopify_client_credentials.py": 826,
    "backend/app/domains/content_metric_snapshots.py": 851,
    "backend/app/domains/costs/budget_guard.py": 993,
    "backend/app/domains/costs/ledger.py": 943,
    "backend/app/domains/events/candidate_staging.py": 993,
    "backend/app/domains/events/dealer_activity_sync.py": 987,
    "backend/app/domains/events/feed_adapters.py": 978,
    "backend/app/domains/events/inventory_service.py": 852,
    "backend/app/domains/events/radar.py": 832,
    "backend/app/domains/events/radar_quality_core.py": 963,
    "backend/app/domains/events/service.py": 958,
    "backend/app/domains/intelligent_query/handlers.py": 884,
    "backend/app/domains/intelligent_query/weekly_voice.py": 905,
    "backend/app/domains/kol/contact_acquisition_queue.py": 831,
    "backend/app/domains/kol/contact_ingest.py": 902,
    "backend/app/domains/kol/contact_system.py": 992,
    "backend/app/domains/kol/content_fit_analysis.py": 1000,
    "backend/app/domains/kol/data_completion_priority.py": 843,
    "backend/app/domains/kol/lens_evidence.py": 828,
    "backend/app/domains/kol/lens_evidence_store.py": 811,
    "backend/app/domains/kol/my_kol_board_ext.py": 880,
    "backend/app/domains/kol/pool.py": 842,
    "backend/app/domains/kol/pool_common.py": 1000,
    "backend/app/domains/kol/profile_recall.py": 998,
    "backend/app/domains/kol/profile_recall_precision.py": 838,
    "backend/app/domains/kol/profile_recall_projection.py": 963,
    "backend/app/domains/kol/profile_recall_qualification.py": 1000,
    "backend/app/domains/kol/provider_job_access.py": 977,
    "backend/app/domains/kol/recall_pipeline.py": 806,
    "backend/app/domains/kol/search_relevance_eval.py": 841,
    "backend/app/domains/kol/search_sessions.py": 853,
    "backend/app/domains/kol/search_sessions_attach.py": 997,
    "backend/app/domains/kol/search_sessions_serde.py": 906,
    "backend/app/domains/kol/url_deep_crawl.py": 842,
    "backend/app/domains/kol/video_analysis_enqueue.py": 967,
    "backend/app/domains/market/ai_today.py": 999,
    "backend/app/domains/market/category_tracks.py": 808,
    "backend/app/domains/market/competitor_radar.py": 980,
    "backend/app/domains/market/market_voice.py": 854,
    "backend/app/domains/market/strategy_sim.py": 905,
    "backend/app/domains/market/voice_report_ext.py": 901,
    "backend/app/domains/market_brain/gtm_plan_preview.py": 1000,
    "backend/app/domains/market_brain/outreach_reply_truth.py": 853,
    "backend/app/domains/market_brain/outreach_truth_bridge.py": 999,
    "backend/app/domains/market_brain/prediction_ledger.py": 976,
    "backend/app/domains/market_brain/verdict_flow.py": 843,
    "backend/app/domains/media/cache_core.py": 880,
    "backend/app/domains/ops/health_sentinel.py": 846,
    "backend/app/domains/platform/workflow_repository.py": 940,
    "backend/app/domains/products/official_catalog_sync.py": 868,
    "backend/app/domains/products/sku_performance.py": 868,
    "backend/app/domains/projects/contract_assist.py": 898,
    "backend/app/domains/projects/launch_assembly.py": 884,
    "backend/app/domains/projects/observation_windows.py": 1274,
    "backend/app/domains/projects/workflow_evidence.py": 900,
    "backend/app/domains/projects/workflow_projects.py": 922,
    "backend/app/domains/recommendations/new_launch_match.py": 839,
    "backend/app/domains/recommendations/outcomes.py": 845,
    "backend/app/domains/reports/export_jobs.py": 839,
    "backend/app/domains/reports/reports.py": 980,
    "backend/app/domains/reports/weekly_generator.py": 826,
    "backend/app/domains/source_passport_store.py": 859,
    "backend/app/domains/tasks/queue_view.py": 942,
    "backend/app/main.py": 1000,
    "backend/app/platform/apify_budget.py": 998,
    "backend/app/platform/llm_gateway.py": 927,
    "backend/app/platform/llm_gateway_invoke.py": 895,
    "backend/app/platform/llm_gateway_json.py": 936,
    "backend/app/platform/models/readiness.py": 892,
    "backend/app/services/ai/analyzers/gemini_video.py": 820,
    "backend/app/services/intelligence/account_search_discovery.py": 843,
    "backend/app/services/jobs/queue.py": 851,
    "backend/app/services/scheduler/fleet_guard.py": 989,
    "backend/app/services/scheduler/jobs_tasks.py": 999,
    "backend/app/services/system/staff.py": 836,
    "backend/app/workers/apify_jobs_worker.py": 991,
    "backend/app/workers/apify_jobs_worker_gemini.py": 966,
    "backend/app/workers/apify_jobs_worker_handlers.py": 869,
    "backend/app/workers/apify_jobs_worker_session.py": 874,
    "scripts/audit_vkpi_event_radar_catalog.py": 980,
    "scripts/backfill_kol_profile_basics.py": 829,
    "scripts/benchmark_kol_online_mock.py": 804,
    "scripts/etl_excel_to_vkpi.py": 1112,
    "scripts/etl_promo_plan_incremental.py": 990,
    "scripts/local_release_acceptance.py": 917,
    "scripts/ops/atomic_release_layout.py": 833,
    "scripts/ops/cloud_preflight_remediation_bundle.py": 825,
    "scripts/ops/freeze_worktree_candidate.py": 1000,
    "scripts/ops/legacy_to_atomic_preflight.py": 945,
    "scripts/ops/load_test_approval.py": 998,
    "scripts/ops/load_test_cli.py": 908,
    "scripts/ops/load_test_runner.py": 918,
    "scripts/ops/pgbouncer_release_map.py": 887,
    "scripts/ops/postgres_restore_rehearsal.py": 989,
    "scripts/ops/staging_db_clone.py": 998,
    "scripts/ops/verify_legacy_bootstrap_anchor.py": 866,
    "scripts/ops/vkpi_stage1_model_canary.py": 951,
    "scripts/scrape_youtube_evidence.py": 807,
    "scripts/verify_browser_console_capture.py": 983,
    "scripts/vkpi_report_model_benchmark.py": 998,
}
# --- snapshot end ---


def _count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root_text in ROOTS:
        root = ROOT / root_text
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            files.append(path)
    return sorted(files)


def _current_oversized() -> dict[str, int]:
    return {
        str(path.relative_to(ROOT)): lines
        for path in _iter_python_files()
        if (lines := _count_lines(path)) > SOFT_LIMIT
    }


def test_snapshot_files_do_not_grow() -> None:
    current = _current_oversized()
    grown = {
        path: {"now": lines, "snapshot": LINE_SNAPSHOT[path]}
        for path, lines in current.items()
        if path in LINE_SNAPSHOT and lines > LINE_SNAPSHOT[path]
    }
    assert not grown, (
        "800 行软棘轮:清单内文件变长了(只许变短)。请把新增逻辑拆到同目录的 *_xxx.py 兄弟模块;"
        f"确需变长请 --refresh 重拍快照并在提交说明解释:{grown}"
    )


def test_files_outside_snapshot_stay_under_soft_limit() -> None:
    current = _current_oversized()
    newcomers = {path: lines for path, lines in current.items() if path not in LINE_SNAPSHOT}
    assert not newcomers, (
        f"800 行软棘轮:清单外文件超过 {SOFT_LIMIT} 行(新文件零豁免,老文件不得被撑过线):{newcomers}"
    )


def test_snapshot_is_well_formed() -> None:
    assert LINE_SNAPSHOT, "快照为空:请运行 --refresh 生成"
    for path, lines in LINE_SNAPSHOT.items():
        assert path.startswith(ROOTS), path
        assert isinstance(lines, int) and lines > SOFT_LIMIT, (path, lines)


def _refresh() -> None:
    me = Path(__file__)
    text = me.read_text(encoding="utf-8")
    body = "\n".join(f'    "{path}": {lines},' for path, lines in sorted(_current_oversized().items()))
    block = "LINE_SNAPSHOT: dict[str, int] = {\n" + body + "\n}"
    pattern = re.compile(r"LINE_SNAPSHOT: dict\[str, int\] = \{.*?\n\}", re.S)
    assert pattern.search(text), "snapshot block missing"
    me.write_text(pattern.sub(lambda _m: block, text, count=1), encoding="utf-8")
    print(f"refreshed {len(_current_oversized())} entries into {me.relative_to(ROOT)}")


if __name__ == "__main__":  # pragma: no cover - 维护入口
    if "--refresh" in sys.argv:
        _refresh()
    else:
        print("usage: python tests/test_line_soft_ratchet.py --refresh")
