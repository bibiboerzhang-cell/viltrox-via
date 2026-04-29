#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import urllib.error
import urllib.request

from stdout_utils import out_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test admin Runtime v2 read endpoints without printing tokens.")
    parser.add_argument("--email", default="zhangjianbo1012@icloud.com")
    parser.add_argument("--base", default="http://127.0.0.1:8102")
    return parser.parse_args()


def request_json(base: str, path: str, *, method: str = "GET", token: str = "", body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base.rstrip('/')}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
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

    results: dict[str, object] = {
        "login": {
            "status": login_payload.get("status"),
            "email": (login_payload.get("user") or {}).get("email"),
            "role": (login_payload.get("user") or {}).get("role"),
            "has_token": bool(token),
        }
    }
    endpoints = {
        "runtime_metrics": "/api/admin/runtime/metrics",
        "system_cache": "/api/admin/intel/system/cache",
        "system_rate_limit": "/api/admin/intel/system/rate-limit",
        "system_health": "/api/admin/intel/system/health",
        "runtime_workers": "/api/admin/runtime/workers",
        "runtime_queues": "/api/admin/runtime/queues",
        "route_performance": "/api/admin/runtime/route-performance?limit=5",
        "system_resources": "/api/admin/runtime/system-resources",
        "integrations": "/api/admin/integrations",
        "trust_users": "/api/admin/trust/users",
        "trust_rules": "/api/admin/trust/rules",
        "staff": "/api/admin/staff",
        "staff_roles": "/api/admin/staff/roles",
        "audit_log": "/api/admin/staff/audit-log?limit=5",
        "api_tokens": "/api/admin/staff/api-tokens",
    }
    for key, path in endpoints.items():
        status, payload = request_json(args.base, path, token=token)
        results[key] = {
            "http": status,
            "keys": sorted(payload.keys())[:8] if isinstance(payload, dict) else [],
        }

    ok = all(isinstance(item, dict) and item.get("http") == 200 for key, item in results.items() if key != "login")
    out_json(results, ensure_ascii=False, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
