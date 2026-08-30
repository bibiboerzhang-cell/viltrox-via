#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from stdout_utils import out, out_json


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_env import apply_runtime_env  # noqa: E402

apply_runtime_env()

from smoke_auth_social_student import create_student_session, http_json  # noqa: E402
from app.db.connection import close_db_runtime  # noqa: E402
from app.db.startup import init_db_runtime  # noqa: E402


def main() -> int:
    asyncio.run(init_db_runtime())
    try:
        session = create_student_session()
        token = session["token"]

        out("1) bootstrap Via session")
        created = http_json(
            "POST",
            "/api/via/sessions",
            token=token,
            payload={
                "surface": "upload",
                "signed_device_id": "smoke-device-v2",
                "client_fingerprint": "smoke-cli",
                "persona": {"display_name": "Via Smoke", "talk_style": "concise"},
            },
        )
        session_key = str((created.get("session") or {}).get("session_key") or "")
        if not session_key:
            raise RuntimeError(f"via session bootstrap failed: {created}")

        out("2) send real Via prompt on the new runtime")
        response = http_json(
            "POST",
            f"/api/via/sessions/{session_key}/respond",
            token=token,
            payload={
                "surface": "upload",
                "text": "I shoot Sony full frame and want a Viltrox lens for portraits plus short-form video. Give me a compact recommendation and why.",
            },
        )
        if not response.get("ok"):
            raise RuntimeError(f"via response failed: {response}")
        reply = response.get("reply") or {}
        reply_payload = reply.get("payload") or {}
        reward_target = reply_payload.get("reward_trace_target") or {}
        reply_text = str(reply.get("text") or "").strip()
        if not reply_text:
            raise RuntimeError(f"via reply text missing: {response}")

        out("3) record reward trace back into the control loop")
        reward = http_json(
            "POST",
            f"/api/via/sessions/{session_key}/reward-traces",
            token=token,
            payload={
                "event_type": "compare",
                "surface": "upload",
                "source": "smoke-script",
                "origin": "integration",
                "product_key": "sony-full-frame-portrait-video",
                "decision_id": reward_target.get("decision_id", ""),
                "event_value": 1.0,
                "idempotency_key": f"{session_key}:compare:smoke",
            },
        )
        if not reward.get("ok"):
            raise RuntimeError(f"reward trace failed: {reward}")

        out("4) fetch Via bundle snapshot")
        bundle = http_json("GET", f"/api/via/sessions/{session_key}", token=token)
        if not bundle.get("session"):
            raise RuntimeError(f"via bundle fetch failed: {bundle}")

        summary = {
            "session_key": session_key,
            "reply_title": reply.get("title"),
            "reply_preview": reply_text[:140],
            "decision_id": reward_target.get("decision_id"),
            "reward_event": (reward.get("trace") or {}).get("event_type"),
            "reward_summary": reward.get("summary"),
            "event_count": len(bundle.get("events") or []),
            "memory_ref_count": len((bundle.get("session") or {}).get("memory_refs") or []),
        }
        out_json(summary, ensure_ascii=False, indent=2)
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
