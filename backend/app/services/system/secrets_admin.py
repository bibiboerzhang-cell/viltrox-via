"""
services/system/secrets_admin.py — high-risk .env key rotation helpers.

This module never returns full secret values. It writes the project .env
atomically so admin-initiated rotation can be reviewed and rolled back.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.services.auth.email import send_email

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ENV_PATH = Path(os.environ.get("VILTROX_ENV_PATH", PROJECT_ROOT / ".env"))
SECURITY_NOTIFY_EMAIL = os.environ.get("SECURITY_NOTIFY_EMAIL", "jianboz@viltrox.com").strip()

PROVIDER_ENV_KEYS = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_PREVIOUS"),
    "google": ("GEMINI_API_KEY", "GEMINI_API_KEY_PREVIOUS"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_API_KEY_PREVIOUS"),
    "openai": ("OPENAI_API_KEY", "OPENAI_API_KEY_PREVIOUS"),
    "apify": ("APIFY_TOKEN", "APIFY_TOKEN_PREVIOUS"),
    "resend": ("RESEND_API_KEY", "RESEND_API_KEY_PREVIOUS"),
}


def mask_secret(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return f"{raw[:15]}..."


def provider_key_prefix(provider: str) -> str:
    env_key, _ = provider_env_keys(provider)
    return mask_secret(os.environ.get(env_key, ""))


def provider_env_keys(provider: str) -> tuple[str, str]:
    key = str(provider or "").strip().lower()
    if key not in PROVIDER_ENV_KEYS:
        raise ValueError("unsupported provider")
    return PROVIDER_ENV_KEYS[key]


def _read_env_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _parse_env(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _render_env(lines: list[str], updates: dict[str, str]) -> str:
    remaining = dict(updates)
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            rendered.append(line)
            continue
        key, _value = stripped.split("=", 1)
        name = key.strip()
        if name in remaining:
            rendered.append(f"{name}={remaining.pop(name)}")
        else:
            rendered.append(line)
    for key, value in remaining.items():
        rendered.append(f"{key}={value}")
    return "\n".join(rendered).rstrip() + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            logger.warning("secrets_admin.tmp_cleanup_failed", exc_info=True)


def rotate_provider_key(
    provider: str,
    new_key: str,
    *,
    move_current_to_previous: bool = True,
    actor_email: str = "",
) -> dict[str, Any]:
    clean_key = str(new_key or "").strip()
    if len(clean_key) < 12:
        raise ValueError("new_key is too short")
    env_key, previous_key = provider_env_keys(provider)
    lines = _read_env_lines(ENV_PATH)
    values = _parse_env(lines)
    current = values.get(env_key) or os.environ.get(env_key, "")
    updates = {env_key: clean_key}
    if move_current_to_previous and current and current != clean_key:
        updates[previous_key] = current
    content = _render_env(lines, updates)
    _atomic_write(ENV_PATH, content)
    os.environ[env_key] = clean_key
    if previous_key in updates:
        os.environ[previous_key] = updates[previous_key]
    _notify_rotation(provider, env_key, actor_email)
    return {
        "ok": True,
        "provider": str(provider).lower(),
        "env_key": env_key,
        "key_prefix": mask_secret(clean_key),
        "previous_set": bool(updates.get(previous_key)),
        "requires_restart": True,
    }


def _notify_rotation(provider: str, env_key: str, actor_email: str) -> None:
    if not SECURITY_NOTIFY_EMAIL:
        return
    try:
        send_email(
            SECURITY_NOTIFY_EMAIL,
            f"V-OS provider key rotated: {provider}",
            (
                "<p>Provider key rotation was requested from V-OS Admin.</p>"
                f"<p><b>Provider:</b> {provider}</p>"
                f"<p><b>Env key:</b> {env_key}</p>"
                f"<p><b>Actor:</b> {actor_email or 'unknown'}</p>"
                "<p>The full key is never included in email or API responses.</p>"
            ),
        )
    except Exception:
        logger.warning("secrets_admin.rotation_email_failed", exc_info=True)
