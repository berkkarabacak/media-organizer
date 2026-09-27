"""Tests for folder-structure strategies (strategies.py)."""

from datetime import datetime

import pytest

from media_organizer.core.metadata import CaptureDate
from media_organizer.core.strategies import (
    STRATEGIES, UNDATED_FOLDER, UNKNOWN_LOCATION_FOLDER,
    get_strategy, month_folder, quarter_of, relative_folder,
)

JULY = CaptureDate(datetime(2024, 7, 15, 10, 30))
JAN = CaptureDate(datetime(2019, 1, 2))
NODATE = CaptureDate(None)


class TestQuarterMath:
    @pytest.mark.parametrize("month,q", [
        (1, 1), (2, 1), (3, 1),
        (4, 2), (5, 2), (6, 2),
        (7, 3), (8, 3), (9, 3),
        (10, 4), (11, 4), (12, 4),
    ])
    def test_quarters(self, month, q):
        assert quarter_of(month) == q

    @pytest.mark.parametrize("bad", [0, 13, -1])
    def test_out_of_range(self, bad):
        with pytest.raises(ValueError):
            quarter_of(bad)


def test_month_folder():
    assert month_folder(7) == "07 July"
    assert month_folder(12) == "12 December"


class TestDateStrategies:
    @pytest.mark.parametrize("key,expected", [
        ("year_only", "2024"),
        ("year_month", "2024/07 July"),
        ("year_quarter", "2024/Q3"),
        ("year_quarter_month", "2024/Q3/07 July"),
        ("monthly_flat", "2024-07 July"),
    ])
    def test_relative_path(self, key, expected):
        assert relative_folder(key, JULY) == expected

    def test_flat_monthly_is_single_level(self):
        assert "/" not in relative_folder("monthly_flat", JAN)
        assert relative_folder("monthly_flat", JAN) == "2019-01 January"

    def test_january_is_q1(self):
        assert relative_folder("year_quarter", JAN) == "2019/Q1"

    @pytest.mark.parametrize("key", ["year_only", "year_month", "year_quarter",
                                     "year_quarter_month", "monthly_flat"])
    def test_undated_goes_to_undated(self, key):
        assert relative_folder(key, NODATE) == UNDATED_FOLDER


class TestLocationStrategies:
    def test_location(self):
        assert relative_folder("location", JULY, "Istanbul, Turkey") == \
            "Istanbul, Turkey"

    def test_location_year(self):
        assert relative_folder("location_year", JULY, "Istanbul, Turkey") == \
            "Istanbul, Turkey/2024"

    def test_no_gps_falls_back_to_unknown(self):
        assert relative_folder("location", JULY, None) == UNKNOWN_LOCATION_FOLDER

    def test_location_year_without_gps(self):
        assert relative_folder("location_year", JULY, None) == \
            f"{UNKNOWN_LOCATION_FOLDER}/2024"

    def test_location_year_with_place_but_no_date(self):
        assert relative_folder("location_year", NODATE, "Paris, France") == \
            f"Paris, France/{UNDATED_FOLDER}"


class TestStrategyRegistry:
    def test_default_is_year_month(self):
        assert get_strategy("does-not-exist").key == "year_month"

    def test_examples_are_concrete(self):
        examples = {s.key: s.example for s in STRATEGIES}
        assert examples["year_month"] == "2024 › 07 July"
        assert examples["year_quarter_month"] == "2024 › Q3 › 07 July"
        assert examples["monthly_flat"] == "2024-07 July"
        assert examples["location"] == "Istanbul, Turkey"
        assert examples["location_year"] == "Istanbul, Turkey › 2024"

    def test_examples_have_no_arrow_glyph(self):
        # '→' triggered font-fallback scrambling in the example pills
        for s in STRATEGIES:
            assert "→" not in s.example

    def test_all_keys_unique(self):
        keys = [s.key for s in STRATEGIES]
        assert len(keys) == len(set(keys)) == 7
