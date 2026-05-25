import pytest

from app.domains.kol import claim_lookup


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row=None):
        self.row = row
        self.calls = []
        self.committed = False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _Result(self.row)

    def commit(self):
        self.committed = True


def test_kol_claim_lookup_requires_supported_platform(monkeypatch):
    monkeypatch.setattr(claim_lookup, "ensure_vkpi_schema", lambda: None)

    with pytest.raises(ValueError, match="supported platform required"):
        claim_lookup.lookup({"platform": "unknown", "handle": "creator"}, staff={"id": 1})


def test_kol_claim_lookup_returns_existing_claim(monkeypatch):
    conn = _Conn({"id": 4, "staff_id": 7, "status": "active"})
    monkeypatch.setattr(claim_lookup, "get_conn", lambda: conn)
    monkeypatch.setattr(claim_lookup, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(claim_lookup.claim_store, "find_kol", lambda platform, handle: {"id": 9, "platform": platform, "channel_name": handle})

    payload = claim_lookup.lookup({"platform": "instagram", "handle": "Creator", "email": "x@example.com"}, staff={"id": 1})

    assert payload["kol"] == {"id": 9, "platform": "ig", "channel_name": "creator"}
    assert payload["claim"]["id"] == 4
    assert payload["claim"]["is_active"] is True
    assert payload["can_claim"] is False
    assert conn.calls[0][1] == (9,)


def test_kol_claim_lookup_can_create_missing_kol(monkeypatch):
    conn = _Conn(None)
    audit_calls = []
    monkeypatch.setattr(claim_lookup, "get_conn", lambda: conn)
    monkeypatch.setattr(claim_lookup, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(claim_lookup.claim_store, "find_kol", lambda platform, handle: None)
    monkeypatch.setattr(
        claim_lookup.claim_store,
        "create_kol",
        lambda platform, handle, body, actor_staff_id: {"id": 11, "platform": platform, "channel_name": handle, "actor": actor_staff_id},
    )
    monkeypatch.setattr(claim_lookup.claim_audit, "log_kol_audit", lambda **kwargs: audit_calls.append(kwargs))

    payload = claim_lookup.lookup({"platform": "youtube", "handle": "@Creator", "create_if_missing": True}, staff={"id": 5})

    assert payload["created"] is True
    assert payload["kol"]["id"] == 11
    assert payload["can_claim"] is True
    assert conn.committed is True
    assert audit_calls[0]["action_type"] == "kol_lookup_create"
    assert audit_calls[0]["kol_id"] == 11
