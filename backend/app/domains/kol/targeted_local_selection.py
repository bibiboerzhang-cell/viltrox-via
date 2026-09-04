"""Deterministic candidate selection helpers for targeted local recall."""
from __future__ import annotations

from typing import Any

from app.domains.kol.targeted_local_support import _rank_key


def balanced_take(
    items: list[dict[str, Any]],
    *,
    target: int,
    creator_quota: int,
    reviewer_quota: int,
) -> list[dict[str, Any]]:
    creators = [item for item in items if item.get("bucket") != "reviewer"]
    reviewers = [item for item in items if item.get("bucket") == "reviewer"]
    chosen = [*creators[:creator_quota], *reviewers[:reviewer_quota]]
    chosen_ids = {id(item) for item in chosen}
    chosen.extend(item for item in items if id(item) not in chosen_ids)
    return sorted(chosen[:target], key=_rank_key, reverse=True)
