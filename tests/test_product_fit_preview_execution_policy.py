from __future__ import annotations

from typing import Any

from app.domains.kol import product_fit


def test_product_fit_execution_policy_is_bounded_and_truthful() -> None:
    assert product_fit._preview_execution_policy(
        with_llm_reasons=False,
        reason_limit=10,
        returned_count=7,
    ) == {
        "mode": "dry_run",
        "provider_calls_allowed": False,
        "provider_calls_planned": 0,
        "provider_call_scope": "none",
        "deterministic_ranking": True,
        "business_actions_executed": False,
    }

    assert product_fit._preview_execution_policy(
        with_llm_reasons=True,
        reason_limit=3,
        returned_count=7,
    ) == {
        "mode": "ai_enriched_preview",
        "provider_calls_allowed": True,
        "provider_calls_planned": 3,
        "provider_call_scope": "recommendation_reason_only",
        "deterministic_ranking": True,
        "business_actions_executed": False,
    }

    empty = product_fit._preview_execution_policy(
        with_llm_reasons=True,
        reason_limit=3,
        returned_count=0,
    )
    assert empty["mode"] == "dry_run"
    assert empty["provider_calls_allowed"] is False
    assert empty["provider_calls_planned"] == 0


def _stub_offline_preview(monkeypatch, *, include_candidate: bool) -> list[dict[str, Any]]:
    family = {
        "id": 11,
        "entity_uid": "product-family-11",
        "identity_key": "prime-lenses",
        "display_name": "Prime lenses",
    }
    product_to_family = {101: {"family_id": 11}}
    monkeypatch.setattr(
        product_fit.memory,
        "readiness",
        lambda: {"status": "ready_for_p4_dry_run", "provider_calls_allowed": False},
    )
    monkeypatch.setattr(product_fit, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        product_fit,
        "get_budget_status",
        lambda *_args, **_kwargs: {"configured": True},
    )
    monkeypatch.setattr(
        product_fit,
        "_resolve_kol",
        lambda **_kwargs: (
            {
                "id": 7,
                "entity_uid": "kol-7",
                "identity_json": "{}",
                "metadata_json": "{}",
                "status": "active",
            },
            {},
        ),
    )
    monkeypatch.setattr(product_fit, "_legacy_entities_by_uid", lambda: {})
    monkeypatch.setattr(product_fit, "_kol_facts", lambda: {})
    monkeypatch.setattr(product_fit, "_worked_links", lambda: {})
    monkeypatch.setattr(
        product_fit,
        "_product_family_maps",
        lambda: (product_to_family, {11: family}),
    )
    monkeypatch.setattr(product_fit, "_official_family_links", lambda: {})
    monkeypatch.setattr(product_fit, "_load_dimensions11_product_fit", lambda _pool_id: [])
    monkeypatch.setattr(product_fit, "_target_market_signals", lambda _family_id: [])
    monkeypatch.setattr(product_fit, "_catalog_products_for_match", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        product_fit,
        "_candidate_product_families",
        lambda: [family] if include_candidate else [],
    )
    reason_calls: list[dict[str, Any]] = []

    def attach_reason(payload: dict[str, Any], item: dict[str, Any]) -> None:
        reason_calls.append({"payload": payload, "item": item})
        item["recommendation_reason"] = {
            "mode": "offline_test_stub",
            "provider": "none",
            "model": "none",
        }

    monkeypatch.setattr(product_fit, "_attach_reason", attach_reason)
    return reason_calls


def test_product_fit_preview_reports_actual_reason_plan_without_provider_or_db(monkeypatch) -> None:
    reason_calls = _stub_offline_preview(monkeypatch, include_candidate=True)

    payload = product_fit.build_kol_product_fit_preview(
        kol_entity_uid="kol-7",
        include_low_evidence=True,
        with_llm_reasons=True,
        reason_limit=4,
    )

    assert len(payload["items"]) == 1
    assert payload["mode"] == "ai_enriched_preview"
    assert payload["provider_calls_allowed"] is True
    assert payload["execution_policy"] == {
        "mode": "ai_enriched_preview",
        "provider_calls_allowed": True,
        "provider_calls_planned": 1,
        "provider_call_scope": "recommendation_reason_only",
        "deterministic_ranking": True,
        "business_actions_executed": False,
    }
    assert payload["summary"]["llm_reason_calls_planned"] == 1
    assert payload["summary"]["reasons_attached"] == 1
    assert payload["budget_guard"]["llm_reason_calls_planned"] == 1
    assert payload["budget_guard"]["llm_reason_atomic_reservation_per_call"] is True
    assert len(reason_calls) == 1


def test_product_fit_preview_with_no_candidates_stays_dry_run(monkeypatch) -> None:
    reason_calls = _stub_offline_preview(monkeypatch, include_candidate=False)

    payload = product_fit.build_kol_product_fit_preview(
        kol_entity_uid="kol-7",
        with_llm_reasons=True,
        reason_limit=4,
    )

    assert payload["items"] == []
    assert payload["mode"] == "dry_run"
    assert payload["provider_calls_allowed"] is False
    assert payload["execution_policy"]["provider_calls_planned"] == 0
    assert payload["execution_policy"]["provider_call_scope"] == "none"
    assert payload["summary"]["reasons_attached"] == 0
    assert reason_calls == []
