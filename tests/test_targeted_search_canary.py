from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from typing import Any

import pytest

from scripts.ops import smoke_vkpi_targeted_search_canary as canary
from app.domains.kol.targeted_search_contract import build_locked_term_groups


TABLE_COUNTS = {
    "vkpi_kol_pool": 1802,
    "vkpi_kol_search_sessions": 1144,
    "vkpi_kol_search_session_items": 3000,
    "vkpi_kol_video_evidence": 3322,
    "apify_jobs": 90,
    "vkpi_ai_cost_ledger": 44,
}


class _Row:
    def __init__(self, value: Any) -> None:
        self.value = value

    def __getitem__(self, key: int | str) -> Any:
        if key in (0, "value", "row_count"):
            return self.value
        raise KeyError(key)


class _Cursor:
    def __init__(self, value: Any) -> None:
        self.value = value

    def fetchone(self) -> _Row:
        return _Row(self.value)


class _FakeConn:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.rollback_count = 0

    def execute(self, sql: str, _params: Any = None) -> _Cursor:
        compact = " ".join(sql.split())
        self.queries.append(compact)
        if compact == "BEGIN TRANSACTION READ ONLY":
            return _Cursor(None)
        if compact == "SHOW transaction_read_only":
            return _Cursor("on")
        for table, count in TABLE_COUNTS.items():
            if compact == f"SELECT COUNT(*) AS row_count FROM {table}":
                return _Cursor(count)
        raise AssertionError(f"unexpected SQL: {compact}")

    def rollback(self) -> None:
        self.rollback_count += 1


def _planner(_query: str, *, body: dict[str, Any]) -> dict[str, Any]:
    assert body["objective"] == "prospective_growth"
    assert body["platforms"] == ["youtube"]
    return {
        "status": "ready",
        "objective": "prospective_growth",
        "resolved_product": {
            "sku": "VINTAGE-Z1-PRO-TTL-RETRO-ON-CAMERA-FLASH",
            "model_name": "Vintage Z1 Pro",
        },
        "search_brief": {
            "search_spec_version": "targeted_search_v2",
            "objective": "prospective_growth",
            "claim_status": "descriptive_only",
        },
        "query_cells": [
            {
                "query_cell_id": "cell-motorsport",
                "objective": "prospective_growth",
                "segment": "motorsport",
                "segment_label": "racing",
                "primary_query": "motorsport photographer on-camera flash",
                "platforms": ["youtube", "instagram"],
                "round": 1,
                "raw_limit": 15,
                "required_evidence_groups": ["product_use_fit", "market_activation"],
                "brand_or_model_required": False,
                "brand_or_model_ranking_weight": 0,
                "locked_term_groups": build_locked_term_groups(
                    capability="on-camera flash", segment="motorsport"
                ),
            },
            {
                "query_cell_id": "cell-food",
                "objective": "prospective_growth",
                "segment": "food",
                "segment_label": "food",
                "primary_query": "food photographer on-camera flash",
                "platforms": ["youtube", "instagram"],
                "round": 1,
                "raw_limit": 12,
                "required_evidence_groups": ["product_use_fit", "market_activation"],
                "brand_or_model_required": False,
                "brand_or_model_ranking_weight": 0,
            },
            {
                "query_cell_id": "cell-wedding",
                "objective": "prospective_growth",
                "segment": "wedding",
                "segment_label": "wedding",
                "primary_query": "wedding photographer on-camera flash",
                "platforms": ["youtube"],
                "round": 1,
                "raw_limit": 10,
            },
        ],
    }


def _args(*extra: str) -> Namespace:
    return canary.parse_args([
        "--query",
        "Z1 Pro 找赛车和餐饮创作者",
        "--database-url",
        "postgresql://local-user:local-password@127.0.0.1:54329/viltrox2",
        *extra,
    ])


def test_non_loopback_or_non_postgresql_database_is_rejected() -> None:
    for value in (
        "postgresql://db.example.com/viltrox2",
        "sqlite:///tmp/viltrox.db",
        "postgresql:///viltrox2",
        "postgresql://127.0.0.1/viltrox2?host=db.example.com",
        "postgresql://127.0.0.1/viltrox2?dbname=shadow",
    ):
        with pytest.raises(ValueError, match="loopback_postgresql_url_required"):
            canary.validate_loopback_database_url(value)


