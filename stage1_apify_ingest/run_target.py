"""
Targeted Stage 1 runner.

This module keeps the Stage 1 asset contract in config.py untouched. It only
adds a production-oriented target queue, checkpoint, report, and per-KOL
timeout wrapper around runner.process_kol().
"""
from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from typing import Any
from urllib.parse import urlparse


def _load_default_env() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ[key] = value


_load_default_env()

from .apify_source import ApifySource, load_kol_list
from .config import SETTINGS
from .manifest import ManifestStore
from .runner import process_kol
from .storage import DualStore


NEGATIVE_STATUSES = {
    "inactive",
    "archived",
    "cancelled",
    "canceled",
    "deleted",
    "closed",
    "complete",
    "completed",
}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required; run_target does not fall back to local runtime defaults")
    if args.commit:
        _guard_commit_database(database_url, args.allow_local_db)
        _guard_commit_secrets()

    priority = _parse_priority(args.priority)
    SETTINGS.fetch_transcript = args.phase == "full"
    SETTINGS.fetch_storyboard = False
    SETTINGS.max_videos_per_kol = max(0, int(args.transcript_cap))

    started = _utc_now()
    deadline = time.monotonic() + args.daily_hours * 3600

    try:
        kols = load_kol_list(SETTINGS)
        meta = _load_priority_metadata(database_url)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    plan = _build_plan(kols, meta, priority)

    previous_state = _load_checkpoint(args.checkpoint)
    state = _initial_state(args, database_url, priority, started, plan, previous_state)
    prior_results = state["results"]
    runnable_plan = [
        item for item in plan
        if not _result_satisfies_phase(prior_results.get(item["kol_id"]), args.phase)
    ]
    state["queued_count"] = len(runnable_plan)
    state["skipped_completed_count"] = len(plan) - len(runnable_plan)
    _save_control_files(args, state)

    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"== Stage 1 target [{mode}] :: {len(plan)} KOLs ==")
    print(f"database: {_mask_database_url(database_url)}")
    print(f"priority buckets: {_fmt_counter(Counter(item['priority_bucket'] for item in plan))}")
    print(f"url_source buckets: {_fmt_counter(Counter(item.get('url_source') or 'unknown' for item in plan))}")
    print(f"checkpoint: {args.checkpoint}")
    print(f"report_dir: {args.report_dir}")
    if previous_state:
        skipped = len(plan) - len(runnable_plan)
        print(f"resume: loaded {len(previous_state.get('results') or {})} prior results; skipping {skipped} completed KOLs")

    if not args.commit:
        for item in runnable_plan[:30]:
            print(
                "[dry-run] "
                f"{item['kol_id']} {item['priority_bucket']} "
                f"followers={item.get('subscribers') or 0} "
                f"{item.get('url_source')} {item.get('channel')}"
            )
        if len(runnable_plan) > 30:
            print(f"[dry-run] ... {len(runnable_plan) - 30} more")
        print("== dry-run complete; no Apify/R2 asset writes ==")
        state["finished_at"] = _utc_now()
        state["status"] = "dry_run"
        _save_control_files(args, state)
        return 0

    completed = 0
    try:
        for item in runnable_plan:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("daily-hours budget exhausted; checkpoint saved")
                break
            timeout = max(1, min(float(args.per_channel_timeout), remaining))
            result = _run_one_with_timeout(
                item,
                phase=args.phase,
                timeout=timeout,
                transcript_cap=SETTINGS.max_videos_per_kol,
                fetch_transcript=SETTINGS.fetch_transcript,
            )
            state["results"][item["kol_id"]] = result
            state["updated_at"] = _utc_now()
            state["processed_count"] = len(state["results"])
            state["result_status_counts"] = _result_status_counts(state)
            _save_control_files(args, state)
            completed += 1
    except KeyboardInterrupt:
        state["status"] = "interrupted"
        state["updated_at"] = _utc_now()
        _save_control_files(args, state)
        print("Interrupted; checkpoint/report saved.")
        raise

    state["finished_at"] = _utc_now()
    state["result_status_counts"] = _result_status_counts(state)
    state["status"] = _final_status(state, len(plan))
    _save_control_files(args, state)
    print(f"== target run {state['status']} :: processed {completed}/{len(runnable_plan)} queued KOLs ==")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the targeted Stage 1 Apify ingest queue.")
    parser.add_argument("--phase", choices=["metadata", "full"], default="full")
    parser.add_argument("--daily-hours", type=float, default=8.0, help="wall-clock run budget in hours")
    parser.add_argument(
        "--priority",
        required=True,
        help="comma-separated priority order; supported: campaign,assignment,subscribers",
    )
    parser.add_argument("--transcript-window", choices=["all"], default="all")
    parser.add_argument("--transcript-cap", type=int, default=0, help="0 means no per-channel cap")
    parser.add_argument("--per-channel-timeout", type=int, default=900, help="seconds before a KOL is timed out")
    parser.add_argument("--commit", action="store_true", help="write assets to local+R2")
    parser.add_argument("--checkpoint", default=".state/stage1_target.json", help="JSON checkpoint path")
    parser.add_argument("--report-dir", required=True, help="directory for JSON/Markdown reports")
    parser.add_argument(
        "--allow-local-db",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def _parse_priority(value: str) -> list[str]:
    tokens = [token.strip().lower() for token in value.split(",") if token.strip()]
    allowed = {"campaign", "assignment", "subscribers"}
    unknown = [token for token in tokens if token not in allowed]
    if unknown:
        raise SystemExit(f"unsupported priority token(s): {', '.join(unknown)}")
    if not tokens:
        raise SystemExit("--priority must include at least one token")
    return tokens


def _load_priority_metadata(database_url: str) -> dict[str, dict[str, Any]]:
    sql = f"""
    COPY (
      WITH campaign_kols AS (
        SELECT DISTINCT a.kol_pool_id::text AS kol_id
        FROM vkpi_project_kol_assignments a
        JOIN vkpi_campaign_projects cp ON cp.project_id = a.project_id
        JOIN vkpi_campaigns c ON c.id = cp.campaign_id
        WHERE lower(coalesce(c.status, '')) NOT IN ({_sql_text_list(NEGATIVE_STATUSES)})
          AND lower(coalesce(a.stage_status, '')) NOT IN ({_sql_text_list(NEGATIVE_STATUSES)})
        UNION
        SELECT DISTINCT kc.kol_id::text AS kol_id
        FROM kol_campaigns kc
        WHERE lower(coalesce(kc.status, '')) NOT IN ({_sql_text_list(NEGATIVE_STATUSES)})
      ),
      assignment_kols AS (
        SELECT DISTINCT kol_pool_id::text AS kol_id
        FROM vkpi_project_kol_assignments
        WHERE lower(coalesce(stage_status, '')) NOT IN ({_sql_text_list(NEGATIVE_STATUSES)})
      )
      SELECT
        k.id::text AS kol_id,
        coalesce(k.display_name, '') AS display_name,
        coalesce(k.handle, '') AS db_handle,
        coalesce(k.followers, 0)::bigint AS subscribers,
        (ck.kol_id IS NOT NULL) AS has_campaign,
        (ak.kol_id IS NOT NULL) AS has_assignment
      FROM vkpi_kol_pool k
      LEFT JOIN campaign_kols ck ON ck.kol_id = k.id::text
      LEFT JOIN assignment_kols ak ON ak.kol_id = k.id::text
      WHERE lower(coalesce(k.platform, '')) = 'youtube'
    ) TO STDOUT WITH CSV HEADER
    """
    rows = _psql_csv(database_url, sql)
    return {row["kol_id"]: _shape_meta(row) for row in rows if row.get("kol_id")}


def _build_plan(kols: list[dict], meta: dict[str, dict[str, Any]], priority: list[str]) -> list[dict]:
    plan: list[dict] = []
    for kol in kols:
        item = dict(kol)
        row = meta.get(item["kol_id"], {})
        item.update(row)
        item["priority_bucket"] = _priority_bucket(row, priority)
        item["priority_rank"] = priority.index(item["priority_bucket"]) if item["priority_bucket"] in priority else 999
        plan.append(item)
    return sorted(
        plan,
        key=lambda item: (
            item["priority_rank"],
            -int(item.get("subscribers") or 0),
            int(item["kol_id"]) if str(item["kol_id"]).isdigit() else str(item["kol_id"]),
        ),
    )


def _priority_bucket(meta: dict[str, Any], priority: list[str]) -> str:
    for token in priority:
        if token == "campaign" and meta.get("has_campaign"):
            return token
        if token == "assignment" and meta.get("has_assignment"):
            return token
        if token == "subscribers":
            return token
    return "unprioritized"


def _run_one_with_timeout(
    kol: dict,
    *,
    phase: str,
    timeout: float,
    transcript_cap: int,
    fetch_transcript: bool,
) -> dict:
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(
        target=_process_kol_child,
        args=(kol, transcript_cap, fetch_transcript, queue),
        daemon=False,
    )
    started = _utc_now()
    proc.start()
    proc.join(timeout)
    if proc.is_alive():
        proc.terminate()
        proc.join(30)
        if proc.is_alive():
            proc.kill()
            proc.join(10)
        result = {
            "kol_id": kol["kol_id"],
            "status": "timeout",
            "phase": phase,
            "started_at": started,
            "finished_at": _utc_now(),
            "timeout_s": timeout,
            "priority_bucket": kol.get("priority_bucket"),
        }
        _mark_kol_level_result(kol["kol_id"], "timeout", result)
        print(f"[{kol['kol_id']}] timeout after {timeout:.0f}s")
        return result

    payload = _queue_get(queue)
    if proc.exitcode == 0 and payload:
        payload.setdefault("started_at", started)
        payload.setdefault("finished_at", _utc_now())
        payload.setdefault("priority_bucket", kol.get("priority_bucket"))
        payload.setdefault("phase", phase)
        if payload.get("status") == "failed":
            _mark_kol_level_result(kol["kol_id"], "failed", payload)
            print(f"[{kol['kol_id']}] failed: {payload.get('error', '')}")
        return payload

    error = payload.get("error") if payload else f"child exitcode={proc.exitcode}"
    result = {
        "kol_id": kol["kol_id"],
        "status": "failed",
        "phase": phase,
        "error": error,
        "started_at": started,
        "finished_at": _utc_now(),
        "priority_bucket": kol.get("priority_bucket"),
    }
    _mark_kol_level_result(kol["kol_id"], "failed", result)
    print(f"[{kol['kol_id']}] failed: {error}")
    return result


def _process_kol_child(kol: dict, transcript_cap: int, fetch_transcript: bool, queue) -> None:
    try:
        SETTINGS.max_videos_per_kol = transcript_cap
        SETTINGS.fetch_transcript = fetch_transcript
        SETTINGS.fetch_storyboard = False
        store = DualStore(SETTINGS, commit=True)
        man = ManifestStore(store)
        src = ApifySource(SETTINGS)
        global_manifest = man.load_global()
        process_kol(kol, src, store, man, global_manifest, retry_only=False)
        manifest = man.load_kol(kol["kol_id"])
        queue.put(_summarize_kol_manifest(kol, manifest))
    except Exception as exc:
        queue.put({"kol_id": kol.get("kol_id"), "status": "failed", "error": repr(exc)})


def _mark_kol_level_result(kol_id: str, status: str, result: dict) -> None:
    store = DualStore(SETTINGS, commit=True)
    man = ManifestStore(store)
    manifest = man.load_kol(kol_id)
    manifest["status"] = status
    manifest[status] = result
    man.save_kol(manifest)
    global_manifest = man.load_global()
    man.mark_kol(global_manifest, kol_id, status, 0)
    man.save_global(global_manifest)


def _summarize_kol_manifest(kol: dict, manifest: dict) -> dict:
    statuses = Counter(
        str(entry.get("status") or "unknown")
        for entry in (manifest.get("videos") or {}).values()
        if isinstance(entry, dict)
    )
    status = manifest.get("status")
    if not status:
        failed = statuses.get("failed", 0)
        status = "partial" if failed else "done"
    return {
        "kol_id": kol["kol_id"],
        "status": status,
        "phase": "metadata" if not SETTINGS.fetch_transcript else "full",
        "url_source": kol.get("url_source"),
        "priority_bucket": kol.get("priority_bucket"),
        "video_status_counts": dict(statuses),
        "updated_at": manifest.get("updated_at"),
    }


def _initial_state(
    args: argparse.Namespace,
    database_url: str,
    priority: list[str],
    started: str,
    plan: list[dict],
    previous_state: dict | None = None,
) -> dict:
    previous_results = {}
    if previous_state and isinstance(previous_state.get("results"), dict):
        current_ids = {item["kol_id"] for item in plan}
        previous_results = {
            kol_id: result
            for kol_id, result in previous_state["results"].items()
            if kol_id in current_ids and isinstance(result, dict)
        }
    return {
        "run_id": started.replace(":", "").replace("-", "").split(".")[0],
        "status": "running" if args.commit else "dry_run_planned",
        "started_at": started,
        "updated_at": started,
        "resume": {
            "loaded": bool(previous_state),
            "previous_run_id": (previous_state or {}).get("run_id"),
            "previous_status": (previous_state or {}).get("status"),
            "previous_result_count": len(previous_results),
        },
        "database": _mask_database_url(database_url),
        "args": {
            "phase": args.phase,
            "daily_hours": args.daily_hours,
            "priority": priority,
            "transcript_window": args.transcript_window,
            "transcript_cap": args.transcript_cap,
            "per_channel_timeout": args.per_channel_timeout,
            "commit": args.commit,
        },
        "planned_count": len(plan),
        "processed_count": len(previous_results),
        "result_status_counts": dict(Counter(
            str(result.get("status") or "unknown")
            for result in previous_results.values()
        )),
        "priority_bucket_counts": dict(Counter(item["priority_bucket"] for item in plan)),
        "url_source_counts": dict(Counter(item.get("url_source") or "unknown" for item in plan)),
        "plan": [_plan_row(item) for item in plan],
        "results": previous_results,
    }


def _plan_row(item: dict) -> dict:
    return {
        "kol_id": item["kol_id"],
        "priority_bucket": item.get("priority_bucket"),
        "url_source": item.get("url_source"),
        "channel": item.get("channel"),
        "display_name": item.get("display_name", ""),
        "db_handle": item.get("db_handle", ""),
        "subscribers": int(item.get("subscribers") or 0),
        "has_campaign": bool(item.get("has_campaign")),
        "has_assignment": bool(item.get("has_assignment")),
    }


def _save_control_files(args: argparse.Namespace, state: dict) -> None:
    checkpoint = Path(args.checkpoint)
    report_dir = Path(args.report_dir)
    _write_json(checkpoint, state)
    report_dir.mkdir(parents=True, exist_ok=True)
    _write_json(report_dir / "stage1_target_latest.json", state)
    _write_markdown(report_dir / "stage1_target_latest.md", state)


def _load_checkpoint(path: str) -> dict | None:
    checkpoint = Path(path)
    if not checkpoint.exists() or checkpoint.stat().st_size == 0:
        return None
    try:
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"checkpoint is not valid JSON: {checkpoint}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"checkpoint root must be an object: {checkpoint}")
    return data


def _result_satisfies_phase(result: Any, phase: str) -> bool:
    if not isinstance(result, dict):
        return False
    status = str(result.get("status") or "")
    if status == "skipped_no_url":
        return True
    if status != "done":
        return False
    result_phase = str(result.get("phase") or "full")
    if phase == "metadata":
        return result_phase in {"metadata", "full"}
    return result_phase == "full"


def _result_status_counts(state: dict) -> dict[str, int]:
    return dict(Counter(
        str(result.get("status") or "unknown")
        for result in (state.get("results") or {}).values()
        if isinstance(result, dict)
    ))


def _final_status(state: dict, planned_count: int) -> str:
    if len(state.get("results") or {}) < planned_count:
        return "checkpointed"
    counts = _result_status_counts(state)
    issue_count = sum(counts.get(status, 0) for status in ("failed", "timeout", "partial"))
    return "complete_with_issues" if issue_count else "complete"


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_markdown(path: Path, state: dict) -> None:
    lines = [
        "# Stage 1 Target Report",
        "",
        f"- status: {state.get('status')}",
        f"- started_at: {state.get('started_at')}",
        f"- updated_at: {state.get('updated_at')}",
        f"- database: {state.get('database')}",
        f"- planned_count: {state.get('planned_count')}",
        f"- queued_count: {state.get('queued_count')}",
        f"- processed_count: {state.get('processed_count')}",
        f"- skipped_completed_count: {state.get('skipped_completed_count')}",
        f"- resume: {state.get('resume')}",
        f"- result_status_counts: {state.get('result_status_counts')}",
        f"- priority_bucket_counts: {state.get('priority_bucket_counts')}",
        f"- url_source_counts: {state.get('url_source_counts')}",
        "",
        "## Recent Results",
        "",
    ]
    for kol_id, result in list((state.get("results") or {}).items())[-30:]:
        lines.append(f"- {kol_id}: {result.get('status')} {result.get('video_status_counts', '')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _psql_csv(database_url: str, sql: str) -> list[dict]:
    proc = subprocess.run(
        ["psql", database_url, "-X", "-q", "-c", sql],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
    return list(csv.DictReader(proc.stdout.splitlines()))


def _shape_meta(row: dict) -> dict:
    return {
        "display_name": row.get("display_name", ""),
        "db_handle": row.get("db_handle", ""),
        "subscribers": int(row.get("subscribers") or 0),
        "has_campaign": _bool_text(row.get("has_campaign")),
        "has_assignment": _bool_text(row.get("has_assignment")),
    }


def _guard_commit_database(database_url: str, allow_local_db: bool) -> None:
    if allow_local_db:
        return
    if any(token in database_url for token in ("…", "...", "<prod>", "<")):
        raise SystemExit("DATABASE_URL still contains a placeholder; replace it with the real production URL")
    parsed = urlparse(database_url)
    host = (parsed.hostname or "").lower()
    db_name = parsed.path.strip("/")
    if host in {"", "localhost", "127.0.0.1", "::1"} or parsed.port == 54329 or db_name == "viltrox2":
        raise SystemExit(
            "--commit requires a production DATABASE_URL for run_target; "
            f"got {_mask_database_url(database_url)}"
        )


def _guard_commit_secrets() -> None:
    required_any = {
        "APIFY_TOKEN/APIFY_API_TOKEN": ("APIFY_TOKEN", "APIFY_API_TOKEN"),
        "R2_ACCESS_KEY_ID": ("R2_ACCESS_KEY_ID",),
        "R2_SECRET_ACCESS_KEY": ("R2_SECRET_ACCESS_KEY",),
        "R2 endpoint/account": ("R2_ENDPOINT", "R2_ENDPOINT_URL", "R2_ACCOUNT_ID"),
    }
    missing = [label for label, names in required_any.items() if not any(os.environ.get(name) for name in names)]
    if missing:
        raise SystemExit("missing commit env var(s): " + ", ".join(missing))


def _mask_database_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    host = parsed.hostname or ""
    user = parsed.username or ""
    db_name = parsed.path.strip("/")
    port = f":{parsed.port}" if parsed.port else ""
    user_part = f"{user}:***@" if user else ""
    return f"{parsed.scheme}://{user_part}{host}{port}/{db_name}"


def _sql_text_list(values: set[str]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in sorted(values))


def _fmt_counter(counter: Counter) -> str:
    return " / ".join(f"{key}={counter[key]}" for key in sorted(counter)) or "(empty)"


def _bool_text(value: Any) -> bool:
    return str(value).strip().lower() in {"t", "true", "1", "yes", "y"}


def _queue_get(queue) -> dict:
    try:
        return queue.get(timeout=1)
    except Empty:
        return {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    sys.exit(main())
