#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    import aiohttp
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "aiohttp is required for load_test_mixed_write.py. Install dependencies from requirements.txt first."
    ) from exc

from runtime_env import apply_runtime_env
from stdout_utils import out, out_json

apply_runtime_env()

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from smoke_auth_social_student import create_student_session  # noqa: E402
from app.db.connection import get_conn  # noqa: E402
from app.core.security import hash_password, make_token  # noqa: E402
from app.db.repositories.users import creator_code_exists, generate_creator_code  # noqa: E402

LOG_DIR = ROOT / "runtime" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

PUBLIC_BASE = os.environ.get("LOAD_TEST_PUBLIC_BASE", "http://127.0.0.1:8101").rstrip("/")
ADMIN_BASE = os.environ.get("LOAD_TEST_ADMIN_BASE", "http://127.0.0.1:8102").rstrip("/")
TIMEOUT_SEC = float(os.environ.get("LOAD_TEST_TIMEOUT_SEC", "30"))
PHASES = [
    max(1, int(part.strip()))
    for part in os.environ.get("LOAD_TEST_MIXED_PHASES", "10,25,50,100,150,200").split(",")
    if part.strip()
]
REQUESTS_PER_PHASE = int(os.environ.get("LOAD_TEST_MIXED_REQUESTS_PER_PHASE", "120"))
PHASE_PAUSE_SEC = float(os.environ.get("LOAD_TEST_MIXED_PHASE_PAUSE_SEC", "1.5"))
SOAK_ENABLED = os.environ.get("LOAD_TEST_MIXED_SOAK_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
SOAK_CONCURRENCY = int(os.environ.get("LOAD_TEST_MIXED_SOAK_CONCURRENCY", "100"))
SOAK_DURATION_SEC = int(os.environ.get("LOAD_TEST_MIXED_SOAK_DURATION_SEC", "600"))
SOAK_REQUESTS_PER_BATCH = int(os.environ.get("LOAD_TEST_MIXED_SOAK_REQUESTS_PER_BATCH", "100"))
ADMIN_EMAIL = os.environ.get("LOAD_TEST_ADMIN_EMAIL", "admin@viltrox.com").strip()
ADMIN_PASSWORD = os.environ.get("LOAD_TEST_ADMIN_PASSWORD", os.environ.get("ADMIN_PASSWORD", "AdminPass123!")).strip()
CREATOR_EMAIL = os.environ.get("LOAD_TEST_CREATOR_EMAIL", "").strip()
CREATOR_PASSWORD = os.environ.get("LOAD_TEST_CREATOR_PASSWORD", "").strip()
CREATOR_POOL_SIZE = int(os.environ.get("LOAD_TEST_CREATOR_POOL_SIZE", "0") or 0)
SESSION_CACHE = ROOT / "runtime" / "tmp" / "latest_student_session.json"
VIA_SESSION_KEYS = [
    value.strip()
    for value in os.environ.get("LOAD_TEST_VIA_SESSION_KEYS", "").split(",")
    if value.strip()
]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[idx]


def _require_env(value: str, name: str) -> str:
    if value:
        return value
    raise RuntimeError(f"{name} is required for mixed write load testing")


def _fake_video_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42" + os.urandom(128 * 1024)


async def decode_payload(response: aiohttp.ClientResponse) -> tuple[dict[str, object], str]:
    raw = await response.text()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    return payload, raw


def login_sync(base: str, email: str, password: str) -> str:
    req = Request(
        f"{base}/api/auth/login",
        data=json.dumps({"email": email, "password": password}).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=TIMEOUT_SEC) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"login failed for {email}: {exc.code} {raw[:240]}") from exc
    if payload.get("status") != "success" or not payload.get("token"):
        raise RuntimeError(f"login failed for {email}: {payload}")
    return str(payload["token"])


