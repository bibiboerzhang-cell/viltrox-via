from __future__ import annotations

import ast
import builtins
import copy
import inspect
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domains.intelligence.raw_market_source import (
    validate_raw_market_source_artifact,
)
from scripts.vkpi_engineering_health_collect import collect_complexity


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "backend/app/domains/intelligence/raw_market_source.py"


def _artifact(
    *,
    generated_at: datetime = NOW,
    sources_requested: int | str = 2,
    sources_fetched: int | str = 2,
    items_loaded: int | str = 3,
) -> dict:
    requested_count = int(sources_requested)
    fetched_count = int(sources_fetched)
    item_count = int(items_loaded)
    return {
        "mode": "market_external_signal_smoke_v0",
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "provider_calls": True,
        "external_http_calls": True,
        "llm_calls": False,
        "gemini_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": True,
        "checks": {
            "no_db_write": True,
            "no_sync_triggered": True,
            "no_llm_call": True,
            "allowlisted_sources_only": True,
            "live_fetch_returned_items": True,
        },
        "summary": {
            "sources_requested": sources_requested,
            "sources_fetched": sources_fetched,
            "items_loaded": items_loaded,
        },
        "source_statuses": [
            {
                "source_key": f"source-{index}",
                "provider": "rss",
                "source_type": "rss_feed",
                "url": f"https://feeds.example.com/{index}",
                "allowlisted": True,
                "status": "fetched" if index < fetched_count else "empty",
            }
            for index in range(requested_count)
        ],
        "items": [
            {
                "source_uid": f"external:{index}",
                "source_key": f"source-{index % fetched_count}",
                "provider": "rss",
                "source_type": "rss_feed",
                "source_url": f"https://news.example.com/items/{index}",
            }
            for index in range(item_count)
        ],
        "errors": [],
    }


def test_valid_artifact_return_shape_values_and_key_order_are_frozen(tmp_path: Path) -> None:
    artifact_path = tmp_path / "raw-market.json"

    result = validate_raw_market_source_artifact(
        _artifact(),
        artifact_path=artifact_path,
        now=NOW,
    )

    assert result == {
        "status": "validated",
        "validated": True,
        "observed": True,
        "evidence_score": 0.322,
        "artifact_path": str(artifact_path.resolve()),
        "generated_at": "2026-08-29T12:00:00+00:00",
        "age_days": 0.0,
        "max_age_days": 7,
        "sources_requested": 2,
        "sources_fetched": 2,
        "items_loaded": 3,
        "source_fetch_coverage": 1.0,
        "source_url_coverage": 1.0,
        "source_provenance_coverage": 1.0,
        "item_url_coverage": 1.0,
        "item_provenance_coverage": 1.0,
        "blockers": [],
        "policy": {
            "read_only": True,
            "counts_as_raw_market_source_only": True,
            "counts_as_promoted_competitor_signal": False,
            "counts_as_market_mention": False,
            "counts_as_outcome": False,
        },
    }
    assert list(result) == [
        "status",
        "validated",
        "observed",
        "evidence_score",
        "artifact_path",
        "generated_at",
        "age_days",
        "max_age_days",
        "sources_requested",
        "sources_fetched",
        "items_loaded",
        "source_fetch_coverage",
        "source_url_coverage",
        "source_provenance_coverage",
        "item_url_coverage",
        "item_provenance_coverage",
        "blockers",
        "policy",
    ]


def test_non_object_rejection_contract_is_frozen(tmp_path: Path) -> None:
    artifact_path = tmp_path / "not-created.json"

    result = validate_raw_market_source_artifact(
        ["not", "an", "object"],
        artifact_path=artifact_path,
        now=NOW,
    )

    assert result == {
        "status": "rejected",
        "validated": False,
        "observed": False,
        "evidence_score": 0.0,
        "artifact_path": str(artifact_path.resolve()),
        "generated_at": None,
        "age_days": None,
        "max_age_days": 7,
        "sources_requested": 0,
        "sources_fetched": 0,
        "items_loaded": 0,
        "source_fetch_coverage": 0.0,
        "source_url_coverage": 0.0,
        "source_provenance_coverage": 0.0,
        "item_url_coverage": 0.0,
        "item_provenance_coverage": 0.0,
        "blockers": ["payload_not_object"],
        "policy": {
            "read_only": True,
            "counts_as_raw_market_source_only": True,
            "counts_as_promoted_competitor_signal": False,
            "counts_as_market_mention": False,
            "counts_as_outcome": False,
        },
    }


