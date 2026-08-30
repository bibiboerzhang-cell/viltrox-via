from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import profile_online_qualification
from scripts.vkpi_engineering_health_collect import collect_complexity


MODULE_PATH = Path(profile_online_qualification.__file__).resolve()
SCENARIO_COUNT = 1_024
FROZEN_PRE_REFACTOR_DIGEST = "edf6ab0fad3f5fcbc3cc7545a77106979dc789c5c6cd9230aad22701ddd72508"


def _canonical_key(item: dict[str, Any]) -> str:
    return str(item.get("canonical") or item.get("handle") or "")


def _aliases(item: dict[str, Any]) -> set[str]:
    aliases = item.get("aliases")
    if isinstance(aliases, list):
        return {str(value) for value in aliases if value}
    key = _canonical_key(item)
    return {key} if key else set()


def _raw(seed: int, slot: int, **overrides: Any) -> dict[str, Any]:
    key = f"creator-{seed}-{slot}"
    row: dict[str, Any] = {
        "canonical": key,
        "handle": key,
        "aliases": [key],
        "outcome": "selected",
        "cell_count": 1,
        "enrollment": "dict",
        "pool_id": 1_000_000 + seed * 100 + slot,
        "db_reads": (seed + slot) % 3,
    }
    row.update(overrides)
    return row


def _qualification_stub(
    candidates: list[dict[str, Any]],
    **_kwargs: Any,
) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    cell_evaluations = 0
    qualified_cells = 0
    for raw in candidates:
        status = str(raw.get("outcome") or "rejected")
        reason = str(raw.get("reason") or "")
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
        cell_count = max(0, int(raw.get("cell_count") or 0))
        cell_evaluations += cell_count
        if status in {"selected", "qualified_overflow"}:
            qualified_cells += cell_count
        fingerprint = raw.get("fingerprint")
        if fingerprint is None:
            fingerprint = hashlib.sha256(_canonical_key(raw).encode("utf-8")).hexdigest()
        item = {
            "handle": raw.get("handle"),
            "canonical": raw.get("canonical"),
            "aliases": list(raw.get("aliases") or []),
            "canonical_fingerprint": fingerprint,
            "qualification_evidence": {"fixture_seed": raw.get("pool_id")},
        }
        outcomes.append({
            "status": status,
            "item": item if status in {"selected", "qualified_overflow"} else None,
            "source": raw if status in {"selected", "qualified_overflow"} else None,
        })
    return {
        "outcomes": outcomes,
        "strict_contract": {"rejected_by_reason": rejected},
        "qualification_stats": {
            "cell_evaluation_count": cell_evaluations,
            "qualified_cell_count": qualified_cells,
        },
    }


def _scenario(index: int) -> tuple[list[Any], Any, set[str], set[str]]:
    archetype = index % 16
    variant = index // 16
    local_keys: set[str] = set()
    inventory_aliases: set[str] = set()
    gate = None

    if archetype == 0:
        rounds = [{"new_creators": [_raw(index, 0)], "provider_calls": True}]
    elif archetype == 1:
        rows = [
            _raw(index, 0, outcome="pending", reason="market_unknown"),
            _raw(index, 1, outcome="rejected", reason="low_relevance"),
            _raw(index, 2, outcome="duplicate_local"),
            _raw(index, 3, outcome="duplicate_online"),
            _raw(index, 4, outcome="qualified_overflow"),
        ]
        rounds = [{"new_creators": rows, "provider_calls": True}]
    elif archetype == 2:
        rounds = [RuntimeError(f"provider-{variant}")]
    elif archetype == 3:
        rounds = [{
            "new_creators": [_raw(index, 0, outcome="pending")],
            "provider_calls": True,
            "has_more": True,
            "next_cursor": f"cursor-{variant}",
        }]
        gate = lambda round_no: {
            "allowed": False,
            "reason": f"gate-{variant % 5}",
            "round": round_no,
        }
    elif archetype == 4:
        row = _raw(index, 0, outcome="pending")
        page = {
            "new_creators": [row],
            "provider_calls": True,
            "has_more": True,
            "next_cursor": f"repeat-{variant}",
        }
        rounds = [page, page]
    elif archetype == 5:
        rows = [
            _raw(index, 0, history_kol_pool_id=7),
            _raw(index, 1, kol_pool_id=8),
            _raw(index, 2, historical_match={"kol_pool_id": 9}),
            _raw(index, 3),
        ]
        rounds = [{"new_creators": rows, "provider_calls": True}]
    elif archetype == 6:
        inventory_aliases = {"inventory-shared", "local-shared"}
        local_keys = {"local-shared"}
        rows = [
            _raw(index, 0, aliases=["inventory-shared"]),
            _raw(index, 1, aliases=["local-shared"], outcome="duplicate_local"),
            _raw(index, 2, aliases=["accepted-shared"]),
            _raw(index, 3, aliases=["accepted-shared"]),
        ]
        rounds = [{"new_creators": rows, "provider_calls": True}]
    elif archetype == 7:
        rows = [
            _raw(index, 0, enrollment="raise"),
            _raw(index, 1, enrollment="none"),
            _raw(index, 2, enrollment="scalar"),
        ]
        rounds = [{"new_creators": rows, "provider_calls": True}]
    elif archetype == 8:
        rows = [
            _raw(index, 0, enrollment="matched_existing"),
            _raw(index, 1, enrollment="duplicate_inventory"),
            _raw(index, 2),
        ]
        rounds = [{"new_creators": rows, "provider_calls": True}]
    elif archetype == 9:
        rows = [
            _raw(index, 0, fingerprint="not-a-fingerprint"),
            _raw(index, 1),
        ]
        rounds = [{"new_creators": rows, "provider_calls": True}]
    elif archetype == 10:
        rounds = [{"status": "failed", "new_creators": [], "provider_calls": True}]
    elif archetype == 11:
        rounds = [{"items": [_raw(index, 0)], "provider_calls": False}]
    elif archetype == 12:
        reported = (1_000, -8, 0, "invalid")[variant % 4]
        rounds = [{
            "new_creators": [_raw(index, 0)],
            "provider_calls": True,
            "provider_call_count": reported,
        }]
    elif archetype == 13:
        rounds = [[_raw(index, 0)]]
    elif archetype == 14:
        rounds = [{
            "items": [None, "bad", _raw(index, 0, cell_count=2)],
            "provider_calls": True,
        }]
    else:
        rounds = [{
            "new_creators": [_raw(index, slot, cell_count=1 + slot % 3) for slot in range(35)],
            "provider_calls": True,
            "has_more": True,
            "next_cursor": f"unused-{variant}",
        }]
    return rounds, gate, local_keys, inventory_aliases


