"""Shared SQL eligibility rules for KOL video-analysis evidence."""
from __future__ import annotations

from typing import Any


VIDEO_MEDIA_KINDS = ("video", "reel", "clip", "igtv")


def eligible_video_evidence_sql(conn: Any, *, alias: str = "e") -> str:
    """Return the enqueue predicate while tolerating legacy missing media_kind."""
    try:
        columns = {
            str(dict(row).get("name") or "")
            for row in conn.execute("PRAGMA table_info(vkpi_kol_video_evidence)").fetchall()
        }
    except Exception:
        columns = set()
    media_expr = f"{alias}.media_kind" if "media_kind" in columns else "NULL"
    allowed = ", ".join(f"'{kind}'" for kind in VIDEO_MEDIA_KINDS)
    return f"""(
        (TRIM(COALESCE({alias}.evidence_type, '')) = ''
         OR LOWER(TRIM({alias}.evidence_type)) = 'video')
        AND (TRIM(COALESCE({media_expr}, '')) = ''
             OR LOWER(TRIM({media_expr})) IN ({allowed}))
    )"""
