from app.domains.kol import claim_query_helpers


class _Result:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_kol_claim_query_helpers_parse_json_with_fallback():
    assert claim_query_helpers.safe_json_loads('{"a": 1}', {}) == {"a": 1}
    assert claim_query_helpers.safe_json_loads("", {"fallback": True}) == {"fallback": True}


def test_kol_claim_query_helpers_fetch_rows(monkeypatch):
    conn = _Conn(_Result(rows=[{"id": 1}, {"id": 2}]))
    monkeypatch.setattr(claim_query_helpers, "get_conn", lambda: conn)

    assert claim_query_helpers.rows_or_empty("SELECT * FROM t WHERE a=?", ("x",)) == [{"id": 1}, {"id": 2}]
    assert conn.calls == [("SELECT * FROM t WHERE a=?", ("x",))]


def test_kol_claim_query_helpers_fetch_row(monkeypatch):
    conn = _Conn(_Result(row={"id": 1}))
    monkeypatch.setattr(claim_query_helpers, "get_conn", lambda: conn)

    assert claim_query_helpers.row_or_empty("SELECT * FROM t") == {"id": 1}


def test_kol_claim_query_helpers_return_empty_on_db_error(monkeypatch):
    monkeypatch.setattr(claim_query_helpers, "get_conn", lambda: _Conn(RuntimeError("boom")))

    assert claim_query_helpers.rows_or_empty("SELECT * FROM t") == []
    assert claim_query_helpers.row_or_empty("SELECT * FROM t") == {}
