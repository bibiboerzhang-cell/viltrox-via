"""Default comments-domain adapter for the collection command port."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal

from app.domains.comments import collector


class CollectorCommentCollectionCommand:
    def enqueue(
        self,
        kol_pool_id: int,
        *,
        staff: dict[str, Any] | None,
        queue_lane: Literal["batch"],
    ) -> Mapping[str, Any]:
        return collector.enqueue_kol_pool_comments_job(
            kol_pool_id,
            staff=staff,
            queue_lane=queue_lane,
        )


DEFAULT_COMMENT_COLLECTION_COMMAND: Final = CollectorCommentCollectionCommand()


__all__ = [
    "CollectorCommentCollectionCommand",
    "DEFAULT_COMMENT_COLLECTION_COMMAND",
]
