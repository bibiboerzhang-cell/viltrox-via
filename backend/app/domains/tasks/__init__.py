"""Async task domain facade."""

from app.domains.tasks import enqueue

__all__ = ["enqueue"]
