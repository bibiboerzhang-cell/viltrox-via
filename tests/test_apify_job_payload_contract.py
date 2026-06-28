"""体系④ Worker job_type ↔ payload 契约锁。

目的:apify_jobs 队列没有中央 schema —— 每个入队器(enqueue_*)各自手搓 payload dict,
worker `_process_job` 再按 job_type 分派到对应 `_process_*` handler。一旦入队器漏塞 handler
赖以工作的关键字段、或新增 job_type 忘了接 worker 分派,就会在跑时炸(ValueError / 静默 blocked),
而不是在合并时被拦。本测试把「真实入队器代码契约」固化成断言:

  1. 每个**活跃** job_type(DB select distinct job_type 实测到的)→ 其 payload 必含的关键字段。
     字段集**从入队器源码推**(payload = {...} 字面),不是从脏数据采样 —— 脏数据可能历史漂移。
  2. worker `_process_job` 的分派表确实路由了每个**有 handler 的**活跃 job_type。
  3. 入队器函数真的存在且可导入(import 级回归)。

口径:
  - 活跃集 = 2026-06-28 DB 实测 11 个 distinct job_type。
  - `contract_invoice_extract` / `contract_polish` 在 worker 分派里有 handler 但 DB 零行 →
    非活跃,本测试不覆盖(见 NOTES)。
  - `kol_auto_poll` 是活跃的(入队 50 条),但 worker `_process_job` **没有** handler 分派 ——
    全部落到 `_target` 兜底分支因缺 target_type/target_id 抛 ValueError → DB 里 50 条全 failed。
    这是真实 gap,本测试用 `test_kol_auto_poll_worker_dispatch_gap` 把它**钉成显式断言**:
    一旦有人补了 handler,该测试会红,提醒同步更新契约(届时移到 routed 集)。

红线:本测试零触 viltrox_fit_score、零真跑抓取/LLM、零入队 —— 纯静态契约 + 源码反射。
"""
from __future__ import annotations

import inspect

import pytest


# ---------------------------------------------------------------------------
# 活跃 job_type 集(2026-06-28 DB: select distinct job_type from apify_jobs)
# 注释里的数字是当时的行数,仅供溯源,断言不依赖它。
# ---------------------------------------------------------------------------
ACTIVE_JOB_TYPES = {
    "video",                            # 871
    "account_dossier_extract",          # 393
    "kol_content_fit_analysis",         # 236
    "kol_auto_poll",                    # 50  (无 worker handler —— 见 gap 测试)
    "smart_search_profile_advance",     # 34  (入队器写 'session_advance' job_type;见 NOTES)
    "kol_profile_deep_crawl",           # 30
    "logistics_track_sync",             # 8
    "project_retrospective_aggregate",  # 6
    "project_contract_extract",         # 4
    "kol_outreach_draft",               # 3
    "kol_pool_comments_collect",        # 1
}


# ---------------------------------------------------------------------------
# job_type → payload 必含关键字段(从入队器 payload={...} 字面 + handler 校验推)。
# 「关键字段」= handler 真正取来用 / 入队器无条件写入的 load-bearing 槽,
# 不含纯展示/可选的 summary、prompt、source 等。
# ---------------------------------------------------------------------------
PAYLOAD_CONTRACT: dict[str, set[str]] = {
    # enqueue_final_v1_video_analysis*(video_analysis_enqueue.py):走 _target 分支,
    # 必须 target_type='video' + target_id(handler 侧 LLM_TARGET_TYPES 校验)+ derive_method 选 gemini 管线。
    "video": {"target_type", "target_id", "derive_method", "kol_pool_id"},
    # enqueue_account_dossier_extract_job:handler 取 target_id|kol_pool_id;走 LLM? 否,走专 handler。
    "account_dossier_extract": {"target_type", "target_id", "derive_method", "analysis_kind"},
    # enqueue_content_fit_for_session/on_demand:handler raise 若缺 kol_pool_id(或 target_id 兜)。
    "kol_content_fit_analysis": {"target_id", "kol_pool_id", "derive_method"},
    # enqueue_auto_poll:轻量 metadata 刷新;handler 缺失(gap)。入队器无条件写 kol_pool_id/mode/trigger。
    "kol_auto_poll": {"kol_pool_id", "mode", "trigger"},
    # enqueue_search_session_advance:job_type 实为 'session_advance',handler 取 search_session_id|target_id。
    "smart_search_profile_advance": {"search_session_id", "target_type", "target_id"},
    # enqueue_profile_deep_crawl_job:handler 内核取 url(run_profile_deep_crawl_for_job)。
    "kol_profile_deep_crawl": {"url", "target_type"},
    # enqueue_logistics_sync_job:handler 取 scope_key。
    "logistics_track_sync": {"scope_key", "target_type"},
    # enqueue_project_retrospective:handler 取 target_id|project_id。
    "project_retrospective_aggregate": {"target_type", "target_id", "project_id", "derive_method"},
    # enqueue_contract_extract_job:handler raise 若缺 target_id/project_id。
    "project_contract_extract": {"target_type", "target_id", "project_id", "r2_key", "file_name"},
    # enqueue_outreach_draft_job:handler 内核取 kol_pool_id。
    "kol_outreach_draft": {"kol_pool_id", "target_type", "target_id"},
    # enqueue_kol_pool_comments_job:handler raise 若缺 kol_pool_id。
    "kol_pool_comments_collect": {"kol_pool_id", "target_type", "target_id"},
}


