"""Upload an organized library into the one bound Drive folder.

Organize stays on disk. This module runs after that, one way, and only
adds or updates files under the folder id stored on the sync record. It
never deletes a local file and never calls Drive's delete or trash APIs.

One file is in flight at a time, through Drive's resumable upload. The
session URI is stored next to the OAuth token (``upload_sessions.json`` in
the Google app-data directory). It is never written into ``drive_sync.json``.

``SyncRecord.decide`` chooses skip or upload. A later pass of the same
library skips a file whose size and SHA-256 still match a finished entry,
creates a path that has no Drive file id, and updates a path that already
has one. A finished file is recorded only after Drive returns a file id,
and the record is saved before the next file starts. Cancel stops before
the next file and leaves the in-flight session in place so that file can
continue later. Nothing is deleted on Drive or on disk.

A hard failure also stops the pass. Storage full, an expired or revoked
sign-in, and a lost connection each raise ``DriveUploadError`` with a
short message. The HTTP status and the raw response are not part of that
message. Files already recorded stay skippable. A session that was already
opened stays, so that file can continue later.
"""

from __future__ import annotations

import errno
import json
import mimetypes
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .drive_folders import FILES_URL, FOLDER_MIME
from .drive_sync import (
    DECISION_SKIP,
    SYNC_DIRNAME,
    load_sync_record,
    normalize_local_path,
    relative_local_path,
    save_sync_record,
)
from .duplicates import full_hash
from .google_auth import apply_user_only_permissions, google_dir

UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
SESSIONS_FILENAME = "upload_sessions.json"
SESSIONS_VERSION = 1
# Drive accepts any size for the final chunk. Earlier chunks of a real
# upload are multiples of 256 KiB. Tests may pass a smaller size.
DEFAULT_CHUNK_SIZE = 256 * 1024
_SESSION_FIELDS = frozenset({
    "resume_uri",
    "pending_upload",
    "upload_session",
    "session_uri",
})
_TOKEN_KEYS = frozenset({
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "client_secret_json",
    "credentials",
    "secret",
})
_RANGE_RE = re.compile(r"(\d+)-(\d+)")

# Shown in the Drive dialog. No HTTP status, response body, or stack trace.
QUOTA_MESSAGE = "Google Drive is full. Free some space, then try the upload again."
AUTH_MESSAGE = (
    "Sign in to Google Drive again. The previous sign-in expired or was revoked."
)
OFFLINE_MESSAGE = (
    "This computer cannot reach Google Drive. "
    "Check the internet connection, then try again."
)
GENERIC_UPLOAD_MESSAGE = "Google Drive did not accept the upload. Try again."

_QUOTA_REASONS = frozenset({
    "storagequotaexceeded",
    "quotaexceeded",
})
_AUTH_REASONS = frozenset({
    "autherror",
    "invalidcredentials",
    "unauthorized",
    "unauthenticated",
    "invalid_token",
    "invalid_grant",
})
_RATE_REASONS = frozenset({
    "ratelimitexceeded",
    "userratelimitexceeded",
    "dailylimitexceeded",
    "sharingratelimitexceeded",
})
_QUOTA_PHRASES = (
    "storage quota",
    "storagequotaexceeded",
    "insufficient storage",
)
_AUTH_PHRASES = (
    "invalid authentication",
    "invalid credentials",
    "autherror",
    "invalid_token",
    "invalid_grant",
    "unauthenticated",
)
_AUTH_CALLER_PHRASES = (
    "sign in to google drive again",
    "expired",
    "revoked",
    "invalid_grant",
    "invalid_token",
    "unauthorized",
    "unauthenticated",
    "did not accept the sign-in",
    "invalid credentials",
)
_OFFLINE_CALLER_PHRASES = (
    "could not reach",
    "internet connection",
    "network is unreachable",
    "name or service not known",
)
_OFFLINE_STATUSES = frozenset({408, 502, 503, 504})
_REASON_KEYS = frozenset({"reason", "status", "error"})
_OFFLINE_ERRNOS = frozenset(
    code for code in (
        errno.ENETDOWN,
        errno.ENETUNREACH,
        errno.ENETRESET,
        errno.ECONNABORTED,
        errno.ECONNRESET,
        errno.ECONNREFUSED,
        errno.EHOSTDOWN,
        errno.EHOSTUNREACH,
        errno.ETIMEDOUT,
        errno.ENOTCONN,
        getattr(errno, "ENONET", None),
    )
    if isinstance(code, int)
)


