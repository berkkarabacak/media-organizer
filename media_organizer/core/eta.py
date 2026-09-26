"""Rolling throughput estimator with ETA for long file operations.

Pure logic, no Qt. Feed it progress samples (bytes completed so far, at a
timestamp) and it produces a smoothed bytes/sec rate plus a remaining-time
estimate. Timestamps are injectable for tests.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional, Tuple


class ThroughputEstimator:
    """Sliding-window throughput estimator.

    Samples older than `window_seconds` are dropped, so the rate reflects the
    recent pace instead of the whole-run average.
    """

    def __init__(self, window_seconds: float = 10.0):
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window = window_seconds
        self._samples: Deque[Tuple[float, float]] = deque()  # (ts, bytes_done)

    def add(self, bytes_done: float, now: Optional[float] = None) -> None:
        ts = time.monotonic() if now is None else now
        if self._samples and bytes_done < self._samples[-1][1]:
            self.reset(now=ts)  # counter went backwards (new run)
        self._samples.append((ts, bytes_done))
        cutoff = ts - self.window
        while len(self._samples) > 1 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def reset(self, now: Optional[float] = None) -> None:
        self._samples.clear()

    def rate(self, now: Optional[float] = None) -> float:
        """Current throughput in bytes/sec (0.0 when not enough data)."""
        if len(self._samples) < 2:
            return 0.0
        t0, b0 = self._samples[0]
        t1, b1 = self._samples[-1]
        span = t1 - t0
        if span <= 0:
            return 0.0
        return (b1 - b0) / span

    def eta_seconds(self, remaining_bytes: float,
                    now: Optional[float] = None) -> Optional[float]:
        """Estimated seconds remaining, or None when no estimate is possible."""
        if remaining_bytes <= 0:
            return 0.0
        r = self.rate(now)
        if r <= 0:
            return None
        return remaining_bytes / r


def format_eta(seconds: Optional[float]) -> str:
    """Human-friendly remaining time: 'about 2 minutes', '< 1 minute'."""
    if seconds is None:
        return "estimating…"
    if seconds <= 0:
        return "almost done"
    if seconds < 60:
        return "< 1 minute"
    minutes = seconds / 60
    if minutes < 60:
        return f"about {max(1, round(minutes))} minute" + (
            "s" if round(minutes) != 1 else "")
    hours = int(minutes // 60)
    mins = round(minutes - hours * 60)
    return f"about {hours} h {mins} min"


def format_rate(bytes_per_sec: float) -> str:
    """'12.3 MB/s' style throughput."""
    if bytes_per_sec <= 0:
        return "—"
    n = float(bytes_per_sec)
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if n < 1024 or unit == "GB/s":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB/s"