def test_forbidden_credentials_are_cleared_before_any_app_import() -> None:
    cli_source = canary.Path(canary.__file__).read_text(encoding="utf-8")
    support_source = (
        canary.Path(canary.__file__).with_name("targeted_search_canary_support.py")
        .read_text(encoding="utf-8")
    )
    run_body = cli_source.split("async def run_from_args", 1)[1]

    assert "from app" not in support_source
    assert "import app" not in support_source
    assert run_body.index("configure_runtime(database_url)") < run_body.index(
        "planner_fn = planner or _load_planner()"
    )
    assert 'conn.execute("BEGIN TRANSACTION READ ONLY")' in support_source


def test_plan_is_stable_bounded_and_authorization_hash_changes_with_scope() -> None:
    first = canary.build_canary_plan(
        query="Z1 Pro 找赛车和餐饮创作者",
        market="US",
        product_sku="",
        planner=_planner,
    )
    again = canary.build_canary_plan(
        query="Z1 Pro 找赛车和餐饮创作者",
        market="US",
        product_sku="",
        planner=_planner,
    )
    changed = canary.build_canary_plan(
        query="Z1 Pro 找赛车创作者",
        market="US",
        product_sku="",
        planner=_planner,
    )
    one_cell = canary.build_canary_plan(
        query="Z1 Pro 找赛车和餐饮创作者",
        market="US",
        product_sku="",
        planner=_planner,
        cell_count=1,
    )

    assert first["plan_hash"] == again["plan_hash"]
    assert first["plan_hash"] != changed["plan_hash"]
    assert first["plan_hash"] != one_cell["plan_hash"]
    assert len(one_cell["query_cells"]) == 1
    assert first["claim_status"] == "descriptive_only"
    assert first["platforms"] == ["youtube"]
    assert len(first["query_cells"]) == 2
    assert {cell["raw_limit"] for cell in first["query_cells"]} == {10}
    assert {tuple(cell["platforms"]) for cell in first["query_cells"]} == {("youtube",)}
    assert first["execution_limits"] == {
        "query_cells": 2,
        "raw_rows_per_cell": 10,
        "max_discovery_legs": 2,
        "max_youtube_search_calls": 2,
        "max_youtube_combined_quota_units": 4,
        "max_youtube_api_calls": 6,
    }
    locked = first["query_cells"][0]["locked_term_groups"]
    assert [group["kind"] for group in locked["groups"]] == ["product", "scene"]
    assert locked["groups"][0]["evidence_group"] == "product_use_fit"
    assert "speedlight" in locked["groups"][0]["aliases"]
    assert first["provider_policy"] == {
        "allowed": ["youtube_data_api"],
        "apify": "disabled",
        "llm": "disabled",
        "gemini": "disabled",
        "fallback_queries": "disabled",
        "auto_enroll": False,
    }


