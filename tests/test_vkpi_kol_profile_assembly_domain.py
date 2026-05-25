from app.domains.kol import profile_assembly


def test_profile_assembly_timeline_sorts_and_limits():
    timeline = profile_assembly.build_activity_timeline(
        claim_history=[{"claimed_at": "2026-01-01", "status": "active"}],
        projects=[{"updated_at": "2026-01-03", "project_name": "P"}],
        messages=[{"captured_at": "2026-01-02", "snippet": "msg"}],
        content_posts=[],
        sales=[],
        kpi_ledger=[],
        recommendation_outcomes=[],
        link_clicks=[],
        audit_events=[],
    )

    assert [item["type"] for item in timeline] == ["project", "message", "claim"]


def test_profile_assembly_link_summary_counts_unique_orders():
    summary = profile_assembly.build_link_summary(
        links=[{"click_count": "5", "valid_click_count": "4", "bot_click_count": "1"}],
        link_clicks=[{"is_unique": 1}, {"is_unique": 0}],
        link_orders=[
            {"source_ref": "A", "revenue_cents": "100"},
            {"source_ref": "A", "revenue_cents": "50"},
            {"shopify_order_id": "B", "revenue_cents": "25"},
        ],
    )

    assert summary == {
        "link_count": 1,
        "click_count": 5,
        "valid_click_count": 4,
        "bot_click_count": 1,
        "unique_click_count": 1,
        "order_count": 2,
        "revenue_cents": 175,
    }


def test_profile_assembly_contacts_and_summary_hide_financials():
    contacts = profile_assembly.build_contacts(
        {"contact_email": "a@example.com", "profile_url": "https://x"},
        contact_links={"not": "list"},
        contact_raw=[],
    )
    summary = profile_assembly.build_profile_summary(
        snapshot={"follower_count": "200", "content_count": "3", "avg_views": "100"},
        kol={"follower_count": "50", "avg_views": "20"},
        posts=[{"id": 1}],
        report={"account_score": "80", "recommended_action": "watch"},
        raw_report={"persona": "creator"},
        revenue_cents=500,
        cost_cents=100,
        show_financials=False,
        projects=[{}],
        links=[{}],
        link_clicks=[{}],
        link_orders=[],
        messages=[],
        content_posts=[{}],
        claim_history=[{}],
        kpi_ledger=[],
        kpi_summary=[],
        recommendations=[],
        recommendation_outcomes=[],
    )

    assert contacts == {"email": "a@example.com", "phone": "", "profile_url": "https://x", "links": [], "raw": {}}
    assert summary["follower_count"] == 200
    assert summary["financials_hidden"] is True
    assert summary["cost_cents"] is None
    assert summary["roi"] is None


def test_profile_assembly_kpi_summary_groups_metrics():
    summary = profile_assembly.build_kpi_summary(
        [
            {"metric_key": "views", "metric_value": "10", "ledger_date": "2026-01-01", "source_ref": "a"},
            {"metric_key": "views", "metric_value": "5.5", "ledger_date": "2026-01-02", "source_ref": "b"},
            {"metric_key": "sales", "metric_value": "bad", "ledger_date": "2026-01-01", "source_ref": "c"},
        ]
    )

    assert summary == [
        {"metric_key": "sales", "total_value": 0.0, "row_count": 1, "latest_ledger_date": "2026-01-01", "latest_source_ref": "c"},
        {"metric_key": "views", "total_value": 15.5, "row_count": 2, "latest_ledger_date": "2026-01-02", "latest_source_ref": "b"},
    ]
