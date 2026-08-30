"""Typed command boundary for durable comment-collection enqueueing."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol


class CommentCollectionCommand(Protocol):
    def enqueue(
        self,
        kol_pool_id: int,
        *,
        staff: dict[str, Any] | None,
        queue_lane: Literal["batch"],
    ) -> Mapping[str, Any]: ...


__all__ = ["CommentCollectionCommand"]
