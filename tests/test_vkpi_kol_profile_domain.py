from app.domains.kol import profile as profile_domain


def test_kol_profile_domain_attaches_dossier(monkeypatch):
    monkeypatch.setattr(profile_domain.claims_domain, "profile", lambda kol_id, *, staff: {"kol_id": kol_id, "staff": staff})
    monkeypatch.setattr(profile_domain.account_domain, "get_dossier", lambda kol_id: {"dossier_kol_id": kol_id})

    assert profile_domain.profile_with_dossier(7, staff={"id": 1}) == {
        "kol_id": 7,
        "staff": {"id": 1},
        "dossier": {"dossier_kol_id": 7},
        "contacts": {
            "profile_url": "",
            "contact_masked": True,
            "contact_projection_reason": "summary_only",
        },
        "contact_masked": True,
        "contact_projection_reason": "summary_only",
    }


def test_kol_profile_domain_keeps_empty_dossier_on_best_effort_failure(monkeypatch):
    monkeypatch.setattr(profile_domain.claims_domain, "profile", lambda kol_id, *, staff: {"kol_id": kol_id})

    def fail_dossier(_kol_id):
        raise RuntimeError("dossier unavailable")

    monkeypatch.setattr(profile_domain.account_domain, "get_dossier", fail_dossier)

    assert profile_domain.profile_with_dossier(7, staff={"id": 1}) == {
        "kol_id": 7,
        "dossier": {},
        "contacts": {
            "profile_url": "",
            "contact_masked": True,
            "contact_projection_reason": "summary_only",
        },
        "contact_masked": True,
        "contact_projection_reason": "summary_only",
    }
