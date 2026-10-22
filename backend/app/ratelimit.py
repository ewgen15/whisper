import time
from collections import deque


class RateLimiter:
    """In-memory sliding-window limiter. One process, so no shared store is needed."""

    def __init__(self, limit: int, window: float = 60, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, deque] = {}

    def reset(self):
        self._hits.clear()

    def hit(self, key: str) -> tuple[bool, int]:
        """Record a request. Returns (allowed, retry_after_seconds)."""
        now = self.clock()
        for k in [k for k, q in self._hits.items() if q and now - q[-1] >= self.window]:
            del self._hits[k]  # idle keys, keeps memory bounded
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False, max(1, int(self.window - (now - q[0]) + 0.999))
        q.append(now)
        return True, 0
