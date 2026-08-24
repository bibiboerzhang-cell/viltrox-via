"""Short-lived process cache for immutable global Pool read selections."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


_TTL_SECONDS = 30.0
_LOCK = threading.Lock()
_ENTRY: tuple[float, Any, Any] | None = None


def cached_global_pool_selection(
    *,
    enabled: bool,
    builder: Callable[[], Any],
    cache_key: Any = None,
) -> Any:
    global _ENTRY
    if not enabled:
        return builder()
    now = time.monotonic()
    with _LOCK:
        if (
            _ENTRY is not None
            and _ENTRY[1] == cache_key
            and now - _ENTRY[0] < _TTL_SECONDS
        ):
            return _ENTRY[2]
        selection = builder()
        _ENTRY = (time.monotonic(), cache_key, selection)
        return selection


def clear_pool_read_selection_cache() -> None:
    global _ENTRY
    with _LOCK:
        _ENTRY = None
