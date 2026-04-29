#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean
from typing import Any

try:
    import aiohttp
except Exception as exc:  # pragma: no cover
    raise SystemExit("aiohttp is required. Install project dependencies first.") from exc

from runtime_env import apply_runtime_env
from stdout_utils import out, out_json

apply_runtime_env()

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.security import hash_password, make_token  # noqa: E402
from app.db.connection import close_db_runtime, db_connection_sync_scope, get_conn  # noqa: E402
from app.db.repositories.users import creator_code_exists, generate_creator_code  # noqa: E402


LOG_DIR = ROOT / "runtime" / "logs"
TMP_DIR = ROOT / "runtime" / "tmp"
LOG_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

PUBLIC_BASE = os.environ.get("REAL_VIDEO_SMOKE_BASE", "http://127.0.0.1:8000").rstrip("/")
TOTAL = int(os.environ.get("REAL_VIDEO_SMOKE_TOTAL", "300"))
CHAIN_CONCURRENCY = int(os.environ.get("REAL_VIDEO_SMOKE_CONCURRENCY", str(TOTAL)))
CREATOR_POOL_SIZE = int(os.environ.get("REAL_VIDEO_SMOKE_CREATOR_POOL", str(max(60, math.ceil(TOTAL / 5)))))
VIDEO_GENERATE_CONCURRENCY = int(os.environ.get("REAL_VIDEO_SMOKE_VIDEO_CONCURRENCY", "10"))
HTTP_TIMEOUT_SEC = float(os.environ.get("REAL_VIDEO_SMOKE_HTTP_TIMEOUT_SEC", "180"))
POLL_TIMEOUT_SEC = int(os.environ.get("REAL_VIDEO_SMOKE_POLL_TIMEOUT_SEC", "900"))
POLL_INTERVAL_SEC = float(os.environ.get("REAL_VIDEO_SMOKE_POLL_INTERVAL_SEC", "5"))
PASSWORD = os.environ.get("REAL_VIDEO_SMOKE_PASSWORD", "RealSmokePass1!")
RUN_STAMP = os.environ.get("REAL_VIDEO_SMOKE_STAMP", time.strftime("%Y%m%d-%H%M%S"))
VIDEO_DIR = TMP_DIR / f"real-video-smoke-{RUN_STAMP}"
RUN_SEED = int(hashlib.sha256(RUN_STAMP.encode("utf-8")).hexdigest()[:8], 16)

TERMINAL_STATUSES = {"done", "partial_done", "failed", "prefilter_rejected"}


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[idx]


def summarize_latencies(values: list[float]) -> dict[str, float]:
    return {
        "avg": round(mean(values), 2) if values else 0.0,
        "p50": round(percentile(values, 50), 2),
        "p95": round(percentile(values, 95), 2),
        "p99": round(percentile(values, 99), 2),
        "max": round(max(values), 2) if values else 0.0,
    }


def _utc(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts or time.time()))


def ensure_load_creator(index: int) -> dict[str, Any]:
    email = f"real.video.smoke.{RUN_STAMP}.{index:03d}@example.com".lower()
    name = f"Real Video Smoke {index:03d}"
    with db_connection_sync_scope():
        conn = get_conn()
        now = _utc()
        aged = _utc(time.time() - 45 * 86400)
        row = conn.execute("SELECT id, creator_code FROM users WHERE lower(email)=?", (email,)).fetchone()
        if row:
            user_id = int(row["id"])
            conn.execute(
                """
                UPDATE users
                SET created_at=?,
                    password_hash=?,
                    name=?,
                    status='approved',
                    role='creator',
                    tier_status='founder',
                    email_verified=1,
                    social_verified=1,
                    trust_score=95,
                    trust_updated_at=?
                WHERE id=?
                """,
                (aged, hash_password(PASSWORD), name, now, user_id),
            )
            conn.commit()
            return {"id": user_id, "email": email, "token": make_token(user_id, "creator")}

        creator_code = ""
        for attempt in range(20):
            candidate = generate_creator_code(conn, offset=index + attempt)
            if not creator_code_exists(conn, candidate):
                creator_code = candidate
                break
        if not creator_code:
            raise RuntimeError("Could not allocate creator_code for smoke creator")

        row = conn.execute(
            """
            INSERT INTO users (
                created_at, email, password_hash, name, creator_code,
                status, role, tier_status, email_verified, social_verified,
                trust_score, trust_updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            RETURNING id
            """,
            (
                aged,
                email,
                hash_password(PASSWORD),
                name,
                creator_code,
                "approved",
                "creator",
                "founder",
                1,
                1,
                95,
                now,
            ),
        ).fetchone()
        conn.commit()
        user_id = int(row["id"])
    return {"id": user_id, "email": email, "token": make_token(user_id, "creator")}


