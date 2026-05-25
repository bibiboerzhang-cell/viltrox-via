from app.domains.kol import claims as claims_domain


def test_kol_claims_domain_wraps_list_and_profile(monkeypatch):
    monkeypatch.setattr(
        claims_domain.kol_claims,
        "list_kols",
        lambda *, search, platform, staff_id, limit, staff: {
            "search": search,
            "platform": platform,
            "staff_id": staff_id,
            "limit": limit,
            "staff": staff,
        },
    )
    monkeypatch.setattr(claims_domain.kol_claims, "profile", lambda kol_id, *, staff: {"kol_id": kol_id, "staff": staff})

    assert claims_domain.list_kols(search="lens", platform="youtube", staff_id=3, limit=10, staff={"id": 1}) == {
        "search": "lens",
        "platform": "youtube",
        "staff_id": 3,
        "limit": 10,
        "staff": {"id": 1},
    }
    assert claims_domain.profile(7, staff={"id": 2}) == {"kol_id": 7, "staff": {"id": 2}}


def test_kol_claims_domain_wraps_claim_actions(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(claims_domain.kol_claims, "claim", lambda kol_id, body, *, staff: ("claim", kol_id, body, staff))
    monkeypatch.setattr(claims_domain.kol_claims, "release", lambda claim_id, body, *, staff: ("release", claim_id, body, staff))
    monkeypatch.setattr(claims_domain.kol_claims, "reassign", lambda claim_id, body, *, staff: ("reassign", claim_id, body, staff))
    monkeypatch.setattr(
        claims_domain.kol_claims,
        "assert_kol_access",
        lambda kol_id, staff, *, allow_unclaimed=False: calls.setdefault("access", (kol_id, staff, allow_unclaimed)),
    )

    assert claims_domain.claim(9, {"x": 1}, staff={"id": 4}) == ("claim", 9, {"x": 1}, {"id": 4})
    assert claims_domain.release(11, {"reason": "done"}, staff={"id": 5}) == ("release", 11, {"reason": "done"}, {"id": 5})
    assert claims_domain.reassign(12, {"staff_id": 6}, staff={"id": 1}) == ("reassign", 12, {"staff_id": 6}, {"id": 1})
    claims_domain.assert_kol_access(13, {"id": 7}, allow_unclaimed=True)
    assert calls["access"] == (13, {"id": 7}, True)
