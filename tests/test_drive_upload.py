"""First Drive upload: resumable sessions, cancel, and resume. No network."""

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from media_organizer.core.drive_folders import FOLDER_MIME
from media_organizer.core.drive_sync import (
    DECISION_SKIP,
    DECISION_UPLOAD,
    SyncRecord,
    load_sync_record,
    save_sync_record,
    sync_path_for,
)
from media_organizer.core.drive_upload import (
    SESSIONS_FILENAME,
    DriveResponse,
    DriveUploadError,
    UploadSessionStore,
    default_drive_request,
    upload_library,
)
from media_organizer.core.duplicates import full_hash

_QUERY_RE = re.compile(
    r"name = '(?P<name>.*?)' and mimeType = 'application/vnd\.google-apps\.folder' "
    r"and '(?P<parent>.*?)' in parents and trashed = false"
)
_FORBIDDEN = {
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "client_secret_json",
    "credentials",
    "secret",
    "resume_uri",
    "upload_session",
    "session_uri",
}


def _response(status, headers, body):
    return DriveResponse(status, {key.lower(): value for key, value in headers.items()}, body)


class FakeDrive:
    """In-memory Drive: folders, resumable sessions, and no delete."""

    def __init__(self):
        self.calls = []
        self.folders = {}
        self.folder_seq = 0
        self.file_seq = 0
        self.sessions = {}
        self.files = {}

    def request(self, method, url, headers, body, timeout=None):
        lowered = {str(key).lower(): value for key, value in headers.items()}
        payload = body if isinstance(body, (bytes, bytearray)) else b""
        call = {
            "method": method.upper(),
            "url": url,
            "headers": {key: value for key, value in lowered.items() if key != "authorization"},
            "authorization": lowered.get("authorization", ""),
            "body": bytes(payload),
        }
        self.calls.append(call)
        if _is_remote_delete(call):
            raise AssertionError(f"remote delete is not allowed: {method} {url}")
        return self._route(call)

    def _route(self, call):
        method = call["method"]
        url = call["url"]
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc == "upload.example":
            return self._resumable(parsed, call["headers"], call["body"])
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path
        upload = "uploadType" in query
        is_files = path.rstrip("/").endswith("/drive/v3/files") or "/drive/v3/files/" in path
        if not is_files:
            raise AssertionError(f"unexpected {method} {url}")
        if method == "GET" and not upload:
            return self._list(query)
        if method == "POST" and not upload:
            return self._make_folder(call["body"])
        if upload and method == "POST" and path.rstrip("/").endswith("/files"):
            return self._open_session(call["body"], "", call["headers"])
        if upload and method == "PATCH" and "/files/" in path:
            file_id = path.rstrip("/").split("/")[-1]
            return self._open_session(call["body"], file_id, call["headers"])
        raise AssertionError(f"unexpected {method} {url}")

    def _list(self, query):
        text = query.get("q", [""])[0]
        match = _QUERY_RE.fullmatch(text)
        if match is None:
            raise AssertionError(f"unexpected folder query: {text}")
        name = match.group("name")
        parent = match.group("parent")
        found = []
        for (folder_parent, folder_name), folder_id in self.folders.items():
            if folder_parent == parent and folder_name == name:
                found.append({
                    "id": folder_id,
                    "name": folder_name,
                    "mimeType": FOLDER_MIME,
                })
        return _response(200, {}, json.dumps({"files": found}).encode("utf-8"))

    def _make_folder(self, body):
        meta = json.loads(body.decode("utf-8"))
        assert meta["mimeType"] == FOLDER_MIME
        parent = meta["parents"][0]
        name = meta["name"]
        self.folder_seq += 1
        folder_id = f"fld-{self.folder_seq}"
        self.folders[(parent, name)] = folder_id
        payload = json.dumps({"id": folder_id}).encode("utf-8")
        return _response(200, {}, payload)

    def _open_session(self, body, file_id, headers):
        meta = json.loads(body.decode("utf-8"))
        self.file_seq += 1
        upload_id = f"up-{self.file_seq}"
        if file_id:
            assigned = file_id
            kind = "update"
        else:
            assigned = f"file-{self.file_seq}"
            kind = "create"
        session = {
            "id": assigned,
            "kind": kind,
            "name": meta.get("name"),
            "parents": list(meta.get("parents") or []),
            "size": int(headers.get("x-upload-content-length", "0")),
            "mime": headers.get("x-upload-content-type", ""),
            "received": 0,
            "data": bytearray(),
            "done": False,
        }
        self.sessions[upload_id] = session
        uri = f"https://upload.example/resumable?upload_id={upload_id}"
        return _response(200, {"Location": uri}, b"")

    def _resumable(self, parsed, headers, body):
        upload_id = urllib.parse.parse_qs(parsed.query)["upload_id"][0]
        session = self.sessions.get(upload_id)
        if session is None:
            return _response(404, {}, b"")
        content_range = headers.get("content-range", "")
        if content_range.startswith("bytes */"):
            if session["done"]:
                return self._file_json(session)
            if session["size"] == 0 and content_range.endswith("/0"):
                return self._finish(session)
            return self._incomplete(session)
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
        if match is None:
            return _response(400, {}, b"")
        start, end, total = (int(match.group(index)) for index in range(1, 4))
        if start != session["received"] or len(body) != (end - start + 1):
            return self._incomplete(session)
        session["data"].extend(body)
        session["received"] = end + 1
        if session["received"] >= total:
            return self._finish(session)
        return self._incomplete(session)

    def _incomplete(self, session):
        headers = {}
        if session["received"] > 0:
            headers["Range"] = f"bytes=0-{session['received'] - 1}"
        return _response(308, headers, b"")

    def _finish(self, session):
        session["done"] = True
        self.files[session["id"]] = {
            "name": session["name"],
            "parents": list(session["parents"]),
            "data": bytes(session["data"]),
            "kind": session["kind"],
        }
        payload = json.dumps({"id": session["id"], "name": session["name"]}).encode("utf-8")
        return _response(200, {"Content-Type": "application/json"}, payload)

    def _file_json(self, session):
        payload = json.dumps({"id": session["id"], "name": session["name"]}).encode("utf-8")
        return _response(200, {}, payload)