def test_all_contract_row_and_coverage_blockers_keep_exact_order() -> None:
    payload = {
        "mode": "wrong",
        "passed": False,
        "is_demo": True,
        "provider_calls": False,
        "external_http_calls": None,
        "llm_calls": True,
        "gemini_calls": True,
        "write_db": True,
        "sync_triggered": True,
        "task_enqueued": True,
        "checks": {},
        "errors": ["provider failed"],
        "generated_at": "not-a-timestamp",
        "summary": {
            "sources_requested": 0,
            "sources_fetched": 0,
            "items_loaded": 0,
        },
        "source_statuses": [
            {
                "status": "fetched",
                "source_key": "",
                "provider": "",
                "source_type": "",
                "url": "",
            },
            "not-a-row",
        ],
        "items": [
            {
                "source_uid": "",
                "source_key": "",
                "provider": "",
                "source_type": "",
                "source_url": "",
            },
            None,
        ],
    }

    result = validate_raw_market_source_artifact(payload, now=NOW)

    assert result["status"] == "rejected"
    assert result["blockers"] == [
        "contract:mode",
        "contract:passed",
        "contract:demo_or_synthetic",
        "contract:provider_calls",
        "contract:external_http_calls",
        "side_effect:llm_calls",
        "side_effect:gemini_calls",
        "side_effect:write_db",
        "side_effect:sync_triggered",
        "side_effect:task_enqueued",
        "contract_check:no_db_write",
        "contract_check:no_sync_triggered",
        "contract_check:no_llm_call",
        "contract_check:allowlisted_sources_only",
        "contract_check:live_fetch_returned_items",
        "contract:errors_present",
        "generated_at:invalid",
        "sources_requested:nonpositive",
        "sources_fetched:nonpositive",
        "items_loaded:nonpositive",
        "source_statuses:malformed",
        "items:malformed",
        "source_statuses:count_mismatch",
        "source_statuses:fetched_count_mismatch",
        "items:count_mismatch",
        "source_statuses:source_key_not_unique",
        "items:source_uid_not_unique",
        "coverage:source_url<1",
        "coverage:source_provenance<1",
        "coverage:item_url<1",
        "coverage:item_provenance<1",
    ]
    assert result["generated_at"] is None
    assert result["age_days"] is None
    assert result["sources_requested"] == 0
    assert result["sources_fetched"] == 0
    assert result["items_loaded"] == 0
    assert result["source_fetch_coverage"] == 0.0
    assert result["source_url_coverage"] == 0.0
    assert result["source_provenance_coverage"] == 0.0
    assert result["item_url_coverage"] == 0.0
    assert result["item_provenance_coverage"] == 0.0


def test_stale_status_is_reserved_for_the_single_stale_blocker() -> None:
    stale = _artifact(generated_at=NOW - timedelta(days=4))

    stale_only = validate_raw_market_source_artifact(
        stale,
        now=NOW,
        max_age_days=3,
    )
    stale["passed"] = False
    stale_and_invalid = validate_raw_market_source_artifact(
        stale,
        now=NOW,
        max_age_days=3,
    )

    assert stale_only["status"] == "stale"
    assert stale_only["blockers"] == ["generated_at:stale>3d"]
    assert stale_only["age_days"] == 4.0
    assert stale_only["max_age_days"] == 3
    assert stale_and_invalid["status"] == "rejected"
    assert stale_and_invalid["blockers"] == [
        "contract:passed",
        "generated_at:stale>3d",
    ]


def test_future_skew_boundary_and_count_coercion_are_frozen() -> None:
    at_boundary = _artifact(
        generated_at=NOW + timedelta(seconds=300),
        sources_requested="2",
        sources_fetched="1",
        items_loaded="1",
    )
    beyond_boundary = _artifact(generated_at=NOW + timedelta(seconds=301))

    accepted = validate_raw_market_source_artifact(at_boundary, now=NOW)
    rejected = validate_raw_market_source_artifact(beyond_boundary, now=NOW)

    assert accepted["status"] == "validated"
    assert accepted["blockers"] == []
    assert accepted["age_days"] == -0.003
    assert accepted["sources_requested"] == 2
    assert accepted["sources_fetched"] == 1
    assert accepted["items_loaded"] == 1
    assert accepted["source_fetch_coverage"] == 0.5
    assert accepted["evidence_score"] == 0.156
    assert rejected["status"] == "rejected"
    assert rejected["blockers"] == ["generated_at:future"]
    assert rejected["age_days"] == -0.003


def test_excess_fetch_count_precedes_row_count_mismatch() -> None:
    payload = _artifact()
    payload["summary"]["sources_requested"] = 1

    result = validate_raw_market_source_artifact(payload, now=NOW)

    assert result["blockers"] == [
        "sources_fetched:exceeds_requested",
        "source_statuses:count_mismatch",
    ]


def test_validation_does_not_mutate_input_or_perform_file_or_network_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _artifact()
    original = copy.deepcopy(payload)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("validation attempted file or network I/O")

    monkeypatch.setattr(builtins, "open", fail)
    monkeypatch.setattr(Path, "open", fail)
    monkeypatch.setattr(Path, "read_text", fail)
    monkeypatch.setattr(Path, "write_text", fail)
    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(socket, "create_connection", fail)

    first = validate_raw_market_source_artifact(payload, now=NOW)
    first["policy"]["read_only"] = False
    second = validate_raw_market_source_artifact(payload, now=NOW)

    assert payload == original
    assert second["policy"]["read_only"] is True


def test_public_signature_complexity_and_module_size_stay_bounded() -> None:
    signature = inspect.signature(validate_raw_market_source_artifact)
    parameters = list(signature.parameters.values())
    assert [(parameter.name, parameter.kind) for parameter in parameters] == [
        ("payload", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("artifact_path", inspect.Parameter.KEYWORD_ONLY),
        ("now", inspect.Parameter.KEYWORD_ONLY),
        ("max_age_days", inspect.Parameter.KEYWORD_ONLY),
    ]
    assert parameters[1].default is None
    assert parameters[2].default is None
    assert parameters[3].default == 7

    source = MODULE_PATH.read_text(encoding="utf-8")
    rows = collect_complexity({str(MODULE_PATH): ast.parse(source)})
    focal = next(
        row
        for row in rows
        if row.qualified_name == "validate_raw_market_source_artifact"
    )

    assert focal.cc <= 20
    assert max(row.cc for row in rows) <= 20
    assert len(source.splitlines()) <= 1000
