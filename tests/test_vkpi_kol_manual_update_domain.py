from app.domains.kol import manual_update


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


def test_kol_manual_update_returns_existing_when_no_updates(monkeypatch):
    conn = _Conn({"id": 9, "channel_name": "creator"})
    monkeypatch.setattr(manual_update, "get_conn", lambda: conn)
    monkeypatch.setattr(manual_update, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(manual_update.claim_access, "assert_kol_access", lambda *args, **kwargs: None)

    payload = manual_update.update_kol_manual(9, {}, staff={"id": 7})

    assert payload == {"kol": {"id": 9, "channel_name": "creator"}}
    assert conn.calls == [("SELECT * FROM kols WHERE id=?", (9,))]
    assert conn.committed is False


def test_kol_manual_update_updates_allowed_fields_and_audits(monkeypatch):
    conn = _Conn({"id": 9, "channel_name": "creator", "follower_count": 1200})
    audit_calls = []
    monkeypatch.setattr(manual_update, "get_conn", lambda: conn)
    monkeypatch.setattr(manual_update, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(manual_update, "utcnow", lambda: "2026-05-25T00:00:00Z")
    monkeypatch.setattr(manual_update.claim_access, "assert_kol_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(manual_update.claim_audit, "log_kol_audit", lambda **kwargs: audit_calls.append(kwargs))

    payload = manual_update.update_kol_manual(
        9,
        {
            "contact_email": " creator@example.com ",
            "follower_count": "1200",
            "contact_links": [{"type": "email"}],
            "contact_raw": {"source": "manual"},
            "ignored": "no",
        },
        staff={"id": 7},
    )

    update_sql, update_params = conn.calls[0]
    assert payload["kol"]["id"] == 9
    assert "contact_email=?" in update_sql
    assert "follower_count=?" in update_sql
    assert "contact_links_json=?" in update_sql
    assert update_params == [
        "creator@example.com",
        1200,
        '[{"type": "email"}]',
        '{"source": "manual"}',
        "2026-05-25T00:00:00Z",
        9,
    ]
    assert conn.committed is True
    assert audit_calls[0]["action_type"] == "kol_manual_update"
    assert audit_calls[0]["metadata"]["changed_fields"] == [
        "contact_email",
        "follower_count",
        "contact_links_json",
        "contact_raw_json",
    ]
