from __future__ import annotations

import json
import sqlite3

import pytest

from app.domains.kol import (
    my_kol_video_cache_truth,
    profile_recall_cached_content as cached_content,
    profile_recall_support,
)
from app.domains.kol.profile_recall_match_evidence import (
    build_controlled_alias_evidence,
    build_match_evidence,
)
from app.domains.kol.targeted_search_contract import build_locked_term_groups
from app.domains.kol.profile_recall_qualification_projection import _project_smart_local_item
from app.domains.kol.search_sessions_attach import _safe_match_evidence


VIDEO_URL = "https://www.youtube.com/watch?v=abcDEF12345"
TARGETS = [{"evidence_id": 42, "content_url": VIDEO_URL}]


class _Rows(list):
    def fetchall(self) -> list:
        return list(self)


class _ContextConnection:
    def __init__(self, raw_platform_data: dict) -> None:
        self.raw_platform_data = raw_platform_data

    def execute(self, sql: str, _params: tuple) -> _Rows:
        if "FROM vkpi_kol_pool" in sql:
            return _Rows([{"kol_pool_id": 7, "raw_platform_data": self.raw_platform_data}])
        if "FROM vkpi_kol_video_evidence" in sql:
            return _Rows(
                [
                    {
                        "kol_pool_id": 7,
                        "posted_at": "2026-08-27T00:00:00Z",
                        "evidence_type": "video",
                        "content_url": VIDEO_URL,
                        "title": "A quiet day on location",
                        "is_active": True,
                    }
                ]
            )
        raise AssertionError(sql)


