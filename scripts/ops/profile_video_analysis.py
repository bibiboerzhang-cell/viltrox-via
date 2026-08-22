#!/usr/bin/env python3
"""final_v1 视频深析链剖面:按阶段 / 按平台的 p50 / p95(只读,零 LLM 成本)。

数据源(全部只读):
* ``vkpi_analysis_cache``(derive_method=video_analysis_final_v1,status=ready)
  - ``result.cost.latency_ms``            任务总耗时(旧行只有这一项)
  - ``result.cost.stage_timings_ms``      2026-08 埋点后:subtitles / youtube_direct / download /
                                          upload / file_active_wait / cache_setup / generation /
                                          cleanup / media_resolve / worker_download /
                                          analyzer_subprocess / r2_warm / cost_record
  - ``result.raw_gemini_video.youtube_direct``  直链尝试 + 降级真因
* ``apify_jobs.payload.diagnostics``      persist / followups / total_ms + 失败路径的直链/下载诊断
* ``vkpi_llm_calls``(purpose=audit_video_analysis)  Gemini 单次 generateContent 延迟
  (旧行的 gemini_call 阶段靠它回填;按 metadata.parent_job_id 关联)

用法:
    PYTHONPATH=.:scripts:backend .venv/bin/python scripts/ops/profile_video_analysis.py \
        --database-url postgresql://postgres@127.0.0.1:54333/vkpi_closeout --limit 300

输出 JSON(stdout):{stages_p50_p95, by_platform, youtube_direct, failures,
instrumentation_coverage, instrumentation_gaps, ...}。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Iterable, Sequence

import psycopg
from psycopg.rows import dict_row


FINAL_V1 = "video_analysis_final_v1"
# 阶段汇总口径:把埋点细阶段归并到任务书要求的五大阶段。
STAGE_FAMILIES: dict[str, tuple[str, ...]] = {
    "download": ("subtitles", "media_resolve", "worker_download", "download"),
    "upload": ("upload", "file_active_wait", "r2_warm"),
    "gemini_call": ("cache_setup", "youtube_direct", "generation"),
    "judges": (),  # 裁判/QA 不在 account_deep 主链(独立 derive_method,按需触发)
    "persist": ("cost_record", "persist", "followups", "cleanup"),
}
OTHER_FAMILY = "other_overhead"


def percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def summarize(values: Sequence[float]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None}
    return {
        "n": len(vals),
        "p50_ms": round(percentile(vals, 0.5) or 0.0),
        "p95_ms": round(percentile(vals, 0.95) or 0.0),
        "mean_ms": round(sum(vals) / len(vals)),
    }


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _method_path(method: str) -> str:
    text = str(method or "")
    if text.startswith("gemini_direct_"):
        return "youtube_direct"
    if text.startswith("gemini_local_fileapi_"):
        return "local_fileapi"
    if text.startswith("gemini_fileapi_"):
        return "youtube_fileapi_fallback"
    return text or "unknown"


def fetch_rows(conn: psycopg.Connection[Any], *, limit: int, days: int, platform: str) -> list[dict[str, Any]]:
    sql = """
        SELECT
          c.id AS cache_id,
          c.target_id,
          c.updated_at,
          COALESCE(NULLIF(c.result->'source'->>'platform', ''), 'unknown') AS platform,
          COALESCE(c.result->'raw_gemini_video'->>'method', '') AS method,
          (c.result->'cost'->>'latency_ms')::float AS latency_ms,
          c.result->'cost'->'stage_timings_ms' AS stage_timings,
          c.result->'raw_gemini_video'->'youtube_direct' AS youtube_direct,
          c.result->'raw_gemini_video'->'download_diagnostics' AS download_diagnostics,
          NULLIF(c.result->>'job_id', '')::bigint AS job_id,
          j.payload->'diagnostics' AS job_diagnostics,
          j.attempts,
          EXTRACT(EPOCH FROM (j.updated_at - j.started_at)) * 1000 AS job_wall_ms
        FROM vkpi_analysis_cache c
        LEFT JOIN apify_jobs j ON j.id = NULLIF(c.result->>'job_id', '')::bigint
        WHERE c.derive_method = %s
          AND c.status = 'ready'
          AND c.updated_at >= NOW() - make_interval(days => %s)
          AND (%s = '' OR COALESCE(c.result->'source'->>'platform', '') = %s)
        ORDER BY c.updated_at DESC
        LIMIT %s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (FINAL_V1, int(days), platform, platform, int(limit)))
        return [dict(row) for row in cur.fetchall()]


