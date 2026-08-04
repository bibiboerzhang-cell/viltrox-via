"""Truthful serialization for staff avatar URLs.

Database rows can outlive files in the shared uploads volume.  Returning such
rows as usable URLs makes every page issue a guaranteed 404.  Keep remote HTTPS
avatars, but expose a local staff-avatar URL only when it maps to a real file
inside the configured staff-avatar directory.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from app.core.config import UPLOAD_DIR


STAFF_AVATAR_URL_PREFIX = "/uploads/staff_avatars/"
STAFF_AVATAR_DIR = UPLOAD_DIR / "staff_avatars"


def serialize_staff_avatar_url(value: object) -> str | None:
    """Return a browser-safe, evidence-backed staff avatar URL.

    HTTPS avatars are remote references and therefore cannot be checked against
    the local uploads volume.  Local URLs use the one supported route and must
    point to a regular file below ``STAFF_AVATAR_DIR``.  Encoded names, nested
    paths, queries and fragments are rejected because uploaded staff avatars
    are generated as a single ASCII basename.
    """
    raw = str(value or "").strip()
    if not raw:
        return None

    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        if (
            parsed.scheme.lower() == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        ):
            return raw
        return None

    if parsed.query or parsed.fragment or not parsed.path.startswith(STAFF_AVATAR_URL_PREFIX):
        return None
    name = parsed.path.removeprefix(STAFF_AVATAR_URL_PREFIX)
    if (
        not name
        or "%" in name
        or unquote(name) != name
        or "\\" in name
        or PurePosixPath(name).name != name
        or name in {".", ".."}
    ):
        return None

    try:
        root = STAFF_AVATAR_DIR.resolve(strict=True)
        candidate = (root / name).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if candidate.parent != root or not candidate.is_file():
        return None
    return f"{STAFF_AVATAR_URL_PREFIX}{name}"
