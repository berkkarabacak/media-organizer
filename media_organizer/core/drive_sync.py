"""Local record of what has already been uploaded to one Drive folder.

This module decides whether a later upload pass should skip a file or send
it. It does not sign in, call the Drive API, or transfer bytes. See
``docs/drive-incremental-upload.md``.

A file is unchanged only when its library-relative path, size, and SHA-256
content hash all match a finished entry (one that has a Drive file id).
A new path, or a different size or hash, is an upload. The record is
additive: it has no delete list. An entry counts as finished only after
``mark_uploaded``, and ``save_sync_record`` replaces the file atomically,
so a crash keeps the previous finished set and leaves the interrupted file
to be uploaded again.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SYNC_DIRNAME = ".media_organizer"
SYNC_FILENAME = "drive_sync.json"
RECORD_VERSION = 1
ADDITIVE_MODE = "additive"
DECISION_SKIP = "skip"
DECISION_UPLOAD = "upload"

_RECORD_KEYS = frozenset({"version", "mode", "drive_folder_id", "entries"})
_ENTRY_KEYS = frozenset({"local_path", "size", "content_hash", "drive_file_id"})
# Cleared once a file is finished so a stale session cannot be reused.
_CLEAR_ON_COMPLETE = frozenset({"resume_uri", "pending_upload", "upload_session", "session_uri"})
# Bearer material belongs next to the OAuth token, never in the library copy.
_FORBIDDEN_KEYS = frozenset({
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
})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class SyncRecordError(Exception):
    """The on-disk record cannot be trusted.

    Callers must stop and tell the user. An empty record here would upload
    the library again and could place a second copy of files already on Drive.
    """


def sync_path_for(library_dir: Path | str) -> Path:
    """Path of the sync record inside an organized library."""
    return Path(library_dir) / SYNC_DIRNAME / SYNC_FILENAME


def normalize_local_path(local_path: str) -> str:
    """Library-relative path with ``/`` separators and no ``.`` or ``..``."""
    if not isinstance(local_path, str):
        raise ValueError("local_path must be a relative path inside the library")
    text = local_path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        raise ValueError("local_path must be a relative path inside the library")
    cleaned: list[str] = []
    for part in text.split("/"):
        if part == ".":
            continue
        if part == "" or part == "..":
            raise ValueError("local_path must stay inside the library")
        cleaned.append(part)
    if not cleaned:
        raise ValueError("local_path must be a relative path inside the library")
    return "/".join(cleaned)


def relative_local_path(library_dir: Path | str, file_path: Path | str) -> str:
    """Return ``file_path`` relative to the organized library root."""
    library = Path(library_dir)
    path = Path(file_path)
    if path.is_absolute():
        try:
            path = path.relative_to(library)
        except ValueError as exc:
            raise ValueError("file is outside the organized library") from exc
    return normalize_local_path(path.as_posix())


def _check_size(size: object) -> int:
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("size must be a non-negative integer")
    return size


def _normalize_hash(content_hash: object) -> str:
    if not isinstance(content_hash, str):
        raise ValueError("content_hash must be a SHA-256 hex string")
    text = content_hash.strip().lower()
    if _HASH_RE.fullmatch(text) is None:
        raise ValueError("content_hash must be a SHA-256 hex string")
    return text


def _check_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        raise ValueError(f"{label} must be a non-empty single-line id")
    return value


def _reject_forbidden_keys(value: object, where: str) -> None:
    """Refuse credential and upload-session fields anywhere in the tree."""
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in _FORBIDDEN_KEYS:
                raise SyncRecordError(f"{where} must not store credentials")
            _reject_forbidden_keys(child, where)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_keys(child, where)


@dataclass
class SyncEntry:
    """One local file that this library has tried to place in Drive.

    ``drive_file_id`` is empty until the upload finishes. An empty id is not
    a finished upload, so :meth:`SyncRecord.decide` still returns upload.
    """

    local_path: str
    size: int
    content_hash: str
    drive_file_id: str
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {
            "local_path": self.local_path,
            "size": self.size,
            "content_hash": self.content_hash,
            "drive_file_id": self.drive_file_id,
        }
        for key, value in self.extra.items():
            if key not in data:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SyncEntry":
        if not isinstance(data, dict):
            raise SyncRecordError("sync record entry is not an object")
        _reject_forbidden_keys(data, "sync record")
        try:
            path = normalize_local_path(data["local_path"])
            size = _check_size(data["size"])
            digest = _normalize_hash(data["content_hash"])
            raw_id = data["drive_file_id"]
        except (KeyError, ValueError) as exc:
            raise SyncRecordError("sync record entry is not usable") from exc
        if raw_id == "":
            file_id = ""
        else:
            try:
                file_id = _check_id(raw_id, "drive_file_id")
            except ValueError as exc:
                raise SyncRecordError("sync record entry is not usable") from exc
        extra = {key: value for key, value in data.items() if key not in _ENTRY_KEYS}
        return cls(
            local_path=path,
            size=size,
            content_hash=digest,
            drive_file_id=file_id,
            extra=extra,
        )


@dataclass
class SyncRecord:
    """In-memory sync record for one organized library and one Drive folder."""

    drive_folder_id: str = ""
    entries: dict[str, SyncEntry] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def bind_folder(self, drive_folder_id: str) -> None:
        """Remember the single Drive folder this library uploads into.

        Binding a different folder drops the stored file ids. That does not
        delete anything in the previous folder; those remote files stay.
        The next pass uploads into the new folder.
        """
        folder_id = _check_id(drive_folder_id, "drive_folder_id")
        if folder_id == self.drive_folder_id:
            return
        if self.drive_folder_id:
            self.entries.clear()
        self.drive_folder_id = folder_id

    def entry_for(self, local_path: str) -> Optional[SyncEntry]:
        """Stored entry for this path, or None if the path is new."""
        return self.entries.get(normalize_local_path(local_path))

    def decide(self, local_path: str, size: int, content_hash: str) -> str:
        """Return ``skip`` or ``upload``.

        ``skip`` means the path is already finished and the size and content
        hash still match. Anything else is ``upload``: a new path, a different
        size, a different hash, or a path whose upload never received a Drive
        file id.
        """
        path = normalize_local_path(local_path)
        checked_size = _check_size(size)
        digest = _normalize_hash(content_hash)
        entry = self.entries.get(path)
        if entry is None or not entry.drive_file_id:
            return DECISION_UPLOAD
        if entry.size != checked_size or entry.content_hash != digest:
            return DECISION_UPLOAD
        return DECISION_SKIP

    def mark_uploaded(
        self,
        local_path: str,
        size: int,
        content_hash: str,
        drive_file_id: str,
    ) -> None:
        """Record a finished upload. Call this only after Drive returns a file id."""
        if not self.drive_folder_id:
            raise ValueError("bind a Drive folder before recording an upload")
        path = normalize_local_path(local_path)
        checked_size = _check_size(size)
        digest = _normalize_hash(content_hash)
        file_id = _check_id(drive_file_id, "drive_file_id")
        previous = self.entries.get(path)
        extra = {}
        if previous is not None:
            extra = {
                key: value
                for key, value in previous.extra.items()
                if key not in _CLEAR_ON_COMPLETE and key not in _ENTRY_KEYS
            }
        self.entries[path] = SyncEntry(
            local_path=path,
            size=checked_size,
            content_hash=digest,
            drive_file_id=file_id,
            extra=extra,
        )

    def to_dict(self) -> dict:
        data = {
            "version": RECORD_VERSION,
            "mode": ADDITIVE_MODE,
            "drive_folder_id": self.drive_folder_id,
            "entries": [self.entries[key].to_dict() for key in sorted(self.entries)],
        }
        for key, value in self.extra.items():
            if key not in data:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SyncRecord":
        if not isinstance(data, dict):
            raise SyncRecordError("sync record is not an object")
        _reject_forbidden_keys(data, "sync record")
        if data.get("version") != RECORD_VERSION or isinstance(data.get("version"), bool):
            raise SyncRecordError("sync record version is not supported")
        if data.get("mode") != ADDITIVE_MODE:
            raise SyncRecordError("sync record mode is not additive")
        if "drive_folder_id" not in data or "entries" not in data:
            raise SyncRecordError("sync record is not usable")
        raw_folder = data["drive_folder_id"]
        if raw_folder == "":
            folder_id = ""
        else:
            try:
                folder_id = _check_id(raw_folder, "drive_folder_id")
            except ValueError as exc:
                raise SyncRecordError("sync record is not usable") from exc
        raw_entries = data["entries"]
        if not isinstance(raw_entries, list):
            raise SyncRecordError("sync record is not usable")
        record = cls(
            drive_folder_id=folder_id,
            extra={key: value for key, value in data.items() if key not in _RECORD_KEYS},
        )
        for raw in raw_entries:
            entry = SyncEntry.from_dict(raw)
            if entry.local_path in record.entries:
                raise SyncRecordError("sync record lists the same local path twice")
            record.entries[entry.local_path] = entry
        return record


def load_sync_record(library_dir: Path | str) -> SyncRecord:
    """Load the library's sync record. A missing file is an empty record."""
    path = sync_path_for(library_dir)
    if not path.exists():
        return SyncRecord()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SyncRecordError(f"cannot read sync record: {path}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SyncRecordError(f"cannot read sync record: {path}") from exc
    return SyncRecord.from_dict(data)


def save_sync_record(record: SyncRecord, library_dir: Path | str) -> Path:
    """Write the record atomically. A crash keeps the previous file."""
    path = sync_path_for(library_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload_obj = record.to_dict()
    _reject_forbidden_keys(payload_obj, "sync record")
    payload = json.dumps(payload_obj, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".part")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)
    return path


def _fsync_dir(directory: Path) -> None:
    """Flush the rename itself. A failure here still leaves the new file in place."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
