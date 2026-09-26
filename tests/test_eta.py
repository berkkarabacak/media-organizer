"""Tests for the throughput / ETA estimator (eta.py)."""

import pytest

from media_organizer.core.eta import (
    ThroughputEstimator, format_eta, format_rate,
)


class TestRate:
    def test_no_samples_zero_rate(self):
        assert ThroughputEstimator().rate() == 0.0

    def test_single_sample_zero_rate(self):
        e = ThroughputEstimator()
        e.add(100, now=0.0)
        assert e.rate() == 0.0

    def test_constant_rate(self):
        e = ThroughputEstimator(window_seconds=60)
        for t in range(11):  # 100 bytes every second for 10 s
            e.add(100 * t, now=float(t))
        assert e.rate() == pytest.approx(100.0)

    def test_old_samples_drop_out_of_window(self):
        e = ThroughputEstimator(window_seconds=5)
        e.add(0, now=0.0)          # falls out of the window
        e.add(1000, now=10.0)
        e.add(2000, now=12.0)
        assert e.rate() == pytest.approx(500.0)  # 1000 bytes / 2 s

    def test_backwards_counter_resets(self):
        e = ThroughputEstimator()
        e.add(5000, now=0.0)
        e.add(100, now=1.0)  # counter went backwards -> reset
        assert e.rate() == 0.0


class TestEta:
    def test_eta_none_without_rate(self):
        e = ThroughputEstimator()
        assert e.eta_seconds(1000) is None

    def test_eta_math(self):
        e = ThroughputEstimator(window_seconds=60)
        for t in range(11):
            e.add(100 * t, now=float(t))  # 100 B/s
        assert e.eta_seconds(5000) == pytest.approx(50.0)

    def test_eta_zero_when_nothing_left(self):
        e = ThroughputEstimator()
        e.add(1, now=0.0)
        e.add(2, now=1.0)
        assert e.eta_seconds(0) == 0.0

    def test_full_run_scenario(self):
        """Copy 100 MB at 10 MB/s -> ETA starts near 10 s and shrinks."""
        e = ThroughputEstimator(window_seconds=60)
        total = 100 * 1024 * 1024
        step = total // 10
        etas = []
        for i in range(11):
            done = step * i
            e.add(done, now=float(i))
            etas.append(e.eta_seconds(total - done, now=float(i)))
        assert etas[1] == pytest.approx(9.0)
        assert etas[-1] == 0.0
        # ETA decreases monotonically once we have a rate
        valid = [x for x in etas if x]
        assert all(a >= b for a, b in zip(valid, valid[1:]))


class TestFormatting:
    @pytest.mark.parametrize("seconds,text", [
        (None, "estimating…"),
        (0, "almost done"),
        (30, "< 1 minute"),
        (90, "about 2 minutes"),
        (60, "about 1 minute"),
        (3720, "about 1 h 2 min"),
    ])
    def test_format_eta(self, seconds, text):
        assert format_eta(seconds) == text

    @pytest.mark.parametrize("rate,text", [
        (0, "—"),
        (512, "512.0 B/s"),
        (2048, "2.0 KB/s"),
        (12.3 * 1024 * 1024, "12.3 MB/s"),
    ])
    def test_format_rate(self, rate, text):
        assert format_rate(rate) == text
