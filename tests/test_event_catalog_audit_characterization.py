"""Frozen public-output characterizations for the Event catalog audit split."""
from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domains.events.radar_quality_audits import audit_event_catalog
from app.domains.events import (
    radar_quality_audits,
    radar_quality_event_opportunities,
    radar_quality_event_sources,
)


AS_OF = datetime(2026, 7, 13, 20, tzinfo=timezone.utc)
CHECKED_AT = "2026-07-13T18:00:00Z"


def _valid_catalog() -> dict:
    return {
        "global_complete": False,
        "coverage_claim": "registered_publisher_owned_public_entries_only",
        "checked_at": CHECKED_AT,
        "sources": [
            {
                "id": "event_source_example",
                "source_kind": "major_expo",
                "canonical_url": "https://events.example/",
                "country_code": "US",
                "timezone": "America/New_York",
                "status": "active",
                "enabled": True,
                "source_checked_at": CHECKED_AT,
                "reviewer_id": "staff_7",
                "evidence_scope": "event_source_listing",
                "value_status": "observed",
            }
        ],
        "opportunities": [
            {
                "id": "opp_example_20260720",
                "canonical_key": "example|2026-07-20|new-york",
                "source_id": "event_source_example",
                "external_event_key": "example-2026-07-20",
                "lane": "major_expo",
                "title": "Example Camera Event",
                "start_date": "2026-07-20",
                "end_date": "2026-07-20",
                "date_precision": "date",
                "timezone": "America/New_York",
                "country_code": "US",
                "official_url": "https://events.example/example-2026-07-20",
                "event_status": "scheduled",
                "verification_status": "verified",
                "source_checked_at": CHECKED_AT,
                "reviewer_id": "staff_7",
                "evidence_scope": "event_official_listing",
                "value_status": "observed",
                "viltrox_presence_status": "unknown",
            }
        ],
    }


def _invalid_catalog() -> dict:
    return {
        "global_complete": True,
        "coverage_claim": "global",
        "checked_at": "bad",
        "sources": [
            "invalid",
            {
                "id": "bad id",
                "source_kind": "bogus",
                "canonical_url": "http://bad",
                "country_code": "usa",
                "timezone": "Mars/Base",
                "status": "active",
                "enabled": False,
                "source_checked_at": None,
                "reviewer_id": "!",
                "evidence_scope": "wrong",
                "value_status": "unknown",
            },
        ],
        "opportunities": [
            "invalid",
            {
                "id": "",
                "canonical_key": "",
                "source_id": "missing",
                "external_event_key": "",
                "lane": "brand_event",
                "country_code": "us",
                "timezone": "Mars/Base",
                "official_url": "http://bad",
                "event_status": "made_up",
                "date_precision": "bad",
                "start_date": "20260720",
                "end_date": "2026-07-19",
                "verification_status": "verified",
                "source_checked_at": None,
                "reviewer_id": "!",
                "evidence_scope": "wrong",
                "value_status": "unknown",
                "viltrox_presence_status": "confirmed_exhibitor",
                "dealer_stable_location_key": "bad",
                "authorized": True,
            },
        ],
    }


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _branch_complexities(module) -> dict[str, int]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    decisions = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.Try,
        ast.TryStar,
        ast.BoolOp,
        ast.IfExp,
        ast.comprehension,
        ast.Match,
        ast.ExceptHandler,
        ast.match_case,
    )
    result: dict[str, int] = {}
    for function in (
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        score = 1
        for node in ast.walk(function):
            if isinstance(node, ast.BoolOp):
                score += len(node.values) - 1
            elif isinstance(node, decisions):
                score += 1
        result[function.name] = score
    return result


@pytest.mark.parametrize(
    ("catalog_factory", "kwargs", "expected_digest"),
    [
        (
            _valid_catalog,
            {},
            "181796e5ea5261b5ddb2176c37b27d597afec38f071cbd9707c2ad12674887c0",
        ),
        (
            _invalid_catalog,
            {
                "known_source_universe_denominator": 0,
                "reviewed_dealer_location_keys": ["dealer_loc_aaaaaaaa"],
            },
            "69bd2f722f7fa83c9cd4ce0e42a210d39392622a6e84596e0bdbe89748e2cdf2",
        ),
    ],
)
def test_audit_event_catalog_keeps_frozen_serialized_output(
    catalog_factory, kwargs: dict, expected_digest: str
) -> None:
    catalog = catalog_factory()
    before = deepcopy(catalog)

    report = audit_event_catalog(catalog, as_of=AS_OF, **kwargs)

    assert _digest(report) == expected_digest
    assert catalog == before
    assert report["claim_status"] == "descriptive_only"
    assert report["read_only"] is True
    assert report["network_accessed"] is False
    assert report["database_accessed"] is False
    assert report["business_rows_written"] == 0


def test_event_audit_split_keeps_branch_complexity_bounded() -> None:
    public_scores = _branch_complexities(radar_quality_audits)
    helper_scores = {
        **_branch_complexities(radar_quality_event_sources),
        **_branch_complexities(radar_quality_event_opportunities),
    }

    assert public_scores["audit_event_catalog"] <= 20
    assert max(helper_scores.values()) <= 25
