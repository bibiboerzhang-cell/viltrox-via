"""Offline contracts for Smart KOL aliases and catalog variant identities."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import product_resolver
from app.domains.kol import product_resolver_catalog
from app.domains.kol import smart_query_planner
from app.domains.kol.search_sessions_attach import _safe_llm_query_plan
from app.domains.products.product_aliases_lens import alias_rows


SEED_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "app"
    / "services"
    / "vkpi"
    / "viltrox_product_catalog_seed.json"
)


def _seed_rows() -> list[dict[str, Any]]:
    rows = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    for row in rows:
        row["specs_json"] = json.dumps(row.get("specs") or {}, ensure_ascii=False)
        row["fit_tags_json"] = json.dumps(row.get("fit_tags") or [], ensure_ascii=False)
    return rows


@pytest.fixture()
def real_seed_catalog(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows = _seed_rows()

    def reader(*, limit: int = 500, query: str = "", **_kwargs: Any) -> dict[str, Any]:
        selected = rows
        if query:
            needle = str(query).strip().lower()
            # Mirror the production SQL contract: specs_json is not part of a
            # filtered lookup, so DC-X2/X3 prove the resolver's variant pass.
            selected = [
                row for row in rows
                if needle in " ".join(
                    str(row.get(key) or "")
                    for key in ("sku", "model_name", "marketing_name", "description")
                ).lower()
            ]
        return {"products": [dict(row) for row in selected[:limit]]}

    monkeypatch.setattr(product_resolver, "list_product_catalog", reader)
    return rows


def test_all_93_reviewed_aliases_are_consumed_against_real_seed(
    real_seed_catalog: list[dict[str, Any]],
) -> None:
    rows = list(alias_rows())
    assert len(rows) == 93

    for alias in rows:
        query = f"给{alias['alias']}找创作者"
        match = product_resolver_catalog.matched_product_alias(query)
        assert match is not None
        assert match["canonical"] == alias["canonical"]
        english_match = product_resolver_catalog.matched_product_alias(
            f"find creators for {alias['alias']}"
        )
        assert english_match is not None
        assert english_match["canonical"] == alias["canonical"]

        result = product_resolver.resolve_product_with_status(query)
        if result["status"] == "resolved":
            assert result["product"]["resolved_canonical"] == alias["canonical"]
            continue
        clarification = product_resolver.unresolved_product_request(query)
        assert result["status"] == "not_found"
        assert clarification is not None
        assert clarification["reason"] == "recognized_product_alias_not_in_catalog"
        assert clarification["requested_canonical"] == alias["canonical"]


@pytest.mark.parametrize(
    ("query", "canonical", "aperture"),
    [
        ("给85/1.4找人像摄影师", "AF 85mm F1.4 Pro", "F1.4"),
        ("给35 1.2找人像摄影师", "AF 35mm F1.2 LAB", "F1.2"),
        ("给135 1.8找赛事摄影师", "AF 135mm F1.8 LAB", "F1.8"),
    ],
)
def test_alias_family_keeps_model_aperture_and_candidate_skus(
    real_seed_catalog: list[dict[str, Any]],
    query: str,
    canonical: str,
    aperture: str,
) -> None:
    resolved = product_resolver.resolve_product(query)

    assert resolved is not None
    assert resolved["sku"] == ""
    assert resolved["model_name"] == canonical
    assert resolved["requested_aperture"] == aperture
    assert resolved["resolution_basis"] == "focal_aperture_family"
    assert len(resolved["focal_family_skus"]) >= 2
    assert resolved["mount"] == ""


@pytest.mark.parametrize(
    ("query", "focal", "aperture"),
    [
        ("给35毫米F1.2找人像摄影师", 35, "F1.2"),
        ("给35焦段1.2光圈找人像摄影师", 35, "F1.2"),
        ("给85毫米F1.4找人像摄影师", 85, "F1.4"),
    ],
)
def test_chinese_focal_and_aperture_prose_keeps_the_full_product_scope(
    real_seed_catalog: list[dict[str, Any]],
    query: str,
    focal: int,
    aperture: str,
) -> None:
    resolved = product_resolver.resolve_product(query)

    assert resolved is not None
    assert resolved["focal_mm"] == focal
    assert resolved["requested_aperture"] == aperture
    assert resolved["resolution_basis"] == "focal_aperture_family"
    assert resolved["sku"] == ""
    assert len(resolved["focal_family_skus"]) >= 2


@pytest.mark.parametrize(
    ("query", "variant"),
    [
        ("DC-X2找监视器评测人", "DC-X2(Only HDMI)"),
        ("DC-X3找监视器评测人", "DC-X3(HDMI+SDI)"),
    ],
)
def test_specs_json_model_variants_resolve_to_real_seed_product(
    real_seed_catalog: list[dict[str, Any]],
    query: str,
    variant: str,
) -> None:
    resolved = product_resolver.resolve_product(query)

    assert resolved is not None
    assert resolved["sku"] == "DC-X-FHD-2000-NITS-6-INCH-CAMERA-MONITOR"
    assert resolved["resolution_kind"] == "catalog_variant_exact"
    assert resolved["resolution_basis"] == "catalog_specs_variant"
    assert resolved["resolved_variant"] == variant


def test_z1_pro_never_downgrades_to_base_z1_in_real_seed(
    real_seed_catalog: list[dict[str, Any]],
) -> None:
    query = "Z1pro找婚礼摄影师"

    assert product_resolver.resolve_product(query) is None
    clarification = product_resolver.unresolved_product_request(query)
    assert clarification is not None
    assert clarification["reason"] == "recognized_product_alias_not_in_catalog"
    assert clarification["requested_canonical"] == "Vintage Z1 Pro"
    assert all("VINTAGE-Z1-RETRO" not in str(item.get("sku")) for item in clarification["suggestions"])


@pytest.mark.parametrize("name", ["Memento", "Maestro"])
def test_named_epic_set_resolves_without_operator_typing_epic(
    real_seed_catalog: list[dict[str, Any]],
    name: str,
) -> None:
    resolved = product_resolver.resolve_product(f"{name}整套找纪录片导演")

    assert resolved is not None
    assert resolved["sku"].startswith(f"EPIC-{name.upper()}-")
    assert resolved["resolution_kind"] == "named_product_family_exact"


@pytest.mark.parametrize(
    "query",
    [
        "Nikon Z1 婚礼摄影师",
        "找 Nikon Z1 Pro 婚礼摄影师",
        "给尼康Z1Pro找婚礼摄影师",
        "Nikon Z6 婚礼摄影师",
        "Nikon Z8 婚礼摄影师",
        "Nikon Z50 婚礼摄影师",
        "找Nikon Z1婚礼摄影师",
        "给尼康Z1找婚礼摄影师",
        "找 Sony A7 Pro 摄影师",
        "找 Canon R5 Pro 摄影师",
        "找 Fuji X-T5 Pro 摄影师",
    ],
)
def test_nikon_camera_body_context_never_routes_through_product_alias(
    real_seed_catalog: list[dict[str, Any]],
    query: str,
) -> None:
    assert product_resolver_catalog.matched_product_alias(query) is None
    assert product_resolver.resolve_product(query) is None
    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    assert plan["status"] != "needs_clarification"
    assert plan.get("resolved_product") is None


@pytest.mark.parametrize(
    "query",
    [
        "比较35 1.2 LAB和85 1.4找摄影师",
        "比较Epic 65 Macro和35 1.2找导演",
    ],
)
def test_multiple_product_aliases_never_silently_choose_the_longest_one(
    real_seed_catalog: list[dict[str, Any]],
    query: str,
) -> None:
    assert product_resolver.resolve_product(query) is None
    clarification = product_resolver.unresolved_product_request(query)
    assert clarification is not None
    assert clarification["reason"] == "multiple_focals_requested"
    assert len(clarification["requested_focals"]) == 2


@pytest.mark.parametrize(
    ("query", "expected_sku"),
    [
        (
            "找使用索尼相机评测DC-X2监视器的人",
            "DC-X-FHD-2000-NITS-6-INCH-CAMERA-MONITOR",
        ),
        ("给Z1闪光灯找富士摄影师", "VINTAGE-Z1-RETRO-ON-CAMERA-FLASH"),
    ],
)
def test_camera_brand_mount_hints_do_not_filter_non_lens_aliases(
    real_seed_catalog: list[dict[str, Any]],
    query: str,
    expected_sku: str,
) -> None:
    resolved = product_resolver.resolve_product(query)

    assert resolved is not None
    assert resolved["sku"] == expected_sku


@pytest.mark.parametrize(
    ("query", "expected_mount"),
    [
        ("给35 1.2 Nikon F mount找摄影师", "F-mount"),
        ("给35 1.2 Sony A mount找摄影师", "A-mount"),
        ("给35 1.2 Fuji GFX mount找摄影师", "G-mount"),
        ("给35 1.2 Panasonic S mount找摄影师", "S-mount"),
    ],
)
def test_explicit_unsupported_mount_wins_over_camera_brand_soft_hint(
    real_seed_catalog: list[dict[str, Any]],
    query: str,
    expected_mount: str,
) -> None:
    assert product_resolver._query_mount(query) == expected_mount
    assert product_resolver.resolve_product(query) is None
    clarification = product_resolver.unresolved_product_request(query)
    assert clarification is not None
    assert clarification["reason"] == "focal_mount_not_in_catalog"
    assert clarification["requested_mount"] == expected_mount


def test_alias_with_explicit_unavailable_mount_fails_closed_to_clarification(
    real_seed_catalog: list[dict[str, Any]],
) -> None:
    query = "给35 1.2富士X卡口找摄影师"

    assert product_resolver.resolve_product(query) is None
    clarification = product_resolver.unresolved_product_request(query)
    assert clarification is not None
    assert clarification["reason"] == "focal_mount_not_in_catalog"
    assert clarification["requested_mount"] == "X-mount"
    assert set(item["mount"] for item in clarification["suggestions"]) == {
        "FE-mount",
        "Z-mount",
    }


@pytest.mark.parametrize("mount_text", ["索尼FE卡口", "尼康Z卡口"])
def test_alias_with_available_mount_resolves_only_that_mount(
    real_seed_catalog: list[dict[str, Any]],
    mount_text: str,
) -> None:
    resolved = product_resolver.resolve_product(f"给35 1.2{mount_text}找摄影师")

    assert resolved is not None
    assert resolved["sku"]
    assert resolved["mount"] in {"FE-mount", "Z-mount"}
    assert resolved["mount"] == ("FE-mount" if "FE" in mount_text else "Z-mount")


def test_catalog_failure_is_explicit_and_does_not_ask_for_another_sku(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_reader(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("offline catalog")

    monkeypatch.setattr(product_resolver, "list_product_catalog", failed_reader)
    monkeypatch.setattr(
        smart_query_planner.llm_gateway,
        "invoke_json",
        lambda *_a, **_k: pytest.fail("catalog guard must stop before provider"),
    )

    status = product_resolver.resolve_product_with_status("给35mm F1.2找人像摄影师")
    plan = smart_query_planner.plan_text_query_provider_free(
        "给35mm F1.2找人像摄影师",
        body={},
    )
    llm_path_plan = smart_query_planner._plan_text_query_impl(
        "给35mm F1.2找人像摄影师",
        body={},
    )

    assert status == {
        "status": "catalog_unavailable",
        "catalog_status": "unavailable",
        "product": None,
    }
    assert plan["status"] == "needs_clarification"
    assert plan["reason"] == "product_catalog_unavailable"
    assert plan["catalog_status"] == "unavailable"
    assert plan["clarification"]["catalog_status"] == "unavailable"
    assert plan["clarification"]["retryable"] is True
    assert "无需修改" in plan["clarification"]["message"]
    assert llm_path_plan["reason"] == "product_catalog_unavailable"
    assert llm_path_plan["catalog_status"] == "unavailable"

    generic_status = product_resolver.resolve_product_with_status("找婚礼摄影师")
    generic_plan = smart_query_planner.plan_text_query_provider_free(
        "找婚礼摄影师",
        body={},
    )
    assert generic_status["status"] == "not_found"
    assert generic_plan["status"] != "needs_clarification"
    assert generic_plan.get("reason") != "product_catalog_unavailable"


def test_planner_preserves_safe_family_resolution_metadata(
    real_seed_catalog: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smart_query_planner, "_plan_from_product_persona", lambda *_a, **_k: None)

    plan = smart_query_planner.plan_text_query_provider_free(
        "给85/1.4找人像摄影师",
        body={"platforms": ["youtube"]},
    )
    product = plan["resolved_product"]

    assert product["resolution_kind"] == "focal_family"
    assert product["resolution_basis"] == "focal_aperture_family"
    assert product["requested_aperture"] == "F1.4"
    assert product["focal_mm"] == 85
    assert product["focal_family_size"] >= 2
    assert set(product["focal_family_mounts"]) >= {"FE-mount", "Z-mount"}
    assert len(product["focal_family_skus"]) >= 2
    replayed = _safe_llm_query_plan(plan)["resolved_product"]
    assert replayed["resolution_basis"] == "focal_aperture_family"
    assert replayed["requested_aperture"] == "F1.4"
    assert replayed["focal_family_mounts"] == product["focal_family_mounts"]
    assert replayed["focal_family_skus"] == product["focal_family_skus"]

    model_plan = smart_query_planner.plan_text_query_provider_free(
        "2X增距镜找野生动物摄影师",
        body={},
    )
    assert model_plan["resolved_product"]["resolution_kind"] == "model_family"
    assert len(model_plan["resolved_product"]["model_family_skus"]) == 2
    assert model_plan["resolved_product"]["model_family_size"] == 2

    product_plan = smart_query_planner.plan_text_query_provider_free(
        "EPIC整套找纪录片导演",
        body={},
    )
    assert product_plan["resolved_product"]["resolution_kind"] == "named_product_family"
    assert product_plan["resolved_product"]["product_family_size"] >= 2
    assert len(product_plan["resolved_product"]["product_family_skus"]) >= 2
