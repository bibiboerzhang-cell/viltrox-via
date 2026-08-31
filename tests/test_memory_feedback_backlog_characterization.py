from __future__ import annotations

from app.domains.memory.feedback_backlog import _entity_backlog_item


def _entity(facts: dict) -> dict:
    return {
        "entity_id": 7,
        "entity_uid": "kol:7",
        "entity_type": "kol",
        "identity_key": "youtube:test",
        "display_name": "Test Creator",
        "entity_status": "active",
        "confidence_score": 0.75,
        "identity_json": '{"platform":"youtube"}',
        "metadata_json": '{"region":"US"}',
        "source_table": "kol_profiles",
        "source_id": "7",
        "updated_at": "2026-08-31T00:00:00Z",
        "facts": facts,
    }


def test_entity_backlog_item_preserves_priority_and_reason_order() -> None:
    result = _entity_backlog_item(
        _entity(
            {
                "risk_flag": [{"value": "manual_check"}],
                "sync_status": [{"value": "needs_human_review"}],
                "weak_label": [{"value": "profile_missing_review"}],
                "review_state": [],
                "contact_status": [{"value": "missing"}],
                "evidence_count": [{"value": "1"}],
            }
        ),
        {"total": 2, "open": 1, "closed": 1},
    )

    assert result is not None
    assert result["signals"] == {
        "sync_status": "needs_human_review",
        "weak_label": "profile_missing_review",
        "review_state": "",
        "contact_status": "missing",
        "risk_flags": [{"value": "manual_check"}],
        "evidence_count": 1,
    }
    assert result["suggestion"] == {
        "suggested_action": "review_risk_memory",
        "suggested_feedback_type": "risk_review",
        "priority_score": 235,
        "severity": "high",
        "reasons": [
            "risk_flag_or_risk_review",
            "needs_human_review",
            "profile_missing_review",
            "contact_missing",
            "low_evidence_count",
            "already_has_open_feedback",
        ],
        "write_allowed": False,
        "operator_note": "Create or resolve Memory feedback manually after reviewing the entity.",
    }


def test_entity_backlog_item_omits_clean_well_evidenced_entity() -> None:
    assert (
        _entity_backlog_item(
            _entity(
                {
                    "contact_status": [{"value": "known"}],
                    "evidence_count": [{"value": "2"}],
                }
            ),
            {"total": 0, "open": 0, "closed": 0},
        )
        is None
    )
