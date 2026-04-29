"""
db/repositories/assets.py — submission_assets persistence helpers
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from app.db.connection import get_conn, is_postgres_runtime
from app.core.logging import get_logger
from app.services.monitoring.runtime import record_background_metric


logger = get_logger(__name__)


def _active_asset_filter(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"COALESCE(CAST({prefix}deleted_at AS TEXT), '') = ''"


def register_submission_asset(
    *,
    submission_id: int,
    asset_role: str,
    storage_key: str,
    mime_type: str = "",
    size_bytes: int = 0,
    duration_ms: int = 0,
    width: int = 0,
    height: int = 0,
    checksum: str = "",
    replace_existing: bool = False,
) -> int:
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    if replace_existing:
        conn.execute(
            "DELETE FROM submission_assets WHERE submission_id=? AND asset_role=?",
            (int(submission_id), asset_role),
        )

    params = (
        int(submission_id),
        asset_role,
        storage_key,
        mime_type,
        int(size_bytes or 0),
        int(duration_ms or 0),
        int(width or 0),
        int(height or 0),
        checksum,
        now,
    )
    sql = """
        INSERT INTO submission_assets (
            submission_id, asset_role, storage_key, mime_type,
            size_bytes, duration_ms, width, height, checksum, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        asset_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        asset_id = int(cur.lastrowid)
    conn.commit()
    return asset_id


def save_asset_fingerprints(
    asset_id: int,
    fingerprints: Sequence[dict],
    *,
    replace_existing: bool = True,
) -> int:
    if not asset_id or not fingerprints:
        return 0
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    if replace_existing:
        conn.execute("DELETE FROM asset_fingerprints WHERE asset_id=?", (int(asset_id),))
    inserted = 0
    for fp in fingerprints:
        value = str((fp or {}).get("fingerprint_value") or "").strip().lower()
        if not value:
            continue
        conn.execute(
            """
            INSERT INTO asset_fingerprints (
                asset_id, fingerprint_type, frame_slot, frame_index, fingerprint_value, created_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                int(asset_id),
                str((fp or {}).get("fingerprint_type") or "phash").strip().lower(),
                str((fp or {}).get("frame_slot") or "").strip().lower(),
                int((fp or {}).get("frame_index") or 0),
                value,
                now,
            ),
        )
        inserted += 1
    conn.commit()
    return inserted


def _hex_hamming_distance(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except Exception:
        return 9999


def find_duplicate_submission_asset(
    *,
    checksum: str = "",
    frame_hashes: Sequence[str] = (),
    max_phash_distance: int = 6,
    min_phash_matches: int = 2,
) -> dict | None:
    conn = get_conn()
    checksum = str(checksum or "").strip().lower()
    if checksum:
        row = conn.execute(
            """
            SELECT a.id AS asset_id, a.submission_id, a.asset_role, a.storage_key, a.checksum
            FROM submission_assets a
            WHERE LOWER(COALESCE(a.checksum, '')) = ?
              AND NOT (a.submission_id = 0 AND a.asset_role = 'uploaded_video_pending')
              AND COALESCE(CAST(a.deleted_at AS TEXT), '') = ''
            ORDER BY CASE WHEN a.submission_id > 0 THEN 0 ELSE 1 END, a.id DESC
            LIMIT 1
            """,
            (checksum,),
        ).fetchone()
        if row:
            return {
                "duplicate": True,
                "match_type": "checksum",
                "matched_asset_id": int(row["asset_id"]),
                "matched_submission_id": int(row["submission_id"] or 0),
                "matched_storage_key": row["storage_key"],
                "reason": "Exact same file hash already exists",
            }

    normalized_hashes = [str(value or "").strip().lower() for value in (frame_hashes or []) if str(value or "").strip()]
    if not normalized_hashes:
        return None

    rows = conn.execute(
        """
        SELECT f.asset_id, f.frame_slot, f.frame_index, f.fingerprint_value,
               a.submission_id, a.asset_role, a.storage_key
        FROM asset_fingerprints f
        JOIN submission_assets a ON a.id = f.asset_id
        WHERE f.fingerprint_type = 'phash'
          AND a.asset_role IN ('uploaded_video', 'uploaded_video_pending', 'url_video_archive')
          AND NOT (a.submission_id = 0 AND a.asset_role = 'uploaded_video_pending')
          AND COALESCE(CAST(a.deleted_at AS TEXT), '') = ''
        ORDER BY f.asset_id DESC, f.id DESC
        """
    ).fetchall()
    if not rows:
        return None

    matches: dict[int, dict] = {}
    for row in rows:
        existing_hash = str(row["fingerprint_value"] or "").strip().lower()
        if not existing_hash:
            continue
        best_distance = min((_hex_hamming_distance(existing_hash, incoming) for incoming in normalized_hashes), default=9999)
        if best_distance > max_phash_distance:
            continue
        asset_id = int(row["asset_id"])
        entry = matches.setdefault(
            asset_id,
            {
                "matched_asset_id": asset_id,
                "matched_submission_id": int(row["submission_id"] or 0),
                "matched_storage_key": row["storage_key"],
                "matched_frame_count": 0,
                "min_distance": best_distance,
            },
        )
        entry["matched_frame_count"] += 1
        entry["min_distance"] = min(int(entry["min_distance"]), best_distance)

    if not matches:
        return None

    best = sorted(
        matches.values(),
        key=lambda item: (-int(item["matched_frame_count"]), int(item["min_distance"]), -int(item["matched_asset_id"])),
    )[0]
    if int(best["matched_frame_count"]) < max(1, int(min_phash_matches or 1)):
        return None

    best["duplicate"] = True
    best["match_type"] = "phash"
    best["reason"] = (
        f"Frame pHash matched {best['matched_frame_count']} frame(s), "
        f"min distance={best['min_distance']}"
    )
    return best


def _asset_role_order(role: str) -> int:
    if role == "uploaded_video":
        return 0
    if role == "url_video_archive":
        return 1
    return 9


def _basename_candidates(*values: str) -> set[str]:
    basenames: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        basename = Path(text).name.strip()
        if basename:
            basenames.add(basename)
    return basenames


def get_submission_asset(
    submission_id: int,
    asset_roles: Sequence[str] = ("uploaded_video", "url_video_archive"),
):
    roles = [str(role or "").strip() for role in (asset_roles or []) if str(role or "").strip()]
    if not submission_id or not roles:
        return None
    conn = get_conn()
    placeholders = ",".join("?" for _ in roles)
    rows = conn.execute(
        f"""
        SELECT *
        FROM submission_assets
        WHERE submission_id=? AND asset_role IN ({placeholders})
          AND {_active_asset_filter()}
        ORDER BY id DESC
        """,
        (int(submission_id), *roles),
    ).fetchall()
    if not rows:
        return None
    ordered = sorted((dict(row) for row in rows), key=lambda row: (_asset_role_order(row.get("asset_role", "")), -int(row.get("id", 0))))
    return ordered[0]


def attach_uploaded_asset_to_submission(
    *,
    submission_id: int,
    asset_id: int = 0,
    r2_key: str = "",
    local_path: str = "",
) -> dict | None:
    if not submission_id:
        return None

    conn = get_conn()
    if asset_id:
        row = conn.execute(
            f"""
            SELECT *
            FROM submission_assets
            WHERE id=? AND asset_role IN ('uploaded_video', 'uploaded_video_pending')
              AND {_active_asset_filter()}
            """,
            (int(asset_id),),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE submission_assets SET submission_id=?, asset_role='uploaded_video', deleted_at=NULL, deleted_reason='' WHERE id=?",
                (int(submission_id), int(asset_id)),
            )
            conn.commit()
            asset = dict(row)
            asset["submission_id"] = int(submission_id)
            asset["asset_role"] = "uploaded_video"
            return asset

    exact_candidates = {str(value or "").strip() for value in (r2_key, local_path) if str(value or "").strip()}
    basename_candidates = _basename_candidates(r2_key, local_path)
    if not exact_candidates and not basename_candidates:
        return None

    rows = conn.execute(
        f"""
        SELECT *
        FROM submission_assets
        WHERE submission_id IN (0, ?)
          AND asset_role IN ('uploaded_video', 'uploaded_video_pending')
          AND {_active_asset_filter()}
        ORDER BY id DESC
        """,
        (int(submission_id),),
    ).fetchall()

    best_match: dict | None = None
    best_score = -1
    for raw_row in rows:
        row = dict(raw_row)
        storage_key = str(row.get("storage_key") or "").strip()
        if not storage_key:
            continue
        score = -1
        if storage_key in exact_candidates:
            score = 30
        elif Path(storage_key).name in basename_candidates:
            score = 20
        elif any(storage_key.endswith(f"/{basename}") for basename in basename_candidates):
            score = 15
        if score > best_score:
            best_match = row
            best_score = score

    if not best_match:
        return None

    conn.execute(
        "UPDATE submission_assets SET submission_id=?, asset_role='uploaded_video', deleted_at=NULL, deleted_reason='' WHERE id=?",
        (int(submission_id), int(best_match["id"])),
    )
    conn.commit()
    best_match["submission_id"] = int(submission_id)
    best_match["asset_role"] = "uploaded_video"
    return best_match


def cleanup_stale_pending_assets(max_age_minutes: int = 30) -> dict[str, int | str]:
    age_minutes = max(1, int(max_age_minutes or 30))
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(minutes=age_minutes)
    cutoff_value = cutoff if is_postgres_runtime() else cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    deleted_at = now_utc if is_postgres_runtime() else now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    reason = f"pending_unbound_older_than_{age_minutes}m"

    conn = get_conn()
    cur = conn.execute(
        """
        UPDATE submission_assets
        SET deleted_at=?, deleted_reason=?
        WHERE submission_id=0
          AND asset_role='uploaded_video_pending'
          AND COALESCE(CAST(deleted_at AS TEXT), '')=''
          AND created_at < ?
        """,
        (deleted_at, reason, cutoff_value),
    )
    deleted = max(0, int(getattr(cur, "rowcount", 0) or 0))
    conn.commit()

    result: dict[str, int | str] = {
        "deleted": deleted,
        "max_age_minutes": age_minutes,
        "reason": reason,
    }
    record_background_metric(
        "pending_asset_cleanup.deleted",
        deleted,
        ok=True,
        max_age_minutes=age_minutes,
    )
    if deleted > 0:
        logger.info("media.pending_assets.cleanup_complete", extra=result)
    return result


def set_submission_video_r2_key(submission_id: int, r2_key: str, force: bool = False) -> bool:
    if not submission_id or not r2_key:
        return False
    conn = get_conn()
    row = conn.execute("SELECT video_analysis FROM submissions WHERE id=?", (int(submission_id),)).fetchone()
    if not row:
        return False

    video_analysis_raw = row["video_analysis"] if row.keys() else row[0]
    try:
        video_analysis = json.loads(video_analysis_raw or "{}")
    except Exception:
        video_analysis = {}
    if not isinstance(video_analysis, dict):
        video_analysis = {}

    if video_analysis.get("r2_key") and not force:
        return False

    video_analysis["r2_key"] = r2_key
    conn.execute(
        "UPDATE submissions SET video_analysis=? WHERE id=?",
        (json.dumps(video_analysis, ensure_ascii=False), int(submission_id)),
    )
    conn.commit()
    return True
