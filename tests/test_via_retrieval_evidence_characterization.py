from __future__ import annotations

from app.services.via.session_reward import _build_retrieval_evidence


def test_retrieval_evidence_preserves_source_mix_scores_and_rerank_payload() -> None:
    result = _build_retrieval_evidence(
        retrieval_execution={
            "plan": "hybrid_ranked",
            "retrieval_mode": "semantic",
            "vector_limit": 3,
            "fallback_order": ["bundle_memory", "vector_memory"],
        },
        retrieval_policy={},
        vector_refs=[
            {"source_ref": "seed:camera", "score": 0.8, "payload": {"summary": "Seed fact"}},
            {"source_ref": "via-vector:7", "weight": 0.4, "payload": {"summary": "Past turn"}},
        ],
        bundle_memory_refs=[{"source_ref": "memory:1"}, {"source_ref": "seed:catalog"}],
    )

    assert result == {
        "candidate_sources": ["bundle_memory", "vector_memory"],
        "selected_sources": ["bundle_memory", "vector_memory", "seed_knowledge"],
        "vector_hit_count": 2,
        "bundle_hit_count": 2,
        "seed_hit_count": 2,
        "vector_limit": 3,
        "top_score": 0.8,
        "avg_score": 0.6,
        "score_spread": 0.4,
        "rerank_applied": True,
        "rerank_summary": {
            "top_refs": [
                {"source_ref": "seed:camera", "score": 0.8, "summary": "Seed fact"},
                {"source_ref": "via-vector:7", "score": 0.4, "summary": "Past turn"},
            ],
            "vector_source_mix": {"seed": 1, "conversation": 1, "memory": 0},
        },
        "evidence_payload": {
            "retrieval_plan": "hybrid_ranked",
            "retrieval_mode": "semantic",
            "fallback_order": ["bundle_memory", "vector_memory"],
        },
    }