def _bind(library: Path, folder_id="folder-bound", note="organized"):
    library.mkdir(parents=True, exist_ok=True)
    record = SyncRecord()
    record.bind_folder(folder_id)
    record.extra["library_note"] = note
    save_sync_record(record, library)
    return record


def _write(library: Path, relative: str, data: bytes) -> None:
    path = library.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _run(library, fake, sessions, **extra):
    progress = []

    def on_progress(update):
        progress.append(update)

    result = upload_library(
        library,
        access_token=lambda: "test-access-token",
        request=fake.request,
        progress=on_progress,
        sessions_path=sessions,
        **extra,
    )
    return result, progress


def _keys(value, found):
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(str(key).strip().lower())
            _keys(child, found)
    elif isinstance(value, list):
        for child in value:
            _keys(child, found)


def _assert_library_has_no_secrets(library: Path):
    for path in library.rglob("*"):
        if not path.is_file():
            continue
        blob = path.read_bytes()
        assert b"test-access-token" not in blob
        if path.name != "drive_sync.json":
            continue
        data = json.loads(blob.decode("utf-8"))
        found = set()
        _keys(data, found)
        assert found.isdisjoint(_FORBIDDEN)
        assert b"resume_uri" not in blob
        assert b"upload.example" not in blob


def _is_remote_delete(call) -> bool:
    if call["method"] == "DELETE":
        return True
    path = urllib.parse.urlparse(call["url"]).path.rstrip("/").lower()
    return path.endswith("/trash")


def _assert_additive(fake: FakeDrive):
    assert fake.calls
    assert not any(_is_remote_delete(call) for call in fake.calls)
    assert all(call["authorization"] == "Bearer test-access-token" for call in fake.calls)


def _resumable_posts(fake: FakeDrive):
    return [
        call for call in fake.calls
        if call["method"] == "POST" and "uploadType=resumable" in call["url"]
    ]


def _resumable_patches(fake: FakeDrive):
    return [
        call for call in fake.calls
        if call["method"] == "PATCH" and "uploadType=resumable" in call["url"]
    ]