class DriveUploadError(Exception):
    """The upload cannot continue. The message has no token or session URI."""


class _UploadCancelled(Exception):
    """Stop before the next file. Not an error for the caller."""


class _SessionExpired(Exception):
    """The stored resumable session can no longer accept bytes."""


@dataclass(frozen=True)
class UploadProgress:
    """Snapshot the dialog can show while an upload is running.

    ``skipped`` and ``uploaded`` are file counts for this pass.
    ``bytes_done`` and ``bytes_total`` count only files that transfer.
    An unchanged file that already has a Drive file id is not included.
    """

    current_file: str
    files_done: int
    files_total: int
    bytes_done: int
    bytes_total: int
    uploading: bool = False
    skipped: int = 0
    uploaded: int = 0


@dataclass(frozen=True)
class UploadResult:
    """What one pass finished, skipped, or left for a later pass.

    ``bytes_sent`` is the size of files that finished transferring.
    Skipped files are not included.
    """

    uploaded: int
    skipped: int
    cancelled: bool
    files_done: int
    files_total: int
    bytes_sent: int = 0


@dataclass(frozen=True)
class StoredSession:
    """One resumable session kept beside the OAuth token, not in the library."""

    library: str
    local_path: str
    size: int
    content_hash: str
    resume_uri: str


@dataclass
class DriveResponse:
    """One Drive HTTP response, including 308 Resume Incomplete."""

    status: int
    headers: dict
    body: bytes

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Leave 308 on the resumable session instead of following Location."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def default_drive_request(
    method: str,
    url: str,
    headers: dict,
    body: bytes | None,
    timeout: float = 120,
) -> DriveResponse:
    """Perform one Drive request with urllib. Does not follow redirects."""
    data = body if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read()
            status = getattr(response, "status", None) or response.getcode()
            return DriveResponse(int(status), _header_map(response.headers), payload)
    except urllib.error.HTTPError as exc:
        payload = exc.read() if exc.fp is not None else b""
        return DriveResponse(int(exc.code), _header_map(exc.headers), payload)
    except urllib.error.URLError as exc:
        raise DriveUploadError(OFFLINE_MESSAGE) from exc
    except OSError as exc:
        if _is_offline_exception(exc):
            raise DriveUploadError(OFFLINE_MESSAGE) from exc
        raise DriveUploadError(GENERIC_UPLOAD_MESSAGE) from exc


