"""Shared status and item-shape constants for KOL search sessions."""
from __future__ import annotations


SESSION_QUERY_TYPES = {"url_video", "url_profile", "text_recall", "unknown"}
SESSION_STATUSES = {"planned", "running", "ready", "partial", "failed", "cancelled"}
ITEM_STATUSES = {
    "planned",
    "identified",
    "matched",
    "queued",
    "running",
    "ready",
    "partial",
    "failed",
    "skipped",
    "already_queued",
    "already_analyzed",
    "unknown",
}

TERMINAL_SESSION_STATUSES = {"ready", "partial", "failed", "cancelled"}
PENDING_ENRICHMENT_STATUSES = {
    "pending",
    "queued",
    "already_queued",
    "running",
    "waiting_for_evidence",
    "waiting_for_profile",
}
REACH_GATED_ITEM_TYPES = {"new_creator", "existing_kol", "recall_candidate"}
