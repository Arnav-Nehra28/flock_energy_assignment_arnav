"""In-memory cache with TTL support.

Simple time-based cache — suitable for a single-process deployment.
For multi-process or distributed setups, swap for Redis.
"""

from __future__ import annotations

import time
from typing import Any, Optional

_MISSING = object()


class TTLCache:
    """Lightweight in-memory key→value cache with per-entry TTL."""

    def __init__(self, default_ttl: int = 300) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._default_ttl = default_ttl

    def get(self, key: str, default: Any = None) -> Any:
        """Return cached value if present and not expired, else default."""
        entry = self._store.get(key)
        if entry is None:
            return default
        expires_at, value = entry
        if time.time() > expires_at:
            del self._store[key]
            return default
        return value

    def has(self, key: str) -> bool:
        """Check if a non-expired entry exists for key."""
        val = self.get(key, _MISSING)
        return val is not _MISSING

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value with the given TTL (seconds).

        Respects ttl=0 (expires immediately) and uses default_ttl only if ttl is None.
        """
        effective_ttl = self._default_ttl if ttl is None else ttl
        self._store[key] = (time.time() + effective_ttl, value)

    def invalidate(self, prefix: str = "") -> int:
        """Remove entries whose key starts with *prefix*. Returns count removed."""
        if not prefix:
            count = len(self._store)
            self._store.clear()
            return count
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            del self._store[k]
        return len(keys)

    @property
    def size(self) -> int:
        return len(self._store)


# Module-level singleton
cache = TTLCache()
