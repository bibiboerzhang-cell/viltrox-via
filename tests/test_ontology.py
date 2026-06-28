"""Tests for the unified Marketing Brain ontology from_row mappings.

Run: PYTHONPATH=backend pytest tests/test_ontology.py
"""

import json
import sqlite3

from app.domains.marketing_brain import ontology as onto


def _sqlite_row(columns: dict) -> sqlite3.Row:
    """Build a real sqlite3.Row so we exercise the non-Mapping dict() path."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = list(columns.keys())
    placeholders = ", ".join("?" for _ in cols)
    select = ", ".join(f"? AS {c}" for c in cols)
    # values bound twice is awkward; simpler: build a temp table.
    conn.execute(f"CREATE TABLE t ({', '.join(c + ' TEXT' for c in cols)})")
    conn.execute(
        f"INSERT INTO t ({', '.join(cols)}) VALUES ({placeholders})",
        [columns[c] for c in cols],
    )
    row = conn.execute("SELECT * FROM t").fetchone()
    return row


# --------------------------------------------------------------------------- #
# Creator <- vkpi_kol_pool
# --------------------------------------------------------------------------- #
def test_creator_from_dict_row():
    row = {
        "id": 7,
        "pool_uid": "pool-7",
        "platform": "youtube",
        "handle": "lens_guy",
        "display_name": "Lens Guy",
        "email": "x@y.com",
        "other_contacts_json": '["wechat:abc"]',
        "followers": "120000",
        "engagement_rate": "0.0425",
        "secondary_topics_json": '["photo","gear"]',
        "audience_estimated_json": '{"geo": ["US"]}',
        "viltrox_fit_score": "73.5",
        "recommended_product_lines_json": '["AF 85mm"]',
        "sync_status": "synced",
    }
    c = onto.Creator.from_row(row)
    assert c.id == 7
    assert c.pool_uid == "pool-7"
    assert c.platform == "youtube"
    assert c.handle == "lens_guy"
    assert c.followers == 120000
    assert abs(c.engagement_rate - 0.0425) < 1e-9
    assert c.other_contacts == ["wechat:abc"]
    assert c.secondary_topics == ["photo", "gear"]
    assert c.audience_estimated == {"geo": ["US"]}
    # readonly mirror only -- ontology never recomputes the score
    assert abs(c.viltrox_fit_score - 73.5) < 1e-9
    assert c.recommended_product_lines == ["AF 85mm"]
    assert c.sync_status == "synced"


def test_creator_from_sqlite_row_non_mapping():
    row = _sqlite_row(
        {
            "id": "3",
            "platform": "instagram",
            "handle": "shooter",
            "other_contacts_json": "[]",
            "secondary_topics_json": "[]",
            "audience_estimated_json": "{}",
        }
    )
    c = onto.Creator.from_row(row)
    assert c.id == 3
    assert c.platform == "instagram"
    assert c.handle == "shooter"
    assert c.other_contacts == []
    assert c.audience_estimated == {}


def test_creator_defaults_on_none_and_missing():
    c = onto.Creator.from_row(None)
    assert c.id is None
    assert c.platform == ""
    assert c.followers is None
    assert c.other_contacts == []
    assert c.sync_status == "imported"


# --------------------------------------------------------------------------- #
# Product <- vkpi_product_launches (+ cost catalog)
# --------------------------------------------------------------------------- #
def test_product_from_row():
    row = {
        "id": 11,
        "launch_uid": "lx-11",
        "name": "AF 35mm Launch",
        "product_sku": "AF35",
        "category": "lens",
        "target_platforms_json": '["youtube","tiktok"]',
        "target_audience_json": '{"age_bands": ["25-34"]}',
        "goals_json": '{"views": 1000000}',
        "status": "active",
        "unit_cost_cents": "8500",
        "currency": "USD",
    }
    p = onto.Product.from_row(row)
    assert p.id == 11
    assert p.launch_uid == "lx-11"
    assert p.product_sku == "AF35"
    assert p.target_platforms == ["youtube", "tiktok"]
    assert p.target_audience == {"age_bands": ["25-34"]}
    assert p.goals == {"views": 1000000}
    assert p.status == "active"
    assert p.unit_cost_cents == 8500


# --------------------------------------------------------------------------- #
# Content <- vkpi_content_posts (BOOLEAN int-1/0 trap)
# --------------------------------------------------------------------------- #
def test_content_from_row_boolean_int_trap():
    row = {
        "id": 5,
        "project_id": 2,
        "kol_id": 9,
        "platform": "youtube",
        "post_url": "https://yt/v/abc",
        "views": "10000",
        "likes": "500",
        "ad_usage_allowed": 1,  # compat returns int 1, not Python True
        "metadata_json": '{"campaign":"spring"}',
    }
    c = onto.Content.from_row(row)
    assert c.id == 5
    assert c.views == 10000
    assert c.likes == 500
    assert c.ad_usage_allowed is True
    assert c.metadata == {"campaign": "spring"}

    row0 = dict(row)
    row0["ad_usage_allowed"] = 0
    assert onto.Content.from_row(row0).ad_usage_allowed is False


# --------------------------------------------------------------------------- #
# Campaign <- project / event
# --------------------------------------------------------------------------- #
def test_campaign_from_project_row():
    row = {
        "id": 4,
        "project_uid": "pj-4",
        "name": "Brand Monitor",
        "project_type": "brand_monitor",
        "status": "running",
        "owner_staff_id": 2,
        "is_active": 1,  # BOOLEAN int trap
        "metadata_json": "{}",
    }
    c = onto.Campaign.from_row(row)
    assert c.kind == "project"
    assert c.id == 4
    assert c.uid == "pj-4"
    assert c.project_type == "brand_monitor"
    assert c.is_active is True


def test_campaign_from_event_row_varchar_id():
    row = {
        "id": "evt_abc",
        "title": "Photokina",
        "type_key": "tradeshow",
        "status": "planning",
        "owner_id": 6,
        "start_date": "2026-09-01",
        "end_date": "2026-09-05",
        "budget_total": "50000",
        "team_ids": "[1, 2]",
        "related_project_ids": "[]",
        "budget_json": "{}",
    }
    c = onto.Campaign.from_event_row(row)
    assert c.kind == "event"
    assert c.id == "evt_abc"
    assert c.uid == "evt_abc"
    assert c.name == "Photokina"
    assert c.budget_total == 50000
    assert c.team_ids == [1, 2]


# --------------------------------------------------------------------------- #
# Result <- event / content
# --------------------------------------------------------------------------- #
def test_result_from_event_row():
    row = {
        "id": "evt_1",
        "roi": "2.5",
        "leads": "40",
        "videos": "12",
        "health_score": "88",
        "retrospective": "great",
    }
    r = onto.Result.from_row(row)
    assert r.subject_kind == "event"
    assert r.subject_id == "evt_1"
    assert abs(r.roi - 2.5) < 1e-9
    assert r.leads == 40
    assert r.videos == 12
    assert r.health_score == 88


def test_result_from_content_row():
    r = onto.Result.from_content_row({"id": 9, "views": 100, "likes": 10})
    assert r.subject_kind == "content"
    assert r.subject_id == 9
    assert r.views == 100
    assert r.likes == 10


# --------------------------------------------------------------------------- #
# Evidence <- message / shipment
# --------------------------------------------------------------------------- #
def test_evidence_from_message_row():
    row = {
        "id": 3,
        "project_id": 1,
        "kol_id": 8,
        "evidence_url": "https://img/proof.png",
        "source": "manual",
        "direction": "outbound",
        "snippet": "hi",
        "metadata_json": '{"k":"v"}',
    }
    e = onto.Evidence.from_row(row)
    assert e.kind == "message"
    assert e.id == 3
    assert e.project_id == 1
    assert e.evidence_url == "https://img/proof.png"
    assert e.snippet == "hi"
    assert e.metadata == {"k": "v"}


def test_evidence_from_shipment_row():
    e = onto.Evidence.from_shipment_row(
        {"id": 2, "project_id": 1, "evidence_url": "u", "status": "delivered"}
    )
    assert e.kind == "shipment"
    assert e.status == "delivered"


# --------------------------------------------------------------------------- #
# Audience / Brand
# --------------------------------------------------------------------------- #
def test_audience_from_creator_blob():
    row = {"audience_estimated_json": '{"geo": ["US","DE"], "label": "photogs"}'}
    a = onto.Audience.from_row(row)
    assert a.geo == ["US", "DE"]
    assert a.label == "photogs"


def test_brand_from_brand_link_row():
    b = onto.Brand.from_row({"brand": "Sony", "metadata_json": '{"tier":"A"}'})
    assert b.name == "Sony"
    assert b.metadata == {"tier": "A"}


# --------------------------------------------------------------------------- #
# to_dict round-trips are JSON-serializable (no DB objects leak)
# --------------------------------------------------------------------------- #
def test_to_dict_is_json_serializable():
    c = onto.Creator.from_row({"id": 1, "handle": "h", "platform": "p"})
    d = c.to_dict()
    json.dumps(d)  # must not raise
    assert d["handle"] == "h"
