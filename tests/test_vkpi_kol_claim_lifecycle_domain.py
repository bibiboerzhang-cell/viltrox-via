import pytest

from app.domains.kol import claim_lifecycle


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, select_rows):
        self.select_rows = list(select_rows)
        self.calls = []
        self.committed = False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if str(sql).lstrip().upper().startswith("SELECT"):
            return _Result(self.select_rows.pop(0) if self.select_rows else None)
        return _Result(None)

    def commit(self):
        self.committed = True


def test_kol_claim_lifecycle_requires_staff(monkeypatch):
    monkeypatch.setattr(claim_lifecycle, "ensure_vkpi_schema", lambda: None)

    with pytest.raises(ValueError, match="staff_id required"):
        claim_lifecycle.claim(9, {}, staff=None)


def test_kol_claim_lifecycle_rejects_missing_kol(monkeypatch):
    conn = _Conn([None])
    monkeypatch.setattr(claim_lifecycle, "get_conn", lambda: conn)
    monkeypatch.setattr(claim_lifecycle, "ensure_vkpi_schema", lambda: None)

    with pytest.raises(LookupError, match="kol not found"):
        claim_lifecycle.claim(9, {}, staff={"id": 7})


def test_kol_claim_lifecycle_creates_claim_and_audits(monkeypatch):
    conn = _Conn([
        {"id": 9},
        None,
        {"id": 4, "kol_id": 9, "staff_id": 7, "status": "active"},
    ])
    audit_calls = []
    monkeypatch.setattr(claim_lifecycle, "get_conn", lambda: conn)
    monkeypatch.setattr(claim_lifecycle, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(claim_lifecycle, "utcnow", lambda: "2026-05-25T00:00:00Z")
    monkeypatch.setattr(claim_lifecycle.claim_audit, "log_kol_audit", lambda **kwargs: audit_calls.append(kwargs))

    payload = claim_lifecycle.claim(
        9,
        {"project_id": "12", "metadata": {"source": "manual"}, "expires_days": "7"},
        staff={"id": 7},
    )

    insert_sql, insert_params = conn.calls[2]
    update_sql, update_params = conn.calls[3]
    assert payload["claim"]["id"] == 4
    assert payload["claim"]["is_active"] is True
    assert "INSERT INTO vkpi_kol_claims" in insert_sql
    assert insert_params[0:5] == (9, 7, 12, "active", "2026-05-25T00:00:00Z")
    assert insert_params[7] == '{"source": "manual"}'
    assert "UPDATE kols SET assigned_staff_id=?" in update_sql
    assert update_params == (7, "2026-05-25T00:00:00Z", 9)
    assert conn.committed is True
    assert audit_calls[0]["action_type"] == "kol_claim_create"
    assert audit_calls[0]["metadata"]["claim_id"] == 4
