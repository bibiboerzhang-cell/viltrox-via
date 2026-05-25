from app.domains.kol import contacts as contacts_domain


def test_kol_contacts_domain_adds_new_contact(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(contacts_domain.claims_domain, "assert_kol_access", lambda *args, **kwargs: calls.setdefault("access", (args, kwargs)))
    monkeypatch.setattr(
        contacts_domain,
        "_latest_kol_context",
        lambda kol_id: {"kol": {"id": kol_id, "contact_links_json": [{"value": "old@example.com"}]}},
    )
    monkeypatch.setattr(
        contacts_domain.claims_domain,
        "update_kol_manual",
        lambda kol_id, payload, *, staff: calls.setdefault("update", (kol_id, payload, staff)),
    )
    monkeypatch.setattr(contacts_domain, "_contact_rows", lambda kol_id, *, include_wrong: {"kol_id": kol_id, "include_wrong": include_wrong})

    result = contacts_domain.add_contact(
        7,
        {"contact_type": "email", "contact_value": "new@example.com", "layer": "2", "confidence": "91"},
        staff={"id": 1},
    )

    kol_id, payload, staff = calls["update"]
    assert result == {"kol_id": 7, "include_wrong": True}
    assert kol_id == 7
    assert staff == {"id": 1}
    assert payload["contact_email"] == "new@example.com"
    assert payload["contact_links"][-1]["layer"] == 2
    assert payload["contact_links"][-1]["confidence"] == 91


def test_kol_contacts_domain_rejects_missing_contact_value():
    try:
        contacts_domain.add_contact(7, {"contact_type": "email"}, staff={"id": 1})
    except ValueError as exc:
        assert "contact_type and contact_value required" in str(exc)
    else:
        raise AssertionError("expected missing contact value to fail")