def ensure_creator_pool() -> list[dict[str, Any]]:
    creators = [ensure_load_creator(i + 1) for i in range(CREATOR_POOL_SIZE)]
    out(f"[real-video-smoke] creator_pool={len(creators)}")
    return creators


def ffmpeg_filter(index: int) -> str:
    colors = ["black", "white", "gray", "red", "green", "blue", "yellow", "magenta", "cyan"]
    filters: list[str] = []
    digest = hashlib.sha256(f"{RUN_STAMP}:{RUN_SEED}:{index}".encode("utf-8")).digest()
    cell_w = 40
    cell_h = 30
    for row in range(6):
        for col in range(8):
            slot = row * 8 + col
            value = digest[slot % len(digest)] ^ digest[(slot * 7 + index) % len(digest)]
            color = colors[value % len(colors)]
            x = col * cell_w
            y = row * cell_h
            filters.append(f"drawbox=x={x}:y={y}:w={cell_w}:h={cell_h}:color={color}@0.95:t=fill")
    for lane in range(4):
        value = digest[(lane * 5) % len(digest)]
        x = (value * 11 + index * 13 + lane * 31) % 280
        y = (value * 7 + index * 17 + lane * 23) % 145
        color = colors[(value + lane) % len(colors)]
        filters.append(f"drawbox=x={x}:y={y}:w=44:h=28:color={color}@1.0:t=fill")
    return ",".join(filters)


def generate_video(index: int) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found; cannot generate real MP4 smoke videos")
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    out_path = VIDEO_DIR / f"real-video-{index:04d}.mp4"
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:size=320x180:rate=12:duration=0.75",
        "-vf",
        ffmpeg_filter(index),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "32",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-metadata",
        f"comment=real-video-smoke-{RUN_STAMP}-{index}",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return out_path


def generate_videos(total: int) -> list[Path]:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=VIDEO_GENERATE_CONCURRENCY) as pool:
        futures = [pool.submit(generate_video, i + 1) for i in range(total)]
        videos = []
        for i, future in enumerate(futures, 1):
            videos.append(future.result())
            if i % 50 == 0 or i == total:
                out(f"[real-video-smoke] generated={i}/{total}")
    elapsed = time.perf_counter() - started
    out(f"[real-video-smoke] video_generation_sec={elapsed:.2f} dir={VIDEO_DIR}")
    return videos


async def decode_json(response: aiohttp.ClientResponse) -> tuple[dict[str, Any], str]:
    raw = await response.text()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    return payload, raw


