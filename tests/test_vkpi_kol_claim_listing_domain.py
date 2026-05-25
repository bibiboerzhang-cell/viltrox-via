from app.domains.kol import claim_listing


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _Result(self.rows)


def test_kol_claim_listing_filters_status_and_scope(monkeypatch):
    conn = _Conn([{"id": 1, "kol_id": 9, "status": "active"}])
    monkeypatch.setattr(claim_listing, "get_conn", lambda: conn)
    monkeypatch.setattr(claim_listing, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(claim_listing.scope, "effective_staff_id", lambda staff, staff_id: 7)
    monkeypatch.setattr(claim_listing.scope, "scope_context", lambda staff, staff_id: {"staff_id": staff_id, "scoped": True})

    payload = claim_listing.list_claims(status="active", limit=999, staff={"id": 7}, staff_id=7)

    assert payload == {
        "claims": [{"id": 1, "kol_id": 9, "status": "active"}],
        "scope": {"staff_id": 7, "scoped": True},
    }
    sql, params = conn.calls[0]
    assert "c.status=?" in sql
    assert "c.staff_id=?" in sql
    assert params == ("active", 7, 500)


def test_kol_claim_listing_can_list_without_status(monkeypatch):
    conn = _Conn([])
    monkeypatch.setattr(claim_listing, "get_conn", lambda: conn)
    monkeypatch.setattr(claim_listing, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(claim_listing.scope, "effective_staff_id", lambda staff, staff_id: None)
    monkeypatch.setattr(claim_listing.scope, "scope_context", lambda staff, staff_id: {"scope": "all"})

    payload = claim_listing.list_claims(status="", limit=0, staff={"role": "manager"})

    assert payload == {"claims": [], "scope": {"scope": "all"}}
    assert conn.calls[0][1] == (100,)
