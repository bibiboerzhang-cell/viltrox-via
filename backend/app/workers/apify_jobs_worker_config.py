"""Leaf config module for the apify_jobs_worker family (shared constants).

拆 import 期真活环(2026-08-30):worker 顶层 import media/prep/maintenance/gemini/
judges 等子模块,子模块又在底部反向 import worker 里的这些常量——六模块在 import 期
互相进入对方的半初始化命名空间。本叶子模块收编「无 worker 依赖」的共享常量;
子模块改 import 叶子,apify_jobs_worker 原位保留同名 re-export,monkeypatch 路径
(app.workers.apify_jobs_worker.<NAME>)与既有 import 点逐字不变。

行为不变量:定义逐字搬迁自 apify_jobs_worker.py,env 读取/默认值/推导关系不变。
注意:本模块绝不 import apify_jobs_worker* 家族任何成员(保持叶子)。
_provider_retry_delay_seconds/_block_job/_finish_skipped 仍钉在 worker
(测试按 worker 模块 globals monkeypatch 常量 + 源码守卫/namespace 契约)。
"""
from __future__ import annotations

import os

from app.core.gemini_models import DEFAULT_GEMINI_JUDGE_MODEL, DEFAULT_VIDEO_GEMINI_MODEL
# 本地算力 worker 专属 job_type 白名单。单一真源仍是 registry.SAFE_TASK_TYPES(别名 import 防漂移);
# 2026-08-31 fan-out 刀把这一跳从 apify_jobs_worker.py 顶层搬进本叶子,worker 侧改成同名 re-export,
# 抢单 SQL 的常量白名单拼装与取值逐字不变。
from app.domains.local_workers.registry import SAFE_TASK_TYPES as LOCAL_EXCLUSIVE_JOB_TYPES  # noqa: F401
from app.platform.llm_gateway import PRODUCTION_EXECUTION_CLASS
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer


MEDIA_RESOLVE_TIMEOUT_SECONDS = max(10, int(os.environ.get("APIFY_WORKER_MEDIA_RESOLVE_TIMEOUT_SEC", "90")))
GEMINI_CALL_TIMEOUT_SECONDS = max(30, int(os.environ.get("APIFY_WORKER_GEMINI_CALL_TIMEOUT_SEC", "1200")))
GEMINI_CALL_TERMINATE_GRACE_SECONDS = max(1, int(os.environ.get("APIFY_WORKER_GEMINI_CALL_TERMINATE_GRACE_SEC", "5")))
STALE_RUNNING_MINUTES = max(1, int(os.environ.get("APIFY_WORKER_STALE_RUNNING_MINUTES", "10")))
STALE_RECLAIM_SECONDS = STALE_RUNNING_MINUTES * 60

MAX_JOB_ATTEMPTS = max(1, int(os.environ.get("APIFY_WORKER_MAX_ATTEMPTS", "2")))
PROVIDER_RETRY_MAX_ATTEMPTS = max(1, int(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_MAX_ATTEMPTS", "5")))
PROVIDER_RETRY_ADOPT_WINDOW_MINUTES = max(0, int(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_ADOPT_WINDOW_MINUTES", "1440")))
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
# 1200 会截断六层 final_v1(分镜只剩前 ~35s);4096 容纳整段分镜时间线,可 env 覆盖。
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "4096"))

GEMINI_VIDEO_V2_DERIVE_METHODS = {
    "gemini_video_v2",
    "gemini_video_v2_pro_single",
    "gemini_video_v2_flash_pro_judge",
    "gemini_video_v2_flash_gpt55_judge",
    "gemini_video_v2_flash_claude_judge",
}
FINAL_V1_KEYFRAME_QA_DERIVE_METHOD = "video_analysis_final_v1_keyframe_qa"
GEMINI_VIDEO_FINAL_DERIVE_METHODS = {"video_analysis_final_v1", FINAL_V1_KEYFRAME_QA_DERIVE_METHOD}
WORKER_GEMINI_MODEL = DEFAULT_VIDEO_GEMINI_MODEL  # env APIFY_WORKER_GEMINI_MODEL(core/gemini_models 唯一默认)
# Worker processes are always production by default.  A persisted, server-
# signed job capability is the only mechanism that can authorize the narrow
# local evaluation branch for one job; an environment flag cannot reinterpret
# an old queue.
WORKER_LLM_EXECUTION_CLASS = PRODUCTION_EXECUTION_CLASS
# One exact worker model is both preflighted and executed.  The former default
# fallback list (3-flash-preview -> 2.5-flash) let a preflight for one binding
# authorize a different provider request.
FINAL_V1_GEMINI_MODELS = gemini_video_analyzer.final_v1_gemini_models([WORKER_GEMINI_MODEL])
FINAL_V1_KEYFRAME_QA_MODEL = DEFAULT_GEMINI_JUDGE_MODEL  # env GEMINI_FINAL_V1_QA_MODEL