# job_type → (入队器模块, 入队器函数名)。import 级回归 + 取源码验契约。
ENQUEUER_REF: dict[str, tuple[str, str]] = {
    # payload 字面在私有内核 _enqueue_final_v1_video_analysis;公开 enqueue_* 只是薄包装。
    "video": ("app.domains.kol.video_analysis_enqueue", "_enqueue_final_v1_video_analysis"),
    "account_dossier_extract": ("app.domains.kol.account_dossier_extract", "enqueue_account_dossier_extract_job"),
    "kol_content_fit_analysis": ("app.domains.kol.content_fit_enqueue", "enqueue_content_fit_for_session"),
    "kol_auto_poll": ("app.domains.kol.auto_poll", "enqueue_auto_poll"),
    "smart_search_profile_advance": ("app.domains.kol.profile_discovery_queue", "enqueue_search_session_advance"),
    "kol_profile_deep_crawl": ("app.domains.kol.url_deep_crawl", "enqueue_profile_deep_crawl_job"),
    "logistics_track_sync": ("app.domains.logistics.seventeen_track", "enqueue_logistics_sync_job"),
    "project_retrospective_aggregate": ("app.domains.projects.retrospective_aggregate", "enqueue_project_retrospective"),
    "project_contract_extract": ("app.domains.projects.contracts", "enqueue_contract_extract_job"),
    "kol_outreach_draft": ("app.domains.kol.outreach_draft", "enqueue_outreach_draft_job"),
    "kol_pool_comments_collect": ("app.domains.comments.collector", "enqueue_kol_pool_comments_job"),
}


# worker `_process_job` 里实际接了专属 handler 分派的活跃 job_type(从源码反射)。
# 注意:入队器把 'smart_search_profile_advance' 当 job_type 暴露给上层,但 INSERT 写的是
# 'session_advance',worker 两个 key 都接。video 走 _target 兜底分支(非显式 if)。
WORKER_ROUTED_JOB_TYPES = {
    "session_advance",
    "smart_search_profile_advance",
    "kol_content_fit_analysis",
    "account_dossier_extract",
    "project_contract_extract",
    "project_retrospective_aggregate",
    "kol_profile_deep_crawl",
    "kol_pool_comments_collect",
    "kol_outreach_draft",
    "logistics_track_sync",
}


def _enqueuer_source(job_type: str) -> str:
    mod_name, fn_name = ENQUEUER_REF[job_type]
    mod = __import__(mod_name, fromlist=[fn_name])
    fn = getattr(mod, fn_name)
    return inspect.getsource(fn)


def _process_job_source() -> str:
    from app.workers import apify_jobs_worker as worker

    return inspect.getsource(worker._process_job)


