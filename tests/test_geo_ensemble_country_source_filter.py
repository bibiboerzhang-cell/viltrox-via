"""geo_ensemble 信号④a 只吃硬信号国家(declared/name);language 推断的假国家不进融合。"""
from __future__ import annotations

from app.domains.audience import geo_ensemble


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=()):
        return _Cur(self.rows)


def test_language_derived_country_rows_are_ignored():
    comments = [{"platform": "youtube", "author_handle": f"u{i}", "author_id": str(i)} for i in range(4)]
    db = _Db([
        {"author_key": "u0", "country": "US", "country_source": "language"},
        {"author_key": "u1", "country": "US", "country_source": "language"},
        {"author_key": "u2", "country": "DE", "country_source": "declared"},
        {"author_key": "u3", "country": "JP", "country_source": "name"},
    ])
    sig = geo_ensemble._signal_commenter_profile_country(db, comments)
    assert sig["status"] == "ready"
    assert sig["sample_size"] == 2
    assert set(sig["source_breakdown"]) == {"declared", "name"}
    codes = {d["code"] if isinstance(d, dict) else d[0] for d in sig["distribution"]}
    assert "US" not in codes


def test_only_language_rows_means_no_hit():
    comments = [{"platform": "tiktok", "author_handle": "a", "author_id": "1"}]
    db = _Db([{"author_key": "a", "country": "US", "country_source": "language"}])
    sig = geo_ensemble._signal_commenter_profile_country(db, comments)
    assert sig["status"] != "ready"
    assert sig.get("sample_size", 0) in (0, None)
