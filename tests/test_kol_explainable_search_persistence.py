from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.kol import product_resolver, search_sessions, smart_query_planner
from app.domains.kol.search_sessions_serde import _row_to_item


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None


class _RecallPersistenceConn:
    def __init__(self, session_id: int) -> None:
        self.session_id = session_id
        self.items: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {}
        self.status = "planned"
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        assert "SELECT id FROM vkpi_kol_search_sessions" in sql
        assert params == (self.session_id,)
        return _Cursor([{"id": self.session_id}])

    def commit(self) -> None:
        self.commits += 1


def test_explicit_vintage_z1_model_remains_resolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    product = {
        "sku": "VINTAGE-Z1-PRO",
        "model_name": "Vintage Z1 Pro",
        "marketing_name": "Vintage Z1 Pro Flash",
        "series": "Vintage",
        "category_main": "Flash",
    }
    monkeypatch.setattr(product_resolver, "list_product_catalog", lambda **_kwargs: {"products": [product]})
    resolved = product_resolver.resolve_product("Vintage Z1 Pro flash portrait creators")
    assert resolved is not None
    assert resolved["sku"] == "VINTAGE-Z1-PRO"


def test_series_plus_category_does_not_guess_an_exact_catalog_sku(monkeypatch: pytest.MonkeyPatch) -> None:
    lab = {
        "sku": "LAB-35-F18",
        "model_name": "Viltrox LAB 35mm F1.8",
        "marketing_name": "LAB 35mm Prime Lens",
        "series": "LAB",
        "category_main": "Lens",
    }
    lab_50 = {
        "sku": "LAB-50-F14",
        "model_name": "Viltrox LAB 50mm F1.4",
        "marketing_name": "LAB 50mm Prime Lens",
        "series": "LAB",
        "category_main": "Lens",
    }
    epic = {
        "sku": "EPIC-65-MACRO-PL",
        "model_name": "EPIC 65mm T2.8 Macro 1.33x",
        "marketing_name": "EPIC 65mm Macro Anamorphic",
        "series": "EPIC",
        "category_main": "Lens",
    }
    monkeypatch.setattr(product_resolver, "list_product_catalog", lambda **_kwargs: {"products": [lab, lab_50, epic]})
    assert product_resolver.resolve_product("LAB macro photographers") is None
    clarification = product_resolver.unresolved_product_request("LAB macro photographers")
    assert clarification is not None
    assert clarification["requested_series"] == "LAB"


def test_series_plus_mount_does_not_guess_an_exact_catalog_sku(monkeypatch: pytest.MonkeyPatch) -> None:
    evo = {
        "sku": "AF-35-EVO-FE",
        "model_name": "Viltrox AF 35mm EVO FE",
        "marketing_name": "EVO 35mm Lens",
        "series": "EVO",
        "category_main": "Lens",
        "mount": "FE-mount",
    }
    monkeypatch.setattr(product_resolver, "list_product_catalog", lambda **_kwargs: {"products": [evo]})
    assert product_resolver.resolve_product("EVO Sony portrait photographers") is None