# ---------------------------------------------------------------------------
# 1) 契约表自洽:覆盖且只覆盖活跃集
# ---------------------------------------------------------------------------
def test_contract_covers_all_active_job_types():
    assert set(PAYLOAD_CONTRACT) == ACTIVE_JOB_TYPES, (
        "PAYLOAD_CONTRACT 必须精确覆盖活跃 job_type 集 —— "
        f"缺: {ACTIVE_JOB_TYPES - set(PAYLOAD_CONTRACT)} 多: {set(PAYLOAD_CONTRACT) - ACTIVE_JOB_TYPES}"
    )


def test_enqueuer_ref_covers_all_active_job_types():
    assert set(ENQUEUER_REF) == ACTIVE_JOB_TYPES


# ---------------------------------------------------------------------------
# 2) 每个活跃 job_type 的入队器 import 级存在(回归:模块/函数没被改名删掉)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("job_type", sorted(ACTIVE_JOB_TYPES))
def test_enqueuer_importable(job_type):
    mod_name, fn_name = ENQUEUER_REF[job_type]
    mod = __import__(mod_name, fromlist=[fn_name])
    assert callable(getattr(mod, fn_name)), f"{mod_name}.{fn_name} 不可调用"


# ---------------------------------------------------------------------------
# 3) 契约字段确实出现在入队器源码的 payload 构造里(从代码推,不从脏数据)
#    入队器都用 payload = {"key": ...} 字面手搓,字段名必出现在源码字符串中。
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("job_type", sorted(ACTIVE_JOB_TYPES))
def test_required_fields_present_in_enqueuer_source(job_type):
    src = _enqueuer_source(job_type)
    missing = [k for k in PAYLOAD_CONTRACT[job_type] if f'"{k}"' not in src and f"'{k}'" not in src]
    assert not missing, (
        f"{job_type} 入队器源码未构造契约字段 {missing} —— "
        "要么入队器漏塞、要么契约写错(从入队器 payload={{...}} 校准)"
    )


# ---------------------------------------------------------------------------
# 4) worker 分派表路由了每个「有 handler」的活跃 job_type
#    （kol_auto_poll 与 video 例外:见各自专测)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("job_type", sorted(WORKER_ROUTED_JOB_TYPES))
def test_worker_dispatch_routes_handler_job_type(job_type):
    src = _process_job_source()
    assert f'"{job_type}"' in src, (
        f"worker _process_job 未显式分派 job_type={job_type} —— "
        "新增 job_type 忘接 worker?会落 _target 兜底分支跑时炸"
    )


def test_video_job_routes_via_target_fallback():
    """video 不走显式 if 分支,而是落 _target 兜底 → 必须 target_type='video' 在 LLM_TARGET_TYPES。"""
    from app.workers.apify_jobs_worker import LLM_TARGET_TYPES

    assert "video" in LLM_TARGET_TYPES, (
        "video job 靠 _target 兜底分支 + LLM_TARGET_TYPES 准入;移除会让 video job 被 block"
    )
    # video 契约要求 target_type/target_id —— 兜底分支缺它直接 raise。
    assert {"target_type", "target_id"}.issubset(PAYLOAD_CONTRACT["video"])


# ---------------------------------------------------------------------------
# 5) GAP 钉子:kol_auto_poll 活跃但 worker 无 handler。
#    入队器注释自称「worker 端再去真抓」,实际 worker _process_job 没接 ——
#    落 _target 兜底分支因缺 target_type/target_id 抛 ValueError,DB 里 50 条全 failed。
#    把现状钉成断言:补了 handler 后此测试会红,提醒把 kol_auto_poll 移进 routed 集 + 校契约。
# ---------------------------------------------------------------------------
def test_kol_auto_poll_worker_dispatch_gap():
    src = _process_job_source()
    assert '"kol_auto_poll"' not in src and "'kol_auto_poll'" not in src, (
        "kol_auto_poll 现在被 worker 分派了 —— gap 已修复。请:把 'kol_auto_poll' 加进 "
        "WORKER_ROUTED_JOB_TYPES、校准 PAYLOAD_CONTRACT、删除本 gap 测试。"
    )
    # 且其 payload 不带 target_type/target_id —— 这正是它落兜底分支必炸的根因。
    auto_poll_keys = PAYLOAD_CONTRACT["kol_auto_poll"]
    assert "target_type" not in auto_poll_keys and "target_id" not in auto_poll_keys