class TestFirstUpload:
    def test_organized_tree_is_uploaded_then_saved_and_skippable(self, tmp_path):
        library = tmp_path / "library"
        _bind(library)
        files = {
            "2024/Q3/07 July/IMG_1234.jpg": b"img-1234",
            "2024/Q3/07 July/IMG_1235.jpg": b"img-1235",
            "_uncertain/guess.jpg": b"guess",
            "_undated/none.jpg": b"none",
            "_unknown-location/where.jpg": b"where",
        }
        for relative, data in files.items():
            _write(library, relative, data)
        hidden = library / ".media_organizer" / "not-for-drive.txt"
        hidden.write_bytes(b"local-only-secret")
        fake = FakeDrive()
        sessions = tmp_path / "google" / "upload_sessions.json"
        result, progress = _run(library, fake, sessions)

        assert result.cancelled is False
        assert result.uploaded == 5
        assert result.skipped == 0
        assert result.files_done == 5
        assert result.files_total == 5
        assert progress[-1].bytes_done == progress[-1].bytes_total
        assert progress[-1].bytes_total == sum(len(data) for data in files.values())
        assert {update.current_file for update in progress if update.uploading} == set(files)

        root = "folder-bound"
        july = fake.folders[(fake.folders[(fake.folders[(root, "2024")], "Q3")], "07 July")]
        assert fake.folders[(root, "_uncertain")]
        assert fake.folders[(root, "_undated")]
        assert fake.folders[(root, "_unknown-location")]
        assert len(fake.folders) == 6
        by_name = {item["name"]: item for item in fake.files.values()}
        assert by_name["IMG_1234.jpg"]["parents"] == [july]
        assert by_name["IMG_1234.jpg"]["data"] == b"img-1234"
        assert by_name["IMG_1235.jpg"]["parents"] == [july]
        assert by_name["guess.jpg"]["parents"] == [fake.folders[(root, "_uncertain")]]
        assert by_name["none.jpg"]["parents"] == [fake.folders[(root, "_undated")]]
        assert by_name["where.jpg"]["parents"] == [fake.folders[(root, "_unknown-location")]]
        assert all(item["kind"] == "create" for item in fake.files.values())
        assert len(_resumable_posts(fake)) == 5
        assert _resumable_patches(fake) == []
        mime_headers = [
            call["headers"]["x-upload-content-type"]
            for call in fake.calls
            if "x-upload-content-type" in call["headers"]
        ]
        assert mime_headers
        assert set(mime_headers) == {"image/jpeg"}
        assert all(b"local-only-secret" not in call["body"] for call in fake.calls)
        assert hidden.read_bytes() == b"local-only-secret"

        record = load_sync_record(library)
        assert record.extra["library_note"] == "organized"
        assert record.drive_folder_id == root
        for relative, data in files.items():
            path = library.joinpath(*relative.split("/"))
            assert path.read_bytes() == data
            entry = record.entry_for(relative)
            assert entry.drive_file_id
            assert entry.size == len(data)
            assert entry.content_hash == full_hash(path)
            assert record.decide(relative, len(data), entry.content_hash) == DECISION_SKIP
            assert "resume_uri" not in entry.extra
        assert not sessions.exists()
        _assert_additive(fake)
        _assert_library_has_no_secrets(library)

        again, _progress = _run(library, fake, sessions)
        assert again.uploaded == 0
        assert again.skipped == 5
        assert len(_resumable_posts(fake)) == 5
        _assert_additive(fake)
        _assert_library_has_no_secrets(library)

    def test_changed_file_updates_the_same_drive_id(self, tmp_path):
        library = tmp_path / "library"
        _bind(library)
        relative = "2024/Q3/07 July/IMG_1234.jpg"
        _write(library, relative, b"old-bytes")
        record = load_sync_record(library)
        record.mark_uploaded(relative, len(b"old-bytes"), full_hash(library / "2024/Q3/07 July/IMG_1234.jpg"), "drive-existing")
        save_sync_record(record, library)
        _write(library, relative, b"new-bytes")
        fake = FakeDrive()
        sessions = tmp_path / "google" / "upload_sessions.json"
        result, _progress = _run(library, fake, sessions)

        assert result.uploaded == 1
        assert result.skipped == 0
        assert _resumable_posts(fake) == []
        patches = _resumable_patches(fake)
        assert len(patches) == 1
        assert "/drive-existing?" in patches[0]["url"] or patches[0]["url"].endswith("/drive-existing") or "drive-existing" in patches[0]["url"]
        meta = json.loads(patches[0]["body"].decode("utf-8"))
        assert "parents" not in meta
        assert fake.files["drive-existing"]["kind"] == "update"
        assert fake.files["drive-existing"]["data"] == b"new-bytes"
        assert list(fake.files) == ["drive-existing"]
        saved = load_sync_record(library)
        assert saved.entry_for(relative).drive_file_id == "drive-existing"
        assert saved.decide(relative, len(b"new-bytes"), full_hash(library / "2024/Q3/07 July/IMG_1234.jpg")) == DECISION_SKIP
        _assert_additive(fake)
        _assert_library_has_no_secrets(library)

    def test_empty_file_uses_the_empty_resumable_range(self, tmp_path):
        library = tmp_path / "library"
        _bind(library)
        _write(library, "_undated/empty.jpg", b"")
        fake = FakeDrive()
        result, _progress = _run(library, fake, tmp_path / "google" / "upload_sessions.json")
        assert result.uploaded == 1
        ranges = [
            call["headers"].get("content-range", "")
            for call in fake.calls
            if call["url"].startswith("https://upload.example/")
        ]
        assert "bytes */0" in ranges
        assert load_sync_record(library).entry_for("_undated/empty.jpg").drive_file_id
        _assert_additive(fake)

    def test_a_removed_local_file_stays_in_the_record_and_on_drive(self, tmp_path):
        library = tmp_path / "library"
        _bind(library)
        gone = library / "2024" / "gone.jpg"
        _write(library, "2024/gone.jpg", b"stay")
        record = load_sync_record(library)
        record.mark_uploaded("2024/gone.jpg", 4, full_hash(gone), "drive-gone")
        save_sync_record(record, library)
        gone.unlink()
        _write(library, "2024/new.jpg", b"fresh")
        fake = FakeDrive()
        result, _progress = _run(library, fake, tmp_path / "google" / "upload_sessions.json")
        assert result.uploaded == 1
        saved = load_sync_record(library)
        assert saved.entry_for("2024/gone.jpg").drive_file_id == "drive-gone"
        assert saved.entry_for("2024/new.jpg").drive_file_id
        assert not any(_is_remote_delete(call) for call in fake.calls)
        _assert_library_has_no_secrets(library)

    def test_refuses_to_upload_without_a_bound_folder(self, tmp_path):
        library = tmp_path / "library"
        library.mkdir()
        _write(library, "2024/a.jpg", b"abcd")
        fake = FakeDrive()
        try:
            upload_library(
                library,
                access_token="test-access-token",
                request=fake.request,
                sessions_path=tmp_path / "sessions.json",
            )
        except DriveUploadError as exc:
            assert "folder" in str(exc).lower()
        else:
            raise AssertionError("upload should refuse a library with no Drive folder")
        assert fake.calls == []