def token_for_email_sync(email: str) -> str:
    normalized = email.strip().lower()
    if not normalized:
        raise RuntimeError("email is required for token minting")
    conn = get_conn()
    row = conn.execute(
        "SELECT id, role FROM users WHERE lower(email)=?",
        (normalized,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"user not found for token minting: {normalized}")
    return make_token(int(row["id"]), str(row["role"] or "creator"))


async def resolve_creator_session(session: aiohttp.ClientSession) -> tuple[str, str]:
    if CREATOR_EMAIL and CREATOR_PASSWORD:
        try:
            ensure_load_creator_account(CREATOR_EMAIL, CREATOR_PASSWORD, _creator_label(CREATOR_EMAIL))
            token = await asyncio.to_thread(token_for_email_sync, CREATOR_EMAIL)
            return token, CREATOR_EMAIL
        except Exception as exc:
            out(f"[load-test-mixed] creator login fallback engaged: {exc}")

    if SESSION_CACHE.exists():
        try:
            cached = json.loads(SESSION_CACHE.read_text(encoding="utf-8"))
            cached_token = str(cached.get("token") or "").strip()
            cached_email = str(cached.get("email") or "").strip()
            cached_password = str(cached.get("password") or "").strip()
            if cached_token and cached_email:
                return cached_token, cached_email
            if cached_email and cached_password:
                token = await asyncio.to_thread(token_for_email_sync, cached_email)
                return token, cached_email
        except Exception as exc:
            out(f"[load-test-mixed] cached student session unusable: {exc}")

    session_bundle = await asyncio.to_thread(create_student_session)
    token = str(session_bundle.get("token") or "").strip()
    email = str(session_bundle.get("email") or "student-smoke").strip()
    if not token:
        raise RuntimeError("fallback creator session did not return a token")
    return token, email


def _creator_label(email: str) -> str:
    base = (email or "creator").split("@", 1)[0].strip().lower()
    safe = "".join(ch if ch.isalnum() else "_" for ch in base)
    return safe or "creator"


def _default_creator_pool_size(total: int) -> int:
    via_budget = max(1, int(total * 0.35))
    upload_budget = max(1, int(total * 0.35))
    upload_creators = max(1, math.ceil(upload_budget / 2))
    via_creators = max(1, math.ceil(via_budget / 30))
    return max(upload_creators, via_creators)


async def resolve_creator_pool(session: aiohttp.ClientSession, required_size: int) -> list[dict[str, str]]:
    creators: list[dict[str, str]] = []
    seen_emails: set[str] = set()

    async def _append(token: str, email: str, password: str = "") -> bool:
        normalized = email.strip().lower()
        if not token or not normalized or normalized in seen_emails:
            return False
        seen_emails.add(normalized)
        creators.append(
            {
                "token": token,
                "email": normalized,
                "password": password,
                "label": _creator_label(normalized),
            }
        )
        return True

    if CREATOR_EMAIL and CREATOR_PASSWORD:
        try:
            ensure_load_creator_account(CREATOR_EMAIL, CREATOR_PASSWORD, _creator_label(CREATOR_EMAIL))
            token = await asyncio.to_thread(token_for_email_sync, CREATOR_EMAIL)
            await _append(token, CREATOR_EMAIL, CREATOR_PASSWORD)
        except Exception as exc:
            out(f"[load-test-mixed] creator login fallback engaged: {exc}")

    if not creators and SESSION_CACHE.exists():
        try:
            cached = json.loads(SESSION_CACHE.read_text(encoding="utf-8"))
            cached_token = str(cached.get("token") or "").strip()
            cached_email = str(cached.get("email") or "").strip()
            cached_password = str(cached.get("password") or "").strip()
            if cached_token and cached_email:
                await _append(cached_token, cached_email, cached_password)
            if cached_email and cached_password:
                token = await asyncio.to_thread(token_for_email_sync, cached_email)
                await _append(token, cached_email, cached_password)
        except Exception as exc:
            out(f"[load-test-mixed] cached student session unusable: {exc}")

    attempts = 0
    while len(creators) < required_size and attempts < max(required_size * 3, 10):
        attempts += 1
        email = f"load.creator.{int(time.time())}.{attempts}@example.com"
        password = "LoadPass1!"
        await asyncio.to_thread(ensure_load_creator_account, email, password, f"Load Creator {attempts}")
        try:
            token = await asyncio.to_thread(token_for_email_sync, email)
        except Exception as exc:
            out(f"[load-test-mixed] direct load creator bootstrap failed for {email}: {exc}")
            continue
        if await _append(token, email, password):
            continue
        await asyncio.sleep(0.05)

    if not creators:
        try:
            token, email = await resolve_creator_session(session)
            await _append(token, email)
        except Exception:
            session_bundle = await asyncio.to_thread(create_student_session)
            await _append(
                str(session_bundle.get("token") or "").strip(),
                str(session_bundle.get("email") or "").strip(),
                str(session_bundle.get("password") or "").strip(),
            )

    return creators


def promote_creator_for_load(email: str) -> None:
    normalized = email.strip().lower()
    if not normalized:
        return
    conn = get_conn()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    aged = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 45 * 86400))
    conn.execute(
        """
        UPDATE users
        SET created_at=?,
            status='approved',
            role='creator',
            email_verified=1,
            social_verified=1,
            trust_score=65,
            trust_updated_at=?
        WHERE lower(email)=?
        """,
        (aged, now, normalized),
    )
    conn.commit()


