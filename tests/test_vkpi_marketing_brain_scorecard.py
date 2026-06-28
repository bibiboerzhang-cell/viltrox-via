from app.domains.intelligence.marketing_brain_scorecard import _action_contract_from_rows


def test_action_contract_scores_complete_recommendation_rows():
    result = _action_contract_from_rows([
        {
            "expected_gain": "提升市场推荐质量",
            "risk_level": "low",
            "evidence_refs_json": [{"type": "market_signal", "id": "1"}],
            "verification_plan_json": ["执行后检查推荐状态"],
            "affected_tables_json": ["vkpi_kol_pool"],
            "writes_business_data": True,
            "uses_llm": False,
            "requires_approval": True,
        },
        {
            "expected_gain": "生成营销策略草案",
            "risk_level": "medium",
            "evidence_refs_json": [{"type": "competitor_brand", "id": "sony"}],
            "verification_plan_json": ["人工审阅后再执行"],
            "affected_tables_json": [],
            "writes_business_data": False,
            "uses_llm": True,
            "requires_approval": True,
        },
    ])

    assert result["score"] == 1.0
    assert result["checks"]["has_decision_fields"] is True
    assert result["checks"]["has_evidence_refs"] is True
    assert result["checks"]["has_verification_plan"] is True
    assert result["checks"]["write_or_llm_requires_approval"] is True
    assert result["checks"]["write_actions_have_affected_tables"] is True


def test_action_contract_penalizes_unexplainable_or_ungated_rows():
    result = _action_contract_from_rows([
        {
            "expected_gain": "",
            "risk_level": "",
            "evidence_refs_json": [],
            "verification_plan_json": [],
            "affected_tables_json": [],
            "writes_business_data": True,
            "uses_llm": True,
            "requires_approval": False,
        }
    ])

    assert result["score"] < 0.25
    assert result["checks"]["has_decision_fields"] is False
    assert result["checks"]["has_evidence_refs"] is False
    assert result["checks"]["has_verification_plan"] is False
    assert result["checks"]["write_or_llm_requires_approval"] is False
    assert result["checks"]["write_actions_have_affected_tables"] is False
