"""Shared fail-closed contract for human review evidence.

Review endpoints write facts that can influence learning and scorecards.  They
therefore accept only the original Viltrox workspace, a resolved staff actor,
bounded correlation ids, and small structured evidence references.  The
helpers are pure and never touch a provider, model, or database.
"""
from __future__ import annotations

import json
import hashlib
import math
import re
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

_CORRELATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$")
_ALLOWED_EVIDENCE_SOURCES = {
    "db_record", "ledger", "manual", "metric", "project", "receipt", "snapshot", "url",
}
_SECRET_MARKERS = (
    "authorization:", "bearer ", "api_key", "api-key", "apikey=", "password=",
    "secret=", "token=", "access_token=", "client_secret", "x-amz-signature",
    "x-goog-signature", "sig=",
)
_SECRET_PATTERNS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_-]{12,}\b"),
)
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:authorization|api[_-]?key|access[_-]?(?:key|token)|"
    r"auth[_-]?token|session[_-]?token|refresh[_-]?token|token|"
    r"client[_-]?secret|private[_-]?key|webhook[_-]?secret|dsn|"
    r"password|passwd|secret|signature|"
    r"signed[_-]?url|provider[_-]?(?:key|secret|token)|cookie|credential)(?:$|[_-])",
    re.IGNORECASE,
)
_QUERY_URL = re.compile(r"(?:https?://|/)[^\s\"']*\?[^\s\"']+", re.IGNORECASE)


def _contains_secret_marker(value: str) -> bool:
    decoded = str(value)
    for _ in range(3):
        lowered = decoded.lower()
        if any(marker in lowered for marker in _SECRET_MARKERS) or any(
            pattern.search(decoded) is not None for pattern in _SECRET_PATTERNS
        ):
            return True
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value[:16_384]
    lowered = decoded.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS) or any(
        pattern.search(decoded) is not None for pattern in _SECRET_PATTERNS
    )


def _safe_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    # Signed/tracking query strings and fragments are not review evidence.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", "", ""))


def _valid_observed_at(value: str) -> bool:
    if not value:
        return True
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def reviewer_context(staff: dict[str, Any] | None) -> tuple[int, int] | None:
    """Return ``(staff_id, organization_id)`` only for resolved org 1."""
    try:
        actor_id = int((staff or {}).get("id") or (staff or {}).get("staff_id") or 0)
        organization_id = int((staff or {}).get("organization_id") or 0)
    except (TypeError, ValueError):
        return None
    scope_status = str((staff or {}).get("organization_scope_status") or "").strip().lower()
    if actor_id <= 0 or organization_id != 1 or scope_status != "resolved":
        return None
    return actor_id, organization_id


def normalize_correlation(value: Any) -> str | None:
    correlation = str(value or "").strip()
    return (
        correlation
        if _CORRELATION_RE.fullmatch(correlation) and not _contains_secret_marker(correlation)
        else None
    )


def normalize_review_text(value: Any, *, max_length: int) -> str | None:
    """Normalize reviewer prose and refuse likely pasted credentials."""
    text = " ".join(str(value or "").replace("\x00", " ").split())
    if not text or len(text) > max(1, int(max_length)) or _contains_secret_marker(text):
        return None
    return text


def normalize_evidence(value: Any, *, max_items: int = 20) -> list[dict[str, str]] | None:
    """Return bounded evidence or ``None`` when any row is ambiguous/unsafe."""
    if not isinstance(value, list) or not value or len(value) > max_items:
        return None
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        source = str(item.get("source") or "").strip().lower()
        reference = " ".join(str(item.get("reference") or "").replace("\x00", " ").split())
        evidence_type = str(item.get("type") or "reference").strip().lower()
        observed_at = str(item.get("observed_at") or "").strip()
        if source not in _ALLOWED_EVIDENCE_SOURCES:
            return None
        if len(reference) < 4 or len(reference) > 500:
            return None
        if (
            not evidence_type or len(evidence_type) > 50 or len(observed_at) > 80
            or _contains_secret_marker(evidence_type)
            or _contains_secret_marker(observed_at)
            or not _valid_observed_at(observed_at)
        ):
            return None
        if _contains_secret_marker(reference):
            return None
        if source == "url":
            reference = _safe_url(reference) or ""
            if not reference:
                return None
        row = {"source": source, "reference": reference, "type": evidence_type}
        if observed_at:
            row["observed_at"] = observed_at
        rows.append(row)
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return rows if len(encoded.encode("utf-8")) <= 16_384 else None


def redact_review_snapshot(value: Any, *, max_depth: int = 6) -> Any:
    """Return a small JSON-safe snapshot suitable for manager review.

    Sensitive keys are omitted, suspicious strings are replaced, and URLs lose
    userinfo, query strings and fragments.  The result is evidence display, not
    a substitute for the immutable source row that is re-read during review.
    """
    def visit(current: Any, depth: int) -> Any:
        if depth > max_depth:
            return "[TRUNCATED]"
        if current is None or isinstance(current, (bool, int)):
            return current
        if isinstance(current, float):
            return current if math.isfinite(current) else None
        if isinstance(current, str):
            text = current.replace("\x00", " ")[:4000]
            if _contains_secret_marker(text):
                return "[REDACTED]"
            stripped = text.strip()
            if stripped.lower().startswith(("http://", "https://")):
                return _safe_url(stripped) or "[REDACTED]"
            if _QUERY_URL.search(text):
                return "[REDACTED URL]"
            return text
        if isinstance(current, (list, tuple)):
            return [visit(item, depth + 1) for item in list(current)[:100]]
        if isinstance(current, dict):
            result: dict[str, Any] = {}
            for raw_key in sorted(current, key=lambda item: str(item))[:100]:
                key = str(raw_key)[:120]
                normalized_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()
                if _SENSITIVE_KEY.search(normalized_key):
                    continue
                result[key] = visit(current[raw_key], depth + 1)
            return result
        return visit(str(current), depth + 1)

    redacted = visit(value, 0)
    encoded = canonical_review_json(redacted)
    if len(encoded.encode("utf-8")) > 65_536:
        return {"status": "redacted", "reason": "snapshot_too_large"}
    return redacted


def canonical_review_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )


def review_snapshot_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_review_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "canonical_review_json", "normalize_correlation", "normalize_evidence",
    "normalize_review_text", "redact_review_snapshot", "review_snapshot_sha256",
    "reviewer_context",
]
