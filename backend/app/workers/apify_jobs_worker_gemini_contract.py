"""Value objects and injected seams for Gemini video orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ContextManager


CallableAny = Callable[..., Any]


@dataclass(frozen=True)
class GeminiVideoRuntimeDependencies:
    target: CallableAny
    derive_method: CallableAny
    block_job: CallableAny
    load_video_evidence: CallableAny
    platform_from_content_url: CallableAny
    url_host: CallableAny
    process_keyframe_qa: CallableAny
    process_flash_pro_judge: CallableAny
    process_flash_gpt55_judge: CallableAny
    process_flash_claude_judge: CallableAny
    logger: Any
    monotonic: Callable[[], float]
    stage_clock_factory: CallableAny
    temporary_directory: Callable[..., ContextManager[str]]
    gemini_analyzer_payload: CallableAny
    resolve_cached_or_provider_video: CallableAny
    persist_image_post_verdict: CallableAny
    download_direct_video_url: CallableAny
    scope_checkpoint: CallableAny
    video_final_context: CallableAny
    video_performance_context: CallableAny
    run_analyzer: CallableAny
    warm_video_to_r2: CallableAny
    bind_execution_authorization: CallableAny
    mark_authorization_snapshot_missing: CallableAny
    ensure_final_v1_result_cacheable: CallableAny
    invalid_final_v1_error: type[BaseException]
    authoritative_cost: CallableAny
    record_cost: CallableAny
    record_diagnostics: CallableAny
    int_or_none: CallableAny
    shape_result: CallableAny
    execution_metadata: CallableAny
    quality_triage_target_type: CallableAny
    upsert_cache: CallableAny
    json_dump: CallableAny
    cache_prompt_version: CallableAny
    finish_cache_job: CallableAny
    quality_incomplete_reason: CallableAny
    sync_search_session_job: CallableAny
    sync_deep_result: CallableAny
    enqueue_account: CallableAny
    enqueue_content_fit: CallableAny
    extract_lens: CallableAny
    search_session_summary: CallableAny
    final_v1_keyframe_qa_derive_method: str
    final_derive_methods: frozenset[str]
    v2_derive_methods: frozenset[str]
    llm_budget_scope: str
    worker_model: str
    worker_execution_class: str


@dataclass(frozen=True)
class RoutedVideo:
    target_type: str
    target_id: str
    derive_method: str
    evidence: dict[str, Any]
    platform: str


@dataclass(frozen=True)
class AnalyzerRun:
    route: RoutedVideo
    started: float
    clock: Any
    analyzer_payload: dict[str, Any]
    model_chain: list[Any]
    authorization: dict[str, Any]


@dataclass(frozen=True)
class FinalizedVideo:
    raw: dict[str, Any]
    latency_ms: int
    cache_status: str
    cost: float
    cost_basis: str
    ledger: dict[str, Any]
