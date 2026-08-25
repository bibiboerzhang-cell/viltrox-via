from __future__ import annotations

from app.domains.recommendations import new_launch_match_helpers as helpers


def test_target_family_raw_hit_skips_catalog_resolver(monkeypatch) -> None:
    selected = {"id": 5, "entity_uid": "family-5"}
    attempted: list[str] = []

    def select(candidate: str) -> dict:
        attempted.append(candidate)
        if candidate == "AF 35mm":
            return selected
        raise ValueError("miss")

    monkeypatch.setattr(helpers, "_query_derived_candidates", lambda _query: [])
    monkeypatch.setattr(
        helpers,
        "_resolver_derived_candidates",
        lambda _query: (_ for _ in ()).throw(AssertionError("catalog resolver must stay lazy")),
    )
    monkeypatch.setattr(helpers, "_select_target_family", select)

    family, used_query, reason = helpers.resolve_target_family("AF 35mm")

    assert family == selected
    assert used_query == "AF 35mm"
    assert reason == ""
    assert attempted == ["AF 35mm"]


def test_target_family_skips_catalog_resolver_when_cheap_candidate_matches(monkeypatch) -> None:
    selected = {"id": 7, "entity_uid": "family-7"}
    attempted: list[str] = []

    def select(candidate: str) -> dict:
        attempted.append(candidate)
        if candidate == "af 28":
            return selected
        raise ValueError("miss")

    monkeypatch.setattr(helpers, "_query_derived_candidates", lambda _query: ["af 28"])
    monkeypatch.setattr(
        helpers,
        "_resolver_derived_candidates",
        lambda _query: (_ for _ in ()).throw(AssertionError("catalog resolver must stay lazy")),
    )
    monkeypatch.setattr(helpers, "_select_target_family", select)

    family, used_query, reason = helpers.resolve_target_family("AF-28MM")

    assert family == selected
    assert used_query == "af 28"
    assert reason == ""
    assert attempted == ["AF-28MM", "af 28"]


def test_target_family_preserves_resolver_fallback_order_and_deduplication(monkeypatch) -> None:
    selected = {"id": 9, "entity_uid": "family-9"}
    attempted: list[str] = []
    resolver_calls: list[str] = []

    def select(candidate: str) -> dict:
        attempted.append(candidate)
        if candidate == "catalog family":
            return selected
        raise ValueError("miss")

    monkeypatch.setattr(helpers, "_query_derived_candidates", lambda _query: ["cheap", "cheap"])

    def resolver(query: str) -> list[str]:
        resolver_calls.append(query)
        return ["cheap", "catalog family", "later"]

    monkeypatch.setattr(helpers, "_resolver_derived_candidates", resolver)
    monkeypatch.setattr(helpers, "_select_target_family", select)

    family, used_query, reason = helpers.resolve_target_family("raw")

    assert family == selected
    assert used_query == "catalog family"
    assert reason == ""
    assert resolver_calls == ["raw"]
    assert attempted == ["raw", "cheap", "catalog family"]
