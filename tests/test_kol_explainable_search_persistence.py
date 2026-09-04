from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.kol import product_resolver, search_sessions, search_sessions_attach, smart_query_planner
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
        if "DELETE FROM vkpi_kol_search_session_items" in sql:
            retained = set(params[1:]) if "dedupe_key NOT IN" in sql else set()
            self.items[:] = [
                item
                for item in self.items
                if item.get("item_type") != "recall_candidate"
                or item.get("dedupe_key") in retained
            ]
            return _Cursor([])
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


# Real vkpi_products rows (prod clone, 2026-08-24) around the Z1 / DC-550 families.
_Z1_AND_DC550_ROWS: list[dict[str, Any]] = [
    {
        "sku": "VINTAGE-Z1-PRO-TTL-RETRO-ON-CAMERA-FLASH",
        "model_name": "Viltrox Vintage Z1 Pro TTL Retro On-Camera Flash",
        "marketing_name": "Vintage Z1 Pro TTL Retro On-Camera Flash",
        "series": "Pro",
        "category_main": "Lighting",
    },
    {
        "sku": "VINTAGE-Z1-RETRO-ON-CAMERA-FLASH",
        "model_name": "Viltrox Vintage Z1 Retro On-Camera Flash",
        "marketing_name": "Vintage Z1 Retro On-Camera Flash",
        "series": "",
        "category_main": "Lighting",
    },
    {"sku": "VL-LIT073", "model_name": "Vintage Z1+", "marketing_name": "", "series": "", "category_main": "Lighting/Flash"},
    {"sku": "VL-LIT092", "model_name": "Vintage Z1 PRO-N", "marketing_name": "", "series": "", "category_main": "Lighting/Flash"},
    {"sku": "VL-LIT093", "model_name": "Vintage Z1 PRO-F", "marketing_name": "", "series": "", "category_main": "Lighting/Flash"},
    {"sku": "VL-LIT094", "model_name": "Vintage Z1 PRO-C", "marketing_name": "", "series": "", "category_main": "Lighting/Flash"},
    {"sku": "VL-LIT095", "model_name": "Vintage Z1 PRO-S", "marketing_name": "", "series": "", "category_main": "Lighting/Flash"},
    {
        "sku": "DC-550-PRO-LL-PORTABLE-5-5-INCH-HD-CAMERA-MONITOR",
        "model_name": "Viltrox DC-550 Pro ll Portable 5.5 Inch HD Camera Monitor",
        "marketing_name": "DC-550 Pro ll Portable 5.5 Inch HD Camera Monitor",
        "series": "Pro",
        "category_main": "Monitor",
    },
    {"sku": "VL-MON003", "model_name": "DC-550", "marketing_name": "", "series": "", "category_main": "Monitor"},
    {"sku": "VL-MON005", "model_name": "DC-550 PRO", "marketing_name": "", "series": "", "category_main": "Monitor"},
    {"sku": "VL-MON016", "model_name": "DC-550 PRO II", "marketing_name": "", "series": "", "category_main": "Monitor"},
]


@pytest.mark.parametrize("query", ["Z1 pro", "z1pro", "Z1 pro的一些不同行业的用户比如赛车,厨师餐饮等"])
def test_compact_z1_pro_query_resolves_the_pro_flash(
    monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    # 2026-08-24 R2:单字母型号 + pro("z1 pro")必须让 pro 进评分词,否则 Z1 Pro 与
    # Z1 打平 → fail-closed None → 整条中文 query 死在澄清墙。
    monkeypatch.setattr(
        product_resolver, "list_product_catalog", lambda **_kwargs: {"products": _Z1_AND_DC550_ROWS}
    )
    resolved = product_resolver.resolve_product(query)
    assert resolved is not None
    assert resolved["sku"] == "VINTAGE-Z1-PRO-TTL-RETRO-ON-CAMERA-FLASH"


def test_bare_z1_still_resolves_the_base_model_not_the_pro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # (strong, matched) 平手时的 series 长度 tiebreak 取最短 = 基础款:query 没写
    # "pro" 就绝不凭空升成 Pro 款。
    monkeypatch.setattr(
        product_resolver, "list_product_catalog", lambda **_kwargs: {"products": _Z1_AND_DC550_ROWS}
    )
    resolved = product_resolver.resolve_product("Z1")
    assert resolved is not None
    assert resolved["sku"] == "VINTAGE-Z1-RETRO-ON-CAMERA-FLASH"


def test_550_pro_resolution_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        product_resolver, "list_product_catalog", lambda **_kwargs: {"products": _Z1_AND_DC550_ROWS}
    )
    resolved = product_resolver.resolve_product("550 pro")
    assert resolved is not None
    assert resolved["sku"] == "DC-550-PRO-LL-PORTABLE-5-5-INCH-HD-CAMERA-MONITOR"


