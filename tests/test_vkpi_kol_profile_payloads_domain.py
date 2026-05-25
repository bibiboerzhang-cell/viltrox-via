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


def test_profile_payload_request_wrappers_preserve_access_and_pool_fallback(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        profile_payloads.claims_domain,
        "assert_kol_access",
        lambda kol_id, staff, *, allow_unclaimed=False: calls.setdefault("assessment_access", (kol_id, staff, allow_unclaimed)),
    )
    monkeypatch.setattr(profile_payloads, "_assessment_payload", lambda kol_id: {"assessment": kol_id})

    assert profile_payloads.assessment_for_request(8, staff={"id": 1}) == {"assessment": 8}
    assert calls["assessment_access"] == (8, {"id": 1}, True)

    def missing_kol(*_args, **_kwargs):
        raise LookupError("not in main kols")

    monkeypatch.setattr(profile_payloads.claims_domain, "assert_kol_access", missing_kol)
    monkeypatch.setattr(
        profile_payloads,
        "_product_fit_preview_payload_for_pool",
        lambda kol_pool_id, limit: {"kol_pool_id": kol_pool_id, "limit": limit},
    )

    assert profile_payloads.product_fit_for_request(99, limit=3, staff={"id": 1}) == {"kol_pool_id": 99, "limit": 3}
