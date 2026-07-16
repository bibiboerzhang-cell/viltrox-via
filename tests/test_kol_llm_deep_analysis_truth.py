from app.domains.kol import llm_deep_analysis


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _sql, _params=()):
        return _Result(self.rows)


def _row(row_id, *, provider, method, score, evidence_id=None, cache_id=None):
    return {
        "id": row_id,
        "kol_pool_id": 42,
        "source_url": "https://example.com/video" if evidence_id else None,
        "source_evidence_id": evidence_id,
        "analysis_kind": "profile_llm",
        "llm_v6_fit": score,
        "llm_dimensions_11": {},
        "method": method,
        "provider": provider,
        "confidence": 0.9,
        "source_cache_id": cache_id,
        "status": "ready",
        "created_at": "2026-07-16T12:00:00Z",
    }


def test_verified_llm_is_primary_ahead_of_higher_scored_local_extract(monkeypatch):
    rows = [
        _row(1, provider="local_extract", method="kol_account_dossier_extract_v1", score=99),
        _row(2, provider="google", method="video_analysis_final_v1", score=61, evidence_id=3683, cache_id=77),
    ]
    monkeypatch.setattr(llm_deep_analysis, "get_conn", lambda: _Conn(rows))

    payload = llm_deep_analysis.get_kol_llm_deep_analysis(42)

    assert payload["primary_result"]["id"] == 2
    assert payload["primary_result"]["result_kind"] == "llm"
    assert payload["summary"]["has_verified_llm"] is True
    assert payload["summary"]["result_kind_counts"] == {"local_aggregate": 1, "llm": 1}


def test_local_extract_is_explicitly_marked_non_llm(monkeypatch):
    rows = [_row(1, provider="local_extract", method="kol_account_dossier_extract_v1", score=None)]
    monkeypatch.setattr(llm_deep_analysis, "get_conn", lambda: _Conn(rows))

    payload = llm_deep_analysis.get_kol_llm_deep_analysis(42)

    assert payload["primary_result"]["result_kind"] == "local_aggregate"
    assert payload["summary"]["primary_result_kind"] == "local_aggregate"
    assert payload["summary"]["has_verified_llm"] is False
