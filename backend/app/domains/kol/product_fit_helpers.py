"""Shared helpers for deterministic KOL-to-product-family fit previews."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from app.core.model_registry import current_task_model_binding, split_binding
from app.db.connection import get_conn
from app.platform import llm_production
from app.domains.recommendations.new_launch_match import (
    _country_key,
    _entity_payload,
    _evidence_count,
    _fact_payload,
    _family_tokens,
    _first_fact,
    _freshness_score,
    _json_default,
    _kol_entities,
    _kol_facts,
    _latest_fact_value,
    _legacy_entities_by_uid,
    _load_json,
    _lower,
    _market_detail,
    _market_signal_score,
    _percentile,
    _pool_by_source_ref,
    _product_family_maps,
    _risk_count,
    _row_to_dict,
    _safe_float,
    _safe_int,
    _source_payload,
    _target_market_signals,
    _text,
    _worked_links,
)

SCENARIO = "kol_product_fit"
REASON_BUDGET_SCOPE = "cron:p4_recommendation_reasons"
REASON_MODEL_TASK = "kol_product_fit_reason"


def _reason_model_binding() -> tuple[str, str]:
    return split_binding(current_task_model_binding().get(REASON_MODEL_TASK) or "")


def _reason_failure_code(value: Any) -> str:
    result = value if isinstance(value, dict) else {}
    failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    latest = errors[-1] if errors and isinstance(errors[-1], dict) else {}
    return str(
        failure.get("code")
        or result.get("failure_code")
        or result.get("reason")
        or latest.get("status")
        or "llm_unavailable"
    )[:120]


def _valid_reason_payload(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(key), str) and bool(str(value.get(key) or "").strip())
        for key in ("short_reason", "pitch_angle", "caution_note")
    )


def _pool_by_id(pool_map: dict[str, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {_safe_int(row.get("id")): row for row in pool_map.values() if _safe_int(row.get("id"))}


def _kol_by_source_ref(kol_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in kol_rows:
        identity = _entity_payload(row, "identity_json")
        source_ref = _text(identity.get("source_ref"))
        if source_ref:
            result[source_ref] = row
    return result


def _resolve_kol(
    *,
    kol_entity_uid: str = "",
    kol_pool_id: int = 0,
    platform: str = "",
    handle: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    selectors = [
        bool(_text(kol_entity_uid)),
        bool(int(kol_pool_id or 0)),
        bool(_text(platform) and _text(handle)),
    ]
    if sum(1 for item in selectors if item) != 1:
        raise ValueError("provide exactly one KOL selector: --kol-entity-uid, --kol-pool-id, or --platform + --handle")

    kol_rows = _kol_entities()
    pool_map = _pool_by_source_ref()
    pools_by_id = _pool_by_id(pool_map)
    kols_by_ref = _kol_by_source_ref(kol_rows)

    if kol_entity_uid:
        for row in kol_rows:
            if _text(row.get("entity_uid")) == _text(kol_entity_uid):
                source_ref = _text(_entity_payload(row, "identity_json").get("source_ref"))
                return row, pool_map.get(source_ref, {})
        raise ValueError(f"KOL memory entity not found: {kol_entity_uid}")

    if kol_pool_id:
        pool = pools_by_id.get(int(kol_pool_id))
        if not pool:
            raise ValueError(f"KOL pool row not found: {kol_pool_id}")
        kol = kols_by_ref.get(_text(pool.get("source_ref")))
        if not kol:
            raise ValueError(f"KOL memory entity not found for pool id: {kol_pool_id}")
        return kol, pool

    platform_key = _lower(platform)
    handle_key = _lower(handle).lstrip("@")
    for pool in pool_map.values():
        if _lower(pool.get("platform")) == platform_key and _lower(pool.get("handle")).lstrip("@") == handle_key:
            kol = kols_by_ref.get(_text(pool.get("source_ref")))
            if not kol:
                raise ValueError(f"KOL memory entity not found for {platform}:{handle}")
            return kol, pool
    raise ValueError(f"KOL pool row not found for {platform}:{handle}")


def _member_counts(product_to_family: dict[int, dict[str, Any]]) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for row in product_to_family.values():
        counts[int(row["family_id"])] += 1
    return counts


def _family_product_ids(product_to_family: dict[int, dict[str, Any]]) -> dict[int, set[int]]:
    result: dict[int, set[int]] = defaultdict(set)
    for product_id, row in product_to_family.items():
        result[int(row["family_id"])].add(int(product_id))
    return result


def _proved_family_ids(
    links: list[dict[str, Any]],
    product_to_family: dict[int, dict[str, Any]],
) -> set[int]:
    family_ids: set[int] = set()
    for link in links:
        family = product_to_family.get(int(link.get("target_entity_id") or 0))
        if family:
            family_ids.add(int(family["family_id"]))
    return family_ids


def _historical_fit(
    family: dict[str, Any],
    links: list[dict[str, Any]],
    product_to_family: dict[int, dict[str, Any]],
) -> tuple[int, str, dict[str, Any] | None]:
    family_id = int(family["id"])
    target_tokens = _family_tokens(_text(family.get("display_name") or family.get("identity_key")))
    best: tuple[int, str, dict[str, Any] | None] = (0, "no_historical_fit", None)
    for link in links:
        link_family = product_to_family.get(int(link.get("target_entity_id") or 0))
        if not link_family:
            continue
        if int(link_family["family_id"]) == family_id:
            return 25, "direct_family_history", link
        tokens = _family_tokens(_text(link_family.get("family_name") or link_family.get("family_key")))
        same_focal = bool(target_tokens["focal"] and target_tokens["focal"] == tokens["focal"])
        if same_focal and best[0] < 10:
            best = (10, "same_focal_history", link)
    return best


def _adjacent_fit(
    family: dict[str, Any],
    proved_families: list[dict[str, Any]],
) -> tuple[int, str, dict[str, Any] | None]:
    target_tokens = _family_tokens(_text(family.get("display_name") or family.get("identity_key")))
    best: tuple[int, str, dict[str, Any] | None] = (0, "no_adjacent_fit", None)
    for proved in proved_families:
        if int(proved["id"]) == int(family["id"]):
            continue
        tokens = _family_tokens(_text(proved.get("display_name") or proved.get("identity_key")))
        same_focal = bool(target_tokens["focal"] and target_tokens["focal"] == tokens["focal"])
        shared_mount = bool(target_tokens["mount"] and tokens["mount"] and target_tokens["mount"] & tokens["mount"])
        target_has_product_type = bool(target_tokens["focal"] or target_tokens["series"])
        proved_has_product_type = bool(tokens["focal"] or tokens["series"])
        if same_focal:
            return 15, "same_focal_expansion", proved
        if target_has_product_type and proved_has_product_type and best[0] < 10:
            best = (10, "same_product_category_expansion", proved)
        if shared_mount and best[0] < 6:
            best = (6, "same_mount_expansion", proved)
    return best


def _region_relevance(country: str, primary: set[str], secondary: set[str]) -> tuple[int, str]:
    key = _country_key(country)
    if not key:
        return 2, "missing_country"
    if primary and key in primary:
        return 5, "primary_target_market"
    if secondary and key in secondary:
        return 3, "secondary_target_market"
    return 2, "known_other_market"


def _cooperation_depth(count: int) -> int:
    if count >= 10:
        return 15
    if count >= 5:
        return 10
    if count >= 2:
        return 6
    if count == 1:
        return 3
    return 0


def _candidate_product_families() -> list[dict[str, Any]]:
    rows = get_conn().execute(
        """
        SELECT *
        FROM vkpi_memory_entities
        WHERE entity_type='product_family'
          AND status IN ('active', 'imported')
        ORDER BY display_name, id
        """
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _official_family_links() -> dict[int, list[dict[str, Any]]]:
    rows = get_conn().execute(
        """
        SELECT *
        FROM vkpi_memory_links
        WHERE link_type='official_account_published_product'
        ORDER BY observed_at DESC, id DESC
        """
    ).fetchall()
    links: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        row = _row_to_dict(raw)
        links[int(row.get("target_entity_id") or 0)].append(row)
    return links


def _render_family_detail(family: dict[str, Any]) -> str:
    return _text(family.get("display_name") or family.get("identity_key") or family.get("entity_uid"))


def _normalize_product_fit_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _lower(value))


def _load_dimensions11_product_fit(kol_pool_id: int) -> dict[str, dict[str, Any]]:
    if not int(kol_pool_id or 0):
        return {}
    row = get_conn().execute(
        """
        SELECT id, dimensions_11_json
        FROM vkpi_kol_profile_deep
        WHERE kol_pool_id=?
        LIMIT 1
        """,
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        return {}
    profile_id = _safe_int(row["id"])
    payload = _load_json(row["dimensions_11_json"] or "{}", {})
    if not isinstance(payload, dict):
        return {}
    block4 = payload.get("block4_specialty") if isinstance(payload.get("block4_specialty"), dict) else {}
    raw_fit = block4.get("product_fit") if isinstance(block4.get("product_fit"), dict) else {}
    raw_conf = block4.get("product_fit_confidence") if isinstance(block4.get("product_fit_confidence"), dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for sku, score in raw_fit.items():
        sku_text = _text(sku)
        normalized = _normalize_product_fit_key(sku_text)
        if not sku_text or not normalized:
            continue
        numeric_score = max(0.0, min(100.0, _safe_float(score, 0.0)))
        confidence = max(0.0, min(1.0, _safe_float(raw_conf.get(sku_text), 0.0)))
        if numeric_score <= 0 or confidence <= 0:
            continue
        result[sku_text] = {
            "sku": sku_text,
            "normalized": normalized,
            "score": numeric_score,
            "confidence": confidence,
            "profile_deep_id": profile_id,
            "method": _text(payload.get("method")),
            "computed_at": _text(payload.get("computed_at")),
        }
    return result


def _dimensions11_product_fit_for_family(
    family: dict[str, Any],
    dimensions_fit: dict[str, dict[str, Any]],
) -> tuple[float, dict[str, Any] | None]:
    if not dimensions_fit:
        return 0.0, None
    family_name = _render_family_detail(family)
    family_key = _text(family.get("identity_key"))
    family_blob = _normalize_product_fit_key(f"{family_name} {family_key}")
    if not family_blob:
        return 0.0, None
    best: dict[str, Any] | None = None
    best_component = 0.0
    for item in dimensions_fit.values():
        sku_norm = _text(item.get("normalized"))
        if not sku_norm:
            continue
        if sku_norm not in family_blob and family_blob not in sku_norm:
            continue
        score = max(0.0, min(100.0, _safe_float(item.get("score"), 0.0)))
        confidence = max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.0)))
        component = round((score / 100.0) * confidence * 20.0, 1)
        if component > best_component:
            best_component = component
            best = {**item, "match_type": "sku_family_exact", "family_name": family_name}
    return best_component, best


def _deterministic_reason(payload: dict[str, Any], item: dict[str, Any]) -> dict[str, str]:
    pro = item.get("evidence_pro") or []
    con = item.get("evidence_con") or []
    strongest = pro[0].get("detail") if pro else "Has usable Memory evidence for this product family"
    concern = con[0].get("detail") if con else "No major concern in current Memory"
    kol = payload.get("kol") or {}
    handle = kol.get("handle") or kol.get("display_name") or kol.get("kol_entity_uid")
    family = item.get("product_family_name") or item.get("product_family_uid")
    return {
        "short_reason": f"{family} fits {handle} because {strongest}.",
        "pitch_angle": "Frame outreach around the closest historical product-family evidence.",
        "caution_note": concern,
    }


def _reason_prompt(payload: dict[str, Any], item: dict[str, Any]) -> str:
    compact = {
        "scenario": SCENARIO,
        "kol": payload.get("kol"),
        "product_family": {
            "uid": item.get("product_family_uid"),
            "name": item.get("product_family_name"),
            "score": item.get("score"),
            "product_member_count": item.get("product_member_count"),
        },
        "score_breakdown": item.get("score_breakdown"),
        "evidence_pro": [
            {"type": row.get("type"), "detail": row.get("detail"), "source_ref": row.get("source_ref")}
            for row in (item.get("evidence_pro") or [])[:5]
        ],
        "evidence_con": [
            {"type": row.get("type"), "detail": row.get("detail"), "severity": row.get("severity")}
            for row in (item.get("evidence_con") or [])[:5]
        ],
    }
    return (
        "Write a concise V-KPI KOL-to-product recommendation reason as strict JSON with keys "
        "short_reason, pitch_angle, caution_note. Do not invent facts. "
        "Use only the evidence below. No markdown.\n\n"
        + json.dumps(compact, ensure_ascii=False, default=_json_default)
    )


def _parse_reason_text(text: str) -> dict[str, str] | None:
    raw = _text(text)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        "short_reason": _text(parsed.get("short_reason")),
        "pitch_angle": _text(parsed.get("pitch_angle")),
        "caution_note": _text(parsed.get("caution_note")),
    }


def _attach_reason(payload: dict[str, Any], item: dict[str, Any]) -> None:
    provider, model = _reason_model_binding()
    target_label = _text(
        item.get("product_family_name") or item.get("product_family_uid")
    )
    try:
        response = llm_production.generate_json(
            _reason_prompt(payload, item),
            provider=provider,
            model=model,
            purpose="p4_recommendation_reasons",
            max_output_tokens=220,
            cost_tag=REASON_BUDGET_SCOPE,
            triggered_by=REASON_MODEL_TASK,
            required_keys=("short_reason", "pitch_angle", "caution_note"),
            validator=_valid_reason_payload,
            metadata={
                "task_binding": REASON_MODEL_TASK,
                "scenario": SCENARIO,
                "kol_entity_uid": (payload.get("kol") or {}).get("kol_entity_uid"),
                "product_family_uid": item.get("product_family_uid"),
                "rank": item.get("rank"),
                "phase": "kol_recommendation",
                "subphase": "product_fit_reason",
                "attempt_index": 1,
                "total": 1,
                "target_label": target_label,
            },
        )
    except Exception as exc:  # deterministic recommendation remains usable
        response = {"status": "unavailable", "failure": {"code": type(exc).__name__}}
    candidate = response.get("json") if isinstance(response, dict) else None
    exact_response = (
        str(response.get("status") or "") == "success"
        and str(response.get("provider") or "").strip().lower() == provider
        and str(response.get("model") or "").strip().startswith(model)
    )
    if exact_response and _valid_reason_payload(candidate):
        reason = {
            "short_reason": _text(candidate.get("short_reason")),
            "pitch_angle": _text(candidate.get("pitch_angle")),
            "caution_note": _text(candidate.get("caution_note")),
        }
        mode = "llm"
        provenance_provider = provider
        provenance_model = model
        status = "success"
        fallback_reason = ""
    else:
        reason = _deterministic_reason(payload, item)
        mode = "deterministic_fallback"
        provenance_provider = "rule_v0"
        provenance_model = "rule_v0"
        response_status = str(response.get("status") or "unavailable")
        # A deterministic reason is usable UI copy, not a successful LLM result.
        status = "degraded" if response_status == "success" else response_status
        fallback_reason = (
            "exact_model_or_json_contract_mismatch"
            if response_status == "success"
            else _reason_failure_code(response)
        )
    item["recommendation_reason"] = {
        "mode": mode,
        "provider": provenance_provider,
        "model": provenance_model,
        "requested_binding": f"{provider}/{model}",
        "status": status,
        "fallback_reason": fallback_reason,
        **reason,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    kol = payload.get("kol") or {}
    distribution = payload.get("score_distribution") or {}
    lines = [
        "# P4-6 KOL Product Fit Preview",
        "",
        f"**KOL:** {kol.get('platform', '')}:{kol.get('handle', '')}",
        f"**Display name:** {kol.get('display_name', '')}",
        f"**Generated at:** {payload.get('generated_at', '')}",
        f"**Mode:** {payload.get('mode', 'dry_run')}",
        f"**Provider calls allowed:** {str(bool(payload.get('provider_calls_allowed'))).lower()}",
        f"**Budget scope:** {(payload.get('budget_guard') or {}).get('scope', '')}",
        "",
        "## Summary",
        "",
        f"- Total families evaluated: {summary.get('total_families_evaluated', 0)}",
        f"- Returned in JSON: {summary.get('returned', 0)}",
        f"- Displayed in Markdown: {summary.get('markdown_display_count', 0)}",
        f"- Excluded inactive/empty family: {summary.get('excluded_inactive_or_empty_family', 0)}",
        f"- Excluded low evidence: {summary.get('excluded_low_evidence', 0)}",
        f"- 11D product-fit candidates: {summary.get('dimensions11_product_fit_candidates', 0)}",
        f"- 11D product-fit matched families: {summary.get('dimensions11_product_fit_matched', 0)}",
        f"- Top score: {summary.get('top_score', 0)}",
        f"- Median score: {summary.get('median_score', 0)}",
        f"- Recommendation reasons attached: {summary.get('reasons_attached', 0)}",
        "",
        "## Score Distribution",
        "",
        f"- P90+: {distribution.get('p90_plus', 0)} candidates",
        f"- P75-P90: {distribution.get('p75_to_p90', 0)} candidates",
        f"- P50-P75: {distribution.get('p50_to_p75', 0)} candidates",
        f"- Below P50: {distribution.get('below_p50', 0)} candidates",
        "",
        "## Top Product Families",
        "",
    ]
    for idx, item in enumerate(payload.get("markdown_items") or [], start=1):
        breakdown = item.get("score_breakdown") or {}
        lines.extend(
            [
                f"### {idx}. {item.get('product_family_name', '')} (score={item.get('score')}, percentile={item.get('percentile_rank')})",
                "",
                f"**Product members:** {item.get('product_member_count', 0)}",
                "",
                "**Supporting evidence:**",
            ]
        )
        for evidence in item.get("evidence_pro") or []:
            source = f"{evidence.get('source_table', '')}:{evidence.get('source_id', '')}".strip(":")
            lines.append(f"- {evidence.get('type', '')}: {evidence.get('detail', '')} [{source}]")
        lines.extend(["", "**Concerns:**"])
        concerns = item.get("evidence_con") or []
        if concerns:
            for evidence in concerns:
                lines.append(
                    f"- {evidence.get('type', '')}: {evidence.get('detail', '')} "
                    f"(severity={evidence.get('severity', '')})"
                )
        else:
            lines.append("- None")
        reason = item.get("recommendation_reason") or {}
        if reason:
            lines.extend(
                [
                    "",
                    "**Recommendation reason:**",
                    f"- Short reason: {reason.get('short_reason', '')}",
                    f"- Pitch angle: {reason.get('pitch_angle', '')}",
                    f"- Caution note: {reason.get('caution_note', '')}",
                    f"- Reason mode: {reason.get('mode', '')} ({reason.get('provider', '')}/{reason.get('status', '')})",
                ]
            )
        lines.extend(
            [
                "",
                "**Score breakdown:** "
                f"historical={breakdown.get('historical_fit', 0)} "
                f"adjacent={breakdown.get('adjacent_product_fit', 0)} "
                f"dimensions11={breakdown.get('dimensions11_product_fit', 0)} "
                f"cooperation={breakdown.get('cooperation_depth', 0)} "
                f"market={breakdown.get('market_activity', 0)} "
                f"contact={breakdown.get('contact_readiness', 0)} "
                f"region={breakdown.get('region_relevance', 0)} "
                f"quality={breakdown.get('data_quality', 0)} "
                f"-> base={breakdown.get('base', 0)} "
                f"x penalty={breakdown.get('penalty_factor', 1)} "
                f"= {breakdown.get('final', item.get('score'))}",
                "",
                f"[Open in V-KPI: {((item.get('links') or {}).get('open_in_vkpi') or '')}]",
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines)


def format_preview_summary(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    kol = payload.get("kol") or {}
    lines = [
        f"scenario={payload.get('scenario', '')}",
        f"mode={payload.get('mode', '')}",
        f"kol={kol.get('platform', '')}:{kol.get('handle', '')}",
        f"kol_entity_uid={kol.get('kol_entity_uid', '')}",
        f"kol_pool_id={kol.get('kol_pool_id', '')}",
        f"provider_calls_allowed={str(bool(payload.get('provider_calls_allowed'))).lower()}",
        f"budget_scope={(payload.get('budget_guard') or {}).get('scope', '')}",
        f"budget_allowed={str(bool((payload.get('budget_guard') or {}).get('allowed'))).lower()}",
        f"budget_recorded_cost={str(bool((payload.get('budget_guard') or {}).get('recorded_cost'))).lower()}",
        f"persistence_enabled={str(bool((payload.get('persistence') or {}).get('enabled'))).lower()}",
        f"persisted_run_uid={(payload.get('persistence') or {}).get('run_uid', '')}",
        f"persisted_recommendations={(payload.get('persistence') or {}).get('recommendation_count', 0)}",
        f"total_families_evaluated={summary.get('total_families_evaluated', 0)}",
        f"eligible_after_hard_filters={summary.get('eligible_after_hard_filters', 0)}",
        f"returned={summary.get('returned', 0)}",
        f"markdown_display_count={summary.get('markdown_display_count', 0)}",
        f"excluded_inactive_or_empty_family={summary.get('excluded_inactive_or_empty_family', 0)}",
        f"excluded_low_evidence={summary.get('excluded_low_evidence', 0)}",
        f"dimensions11_product_fit_candidates={summary.get('dimensions11_product_fit_candidates', 0)}",
        f"dimensions11_product_fit_matched={summary.get('dimensions11_product_fit_matched', 0)}",
        f"top_score={summary.get('top_score', 0)}",
        f"median_score={summary.get('median_score', 0)}",
        f"llm_reasons_requested={str(bool(summary.get('llm_reasons_requested'))).lower()}",
        f"reasons_attached={summary.get('reasons_attached', 0)}",
    ]
    for idx, item in enumerate((payload.get("items") or [])[:5], start=1):
        lines.append(
            f"sample.{idx}=rank:{item.get('rank')} score:{item.get('score')} "
            f"percentile:{item.get('percentile_rank')} "
            f"{item.get('product_family_name')} "
            f"pro:{len(item.get('evidence_pro') or [])} con:{len(item.get('evidence_con') or [])}"
        )
    return "\n".join(lines)


__all__ = [name for name in globals() if name.startswith("_") or name in {"render_markdown", "format_preview_summary"}]
