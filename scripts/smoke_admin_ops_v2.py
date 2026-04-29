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
    parser = argparse.ArgumentParser(description="Smoke-test admin Operations v2 API wiring without printing tokens.")
    parser.add_argument("--email", default="zhangjianbo1012@icloud.com")
    parser.add_argument("--base", default="http://127.0.0.1:8102")
    return parser.parse_args()


def request_json(base: str, path: str, *, method: str = "GET", token: str = "", body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
            return response.status, json.loads(payload or "{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(payload or "{}")
        except json.JSONDecodeError:
            parsed = {"detail": payload[:300]}
        return exc.code, parsed


def ok(payload: dict) -> bool:
    status = str(payload.get("status", "")).lower()
    return status in {"success", "created", "updated", "deleted"} or bool(payload.get("id"))


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
        out_json(
            {"login": {"http": login_status, "status": login_payload.get("status"), "message": login_payload.get("message")}},
            ensure_ascii=False,
        )
        return 1

    stamp = int(time.time())
    fake_url = f"https://example.invalid/viltrox-admin-ops-smoke/{stamp}?fake_link=1"
    results: dict[str, object] = {
        "login": {
            "status": login_payload.get("status"),
            "email": (login_payload.get("user") or {}).get("email"),
            "role": (login_payload.get("user") or {}).get("role"),
            "has_token": bool(token),
        },
        "fake_link": fake_url,
    }

    users_status, users_payload = request_json(args.base, "/api/admin/users?limit=20", token=token)
    users = users_payload.get("users") or []
    results["users_list"] = {"http": users_status, "count": len(users)}

    manual_payload = {
        "platform": "TikTok",
        "extracted_handle": f"ops_smoke_{stamp}",
        "url": fake_url,
        "title": f"Admin ops smoke fake link {stamp}",
        "detection_status": "suspected",
        "product_series": "SMOKE_PRE",
        "product_label": "Smoke pre-label",
        "final_score": 77,
        "creator_score": 66,
        "overall_score": 72,
        "views": 1234,
        "likes": 98,
        "comments": 7,
        "shares": 3,
        "recommendation": "Admin smoke test",
        "memo": "ops v2 fake link smoke",
    }
    manual_status, manual_result = request_json(
        args.base,
        "/api/admin/submissions/manual",
        method="POST",
        token=token,
        body=manual_payload,
    )
    submission_id = int(manual_result.get("id") or 0)
    results["manual_submission"] = {"http": manual_status, "status": manual_result.get("status"), "id": submission_id}

    if submission_id:
        correct_status, correct_result = request_json(
            args.base,
            f"/api/admin/submissions/{submission_id}/correct",
            method="POST",
            token=token,
            body={
                "correct_series": "SMOKE_CORRECTED",
                "correct_label": "Smoke Corrected Lens",
                "note": "ops v2 correction smoke",
            },
        )
        results["product_correction"] = {
            "http": correct_status,
            "status": correct_result.get("status"),
            "submission_id": correct_result.get("submission_id"),
            "correct_series": correct_result.get("correct_series"),
        }

    target_user = next((u for u in users if str(u.get("role", "")).lower() != "admin" and u.get("id")), None)
    if target_user:
        uid = int(target_user["id"])
        grant_status, grant_result = request_json(
            args.base,
            f"/api/admin/users/{uid}/grant_points",
            method="POST",
            token=token,
            body={"points": 1, "reason": "ops v2 smoke grant"},
        )
        adjust_status, adjust_result = request_json(
            args.base,
            f"/api/admin/users/{uid}/adjust_points",
            method="POST",
            token=token,
            body={"delta": -1, "reason": "ops v2 smoke rollback"},
        )
        results["points_ops"] = {
            "user_id": uid,
            "grant": {"http": grant_status, "status": grant_result.get("status")},
            "adjust": {"http": adjust_status, "status": adjust_result.get("status")},
        }
    else:
        results["points_ops"] = {"skipped": "no non-admin user found"}

    social_status, social_payload = request_json(args.base, "/api/admin/social-accounts", token=token)
    socials = social_payload.get("accounts") or []
    social_result: dict[str, object] = {"http": social_status, "count": len(socials)}
    candidate_social = next((s for s in socials if not bool(s.get("verified")) and s.get("id")), None)
    if candidate_social:
        verify_status, verify_payload = request_json(
            args.base,
            f"/api/admin/social-accounts/{int(candidate_social['id'])}/verify",
            method="POST",
            token=token,
        )
        social_result["verify_probe"] = {
            "http": verify_status,
            "expected_strict_mode": verify_status == 409,
            "detail": str(verify_payload.get("detail") or "")[:120],
        }
    results["social_accounts"] = social_result

    redemptions_status, redemptions_payload = request_json(args.base, "/api/admin/redemptions?limit=20", token=token)
    redemptions = redemptions_payload.get("items") or []
    redemption_result: dict[str, object] = {"http": redemptions_status, "count": len(redemptions)}
    if redemptions:
        redemption = redemptions[0]
        rid = int(redemption["id"])
        update_status, update_payload = request_json(
            args.base,
            f"/api/admin/redemptions/{rid}/update",
            method="POST",
            token=token,
            body={
                "status": str(redemption.get("status") or "pending"),
                "tracking_number": str(redemption.get("tracking_number") or f"SMOKE-{stamp}"),
                "admin_note": "ops v2 redemption smoke",
            },
        )
        redemption_result["update"] = {"id": rid, "http": update_status, "status": update_payload.get("status")}
    results["redemptions"] = redemption_result

    via_result: dict[str, object] = {}
    for key, path in {
        "control_overview": "/api/admin/intel/via/control-overview",
        "proposals": "/api/admin/intel/via/proposals",
        "live_policies": "/api/admin/intel/via/policies/live",
        "policy_history": "/api/admin/intel/via/policies/history",
    }.items():
        status, payload = request_json(args.base, path, token=token)
        items = payload.get("items") if isinstance(payload, dict) else None
        via_result[key] = {
            "http": status,
            "count": len(items) if isinstance(items, list) else None,
        }
    evaluate_status, evaluate_payload = request_json(
        args.base,
        "/api/admin/intel/via/evaluate-now?days=1&limit=40",
        method="POST",
        token=token,
    )
    proposals = evaluate_payload.get("proposals") if isinstance(evaluate_payload, dict) else None
    via_result["evaluate_now"] = {
        "http": evaluate_status,
        "status": evaluate_payload.get("status") if isinstance(evaluate_payload, dict) else None,
        "proposal_count": len(proposals) if isinstance(proposals, list) else None,
    }
    results["via"] = via_result

    all_good = (
        ok(manual_result)
        and bool(submission_id)
        and ("product_correction" in results and str((results["product_correction"] or {}).get("status")).lower() == "success")
        and all(
            isinstance(item, dict) and item.get("http") == 200
            for item in via_result.values()
        )
    )
    out_json(results, ensure_ascii=False, indent=2)
    return 0 if all_good else 1


if __name__ == "__main__":
    raise SystemExit(main())
