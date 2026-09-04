from __future__ import annotations

from app.domains.kol.search_sessions_attach import _safe_llm_query_plan


def test_family_resolution_survives_session_projection_without_exact_sku() -> None:
    projected = _safe_llm_query_plan(
        {
            "status": "ready",
            "search_query": "portrait wedding photographer",
            "resolved_product": {
                "sku": "",
                "model_name": "Viltrox 35mm F1.2 LAB",
                "marketing_name": "Viltrox 35mm F1.2 LAB",
                "category_main": "Lens",
                "category_detail": "Auto Focus Lens",
                "series": "LAB",
                "resolution_kind": "focal_family",
                "resolution_basis": "focal_aperture_family",
                "alias_resolution_basis": "reviewed_alias_table",
                "resolved_alias": "35 1.2",
                "resolved_canonical": "AF 35mm F1.2 LAB",
                "requested_aperture": "F1.2",
                "focal_mm": 35,
                "focal_family_size": 2,
                "focal_family_mounts": ["FE-mount", "Z-mount"],
                "focal_family_skus": ["AF-35MM-F12-LAB-FE", "AF-35MM-F12-LAB-Z"],
            },
        }
    )

    product = projected["resolved_product"]
    assert product.get("sku", "") == ""
    assert product["resolution_basis"] == "focal_aperture_family"
    assert product["alias_resolution_basis"] == "reviewed_alias_table"
    assert product["resolved_alias"] == "35 1.2"
    assert product["resolved_canonical"] == "AF 35mm F1.2 LAB"
    assert product["requested_aperture"] == "F1.2"
    assert product["focal_mm"] == 35
    assert product["focal_family_size"] == 2
    assert product["focal_family_mounts"] == ["FE-mount", "Z-mount"]
    assert product["focal_family_skus"] == [
        "AF-35MM-F12-LAB-FE",
        "AF-35MM-F12-LAB-Z",
    ]


def test_product_resolution_projection_is_bounded_and_drops_unsafe_values() -> None:
    projected = _safe_llm_query_plan(
        {
            "status": "ready",
            "search_query": "cinema creator",
            "resolved_product": {
                "model_name": "Viltrox EPIC family",
                "resolution_kind": "named_product_family",
                "product_family_size": 9000,
                "product_family_skus": [
                    "EPIC-25",
                    "https://private.example/token?secret=1",
                    *[f"EPIC-{index}" for index in range(30)],
                ],
            },
        }
    )

    product = projected["resolved_product"]
    assert "product_family_size" not in product
    assert len(product["product_family_skus"]) == 12
    assert all(not value.startswith("http") for value in product["product_family_skus"])


def test_catalog_clarification_metadata_survives_session_projection() -> None:
    projected = _safe_llm_query_plan(
        {
            "status": "needs_clarification",
            "reason": "product_catalog_unavailable",
            "catalog_status": "unavailable",
            "clarification": {
                "reason": "recognized_product_alias_not_in_catalog",
                "catalog_status": "unavailable",
                "retryable": True,
                "requested_alias": "Z1 Pro",
                "requested_canonical": "Vintage Z1 Pro",
                "message": "产品目录暂时不可用，请稍后重试。",
            },
        }
    )

    assert projected["catalog_status"] == "unavailable"
    assert projected["clarification"] == {
        "reason": "recognized_product_alias_not_in_catalog",
        "requested_alias": "Z1 Pro",
        "requested_canonical": "Vintage Z1 Pro",
        "message": "产品目录暂时不可用，请稍后重试。",
        "catalog_status": "unavailable",
        "retryable": True,
    }
