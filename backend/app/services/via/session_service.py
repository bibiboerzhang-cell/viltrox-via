"""Via session/persona orchestration compatibility facade."""
from __future__ import annotations

from app.services.via.session_generation import (
    _generate_via_reply_with_ai,
    _resolve_retrieval_execution,
    compose_via_reply,
)
from app.services.via.session_guidance import (
    _CASUAL_CHAT_PATTERNS,
    _DEEP_REASONING_PATTERNS,
    _FOLLOW_UP_MEMORY_PATTERNS,
    _IDENTITY_PATTERNS,
    _IMAGE_VIDEO_PATTERNS,
    _JAILBREAK_PATTERNS,
    _MEMORY_PATTERNS,
    _PHOTOGRAPHY_BASICS_PATTERNS,
    _PRODUCT_LINE_PATTERNS,
    _SENSITIVE_PATTERNS,
    _SOFTWARE_PATTERNS,
    _TRANSACTIONAL_PRODUCT_PATTERNS,
    _casual_companion_reply,
    _classify_via_intent,
    _contains_cjk,
    _guard_sensitive_request,
    _matches_any,
    _photography_guide_reply,
    _product_line_context_lines,
    _product_line_context_payload,
    _product_line_guide_reply,
    _reply_lang,
    _should_use_ai_dialogue,
    _should_use_dialogue_collab,
    _software_context_lines,
    _software_guide_reply,
    _targeted_product_line_keys,
)
from app.services.via.session_lifecycle import (
    _emit_via_session_to_party_layer,
    bootstrap_via_session,
    patch_via_persona_for_session,
    publish_via_session_event,
    refresh_via_memory_refs,
)
from app.services.via.session_memory import (
    _anonymous_persona_key,
    _fire_and_forget,
    _hash_ip,
    _load_memory_candidates,
    _memory_prompt_lines,
    _memory_teaser,
    _parse_json_object,
    _persist_via_learning,
    _prime_via_memory_assets,
    _sanitize_persona_patch,
)
from app.services.via.session_reply import reply_in_via_session
from app.services.via.session_reward import (
    _REWARD_TRACE_DEDUPE_EVENTS,
    _VIA_REWARD_TRACE_EVENTS,
    _build_retrieval_evidence,
    _control_source_ref,
    _pick_reward_trace_decision,
    _record_shadow_eval,
    _reinforce_memory_retention,
    _reward_trace_source,
    _routing_bucket_key,
    record_via_reward_trace_for_session,
)

__all__ = [name for name in globals() if not name.startswith("__")]
