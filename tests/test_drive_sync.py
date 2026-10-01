"""Local Drive sync record: load, save, and skip-vs-upload. No network."""

import ast
import hashlib
import json
import socket
from pathlib import Path

import pytest

from media_organizer.core.drive_sync import (
    ADDITIVE_MODE,
    DECISION_SKIP,
    DECISION_UPLOAD,
    SyncEntry,
    SyncRecord,
    SyncRecordError,
    load_sync_record,
    normalize_local_path,
    relative_local_path,
    save_sync_record,
    sync_path_for,
)
from media_organizer.core.duplicates import full_hash


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _finished(library: Path, path: str, data: bytes, file_id: str,
              folder: str = "folder-1") -> SyncRecord:
    record = load_sync_record(library)
    record.bind_folder(folder)
    record.mark_uploaded(path, len(data), _digest(data), file_id)
    save_sync_record(record, library)
    return record


class TestPaths:
    def test_backslash_and_dot_segments_match(self):
        assert normalize_local_path("2024\\07 July\\IMG_1.jpg") == "2024/07 July/IMG_1.jpg"
        assert normalize_local_path("./2024/./07 July/IMG_1.jpg") == "2024/07 July/IMG_1.jpg"

    def test_parent_and_absolute_paths_rejected(self):
        with pytest.raises(ValueError):
            normalize_local_path("../secret.jpg")
        with pytest.raises(ValueError):
            normalize_local_path("/tmp/library/a.jpg")
        with pytest.raises(ValueError):
            normalize_local_path("C:/Users/a.jpg")

    def test_relative_to_library(self, tmp_path):
        library = tmp_path / "library"
        photo = library / "2024" / "07 July" / "IMG_1.jpg"
        photo.parent.mkdir(parents=True)
        photo.write_bytes(b"x")
        assert relative_local_path(library, photo) == "2024/07 July/IMG_1.jpg"

    def test_file_outside_library_rejected(self, tmp_path):
        library = tmp_path / "library"
        library.mkdir()
        outside = tmp_path / "other.jpg"
        outside.write_bytes(b"x")
        with pytest.raises(ValueError):
            relative_local_path(library, outside)