def test_minimal_cache_schema_projects_optional_production_columns_safely() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_analysis_cache (
            target_type TEXT,
            target_id TEXT,
            derive_method TEXT,
            status TEXT,
            result TEXT
        );
        INSERT INTO vkpi_analysis_cache
            (target_type, target_id, derive_method, status, result)
        VALUES ('video', '42', 'video_analysis_final_v1', 'ready', '{}');
        """
    )

    rows = my_kol_video_cache_truth.analysis_caches_for_evidence(conn, [42])

    assert rows[42]["raw_status"] == "ready"
    assert rows[42]["status"] == "legacy_unverified"
    assert rows[42]["model"] is None
    assert rows[42]["prompt_version"] is None
    assert rows[42]["updated_at"] is None


def test_cache_schema_probe_does_not_hide_operational_database_errors() -> None:
    class _BrokenConnection:
        def execute(self, _sql: str, _params: tuple = ()) -> None:
            raise RuntimeError("database connection lost")

    with pytest.raises(RuntimeError, match="database connection lost"):
        my_kol_video_cache_truth.analysis_caches_for_evidence(
            _BrokenConnection(),
            [42],
        )


def _evidence(records: list[dict]) -> dict:
    return {
        "representative_evidence": [
            {"title": "A quiet day on location", "content_url": VIDEO_URL}
        ],
        cached_content.PRIVATE_CONTENT_TARGETS_KEY: TARGETS,
        cached_content.PRIVATE_CONTENT_EVIDENCE_KEY: records,
    }


def test_title_miss_exact_cached_description_builds_field_evidence_without_raw_leak() -> None:
    private_marker = "private-producer@example.test +1 (202) 555-0199"
    raw = {
        "videos": [
            {
                "video_id": "abcDEF12345",
                "title": "A quiet day on location",
                "description": (
                    "Food shoot workflow using TTL flash for restaurant portraits. "
                    f"Contact {private_marker}"
                ),
            }
        ]
    }

    enriched = cached_content.attach_private_content_evidence(
        {
            "representative_evidence": [
                {"title": "A quiet day on location", "content_url": VIDEO_URL}
            ],
            cached_content.PRIVATE_CONTENT_TARGETS_KEY: TARGETS,
        },
        raw_platform_data=raw,
        cache_rows_by_evidence_id={},
    )
    match = build_match_evidence(
        {},
        enriched,
        "food TTL flash creator",
        min_intent_terms=1,
    )

    assert {
        (row["field"], row["source"])
        for row in match
    } >= {
        ("representative_evidence.description", "cached_pool_video.description"),
    }
    assert {row["term"] for row in match} >= {"food", "ttl", "flash"}
    assert enriched[cached_content.CONTENT_EVIDENCE_STATUS_KEY] == {
        "status": "available",
        "pending": False,
        "pending_counts_toward_target": False,
        "source_types": ["cached_pool_video.description"],
        "evidence_fields": ["description"],
        "evidence_record_count": 1,
        "content_text_returned": False,
        "provider_calls": False,
        "llm_calls": False,
        "claim_status": "descriptive_only",
    }

    projected = _project_smart_local_item(
        {
            "match_evidence": match,
            "why_fit": private_marker,
            "candidate_facets": {},
            "content_evidence_status": enriched[cached_content.CONTENT_EVIDENCE_STATUS_KEY],
            cached_content.PRIVATE_CONTENT_EVIDENCE_KEY: enriched[
                cached_content.PRIVATE_CONTENT_EVIDENCE_KEY
            ],
        }
    )
    serialized = json.dumps(projected, ensure_ascii=False)
    assert private_marker not in serialized
    assert "restaurant portraits" not in serialized
    assert cached_content.PRIVATE_CONTENT_EVIDENCE_KEY not in projected
    assert projected["content_evidence_status"]["content_text_returned"] is False
    assert _safe_match_evidence(
        match,
        allowed_terms={"food", "ttl", "flash"},
    ) == match


def test_smart_local_context_connects_existing_pool_cache_to_match_gate(monkeypatch) -> None:
    conn = _ContextConnection(
        {
            "videos": [
                {
                    "video_id": "abcDEF12345",
                    "description": "Restaurant food production using a TTL flash",
                }
            ]
        }
    )
    monkeypatch.setattr(
        my_kol_video_cache_truth,
        "analysis_caches_for_evidence",
        lambda _conn, _ids: {},
    )

    _rows, evidence = profile_recall_support.smart_local_qualification_context(
        [7],
        rows_by_id={7: {"kol_pool_id": 7}},
        evidence_by_id={
            7: {
                "representative_evidence": [
                    {"title": "A quiet day on location", "content_url": VIDEO_URL}
                ],
                cached_content.PRIVATE_CONTENT_TARGETS_KEY: TARGETS,
            }
        },
        get_connection=lambda: conn,
        table_columns=lambda _conn, table: (
            {"raw_platform_data"}
            if table == "vkpi_kol_pool"
            else {"posted_at", "title", "video_title", "content_url", "is_active", "evidence_type"}
        ),
    )
    match = build_match_evidence(
        {},
        evidence[7],
        "restaurant food TTL flash creator",
        min_intent_terms=1,
    )

    assert match
    assert {row["source"] for row in match} == {"cached_pool_video.description"}
    assert evidence[7][cached_content.CONTENT_EVIDENCE_STATUS_KEY]["provider_calls"] is False
    assert evidence[7][cached_content.CONTENT_EVIDENCE_STATUS_KEY]["llm_calls"] is False


def test_mismatched_or_absent_cache_stays_pending_and_never_invents_evidence() -> None:
    enriched = cached_content.attach_private_content_evidence(
        {
            "representative_evidence": [
                {"title": "A quiet day on location", "content_url": VIDEO_URL}
            ],
            cached_content.PRIVATE_CONTENT_TARGETS_KEY: TARGETS,
        },
        raw_platform_data={
            "videos": [
                {
                    "video_id": "different9999",
                    "description": "Food TTL flash restaurant workflow",
                }
            ]
        },
        cache_rows_by_evidence_id={},
    )

    assert enriched[cached_content.PRIVATE_CONTENT_EVIDENCE_KEY] == []
    assert enriched[cached_content.CONTENT_EVIDENCE_STATUS_KEY]["status"] == "pending_content_evidence"
    assert enriched[cached_content.CONTENT_EVIDENCE_STATUS_KEY]["pending"] is True
    assert enriched[cached_content.CONTENT_EVIDENCE_STATUS_KEY]["pending_counts_toward_target"] is False
    assert build_match_evidence(
        {},
        enriched,
        "food TTL flash creator",
        min_intent_terms=1,
    ) == []


def test_canonical_final_v1_uses_only_generic_visual_facts_not_fit_or_verdict() -> None:
    cache_row = {
        "id": 900,
        "target_type": "video",
        "target_id": "42",
        "derive_method": "video_analysis_final_v1",
        "status": "ready",
        "result": {
            "layer1_visual_content": {
                "content_summary": "Wedding filmmaker prepares a field monitor on set",
                "product_presence": "External field monitor attached to the camera rig",
                "scene_timeline": [
                    {"timestamp": "00:04", "what": "Wedding ceremony camera setup"},
                ],
                "brand_exposure": "Viltrox brand affinity should be high",
            },
            "layer5_recommendations": {
                "cooperation_recommendation": "Hire this creator for Viltrox",
            },
            "layer6_flags_and_scores": {
                "final_verdict": "Excellent Viltrox fit",
            },
            "raw_gemini_video": {"viltrox_detected": True},
        },
    }

    accepted = cached_content.canonical_final_v1_content_evidence(
        {42: cache_row},
        TARGETS,
        classifier=lambda *_args, **_kwargs: {"reusable": True},
    )
    joined = " ".join(str(row.get("text") or "") for row in accepted).lower()

    assert {row["source"] for row in accepted} == {
        "canonical_final_v1.content_summary",
        "canonical_final_v1.product_presence",
        "canonical_final_v1.scene_timeline",
    }
    assert "wedding filmmaker" in joined
    assert "field monitor" in joined
    assert "brand affinity" not in joined
    assert "excellent viltrox fit" not in joined
    assert "hire this creator" not in joined
    assert all(row["claim_status"] == "descriptive_only" for row in accepted)

    match = build_match_evidence(
        {},
        _evidence(accepted),
        "field monitor wedding filmmaker",
        min_intent_terms=1,
    )
    assert match
    assert {row["source"] for row in match}.issubset(
        {
            "canonical_final_v1.content_summary",
            "canonical_final_v1.product_presence",
            "canonical_final_v1.scene_timeline",
        }
    )

    rejected = cached_content.canonical_final_v1_content_evidence(
        {42: cache_row},
        TARGETS,
        classifier=lambda *_args, **_kwargs: {"reusable": False},
    )
    assert rejected == []


def test_cached_caption_and_transcript_are_field_specific_and_contact_sanitized() -> None:
    fields = cached_content.cached_item_content_fields(
        {
            "caption": "Motorsport shoot with a TTL flash; mail racer@example.test",
            "transcript": [
                {"text": "At the track I use the flash for pit-lane portraits"},
                {"text": "Call +44 20 7946 0958 for booking"},
            ],
        }
    )

    assert set(fields) == {"caption", "transcript"}
    assert "motorsport shoot" in fields["caption"].lower()
    assert "pit-lane portraits" in fields["transcript"].lower()
    serialized = json.dumps(fields, ensure_ascii=False)
    assert "racer@example.test" not in serialized
    assert "+44 20 7946 0958" not in serialized


def test_private_cached_content_can_emit_controlled_alias_coordinates_without_text() -> None:
    evidence = _evidence([{
        "field": "transcript",
        "text": "Racing night shoot with one speedlight and a private workflow note",
        "source": "cached_pool_video.transcript",
    }])
    locked = build_locked_term_groups(
        capability="on-camera flash",
        segment="motorsport",
    )

    rows = build_controlled_alias_evidence({}, evidence, locked)

    assert {
        (row["canonical_term"], row["observed_term"], row["field"])
        for row in rows
    } >= {
        ("on-camera flash", "speedlight", "representative_evidence.transcript"),
        ("motorsport", "racing", "representative_evidence.transcript"),
    }
    assert "private workflow note" not in json.dumps(rows, ensure_ascii=False)

    persisted = _safe_match_evidence(
        rows,
        allowed_terms=set(),
        controlled_specs=[locked],
    )
    assert persisted == rows

    injected = [{**rows[0], "term": "zoomlight", "observed_term": "zoomlight"}]
    assert _safe_match_evidence(
        injected,
        allowed_terms=set(),
        controlled_specs=[locked],
    ) == []


def test_session_projection_keeps_people_role_only_from_profile_identity_fields() -> None:
    locked = build_locked_term_groups(
        capability="",
        segment="night",
        role_terms=["camera operator"],
    )
    rows = build_controlled_alias_evidence(
        {"bio": "Independent camera operator"},
        {"representative_evidence": [{"title": "Interview with a camera operator"}]},
        locked,
    )
    role = next(row for row in rows if row["evidence_group"] == "people_role")

    assert role["field"] == "bio"
    assert _safe_match_evidence(
        [role],
        allowed_terms=set(),
        controlled_specs=[locked],
    ) == [role]
    assert _safe_match_evidence(
        [{**role, "field": "representative_evidence.title"}],
        allowed_terms=set(),
        controlled_specs=[locked],
    ) == []
    for derived_or_content_field in (
        "primary_topic",
        "content_style",
        "secondary_topics_json",
        "profile_text",
        "type_reason",
    ):
        assert _safe_match_evidence(
            [{**role, "field": derived_or_content_field}],
            allowed_terms=set(),
            controlled_specs=[locked],
        ) == []
