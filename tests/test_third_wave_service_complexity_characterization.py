"""Behavior locks for the final service-layer complexity extractions."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.ai import orchestrator
from app.services.ai.orchestrator_analyzers import VideoJobInput, VideoTask
from app.services.via import knowledge_seed
from app.services.via.session_reply_outcome_helpers import record_promotion_controls


def test_product_line_docx_parser_preserves_merge_and_mtime_cache(monkeypatch, tmp_path) -> None:
    path = tmp_path / "product-lines.docx"
    path.write_bytes(b"not-read-by-characterization")
    knowledge_seed._DOCX_PRODUCT_LINE_CACHE.clear()
    monkeypatch.setattr(
        knowledge_seed,
        "_external_product_line_catalog",
        lambda: {
            "LAB": {
                "name": "LAB official",
                "summary": "official summary",
                "models": ["official model"],
                "notes": [],
            }
        },
    )
    monkeypatch.setattr(knowledge_seed, "_workspace_docx_candidates", lambda: [path])
    calls: list[str] = []

    def extract(_path) -> list[str]:
        calls.append("extract")
        return [
            "LAB",
            "LAB 系列镜头",
            "AF 35mm F1.2 LAB",
            "（尼康 Z）",
            "高端自动对焦定焦系列",
        ]

    monkeypatch.setattr(knowledge_seed, "_extract_docx_lines", extract)
    first = knowledge_seed.extract_workspace_docx_product_line_catalog()
    second = knowledge_seed.extract_workspace_docx_product_line_catalog()

    assert calls == ["extract"]
    assert first["LAB"] == {
        "name": "LAB official",
        "summary": "official summary",
        "models": ["official model", "AF 35mm F1.2 LAB （尼康 Z）"],
        "notes": ["高端自动对焦定焦系列"],
    }
    # Existing cache-hit semantics replay the raw DOCX catalog over the fresh
    # external baseline; lock that behavior instead of silently normalizing it.
    assert second["LAB"] == {
        "name": "LAB",
        "summary": "LAB 系列镜头",
        "models": ["AF 35mm F1.2 LAB （尼康 Z）"],
        "notes": ["高端自动对焦定焦系列"],
    }


class _Conn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.calls.append((sql, params))

    def commit(self) -> None:
        self.commits += 1


def test_dbwriter_preserves_provider_precedence_scoring_and_commit(monkeypatch) -> None:
    conn = _Conn()
    callback_events: list[Any] = []
    writer = orchestrator.DBWriter(
        get_conn_fn=lambda: conn,
        compute_weighted_fn=lambda quality, genre, vertical: {
            "tech_status": "good",
            "tech_score": 80,
            "marketing_score": 70,
            "quality_overall": 75,
        },
        update_benchmark_fn=lambda genre, tech, marketing: {
            "percentile_tech": 91,
            "percentile_mkt": 82,
        },
        get_vertical_fn=lambda genre: callback_events.append(("vertical", genre)) or "lens",
        apply_learned_weights_fn=lambda key: callback_events.append(("weights", key)),
    )
    from app.services.audit import similarity
    from app.services.rewards import points
    from app.services.scoring import campaign

    monkeypatch.setattr(
        similarity,
        "classify_product",
        lambda _text: {"series": "LAB", "label": "LAB 35mm", "confidence": "high"},
    )
    monkeypatch.setattr(campaign, "compute_creator_score", lambda *_args: 20)
    monkeypatch.setattr(campaign, "compute_campaign_score", lambda *_args: {"raw_score": 100})
    monkeypatch.setattr(
        points,
        "auto_award_points",
        lambda submission_id, handle, score: callback_events.append(
            ("points", submission_id, handle, score)
        ) or {"points": 5},
    )
    task = VideoTask(
        task_id="task-1",
        job=VideoJobInput(
            submission_id=77,
            url="https://example.test/video",
            handle="creator",
            hints={"logo": True},
            metrics={"views": 1000, "likes": 100, "comments": 20, "shares": 5, "favorites": 3},
        ),
        results={
            "gemini": {
                "ok": True,
                "payload": {
                    "content_genre": "review",
                    "vertical_category": "camera",
                    "quality_scores": {},
                    "viltrox_detected": True,
                    "confidence": "high",
                    "products_detected": ["Viltrox LAB 35mm"],
                    "viltrox_products_all": [],
                    "brand_elements": ["logo"],
                    "content_types": ["review"],
                    "notes": "gemini wins",
                },
            },
            "claude": {"ok": True, "payload": {"notes": "must not overwrite", "storytelling_score": 9}},
        },
    )

    writer.write(task)

    assert conn.commits == 1
    assert len(conn.calls) == 1
    params = conn.calls[0][1]
    stored_analysis = json.loads(params[0])
    assert stored_analysis["notes"] == "gemini wins"
    assert stored_analysis["storytelling_score"] == 9
    assert params[12:20] == (
        "confirmed",
        "LAB",
        "LAB 35mm",
        115,
        20,
        26,
        0,
        "Eligible for brand campaign pool",
    )
    assert params[23:] == (1000, 100, 20, 5, 3, 77)
    assert callback_events == [
        ("vertical", "review"),
        ("weights", "lens"),
        ("points", 77, "creator", 115),
    ]


def test_promotion_control_keeps_decision_visible_when_outcome_write_fails() -> None:
    decisions: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    events: list[str] = []

    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def insert_decision(**kwargs):
        events.append("decision")
        return {"decision_id": "decision-1", **kwargs}

    def insert_outcome(**_kwargs):
        events.append("outcome")
        raise RuntimeError("outcome unavailable")

    async def run() -> None:
        await record_promotion_controls(
            {"tier": "l2", "memory_kind": "preference", "fact_key": "camera", "persisted_ref_id": 9},
            session_key="session-1",
            session={"id": 7, "user_id": 8},
            persona={"id": 9},
            policy_route={},
            trigger_snapshot={"state_snapshot": {}},
            context_refs=[],
            reply_outcome={"reward_score": 1.0},
            primary_outcome={"created_at": "2026-08-31T00:00:00Z"},
            control_source_ref="control:1",
            decision_records=decisions,
            outcome_records=outcomes,
            to_thread=inline,
            upsert_retention=lambda **_kwargs: events.append("retention"),
            get_policy=lambda *_args, **_kwargs: {"policy_key": "memory", "policy_version": "v1"},
            build_candidates=lambda *_args, **_kwargs: [],
            insert_decision=insert_decision,
            insert_outcome=insert_outcome,
        )

    with pytest.raises(RuntimeError, match="outcome unavailable"):
        import asyncio

        asyncio.run(run())
    assert events == ["retention", "decision", "outcome"]
    assert len(decisions) == 1
    assert outcomes == []
