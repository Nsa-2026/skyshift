"""Time budget and stage timings for one live build (GET /api/frames, GET /api/object).

Firebase Hosting gives up on a rewrite to Cloud Run after 60 s, so a live build must answer well
inside that. A Budget carries the deadline through the engine: downloads that have not started by
then are skipped, those still running are left to finish in the background (they fill the cache,
so the next request is faster), and the build returns what it has, marked partial. A partial
result is real data (every count in it is true) but it is not cached as the answer.

It also adds up how long each stage took (search, cutouts, zodi, gaia, known, stack, ...) and
logs one line per build, so the Cloud Run logs say where the time went.

scripts/precompute passes no budget: Budget.unlimited() never expires and records nothing new.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from .errors import UpstreamError

log = logging.getLogger("skyshift.budget")


class BudgetError(UpstreamError):
    """Nothing could be built inside the time budget: 503 upstream (contract error shape)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status = 503


class Budget:
    def __init__(self, seconds: float | None = None, name: str = "") -> None:
        self.name = name
        self.t0 = time.monotonic()
        self.at = math.inf if seconds is None else self.t0 + seconds
        self.partial: list[str] = []  # why the result is partial (empty: complete)
        self._stages: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    @classmethod
    def unlimited(cls) -> "Budget":
        return cls(None)

    @property
    def limited(self) -> bool:
        return math.isfinite(self.at)

    def remaining(self, reserve: float = 0.0) -> float:
        """Seconds left before the deadline minus `reserve` (inf without a limit, never below 0)."""
        return max(0.0, self.at - reserve - time.monotonic())

    def expired(self, reserve: float = 0.0) -> bool:
        return self.remaining(reserve) <= 0.0

    def until(self, reserve: float) -> float | None:
        """A timeout for futures.wait: remaining(reserve), or None (wait for ever) without a limit."""
        return self.remaining(reserve) if self.limited else None

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def cut(self, why: str) -> None:
        """Record that the result is partial, and why."""
        with self._lock:
            if why not in self.partial:
                self.partial.append(why)

    def stop(self, why: str) -> None:
        """End the budget now (nobody waits for the answer): no new downloads start."""
        self.cut(why)
        self.at = min(self.at, time.monotonic())

    def add(self, stage: str, seconds: float) -> None:
        with self._lock:
            self._stages[stage] = self._stages.get(stage, 0.0) + seconds

    def count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + n

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t = time.monotonic()
        try:
            yield
        finally:
            self.add(name, time.monotonic() - t)

    def summary(self) -> str:
        with self._lock:
            st = " ".join(f"{k}={v:.2f}s" for k, v in self._stages.items())
            ct = " ".join(f"{k}={v}" for k, v in sorted(self._counts.items()))
            part = ("; partial: " + ", ".join(self.partial)) if self.partial else ""
        return f"{self.name} total={self.elapsed():.2f}s {st} [{ct}]{part}"

    def log(self) -> None:
        log.warning("timing %s", self.summary())  # WARNING: Cloud Run shows it without logging setup


def get(budget: Budget | None) -> Budget:
    return budget if budget is not None else Budget.unlimited()
