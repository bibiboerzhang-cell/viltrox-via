from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from app.domains.kol import profile_discovery_candidates
from scripts.vkpi_engineering_health_collect import collect_complexity


MODULE_PATH = Path(profile_discovery_candidates.__file__).resolve()
SCENARIO_COUNT = 1_024
FROZEN_PRE_REFACTOR_DIGEST = "f78d110f0d85c4f86eb797aa7923f13e0d5ade4bf914df6a44e83908feb279b9"
_LANES = ("core_vertical", "expansion", "exploration")


def _legacy_filter_recall_result_platforms(
    result: dict[str, Any],
    value: Any,
    distribution: Callable[[list[Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Wave 20b reference oracle, copied before the complexity-only refactor."""
    raw_values = value if isinstance(value, list) else [value]
    requested = {
        profile_discovery_candidates._normalize_discovery_platform(raw)
        for raw in raw_values
        if profile_discovery_candidates._text(raw)
        and profile_discovery_candidates._text(raw).lower() not in {"all", "*"}
    }
    if not requested:
        return result

    def _keep(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        platform = profile_discovery_candidates._normalize_discovery_platform(
            item.get("platform") or payload.get("platform")
        )
        return bool(platform and platform in requested)

    filtered = dict(result)
    original_items = result.get("items") if isinstance(result.get("items"), list) else None
    if original_items is not None:
        filtered["items"] = [item for item in original_items if _keep(item)]
    original_buckets = result.get("buckets") if isinstance(result.get("buckets"), dict) else None
    if original_buckets is not None:
        filtered["buckets"] = {
            key: [item for item in items if _keep(item)]
            for key, items in original_buckets.items()
            if isinstance(items, list)
        }
    original_business_buckets = (
        result.get("business_buckets")
        if isinstance(result.get("business_buckets"), dict)
        else None
    )
    if original_business_buckets is not None:
        filtered["business_buckets"] = {
            key: [item for item in items if _keep(item)]
            for key, items in original_business_buckets.items()
            if isinstance(items, list)
        }
    before_item_count = len(original_items or [])
    before_bucket_count = sum(
        len(items) for items in (original_buckets or {}).values() if isinstance(items, list)
    )
    after_item_count = len(filtered.get("items") or [])
    after_bucket_count = sum(
        len(items)
        for items in (filtered.get("buckets") or {}).values()
        if isinstance(items, list)
    )
    filtered_buckets = (
        filtered.get("buckets") if isinstance(filtered.get("buckets"), dict) else {}
    )
    filtered_business_buckets = (
        filtered.get("business_buckets")
        if isinstance(filtered.get("business_buckets"), dict)
        else {}
    )
    before_count = before_item_count or before_bucket_count
    after_count = after_item_count or after_bucket_count
    diagnostics = (
        dict(result.get("diagnostics"))
        if isinstance(result.get("diagnostics"), dict)
        else {}
    )
    requested_count = int(diagnostics.get("requested_count") or after_count)
    final_count = after_count
    shortfall = max(0, requested_count - final_count)
    filtered_items = filtered.get("items") if isinstance(filtered.get("items"), list) else []
    lane_order = ("core_vertical", "expansion", "exploration")
    lane_selected = {
        lane: len(filtered_business_buckets.get(lane) or [])
        for lane in lane_order
    }
    if not any(lane_selected.values()) and filtered_items:
        lane_selected = {
            lane: sum(
                1 for item in filtered_items if item.get("candidate_bucket") == lane
            )
            for lane in lane_order
        }
    original_lane_selection = (
        diagnostics.get("lane_selection")
        if isinstance(diagnostics.get("lane_selection"), dict)
        else {}
    )
    original_targets = (
        original_lane_selection.get("lane_targets")
        if isinstance(original_lane_selection.get("lane_targets"), dict)
        else {}
    )
    lane_targets = {
        lane: max(0, int(original_targets.get(lane) or 0)) for lane in lane_order
    }
    lane_shortfalls = {
        lane: max(
            0,
            lane_targets[lane] - min(lane_selected[lane], lane_targets[lane]),
        )
        for lane in lane_order
    }
    lane_refills = {
        lane: max(0, lane_selected[lane] - lane_targets[lane]) for lane in lane_order
    }
    profile_counts = {
        "creator": len(filtered_buckets.get("creator") or []),
        "reviewer": len(filtered_buckets.get("reviewer") or []),
        "unknown": len(filtered_buckets.get("unknown") or []),
    }
    filter_changed_result = before_count != after_count
    if not filter_changed_result and original_lane_selection:
        reconciled_lane_selection = original_lane_selection
    else:
        reconciled_lane_selection = {
            **original_lane_selection,
            "lane_targets": lane_targets,
            "lane_available": dict(lane_selected),
            "lane_available_scope": "post_filter_returned_set",
            "lane_selected": lane_selected,
            "lane_shortfalls": lane_shortfalls,
            "lane_refills": lane_refills,
            "lane_contract_satisfied": all(
                value == 0 for value in lane_shortfalls.values()
            ),
            "profile_counts": profile_counts,
        }
    original_business_counts = diagnostics.get("business_bucket_counts")
    reconciled_business_counts = (
        original_business_counts
        if not filter_changed_result and isinstance(original_business_counts, dict)
        else lane_selected
    )
    diagnostics.update(
        {
            "returned_count": after_count,
            "final_count": final_count,
            "shortfall": shortfall,
            "result_contract_satisfied": shortfall == 0,
            "creator_returned": len(filtered_buckets.get("creator") or []),
            "reviewer_returned": len(filtered_buckets.get("reviewer") or []),
            "unknown_type_returned": len(filtered_buckets.get("unknown") or []),
            "strict_count": sum(
                1 for item in filtered_items if item.get("match_tier") == "strict"
            ),
            "relaxed_count": sum(
                1 for item in filtered_items if item.get("match_tier") == "relaxed"
            ),
            "backfill_count": sum(
                1 for item in filtered_items if item.get("match_tier") == "backfill"
            ),
            "business_bucket_counts": reconciled_business_counts,
            "lane_selection": reconciled_lane_selection,
            "platform_filtered_out": max(0, before_count - after_count),
            "platform_filter": sorted(requested),
            "post_filter_counts_reconciled": True,
        }
    )
    filtered["diagnostics"] = diagnostics
    if diagnostics.get("evidence_gate_enabled"):
        filtered["match_status"] = "matched" if after_count else "empty"
        if after_count:
            diagnostics["empty_reason"] = ""
        elif before_count > 0:
            diagnostics["empty_reason"] = "no_platform_evidence_match"
        filtered["candidate_set_distribution"] = distribution(
            list(filtered.get("items") or [])
        )
    filtered["platform_filter"] = {
        "applied": True,
        "requested": sorted(requested),
        "filtered_out": max(0, before_count - after_count),
    }
    return filtered


def _candidate(seed: int, slot: int) -> Any:
    platforms: tuple[Any, ...] = (
        "youtube",
        "yt",
        "YouTube",
        " youtube ",
        "youtube_shorts",
        "instagram",
        "ig",
        "ins",
        "tiktok",
        "tt",
        "facebook",
        "fb",
        "unknown",
        "",
        None,
        0,
    )
    followers: tuple[Any, ...] = (
        None,
        0,
        -1,
        1,
        999,
        50_000,
        "50000",
        "50K",
        {},
        [],
    )
    identities: tuple[Any, ...] = (
        None,
        "",
        f"@creator_{seed}_{slot}",
        "@ViltroxOfficial",
        "Creator.Name",
        "摄影师",
        "https://youtube.com/@creator",
        "https://instagram.com/creator/extra",
    )
    if (seed + slot) % 13 == 0:
        return None
    if (seed + slot) % 17 == 0:
        return f"scalar-{seed}-{slot}"
    platform = platforms[(seed * 3 + slot) % len(platforms)]
    payload_platform = platforms[(seed + slot * 5 + 1) % len(platforms)]
    identity = identities[(seed + slot) % len(identities)]
    return {
        "id": seed * 10 + slot,
        "platform": platform if slot % 3 else None,
        "payload": {"platform": payload_platform} if slot % 4 else "not-a-dict",
        "candidate_bucket": _LANES[(seed + slot) % len(_LANES)],
        "match_tier": ("strict", "relaxed", "backfill", "other")[(seed + slot) % 4],
        "followers": followers[(seed + slot * 2) % len(followers)],
        "handle": identity,
        "channel_handle": identities[(seed + slot + 1) % len(identities)],
        "username": identities[(seed + slot + 2) % len(identities)],
        "profile_url": identities[(seed + slot + 3) % len(identities)],
    }


def _scenario(index: int) -> tuple[dict[str, Any], Any]:
    rows = [_candidate(index, slot) for slot in range(6)]
    values: tuple[Any, ...] = (
        None,
        "",
        "all",
        "*",
        "youtube",
        "yt",
        " instagram ",
        "tt",
        "facebook",
        "unknown",
        ["youtube", "ig"],
        ["all", "*", ""],
        [None, "tt", "facebook", "tt"],
        ("youtube", "instagram"),
        0,
        False,
    )
    requested_counts: tuple[Any, ...] = (None, 0, 1, 3, 20, "4", -2, "invalid")
    targets: tuple[Any, ...] = (None, 0, 1, 4, -3, "2", "invalid")
    diagnostics: Any = {
        "requested_count": requested_counts[index % len(requested_counts)],
        "evidence_gate_enabled": index % 3 == 0,
        "empty_reason": f"before-{index % 5}",
        "business_bucket_counts": (
            {lane: (index + lane_index) % 4 for lane_index, lane in enumerate(_LANES)}
            if index % 4
            else "not-a-dict"
        ),
        "lane_selection": {
            "lane_targets": {
                lane: targets[(index + lane_index) % len(targets)]
                for lane_index, lane in enumerate(_LANES)
            },
            "lane_available": {lane: 100 + index for lane in _LANES},
            "marker": f"lane-{index}",
        }
        if index % 5
        else "not-a-dict",
        "marker": f"diagnostics-{index}",
    }
    if index % 11 == 0:
        diagnostics = "not-a-dict"
    result: dict[str, Any] = {
        "items": rows if index % 7 else tuple(rows),
        "buckets": {
            "creator": rows[:2],
            "reviewer": rows[2:4],
            "unknown": rows[4:],
            "ignored_scalar": "not-a-list",
        }
        if index % 6
        else "not-a-dict",
        "business_buckets": {
            "core_vertical": rows[::3],
            "expansion": rows[1::3],
            "exploration": rows[2::3],
            "ignored_scalar": None,
        }
        if index % 8
        else None,
        "diagnostics": diagnostics,
        "candidate_set_distribution": {"stale": True, "seed": index},
        "match_status": "before",
        "identity_marker": identities_marker(index),
    }
    if index % 9 == 0:
        result.pop("items")
    if index % 10 == 0:
        result.pop("buckets")
    return result, values[index % len(values)]


def identities_marker(index: int) -> dict[str, Any]:
    """Unconsumed identity/follower context must survive the compatibility filter."""
    return {
        "canonical_identity": f"platform://creator/{index}",
        "min_followers": (None, 0, 10_000, "100K")[index % 4],
        "max_followers": (None, 50_000, 1_000_000, "1M")[(index // 4) % 4],
    }


def _capture(
    function: Callable[[dict[str, Any], Any], dict[str, Any]],
    result: dict[str, Any],
    value: Any,
) -> dict[str, Any]:
    calls: list[list[Any]] = []

    def distribution(items: list[Any]) -> dict[str, Any]:
        calls.append(deepcopy(items))
        return {"stubbed_distribution": deepcopy(items)}

    argument = deepcopy(result)
    original_argument = deepcopy(argument)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            profile_discovery_candidates,
            "candidate_set_distribution_from_items",
            distribution,
        )
        try:
            output = function(argument, deepcopy(value))
        except Exception as exc:  # characterization includes legacy exception parity
            outcome: dict[str, Any] = {
                "status": "raised",
                "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                "args": [repr(item) for item in exc.args],
            }
        else:
            outcome = {
                "status": "returned",
                "value": output,
                "same_input_object": output is argument,
            }
    return {
        "outcome": outcome,
        "calls": calls,
        "input_unchanged": argument == original_argument,
        "input_after": argument,
    }


def _capture_legacy(result: dict[str, Any], value: Any) -> dict[str, Any]:
    return _capture(
        lambda argument, selected: _legacy_filter_recall_result_platforms(
            argument,
            selected,
            profile_discovery_candidates.candidate_set_distribution_from_items,
        ),
        result,
        value,
    )


def test_platform_filter_matches_wave20b_across_1024_offline_scenarios() -> None:
    actual_outputs: list[dict[str, Any]] = []
    returned = raised = collaborator_calls = no_op_identity_returns = unchanged_inputs = 0
    for index in range(SCENARIO_COUNT):
        result, value = _scenario(index)
        expected = _capture_legacy(result, value)
        actual = _capture(
            profile_discovery_candidates.filter_recall_result_platforms,
            result,
            value,
        )
        assert actual == expected, index
        actual_outputs.append(actual)
        returned += actual["outcome"]["status"] == "returned"
        raised += actual["outcome"]["status"] == "raised"
        collaborator_calls += len(actual["calls"])
        no_op_identity_returns += bool(actual["outcome"].get("same_input_object"))
        unchanged_inputs += actual["input_unchanged"]

    encoded = json.dumps(
        actual_outputs,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    assert (returned, raised) == (755, 269)
    assert collaborator_calls == 57
    assert no_op_identity_returns == 448
    assert unchanged_inputs == SCENARIO_COUNT
    assert digest == FROZEN_PRE_REFACTOR_DIGEST, digest


def test_platform_filter_complexity_and_module_size_stay_bounded() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    rows = collect_complexity({str(MODULE_PATH): ast.parse(source)})
    focal = next(
        row for row in rows if row.qualified_name == "filter_recall_result_platforms"
    )
    family = [
        row
        for row in rows
        if "platform_filter" in row.qualified_name
        or row.qualified_name.startswith("filter_recall_result_platforms")
    ]

    assert focal.cc <= 20
    assert max(row.cc for row in family) <= 20
    assert focal.loc <= 80
    assert len(source.splitlines()) <= 800
