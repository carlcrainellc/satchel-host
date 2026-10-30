"""Write limits.

RateLimiter: per-author-key sliding windows (pattern from room_host parse_chat_rate, keyed by
author signing key instead of transport peer).  Keys with no hit inside the longest window are
dropped, so a flood of throwaway keys doesn't grow memory without bound.

TokenBucket: one shared budget for a whole collection, whatever key signs.  Signing keys cost
nothing to make, so the per-key limits alone don't cap what a host takes in."""
from __future__ import annotations

import collections
import threading
import time

GC_EVERY = 256  # calls between sweeps of idle keys


class RateLimiter:
    def __init__(self, rules: list[tuple[int, int]], clock=time.monotonic):
        self.rules = rules  # [(max_posts, window_seconds), ...]
        self.hits: dict[str, collections.deque] = {}
        self.clock, self.lock = clock, threading.Lock()
        self._calls = 0

    def _gc(self, now, horizon):
        for k in [k for k, q in self.hits.items() if not q or q[-1] <= now - horizon]:
            del self.hits[k]

    def allow(self, key: str) -> bool:
        if not self.rules:
            return True
        now = self.clock()
        horizon = max(w for _, w in self.rules)
        with self.lock:
            self._calls += 1
            if self._calls % GC_EVERY == 0:
                self._gc(now, horizon)
            q = self.hits.get(key)
            if q is None:
                q = self.hits[key] = collections.deque()
            while q and q[0] <= now - horizon:
                q.popleft()
            for mx, w in self.rules:
                if sum(1 for t in q if t > now - w) >= mx:
                    return False
            q.append(now)
            return True


class TokenBucket:
    """rules [(max, window_s), ...]: each is a bucket holding `max` tokens that refills at
    max/window per second.  allow() takes one token from every bucket, or none."""

    def __init__(self, rules: list[tuple[int, int]], clock=time.monotonic):
        self.rules = [(float(mx), float(w)) for mx, w in rules or [] if mx > 0 and w > 0]
        self.clock, self.lock = clock, threading.Lock()
        now = clock()
        self.level = [mx for mx, _ in self.rules]
        self.at = now

    def allow(self) -> bool:
        if not self.rules:
            return True
        with self.lock:
            now = self.clock()
            dt = max(0.0, now - self.at)
            self.at = now
            self.level = [min(mx, lv + dt * mx / w) for (mx, w), lv in zip(self.rules, self.level)]
            if any(lv < 1 for lv in self.level):
                return False
            self.level = [lv - 1 for lv in self.level]
            return True