class TestCancelAndResume:
    def test_cancel_and_resume_round_trip(self, tmp_path, monkeypatch):
        google = tmp_path / "appdata" / "google"
        monkeypatch.setattr(
            "media_organizer.core.drive_upload.google_dir",
            lambda: google,
        )
        library = tmp_path / "library"
        _bind(library)
        _write(library, "2024/a.jpg", b"aaaa")
        _write(library, "2024/b.jpg", b"0123456789")
        _write(library, "2024/c.jpg", b"zzz")
        fake = FakeDrive()
        gate = {"stop": False}

        def request(method, url, headers, body, timeout=None):
            response = fake.request(method, url, headers, body, timeout)
            lowered = {str(key).lower(): value for key, value in headers.items()}
            if lowered.get("content-range") == "bytes 0-3/10":
                gate["stop"] = True
            return response

        progress = []
        first = upload_library(
            library,
            access_token=lambda: "test-access-token",
            request=request,
            progress=progress.append,
            cancel=lambda: gate["stop"],
            chunk_size=4,
        )
        assert first.cancelled is True
        assert first.uploaded == 1
        assert first.files_done == 1
        record = load_sync_record(library)
        assert record.decide("2024/a.jpg", 4, full_hash(library / "2024/a.jpg")) == DECISION_SKIP
        assert record.decide("2024/b.jpg", 10, full_hash(library / "2024/b.jpg")) == DECISION_UPLOAD
        assert record.entry_for("2024/c.jpg") is None
        store = UploadSessionStore()
        stored = store.get(library, "2024/b.jpg")
        assert stored is not None
        assert stored.resume_uri.startswith("https://upload.example/")
        assert stored.size == 10
        assert stored.content_hash == full_hash(library / "2024/b.jpg")
        assert store.get(library, "2024/a.jpg") is None
        assert store.get(library, "2024/c.jpg") is None
        session_file = google / SESSIONS_FILENAME
        assert session_file.is_file()
        assert not str(session_file.resolve()).startswith(str(library.resolve()))
        session_text = session_file.read_text(encoding="utf-8")
        assert "resume_uri" in session_text
        assert "2024/b.jpg" in session_text
        assert "test-access-token" not in session_text
        assert "access_token" not in session_text
        assert (session_file.stat().st_mode & 0o777) == 0o600
        assert (library / "2024/a.jpg").read_bytes() == b"aaaa"
        assert (library / "2024/b.jpg").read_bytes() == b"0123456789"
        _assert_library_has_no_secrets(library)
        _assert_additive(fake)
        creates_after_cancel = len(_resumable_posts(fake))
        assert creates_after_cancel == 2

        gate["stop"] = False
        second = upload_library(
            library,
            access_token=lambda: "test-access-token",
            request=request,
            cancel=lambda: False,
            chunk_size=4,
        )
        assert second.cancelled is False
        assert second.uploaded == 2
        assert second.skipped == 1
        ranges = [
            call["headers"].get("content-range", "")
            for call in fake.calls
            if "upload.example" in call["url"] and call["headers"].get("content-range", "").startswith("bytes 4-")
        ]
        assert any(item.startswith("bytes 4-") for item in ranges)
        assert len(_resumable_posts(fake)) == 3
        saved = load_sync_record(library)
        for relative in ("2024/a.jpg", "2024/b.jpg", "2024/c.jpg"):
            path = library / Path(relative)
            entry = saved.entry_for(relative)
            assert saved.decide(relative, path.stat().st_size, full_hash(path)) == DECISION_SKIP
        assert fake.files
        b_file = next(item for item in fake.files.values() if item["name"] == "b.jpg")
        assert b_file["data"] == b"0123456789"
        assert b_file["kind"] == "create"
        assert not session_file.exists()
        _assert_additive(fake)
        _assert_library_has_no_secrets(library)
        assert any(update.files_total == 3 and update.bytes_total == 17 for update in progress)

    def test_resume_continues_a_stored_session(self, tmp_path):
        library = tmp_path / "library"
        _bind(library)
        data = b"hello world"
        _write(library, "2024/a.jpg", data)
        sessions = tmp_path / "google" / "upload_sessions.json"
        fake = FakeDrive()
        fake.sessions["sess-1"] = {
            "id": "file-continued",
            "kind": "create",
            "name": "a.jpg",
            "parents": ["fld-1"],
            "size": len(data),
            "mime": "image/jpeg",
            "received": 4,
            "data": bytearray(data[:4]),
            "done": False,
        }
        UploadSessionStore(sessions).put(
            library,
            "2024/a.jpg",
            len(data),
            full_hash(library / "2024/a.jpg"),
            "https://upload.example/resumable?upload_id=sess-1",
        )
        result, _progress = _run(library, fake, sessions, chunk_size=4)
        assert result.uploaded == 1
        assert _resumable_posts(fake) == []
        ranges = [
            call["headers"].get("content-range", "")
            for call in fake.calls
            if "sess-1" in call["url"]
        ]
        assert "bytes */11" in ranges
        assert any(item.startswith("bytes 4-") for item in ranges)
        assert not any(item.startswith("bytes 0-") for item in ranges)
        assert fake.files["file-continued"]["data"] == data
        assert load_sync_record(library).entry_for("2024/a.jpg").drive_file_id == "file-continued"
        assert not sessions.exists()
        _assert_additive(fake)
        _assert_library_has_no_secrets(library)

    def test_a_finished_session_is_recorded_without_sending_the_bytes_again(self, tmp_path):
        library = tmp_path / "library"
        _bind(library)
        data = b"abcd"
        _write(library, "2024/a.jpg", data)
        sessions = tmp_path / "google" / "upload_sessions.json"
        fake = FakeDrive()
        fake.sessions["sess-done"] = {
            "id": "file-done",
            "kind": "create",
            "name": "a.jpg",
            "parents": ["fld-1"],
            "size": len(data),
            "mime": "image/jpeg",
            "received": len(data),
            "data": bytearray(data),
            "done": True,
        }
        fake.files["file-done"] = {
            "name": "a.jpg",
            "parents": ["fld-1"],
            "data": data,
            "kind": "create",
        }
        UploadSessionStore(sessions).put(
            library,
            "2024/a.jpg",
            len(data),
            full_hash(library / "2024/a.jpg"),
            "https://upload.example/resumable?upload_id=sess-done",
        )
        result, _progress = _run(library, fake, sessions)
        assert result.uploaded == 1
        byte_puts = [
            call for call in fake.calls
            if "sess-done" in call["url"] and call["body"]
        ]
        assert byte_puts == []
        assert load_sync_record(library).entry_for("2024/a.jpg").drive_file_id == "file-done"
        _assert_library_has_no_secrets(library)

    def test_expired_session_starts_a_new_one(self, tmp_path):
        library = tmp_path / "library"
        _bind(library)
        data = b"abcd"
        _write(library, "2024/a.jpg", data)
        sessions = tmp_path / "google" / "upload_sessions.json"
        fake = FakeDrive()
        UploadSessionStore(sessions).put(
            library,
            "2024/a.jpg",
            len(data),
            full_hash(library / "2024/a.jpg"),
            "https://upload.example/resumable?upload_id=missing",
        )
        result, _progress = _run(library, fake, sessions)
        assert result.uploaded == 1
        old_calls = [call for call in fake.calls if "upload_id=missing" in call["url"]]
        assert len(old_calls) == 1
        assert old_calls[0]["headers"].get("content-range") == "bytes */4"
        assert old_calls[0]["body"] == b""
        assert len(_resumable_posts(fake)) == 1
        new_ranges = [
            call["headers"].get("content-range", "")
            for call in fake.calls
            if "upload.example" in call["url"] and "missing" not in call["url"]
        ]
        assert any(item.startswith("bytes 0-") for item in new_ranges)
        assert load_sync_record(library).entry_for("2024/a.jpg").drive_file_id
        _assert_additive(fake)
        _assert_library_has_no_secrets(library)

    def test_a_changed_file_does_not_reuse_the_old_session(self, tmp_path):
        library = tmp_path / "library"
        _bind(library)
        _write(library, "2024/a.jpg", b"zzzz")
        sessions = tmp_path / "google" / "upload_sessions.json"
        fake = FakeDrive()
        fake.sessions["sess-old"] = {
            "id": "file-old",
            "kind": "create",
            "name": "a.jpg",
            "parents": ["fld-1"],
            "size": 4,
            "mime": "image/jpeg",
            "received": 2,
            "data": bytearray(b"zz"),
            "done": False,
        }
        other = tmp_path / "other.bin"
        other.write_bytes(b"yyyy")
        assert full_hash(other) != full_hash(library / "2024/a.jpg")
        UploadSessionStore(sessions).put(
            library,
            "2024/a.jpg",
            4,
            full_hash(other),
            "https://upload.example/resumable?upload_id=sess-old",
        )
        result, _progress = _run(library, fake, sessions)
        assert result.uploaded == 1
        assert all("sess-old" not in call["url"] for call in fake.calls)
        assert len(_resumable_posts(fake)) == 1
        assert fake.files
        assert "file-old" not in fake.files
        _assert_library_has_no_secrets(library)
        _assert_additive(fake)


class TestResumableHttp:
    def test_308_is_returned_instead_of_followed(self):
        class Handler(BaseHTTPRequestHandler):
            def do_PUT(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(308)
                self.send_header("Range", "bytes=0-4")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, fmt, *args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            response = default_drive_request(
                "PUT",
                f"http://{host}:{port}/session",
                {
                    "Content-Range": "bytes */10",
                    "Content-Type": "application/octet-stream",
                    "Content-Length": "0",
                },
                b"",
                timeout=5,
            )
        finally:
            server.server_close()
            thread.join(timeout=2)
        assert response.status == 308
        assert response.header("range") == "bytes=0-4"