def test_default_dry_run_never_calls_provider_and_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn()
    calls: list[dict[str, Any]] = []

    async def discover(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        raise AssertionError("dry-run must not call provider")

    monkeypatch.setenv("APIFY_TOKEN", "must-be-cleared")
    monkeypatch.setenv("GEMINI_API_KEY", "must-be-cleared")
    monkeypatch.setenv("OPENAI_API_KEY", "must-be-cleared")
    report = asyncio.run(canary.run_from_args(
        _args(),
        planner=_planner,
        discover=discover,
        conn=conn,
    ))

    assert calls == []
    assert report["mode"] == "plan_only"
    assert report["execution"]["provider_calls"] is False
    assert report["execution"]["reason"] == "provider_calls_not_authorized"
    assert report["database"]["transaction"] == "read_only_rolled_back"
    assert report["database"]["counts_before"] == TABLE_COUNTS
    assert report["database"]["counts_after"] == TABLE_COUNTS
    assert report["database"]["mutations_detected"] is False
    assert conn.rollback_count == 1
    assert "BEGIN TRANSACTION READ ONLY" in conn.queries
    assert "SHOW transaction_read_only" in conn.queries
    assert canary.os.environ["APIFY_TOKEN"] == ""
    assert canary.os.environ["GEMINI_API_KEY"] == ""
    assert canary.os.environ["OPENAI_API_KEY"] == ""


@pytest.mark.parametrize(
    "extra",
    [
        ("--execute",),
        ("--allow-provider-calls",),
        ("--execute", "--allow-provider-calls"),
        ("--execute", "--allow-provider-calls", "--authorization", "stale-plan-hash"),
    ],
)
def test_live_requires_execute_allow_and_matching_plan_hash(
    monkeypatch: pytest.MonkeyPatch,
    extra: tuple[str, ...],
) -> None:
    conn = _FakeConn()
    calls: list[dict[str, Any]] = []

    async def discover(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {}

    monkeypatch.setenv("YOUTUBE_API_KEY", "configured-test-key")
    report = asyncio.run(canary.run_from_args(
        _args(*extra),
        planner=_planner,
        discover=discover,
        conn=conn,
    ))

    assert calls == []
    assert report["execution"]["provider_calls"] is False
    assert report["execution"]["authorized"] is False
    assert report["execution"]["reason"] in {
        "provider_calls_not_authorized",
        "authorization_missing",
        "authorization_plan_hash_mismatch",
    }
    assert conn.rollback_count == 1


def test_authorized_live_is_youtube_exact_readonly_and_dual_qualifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    planned = canary.build_canary_plan(
        query="Z1 Pro 找赛车和餐饮创作者",
        market="US",
        product_sku="",
        planner=_planner,
    )
    discovery_calls: list[dict[str, Any]] = []
    policies: list[dict[str, Any]] = []
    qualifications: list[dict[str, Any]] = []

    async def discover(**kwargs: Any) -> dict[str, Any]:
        discovery_calls.append(kwargs)
        return {
            "status": "ready",
            "platforms": ["youtube"],
            "new_creators": [{
                "platform": "youtube",
                "handle": "public_creator",
                "display_name": "Public Creator",
                "followers": 120_000,
                "profile_url": "https://www.youtube.com/@public_creator",
                "sample_title": "Using a flash at a race",
                "email": "private@example.com",
                "contact": {"phone": "+1-secret"},
                "api_key": "candidate-secret",
            }],
            "platform_results": [{
                "platform": "youtube",
                "status": "ready",
                "metadata": {
                    "provider": "youtube_data_api",
                    "youtube_search_calls": 1,
                    "youtube_combined_quota_units": 2,
                    "youtube_api_calls": 3,
                    "quota_units": 2,
                    "quota_units_deprecated": True,
                },
            }],
            "provider_calls": True,
        }

    def policy_builder(**kwargs: Any) -> dict[str, Any]:
        policies.append(kwargs)
        return {
            "platforms": kwargs["platforms"],
            "followers_min": kwargs.get("followers_min"),
            "followers_max": kwargs.get("followers_max"),
        }

    def qualify(candidates: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        qualifications.append({"candidates": candidates, **kwargs})
        return {
            "schema": "smart_online_net_new_qualified_v1",
            "accepted": candidates,
            "counts": {"selected": len(candidates)},
            "rejected_by_reason": {},
            "qualification_stats": {"unique_candidate_count": len(candidates)},
            "unique_candidate_count": len(candidates),
            "cell_evaluation_count": len(candidates),
        }

    monkeypatch.setenv("YOUTUBE_API_KEY", "configured-test-key")
    report = asyncio.run(canary.run_from_args(
        _args(
            "--execute",
            "--allow-provider-calls",
            "--authorization",
            planned["plan_hash"],
        ),
        planner=_planner,
        discover=discover,
        conn=conn,
        policy_builder=policy_builder,
        qualify=qualify,
    ))

    assert len(discovery_calls) == 2
    for call in discovery_calls:
        assert call["platforms"] == ["youtube"]
        assert call["limit"] == 10
        assert call["per_platform_limit"] == 10
        assert call["per_platform_limits"] == {"youtube": 10}
        assert call["auto_enroll"] is False
        assert call["exact_query"] is True
        assert call["page_cursors"] is None
    assert [(row["followers_min"], row["followers_max"]) for row in policies] == [
        (None, None),
        (50_000, 500_000),
    ]
    assert len(qualifications) == 2
    assert report["mode"] == "authorized_live_canary"
    assert report["claim_status"] == "descriptive_only"
    assert report["execution"]["authorized"] is True
    assert report["execution"]["provider_calls"] is True
    assert report["execution"]["discovery_leg_count"] == 2
    assert report["execution"]["youtube_search_calls"] == 2
    assert report["execution"]["youtube_combined_quota_units"] == 4
    assert report["execution"]["youtube_api_calls"] == 6
    assert report["execution"]["youtube_quota_units"] == 4
    assert report["execution"]["youtube_quota_units_deprecated"] is True
    assert report["execution"]["fallback_queries_used"] is False
    assert set(report["qualification"]) == {"followers_unlimited", "followers_50k_500k"}
    assert report["database"]["mutations_detected"] is False
    assert conn.rollback_count == 1

    rendered = json.dumps(report, ensure_ascii=False)
    assert "public_creator" in rendered
    assert "private@example.com" not in rendered
    assert "+1-secret" not in rendered
    assert "candidate-secret" not in rendered
    assert "configured-test-key" not in rendered


def test_authorized_live_blocks_when_youtube_key_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn()
    planned = canary.build_canary_plan(
        query="Z1 Pro 找赛车和餐饮创作者",
        market="US",
        product_sku="",
        planner=_planner,
    )
    called = False

    async def discover(**_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_YOUTUBE_API_KEY", raising=False)
    report = asyncio.run(canary.run_from_args(
        _args(
            "--execute",
            "--allow-provider-calls",
            "--authorization",
            planned["plan_hash"],
        ),
        planner=_planner,
        discover=discover,
        conn=conn,
    ))

    assert called is False
    assert report["execution"]["provider_calls"] is False
    assert report["execution"]["reason"] == "youtube_api_not_configured"
    assert report["provider_readiness"] == {
        "youtube_data_api_configured": False,
        "apify_disabled": True,
        "llm_disabled": True,
        "gemini_disabled": True,
    }


def test_authorized_live_can_be_bound_to_one_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn()
    planned = canary.build_canary_plan(
        query="Z1 Pro 找赛车和餐饮创作者",
        market="US",
        product_sku="",
        planner=_planner,
        cell_count=1,
    )
    calls: list[dict[str, Any]] = []

    async def discover(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "empty",
            "platforms": ["youtube"],
            "new_creators": [],
            "platform_results": [{
                "platform": "youtube",
                "status": "ready",
                "metadata": {
                    "youtube_search_calls": 1,
                    "youtube_combined_quota_units": 2,
                    "youtube_api_calls": 3,
                    "quota_units": 2,
                    "quota_units_deprecated": True,
                },
            }],
            "provider_calls": True,
        }

    def policy_builder(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    def qualify(_candidates: list[dict[str, Any]], **_kwargs: Any) -> dict[str, Any]:
        return {
            "schema": "smart_online_net_new_qualified_v1",
            "accepted": [],
            "counts": {},
            "rejected_by_reason": {},
            "qualification_stats": {},
        }

    monkeypatch.setenv("YOUTUBE_API_KEY", "configured-test-key")
    report = asyncio.run(canary.run_from_args(
        _args(
            "--cell-count",
            "1",
            "--execute",
            "--allow-provider-calls",
            "--authorization",
            planned["plan_hash"],
        ),
        planner=_planner,
        discover=discover,
        conn=conn,
        policy_builder=policy_builder,
        qualify=qualify,
    ))

    assert len(planned["query_cells"]) == 1
    assert len(calls) == 1
    assert report["execution"]["query_cells_executed"] == 1
    assert report["execution"]["discovery_leg_count"] == 1
    assert report["execution"]["youtube_search_calls"] == 1
    assert report["execution"]["youtube_combined_quota_units"] == 2
    assert report["execution"]["youtube_api_calls"] == 3