class TestDecide:
    def test_missing_record_uploads_everything(self, tmp_path):
        record = load_sync_record(tmp_path)
        assert record.entries == {}
        assert record.decide("2024/a.jpg", 4, _digest(b"data")) == DECISION_UPLOAD

    def test_unchanged_file_is_skipped(self, tmp_path):
        data = b"same-bytes"
        _finished(tmp_path, "2024/Q3/07 July/IMG_1.jpg", data, "drive-1")
        record = load_sync_record(tmp_path)
        decision = record.decide("2024/Q3/07 July/IMG_1.jpg", len(data), _digest(data))
        assert decision == DECISION_SKIP

    def test_new_path_is_uploaded(self, tmp_path):
        data = b"same-bytes"
        _finished(tmp_path, "2024/a.jpg", data, "drive-1")
        record = load_sync_record(tmp_path)
        assert record.decide("2024/b.jpg", len(data), _digest(data)) == DECISION_UPLOAD

    def test_size_change_is_uploaded_even_if_hash_matches(self, tmp_path):
        data = b"same-bytes"
        _finished(tmp_path, "2024/a.jpg", data, "drive-1")
        record = load_sync_record(tmp_path)
        assert record.decide("2024/a.jpg", len(data) + 1, _digest(data)) == DECISION_UPLOAD

    def test_hash_change_at_the_same_size_is_uploaded(self, tmp_path):
        original = b"aaaa"
        changed = b"bbbb"
        assert len(original) == len(changed)
        _finished(tmp_path, "2024/a.jpg", original, "drive-1")
        record = load_sync_record(tmp_path)
        assert record.decide("2024/a.jpg", len(changed), _digest(changed)) == DECISION_UPLOAD

    def test_zero_byte_file_can_be_skipped(self, tmp_path):
        _finished(tmp_path, "_undated/empty.jpg", b"", "drive-empty")
        record = load_sync_record(tmp_path)
        assert record.decide("_undated/empty.jpg", 0, _digest(b"")) == DECISION_SKIP

    def test_hash_comparison_is_case_insensitive(self, tmp_path):
        data = b"Photo"
        _finished(tmp_path, "2024/a.jpg", data, "drive-1")
        record = load_sync_record(tmp_path)
        assert record.decide("2024/a.jpg", len(data), _digest(data).upper()) == DECISION_SKIP

    def test_path_match_ignores_slash_style(self, tmp_path):
        data = b"Photo"
        _finished(tmp_path, "2024/07 July/IMG_1.jpg", data, "drive-1")
        record = load_sync_record(tmp_path)
        decision = record.decide("2024\\07 July\\IMG_1.jpg", len(data), _digest(data))
        assert decision == DECISION_SKIP

    def test_interrupted_entry_without_file_id_is_uploaded(self, tmp_path):
        data = b"partial"
        digest = _digest(data)
        path = sync_path_for(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "version": 1,
            "mode": ADDITIVE_MODE,
            "drive_folder_id": "folder-1",
            "entries": [{
                "local_path": "2024/a.jpg",
                "size": len(data),
                "content_hash": digest,
                "drive_file_id": "",
            }],
        }), encoding="utf-8")
        record = load_sync_record(tmp_path)
        assert record.decide("2024/a.jpg", len(data), digest) == DECISION_UPLOAD

    def test_changed_file_keeps_old_id_until_the_new_upload_is_recorded(self, tmp_path):
        original = b"v1"
        _finished(tmp_path, "2024/a.jpg", original, "drive-1")
        record = load_sync_record(tmp_path)
        changed = b"v2!"
        assert record.decide("2024/a.jpg", len(changed), _digest(changed)) == DECISION_UPLOAD
        assert record.entry_for("2024/a.jpg").drive_file_id == "drive-1"
        record.mark_uploaded("2024/a.jpg", len(changed), _digest(changed), "drive-1")
        assert record.decide("2024/a.jpg", len(changed), _digest(changed)) == DECISION_SKIP

    def test_content_hash_matches_the_duplicate_hasher(self, tmp_path):
        payload = b"photo-bytes"
        photo = tmp_path / "IMG_1.jpg"
        photo.write_bytes(payload)
        assert full_hash(photo) == _digest(payload)

    def test_only_skip_or_upload(self, tmp_path):
        record = SyncRecord()
        assert record.decide("2024/a.jpg", 1, _digest(b"a")) == DECISION_UPLOAD
        record.bind_folder("folder-1")
        record.mark_uploaded("2024/a.jpg", 1, _digest(b"a"), "drive-1")
        assert record.decide("2024/a.jpg", 1, _digest(b"a")) == DECISION_SKIP


class TestAdditive:
    def test_removed_local_file_stays_in_the_record(self, tmp_path):
        _finished(tmp_path, "2024/keep.jpg", b"keep", "drive-keep")
        _finished(tmp_path, "2024/gone.jpg", b"gone", "drive-gone")
        record = load_sync_record(tmp_path)
        # The pass only asks about files still on disk. The missing path
        # stays recorded, and nothing in the record asks for a remote delete.
        assert record.decide("2024/keep.jpg", 4, _digest(b"keep")) == DECISION_SKIP
        assert record.entry_for("2024/gone.jpg").drive_file_id == "drive-gone"
        saved = record.to_dict()
        assert saved["mode"] == ADDITIVE_MODE
        assert "delete" not in saved

    def test_switching_folder_drops_ids_and_does_not_emit_deletes(self, tmp_path):
        _finished(tmp_path, "2024/a.jpg", b"abc", "drive-1", folder="folder-a")
        record = load_sync_record(tmp_path)
        record.bind_folder("folder-b")
        assert record.drive_folder_id == "folder-b"
        assert record.entries == {}
        assert record.decide("2024/a.jpg", 3, _digest(b"abc")) == DECISION_UPLOAD
        assert record.to_dict()["mode"] == ADDITIVE_MODE

    def test_rebinding_the_same_folder_keeps_entries(self, tmp_path):
        _finished(tmp_path, "2024/a.jpg", b"abc", "drive-1")
        record = load_sync_record(tmp_path)
        record.bind_folder("folder-1")
        assert record.decide("2024/a.jpg", 3, _digest(b"abc")) == DECISION_SKIP

    def test_failed_rebind_leaves_the_record_alone(self, tmp_path):
        _finished(tmp_path, "2024/a.jpg", b"abc", "drive-1")
        record = load_sync_record(tmp_path)
        with pytest.raises(ValueError):
            record.bind_folder("  ")
        assert record.drive_folder_id == "folder-1"
        assert record.decide("2024/a.jpg", 3, _digest(b"abc")) == DECISION_SKIP