def test_genuinely_ambiguous_tie_still_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # 只剩两条同分同 series 长度的卡口变体(PRO-N / PRO-F)→ tiebreak 后仍并列 →
    # 保持 fail-closed 返回 None,不靠行序猜卡口。
    variants = [row for row in _Z1_AND_DC550_ROWS if row["sku"] in {"VL-LIT092", "VL-LIT093"}]
    monkeypatch.setattr(
        product_resolver, "list_product_catalog", lambda **_kwargs: {"products": variants}
    )
    assert product_resolver.resolve_product("Z1 pro") is None


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


def test_smart_local_session_persists_global_rank_and_safe_qualification_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def record_items(
        session_id: int,
        items: list[dict[str, Any]],
        *,
        status: str,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        captured.update(session_id=session_id, items=items, status=status, summary=summary)
        return {"id": session_id, "items": items, "status": status, "result_summary": summary}

    monkeypatch.setattr(search_sessions, "record_items", record_items)
    proof = {
        "schema": "smart_local_gate_evidence_v2",
        "passed": True,
        "rejection_reasons": [],
        "account_quality": {"verdict": "eligible_creator_account", "passed": True, "source": "existing_discovery_classifiers"},
        "followers": {"value": 12_000, "minimum": 3_000, "known": True, "passed": True, "source": "vkpi_kol_pool.followers"},
        "activity": {"posted_at": "2026-08-10T00:00:00+00:00", "age_days": 5, "fresh_priority": True, "maximum_age_days": 45, "passed": True, "source": "vkpi_kol_video_evidence.posted_at"},
        "market": {"value": "us", "target": "us", "method": "explicit_country", "confidence": 1, "source": "vkpi_kol_pool.country", "passed": True},
        "language": {"values": ["en"], "targets": [], "filter_requested": False, "passed": True, "source": "vkpi_kol_profiles.language"},
        "profile_type": {"values": ["creator"], "targets": [], "filter_requested": False, "passed": True, "source": "vkpi_kol_profile_embeddings.profile_type"},
        "platform": {"value": "youtube", "targets": ["youtube"], "passed": True, "source": "vkpi_kol_pool.platform"},
        "relevance": {"passed": True, "evidence": [{"field": "bio", "term": "35mm", "source": "server_profile_evidence"}], "source": "field_level_match_evidence"},
        "email": "private@example.test",
    }
    reviewer = {
        "kol_pool_id": 2, "bucket": "reviewer", "handle": "review-first", "platform": "youtube",
        "followers": 12_000, "recall_rank_score": 0.9,
        "match_evidence": [{"field": "bio", "term": "35mm", "source": "server_profile_evidence"}],
        "candidate_facets": {"platform": "youtube", "country": "us", "contact_available": "yes"},
        "qualification_evidence": proof,
    }
    creator = {
        **reviewer,
        "kol_pool_id": 1,
        "bucket": "creator",
        "handle": "creator-second",
        "recall_rank_score": 0.8,
    }
    local_contract = {
        "schema": "smart_local_qualified_v2",
        "status": "shortfall",
        "policy": {"policy_version": 2, "target_count": 30, "candidate_limit": 500, "min_followers": 3000, "server_owned": True, "platforms": ["youtube"], "market": "us"},
        "qualified_count": 2,
        "returned_count": 2,
        "shortfall": 28,
        "shortfall_reason": "qualified_candidates_exhausted",
        "funnel": {"qualified": 2, "returned": 2},
        "rejected_by_reason": {"latest_video_stale": 3},
        "stage_timing": {"qualification_ms": 1.25, "total_ms": 4.5},
        "ratio_policy": {"policy": "soft", "creator_target": 15, "reviewer_target": 15, "unused_quota_backfilled": 0},
        "gate_evidence": [{**proof, "email": "private@example.test"}],
    }

    search_sessions_attach.attach_recall_result(
        77,
        {
            "query": {"query_text": "US YouTube 35mm"},
            "items": [reviewer, creator],
            "buckets": {"creator": [creator], "reviewer": [reviewer]},
            "local_qualification": local_contract,
        },
    )

    assert [item["payload"]["bucket"] for item in captured["items"]] == ["reviewer", "creator"]
    assert [item["rank"] for item in captured["items"]] == [1, 2]
    assert captured["items"][0]["payload"]["server_rank"] == 1
    assert captured["items"][0]["payload"]["candidate_facets"]["contact_available"] == "yes"
    assert captured["items"][0]["payload"]["qualification_evidence"]["activity"]["posted_at"] == "2026-08-10T00:00:00+00:00"
    assert captured["summary"]["local_qualification"]["shortfall"] == 28
    assert captured["summary"]["recall_snapshot_attached"] is True
    assert captured["summary"]["recall_snapshot_complete"] is True
    assert "gate_evidence" not in captured["summary"]["local_qualification"]
    assert "private@example.test" not in json.dumps(captured, ensure_ascii=False)


def test_recall_snapshot_becomes_authoritative_only_after_worker_attach() -> None:
    queued = {
        "query_type": "text_recall",
        "result_summary": {"smart_search_profile_advance_job": {"status": "queued"}},
    }
    search_sessions._refresh_visible_recall_summary(queued, [])

    assert queued["items_snapshot_complete"] is True
    assert queued["recall_snapshot_complete"] is False
    assert queued["result_summary"]["recall_snapshot_complete"] is False
    assert "match_status" not in queued["result_summary"]

    worker_attached_then_reach_gated_empty = {
        "query_type": "text_recall",
        "result_summary": {"kind": "kol_recall", "recall_snapshot_attached": True},
    }
    search_sessions._refresh_visible_recall_summary(worker_attached_then_reach_gated_empty, [])

    assert worker_attached_then_reach_gated_empty["recall_snapshot_complete"] is True
    assert worker_attached_then_reach_gated_empty["result_summary"]["match_status"] == "empty"
    assert worker_attached_then_reach_gated_empty["result_summary"]["diagnostics"]["returned_count"] == 0


def test_visible_recall_rows_recompute_strict_v2_counts_after_read_time_gate() -> None:
    session = {
        "query_type": "text_recall",
        "result_summary": {
            "kind": "kol_recall",
            "recall_snapshot_attached": True,
            "local_qualification": {
                "schema": "smart_local_qualified_v2",
                "policy": {"policy_version": 2, "server_owned": True, "target_count": 30},
                "status": "ready",
                "qualified_count": 30,
                "returned_count": 30,
                "shortfall": 0,
                "funnel": {"qualified": 30, "returned": 30},
            },
        },
    }
    visible = [{
        "id": 1,
        "item_type": "recall_candidate",
        "kol_pool_id": 7,
        "payload": {"bucket": "creator", "candidate_facets": {"platform": "youtube"}},
    }]
    search_sessions._refresh_visible_recall_summary(session, visible)
    contract = session["result_summary"]["local_qualification"]
    assert contract["qualified_count"] == 1
    assert contract["returned_count"] == 1
    assert contract["shortfall"] == 29
    assert contract["status"] == "shortfall"
    assert contract["funnel"]["qualified"] == 1
    assert contract["funnel"]["returned"] == 1


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
    ("query", "series", "reason"),
    [
        ("EPIC 65mm macro anamorphic cinematographer", "EPIC", "explicit_product_not_in_catalog"),
        ("Vintage Z1 Pro flash portrait creators", "VINTAGE", "recognized_product_alias_not_in_catalog"),
        ("find creators for 26 e vo lens", "EVO", "explicit_product_not_in_catalog"),
    ],
)
def test_provider_free_planner_preserves_explicit_product_series_guard(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    series: str,
    reason: str,
) -> None:
    monkeypatch.setattr(product_resolver, "list_product_catalog", lambda **_kwargs: {"products": []})
    plan = smart_query_planner.plan_text_query_provider_free(query, body={})
    assert plan["status"] == "needs_clarification"
    assert plan["reason"] == reason
    assert plan["provider_calls_performed"] is False
    assert plan["clarification"]["requested_series"] == series
