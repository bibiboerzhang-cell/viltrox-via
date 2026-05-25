from app.domains.kol import profile_payloads


def test_profile_payload_helpers_normalize_handles_and_dimensions():
    assert profile_payloads._normalize_handle_for_match("https://www.youtube.com/@Creator?x=1") == "creator"
    assert profile_payloads._normalize_handle_for_match("instagram.com/lens.creator/") == "lens.creator"

    dim = profile_payloads._dimension(120, "source", "reason")
    assert dim == {"score": 100, "source": "source", "reason": "reason", "status": "ready"}


def test_dimensions11_product_fit_items_filters_low_confidence():
    profile_deep = {
        "profile_deep_id": 9,
        "kol_pool_id": 12,
        "dimensions_11_json": {
            "method": "rule_dimensions_11_v0",
            "block4_specialty": {
                "product_fit": {"AF 35mm F1.2": 86, "bad": 0, "low confidence": 70},
                "product_fit_confidence": {"AF 35mm F1.2": 0.74, "low confidence": 0.2},
            },
        },
    }

    items = profile_payloads._dimensions11_product_fit_items(profile_deep)

    assert len(items) == 1
    assert items[0]["sku"] == "AF 35mm F1.2"
    assert items[0]["score"] == 86
    assert items[0]["confidence"] == 0.74
