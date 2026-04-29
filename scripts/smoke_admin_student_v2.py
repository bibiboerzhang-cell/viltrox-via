#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import time
import urllib.error
import urllib.request

from stdout_utils import out_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test admin Student QR v2 API wiring without printing tokens.")
    parser.add_argument("--email", default="zhangjianbo1012@icloud.com")
    parser.add_argument("--base", default="http://127.0.0.1:8102")
    return parser.parse_args()


def request_json(base: str, path: str, *, method: str = "GET", token: str = "", body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"content-type": "application/json"}
    if token:
      headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload or "{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(payload or "{}")
        except json.JSONDecodeError:
            parsed = {"detail": payload[:300]}
        return exc.code, parsed


def main() -> int:
    args = parse_args()
    password = getpass.getpass("Admin password: ")
    login_status, login_payload = request_json(
        args.base,
        "/api/auth/login",
        method="POST",
        body={"email": args.email.strip().lower(), "password": password},
    )
    token = str(login_payload.get("token") or "")
    if login_status != 200 or not token:
        out_json({"login": {"http": login_status, "status": login_payload.get("status")}}, ensure_ascii=False)
        return 1

    stamp = int(time.time())
    school_id = "SMOKE_STUDENT"
    batch_name = f"admin-student-v2-smoke-{stamp}"
    results: dict[str, object] = {
        "login": {
            "status": login_payload.get("status"),
            "email": (login_payload.get("user") or {}).get("email"),
            "role": (login_payload.get("user") or {}).get("role"),
            "has_token": bool(token),
        },
        "school_id": school_id,
        "batch_name": batch_name,
    }

    overview_status, overview_payload = request_json(args.base, "/api/admin/intel/student/overview?limit=48", token=token)
    results["overview"] = {"http": overview_status, "schools": len(overview_payload.get("schools") or [])}

    school_status, school_payload = request_json(
        args.base,
        "/api/admin/intel/student/schools",
        method="POST",
        token=token,
        body={
            "school_id": school_id,
            "school_code": "SMK",
            "school_name": "Smoke Student Test School",
            "region": "Local",
            "country": "US",
            "partnership_status": "pilot",
            "visual_theme": {"primary_color": "#111111", "accent_color": "#ff7a1a"},
        },
    )
    results["school_save"] = {"http": school_status, "school_id": school_payload.get("school_id")}

    batch_status, batch_payload = request_json(
        args.base,
        "/api/admin/intel/student/batches",
        method="POST",
        token=token,
        body={
            "school_id": school_id,
            "batch_name": batch_name,
            "count": 2,
            "roster_csv": "",
            "qr_only": True,
        },
    )
    items = batch_payload.get("items") or []
    results["batch_create"] = {
        "http": batch_status,
        "batch_name": batch_payload.get("batch_name"),
        "count": len(items),
        "has_manifest": bool(batch_payload.get("manifest_url")),
    }

    detail_status, detail_payload = request_json(
        args.base,
        f"/api/admin/intel/student/batches/detail?school_id={school_id}&batch_name={batch_name}&limit=20",
        token=token,
    )
    detail_items = detail_payload.get("items") or []
    results["batch_detail"] = {"http": detail_status, "count": len(detail_items)}

    first = detail_items[0] if detail_items else {}
    second = detail_items[1] if len(detail_items) > 1 else {}
    if first.get("qr_id"):
        qr_id = str(first["qr_id"])
        reissue_status, reissue_payload = request_json(
            args.base,
            f"/api/admin/intel/student/cards/{qr_id}/reissue",
            method="POST",
            token=token,
        )
        results["card_reissue"] = {
            "http": reissue_status,
            "qr_id": qr_id,
            "status": reissue_payload.get("status"),
            "has_claim": bool(reissue_payload.get("claim_url")),
        }
    if second.get("qr_id"):
        qr_id = str(second["qr_id"])
        revoke_status, revoke_payload = request_json(
            args.base,
            f"/api/admin/intel/student/cards/{qr_id}/revoke",
            method="POST",
            token=token,
            body={"reason": "student v2 smoke revoke unbound test card"},
        )
        results["card_revoke"] = {
            "http": revoke_status,
            "qr_id": qr_id,
            "status": revoke_payload.get("status"),
        }

    ok = (
        overview_status == 200
        and school_status == 200
        and batch_status == 200
        and len(items) >= 2
        and detail_status == 200
        and len(detail_items) >= 2
        and (results.get("card_reissue") or {}).get("http") == 200
        and (results.get("card_revoke") or {}).get("http") == 200
    )
    out_json(results, ensure_ascii=False, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
