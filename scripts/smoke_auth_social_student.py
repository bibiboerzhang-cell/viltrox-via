#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen

from stdout_utils import out, out_json


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_env import apply_runtime_env  # noqa: E402

apply_runtime_env()

BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.connection import close_db_runtime, init_db_runtime  # noqa: E402
from app.db.connection import get_conn  # noqa: E402
from app.services.student_identity import create_student_qr_batch, ensure_student_school_defaults  # noqa: E402


BASE_URL = os.getenv("VILTROX2_PUBLIC_URL", "http://127.0.0.1:8101").rstrip("/")
SESSION_CACHE = ROOT / "runtime" / "tmp" / "latest_student_session.json"


def http_json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "ignore")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"status": "error", "detail": raw or exc.reason}
        data["_http_status"] = exc.code
        return data


def assert_ok(name: str, payload: dict[str, Any], *, statuses: tuple[str, ...] = ("success",)) -> None:
    status = str(payload.get("status", ""))
    if statuses and status not in statuses:
        raise RuntimeError(f"{name} failed: {payload}")


def _login_cached_student(claim: dict[str, str]) -> dict[str, Any] | None:
    candidates: list[dict[str, str]] = []
    if SESSION_CACHE.exists():
        cached = json.loads(SESSION_CACHE.read_text(encoding="utf-8"))
        candidates.append({"email": cached.get("email", ""), "password": cached.get("password", "StudentPass1!")})

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT email
        FROM users
        WHERE email LIKE ?
        ORDER BY id DESC
        LIMIT 5
        """,
        ("student.smoke.%@example.com",),
    ).fetchall()
    for row in rows:
        candidates.append({"email": str(row["email"] or ""), "password": "StudentPass1!"})

    seen: set[str] = set()
    for candidate in candidates:
        email = str(candidate.get("email") or "").strip().lower()
        password = str(candidate.get("password") or "StudentPass1!")
        if not email or email in seen:
            continue
        seen.add(email)
        login = http_json(
            "POST",
            "/api/auth/login",
            payload={"email": email, "password": password},
        )
        if str(login.get("status") or "") == "success" and str(login.get("token") or ""):
            SESSION_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_CACHE.write_text(
                json.dumps({"email": email, "password": password}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return {
                "token": str(login.get("token") or ""),
                "claim": claim,
                "signup": login,
                "email": email,
                "password": password,
            }
    return None


def create_claim() -> dict[str, str]:
    ensure_student_school_defaults()
    batch = create_student_qr_batch(
        school_id="AFI_001",
        batch_name=f"smoke-{int(time.time())}",
        count=1,
        qr_only=True,
    )
    item = batch["items"][0]
    parsed = urlparse(item["claim_url"])
    query = parse_qs(parsed.query)
    return {
        "qr_id": item["qr_id"],
        "claim": query.get("claim", [""])[0],
        "sig": query.get("sig", [""])[0],
    }


def create_student_session(*, stamp: int | None = None) -> dict[str, Any]:
    session_stamp = int(stamp or time.time_ns())
    claim = create_claim()
    email = f"student.smoke.{session_stamp}@example.com"
    password = "StudentPass1!"
    student_signup = http_json(
        "POST",
        "/api/student/signup",
        payload={
            "qr_id": claim["qr_id"],
            "claim_token": claim["claim"],
            "signature": claim["sig"],
            "email": email,
            "password": password,
            "name": "Smoke Student",
            "major": "Cinematography",
            "year": "2026",
        },
    )
    if int(student_signup.get("_http_status") or 0) == 429:
        fallback = _login_cached_student(claim)
        if fallback:
            return fallback
    assert_ok("student signup", student_signup)
    token = str(student_signup.get("token") or "")
    if not token:
        raise RuntimeError(f"student signup missing token: {student_signup}")
    session = {
        "token": token,
        "claim": claim,
        "signup": student_signup,
        "email": email,
        "password": password,
    }
    SESSION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_CACHE.write_text(
        json.dumps({"email": email, "password": password}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return session


def main() -> int:
    asyncio.run(init_db_runtime())
    try:
        stamp = time.time_ns()
        creator_email = f"auth.smoke.{stamp}@example.com"
        creator_password = "Sm0kePass!"
        student_email = f"student.smoke.{stamp}@example.com"
        student_password = "StudentPass1!"

        out("1) register pending creator account")
        register_payload = http_json(
            "POST",
            "/api/auth/register",
            payload={
                "name": "Smoke Creator",
                "email": creator_email,
                "password": creator_password,
            },
        )
        assert_ok("register", register_payload)

        out("2) verify pending login response stays contract-compatible")
        pending_login = http_json(
            "POST",
            "/api/auth/login",
            payload={"email": creator_email, "password": creator_password},
        )
        if pending_login.get("status") != "pending":
            raise RuntimeError(f"expected pending login, got {pending_login}")

        out("3) create student QR claim and fetch claim metadata")
        claim = create_claim()
        claim_meta = http_json(
            "GET",
            f"/api/student/claim/{claim['qr_id']}?{urlencode({'claim': claim['claim'], 'sig': claim['sig']})}",
        )
        assert_ok("student claim metadata", claim_meta)

        out("4) sign up student creator and capture live session token")
        student_signup = http_json(
            "POST",
            "/api/student/signup",
            payload={
                "qr_id": claim["qr_id"],
                "claim_token": claim["claim"],
                "signature": claim["sig"],
                "email": student_email,
                "password": student_password,
                "name": "Smoke Student",
                "major": "Cinematography",
                "year": "2026",
            },
        )
        assert_ok("student signup", student_signup)
        token = str(student_signup.get("token") or "")
        if not token:
            raise RuntimeError(f"student signup missing token: {student_signup}")

        out("5) fetch account-adjacent creator data")
        me_payload = http_json("GET", "/api/auth/me", token=token)
        assert_ok("auth me", me_payload)
        program_payload = http_json("GET", "/api/creator/program", token=token)
        assert_ok("creator program", program_payload)
        memory_payload = http_json("GET", "/api/creator/memory", token=token)
        assert_ok("creator memory", memory_payload)
        student_pass_payload = http_json("GET", "/api/student/pass", token=token)
        assert_ok("student pass", student_pass_payload)

        out("6) add address and verify default address flow")
        add_address = http_json(
            "POST",
            "/api/creator/addresses",
            token=token,
            payload={
                "name": "Smoke Student",
                "phone": "555-0100",
                "address1": "101 Cinema Row",
                "address2": "Suite 5",
                "city": "Los Angeles",
                "state": "CA",
                "country": "US",
                "postal_code": "90028",
            },
        )
        assert_ok("add address", add_address)
        addresses = http_json("GET", "/api/creator/addresses", token=token)
        if not addresses.get("addresses"):
            raise RuntimeError(f"expected addresses after create, got {addresses}")
        address_id = int(addresses["addresses"][0]["id"])
        set_default = http_json("PATCH", f"/api/creator/addresses/{address_id}/default", token=token)
        assert_ok("set default address", set_default)

        out("7) add social account and run verification workflow")
        add_social = http_json(
            "POST",
            "/api/creator/social-accounts",
            token=token,
            payload={"platform": "instagram", "handle": f"smoke_student_{stamp}"},
        )
        assert_ok("add social", add_social)
        preview = http_json(
            "POST",
            "/api/verify/preview",
            token=token,
            payload={"profile_url": add_social["profile_url"]},
        )
        if preview.get("platform") != "instagram":
            raise RuntimeError(f"unexpected preview payload: {preview}")
        verify_start = http_json(
            "POST",
            "/api/verify/start",
            token=token,
            payload={"profile_url": add_social["profile_url"]},
        )
        if int(verify_start.get("verification_id") or 0) <= 0:
            raise RuntimeError(f"verification start failed: {verify_start}")
        posted = http_json(
            "POST",
            "/api/verify/posted",
            token=token,
            payload={"verification_id": verify_start["verification_id"]},
        )
        if str(posted.get("status") or "") != "awaiting_scan":
            raise RuntimeError(f"verification posted failed: {posted}")
        verification_list = http_json("GET", "/api/verify/my", token=token)
        if int(verification_list.get("total") or 0) < 1:
            raise RuntimeError(f"expected verification rows, got {verification_list}")

        out("8) fetch social accounts snapshot")
        accounts = http_json("GET", "/api/creator/social-accounts", token=token)
        if not accounts.get("accounts"):
            raise RuntimeError(f"expected social accounts, got {accounts}")

        summary = {
            "creator_register_status": register_payload["status"],
            "creator_login_status": pending_login["status"],
            "student_signup_status": student_signup["status"],
            "student_id_code": student_signup.get("student", {}).get("student_id_code"),
            "creator_code": student_signup.get("user", {}).get("creator_code"),
            "verification_status": posted.get("status"),
            "verification_job_id": posted.get("job_id"),
            "social_accounts": len(accounts["accounts"]),
            "addresses": len(addresses["addresses"]),
        }
        out_json(summary, ensure_ascii=False, indent=2)
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