async def upload_then_audit(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    *,
    ordinal: int,
    creator: dict[str, Any],
    video_path: Path,
) -> dict[str, Any]:
    async with semaphore:
        title = f"Real video smoke {RUN_STAMP} #{ordinal:04d}"
        upload_started = time.perf_counter()
        result: dict[str, Any] = {
            "ordinal": ordinal,
            "creator_id": int(creator["id"]),
            "creator_email": str(creator["email"]),
            "video_path": str(video_path),
            "upload_status": 0,
            "audit_status": 0,
            "ok": False,
            "error": "",
        }
        try:
            form = aiohttp.FormData()
            form.add_field("title", title)
            form.add_field("notes", "300 real-video upload/audit smoke")
            form.add_field(
                "file",
                video_path.read_bytes(),
                filename=video_path.name,
                content_type="video/mp4",
            )
            async with session.post(
                f"{PUBLIC_BASE}/api/upload/video",
                headers={"Authorization": f"Bearer {creator['token']}"},
                data=form,
            ) as response:
                upload_payload, raw = await decode_json(response)
                result["upload_status"] = response.status
                result["upload_latency_ms"] = round((time.perf_counter() - upload_started) * 1000, 2)
                if response.status >= 400 or upload_payload.get("status") != "success":
                    result["error"] = f"upload:{response.status}:{upload_payload or raw[:240]}"
                    return result

            audit_started = time.perf_counter()
            uploaded_video = {
                "video_id": str(upload_payload.get("video_id") or ""),
                "asset_id": int(upload_payload.get("asset_id") or 0),
                "filename": str(upload_payload.get("filename") or video_path.name),
                "mime_type": str(upload_payload.get("mime_type") or "video/mp4"),
                "size_mb": float(upload_payload.get("size_mb") or 0),
                "r2_key": str(upload_payload.get("r2_key") or ""),
            }
            audit_body = {
                "title": title,
                "caption": "Real uploaded video smoke test for Viltrox 2.0 worker tier 300.",
                "raw_text": "Generated valid MP4 file; testing upload, asset registry, audit enqueue, and worker ledger.",
                "linked_handles": {"instagram": f"@real_video_smoke_{int(creator['id'])}"},
                "uploaded_video": uploaded_video,
            }
            async with session.post(
                f"{PUBLIC_BASE}/api/audit/v2",
                headers={
                    "Authorization": f"Bearer {creator['token']}",
                    "Content-Type": "application/json",
                },
                json=audit_body,
            ) as response:
                audit_payload, raw = await decode_json(response)
                result["audit_status"] = response.status
                result["audit_latency_ms"] = round((time.perf_counter() - audit_started) * 1000, 2)
                if response.status >= 400 or audit_payload.get("status") != "queued":
                    result["error"] = f"audit:{response.status}:{audit_payload or raw[:240]}"
                    return result
            result.update(
                {
                    "ok": True,
                    "asset_id": uploaded_video["asset_id"],
                    "video_id": uploaded_video["video_id"],
                    "task_id": str(audit_payload.get("job_id") or ""),
                    "submission_id": int(audit_payload.get("submission_id") or 0),
                }
            )
            return result
        except Exception as exc:
            result["error"] = f"{exc.__class__.__name__}:{exc}"
            return result


async def submit_all(creators: list[dict[str, Any]], videos: list[Path]) -> list[dict[str, Any]]:
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SEC)
    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0, ssl=False, force_close=True, enable_cleanup_closed=True)
    semaphore = asyncio.Semaphore(CHAIN_CONCURRENCY)
    results: list[dict[str, Any]] = []
    started = time.perf_counter()
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        tasks = [
            upload_then_audit(
                session,
                semaphore,
                ordinal=i + 1,
                creator=creators[i % len(creators)],
                video_path=videos[i],
            )
            for i in range(TOTAL)
        ]
        for i, task in enumerate(asyncio.as_completed(tasks), 1):
            results.append(await task)
            if i % 25 == 0 or i == TOTAL:
                ok = sum(1 for item in results if item.get("ok"))
                out(f"[real-video-smoke] submitted={i}/{TOTAL} queued_ok={ok} failed={i - ok}")
    elapsed = time.perf_counter() - started
    out(f"[real-video-smoke] submit_elapsed_sec={elapsed:.2f}")
    return results


def query_jobs(task_ids: list[str], submission_ids: list[int]) -> dict[str, Any]:
    if not task_ids:
        return {"job_status_counts": {}, "submission_counts": {}, "sample_failures": []}
    with db_connection_sync_scope():
        conn = get_conn()
        placeholders = ",".join("?" for _ in task_ids)
        job_rows = conn.execute(
            f"""
            SELECT status, COUNT(*) AS n
            FROM job_execution_ledger
            WHERE task_id IN ({placeholders})
            GROUP BY status
            """,
            tuple(task_ids),
        ).fetchall()
        failure_rows = conn.execute(
            f"""
            SELECT task_id, submission_id, status, error_message, detection_status, summary
            FROM job_execution_ledger
            WHERE task_id IN ({placeholders})
              AND status IN ('failed', 'prefilter_rejected')
            ORDER BY updated_at DESC
            LIMIT 10
            """,
            tuple(task_ids),
        ).fetchall()
        submission_counts: dict[str, int] = {}
        if submission_ids:
            sub_placeholders = ",".join("?" for _ in submission_ids)
            sub_rows = conn.execute(
                f"""
                SELECT detection_status, job_status, COUNT(*) AS n
                FROM submissions
                WHERE id IN ({sub_placeholders})
                GROUP BY detection_status, job_status
                """,
                tuple(submission_ids),
            ).fetchall()
            for row in sub_rows:
                key = f"{row['detection_status'] or ''}/{row['job_status'] or ''}"
                submission_counts[key] = int(row["n"] or 0)
    return {
        "job_status_counts": {str(row["status"] or ""): int(row["n"] or 0) for row in job_rows},
        "submission_counts": submission_counts,
        "sample_failures": [dict(row) for row in failure_rows],
    }


