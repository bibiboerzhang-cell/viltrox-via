"""V-KPI Memory v0 compatibility facade.

Concrete implementation lives in memory_* modules so no single file absorbs all
Memory product, market, matching, and feedback behavior again.
"""
from __future__ import annotations

from app.services.vkpi.memory_common import (
    _entity_uid,
    _fact_uid,
    _fetch_batch,
    _kol_feature_summary,
    _link_uid,
    _load_json,
    _market_signal_counts,
    _memory_candidate_score,
    _memory_entity_by_uid,
    _public_entity,
    _public_fact,
    _public_feedback,
    _public_link,
    _public_market_signal,
    _public_product_link,
    _readiness_gate,
    _row_to_dict,
    _safe_limit,
    _slug,
    _snapshot_uid,
    _table_exists,
    _upsert_entity,
    _upsert_fact,
    _upsert_link,
    _utcnow,
    ensure_memory_schema,
    list_entities,
    summary,
)
from app.services.vkpi.memory_feedback import list_feedback, readiness, record_feedback, update_feedback
from app.services.vkpi.memory_legacy import build_memory_from_legacy_batch
from app.services.vkpi.memory_market import (
    _market_target_for_product,
    _market_topic_entity,
    _official_account_entity,
    _product_family_for_normalized,
    _upsert_market_signal,
    build_market_memory_from_legacy_batch,
    market_signal_summary,
    market_signals,
)
from app.services.vkpi.memory_matching import (
    _matched_product_rows,
    entity_facts,
    fit_features,
    kol_product_memory,
    product_kol_candidates,
)
from app.services.vkpi.memory_product import (
    _clean_product_name,
    _format_aperture,
    _normalize_lens_family,
    _normalize_model_family,
    _normalize_product_family,
    _product_entity,
    _product_key,
    _product_mount_tokens,
    _product_series_tokens,
    _title_product_model,
    build_product_family_memory,
    product_family_summary,
)

__all__ = [name for name in globals() if not name.startswith("__")]