class UploadSessionStore:
    """Resumable session URIs, keyed by library path and library-relative path.

    The file lives next to ``token.json``. A missing or unreadable file means
    there is nothing to resume. Saving replaces the file atomically and keeps
    it readable only by the user.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else google_dir() / SESSIONS_FILENAME

    def get(self, library_dir: Path | str, local_path: str) -> Optional[StoredSession]:
        key = _session_key(library_dir, local_path)
        return _load_sessions(self.path).get(key)

    def put(
        self,
        library_dir: Path | str,
        local_path: str,
        size: int,
        content_hash: str,
        resume_uri: str,
    ) -> None:
        uri = _require_https_uri(resume_uri)
        key = _session_key(library_dir, local_path)
        sessions = _load_sessions(self.path)
        library, rel = key
        sessions[key] = StoredSession(
            library=library,
            local_path=rel,
            size=size,
            content_hash=content_hash,
            resume_uri=uri,
        )
        _save_sessions(self.path, sessions)

    def drop(self, library_dir: Path | str, local_path: str) -> None:
        key = _session_key(library_dir, local_path)
        sessions = _load_sessions(self.path)
        if key not in sessions:
            return
        del sessions[key]
        _save_sessions(self.path, sessions)


def iter_library_files(library_dir: Path | str) -> list[tuple[str, Path, int]]:
    """Regular files in the organized tree, in library-relative path order.

    ``.media_organizer`` (the sync record and the operation log) is not part
    of the tree that gets uploaded. Symlinks are skipped so a link cannot
    pull in a file outside the library.
    """
    library = Path(library_dir)
    found: list[tuple[str, Path, int]] = []
    if not library.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(library):
        dirnames[:] = [name for name in dirnames if name != SYNC_DIRNAME]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                size = path.stat().st_size
            except OSError:
                continue
            try:
                relative = relative_local_path(library, path)
            except ValueError:
                continue
            if SYNC_DIRNAME in relative.split("/"):
                continue
            found.append((relative, path, size))
    found.sort(key=lambda item: item[0])
    return found


def upload_library(
    library_dir: Path | str,
    *,
    access_token: str | Callable[[], str],
    request: Callable | None = None,
    progress: Callable[[UploadProgress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    sessions_path: Path | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    timeout: float = 120,
) -> UploadResult:
    """Upload the organized library under its bound Drive folder.

    A file whose size and SHA-256 still match a finished sync entry is
    skipped. A new path is created under the bound folder. A changed file
    updates the Drive file that entry already names. ``access_token`` is a
    token string or a callable that returns one (used so a long run can
    refresh). ``request`` defaults to urllib and is injected by tests.
    Nothing is deleted on Drive or on disk.

    ``DriveUploadError`` stops the pass. Files already saved stay skippable.
    The in-flight session is left in place. Later files in this pass are
    not started.
    """
    library = Path(library_dir)
    if not library.is_dir():
        raise DriveUploadError("Choose the organized library before uploading.")
    record = load_sync_record(library)
    if not record.drive_folder_id:
        raise DriveUploadError("Choose a Drive folder before uploading.")
    chunk = chunk_size if isinstance(chunk_size, int) and chunk_size > 0 else DEFAULT_CHUNK_SIZE
    files = iter_library_files(library)
    files_total = len(files)
    # Starts as every file. Each skip removes that file, so the total becomes
    # the bytes this pass will actually transfer.
    bytes_total = sum(size for _rel, _path, size in files)
    store = UploadSessionStore(sessions_path)
    drive = _Drive(access_token, request or default_drive_request, timeout)
    folder_cache: dict[tuple[str, str], str] = {}
    uploaded = 0
    skipped = 0
    files_done = 0
    bytes_done = 0

    def report(current: str, *, uploading: bool, sent: int | None = None) -> None:
        if progress is None:
            return
        progress(UploadProgress(
            current_file=current,
            files_done=files_done,
            files_total=files_total,
            bytes_done=bytes_done if sent is None else sent,
            bytes_total=bytes_total,
            uploading=uploading,
            skipped=skipped,
            uploaded=uploaded,
        ))

    def stopped() -> UploadResult:
        return _result(uploaded, skipped, True, files_done, files_total, bytes_done)

    report("", uploading=False)
    for relative, path, size in files:
        if _cancelled(cancel):
            return stopped()
        report(relative, uploading=False)
        try:
            digest = full_hash(path)
        except OSError as exc:
            raise DriveUploadError("Could not read a file in the library.") from exc
        if _cancelled(cancel):
            return stopped()
        if record.decide(relative, size, digest) == DECISION_SKIP:
            store.drop(library, relative)
            files_done += 1
            bytes_total -= size
            skipped += 1
            report(relative, uploading=False)
            continue
        entry = record.entry_for(relative)
        existing_id = entry.drive_file_id if entry is not None else ""

        def on_sent(sent_offset: int, _relative=relative, _base=bytes_done) -> None:
            report(_relative, uploading=True, sent=_base + sent_offset)

        try:
            file_id = _upload_one(
                drive,
                store,
                library,
                path,
                relative,
                size,
                digest,
                record.drive_folder_id,
                existing_id,
                folder_cache,
                chunk,
                cancel,
                on_sent,
            )
        except _UploadCancelled:
            return stopped()
        record.mark_uploaded(relative, size, digest, file_id)
        finished = record.entry_for(relative)
        if finished is not None and any(key in finished.extra for key in _SESSION_FIELDS):
            raise DriveUploadError("A finished upload must not keep a session.")
        try:
            save_sync_record(record, library)
        except OSError as exc:
            raise DriveUploadError(
                "The upload record could not be saved on this computer."
            ) from exc
        store.drop(library, relative)
        files_done += 1
        bytes_done += size
        uploaded += 1
        report(relative, uploading=False)
    return _result(uploaded, skipped, False, files_done, files_total, bytes_done)


def _result(
    uploaded: int,
    skipped: int,
    cancelled: bool,
    files_done: int,
    files_total: int,
    bytes_sent: int,
) -> UploadResult:
    return UploadResult(
        uploaded=uploaded,
        skipped=skipped,
        cancelled=cancelled,
        files_done=files_done,
        files_total=files_total,
        bytes_sent=bytes_sent,
    )


def _upload_one(
    drive: "_Drive",
    store: UploadSessionStore,
    library: Path,
    path: Path,
    relative: str,
    size: int,
    digest: str,
    root_folder_id: str,
    existing_file_id: str,
    folder_cache: dict[tuple[str, str], str],
    chunk_size: int,
    cancel: Callable[[], bool] | None,
    on_sent: Callable[[int], None],
) -> str:
    if _cancelled(cancel):
        raise _UploadCancelled()
    parent_id = _ensure_parent_folders(
        drive, root_folder_id, relative, folder_cache, cancel,
    )
    name = relative.split("/")[-1]
    mime = _mime_for(name)
    stored = store.get(library, relative)
    session_uri = ""
    offset = 0
    if (
        stored is not None
        and stored.size == size
        and stored.content_hash == digest
        and stored.resume_uri
    ):
        try:
            queried = _query_session(drive, stored.resume_uri, size)
        except _SessionExpired:
            queried = None
        if isinstance(queried, str):
            return queried
        if isinstance(queried, int):
            session_uri = stored.resume_uri
            offset = queried
    if not session_uri:
        if _cancelled(cancel):
            raise _UploadCancelled()
        session_uri = _start_session(
            drive,
            name=name,
            parent_id=parent_id,
            size=size,
            mime=mime,
            file_id=existing_file_id,
        )
        store.put(library, relative, size, digest, session_uri)
        offset = 0
    on_sent(min(offset, size))
    try:
        return _send_file(
            drive, path, size, offset, session_uri, chunk_size, cancel, on_sent,
        )
    except _SessionExpired:
        if _cancelled(cancel):
            raise _UploadCancelled()
        session_uri = _start_session(
            drive,
            name=name,
            parent_id=parent_id,
            size=size,
            mime=mime,
            file_id=existing_file_id,
        )
        store.put(library, relative, size, digest, session_uri)
        try:
            return _send_file(
                drive, path, size, 0, session_uri, chunk_size, cancel, on_sent,
            )
        except _SessionExpired as exc:
            raise DriveUploadError("The upload session expired. Try again.") from exc


def _ensure_parent_folders(
    drive: "_Drive",
    root_folder_id: str,
    relative: str,
    cache: dict[tuple[str, str], str],
    cancel: Callable[[], bool] | None,
) -> str:
    parent = root_folder_id
    for name in relative.split("/")[:-1]:
        if _cancelled(cancel):
            raise _UploadCancelled()
        key = (parent, name)
        cached = cache.get(key)
        if cached:
            parent = cached
            continue
        found = _find_child_folder(drive, parent, name)
        if not found:
            found = _create_folder(drive, parent, name)
        cache[key] = found
        parent = found
    return parent


def _find_child_folder(drive: "_Drive", parent_id: str, name: str) -> str:
    query = (
        f"name = '{_escape_query(name)}' and "
        f"mimeType = '{FOLDER_MIME}' and "
        f"'{_escape_query(parent_id)}' in parents and "
        "trashed = false"
    )
    url = FILES_URL + "?" + urllib.parse.urlencode({
        "q": query,
        "fields": "files(id,name,mimeType)",
        "pageSize": "10",
        "spaces": "drive",
    })
    response = drive.call("GET", url, {"Accept": "application/json"}, None)
    _raise_for_status(response)
    payload = _json_object(response)
    files = payload.get("files") or []
    if not isinstance(files, list):
        raise DriveUploadError("Google Drive returned something this app could not read.")
    for item in files:
        if not isinstance(item, dict):
            continue
        if item.get("mimeType") not in (None, FOLDER_MIME):
            continue
        if item.get("name") != name:
            continue
        folder_id = item.get("id")
        if isinstance(folder_id, str) and folder_id and not any(ch.isspace() for ch in folder_id):
            return folder_id
    return ""


def _create_folder(drive: "_Drive", parent_id: str, name: str) -> str:
    url = FILES_URL + "?" + urllib.parse.urlencode({"fields": "id"})
    body = json.dumps({
        "name": name,
        "mimeType": FOLDER_MIME,
        "parents": [parent_id],
    }).encode("utf-8")
    response = drive.call("POST", url, {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=UTF-8",
    }, body)
    _raise_for_status(response)
    folder_id = _json_object(response).get("id")
    if not isinstance(folder_id, str) or not folder_id or any(ch.isspace() for ch in folder_id):
        raise DriveUploadError("Google Drive did not create a folder for the upload.")
    return folder_id


def _start_session(
    drive: "_Drive",
    *,
    name: str,
    parent_id: str,
    size: int,
    mime: str,
    file_id: str,
) -> str:
    """Open one resumable session. An existing file id updates that file."""
    if file_id:
        quoted = urllib.parse.quote(file_id, safe="")
        url = f"{UPLOAD_URL}/{quoted}?uploadType=resumable&fields=id"
        method = "PATCH"
        metadata = {"name": name}
    else:
        url = f"{UPLOAD_URL}?uploadType=resumable&fields=id"
        method = "POST"
        metadata = {"name": name, "parents": [parent_id]}
    body = json.dumps(metadata).encode("utf-8")
    response = drive.call(method, url, {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": mime,
        "X-Upload-Content-Length": str(size),
    }, body)
    _raise_for_status(response)
    return _require_https_uri(response.header("location"))


def _query_session(drive: "_Drive", resume_uri: str, size: int):
    """Return a file id when the session already finished, or the next offset.

    ``None`` is not used. A string is a finished file id. An int is the
    number of bytes Drive already has. ``_SessionExpired`` means start over.
    """
    response = drive.call("PUT", resume_uri, {
        "Content-Length": "0",
        "Content-Range": f"bytes */{size}",
        "Content-Type": "application/octet-stream",
    }, b"")
    if response.status in (200, 201):
        return _file_id_from(response)
    if response.status == 308:
        if size == 0:
            return 0
        return _offset_from_range(response.header("range"), fallback=0)
    if response.status in (404, 410):
        raise _SessionExpired()
    _raise_for_status(response)
    raise DriveUploadError(GENERIC_UPLOAD_MESSAGE)


def _send_file(
    drive: "_Drive",
    path: Path,
    size: int,
    offset: int,
    resume_uri: str,
    chunk_size: int,
    cancel: Callable[[], bool] | None,
    on_sent: Callable[[int], None],
) -> str:
    if size == 0:
        if _cancelled(cancel):
            raise _UploadCancelled()
        response = drive.call("PUT", resume_uri, {
            "Content-Length": "0",
            "Content-Range": "bytes */0",
            "Content-Type": "application/octet-stream",
        }, b"")
        if response.status in (200, 201):
            on_sent(0)
            return _file_id_from(response)
        if response.status in (404, 410):
            raise _SessionExpired()
        _raise_for_status(response)
        raise DriveUploadError("The upload did not finish.")
    guard = 0
    limit = max(8, (size // chunk_size) + 8)
    try:
        handle = open(path, "rb")
    except OSError as exc:
        raise DriveUploadError("Could not read a file in the library.") from exc
    with handle:
        while offset < size:
            if _cancelled(cancel):
                raise _UploadCancelled()
            guard += 1
            if guard > limit:
                raise DriveUploadError("The upload did not finish.")
            handle.seek(offset)
            chunk = handle.read(min(chunk_size, size - offset))
            if not chunk:
                raise DriveUploadError("Could not read a file in the library.")
            end = offset + len(chunk) - 1
            response = drive.call("PUT", resume_uri, {
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{end}/{size}",
                "Content-Type": "application/octet-stream",
            }, chunk)
            if response.status in (200, 201):
                on_sent(size)
                return _file_id_from(response)
            if response.status == 308:
                offset = _offset_from_range(response.header("range"), fallback=end + 1)
                if offset < 0:
                    offset = 0
                on_sent(min(offset, size))
                continue
            if response.status in (404, 410):
                raise _SessionExpired()
            _raise_for_status(response)
            raise DriveUploadError(GENERIC_UPLOAD_MESSAGE)
    raise DriveUploadError("The upload did not finish.")


class _Drive:
    """Adds the bearer token. The token is a header, never part of an error."""

    def __init__(self, access_token, request, timeout: float):
        self._access_token = access_token
        self._request = request
        self._timeout = timeout

    def call(self, method: str, url: str, headers: dict, body: bytes | None) -> DriveResponse:
        token = self._token()
        sent = {
            "Authorization": "Bearer " + token,
            "User-Agent": "MediaOrganizer",
        }
        sent.update(headers)
        try:
            response = self._request(method, url, sent, body, self._timeout)
        except DriveUploadError:
            raise
        except AssertionError:
            raise
        except Exception as exc:
            raise DriveUploadError(_message_for_caller_failure(exc)) from exc
        if not isinstance(response, DriveResponse):
            raise DriveUploadError("Google Drive returned something this app could not read.")
        return response

    def _token(self) -> str:
        try:
            value = self._access_token() if callable(self._access_token) else self._access_token
        except DriveUploadError:
            raise
        except AssertionError:
            raise
        except Exception as exc:
            raise DriveUploadError(_message_for_caller_failure(exc)) from exc
        if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
            raise DriveUploadError("Sign in to Google Drive first.")
        return value


def _raise_for_status(response: DriveResponse) -> None:
    if response.status in (200, 201):
        return
    raise DriveUploadError(_drive_failure_message(response))


def _drive_failure_message(response: DriveResponse) -> str:
    """Plain message for one failed Drive response. The body is not included."""
    kind = _failure_kind(response)
    if kind == "quota":
        return QUOTA_MESSAGE
    if kind == "auth":
        return AUTH_MESSAGE
    if kind == "offline":
        return OFFLINE_MESSAGE
    return GENERIC_UPLOAD_MESSAGE


def _failure_kind(response: DriveResponse) -> str:
    status = response.status
    reasons = _error_tokens(response.body)
    text = _body_text(response.body)
    if status == 401 or (reasons & _AUTH_REASONS and not (reasons & _QUOTA_REASONS)):
        return "auth"
    if reasons & _RATE_REASONS and not (reasons & _QUOTA_REASONS):
        return "generic"
    if status == 507 or reasons & _QUOTA_REASONS or _contains_any(text, _QUOTA_PHRASES):
        return "quota"
    if _contains_any(text, _AUTH_PHRASES):
        return "auth"
    if status <= 0 or status in _OFFLINE_STATUSES:
        return "offline"
    return "generic"


def _message_for_caller_failure(exc: BaseException) -> str:
    """Map a raised network or auth failure. The exception text is not shown."""
    if _is_offline_exception(exc):
        return OFFLINE_MESSAGE
    lowered = str(exc).lower()
    if any(phrase in lowered for phrase in _OFFLINE_CALLER_PHRASES):
        return OFFLINE_MESSAGE
    leaked = any(mark in lowered for mark in ("access_token", "refresh_token", "client_secret", "bearer "))
    if any(phrase in lowered for phrase in _AUTH_CALLER_PHRASES):
        return AUTH_MESSAGE
    if leaked:
        return GENERIC_UPLOAD_MESSAGE
    from .google_auth import GoogleAuthError

    if isinstance(exc, GoogleAuthError):
        text = str(exc).strip()
        if text and "traceback" not in text.lower() and not any(ch.isdigit() for ch in text):
            return text
    return GENERIC_UPLOAD_MESSAGE


def _is_offline_exception(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _offline_type(current):
            return True
        current = current.__cause__
    return False


def _offline_type(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, socket.gaierror, ssl.SSLError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, OSError) and exc.errno in _OFFLINE_ERRNOS:
        return True
    return False


def _error_tokens(body: bytes) -> set[str]:
    found: set[str] = set()
    data = _parse_error_json(body)
    if data is not None:
        _collect_tokens(data, found)
        return found
    compact = _body_text(body).replace(" ", "")
    for token in _QUOTA_REASONS | _AUTH_REASONS | _RATE_REASONS:
        if token in compact:
            found.add(token)
    return found


def _parse_error_json(body: bytes):
    if not body:
        return None
    try:
        data = json.loads(body[:8192].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    return data


def _collect_tokens(value, found: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _REASON_KEYS and isinstance(child, str):
                token = _norm_token(child)
                if token:
                    found.add(token)
            _collect_tokens(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_tokens(child, found)


def _norm_token(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum() or ch == "_")


def _body_text(body: bytes) -> str:
    if not body:
        return ""
    return body[:8192].decode("utf-8", errors="ignore").lower()


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _json_object(response: DriveResponse) -> dict:
    if not response.body:
        return {}
    try:
        data = json.loads(response.body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DriveUploadError("Google Drive returned something this app could not read.") from exc
    if not isinstance(data, dict):
        raise DriveUploadError("Google Drive returned something this app could not read.")
    return data


def _file_id_from(response: DriveResponse) -> str:
    file_id = _json_object(response).get("id")
    if not isinstance(file_id, str) or not file_id or any(ch.isspace() for ch in file_id):
        raise DriveUploadError("Google Drive did not finish the upload.")
    return file_id


def _offset_from_range(range_header: str, fallback: int) -> int:
    if not range_header:
        return fallback
    match = None
    for match in _RANGE_RE.finditer(range_header):
        pass
    if match is None:
        return fallback
    return int(match.group(2)) + 1


def _mime_for(name: str) -> str:
    guessed, _encoding = mimetypes.guess_type(name)
    if guessed:
        return guessed
    return "application/octet-stream"


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _require_https_uri(value: str) -> str:
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        raise DriveUploadError("Google Drive did not start the upload.")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise DriveUploadError("Google Drive did not start the upload.")
    return value


def _cancelled(cancel: Callable[[], bool] | None) -> bool:
    return bool(cancel and cancel())


def _header_map(headers) -> dict:
    mapping = {}
    if headers is None:
        return mapping
    for key in headers.keys():
        if not key:
            continue
        mapping[key.lower()] = headers.get(key) or ""
    return mapping


def _library_key(library_dir: Path | str) -> str:
    return str(Path(library_dir).resolve())


def _session_key(library_dir: Path | str, local_path: str) -> tuple[str, str]:
    return (_library_key(library_dir), normalize_local_path(local_path))


def _load_sessions(path: Path) -> dict[tuple[str, str], StoredSession]:
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != SESSIONS_VERSION:
        return {}
    _reject_token_keys(data)
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        return {}
    sessions: dict[tuple[str, str], StoredSession] = {}
    for item in raw_entries:
        parsed = _parse_stored(item)
        if parsed is None:
            continue
        sessions[(parsed.library, parsed.local_path)] = parsed
    return sessions


def _parse_stored(item: object) -> Optional[StoredSession]:
    if not isinstance(item, dict):
        return None
    library = item.get("library")
    local_path = item.get("local_path")
    size = item.get("size")
    digest = item.get("content_hash")
    resume_uri = item.get("resume_uri")
    if not isinstance(library, str) or not library:
        return None
    if not isinstance(local_path, str) or not isinstance(digest, str):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    if not isinstance(resume_uri, str):
        return None
    try:
        rel = normalize_local_path(local_path)
        uri = _require_https_uri(resume_uri)
    except (ValueError, DriveUploadError):
        return None
    return StoredSession(
        library=str(Path(library).resolve()),
        local_path=rel,
        size=size,
        content_hash=digest,
        resume_uri=uri,
    )


def _save_sessions(path: Path, sessions: dict[tuple[str, str], StoredSession]) -> None:
    if ".media_organizer" in path.parts or path.name == "drive_sync.json":
        raise DriveUploadError(
            "Upload sessions are stored on this computer, not in the library."
        )
    payload_obj = {
        "version": SESSIONS_VERSION,
        "entries": [
            {
                "library": sessions[key].library,
                "local_path": sessions[key].local_path,
                "size": sessions[key].size,
                "content_hash": sessions[key].content_hash,
                "resume_uri": sessions[key].resume_uri,
            }
            for key in sorted(sessions)
        ],
    }
    _reject_token_keys(payload_obj)
    if not payload_obj["entries"]:
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            pass
        else:
            return
    payload = json.dumps(payload_obj, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    apply_user_only_permissions(path.parent)
    tmp = path.with_name(path.name + ".part")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    apply_user_only_permissions(tmp)
    os.replace(tmp, path)
    apply_user_only_permissions(path)


def _reject_token_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in _TOKEN_KEYS:
                raise DriveUploadError("Upload sessions must not store a Google token.")
            _reject_token_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_token_keys(child)
