from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    value: Any
    expires_at: float
    created_at: float


class DeepSightCache:
    def __init__(self) -> None:
        self._data: dict[str, CacheEntry] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        now = time.time()
        if not entry:
            self.misses += 1
            return None
        if entry.expires_at <= now:
            self._data.pop(key, None)
            self.misses += 1
            return None
        self.hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl_sec: int) -> None:
        now = time.time()
        self._data[key] = CacheEntry(value=value, created_at=now, expires_at=now + ttl_sec)

    def clear(self) -> int:
        n = len(self._data)
        self._data.clear()
        return n

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        hit_rate = (self.hits / total) if total else 0.0
        return {
            "size": len(self._data),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 4),
        }


cache = DeepSightCache()