async def poll_jobs(results: list[dict[str, Any]]) -> dict[str, Any]:
    task_ids = [str(item.get("task_id") or "") for item in results if item.get("task_id")]
    submission_ids = [int(item.get("submission_id") or 0) for item in results if int(item.get("submission_id") or 0) > 0]
    started = time.perf_counter()
    last_snapshot: dict[str, Any] = {}
    while True:
        snapshot = await asyncio.to_thread(query_jobs, task_ids, submission_ids)
        counts = snapshot.get("job_status_counts") or {}
        terminal = sum(int(counts.get(status, 0) or 0) for status in TERMINAL_STATUSES)
        elapsed = time.perf_counter() - started
        out(f"[real-video-smoke] poll elapsed={elapsed:.0f}s terminal={terminal}/{len(task_ids)} jobs={counts}")
        last_snapshot = snapshot
        if terminal >= len(task_ids) or elapsed >= POLL_TIMEOUT_SEC:
            break
        await asyncio.sleep(POLL_INTERVAL_SEC)
    last_snapshot["poll_elapsed_sec"] = round(time.perf_counter() - started, 2)
    last_snapshot["poll_timeout_sec"] = POLL_TIMEOUT_SEC
    return last_snapshot


async def main() -> None:
    out(
        "[real-video-smoke] "
        f"base={PUBLIC_BASE} total={TOTAL} concurrency={CHAIN_CONCURRENCY} creator_pool={CREATOR_POOL_SIZE}"
    )
    creators = await asyncio.to_thread(ensure_creator_pool)
    videos = await asyncio.to_thread(generate_videos, TOTAL)
    results = await submit_all(creators, videos)
    queued = [item for item in results if item.get("ok")]
    poll_snapshot = await poll_jobs(results) if queued else {}

    upload_latencies = [float(item.get("upload_latency_ms") or 0) for item in results if item.get("upload_latency_ms")]
    audit_latencies = [float(item.get("audit_latency_ms") or 0) for item in results if item.get("audit_latency_ms")]
    failure_errors = [str(item.get("error") or "") for item in results if not item.get("ok")]
    summary = {
        "run_stamp": RUN_STAMP,
        "public_base": PUBLIC_BASE,
        "total_requested": TOTAL,
        "creator_pool_size": len(creators),
        "concurrency": CHAIN_CONCURRENCY,
        "video_dir": str(VIDEO_DIR),
        "queued_count": len(queued),
        "failed_submit_count": len(results) - len(queued),
        "upload_status_counts": dict(Counter(int(item.get("upload_status") or 0) for item in results)),
        "audit_status_counts": dict(Counter(int(item.get("audit_status") or 0) for item in results)),
        "upload_latency_ms": summarize_latencies(upload_latencies),
        "audit_latency_ms": summarize_latencies(audit_latencies),
        "sample_submit_failures": failure_errors[:10],
        "poll": poll_snapshot,
        "task_ids": [str(item.get("task_id") or "") for item in queued],
        "submission_ids": [int(item.get("submission_id") or 0) for item in queued],
    }
    report_path = LOG_DIR / f"real-video-smoke-{RUN_STAMP}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    out_json(summary, ensure_ascii=False, indent=2)
    out(f"\nreport_path={report_path}")

    await close_db_runtime()
    if len(queued) != TOTAL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
