"""Small in-process sliding-window rate limiter.

Intentionally dependency-free: the app runs as a handful of gunicorn workers
on one host, so a per-worker in-memory window is sufficient to blunt
credential-stuffing and signup floods (each worker enforces the limit
independently, which multiplies the effective budget by the worker count —
acceptable for a self-hosted app; a shared store would be needed for a
multi-host deployment).

Buckets are keyed by (name, ip) and evicted lazily, so memory use stays
bounded even under spoofed-address floods.
"""
import threading
import time
from collections import deque


class SlidingWindowLimiter:
    """Allow at most ``limit`` events per ``window_seconds`` per key."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int | None = None, window: float | None = None) -> bool:
        """Record an event for ``key``; return False when over the limit.

        ``limit``/``window`` override the instance defaults so callers can
        drive the thresholds from app configuration.
        """
        limit = self.limit if limit is None else limit
        window = self.window if window is None else window
        now = time.monotonic()
        with self._lock:
            hits = self._events.get(key)
            if hits is None:
                self._events[key] = hits = deque()
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            # Opportunistic cleanup so abandoned buckets do not accumulate.
            if len(self._events) > 10_000:
                cutoff = now - window
                for k in [k for k, q in self._events.items() if not q or q[-1] <= cutoff]:
                    del self._events[k]
            return True

    def reset(self, key: str) -> None:
        """Clear a key's history (e.g. after a successful login)."""
        with self._lock:
            self._events.pop(key, None)
