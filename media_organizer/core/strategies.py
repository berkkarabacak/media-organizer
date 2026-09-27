"""Folder-structure strategies: map capture info -> relative destination folder.

Pure logic, no Qt. Each strategy takes a CaptureDate (from metadata.py) and an
optional location label (from geodata.py) and returns a POSIX-style relative
folder path such as "2024/Q3/07 July". The caller appends the file name and
converts separators for the host OS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .metadata import CaptureDate

UNDATED_FOLDER = "_undated"
UNCERTAIN_FOLDER = "_uncertain"
UNKNOWN_LOCATION_FOLDER = "_unknown-location"

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

#: Date/place used to render the "live example" texts in the UI.
EXAMPLE_DATE = datetime(2024, 7, 15, 10, 30)
EXAMPLE_LOCATION = "Istanbul, Turkey"


def quarter_of(month: int) -> int:
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    return (month - 1) // 3 + 1


def month_folder(month: int) -> str:
    """'07 July' style folder name."""
    return f"{month:02d} {MONTH_NAMES[month - 1]}"


@dataclass(frozen=True)
class Strategy:
    key: str                       # stable identifier (stored in settings)
    name: str                      # friendly UI name
    uses_location: bool = False

    def relative_path(self, capture: CaptureDate,
                      location: Optional[str] = None) -> str:
        """Relative destination folder (forward slashes, no trailing slash)."""
        if self.uses_location:
            return _location_path(self, capture, location)
        if not capture.found:
            return UNDATED_FOLDER
        d = capture.date
        year = f"{d.year:04d}"
        if self.key == "year_only":
            parts = [year]
        elif self.key == "year_month":
            parts = [year, month_folder(d.month)]
        elif self.key == "year_quarter":
            parts = [year, f"Q{quarter_of(d.month)}"]
        elif self.key == "year_quarter_month":
            parts = [year, f"Q{quarter_of(d.month)}", month_folder(d.month)]
        elif self.key == "monthly_flat":
            parts = [f"{year}-{month_folder(d.month)}"]
        else:
            parts = [year, month_folder(d.month)]  # sane default
        return "/".join(parts)

    @property
    def example(self) -> str:
        """Live example like '2024 › Q3 › 07 July'.

        Uses '›' (U+203A) rather than '→': the arrow glyph triggered
        font-fallback scrambling inside the monospace example pills on
        some Windows machines with fractional DPI scaling.
        """
        capture = CaptureDate(EXAMPLE_DATE)
        path = self.relative_path(
            capture, EXAMPLE_LOCATION if self.uses_location else None)
        return " › ".join(path.split("/"))


def _location_path(strategy: Strategy, capture: CaptureDate,
                   location: Optional[str]) -> str:
    base = location or UNKNOWN_LOCATION_FOLDER
    if strategy.key == "location_year":
        if capture.found:
            return f"{base}/{capture.date.year:04d}"
        if location is not None:
            return f"{base}/{UNDATED_FOLDER}"
    return base


STRATEGIES: tuple[Strategy, ...] = (
    Strategy("year_only", "Year only"),
    Strategy("year_month", "Year → Month"),
    Strategy("year_quarter", "Year → Quarter"),
    Strategy("year_quarter_month", "Year → Quarter → Month (nested)"),
    Strategy("monthly_flat", "Monthly, single level (flat)"),
    Strategy("location", "By location (GPS)", uses_location=True),
    Strategy("location_year", "Location → Year", uses_location=True),
)

DEFAULT_STRATEGY_KEY = "year_month"

_BY_KEY = {s.key: s for s in STRATEGIES}


def get_strategy(key: str) -> Strategy:
    """Look up a strategy; unknown keys fall back to the default."""
    return _BY_KEY.get(key, _BY_KEY[DEFAULT_STRATEGY_KEY])


def relative_folder(strategy_key: str, capture: CaptureDate,
                    location: Optional[str] = None) -> str:
    """Convenience one-shot: strategy + capture info -> relative folder."""
    return get_strategy(strategy_key).relative_path(capture, location)