def fetch_llm_call_latency(conn: psycopg.Connection[Any], job_ids: Iterable[int]) -> dict[int, list[dict[str, Any]]]:
    ids = sorted({int(j) for j in job_ids if j})
    if not ids:
        return {}
    sql = """
        SELECT
          NULLIF(metadata_json::jsonb->>'parent_job_id', '')::bigint AS job_id,
          metadata_json::jsonb->>'subphase' AS subphase,
          status,
          latency_ms
        FROM vkpi_llm_calls
        WHERE purpose = 'audit_video_analysis'
          AND NULLIF(metadata_json::jsonb->>'parent_job_id', '')::bigint = ANY(%s)
    """
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (ids,))
        for row in cur.fetchall():
            if row.get("job_id") is not None:
                out[int(row["job_id"])].append(dict(row))
    return out


def fetch_failures(conn: psycopg.Connection[Any], *, days: int, limit: int) -> list[dict[str, Any]]:
    sql = """
        SELECT
          status,
          COALESCE(last_error_category, '') AS category,
          LEFT(COALESCE(last_error, ''), 120) AS error_head,
          COALESCE(payload->>'platform', payload->>'platform_by_host', '') AS platform,
          payload->'diagnostics' AS diagnostics
        FROM apify_jobs
        WHERE job_type = 'video'
          AND payload->>'derive_method' = %s
          AND status IN ('failed', 'triage', 'blocked')
          AND updated_at >= NOW() - make_interval(days => %s)
        ORDER BY updated_at DESC
        LIMIT %s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (FINAL_V1, int(days), int(limit)))
        return [dict(row) for row in cur.fetchall()]


def build_report(
    rows: list[dict[str, Any]],
    llm_calls: dict[int, list[dict[str, Any]]],
    failures: list[dict[str, Any]],
    *,
    limit: int,
    days: int,
) -> dict[str, Any]:
    totals: list[float] = []
    by_stage: dict[str, list[float]] = defaultdict(list)
    by_family: dict[str, list[float]] = defaultdict(list)
    by_platform_total: dict[str, list[float]] = defaultdict(list)
    by_platform_path: dict[str, Counter[str]] = defaultdict(Counter)
    by_platform_family: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_path_total: dict[str, list[float]] = defaultdict(list)
    instrumented = 0
    legacy_gemini_backfilled = 0
    direct_attempted = 0
    direct_success = 0
    direct_fallback_reasons: Counter[str] = Counter()
    direct_attempt_errors: Counter[str] = Counter()
    download_stderr: Counter[str] = Counter()

    for row in rows:
        platform = str(row.get("platform") or "unknown")
        path = _method_path(str(row.get("method") or ""))
        latency = row.get("latency_ms")
        job_diag = _as_dict(row.get("job_diagnostics"))
        total_ms = job_diag.get("total_ms") if isinstance(job_diag.get("total_ms"), (int, float)) else latency
        if total_ms is not None:
            totals.append(float(total_ms))
            by_platform_total[platform].append(float(total_ms))
            by_path_total[path].append(float(total_ms))
        by_platform_path[platform][path] += 1

        stage_timings = _as_dict(row.get("stage_timings"))
        job_stage_timings = _as_dict(job_diag.get("stage_timings_ms"))
        merged = {**stage_timings, **job_stage_timings}
        if merged:
            instrumented += 1
        else:
            # 旧行:gemini_call 用 vkpi_llm_calls 的 generateContent 延迟回填(仅 success 调用之和)
            calls = llm_calls.get(int(row["job_id"])) if row.get("job_id") else None
            if calls:
                gen_ms = sum(float(c.get("latency_ms") or 0) for c in calls if c.get("latency_ms"))
                if gen_ms > 0:
                    merged = {"generation": gen_ms, "_backfilled_from_llm_calls": 1}
                    legacy_gemini_backfilled += 1
        family_sum = 0.0
        for stage, value in merged.items():
            if stage.startswith("_"):
                continue
            try:
                ms = float(value)
            except (TypeError, ValueError):
                continue
            by_stage[stage].append(ms)
            for family, members in STAGE_FAMILIES.items():
                if stage in members:
                    by_family[family].append(ms)
                    by_platform_family[platform][family].append(ms)
                    family_sum += ms
                    break
        if merged and total_ms is not None and not merged.get("_backfilled_from_llm_calls"):
            overhead = max(0.0, float(total_ms) - family_sum)
            by_family[OTHER_FAMILY].append(overhead)
            by_platform_family[platform][OTHER_FAMILY].append(overhead)

        direct = _as_dict(row.get("youtube_direct")) or _as_dict(job_diag.get("youtube_direct"))
        if direct.get("attempted"):
            direct_attempted += 1
            if direct.get("success"):
                direct_success += 1
            else:
                direct_fallback_reasons[str(direct.get("fallback_reason") or "")[:100] or "unknown"] += 1
            for attempt in direct.get("attempts") or []:
                if isinstance(attempt, dict) and not attempt.get("ok"):
                    direct_attempt_errors[str(attempt.get("error") or "")[:100] or "unknown"] += 1
        dl = _as_dict(row.get("download_diagnostics")) or _as_dict(job_diag.get("download_diagnostics"))
        if dl and dl.get("returncode") not in (0, None):
            download_stderr[str(dl.get("stderr_tail") or "")[-100:] or "empty_stderr"] += 1

    failure_buckets: Counter[str] = Counter()
    failure_direct_reasons: Counter[str] = Counter()
    failure_download_stderr: Counter[str] = Counter()
    failure_platform: Counter[str] = Counter()
    for item in failures:
        failure_buckets[f"{item.get('status')}:{item.get('category') or '-'}:{item.get('error_head') or ''}"] += 1
        failure_platform[str(item.get("platform") or "unknown")] += 1
        diag = _as_dict(item.get("diagnostics"))
        direct = _as_dict(diag.get("youtube_direct"))
        if direct.get("attempted") and not direct.get("success"):
            failure_direct_reasons[str(direct.get("fallback_reason") or "")[:100] or "unknown"] += 1
        dl = _as_dict(diag.get("download_diagnostics"))
        if dl:
            failure_download_stderr[str(dl.get("stderr_tail") or "")[-120:] or "empty_stderr"] += 1

    n_rows = len(rows)
    coverage = instrumented / n_rows if n_rows else 0.0
    gaps: list[str] = []
    if coverage < 1.0:
        gaps.append(
            f"{n_rows - instrumented}/{n_rows} 条 ready 结果没有 stage_timings_ms(埋点前旧行);"
            "部署本刀后新任务自动带阶段耗时,旧行只能给 total + llm_calls 回填的 gemini_call。"
        )
    if not any(_as_dict(r.get("job_diagnostics")).get("stage_timings_ms") for r in rows):
        gaps.append("apify_jobs.payload.diagnostics 尚无 persist/followups 阶段(worker 未部署埋点版)。")
    if not direct_attempted:
        gaps.append("无 youtube_direct 诊断(埋点前直链失败原因不落库;部署后自动补齐)。")
    gaps.append("judges/keyframe_qa 不在 account_deep 主链(独立 derive_method、按需触发),不计阶段。")

    stage_report = {stage: summarize(vals) for stage, vals in sorted(by_stage.items())}
    family_report = {family: summarize(by_family.get(family, [])) for family in list(STAGE_FAMILIES) + [OTHER_FAMILY]}
    platform_report: dict[str, Any] = {}
    for platform in sorted(by_platform_total):
        platform_report[platform] = {
            "total": summarize(by_platform_total[platform]),
            "paths": dict(by_platform_path[platform]),
            "families": {
                family: summarize(vals) for family, vals in sorted(by_platform_family[platform].items())
            },
        }
    return {
        "schema": "vkpi-video-analysis-profile/v1",
        "window": {"limit": limit, "days": days, "rows": n_rows},
        "total": summarize(totals),
        "stages_p50_p95": {"families": family_report, "raw_stages": stage_report},
        "by_platform": platform_report,
        "by_path": {path: summarize(vals) for path, vals in sorted(by_path_total.items())},
        "youtube_direct": {
            "attempted": direct_attempted,
            "success": direct_success,
            "hit_rate": round(direct_success / direct_attempted, 3) if direct_attempted else None,
            "fallback_reasons": direct_fallback_reasons.most_common(10),
            "attempt_errors": direct_attempt_errors.most_common(10),
            "download_stderr_on_ready_rows": download_stderr.most_common(5),
        },
        "failures": {
            "rows": len(failures),
            "by_platform": dict(failure_platform),
            "buckets": failure_buckets.most_common(15),
            "youtube_direct_fallback_reasons": failure_direct_reasons.most_common(10),
            "download_stderr_tails": failure_download_stderr.most_common(10),
        },
        "instrumentation_coverage": {
            "instrumented_rows": instrumented,
            "legacy_rows_gemini_backfilled_from_llm_calls": legacy_gemini_backfilled,
            "coverage_ratio": round(coverage, 3),
        },
        "instrumentation_gaps": gaps,
    }


def _database_url(explicit: str) -> str:
    for candidate in (explicit, os.environ.get("DATABASE_URL"), os.environ.get("LOCAL_DATABASE_URL")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    raise SystemExit("database url required (--database-url or DATABASE_URL)")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--limit", type=int, default=200, help="最近 N 条 ready final_v1")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--platform", default="", help="youtube / instagram / tiktok;空=全部")
    parser.add_argument("--failures-limit", type=int, default=400)
    parser.add_argument("--output", default="", help="写文件;空=stdout")
    args = parser.parse_args(argv)

    url = _database_url(args.database_url)
    with psycopg.connect(url, autocommit=True) as conn:
        rows = fetch_rows(conn, limit=max(1, args.limit), days=max(1, args.days), platform=args.platform.strip().lower())
        llm_calls = fetch_llm_call_latency(conn, [r.get("job_id") for r in rows if r.get("job_id")])
        failures = fetch_failures(conn, days=max(1, args.days), limit=max(1, args.failures_limit))
    report = build_report(rows, llm_calls, failures, limit=args.limit, days=args.days)
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
