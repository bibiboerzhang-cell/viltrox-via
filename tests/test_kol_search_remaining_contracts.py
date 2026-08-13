from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_search
from app.domains.kol import product_resolver
from app.domains.kol import profile_discovery_candidates
from app.domains.kol import profile_discovery_pipeline
from app.domains.kol import profile_recall_match_evidence as match_evidence
from app.domains.kol import search_sessions, search_sessions_attach
from app.domains.kol import smart_query_planner


def test_measurement_inches_do_not_lock_search_to_instagram() -> None:
    assert profile_discovery_candidates.explicit_platforms_from_query(
        "5.5 ins field monitor creator"
    ) == []


@pytest.mark.parametrize("query", ["Instagram field monitor creator", "IG field monitor creator"])
def test_explicit_instagram_names_still_lock_search_to_instagram(query: str) -> None:
    assert profile_discovery_candidates.explicit_platforms_from_query(query) == ["instagram"]


def test_los_angeles_state_abbreviation_is_not_a_canada_constraint() -> None:
    assert profile_discovery_candidates.explicit_market_constraint(
        "Los Angeles CA wedding filmmakers",
        "Canada",
    ) == ""


@pytest.mark.parametrize("query", ["wedding filmmakers in CA", "country:CA wedding filmmakers"])
def test_contextual_ca_remains_an_explicit_canada_constraint(query: str) -> None:
    assert profile_discovery_candidates.explicit_market_constraint(query, "US") == "ca"


def test_pl_mount_is_product_syntax_not_a_poland_market_constraint() -> None:
    assert profile_discovery_candidates.explicit_market_constraint(
        "EPIC 65mm PL mount cinematographers",
        None,
    ) == ""


def test_in_pl_is_an_explicit_poland_market_constraint() -> None:
    assert profile_discovery_candidates.explicit_market_constraint(
        "cinematographers in PL",
        None,
    ) == "pl"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Mexico", "mx"),
        ("MX", "mx"),
        ("Brazil", "br"),
        ("BR", "br"),
        ("ES", "es"),
        ("IT", "it"),
        ("RU", "ru"),
        ("TH", "th"),
        ("VN", "vn"),
        ("ID", "id"),
    ],
)
def test_structured_market_allowlist_normalizes_supported_countries(raw: str, expected: str) -> None:
    assert profile_discovery_candidates.normalize_market_constraint(raw) == expected


@pytest.mark.parametrize("raw", ["Atlantis", "ZZ", "North America", ""])
def test_unknown_structured_markets_fail_closed_without_guessing(raw: str) -> None:
    assert profile_discovery_candidates.normalize_market_constraint(raw) == ""


def test_unknown_structured_market_never_widens_to_unfiltered_candidates() -> None:
    candidate = {
        "kol_pool_id": 77,
        "candidate_facets": {
            "platform": "youtube",
            "country": "us",
            "language": "en",
            "profile_type": "creator",
            "contact_available": "unknown",
            "video_evidence": "no",
        },
    }
    filtered = profile_discovery_candidates.filter_recall_result_market(
        {
            "match_status": "matched",
            "items": [candidate],
            "buckets": {"creator": [candidate], "reviewer": []},
            "diagnostics": {"returned_count": 1, "evidence_gate_enabled": True},
        },
        "Atlantis",
    )

    assert filtered["items"] == []
    assert filtered["buckets"] == {"creator": [], "reviewer": []}
    assert filtered["match_status"] == "empty"
    assert filtered["diagnostics"]["empty_reason"] == "invalid_market_constraint"


