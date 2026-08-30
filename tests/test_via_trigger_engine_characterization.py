from __future__ import annotations

import ast
from pathlib import Path

from app.services.via.trigger_engine import build_via_trigger_snapshot
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/app/services/via/trigger_engine.py"


def test_trigger_snapshot_preserves_ordered_rich_control_signals() -> None:
    result = build_via_trigger_snapshot(
        {
            "session": {
                "session_key": "s",
                "user_id": 7,
                "state": {
                    "turn_count": 2,
                    "last_product_labels": ["Air"],
                    "last_business_intent": "buy",
                },
            },
            "persona": {"persona_key": "p"},
            "memory_refs": [{"id": 1}],
        },
        "Actually I prefer Viltrox Air; what about price and video?",
        current_surface="chat",
        route_info={
            "intent": "product",
            "brain": "deep_reasoning",
            "needs_memory": True,
            "use_deep_reasoning": True,
        },
        vector_refs=[{"id": 2}],
    )

    assert result["semantic"] == ["product", "deep_reasoning", "visual_query"]
    assert result["business"] == [
        "product",
        "purchase_intent",
        "official_product",
    ]
    assert result["learning"] == [
        "user_correction",
        "followup_question",
        "preference_signal",
        "memory_hit_available",
        "memory_hit_used",
    ]
    assert result["primary_trigger"] == "product"
    assert result["confidence_score"] == 0.9
    assert result["recommended_decisions"] == [
        "intent_route",
        "reply_mode",
        "retrieval_plan",
        "memory_promotion",
    ]


def test_trigger_snapshot_stays_below_the_v1_complexity_redline() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    rows = collect_complexity({str(SOURCE): ast.parse(source)})
    build = next(
        row for row in rows if row.qualified_name == "build_via_trigger_snapshot"
    )

    assert build.cc <= 30
    assert max(row.cc for row in rows) <= 30
    assert len(source.splitlines()) < 800
