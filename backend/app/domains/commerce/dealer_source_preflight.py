"""Read-only technical preflight for registered public Dealer sources.

This module records transport, robots, redirect, and bounded content-snapshot
facts.  It deliberately cannot approve terms, activate a source, extract a
Dealer candidate, or write a business row.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser


USER_AGENT = "VKPI-SourceReview/1.0"
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_TERMS_MARKERS = (
    "terms",
    "terms-of-use",
    "terms-and-conditions",
    "legal",
    "privacy",
)


class _TermsLinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = next((value for key, value in attrs if key.casefold() == "href"), None)
        if not href:
            return
        absolute = urljoin(self.base_url, href)
        parsed = urlsplit(absolute)
        base_host = str(urlsplit(self.base_url).hostname or "").casefold()
        if parsed.scheme not in {"http", "https"} or str(parsed.hostname or "").casefold() != base_host:
            return
        path = f"{parsed.path}?{parsed.query}".casefold()
        if any(marker in path for marker in _TERMS_MARKERS):
            self.links.append(absolute)


def robots_url(source_url: str) -> str:
    parsed = urlsplit(str(source_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source_url must be public HTTP(S)")
    return f"{parsed.scheme}://{parsed.netloc}/robots.txt"


def evaluate_robots(
    *, source_url: str, status_code: int | None, text: str | None
) -> dict[str, Any]:
    """Return a conservative fetch decision from one robots response."""
    if status_code in {404, 410}:
        return {
            "status": "not_published",
            "fetch_allowed": True,
            "reason": "robots_not_published",
        }
    if status_code != 200 or text is None:
        return {
            "status": "unavailable",
            "fetch_allowed": False,
            "reason": "robots_must_be_reviewed",
        }
    parser = RobotFileParser()
    parser.set_url(robots_url(source_url))
    parser.parse(text.splitlines())
    allowed = bool(parser.can_fetch(USER_AGENT, source_url))
    return {
        "status": "reviewed",
        "fetch_allowed": allowed,
        "reason": "robots_allows_source_path" if allowed else "robots_disallows_source_path",
        "sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
    }


def terms_link_candidates(base_url: str, html: str) -> list[str]:
    parser = _TermsLinkParser(base_url)
    try:
        parser.feed(str(html or ""))
    except Exception:
        return []
    return sorted(set(parser.links))[:8]


def audit_one_source(source: dict[str, Any], client: Any) -> dict[str, Any]:
    source_id = str(source.get("id") or "").strip()
    canonical_url = str(source.get("canonical_url") or "").strip()
    base = {
        "source_registry_id": source_id,
        "publisher": str(source.get("publisher") or ""),
        "source_kind": str(source.get("source_kind") or ""),
        "canonical_url": canonical_url,
        "terms_legal_approval": "not_performed_requires_human",
        "source_activation_recommended": False,
        "candidate_extraction_performed": False,
        "business_rows_written": 0,
        "claim_status": "descriptive_only",
    }
    try:
        robots_response = client.get(robots_url(canonical_url))
        robots = evaluate_robots(
            source_url=canonical_url,
            status_code=int(robots_response.status_code),
            text=robots_response.text if int(robots_response.status_code) == 200 else None,
        )
        robots["url"] = str(robots_response.url)
        robots["http_status"] = int(robots_response.status_code)
    except Exception as exc:  # network/provider failures are data, not approval
        return {
            **base,
            "technical_status": "robots_request_failed",
            "robots": {
                "status": "unavailable",
                "fetch_allowed": False,
                "reason": "robots_request_failed",
                "error": f"{exc.__class__.__name__}: {str(exc)[:240]}",
            },
            "snapshot": None,
        }
    if robots.get("fetch_allowed") is not True:
        return {
            **base,
            "technical_status": "blocked_by_robots_gate",
            "robots": robots,
            "snapshot": None,
        }
    try:
        response = client.get(canonical_url)
        content = bytes(response.content or b"")
        truncated = len(content) > MAX_SNAPSHOT_BYTES
        bounded = content[:MAX_SNAPSHOT_BYTES]
        content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().casefold()
        html = bounded[:1_500_000].decode("utf-8", "replace") if content_type in {"text/html", "application/xhtml+xml"} else ""
        snapshot = {
            "http_status": int(response.status_code),
            "final_url": str(response.url),
            "content_type": content_type or None,
            "content_length": len(content),
            "captured_bytes": len(bounded),
            "truncated": truncated,
            "sha256": hashlib.sha256(bounded).hexdigest(),
            "hash_scope": "prefix" if truncated else "complete_response",
            "terms_link_candidates": terms_link_candidates(str(response.url), html),
        }
        return {
            **base,
            "technical_status": (
                "reachable" if 200 <= int(response.status_code) < 400 else "http_error"
            ),
            "robots": robots,
            "snapshot": snapshot,
        }
    except Exception as exc:
        return {
            **base,
            "technical_status": "source_request_failed",
            "robots": robots,
            "snapshot": {
                "error": f"{exc.__class__.__name__}: {str(exc)[:240]}",
            },
        }


__all__ = [
    "MAX_SNAPSHOT_BYTES",
    "USER_AGENT",
    "audit_one_source",
    "evaluate_robots",
    "robots_url",
    "terms_link_candidates",
]
