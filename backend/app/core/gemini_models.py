"""Gemini 视频主力/裁判模型的唯一默认值(leaf 模块,只依赖 os)。

为什么是独立 leaf:admin-web(video_analysis_enqueue 预检)与 worker(apify_jobs_worker
执行)必须认同同一个精确模型,否则 worker_runtime / worker_gemini 会以
model_binding_mismatch 拦下每一条视频 job;而 admin-web 进程绝不能 import
apify_jobs_worker(会拖进 psycopg 常驻连接与信号处理)。本模块零业务依赖,两侧各自 import。

字面契约(跨车道,一字不差):'gemini-3.6-flash' 必须等于
model_registry.TASK_MODEL_BINDING['audit_video_analysis'] 的模型后缀,
以及 platform/llm_local_evaluation.LOCAL_EVALUATION_MODEL。
tests/test_gemini_models_leaf.py 守这条不变量。

env 名沿用旧 worker(APIFY_WORKER_GEMINI_MODEL / GEMINI_FINAL_V1_QA_MODEL),
prod 可用 env 钉回旧模型而不改代码。
"""
from __future__ import annotations

import os


def _env_model(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


# 付费视频 worker 预检+执行的唯一精确模型(final_v1 六层分析主力)。
DEFAULT_VIDEO_GEMINI_MODEL = _env_model("APIFY_WORKER_GEMINI_MODEL", "gemini-3.6-flash")
# 关键帧 QA / v2 评审的 Gemini 裁判(直连 SDK,thinking_level=minimal)。
DEFAULT_GEMINI_JUDGE_MODEL = _env_model("GEMINI_FINAL_V1_QA_MODEL", "gemini-3.5-flash-lite")
# v2 多 pass 评审的「视觉 pass」模型:与视频主力同一模型。
VISUAL_PASS_MODEL = DEFAULT_VIDEO_GEMINI_MODEL
# final_v1 默认模型链:单精确模型(链语义仍保留给 GEMINI_FINAL_V1_MODELS env)。
DEFAULT_FINAL_V1_CHAIN = (DEFAULT_VIDEO_GEMINI_MODEL,)


def is_gemini_3_family(model_name: str) -> bool:
    """3.x 家族必须用 thinking_level='minimal'(thinking_budget=0 会 400);2.5 仍用 budget。"""
    return str(model_name or "").strip().lower().startswith("gemini-3")