def test_recall_attachment_persists_explainability_without_contact_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = 733
    conn = _RecallPersistenceConn(session_id)

    def persist_item(
        target_conn: _RecallPersistenceConn,
        target_session_id: int,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        assert target_conn is conn
        persisted = json.loads(json.dumps(item, ensure_ascii=False))
        row = {
            "id": len(conn.items) + 1,
            "session_id": target_session_id,
            "dedupe_key": persisted["dedupe_key"],
            "item_type": persisted["item_type"],
            "status": persisted["status"],
            "stage": persisted["stage"],
            "rank": persisted["rank"],
            "score": persisted["score"],
            "kol_pool_id": persisted["kol_pool_id"],
            "evidence_id": None,
            "job_id": None,
            "source_url": persisted["source_url"],
            "payload_json": json.dumps(persisted["payload"], ensure_ascii=False),
        }
        restored = _row_to_item(row)
        conn.items.append(restored)
        return restored

    def persist_session(
        target_conn: _RecallPersistenceConn,
        target_session_id: int,
        *,
        status: str,
        summary: dict[str, Any],
    ) -> None:
        assert target_conn is conn
        assert target_session_id == session_id
        conn.status = status
        conn.summary = json.loads(json.dumps(summary, ensure_ascii=False))

    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    monkeypatch.setattr(search_sessions, "_upsert_item", persist_item)
    monkeypatch.setattr(search_sessions, "_update_session", persist_session)
    distribution = {
        "claim_status": "descriptive_only",
        "denominator": 1,
        "denominator_definition": "returned_canonical_candidates",
        "facets": {
            "platform": {"unknown": 0, "youtube": 1},
            "country": {"unknown": 0, "us": 1},
            "language": {"en": 1, "unknown": 0},
            "profile_type": {"creator": 1, "unknown": 0},
            "contact_available": {"unknown": 0, "yes": 1},
            "video_evidence": {"unknown": 0, "yes": 1},
        },
    }
    evidence = [
        {"field": "bio", "term": "35mm", "source": "server_profile_evidence"},
        {"field": "bio", "term": "portrait", "source": "server_profile_evidence"},
    ]
    unsafe_evidence = [
        *evidence,
        {"field": "bio", "term": "sensitive.person@example.test", "source": "server_profile_evidence"},
        {"field": "type_reason", "term": "+1-202-555-0199", "source": "server_profile_evidence"},
    ]
    candidate_facets = {
        "platform": "youtube", "country": "us", "language": "en", "profile_type": "creator",
        "contact_available": "yes", "video_evidence": "yes",
    }
    result = search_sessions.attach_recall_result(
        session_id,
        {
            "match_status": "matched",
            "candidate_set_distribution": distribution,
            "query": {"query_text": "35mm low-light portrait"},
            "diagnostics": {"returned_count": 1, "evidence_gate_enabled": True},
            "buckets": {
                "creator": [{
                    "kol_pool_id": 9001,
                    "handle": "grounded-portrait",
                    "display_name": "Grounded Portrait",
                    "platform": "youtube",
                    "profile_type": "creator",
                    "profile_url": "https://www.youtube.com/@grounded-portrait",
                    "followers": 42_000,
                    "recall_rank_score": 0.83,
                    "display_rank_score": 0.89,
                    "display_relevance_adjust": 0.06,
                    "relevance_flags": ["video_evidence", "fresh_creator"],
                    "relevance_tier_hint": "A",
                    "match_evidence": unsafe_evidence,
                    "why_fit": "private-whatsapp-handle",
                    "candidate_facets": candidate_facets,
                    "evidence": {"contact": "private-whatsapp-handle", "email": "sensitive.person@example.test"},
                    "email": "sensitive.person@example.test",
                    "phone": "+1-202-555-0199",
                    "contact_value": "private-whatsapp-handle",
                    "other_contacts_json": [{"type": "whatsapp", "value": "private-whatsapp-handle"}],
                }],
                "reviewer": [],
            },
        },
    )

    assert conn.commits == 1
    assert conn.status == "ready"
    assert result["items"] == conn.items
    assert len(conn.items) == 1
    payload = conn.items[0]["payload"]
    assert payload["match_evidence"] == evidence
    assert payload["why_fit"] == "bio 命中 35mm；bio 命中 portrait"
    assert payload["candidate_facets"] == candidate_facets
    assert payload["display_rank_score"] == 0.89
    assert payload["display_relevance_adjust"] == 0.06
    assert payload["relevance_flags"] == ["video_evidence", "fresh_creator"]
    assert payload["relevance_tier_hint"] == "A"
    assert conn.summary["candidate_set_distribution"] == distribution
    assert conn.summary["match_status"] == "matched"
    persisted_blob = json.dumps({"items": conn.items, "summary": conn.summary}, ensure_ascii=False)
    for secret in ("sensitive.person@example.test", "+1-202-555-0199", "private-whatsapp-handle"):
        assert secret not in persisted_blob
    assert not ({"email", "phone", "contact", "contact_value", "other_contacts_json"} & set(payload))


@pytest.mark.parametrize(
    "query",
    [
        "collaboration macro creators", "epicenter documentary filmmakers",
        "laboratory macro photographers", "vintage portrait photographers",
    ],
)
def test_provider_free_planner_does_not_clarify_series_word_substrings(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    monkeypatch.setattr(product_resolver, "list_product_catalog", lambda **_kwargs: {"products": []})
    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    assert plan["status"] != "needs_clarification"
    assert plan["reason"] != "explicit_product_not_in_catalog"
    assert plan["provider_calls_performed"] is False


@pytest.mark.parametrize(
    ("query", "series"),
    [
        ("EPIC 65mm macro anamorphic cinematographer", "EPIC"),
        ("Vintage Z1 Pro flash portrait creators", "VINTAGE"),
        ("find creators for 26 e vo lens", "EVO"),
    ],
)
def test_provider_free_planner_preserves_explicit_product_series_guard(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    series: str,
) -> None:
    monkeypatch.setattr(product_resolver, "list_product_catalog", lambda **_kwargs: {"products": []})
    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    assert plan["status"] == "needs_clarification"
    assert plan["reason"] == "explicit_product_not_in_catalog"
    assert plan["provider_calls_performed"] is False
    assert plan["clarification"]["requested_series"] == series