def test_multiple_operator_markets_are_rejected_instead_of_becoming_unfiltered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "search_query": "wedding filmmakers",
            "product_focus": ["wedding", "filmmaker"],
            "target_persona": "Wedding filmmakers",
            "market": "US",
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: {
            "match_status": "matched",
            "items": [{"kol_pool_id": 88, "platform": "youtube"}],
            "buckets": {
                "creator": [{"kol_pool_id": 88, "platform": "youtube"}],
                "reviewer": [],
            },
            "diagnostics": {"returned_count": 1, "evidence_gate_enabled": True},
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_search_sessions,
        "ensure_session_for_result",
        lambda **_kwargs: touched.append("session"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            vkpi_kol_pool_search.smart_kol_search(
                {"input": "wedding filmmakers in US and UK"},
                staff={"id": 42},
            )
        )

    assert exc_info.value.status_code == 400
    assert touched == []


def _dc_550_candidate() -> dict[str, Any]:
    return {
        "kol_pool_id": 550,
        "handle": "wedding-pro",
        "display_name": "Wedding Pro",
        "bio": "Professional wedding filmmaker and camera operator",
        "primary_topic": "wedding filmmaking",
        "content_style": "professional wedding films",
        "profile_text": "Professional wedding filmmaker",
        "type_reason": "",
    }


def test_resolved_dc_550_requires_product_evidence_not_only_pro_wedding_persona() -> None:
    row = _dc_550_candidate()

    assert match_evidence.build_match_evidence(
        row,
        {},
        "professional wedding videographer",
        required_product_terms=["DC-550"],
    ) == []


def test_resolved_dc_550_accepts_representative_work_with_model_evidence() -> None:
    evidence = match_evidence.build_match_evidence(
        _dc_550_candidate(),
        {
            "representative_evidence": [
                {"title": "Viltrox DC-550 field monitor setup for a wedding film"},
            ],
        },
        "professional wedding videographer",
        required_product_terms=["DC-550"],
    )

    assert evidence
    assert {
        (item["field"], item["term"])
        for item in evidence
    } >= {
        ("primary_topic", "wedding"),
        ("representative_evidence.title", "dc-550"),
    }


@pytest.mark.parametrize(
    ("product", "query", "profile_text", "attribute_only_title", "identity_title", "identity_terms"),
    [
        (
            {
                "sku": "EPIC-65-MACRO-PL",
                "model_name": "EPIC 65mm T2.8 Macro 1.33x",
                "marketing_name": "EPIC 65mm Macro Anamorphic",
                "series": "EPIC",
            },
            "commercial cinematographer",
            "Commercial cinematographer",
            "65mm macro anamorphic lens test",
            "EPIC 65mm macro anamorphic lens test",
            {"epic", "65mm"},
        ),
        (
            {
                "sku": "DC-550",
                "model_name": "DC-550 Pro",
                "marketing_name": "DC-550 5.5-inch Camera Monitor",
                "series": "DC",
            },
            "wedding filmmaker",
            "Wedding filmmaker",
            "5.5-inch camera monitor workflow",
            "DC-550 camera monitor workflow",
            {"dc-550"},
        ),
    ],
)
def test_product_attributes_cannot_replace_resolved_product_identity(
    product: dict[str, Any],
    query: str,
    profile_text: str,
    attribute_only_title: str,
    identity_title: str,
    identity_terms: set[str],
) -> None:
    row = {
        "handle": "working-creator",
        "display_name": "Working Creator",
        "bio": profile_text,
        "primary_topic": profile_text,
        "content_style": profile_text,
        "profile_text": profile_text,
        "type_reason": "",
    }
    product_terms = match_evidence.product_evidence_terms(product)

    attribute_only = match_evidence.build_match_evidence(
        row,
        {"representative_evidence": [{"title": attribute_only_title}]},
        query,
        required_product_terms=product_terms,
    )
    identity_proven = match_evidence.build_match_evidence(
        row,
        {"representative_evidence": [{"title": identity_title}]},
        query,
        required_product_terms=product_terms,
    )

    assert attribute_only == []
    assert identity_proven
    assert identity_terms <= {item["term"] for item in identity_proven}


def test_smart_preview_passes_resolved_product_anchor_to_the_evidence_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_product = {
        "sku": "DC-550",
        "model_name": "DC-550 Pro",
        "marketing_name": "DC-550 5.5-inch Camera Monitor",
        "category_main": "Monitor",
        "series": "DC",
    }
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "search_query": "professional wedding videographer",
            "product_focus": ["professional", "wedding", "videographer"],
            "target_persona": "Professional wedding filmmakers",
            "resolved_product": resolved_product,
        },
    )

    def capture_recall(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "match_status": "empty",
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {
                "returned_count": 0,
                "evidence_gate_enabled": True,
                "empty_reason": "no_evidence_match",
            },
        }

    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        capture_recall,
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {"input": "DC-550 professional wedding creators", "create_session": False},
            staff={"id": 42},
        )
    )

    anchors = match_evidence.product_evidence_terms(
        captured["required_product_evidence_terms"]
    )
    assert "dc-550" in anchors


