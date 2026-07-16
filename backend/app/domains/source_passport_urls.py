"""Credential-free canonical URL identity for public source passports."""
from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref_src",
    }
)


def canonical_source_url(value: Any) -> str:
    """Return a stable public HTTPS URL or ``""`` for unsafe input.

    Credentials, fragments, traversal segments and non-HTTPS schemes are
    rejected.  Common tracking-only query keys are removed while semantic query
    keys are sorted and retained.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or any(character.isspace() for character in text):
        return ""
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return ""
    try:
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except (UnicodeError, ValueError):
        return ""
    path = parsed.path or "/"
    if any(segment in {".", ".."} for segment in path.split("/")):
        return ""
    path = path.rstrip("/") or "/"
    query_pairs = []
    try:
        for key, item in parse_qsl(parsed.query, keep_blank_values=True):
            normalized_key = key.casefold()
            if normalized_key.startswith("utm_") or normalized_key in _TRACKING_QUERY_KEYS:
                continue
            query_pairs.append((key, item))
    except ValueError:
        return ""
    query = urlencode(sorted(query_pairs), doseq=True)
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port not in (None, 443):
        netloc = f"{netloc}:{port}"
    return urlunsplit(("https", netloc, path, query, ""))


def source_url_identity(value: Any) -> dict[str, Any]:
    canonical = canonical_source_url(value)
    if not canonical:
        return {
            "valid": False,
            "canonical_url": None,
            "host": None,
            "url_sha256": None,
        }
    return {
        "valid": True,
        "canonical_url": canonical,
        "host": str(urlsplit(canonical).hostname or ""),
        "url_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


__all__ = ["canonical_source_url", "source_url_identity"]