class TestRoundTrip:
    def test_save_and_load_preserve_fields(self, tmp_path):
        data = "café".encode()
        path = "2024/07 July/café.jpg"
        _finished(tmp_path, path, data, "drive-café")
        record = load_sync_record(tmp_path)
        entry = record.entry_for(path)
        assert record.drive_folder_id == "folder-1"
        assert entry.local_path == path
        assert entry.size == len(data)
        assert entry.content_hash == _digest(data)
        assert entry.drive_file_id == "drive-café"
        on_disk = json.loads(sync_path_for(tmp_path).read_text(encoding="utf-8"))
        assert on_disk["version"] == 1
        assert on_disk["mode"] == ADDITIVE_MODE
        assert on_disk["entries"][0]["local_path"] == path

    def test_unknown_fields_round_trip(self, tmp_path):
        digest = _digest(b"abc")
        path = sync_path_for(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "version": 1,
            "mode": ADDITIVE_MODE,
            "drive_folder_id": "folder-1",
            "library_note": "organized",
            "entries": [{
                "local_path": "2024/a.jpg",
                "size": 3,
                "content_hash": digest,
                "drive_file_id": "drive-1",
                "mtime_hint": "2024-07-15",
            }],
        }), encoding="utf-8")
        record = load_sync_record(tmp_path)
        record.mark_uploaded("2024/b.jpg", 3, digest, "drive-2")
        save_sync_record(record, tmp_path)
        again = load_sync_record(tmp_path)
        assert again.extra["library_note"] == "organized"
        assert again.entry_for("2024/a.jpg").extra["mtime_hint"] == "2024-07-15"

    def test_finishing_an_upload_drops_a_stale_session_field(self, tmp_path):
        digest = _digest(b"abc")
        record = SyncRecord(drive_folder_id="folder-1")
        record.entries["2024/a.jpg"] = SyncEntry(
            local_path="2024/a.jpg",
            size=3,
            content_hash=digest,
            drive_file_id="",
            extra={"mtime_hint": "kept", "resume_uri": "https://upload.example/session"},
        )
        # resume_uri is forbidden on load/save; mark_uploaded must drop it
        # before the record can be written.
        record.mark_uploaded("2024/a.jpg", 3, digest, "drive-1")
        assert "resume_uri" not in record.entry_for("2024/a.jpg").extra
        assert record.entry_for("2024/a.jpg").extra["mtime_hint"] == "kept"
        save_sync_record(record, tmp_path)

    def test_two_libraries_do_not_share_a_record(self, tmp_path):
        one = tmp_path / "one"
        two = tmp_path / "two"
        _finished(one, "2024/a.jpg", b"one", "drive-1")
        other = load_sync_record(two)
        assert other.entries == {}
        assert other.decide("2024/a.jpg", 3, _digest(b"one")) == DECISION_UPLOAD

    def test_entries_are_stored_in_path_order(self, tmp_path):
        record = SyncRecord()
        record.bind_folder("folder-1")
        record.mark_uploaded("2024/b.jpg", 1, _digest(b"b"), "id-b")
        record.mark_uploaded("2024/a.jpg", 1, _digest(b"a"), "id-a")
        save_sync_record(record, tmp_path)
        names = [entry["local_path"] for entry in json.loads(
            sync_path_for(tmp_path).read_text(encoding="utf-8"))["entries"]]
        assert names == ["2024/a.jpg", "2024/b.jpg"]


