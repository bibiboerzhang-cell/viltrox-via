from app.domains.kol import claim_access, claim_lifecycle, claim_listing, claim_lookup, claims, manual_update, profile_detail


def test_claims_facade_exports_domain_use_cases():
    assert claims.assert_kol_access is claim_access.assert_kol_access
    assert claims.claim is claim_lifecycle.claim
    assert claims.release is claim_lifecycle.release
    assert claims.reassign is claim_lifecycle.reassign
    assert claims.list_claims is claim_listing.list_claims
    assert claims.list_kols is claim_listing.list_kols
    assert claims.lookup is claim_lookup.lookup
    assert claims.profile is profile_detail.profile
    assert claims.update_kol_manual is manual_update.update_kol_manual