async def _run_scenario(index: int) -> dict[str, Any]:
    rounds, round_gate, local_keys, inventory_aliases = _scenario(index)

    async def fetch_batch(*, round_no: int, **_kwargs: Any) -> Any:
        value = rounds[min(round_no - 1, len(rounds) - 1)]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    def enroll(raw: dict[str, Any]) -> Any:
        mode = raw.get("enrollment")
        if mode == "raise":
            raise RuntimeError("fixture enrollment failure")
        if mode == "none":
            return None
        if mode == "scalar":
            return raw["pool_id"]
        if mode == "matched_existing":
            return {"kol_pool_id": raw["pool_id"], "matched_existing": True, "db_reads": 2}
        if mode == "duplicate_inventory":
            return {"kol_pool_id": raw["pool_id"], "duplicate_local_inventory": True, "db_reads": 3}
        return {"kol_pool_id": raw["pool_id"], "db_reads": raw.get("db_reads")}

    result = await profile_online_qualification.collect_strict_online_candidates(
        query_text=f"query {index % 11}",
        policy={
            "min_followers": index % 3 or None,
            "max_followers": None,
            "followers_filter": {"requested": bool(index % 2)},
            "max_video_age_days": 90,
            "market": "US" if index % 2 else "",
            "platforms": ["youtube"],
            "languages": ["en"],
            "profile_types": ["creator"],
            "exclude_chinese_regions": index % 3 == 0,
        },
        local_canonical_keys=local_keys,
        inventory_aliases=inventory_aliases,
        local_unique_count=None if index % 4 else index % 40,
        inventory_snapshot_rows=index % 7,
        inventory_db_reads=index % 3,
        fetch_batch=fetch_batch,
        enroll_candidate=enroll,
        candidate_budget=30 + index % 121,
        max_provider_rounds=3,
        round_gate=round_gate,
        exhaustion_reason=f"exhausted-{index % 5}",
    )
    result["stage_timing"] = {"online_qualification_ms": "<normalized>"}
    return result


async def _run_all_scenarios() -> list[dict[str, Any]]:
    return await asyncio.gather(*(_run_scenario(index) for index in range(SCENARIO_COUNT)))


def test_collect_matches_frozen_pre_refactor_behavior_across_1024_scenarios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profile_online_qualification, "_identity_probe", lambda raw: dict(raw))
    monkeypatch.setattr(profile_online_qualification, "_candidate_query_cells", lambda raw, **_kwargs: [
        {"cell": index} for index in range(max(0, int(raw.get("cell_count") or 0)))
    ])
    monkeypatch.setattr(
        profile_online_qualification.profile_recall_qualification,
        "canonical_creator_key",
        _canonical_key,
    )
    monkeypatch.setattr(
        profile_online_qualification.profile_recall_qualification,
        "canonical_creator_aliases",
        _aliases,
    )
    monkeypatch.setattr(
        profile_online_qualification,
        "_qualify_online_candidates_internal",
        _qualification_stub,
    )

    outputs = asyncio.run(_run_all_scenarios())
    encoded = json.dumps(outputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    assert len(outputs) == SCENARIO_COUNT
    assert {result["status"] for result in outputs} == {"ready", "shortfall"}
    assert any(result["round_gate"]["stopped_by"] for result in outputs)
    assert any(result["provider_calls"] == 100 for result in outputs)
    assert digest == FROZEN_PRE_REFACTOR_DIGEST


def test_collect_refactor_keeps_focal_complexity_and_module_size_bounded() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    rows = collect_complexity({str(MODULE_PATH): ast.parse(source)})
    focal = next(row for row in rows if row.qualified_name == "collect_strict_online_candidates")
    family = [row for row in rows if row.qualified_name.startswith("collect_strict_online_candidates")]

    assert focal.cc <= 20
    assert max(row.cc for row in family) <= 20
    assert focal.loc <= 313
    assert len(source.splitlines()) <= 800