def ensure_load_creator_account(email: str, password: str, name: str) -> None:
    normalized = email.strip().lower()
    if not normalized:
        return
    conn = get_conn()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    aged = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 45 * 86400))
    row = conn.execute("SELECT id FROM users WHERE lower(email)=?", (normalized,)).fetchone()
    if row:
        conn.execute(
            """
            UPDATE users
            SET created_at=?,
                password_hash=?,
                name=?,
                status='approved',
                role='creator',
                email_verified=1,
                social_verified=1,
                trust_score=65,
                trust_updated_at=?
            WHERE id=?
            """,
            (aged, hash_password(password), name.strip(), now, int(row["id"])),
        )
        conn.commit()
        return

    creator_code = ""
    for attempt in range(10):
        candidate = generate_creator_code(conn, offset=attempt)
        if not creator_code_exists(conn, candidate):
            creator_code = candidate
            break
    if not creator_code:
        raise RuntimeError("Could not allocate creator_code for load creator")

    conn.execute(
        """
        INSERT INTO users (
            created_at, email, password_hash, name, creator_code,
            status, role, email_verified, social_verified, trust_score, trust_updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            aged,
            normalized,
            hash_password(password),
            name.strip(),
            creator_code,
            "approved",
            "creator",
            1,
            1,
            65,
            now,
        ),
    )
    conn.commit()


async def fire_one(
    session: aiohttp.ClientSession,
    item: dict[str, object],
    semaphore: asyncio.Semaphore,
    bucket: list[dict[str, object]],
) -> None:
    async with semaphore:
        started = time.perf_counter()
        status_code = 0
        error = ""
        size = 0
        try:
            async with session.request(
                str(item["method"]),
                str(item["url"]),
                headers={**dict(item.get("headers") or {}), "Connection": "close"},
                json=item.get("json"),
                data=item.get("data"),
            ) as response:
                payload = await response.read()
                status_code = response.status
                size = len(payload)
        except Exception as exc:  # pragma: no cover
            error = exc.__class__.__name__
        latency_ms = (time.perf_counter() - started) * 1000
        bucket.append(
            {
                "name": str(item["name"]),
                "status": status_code,
                "latency_ms": latency_ms,
                "ok": not error and 200 <= status_code < 400,
                "error": error,
                "bytes": size,
            }
        )


async def build_upload_audit_job(
    session: aiohttp.ClientSession,
    creator: dict[str, str],
    ordinal: int,
) -> dict[str, object]:
    video_name = f"stress-{uuid.uuid4().hex}.mp4"
    payload_bytes = _fake_video_bytes()
    form = aiohttp.FormData()
    form.add_field("title", f"Stress upload {ordinal}")
    form.add_field("notes", "mixed write load test")
    form.add_field("file", payload_bytes, filename=video_name, content_type="video/mp4")
    async with session.post(
        f"{PUBLIC_BASE}/api/upload/video",
        headers={"Authorization": f"Bearer {creator['token']}"},
        data=form,
    ) as response:
        upload_payload, raw = await decode_payload(response)
        if response.status >= 400 or upload_payload.get("status") not in {"success", "rejected"}:
            raise RuntimeError(f"upload failed: {response.status} {upload_payload or raw[:240]}")
        if upload_payload.get("status") != "success":
            raise RuntimeError(f"upload rejected: {upload_payload}")

    creator_handle = f"@{creator['label']}_{ordinal}"
    return {
        "name": "creator_upload_audit",
        "method": "POST",
        "url": f"{PUBLIC_BASE}/api/audit/v2",
        "headers": {
            "Authorization": f"Bearer {creator['token']}",
            "Content-Type": "application/json",
        },
        "json": {
            "title": f"Stress upload {ordinal}",
            "caption": f"Mixed load audit request {ordinal}",
            "raw_text": f"Mixed load audit raw text {ordinal}",
            "linked_handles": {"instagram": creator_handle},
            "uploaded_video": {
                "video_id": str(upload_payload.get("video_id") or ""),
                "asset_id": int(upload_payload.get("asset_id") or 0),
                "filename": str(upload_payload.get("filename") or video_name),
                "mime_type": str(upload_payload.get("mime_type") or "video/mp4"),
                "size_mb": float(upload_payload.get("size_mb") or 0),
                "r2_key": str(upload_payload.get("r2_key") or ""),
            },
        },
    }


async def create_via_session(
    session: aiohttp.ClientSession,
    creator_token: str,
    ordinal: int,
    *,
    retries: int = 3,
) -> str:
    payload = {
        "surface": "upload",
        "signed_device_id": f"load-device-{ordinal}",
        "client_fingerprint": f"load-script-{ordinal}",
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {creator_token}",
    }
    last_error = "unknown"
    for attempt in range(1, retries + 1):
        try:
            async with session.post(f"{PUBLIC_BASE}/api/via/sessions", headers=headers, json=payload) as response:
                decoded, raw = await decode_payload(response)
            if response.status >= 400:
                last_error = f"{response.status} {(raw or '')[:240]}"
            else:
                session_key = str((decoded.get("session") or {}).get("session_key") or "")
                if session_key:
                    return session_key
                last_error = f"missing_session_key:{decoded}"
        except Exception as exc:  # pragma: no cover
            last_error = exc.__class__.__name__
        await asyncio.sleep(min(1.0, 0.2 * attempt))
    raise RuntimeError(f"via session bootstrap failed after retries: {last_error}")


def build_via_turn_job(creator_token: str, session_key: str, ordinal: int) -> dict[str, object]:
    return {
        "name": "via_turn",
        "method": "POST",
        "url": f"{PUBLIC_BASE}/api/via/sessions/{session_key}/respond",
        "headers": {
            "Authorization": f"Bearer {creator_token}",
            "Content-Type": "application/json",
        },
        "json": {
            "surface": "upload",
            "text": "Recommend one Viltrox portrait lens for Sony full frame and keep it concise.",
        },
    }


def summarize_results(results: list[dict[str, object]], *, elapsed: float, concurrency: int) -> dict[str, object]:
    latencies = [float(item["latency_ms"]) for item in results]
    successes = [item for item in results if bool(item["ok"])]
    failures = [item for item in results if not bool(item["ok"])]
    by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in results:
        by_name[str(item["name"])].append(item)
    return {
        "concurrency": concurrency,
        "total_requests": len(results),
        "success_count": len(successes),
        "failure_count": len(failures),
        "success_rate": round((len(successes) / len(results)) if results else 0.0, 4),
        "elapsed_sec": round(elapsed, 3),
        "requests_per_sec": round((len(results) / elapsed) if elapsed > 0 else 0.0, 2),
        "latency_ms": {
            "avg": round(mean(latencies), 2) if latencies else 0.0,
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
            "p99": round(percentile(latencies, 99), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "status_codes": dict(Counter(int(item["status"]) for item in results)),
        "error_types": dict(Counter(str(item["error"]) for item in failures if item["error"])),
        "scenarios": {
            name: {
                "count": len(entries),
                "success_rate": round(sum(1 for item in entries if bool(item["ok"])) / len(entries), 4) if entries else 0.0,
                "avg_ms": round(mean(float(item["latency_ms"]) for item in entries), 2) if entries else 0.0,
                "p95_ms": round(percentile([float(item["latency_ms"]) for item in entries], 95), 2) if entries else 0.0,
            }
            for name, entries in sorted(by_name.items())
        },
    }


async def run_phase(
    session: aiohttp.ClientSession,
    creators: list[dict[str, str]],
    admin_token: str,
    via_session_keys_by_creator: dict[str, list[str]],
    *,
    concurrency: int,
    total: int,
    name: str = "stage",
) -> dict[str, object]:
    workload, bootstrap_failures = await build_phase_workload(
        session, creators, admin_token, via_session_keys_by_creator, total
    )
    started = time.perf_counter()
    results: list[dict[str, object]] = list(bootstrap_failures)
    semaphore = asyncio.Semaphore(concurrency)
    await asyncio.gather(*(fire_one(session, item, semaphore, results) for item in workload))
    elapsed = time.perf_counter() - started
    summary = summarize_results(results, elapsed=elapsed, concurrency=concurrency)
    summary["phase_name"] = name
    summary["planned_requests"] = total
    return summary


async def run_soak(
    session: aiohttp.ClientSession,
    creators: list[dict[str, str]],
    admin_token: str,
    via_session_keys_by_creator: dict[str, list[str]],
) -> dict[str, object]:
    if SOAK_DURATION_SEC <= 0 or SOAK_REQUESTS_PER_BATCH <= 0:
        return {
            "phase_name": "soak",
            "enabled": False,
            "reason": "invalid_soak_configuration",
        }
    started = time.perf_counter()
    results: list[dict[str, object]] = []
    batches = 0
    while (time.perf_counter() - started) < SOAK_DURATION_SEC:
        batches += 1
        workload, bootstrap_failures = await build_phase_workload(
            session, creators, admin_token, via_session_keys_by_creator, SOAK_REQUESTS_PER_BATCH
        )
        results.extend(bootstrap_failures)
        semaphore = asyncio.Semaphore(SOAK_CONCURRENCY)
        await asyncio.gather(*(fire_one(session, item, semaphore, results) for item in workload))
    elapsed = time.perf_counter() - started
    summary = summarize_results(results, elapsed=elapsed, concurrency=SOAK_CONCURRENCY)
    summary["phase_name"] = "soak"
    summary["enabled"] = True
    summary["duration_target_sec"] = SOAK_DURATION_SEC
    summary["batches"] = batches
    summary["batch_size"] = SOAK_REQUESTS_PER_BATCH
    return summary


async def build_phase_workload(
    session: aiohttp.ClientSession,
    creators: list[dict[str, str]],
    admin_token: str,
    via_session_keys_by_creator: dict[str, list[str]],
    total: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    workload: list[dict[str, object]] = []
    bootstrap_failures: list[dict[str, object]] = []
    via_budget = max(1, int(total * 0.35))
    upload_budget = max(1, int(total * 0.35))
    creator_reads = max(1, int(total * 0.15))
    admin_reads = max(1, total - via_budget - upload_budget - creator_reads)

    if not creators:
        raise RuntimeError("creator pool is empty")
    via_ready_creators = [creator for creator in creators if via_session_keys_by_creator.get(creator["email"])]
    if not via_ready_creators:
        raise RuntimeError("no via-ready creators are available for mixed write load testing")

    for ordinal in range(via_budget):
        creator = via_ready_creators[ordinal % len(via_ready_creators)]
        keys = via_session_keys_by_creator[creator["email"]]
        workload.append(build_via_turn_job(creator["token"], keys[ordinal % len(keys)], ordinal))
    for ordinal in range(upload_budget):
        creator = creators[ordinal % len(creators)]
        try:
            workload.append(await build_upload_audit_job(session, creator, ordinal))
        except Exception as exc:
            bootstrap_failures.append(
                {
                    "name": "creator_upload_audit",
                    "status": 0,
                    "latency_ms": 0.0,
                    "ok": False,
                    "error": f"bootstrap:{exc.__class__.__name__}",
                    "bytes": 0,
                }
            )
    workload.extend(
        {
            "name": "creator_snapshot",
            "method": "GET",
            "url": f"{PUBLIC_BASE}/api/creator/submissions?limit=20",
            "headers": {"Authorization": f"Bearer {creators[index % len(creators)]['token']}"},
        }
        for index in range(creator_reads)
    )
    workload.extend(
        {
            "name": "admin_review",
            "method": "GET",
            "url": f"{ADMIN_BASE}/api/admin/submissions?limit=20",
            "headers": {"Authorization": f"Bearer {admin_token}"},
        }
        for _ in range(admin_reads)
    )
    random.shuffle(workload)
    return workload[:total], bootstrap_failures


async def main() -> None:
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SEC)
    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0, ssl=False, force_close=True, enable_cleanup_closed=True)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        max_total = max(max(phase, REQUESTS_PER_PHASE) for phase in PHASES)
        required_pool = CREATOR_POOL_SIZE or _default_creator_pool_size(max_total)
        creators = await resolve_creator_pool(session, required_pool)
        for creator in creators:
            await asyncio.to_thread(promote_creator_for_load, creator["email"])
        admin_token = await asyncio.to_thread(token_for_email_sync, ADMIN_EMAIL)
        if VIA_SESSION_KEYS:
            via_session_keys_by_creator = {
                creator["email"]: VIA_SESSION_KEYS[:]
                for creator in creators
            }
        else:
            via_session_keys_by_creator: dict[str, list[str]] = {}
            for index, creator in enumerate(creators):
                try:
                    via_session_keys_by_creator[creator["email"]] = [
                        await create_via_session(session, creator["token"], index)
                    ]
                except Exception as exc:
                    out(
                        f"[load-test-mixed] via session bootstrap skipped for {creator['email']}: {exc}"
                    )
            if not via_session_keys_by_creator:
                raise RuntimeError("unable to bootstrap any via sessions for mixed write load testing")
        phase_results: list[dict[str, object]] = []
        for phase_concurrency in PHASES:
            phase_total = max(phase_concurrency, REQUESTS_PER_PHASE)
            phase_results.append(
                await run_phase(
                    session,
                    creators,
                    admin_token,
                    via_session_keys_by_creator,
                    concurrency=phase_concurrency,
                    total=phase_total,
                    name=f"stage_{phase_concurrency}",
                )
            )
            if phase_concurrency != PHASES[-1]:
                await asyncio.sleep(PHASE_PAUSE_SEC)
        soak_summary = await run_soak(session, creators, admin_token, via_session_keys_by_creator) if SOAK_ENABLED else {
            "phase_name": "soak",
            "enabled": False,
            "reason": "disabled",
        }

    summary = {
        "public_base": PUBLIC_BASE,
        "admin_base": ADMIN_BASE,
        "creator_pool_size": len(creators),
        "creator_identities": [creator["email"] for creator in creators],
        "phases": phase_results,
        "soak": soak_summary,
    }
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report_path = LOG_DIR / f"load-test-mixed-{stamp}.json"
    report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    out_json(summary, indent=2)
    out(f"\nreport_path={report_path}")


if __name__ == "__main__":
    asyncio.run(main())
