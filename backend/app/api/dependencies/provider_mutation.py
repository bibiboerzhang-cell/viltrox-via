"""Fail-closed guard for manager-triggered global provider mutations."""
from __future__ import annotations

import functools
from typing import Any, Callable

from fastapi import HTTPException

from app.api.dependencies.manager_guard import require_manager_staff
from app.domains.audit.decorator import audit_action


def manager_provider_mutation(
    *,
    action_type: str,
    target_type: str,
    release_check: Callable[[], bool],
    target_id_extractor: Callable[[Any, dict], str] | None = None,
    detail_extractor: Callable[[Any, dict], str] | None = None,
    metadata_extractor: Callable[[Any, dict], dict] | None = None,
) -> Callable:
    """Gate manager/release state before the audit wrapper can touch storage."""

    def decorator(func: Callable) -> Callable:
        audited = audit_action(
            action_type=action_type,
            target_type=target_type,
            target_id_extractor=target_id_extractor,
            detail_extractor=detail_extractor,
            metadata_extractor=metadata_extractor,
        )(func)

        @functools.wraps(func)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            require_manager_staff(kwargs.get("staff") or {})
            if release_check():
                raise HTTPException(status_code=503, detail="release_validation_fenced")
            return audited(*args, **kwargs)

        return guarded

    return decorator


__all__ = ["manager_provider_mutation"]