class TestResume:
    def test_crash_before_save_keeps_finished_files_and_retries_the_rest(self, tmp_path):
        first = b"finished"
        _finished(tmp_path, "2024/done.jpg", first, "drive-done")
        # A second file was being uploaded when the process died, before
        # mark_uploaded + save. The on-disk record still has only the first.
        record = load_sync_record(tmp_path)
        assert record.decide("2024/done.jpg", len(first), _digest(first)) == DECISION_SKIP
        pending = b"not-yet"
        assert record.decide("2024/pending.jpg", len(pending), _digest(pending)) == DECISION_UPLOAD

    def test_leftover_part_file_does_not_replace_the_record(self, tmp_path):
        data = b"safe"
        _finished(tmp_path, "2024/a.jpg", data, "drive-1")
        part = sync_path_for(tmp_path).with_name("drive_sync.json.part")
        part.write_text("{not-json", encoding="utf-8")
        record = load_sync_record(tmp_path)
        assert record.decide("2024/a.jpg", len(data), _digest(data)) == DECISION_SKIP
        save_sync_record(record, tmp_path)
        assert not part.exists()

    def test_mark_uploaded_requires_a_folder_and_a_file_id(self):
        record = SyncRecord()
        with pytest.raises(ValueError):
            record.mark_uploaded("2024/a.jpg", 1, _digest(b"a"), "drive-1")
        record.bind_folder("folder-1")
        with pytest.raises(ValueError):
            record.mark_uploaded("2024/a.jpg", 1, _digest(b"a"), "")
        with pytest.raises(ValueError):
            record.mark_uploaded("2024/a.jpg", -1, _digest(b"a"), "drive-1")
        with pytest.raises(ValueError):
            record.mark_uploaded("2024/a.jpg", 1, "abcd", "drive-1")


class TestCorruptRecord:
    def _write(self, library: Path, payload) -> None:
        path = sync_path_for(library)
        path.parent.mkdir(parents=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                        encoding="utf-8")

    def test_broken_json_raises(self, tmp_path):
        self._write(tmp_path, "{")
        with pytest.raises(SyncRecordError):
            load_sync_record(tmp_path)

    def test_unsupported_version_raises(self, tmp_path):
        self._write(tmp_path, {
            "version": 2,
            "mode": ADDITIVE_MODE,
            "drive_folder_id": "folder-1",
            "entries": [],
        })
        with pytest.raises(SyncRecordError):
            load_sync_record(tmp_path)

    def test_non_additive_mode_raises(self, tmp_path):
        self._write(tmp_path, {
            "version": 1,
            "mode": "mirror",
            "drive_folder_id": "folder-1",
            "entries": [],
        })
        with pytest.raises(SyncRecordError):
            load_sync_record(tmp_path)

    def test_duplicate_paths_raise(self, tmp_path):
        digest = _digest(b"abc")
        entry = {
            "local_path": "2024/a.jpg",
            "size": 3,
            "content_hash": digest,
            "drive_file_id": "drive-1",
        }
        self._write(tmp_path, {
            "version": 1,
            "mode": ADDITIVE_MODE,
            "drive_folder_id": "folder-1",
            "entries": [entry, dict(entry, local_path="2024\\a.jpg")],
        })
        with pytest.raises(SyncRecordError):
            load_sync_record(tmp_path)

    def test_credential_fields_are_rejected(self, tmp_path):
        self._write(tmp_path, {
            "version": 1,
            "mode": ADDITIVE_MODE,
            "drive_folder_id": "folder-1",
            "refresh_token": "should-not-be-here",
            "entries": [],
        })
        with pytest.raises(SyncRecordError):
            load_sync_record(tmp_path)

    def test_upload_session_is_rejected_in_the_library_record(self, tmp_path):
        digest = _digest(b"abc")
        self._write(tmp_path, {
            "version": 1,
            "mode": ADDITIVE_MODE,
            "drive_folder_id": "folder-1",
            "entries": [{
                "local_path": "2024/a.jpg",
                "size": 3,
                "content_hash": digest,
                "drive_file_id": "",
                "session_uri": "https://upload.example/session",
            }],
        })
        with pytest.raises(SyncRecordError):
            load_sync_record(tmp_path)


class TestNoNetwork:
    def test_module_imports_no_network_libraries(self):
        import media_organizer.core.drive_sync as drive_sync

        tree = ast.parse(Path(drive_sync.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint({
            "urllib", "requests", "http", "socket", "httplib",
            "google", "googleapiclient", "aiohttp", "httpx",
        })

    def test_load_save_and_decide_do_not_open_sockets(self, tmp_path, monkeypatch):
        def boom(*_args, **_kwargs):
            raise AssertionError("network call")

        monkeypatch.setattr(socket, "socket", boom)
        monkeypatch.setattr(socket, "create_connection", boom)
        record = _finished(tmp_path, "2024/a.jpg", b"abc", "drive-1")
        loaded = load_sync_record(tmp_path)
        assert loaded.decide("2024/a.jpg", 3, _digest(b"abc")) == DECISION_SKIP
        assert record.decide("2024/b.jpg", 1, _digest(b"b")) == DECISION_UPLOAD
