"""V-KPI settings-facing helpers.

This layer intentionally returns only provider status and masked key prefixes.
Full API keys stay server-side.
"""
from __future__ import annotations

import os
from typing import Any

from app.services.system import provider_health


PROVIDER_LABELS = {
    "anthropic": "Claude",
    "google": "Gemini",
    "openai": "OpenAI",
    "apify": "Apify",
    "youtube": "YouTube Data API",
    "resend": "Resend",
}

# Keep the settings surface aligned with the authoritative model-readiness
# credential check.  Google SDK deployments commonly use any one of these
# names; treating only GEMINI_API_KEY as configured created a contradictory UI
# where six Google models were configured while the Gemini provider card said
# "not configured".
PROVIDER_CREDENTIAL_ENVS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": (
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_GENERATIVE_AI_API_KEY",
    ),
    "openai": ("OPENAI_API_KEY",),
    "apify": ("APIFY_TOKEN",),
    "youtube": ("YOUTUBE_API_KEY",),
    "resend": ("RESEND_API_KEY",),
}


def _configured(provider: str) -> bool:
    names = PROVIDER_CREDENTIAL_ENVS.get(str(provider or "").strip().lower(), ())
    return any(bool(str(os.environ.get(name) or "").strip()) for name in names)


def _mask(provider: str) -> str:
    # Never derive a public value from a credential.  This fixed marker also
    # works when Google uses an accepted alias instead of GEMINI_API_KEY.
    return "configured" if _configured(provider) else ""


def provider_statuses() -> dict[str, Any]:
    raw = provider_health.list_provider_status().get("providers") or []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        provider = str(item.get("provider") or "").strip().lower()
        if not provider:
            continue
        seen.add(provider)
        rows.append(
            {
                "provider": provider,
                "label": PROVIDER_LABELS.get(provider, provider),
                "configured": _configured(provider),
                "latest_status": str(item.get("latest_status") or "unknown"),
                "ok": str(item.get("latest_status") or "").lower() == "healthy",
                "last_ok_at": item.get("last_ok_at") or "",
                "last_error": item.get("last_error") or "",
                "consecutive_failures": int(item.get("consecutive_failures") or 0),
                "updated_at": item.get("updated_at") or "",
                "key_mask": _mask(provider),
                "key_visible": False,
                "can_probe": True,
            }
        )
    for provider in ("apify", "anthropic", "google", "openai", "youtube"):
        if provider in seen:
            continue
        rows.append(
            {
                "provider": provider,
                "label": PROVIDER_LABELS.get(provider, provider),
                "configured": _configured(provider),
                "latest_status": "unknown",
                "ok": False,
                "last_ok_at": "",
                "last_error": "",
                "consecutive_failures": 0,
                "updated_at": "",
                "key_mask": _mask(provider),
                "key_visible": False,
                "can_probe": True,
            }
        )
    rows.sort(key=lambda row: str(row.get("label") or row.get("provider") or ""))
    return {"providers": rows, "full_key_readable": False}


async def probe(provider: str) -> dict[str, Any]:
    result = await provider_health.probe_provider(provider)
    provider_health.record_provider_probe(str(result.get("provider") or provider), bool(result.get("ok")), str(result.get("error") or ""))
    status = provider_statuses()
    return {
        "result": {
            "provider": result.get("provider") or provider,
            "ok": bool(result.get("ok")),
            "status": result.get("status") or "unknown",
            "error": result.get("error") or "",
        },
        "providers": status.get("providers") or [],
        "full_key_readable": False,
    }
