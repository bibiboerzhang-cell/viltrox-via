"""Worker task entrypoint for KOL content scoring."""
from __future__ import annotations

from app.services.kol.content_scorer import score_kol_content


async def process_score_kol_content(payload: dict) -> dict:
    return await score_kol_content(int(payload.get("content_id") or 0))
