"""
Disk-based response cache for GitHub API calls.

Stores JSON responses in ~/.cache/gh-analyzer/ keyed by a hash of the
URL + sorted query parameters. Entries expire after TTL_SECONDS (default
5 minutes) so repeated runs within a session avoid redundant API calls.

Usage:
    cache = ResponseCache()
    data = cache.get(url, params)
    if data is None:
        data = await fetch(...)
        cache.set(url, params, data)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("gh_analyzer")

# Default TTL: 5 minutes — short enough to stay fresh, long enough to
# avoid hammering the API on rapid repeated invocations.
TTL_SECONDS: int = 300

# Cache directory under the user's home folder.
CACHE_DIR: Path = Path.home() / ".cache" / "gh-analyzer"


def _cache_key(url: str, params: dict | None) -> str:
    """Stable SHA-256 key from URL + sorted params."""
    canonical = url
    if params:
        sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        canonical = f"{url}?{sorted_params}"
    return hashlib.sha256(canonical.encode()).hexdigest()


class ResponseCache:
    """
    Simple file-system cache. Each entry is a JSON file containing the
    response payload and an expiry timestamp.

    Thread-safety: individual file writes are atomic via rename on POSIX.
    Concurrent processes may occasionally re-fetch the same resource, but
    they will never read a half-written cache file.
    """

    def __init__(self, cache_dir: Path = CACHE_DIR, ttl: int = TTL_SECONDS) -> None:
        self._dir = cache_dir
        self._ttl = ttl
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get(self, url: str, params: dict | None = None) -> Any | None:
        """
        Return the cached response for (url, params), or None if the entry
        is absent or has expired.
        """
        key  = _cache_key(url, params)
        path = self._path(key)

        if not path.exists():
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if time.time() > raw["expires_at"]:
                path.unlink(missing_ok=True)
                return None
            return raw["data"]
        except Exception as exc:
            # Corrupt or unreadable cache entry — treat as a miss.
            logger.debug("Cache read error for %s: %s", url, exc)
            return None

    def set(self, url: str, params: dict | None, data: Any) -> None:
        """
        Persist response data to disk with an expiry timestamp.
        Uses atomic rename so readers never see a partial write.
        """
        key     = _cache_key(url, params)
        path    = self._path(key)
        tmp     = path.with_suffix(".tmp")
        payload = {
            "url":        url,
            "expires_at": time.time() + self._ttl,
            "data":       data,
        }

        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(path)   # atomic on POSIX; best-effort on Windows
        except Exception as exc:
            logger.debug("Cache write error for %s: %s", url, exc)
            tmp.unlink(missing_ok=True)

    def delete(self, url: str, params: dict | None = None) -> None:
        """Remove a single cache entry."""
        key  = _cache_key(url, params)
        path = self._path(key)
        path.unlink(missing_ok=True)

    def clear(self) -> int:
        """Delete all cache entries. Returns the number of files removed."""
        removed = 0
        for f in self._dir.glob("*.json"):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def evict_expired(self) -> int:
        """Remove expired entries. Returns the number of files removed."""
        removed = 0
        now = time.time()
        for f in self._dir.glob("*.json"):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                if now > raw.get("expires_at", 0):
                    f.unlink()
                    removed += 1
            except Exception:
                pass
        return removed
