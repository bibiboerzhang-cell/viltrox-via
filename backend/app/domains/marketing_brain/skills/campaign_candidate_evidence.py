"""Candidate display evidence copied from the current masked Pool projection.

This is record provenance, not verified personal fit. No model reasons, fit
scores, tier labels, raw provider blobs or synthesized links become evidence.
Pool updated_at is a record-update time; it is never called collection time.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
from typing import Any
from urllib.parse import urlsplit

from .campaign_plan_validation import exact_nonnegative_int

_FACT_LIMITS = {"bio": 600, "country": 120, "language": 120, "primary_topic": 200, "content_style": 200}


def unique_pool_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same Pool ID is one record, in stable first-source order; no alias merge."""
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = exact_nonnegative_int(row.get("id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append({**row, "id": identity})
    return result


def _source_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        return None
    text = value.strip()
    if any(character.isspace() or ord(character) < 32 for character in text):
        return None
    try:
        parsed = urlsplit(text)
        _ = parsed.port  # Validate an explicitly supplied port without network I/O.
        # Do not expose credential/query/fragment material or rewrite a source.
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            return None
    except ValueError:
        return None
    return text


def _record_updated_at(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) > 40 or not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", text):
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text  # Preserve source timezone/precision; do not invent one.


def _evidence(row: dict[str, Any]) -> dict[str, Any]:
    identity = row["id"]
    reference = f"kol_pool:{identity}"
    facts = []
    for field, limit in _FACT_LIMITS.items():
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            facts.append({"field": field, "value": value.strip()[:limit], "source_ref": reference})
    url = _source_url(row.get("profile_url"))
    updated_at = _record_updated_at(row.get("updated_at"))
    gaps = [
        {"code": "match_reason_unverified", "message": "现有资料记录不等于个人匹配理由，需人工核对产品、市场与内容证据。"},
        {"code": "collection_time_unknown", "message": "当前资料未提供可核验的采集时间。"},
    ]
    if not facts:
        gaps.append({"code": "profile_facts_missing", "message": "本次候选池未提供可展示的画像资料记录。"})
    if url is None:
        gaps.append({"code": "source_url_missing", "message": "本次候选池未提供可展示的来源链接，不根据账号名补造。"})
    if updated_at is None:
        gaps.append({"code": "record_updated_at_unknown", "message": "当前资料更新时间未知或格式不可核验。"})
    return {
        "schema_version": "campaign_candidate_evidence.v1",
        "status": "partial" if facts or url else "unknown",
        "claim_status": "descriptive_only", "match_status": "unverified",
        "facts": facts,
        "source_refs": [{"ref": reference, "kind": "kol_pool", "record_id": identity,
                         "url": url, "record_updated_at": updated_at}],
        "observed_at": None, "gaps": gaps,
    }


def _candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "handle": row.get("handle") or row.get("username") or row.get("name") or row.get("display_name"),
        "platform": row.get("platform"),
        "fit": row.get("viltrox_fit_score") if "viltrox_fit_score" in row else row.get("fit_score"),
        "evidence": _evidence(row),
    }


def _coverage(planned: int, shown: int) -> dict[str, Any]:
    missing = shown < planned
    return {
        "planned_count": planned, "unique_sample_count": shown,
        "status": ("empty" if shown == 0 else "insufficient") if missing else "count_met",
        "gaps": [{
            "code": "candidate_samples_insufficient",
            "message": f"本梯队仅展示 {shown} 个唯一候选，少于拟定的 {planned} 人；人数提议不是已确认合作人数。",
        }] if missing else [],
    }


def attach_candidate_evidence(mix: list[dict[str, Any]], pool_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Final server-owned projection, shared by rule and model result paths."""
    by_id = {row["id"]: row for row in unique_pool_rows(pool_items)}
    seen = set()
    result = []
    for group in mix:
        projected = deepcopy(group)
        samples = []
        for sample in group.get("sample_creators") or []:
            identity = exact_nonnegative_int(sample.get("id")) if isinstance(sample, dict) else None
            if identity not in by_id or identity in seen:
                continue
            seen.add(identity)
            samples.append(_candidate(by_id[identity]))
        projected["sample_creators"] = samples
        projected["candidate_coverage"] = _coverage(group["count"], len(samples))
        result.append(projected)
    return result
