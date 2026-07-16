from __future__ import annotations

import json

from app.domains.settings import api_key_pool
from app.domains.settings import provider as provider_settings
from app.services.system import secrets_admin


_YOUTUBE_SECRET = "sensitive-youtube-credential-material-never-return"


def test_provider_status_never_returns_a_secret_derived_prefix(monkeypatch) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", _YOUTUBE_SECRET)
    monkeypatch.setattr(
        provider_settings.provider_health,
        "list_provider_status",
        lambda: {
            "providers": [
                {
                    "provider": "youtube",
                    "latest_status": "healthy",
                    "last_ok_at": "2026-07-15T00:00:00Z",
                }
            ]
        },
    )

    payload = provider_settings.provider_statuses()
    encoded = json.dumps(payload, ensure_ascii=False)
    youtube = next(row for row in payload["providers"] if row["provider"] == "youtube")

    assert payload["full_key_readable"] is False
    assert youtube["configured"] is True
    assert youtube["key_mask"] == "configured"
    assert _YOUTUBE_SECRET not in encoded
    assert _YOUTUBE_SECRET[:15] not in encoded
    assert "sensitive-youtube" not in encoded


def test_provider_mask_is_a_fixed_configuration_marker() -> None:
    assert secrets_admin.mask_secret("") == ""
    assert secrets_admin.mask_secret(_YOUTUBE_SECRET) == "configured"
    assert secrets_admin.mask_secret("a completely different secret") == "configured"


def test_google_credential_alias_matches_model_readiness_without_leaking_key(
    monkeypatch,
) -> None:
    google_secret = "google-alias-sensitive-credential-never-return"
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", google_secret)
    monkeypatch.setattr(
        provider_settings.provider_health,
        "list_provider_status",
        lambda: {
            "providers": [
                {
                    "provider": "google",
                    "latest_status": "unknown",
                }
            ]
        },
    )

    payload = provider_settings.provider_statuses()
    encoded = json.dumps(payload, ensure_ascii=False)
    google = next(row for row in payload["providers"] if row["provider"] == "google")

    assert google["configured"] is True
    assert google["key_mask"] == "configured"
    assert google_secret not in encoded
    assert google_secret[:15] not in encoded


def test_api_key_pool_public_row_hides_historical_prefixes() -> None:
    payload = api_key_pool._row_to_public(
        {
            "id": 7,
            "account_name": "youtube-primary",
            "provider": "youtube",
            "key_encrypted": "gAAAAABencrypted-only",
            "key_prefix": _YOUTUBE_SECRET[:6] + "...",
            "daily_quota": 100,
            "enabled": True,
            "last_used_at": None,
            "updated_at": "2026-07-15T00:00:00Z",
        }
    )
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["credential_status"] == "configured"
    assert payload["key_prefix"] == "configured"
    assert "sensitive-youtube" not in encoded
    assert "gAAAAAB" not in encoded