def test_worker_passes_resolved_product_anchor_to_the_evidence_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_product = {
        "sku": "DC-550",
        "model_name": "DC-550 Pro",
        "marketing_name": "DC-550 5.5-inch Camera Monitor",
        "category_main": "Monitor",
        "series": "DC",
    }
    captured: dict[str, Any] = {}

    class _StopAfterRecall(RuntimeError):
        pass

    def capture_recall(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        raise _StopAfterRecall

    monkeypatch.setattr(
        profile_discovery_pipeline.profile_recall,
        "recall_kol_profiles",
        capture_recall,
    )

    with pytest.raises(_StopAfterRecall):
        asyncio.run(
            profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
                session_id=809,
                payload={
                    "query_text": "professional wedding videographer",
                    "_worker_planned": True,
                    "resolved_product": resolved_product,
                    "include_new_discovery": False,
                },
            )
        )

    anchors = match_evidence.product_evidence_terms(
        captured["required_product_evidence_terms"]
    )
    assert "dc-550" in anchors


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("EPIC 65mm PL mount cinematographers", "PL-mount"),
        ("85mm RF mount portrait lens", "RF-mount"),
        ("Canon 85mm portrait lens", ""),
    ],
)
def test_product_mount_parser_requires_an_explicit_mount_standard(
    query: str,
    expected: str,
) -> None:
    assert product_resolver._query_mount(query) == expected


def _mount_variant(
    sku: str,
    *,
    model_name: str,
    series: str,
    mount: str,
) -> dict[str, Any]:
    return {
        "sku": sku,
        "model_name": model_name,
        "marketing_name": model_name,
        "series": series,
        "category_main": "Lens",
        "category_detail": "Prime Lens",
        "mount": mount,
    }


def test_explicit_pl_mount_resolves_only_the_pl_catalog_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pl = _mount_variant(
        "EPIC-65-PL",
        model_name="EPIC 65mm Macro",
        series="EPIC",
        mount="PL-mount",
    )
    l_mount = _mount_variant(
        "EPIC-65-L",
        model_name="EPIC 65mm Macro",
        series="EPIC",
        mount="L-mount",
    )
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [pl, l_mount]},
    )

    resolved = product_resolver.resolve_product("EPIC 65mm PL mount cinematographers")

    assert resolved is not None
    assert resolved["sku"] == "EPIC-65-PL"


def test_explicit_rf_mount_resolves_only_the_rf_catalog_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rf = _mount_variant(
        "AF-85-A",
        model_name="Viltrox AF 85mm F1.8",
        series="Pro",
        mount="RF-mount",
    )
    ef = _mount_variant(
        "AF-85-B",
        model_name="Viltrox AF 85mm F1.8",
        series="Pro",
        mount="EF-mount",
    )
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [rf, ef]},
    )

    resolved = product_resolver.resolve_product("Viltrox AF 85mm F1.8 RF mount lens")

    assert resolved is not None
    assert resolved["sku"] == "AF-85-A"


def test_canon_brand_without_mount_does_not_guess_ef_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ef = _mount_variant(
        "AF-85-A",
        model_name="Viltrox AF 85mm F1.8",
        series="Pro",
        mount="EF-mount",
    )
    rf = _mount_variant(
        "AF-85-B",
        model_name="Viltrox AF 85mm F1.8",
        series="Pro",
        mount="RF-mount",
    )
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [ef, rf]},
    )

    assert product_resolver.resolve_product(
        "Viltrox AF 85mm F1.8 Pro Canon portrait lens"
    ) is None


