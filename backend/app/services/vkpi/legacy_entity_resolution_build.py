"""Build helpers for legacy KOL entity resolution candidates."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.vkpi.legacy_import_audit import _first_url, _text
from app.services.vkpi.legacy_import_staging import PIPELINE_TABLES, json_dumps


logger = get_logger(__name__)


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row.items()) if hasattr(row, "items") else dict(row)


def _load_json(value: str) -> Any:
    try:
        return json.loads(value or "{}")
    except Exception as exc:
        logger.warning("legacy entity resolution json parse failed: %s", exc)
        return {}


def _profile_url_from_raw(raw: dict[str, Any]) -> str:
    for key in ("主页链接", "频道/主页链接", "红人视频链接", "发布链接", "内容发布链接", "回片链接"):
        if _text(raw.get(key)):
            return _text(raw.get(key))
    return _first_url(" ".join(_text(value) for value in raw.values()))


def _canonical_key(row: dict[str, Any]) -> str:
    key = _text(row.get("dedup_key"))
    if key:
        return key.lower()
    platform = _text(row.get("normalized_platform") or row.get("platform")).lower()
    handle = _text(row.get("normalized_handle") or row.get("handle")).lower()
    return f"{platform}:{handle}" if platform and handle else ""


def _split_key(key: str) -> tuple[str, str]:
    if ":" not in key:
        return "", key
    platform, handle = key.split(":", 1)
    return platform, handle


def fetch_staging_rows(import_batch_id: int) -> list[dict[str, Any]]:
    conn = get_conn()
    queries = {
        "kol_profiles": """
            SELECT id, source_sheet, source_row, platform, normalized_platform,
                   handle, normalized_handle, dedup_key, display_name, country,
                   region, category, email, phone, contact_missing,
                   contact_visibility_level, raw_row_json
            FROM vkpi_legacy_kol_profiles_staging
            WHERE import_batch_id=?
        """,
        "cooperations": """
            SELECT id, source_sheet, source_row, platform, normalized_platform,
                   handle, normalized_handle, dedup_key, display_name, product,
                   project, status, content_link, cost_amount, cost_currency,
                   raw_row_json
            FROM vkpi_legacy_cooperations_staging
            WHERE import_batch_id=?
        """,
        "risk_watchlist": """
            SELECT id, source_sheet, source_row, platform, normalized_platform,
                   handle, normalized_handle, dedup_key, display_name, risk_type,
                   risk_reason, severity, evidence, status, raw_row_json
            FROM vkpi_legacy_risk_watchlist_staging
            WHERE import_batch_id=?
        """,
    }
    rows: list[dict[str, Any]] = []
    for pipeline, sql in queries.items():
        table = PIPELINE_TABLES[pipeline]
        for row in conn.execute(sql, (import_batch_id,)).fetchall():
            item = _row_to_dict(row)
            item["pipeline"] = pipeline
            item["staging_table"] = table
            item["canonical_key"] = _canonical_key(item)
            item["raw"] = _load_json(item.get("raw_row_json") or "{}")
            rows.append(item)
    return rows


def _pick_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profiles = [row for row in rows if row["pipeline"] == "kol_profiles"]
    if profiles:
        return profiles[0]
    return rows[0]


def _contact_status(profile: dict[str, Any]) -> str:
    if _text(profile.get("email")) or _text(profile.get("phone")):
        return "available_restricted"
    if profile.get("pipeline") == "kol_profiles" and bool(profile.get("contact_missing")):
        return "missing"
    return "unknown"


def _weak_label(
    *,
    profile_count: int,
    cooperation_count: int,
    risk_rows: list[dict[str, Any]],
    contact_status: str,
) -> tuple[str, float, list[str]]:
    reasons: list[str] = []
    high_risk = any(_text(row.get("severity")).lower() == "high" for row in risk_rows)
    if profile_count == 0:
        reasons.append("missing_kol_profile")
    if risk_rows:
        reasons.append("risk_watchlist")
    if contact_status == "missing":
        reasons.append("contact_missing")
    if cooperation_count == 0:
        reasons.append("no_cooperation_history")

    if high_risk:
        return "blocked_risk", 0.82, reasons
    if profile_count and cooperation_count and not risk_rows:
        return "ready", 0.98, reasons
    if profile_count and not risk_rows:
        return "profile_only_review", 0.9, reasons
    if profile_count and risk_rows:
        return "risk_review", 0.88, reasons
    if cooperation_count:
        return "profile_missing_review", 0.78, reasons
    return "manual_review", 0.65, reasons or ["low_evidence"]


def build_entity_payload(canonical_key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    platform, handle = _split_key(canonical_key)
    profile = _pick_profile(rows)
    risk_rows = [row for row in rows if row["pipeline"] == "risk_watchlist"]
    profile_count = sum(1 for row in rows if row["pipeline"] == "kol_profiles")
    cooperation_count = sum(1 for row in rows if row["pipeline"] == "cooperations")
    contact_status = _contact_status(profile)
    weak_label, confidence, reasons = _weak_label(
        profile_count=profile_count,
        cooperation_count=cooperation_count,
        risk_rows=risk_rows,
        contact_status=contact_status,
    )
    display_name = next((_text(row.get("display_name")) for row in rows if _text(row.get("display_name"))), handle)
    profile_url = next((_profile_url_from_raw(row.get("raw") or {}) for row in rows if _profile_url_from_raw(row.get("raw") or {})), "")
    identity = {
        "canonical_key": canonical_key,
        "platform": platform,
        "handle": handle,
        "display_name_candidates": sorted({_text(row.get("display_name")) for row in rows if _text(row.get("display_name"))}),
        "profile_url": profile_url,
        "contact_status": contact_status,
    }
    evidence = {
        "sources": Counter(row["pipeline"] for row in rows),
        "source_rows": [
            {
                "pipeline": row["pipeline"],
                "staging_id": int(row["id"]),
                "source_sheet": row["source_sheet"],
                "source_row": int(row["source_row"]),
            }
            for row in rows[:50]
        ],
        "risk": [
            {
                "risk_type": _text(row.get("risk_type")),
                "severity": _text(row.get("severity")),
                "status": _text(row.get("status")),
            }
            for row in risk_rows
        ],
    }
    return {
        "canonical_key": canonical_key,
        "normalized_platform": platform,
        "normalized_handle": handle,
        "display_name": display_name,
        "profile_url": profile_url,
        "country": _text(profile.get("country")),
        "region": _text(profile.get("region")),
        "category": _text(profile.get("category")),
        "email": _text(profile.get("email")),
        "phone": _text(profile.get("phone")),
        "contact_status": contact_status,
        "contact_visibility_level": _text(profile.get("contact_visibility_level")) or "restricted",
        "confidence_score": confidence,
        "weak_label": weak_label,
        "resolution_status": "candidate",
        "evidence_count": len(rows),
        "kol_profile_rows": profile_count,
        "cooperation_rows": cooperation_count,
        "risk_rows": len(risk_rows),
        "review_reason_json": json_dumps(reasons),
        "identity_json": json_dumps(identity),
        "evidence_json": json_dumps(evidence),
    }
