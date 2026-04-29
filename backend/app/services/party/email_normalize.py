"""
services/party/email_normalize.py

Email normalization + hashing for identity stitching.

Design constraints (locked 2026-04-22):
    - Gmail rule (dots + plus alias) applies ONLY to @gmail.com and @googlemail.com.
    - Other domains get minimal normalization (lowercase + strip whitespace).
    - Both raw-hash and normalized-hash are stored as identity_links, dual-indexed.
    - Normalization is recorded in consent_records.email_normalization_consent for audit.
    - Hash is SHA-256 (no salt — cross-system joinability > local obfuscation).

Module exports:
    normalize_email(email) -> str                  # normalized form (lowercased, dots/plus handled)
    hash_email(email_raw_or_normalized) -> str     # SHA-256 hex
    email_hashes(email) -> {raw_hash, normalized_hash, was_normalized}
"""
from __future__ import annotations

import hashlib
import re
from typing import TypedDict


# Domains where we apply the "dots are ignored + plus alias stripped" rule.
# Everything else: lowercase + trim only.
_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})

_EMAIL_SPLIT = re.compile(r"^\s*([^@\s]+)@([^@\s]+?)\s*$")


class EmailHashes(TypedDict):
    raw_hash: str
    normalized_hash: str
    was_normalized: bool
    normalized_form: str       # redacted display form (for audit, not storage)
    raw_domain: str            # e.g. "gmail.com" — OK to store, not PII


def _minimal_normalize(email: str) -> str:
    """Lowercase + trim whitespace, no gmail rules."""
    return (email or "").strip().lower()


def normalize_email(email: str) -> str:
    """
    Returns the normalized form of an email address.

    For Gmail (@gmail.com, @googlemail.com):
        - Remove all dots from the local part
        - Strip everything after '+' in the local part
        - Rewrite @googlemail.com as @gmail.com (they are the same inbox)
        - Lowercase the full address

    For all other domains:
        - Lowercase the full address, trim whitespace
        - No local-part rewriting

    Examples:
        'Foo.Bar+news@Gmail.com'   → 'foobar@gmail.com'
        'foo+x@googlemail.com'     → 'foo@gmail.com'
        'Foo.Bar@Outlook.com'      → 'foo.bar@outlook.com'    (dots preserved!)
    """
    if not email:
        return ""

    e = _minimal_normalize(email)
    m = _EMAIL_SPLIT.match(e)
    if not m:
        # Not a parseable email; return the minimally normalized form
        return e

    local, domain = m.group(1), m.group(2)

    if domain not in _GMAIL_DOMAINS:
        return f"{local}@{domain}"

    # Gmail rules
    # 1. Strip everything after '+'
    local = local.split("+", 1)[0]
    # 2. Remove all dots
    local = local.replace(".", "")
    # 3. googlemail.com → gmail.com
    return f"{local}@gmail.com"


def hash_email(value: str) -> str:
    """SHA-256 hex of the input string (does NOT normalize; caller decides form)."""
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _redact_preview(email: str) -> str:
    """
    Return a short preview like 'sa***@gm***.com' for audit logs / admin UI.
    Never used as a lookup key. Stored in identity_links.link_value_preview.
    """
    if not email or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    if not local or not domain:
        return ""
    local_redacted = (local[:2] + "***") if len(local) > 2 else (local[0] + "*")
    if "." in domain:
        dom_name, dot, tld = domain.partition(".")
        dom_redacted = (dom_name[:2] + "***") if len(dom_name) > 2 else (dom_name[0] + "*")
        return f"{local_redacted}@{dom_redacted}.{tld}"
    return f"{local_redacted}@{domain}"


def email_hashes(email: str) -> EmailHashes:
    """
    Computes everything needed to create identity_links rows for an email.

    Returns:
        raw_hash         SHA-256 of the minimally-normalized (lowercased/trimmed) form
        normalized_hash  SHA-256 of the fully-normalized form (gmail-aware)
        was_normalized   True iff raw and normalized differ
        normalized_form  redacted preview (e.g. 'sa***@gm***.com')
        raw_domain       'gmail.com' / 'outlook.com' / ... (bare domain, not PII on its own)
    """
    if not email:
        return {
            "raw_hash": "",
            "normalized_hash": "",
            "was_normalized": False,
            "normalized_form": "",
            "raw_domain": "",
        }

    raw = _minimal_normalize(email)
    normalized = normalize_email(email)
    raw_domain = raw.rpartition("@")[2] if "@" in raw else ""

    return {
        "raw_hash": hash_email(raw),
        "normalized_hash": hash_email(normalized),
        "was_normalized": raw != normalized,
        "normalized_form": _redact_preview(normalized),
        "raw_domain": raw_domain,
    }
