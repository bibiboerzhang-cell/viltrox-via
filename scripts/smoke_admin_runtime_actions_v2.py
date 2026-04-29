#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import urllib.error
import urllib.request

from stdout_utils import out_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test safe admin Runtime v2 write actions.")
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


def flatten_integrations(payload: dict) -> list[dict]:
    out: list[dict] = []
    by_category = payload.get("integrations_by_category") or {}
    if isinstance(by_category, dict):
        for items in by_category.values():
            if isinstance(items, list):
                out.extend(item for item in items if isinstance(item, dict))
    return out


def first_rule(payload: dict) -> dict | None:
    for key in ("positive", "negative"):
        rows = payload.get(key)
        if isinstance(rows, list) and rows:
            row = rows[0]
            return row if isinstance(row, dict) else None
    return None


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
        },
        "token_mutations": "skipped: persistent API token creation/revoke is not run by smoke",
    }

    integrations_status, integrations_payload = request_json(args.base, "/api/admin/integrations", token=token)
    integrations = flatten_integrations(integrations_payload)
    target = next((row for row in integrations if str(row.get("service_name") or row.get("name")) == "redis"), None) or (integrations[0] if integrations else None)
    results["integrations_list"] = {"http": integrations_status, "count": len(integrations)}
    if target and target.get("id"):
        integration_id = int(target["id"])
        test_status, test_payload = request_json(
            args.base,
            f"/api/admin/integrations/{integration_id}/test",
            method="POST",
            token=token,
        )
        health_status, health_payload = request_json(
            args.base,
            f"/api/admin/integrations/{integration_id}/health",
            token=token,
        )
        results["integration_action"] = {
            "id": integration_id,
            "service": target.get("service_name") or target.get("name"),
            "test_http": test_status,
            "test_status": test_payload.get("status"),
            "health_http": health_status,
            "health_status": health_payload.get("status"),
        }
    else:
        results["integration_action"] = {"skipped": "no integration rows"}

    rules_status, rules_payload = request_json(args.base, "/api/admin/trust/rules", token=token)
    rule = first_rule(rules_payload)
    results["trust_rules"] = {"http": rules_status, "has_rule": bool(rule)}
    if rule and rule.get("id"):
        rule_id = int(rule["id"])
        update_status, update_payload = request_json(
            args.base,
            f"/api/admin/trust/rules/{rule_id}",
            method="PUT",
            token=token,
            body={
                "delta": int(rule.get("delta") or 0),
                "description": str(rule.get("description") or ""),
                "enabled": bool(rule.get("enabled", 1)),
            },
        )
        results["trust_rule_update"] = {"id": rule_id, "http": update_status, "ok": update_payload.get("ok")}
    else:
        results["trust_rule_update"] = {"skipped": "no editable rule"}

    staff_status, staff_payload = request_json(args.base, "/api/admin/staff", token=token)
    roles_status, roles_payload = request_json(args.base, "/api/admin/staff/roles", token=token)
    tokens_status, tokens_payload = request_json(args.base, "/api/admin/staff/api-tokens", token=token)
    results["staff_read"] = {
        "staff_http": staff_status,
        "staff_count": len(staff_payload.get("members") or []),
        "roles_http": roles_status,
        "roles_count": len(roles_payload.get("roles") or []),
        "tokens_http": tokens_status,
        "tokens_count": len(tokens_payload.get("tokens") or []),
    }

    ok = (
        integrations_status == 200
        and rules_status == 200
        and staff_status == 200
        and roles_status == 200
        and tokens_status == 200
        and (not target or results.get("integration_action", {}).get("test_http") == 200)
        and (not rule or results.get("trust_rule_update", {}).get("http") == 200)
    )
    out_json(results, ensure_ascii=False, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
