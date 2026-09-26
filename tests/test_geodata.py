"""Tests for the offline city list + GPS mapping (geodata.py, EXIF GPS)."""

from datetime import datetime

import pytest

from media_organizer.core.geodata import (
    CITIES, haversine_km, location_label, nearest_city,
)
from media_organizer.core.metadata import extract_gps
from tests.helpers import make_jpeg_with_gps


class TestCityList:
    def test_reasonable_size(self):
        assert len(CITIES) >= 400  # bundled offline coverage

    def test_no_duplicate_labels(self):
        labels = [c.label for c in CITIES]
        dupes = {l for l in labels if labels.count(l) > 1}
        # a few legit repeats are fine if coordinates differ
        assert len(dupes) <= 5

    def test_coordinates_in_range(self):
        for c in CITIES:
            assert -90 <= c.lat <= 90, c
            assert -180 <= c.lon <= 180, c


class TestHaversine:
    def test_zero_distance(self):
        assert haversine_km(41.0, 28.9, 41.0, 28.9) == pytest.approx(0.0)

    def test_known_distance(self):
        # Istanbul -> Ankara is roughly 350 km
        d = haversine_km(41.01, 28.98, 39.93, 32.86)
        assert 330 < d < 380


class TestNearestCity:
    def test_istanbul(self):
        assert nearest_city(41.02, 28.97).label == "Istanbul, Turkey"

    def test_near_paris_suburb(self):
        assert nearest_city(48.80, 2.40).name == "Paris"

    def test_southern_hemisphere_negative_coords(self):
        assert nearest_city(-33.9, 18.4).label == "Cape Town, South Africa"

    def test_middle_of_nowhere_returns_none(self):
        # South Pacific, far from anything
        assert nearest_city(-40.0, -140.0, max_km=200) is None

    def test_label_helper(self):
        assert location_label(35.68, 139.70) == "Tokyo, Japan"
        assert location_label(0.0, 0.0) is None  # null island -> None


class TestExifGps:
    def test_read_gps_from_real_jpeg(self, tmp_path):
        f = make_jpeg_with_gps(tmp_path / "photo.jpg",
                               datetime(2023, 6, 1, 12, 0, 0),
                               41.01, 28.98)
        coords = extract_gps(f)
        assert coords is not None
        assert coords[0] == pytest.approx(41.01, abs=0.01)
        assert coords[1] == pytest.approx(28.98, abs=0.01)

    def test_negative_hemispheres(self, tmp_path):
        f = make_jpeg_with_gps(tmp_path / "south.jpg", None, -33.92, -70.66)
        lat, lon = extract_gps(f)
        assert lat == pytest.approx(-33.92, abs=0.01)
        assert lon == pytest.approx(-70.66, abs=0.01)

    def test_jpeg_without_gps_returns_none(self, tmp_path):
        from tests.helpers import make_jpeg_with_exif
        f = make_jpeg_with_exif(tmp_path / "plain.jpg",
                                datetime(2020, 1, 1))
        assert extract_gps(f) is None

    def test_video_extension_never_reads_gps(self, tmp_path):
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00" * 64)
        assert extract_gps(f) is None

    def test_corrupt_jpeg_never_raises(self, tmp_path):
        f = tmp_path / "broken.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 37)
        assert extract_gps(f) is None

    def test_gps_jpeg_maps_to_city(self, tmp_path):
        f = make_jpeg_with_gps(tmp_path / "tokyo.jpg", None, 35.68, 139.69)
        lat, lon = extract_gps(f)
        assert location_label(lat, lon) == "Tokyo, Japan"
