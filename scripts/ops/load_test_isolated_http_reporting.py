"""Aggregation, historical context, and artifact rendering for the HTTP fixture.

Kept separate so the executable harness remains below the repository's
thousand-line operational-script guard.  This module performs no network I/O,
does not import the V-KPI application, and only reads an explicitly supplied
local historical evidence artifact.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _rounded_percentiles(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "p50": _round_optional(_percentile(values, 50)),
        "p95": _round_optional(_percentile(values, 95)),
        "p99": _round_optional(_percentile(values, 99)),
        "max": _round_optional(max(values) if values else None),
    }


def _round_optional(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def _rate_metric(
    numerator: int, denominator: int
) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 8) if denominator else None,
    }


def _throughput_metric(
    numerator: int, denominator_seconds: float
) -> dict[str, int | float | None]:
    return {
        "numerator_requests": numerator,
        "denominator_seconds": round(denominator_seconds, 6),
        "requests_per_second": (
            round(numerator / denominator_seconds, 3)
            if denominator_seconds > 0
            else None
        ),
    }


def aggregate_tier(
    tier: int, trials: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    records = [
        measurement
        for trial in trials
        for measurement in trial["measurements"]
    ]
    offered = len(records)
    outcomes = Counter(str(item["outcome"]) for item in records)
    completed = sum(outcomes.values())
    successes = outcomes["success"]
    errors = completed - successes
    timeouts = outcomes["timeout"]
    offer_seconds = sum(float(item["duration_target_seconds"]) for item in trials)
    completion_seconds = sum(
        float(item["completion_window_seconds"]) for item in trials
    )
    latencies = [float(item["latency_ms"]) for item in records]
    rps_values = [
        float(item["summary"]["completed_throughput"]["requests_per_second"])
        for item in trials
    ]
    resource_waits = {
        resource: {
            "p95_ms_max_across_trials": max(
                float(
                    trial["server"]["resources"][resource]["wait_ms"]["p95"]
                    or 0.0
                )
                for trial in trials
            ),
            "max_waiting_across_trials": max(
                int(trial["server"]["resources"][resource]["max_waiting"])
                for trial in trials
            ),
            "max_active_across_trials": max(
                int(trial["server"]["resources"][resource]["max_active"])
                for trial in trials
            ),
            "max_slot_utilization": max(
                float(
                    trial["server"]["resources"][resource][
                        "max_slot_utilization"
                    ]
                )
                for trial in trials
            ),
        }
        for resource in ("database", "aggregate")
    }
    result = {
        "tier_vu": tier,
        "trial_count": len(trials),
        "denominators": {
            "offered_requests": offered,
            "completed_outcomes": completed,
            "offer_window_seconds_sum": round(offer_seconds, 6),
            "completion_window_seconds_sum": round(completion_seconds, 6),
            "latency_samples": len(latencies),
        },
        "rates": {
            "completion_rate": _rate_metric(completed, offered),
            "success_rate": _rate_metric(successes, offered),
            "error_rate": _rate_metric(errors, offered),
            "timeout_rate": _rate_metric(timeouts, offered),
        },
        "throughput": {
            "offered": _throughput_metric(offered, offer_seconds),
            "completed": _throughput_metric(completed, completion_seconds),
            "successful": _throughput_metric(successes, completion_seconds),
            "completed_rps_trials": {
                "min": round(min(rps_values), 3),
                "median": round(statistics.median(rps_values), 3),
                "max": round(max(rps_values), 3),
                "coefficient_of_variation": round(
                    statistics.pstdev(rps_values) / statistics.mean(rps_values),
                    6,
                )
                if statistics.mean(rps_values)
                else None,
            },
        },
        "latency_ms_all_outcomes": {
            "sample_count": len(latencies),
            **_rounded_percentiles(latencies),
        },
        "resource_saturation": resource_waits,
        "trials": list(trials),
    }
    result["calculation_sha256"] = _sha256_json(
        {key: value for key, value in result.items() if key != "trials"}
    )
    return result


def analyze_saturation(tiers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    breakpoint: int | None = None
    for previous, current in zip(tiers, tiers[1:]):
        prior_rps = float(
            previous["throughput"]["completed"]["requests_per_second"]
        )
        current_rps = float(
            current["throughput"]["completed"]["requests_per_second"]
        )
        prior_p95 = float(previous["latency_ms_all_outcomes"]["p95"] or 0.0)
        current_p95 = float(current["latency_ms_all_outcomes"]["p95"] or 0.0)
        gain = current_rps / prior_rps - 1.0 if prior_rps else None
        latency_multiplier = current_p95 / prior_p95 if prior_p95 else None
        aggregate_wait = float(
            current["resource_saturation"]["aggregate"][
                "p95_ms_max_across_trials"
            ]
        )
        expected_linear_gain = (
            int(current["tier_vu"]) / int(previous["tier_vu"]) - 1.0
        )
        saturated = bool(
            gain is not None
            and latency_multiplier is not None
            and gain < min(0.35, expected_linear_gain * 0.5)
            and latency_multiplier >= 1.35
            and aggregate_wait >= 1.0
        )
        if saturated and breakpoint is None:
            breakpoint = int(current["tier_vu"])
        comparisons.append(
            {
                "from_vu": int(previous["tier_vu"]),
                "to_vu": int(current["tier_vu"]),
                "completed_throughput_gain": _round_optional(gain, 6),
                "expected_linear_gain": round(expected_linear_gain, 6),
                "p95_latency_multiplier": _round_optional(
                    latency_multiplier, 6
                ),
                "aggregate_p95_wait_ms": round(aggregate_wait, 3),
                "fixture_saturation_triggered": saturated,
            }
        )
    highest_pre_saturation: int | None = None
    if breakpoint is not None:
        for item in tiers:
            if int(item["tier_vu"]) < breakpoint:
                highest_pre_saturation = int(item["tier_vu"])
    return {
        "fixture_saturation_breakpoint_vu": breakpoint,
        "highest_pre_saturation_fixture_vu": highest_pre_saturation,
        "comparisons": comparisons,
        "rule": (
            "on a tier increase: completed throughput gain < min(35%, half linear "
            "expectation), p95 latency >= 1.35x, and aggregate p95 wait >= 1ms"
        ),
        "dominant_fixture_bottleneck": "synthetic_aggregate_slots",
        "production_capacity": None,
        "maximum_real_users": None,
    }


def load_historical_chain_context(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = payload["source_sample"]
    profiles = payload["capacity_evidence"]["profiles"]
    if int(source["sample_count"]) != 42:
        raise ValueError("historical capacity source must contain exactly 42 tasks")
    if "20-survivor" not in str(source["scope"]):
        raise ValueError("historical capacity source is not the 20-survivor chain")
    expected_profiles = (
        "current_guarded_cap_1",
        "two_slot_candidate_cap_2",
        "ideal_unbounded_lower_bound",
    )
    if set(profiles) != set(expected_profiles):
        raise ValueError("historical capacity profiles do not match the expected bundle")

    extracted_profiles: dict[str, Any] = {}
    for name in expected_profiles:
        profile = profiles[name]
        scenarios = []
        for scenario in profile["scenarios"]:
            quality = scenario["observed_attempt_quality"]
            scenarios.append(
                {
                    "lanes": int(scenario["lanes"]),
                    "makespan_minutes": float(scenario["makespan_minutes"]),
                    "attempts_per_hour": float(
                        scenario["throughput"]["attempts_per_hour"]
                    ),
                    "error_rate": quality["error_rate"],
                    "conflict_rate": quality["conflict_rate"],
                }
            )
        extracted_profiles[name] = {
            "definition": profile["definition"],
            "scenarios": scenarios,
            "calculation_sha256": profile["calculation_sha256"],
        }
    return {
        "evidence_class": "historical_chain_duration_plus_deterministic_model",
        "source_artifact": str(path),
        "source_artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "observation_date_local": source["observation_date_local"],
        "scope": source["scope"],
        "task_count": int(source["sample_count"]),
        "full_chain_wall_seconds": float(source["full_chain_wall_seconds"]),
        "sampled_service_seconds": float(source["sampled_service_seconds"]),
        "sampled_to_wall_coverage": float(source["sampled_to_wall_coverage"]),
        "profiles": extracted_profiles,
        "claim_boundary": {
            "historical_single_chain_duration_evidence": True,
            "concurrent_scenarios_are_models": True,
            "cap_2_runtime_verified": False,
            "ideal_is_mathematical_lower_bound_only": True,
            "production_capacity": None,
            "maximum_real_users": None,
            "error_rate": None,
            "conflict_rate": None,
            "reason_rates_are_null": (
                "the 42-task source has zero labelled outcome/conflict denominator"
            ),
        },
    }


def build_two_slot_fixture_evidence(
    tiers: Sequence[Mapping[str, Any]], config: Any
) -> dict[str, Any]:
    tier_two = next((item for item in tiers if int(item["tier_vu"]) == 2), None)
    if tier_two is None or config.aggregate_slots != 2:
        return {
            "status": "not_exercised",
            "reason": (
                "tier 2 was not requested"
                if tier_two is None
                else "synthetic aggregate slot count was not exactly 2"
            ),
            "real_worker_lane_validation": False,
            "idempotency_validation": False,
        }
    aggregate = tier_two["resource_saturation"]["aggregate"]
    return {
        "status": "verified_in_process_fixture_only",
        "synthetic_fixture_vu": 2,
        "synthetic_aggregate_slots": config.aggregate_slots,
        "aggregate_max_active": aggregate["max_active_across_trials"],
        "aggregate_max_slot_utilization": aggregate["max_slot_utilization"],
        "aggregate_p95_wait_ms_max": aggregate[
            "p95_ms_max_across_trials"
        ],
        "completed_throughput_rps": tier_two["throughput"]["completed"][
            "requests_per_second"
        ],
        "error_rate": tier_two["rates"]["error_rate"],
        "timeout_rate": tier_two["rates"]["timeout_rate"],
        "what_it_proves": (
            "the memory-only HTTP harness can drive two closed-loop tasks "
            "through a two-slot synthetic semaphore and preserve denominators"
        ),
        "real_worker_lane_validation": False,
        "provider_cap_validation": False,
        "database_conflict_validation": False,
        "idempotency_validation": False,
        "next_gate": (
            "isolated staging 1-lane baseline then 2-lane/cap=2 with task-ledger "
            "attempt, success, retry, 429, conflict, idempotency, cost and p50/p95/p99"
        ),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    tiers = report["tier_results"]
    lines = [
        "# V-KPI Round 14 隔离 HTTP 用户负载校准",
        "",
        f"- 生成时间：`{report['completed_at']}`",
        f"- Run ID：`{report['run_id']}`",
        f"- 证据类别：`{report['evidence_class']}`",
        "- 判定：**仅能证明进程内 ASGI HTTP 夹具与负载发生器；不能证明 V-KPI、生产、云端或真实用户容量。**",
        "",
        "## 安全边界",
        "",
        "工具没有目标 URL 参数。每个 trial 创建全新的进程内 ASGI 应用，HTTPX 通过 memory-only transport 发送 method/path/query/header 并读取 status/body；不创建 listener、不解析 DNS、不发起 socket connect。明确禁止接触 5173、8102、54329、6379；本轮数据库、Redis、Worker、Provider、浏览器和业务数据读写均为 0。",
        "",
        "## 结果",
        "",
        "| Fixture VU | Offered / 分母秒 | Completed / 分母秒 | 完成 RPS | p50 | p95 | p99 | 错误率 | 超时率 | Aggregate p95 wait |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in tiers:
        den = item["denominators"]
        completed = item["throughput"]["completed"]
        latency = item["latency_ms_all_outcomes"]
        error = item["rates"]["error_rate"]
        timeout = item["rates"]["timeout_rate"]
        aggregate_wait = item["resource_saturation"]["aggregate"][
            "p95_ms_max_across_trials"
        ]
        lines.append(
            "| {tier} | {offered}/{offer_s:.3f}s | {done}/{done_s:.3f}s | {rps:.3f} | {p50:.3f}ms | {p95:.3f}ms | {p99:.3f}ms | {errors}/{error_den} ({error_pct:.3f}%) | {timeouts}/{timeout_den} ({timeout_pct:.3f}%) | {wait:.3f}ms |".format(
                tier=item["tier_vu"],
                offered=den["offered_requests"],
                offer_s=den["offer_window_seconds_sum"],
                done=den["completed_outcomes"],
                done_s=den["completion_window_seconds_sum"],
                rps=completed["requests_per_second"],
                p50=latency["p50"],
                p95=latency["p95"],
                p99=latency["p99"],
                errors=error["numerator"],
                error_den=error["denominator"],
                error_pct=(error["value"] or 0.0) * 100,
                timeouts=timeout["numerator"],
                timeout_den=timeout["denominator"],
                timeout_pct=(timeout["value"] or 0.0) * 100,
                wait=aggregate_wait,
            )
        )
    analysis = report["analysis"]
    lines.extend(
        [
            "",
            "## 夹具饱和结论",
            "",
            f"- 规则判定的夹具拐点：`{analysis['fixture_saturation_breakpoint_vu']}` VU。",
            f"- 拐点前最高夹具层级：`{analysis['highest_pre_saturation_fixture_vu']}` VU。",
            f"- 刻意设置的主瓶颈：`{analysis['dominant_fixture_bottleneck']}`。",
            "- 上述 VU 是合成闭环 async task，不是人数；`production_capacity` 与 `maximum_real_users` 都保持 `null`。",
            "",
            "## 方法和分母",
            "",
            "- 每层执行 3 次，采用 trial-major 顺序，以显露随时间漂移。",
            "- Offered 是 deadline 前真正开始的 HTTP attempt；Completed 包含成功、HTTP 错误、timeout 与 client error。",
            "- 错误率和超时率均以 offered request 为分母，不用缺失值冒充 0。",
            "- JSON 保留每个请求的 endpoint、offer/completion offset、latency、status 和 outcome，可独立复算分位数与分母。",
            "",
            "## 不能外推的部分",
            "",
            "本轮没有触碰 V-KPI 应用、数据库、缓存、Worker、外部 Provider 或真实业务数据。它证明的是 HTTP 负载工具会并发、会计数、会识别合成资源饱和；它不回答线上最多能有多少人，也不能替代隔离 staging 的 1→2→4→8 分级压测。",
            "",
            "## 复现",
            "",
            "```bash",
            "python3 scripts/ops/load_test_isolated_http.py \\",
            "  --tiers 1,2,4,8,16,32 --trials 3 --duration-seconds 1.0 \\",
            "  --historical-chain-evidence runtime/ops/vkpi-round12-capacity-release-evidence-20260714.json \\",
            "  --json-output /tmp/vkpi-isolated-http.json \\",
            "  --markdown-output /tmp/vkpi-isolated-http.md",
            "```",
            "",
            f"Calculation SHA-256：`{report['report_calculation_sha256']}`",
            "",
        ]
    )
    _insert_split_capacity_evidence(lines, report)
    return "\n".join(lines)


def _insert_split_capacity_evidence(
    lines: list[str], report: Mapping[str, Any]
) -> None:
    insertion_at = lines.index("## 方法和分母")
    two_slot = report["two_slot_fixture_mechanics"]
    two_slot_error = two_slot.get("error_rate") or {}
    two_slot_timeout = two_slot.get("timeout_rate") or {}
    split_block = [
        "## 两条容量证据必须分开",
        "",
        "### A. HTTP fixture 虚拟用户",
        "",
        (
            f"2 fixture VU / 2 synthetic aggregate slots 的完成吞吐为 "
            f"`{two_slot.get('completed_throughput_rps')}` RPS；fixture error "
            f"为 `{two_slot_error.get('numerator')}/{two_slot_error.get('denominator')}`，"
            f"timeout 为 `{two_slot_timeout.get('numerator')}/{two_slot_timeout.get('denominator')}`，"
            f"实际观测 max active 为 `{two_slot.get('aggregate_max_active')}`。"
            "它只证明内存 HTTP transport、两槽 semaphore 和分母统计能工作。"
        ),
        "",
        "它**没有**运行真实 Worker lane、任务 ledger、Provider、数据库冲突或幂等键，所以不能把该结果当作 2 lane/cap=2 抓取验收。幂等和真实 conflict rate 仍未验证。",
        "",
        "### B. 真实 20-survivor 抓取链",
        "",
    ]
    historical = report.get("historical_20_survivor_chain")
    if historical is None:
        split_block.extend(
            [
                "本次没有附加 Round 12 历史链 artifact；真实抓取 ETA 不在本报告中推断。",
                "",
            ]
        )
    else:
        split_block.extend(
            [
                (
                    f"Round 12 本地历史证据是一条 20-survivor discovery chain："
                    f"`{historical['task_count']}` 个拆分任务，wall time "
                    f"`{historical['full_chain_wall_seconds'] / 60:.3f}` 分钟，"
                    f"可归因 service time `{historical['sampled_service_seconds'] / 60:.3f}` 分钟，"
                    f"覆盖 `{historical['sampled_to_wall_coverage'] * 100:.3f}%`。"
                ),
                "",
                "| 历史链模型 | Lane | Makespan | Attempts/h | Error rate | Conflict rate |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        labels = {
            "current_guarded_cap_1": "current cap=1",
            "two_slot_candidate_cap_2": "候选 cap=2",
            "ideal_unbounded_lower_bound": "理想无资源上限",
        }
        for name, profile in historical["profiles"].items():
            for scenario in profile["scenarios"]:
                error = scenario["error_rate"]
                conflict = scenario["conflict_rate"]
                error_value = "null" if error["rate"] is None else str(error["rate"])
                conflict_value = (
                    "null" if conflict["rate"] is None else str(conflict["rate"])
                )
                split_block.append(
                    f"| {labels[name]} | {scenario['lanes']} | "
                    f"{scenario['makespan_minutes']:.3f} 分钟 | "
                    f"{scenario['attempts_per_hour']:.3f} | "
                    f"{error_value} (den={error['denominator']}) | "
                    f"{conflict_value} (den={conflict['denominator']}) |"
                )
        split_block.extend(
            [
                "",
                "`current cap=1` 从 1 lane 的 102.692 分钟到 8 lane 仍需 89.415 分钟；候选 2 lane/cap=2 模型是 51.585 分钟；14.049 分钟只是 8 lane 无 Provider/DB/R2/重试/成本约束的数学下限。三者都不是新的实际运行测量。",
                "",
                "历史 42-task 样本没有 outcome/conflict 标签，error/conflict 分母均为 0，因此必须保持 `null`，不能用 HTTP fixture 的 0% 填入。",
                "",
                "**下一门仍是隔离 staging：**先 1 lane baseline，再 2 lane/cap=2；记录 task-ledger attempt/success/retry/429/conflict/idempotency/cost 与 end-to-end、service、queue-wait p50/p95/p99。只有这些分母完整且门禁通过，才可讨论 4/8 lane。",
                "",
            ]
        )
    lines[insertion_at:insertion_at] = split_block


def write_exclusive(path: Path, content: bytes) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if fd >= 0:
            os.close(fd)
