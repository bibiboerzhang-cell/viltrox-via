"""Safe coordinates for matched public works, never copied content bodies."""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit
import re

from app.domains.kol.profile_recall_activity_gate import parse_evidence_datetime
from app.domains.kol.search_sessions_serde import project_public_asset_url


def content_coordinates(record: dict[str, Any]) -> dict[str, str]:
    from app.domains.kol.profile_online_identity import is_platform_video_url

    raw_url = record.get("content_url")
    clean = project_public_asset_url(raw_url)
    if clean and is_platform_video_url(raw_url, platform=record.get("platform")):
        parsed = urlsplit(str(raw_url))
        if record.get("platform") == "youtube" and parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            clean = urlunsplit(("https", parsed.hostname or "", "/watch", f"v={video_id}", "")) if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", video_id) else ""
    else:
        clean = ""
    output = {"content_url": clean} if clean else {}
    for origin, target in (("posted_at", "content_posted_at"), ("fetched_at", "content_observed_at")):
        stamp = parse_evidence_datetime(record.get(origin))
        if stamp is not None:
            output[target] = stamp.isoformat()
    output["content_trace_status"] = "traceable" if clean and output.get("content_observed_at") and output.get("content_posted_at") else "incomplete"
    return output


def project_match_coordinates(match: dict[str, Any]) -> dict[str, str]:
    from app.domains.kol.profile_online_identity import is_platform_video_url

    if not str(match.get("field") or "").startswith("representative_evidence."):
        return {}
    if not any(key in match for key in ("content_url", "content_posted_at", "content_observed_at", "content_trace_status")):
        return {}
    platform = next((name for name in ("youtube", "instagram", "tiktok", "x", "reddit")
                     if is_platform_video_url(match.get("content_url"), platform=name)), "")
    return content_coordinates({
        "platform": platform, "content_url": match.get("content_url"),
        "posted_at": match.get("content_posted_at"), "fetched_at": match.get("content_observed_at"),
    })


def attach_content_match_coordinates(
    matches: list[dict[str, str]], records: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Locate the actual matching work, not whichever video was most recent."""
    from app.domains.kol.profile_recall_match_evidence import _contains_term

    result = []
    for match in matches:
        item = dict(match)
        field = str(item.get("field") or "")
        if field.startswith("representative_evidence."):
            name = field.split(".", 1)[1]
            term = item.get("observed_term") or item.get("term")
            coordinate = next((record for record in records[:24] if _contains_term(record.get(name), term)), None)
            if coordinate is not None:
                item.update({key: coordinate[key] for key in (
                    "content_url", "content_posted_at", "content_observed_at", "content_trace_status",
                ) if key in coordinate})
        result.append(item)
    return result
