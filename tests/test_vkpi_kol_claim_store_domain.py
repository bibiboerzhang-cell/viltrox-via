from app.domains.kol import claim_store


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if str(sql).lstrip().upper().startswith("SELECT"):
            return _Result(self.rows.pop(0) if self.rows else None)
        return _Result(None)


def test_kol_claim_store_find_kol_returns_dict(monkeypatch):
    conn = _Conn([{"id": 9, "platform": "youtube", "channel_name": "creator"}])
    monkeypatch.setattr(claim_store, "get_conn", lambda: conn)

    assert claim_store.find_kol("youtube", "creator") == {"id": 9, "platform": "youtube", "channel_name": "creator"}
    assert conn.calls[0][1] == ("youtube", "creator", "creator")


def test_kol_claim_store_create_kol_inserts_and_reads_back(monkeypatch):
    conn = _Conn([{"id": 12, "channel_name": "creator", "platform": "instagram"}])
    monkeypatch.setattr(claim_store, "get_conn", lambda: conn)
    monkeypatch.setattr(claim_store, "utcnow", lambda: "2026-05-25T00:00:00Z")

    row = claim_store.create_kol(
        "instagram",
        "creator",
        {
            "url": "https://instagram.com/creator",
            "category": "photo",
            "follower_count": "1000",
            "avg_views": "250",
            "contact_links": [{"type": "email"}],
            "contact_raw": {"source": "manual"},
        },
        7,
    )

    insert_params = conn.calls[0][1]
    assert row == {"id": 12, "channel_name": "creator", "platform": "instagram"}
    assert insert_params[0:3] == ("creator", "https://instagram.com/creator", "instagram")
    assert insert_params[20:22] == (1000, 250)
    assert insert_params[28] == '[{"type": "email"}]'
    assert insert_params[29] == '{"source": "manual"}'
    assert insert_params[-4:] == (7, 7, "2026-05-25T00:00:00Z", "2026-05-25T00:00:00Z")