def test_recall_attachment_saves_llm_query_plan_in_result_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    plan = {
        "status": "ready",
        "search_query": "professional wedding videographer",
        "target_persona": "Professional wedding filmmakers",
        "resolved_product": {"sku": "DC-550", "model_name": "DC-550 Pro"},
        "provider_calls_performed": False,
    }

    def record_items(
        session_id: int,
        items: list[dict[str, Any]],
        *,
        status: str,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        captured.update(
            session_id=session_id,
            items=items,
            status=status,
            summary=summary,
        )
        return {"id": session_id, "items": items, "status": status}

    monkeypatch.setattr(search_sessions, "record_items", record_items)

    search_sessions_attach.attach_recall_result(
        808,
        {
            "match_status": "empty",
            "query": {"query_text": "professional wedding videographer"},
            "llm_query_plan": plan,
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0, "evidence_gate_enabled": True},
        },
    )

    assert captured["summary"]["llm_query_plan"] == plan


def _explicit_product() -> dict[str, Any]:
    return {
        "sku": "DC-550",
        "model_name": "DC-550 Pro",
        "marketing_name": "DC-550 5.5-inch Camera Monitor",
        "category_main": "Monitor",
        "series": "DC",
    }


def test_explicit_product_sku_drives_provider_free_plan_for_generic_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(product_resolver, "resolve_product", lambda _query: None)
    monkeypatch.setattr(product_resolver, "resolve_product_sku", lambda sku: _explicit_product() if sku == "DC-550" else None)
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_args, **_kwargs: None)

    plan = smart_query_planner.plan_text_query_provider_free(
        "wedding filmmakers",
        body={"product_sku": "DC-550"},
    )

    assert plan["status"] != "needs_clarification"
    assert plan["resolved_product"]["sku"] == "DC-550"


def test_unknown_or_conflicting_explicit_product_sku_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(product_resolver, "resolve_product_sku", lambda sku: _explicit_product() if sku == "DC-550" else None)
    monkeypatch.setattr(product_resolver, "unresolved_product_request", lambda _query: None)

    monkeypatch.setattr(product_resolver, "resolve_product", lambda _query: None)
    unknown = smart_query_planner.plan_text_query_provider_free(
        "wedding filmmakers",
        body={"product_sku": "NOT-A-SKU"},
    )
    assert unknown["status"] == "needs_clarification"
    assert unknown["reason"] == "explicit_product_sku_not_in_catalog"

    monkeypatch.setattr(product_resolver, "resolve_product", lambda _query: {"sku": "EPIC-65-MACRO-PL"})
    conflict = smart_query_planner.plan_text_query_provider_free(
        "EPIC 65mm filmmakers",
        body={"product_sku": "DC-550"},
    )
    assert conflict["status"] == "needs_clarification"
    assert conflict["reason"] == "conflicting_product_constraints"


def test_smart_preview_uses_the_explicit_sku_as_required_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    product = _explicit_product()
    monkeypatch.setattr(product_resolver, "resolve_product", lambda _query: None)
    monkeypatch.setattr(product_resolver, "resolve_product_sku", lambda sku: product if sku == "DC-550" else None)
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_args, **_kwargs: None)

    def capture_recall(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "match_status": "empty",
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0, "evidence_gate_enabled": True},
        }

    monkeypatch.setattr(vkpi_kol_pool_search.kol_profile_recall, "recall_kol_profiles", capture_recall)
    monkeypatch.setattr(vkpi_kol_pool_search, "_attach_smart_recall_session", lambda **kwargs: kwargs["result"])

    result = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {
                "input": "wedding filmmakers",
                "product_sku": "DC-550",
                "create_session": False,
            },
            staff={"id": 42},
        )
    )

    assert result["llm_query_plan"]["resolved_product"]["sku"] == "DC-550"
    assert "dc-550" in match_evidence.product_evidence_terms(captured["required_product_evidence_terms"])
