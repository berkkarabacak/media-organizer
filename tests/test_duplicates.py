"""Tests for content-based duplicate detection (duplicates.py)."""

from media_organizer.core.duplicates import find_duplicates, full_hash


class TestContentHashOnly:
    def test_renamed_identical_files_are_duplicates(self, tmp_path):
        """Two files with DIFFERENT names but identical bytes -> duplicate."""
        payload = b"\x89\x50\x4e\x47" + bytes(range(256)) * 10
        a = tmp_path / "IMG_0001.jpg"
        b = tmp_path / "completely_different_name.jpg"
        a.write_bytes(payload)
        b.write_bytes(payload)
        dupes = find_duplicates([a, b])
        assert len(dupes) == 1
        assert dupes.pop() in (a, b)  # second occurrence is the duplicate

    def test_same_name_different_content_not_duplicates(self, tmp_path):
        """Same filename in different folders, different bytes -> NOT dupes."""
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        f1 = d1 / "photo.jpg"
        f2 = d2 / "photo.jpg"
        f1.write_bytes(b"A" * 5000)
        f2.write_bytes(b"B" * 5000)
        assert find_duplicates([f1, f2]) == set()

    def test_different_content_not_duplicates(self, tmp_path):
        a = tmp_path / "x.jpg"
        b = tmp_path / "y.jpg"
        a.write_bytes(b"first" * 100)
        b.write_bytes(b"secnd" * 100)
        assert find_duplicates([a, b]) == set()

    def test_three_copies_two_duplicates(self, tmp_path):
        payload = b"z" * 3000
        files = []
        for i in range(3):
            p = tmp_path / f"copy_{i}.jpg"
            p.write_bytes(payload)
            files.append(p)
        assert len(find_duplicates(files)) == 2

    def test_unreadable_file_ignored(self, tmp_path):
        a = tmp_path / "a.jpg"
        a.write_bytes(b"data" * 100)
        ghost = tmp_path / "ghost.jpg"  # does not exist
        assert find_duplicates([a, ghost]) == set()

    def test_full_hash_is_sha256(self, tmp_path):
        import hashlib
        p = tmp_path / "f.bin"
        data = b"hello world" * 1000
        p.write_bytes(data)
        assert full_hash(p) == hashlib.sha256(data).hexdigest()

    def test_cancel_stops_scan(self, tmp_path):
        a = tmp_path / "a.jpg"
        b = tmp_path / "b.jpg"
        a.write_bytes(b"same" * 100)
        b.write_bytes(b"same" * 100)
        assert find_duplicates([a, b], cancel=lambda: True) == set()
