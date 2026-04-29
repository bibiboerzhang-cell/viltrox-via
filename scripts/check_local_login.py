#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from stdout_utils import out_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check local auth login without printing tokens.")
    parser.add_argument("email")
    parser.add_argument("--base", default="http://127.0.0.1:8102")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    password = sys.stdin.readline().rstrip("\n")
    body = json.dumps({"email": args.email.strip().lower(), "password": password}).encode()
    request = urllib.request.Request(
        f"{args.base.rstrip('/')}/api/auth/login",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        out_json({"http_status": exc.code, "body": raw[:200]})
        return 1

    out_json(
        {
            "status": payload.get("status"),
            "role": (payload.get("user") or {}).get("role"),
            "email": (payload.get("user") or {}).get("email"),
            "has_token": bool(payload.get("token")),
        },
        ensure_ascii=False,
    )
    return 0 if payload.get("status") == "success" and payload.get("token") else 1


if __name__ == "__main__":
    raise SystemExit(main())
