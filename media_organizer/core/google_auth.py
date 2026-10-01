"""Google sign-in for the desktop app. No upload.

Authorization Code with PKCE, loopback redirect on ``127.0.0.1``, and the
single scope ``https://www.googleapis.com/auth/drive.file``. The app does
not use a client secret.

Tokens are written only under the per-user Google directory (next to
``crash.log``). They are never written into an organized library, the sync
record, a log, or this repository. Sign-out deletes the token file.
Resumable upload sessions live in that same directory, not in the library.

The desktop client id is public. Set ``MEDIA_ORGANIZER_GOOGLE_CLIENT_ID``,
or replace ``BUNDLED_CLIENT_ID`` below with the Desktop OAuth client id
from Google Cloud Console. The placeholder shipped here is not a client id.
Do not put a client secret in this file.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .drive_folders import (
    FOLDER_MIME,
    DriveFolder,
    DriveFolderError,
    fetch_drive_folder,
    list_drive_folders,
    parse_one_folder_id,
)

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ABOUT_URL = (
    "https://www.googleapis.com/drive/v3/about"
    "?fields=user(emailAddress,displayName)"
)

CLIENT_ID_ENV = "MEDIA_ORGANIZER_GOOGLE_CLIENT_ID"
# Public desktop client id. Not a secret: PKCE replaces the client secret.
# Berk replaces this placeholder with his Google Cloud Desktop OAuth client
# id, or sets MEDIA_ORGANIZER_GOOGLE_CLIENT_ID. Leave the secret out.
BUNDLED_CLIENT_ID = "REPLACE_WITH_YOUR_DESKTOP_CLIENT_ID"

CLIENT_ID_HELP = (
    "Google sign-in needs a desktop OAuth client id. "
    "Set MEDIA_ORGANIZER_GOOGLE_CLIENT_ID to the Desktop client id "
    "from Google Cloud Console, or replace the placeholder in "
    "media_organizer/core/google_auth.py. "
    "No client secret is required."
)

TOKEN_FILENAME = "token.json"
_UNCONFIGURED_CLIENT_IDS = frozenset({"", BUNDLED_CLIENT_ID})
_BROAD_DRIVE_SCOPES = frozenset({
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.appdata",
    "https://www.googleapis.com/auth/drive.scripts",
})
_DROPPED_TOKEN_KEYS = frozenset({"client_secret", "client_secret_json"})
_MAX_TOKEN_BYTES = 100_000
_DEFAULT_TIMEOUT_S = 180.0

_PAGE_SIGNED_IN = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Media Organizer</title></head>
<body><p>Signed in. You can close this window and return to Media Organizer.</p>
</body></html>
"""
_PAGE_FOLDER = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Media Organizer</title></head>
<body><p>Folder chosen. You can close this window and return to Media Organizer.</p>
</body></html>
"""
_PAGE_CANCELLED = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Media Organizer</title></head>
<body><p>Nothing was chosen. You can close this window and return to Media Organizer.</p>
</body></html>
"""


class GoogleAuthError(Exception):
    """Sign-in failed in a way the user can be told about.

    Messages stay free of access tokens, refresh tokens, and authorization
    codes.
    """


@dataclass(frozen=True)
class GoogleAccount:
    """The signed-in person, as much as Drive's about call returned."""

    email: str = ""
    display_name: str = ""

    @property
    def label(self) -> str:
        if self.email:
            return self.email
        if self.display_name:
            return self.display_name
        return "Signed in"


@dataclass
class OAuthCallback:
    """What the loopback redirect received. Not written to disk."""

    code: str = ""
    state: str = ""
    error: str = ""
    picked_file_ids: str = ""
    scope: str = ""


def user_data_root() -> Path:
    """Same per-user root as ``crash.log``."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / ".local" / "share"


def google_dir() -> Path:
    """Directory that holds the Google token and nothing from the library."""
    return user_data_root() / "MediaOrganizer" / "google"


def token_file_path() -> Path:
    """Local token file. Readable only by the user once it has been saved."""
    return google_dir() / TOKEN_FILENAME


def resolve_client_id() -> str:
    """Public desktop client id, or ``""`` when it has not been configured.

    The environment variable wins. The bundled placeholder is not a client id.
    """
    raw = os.environ.get(CLIENT_ID_ENV, "").strip()
    if raw:
        if raw in _UNCONFIGURED_CLIENT_IDS:
            return ""
        return _validate_client_id(raw)
    bundled = BUNDLED_CLIENT_ID.strip()
    if bundled in _UNCONFIGURED_CLIENT_IDS:
        return ""
    return _validate_client_id(bundled)


def make_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, S256 code_challenge)``."""
    verifier = secrets.token_urlsafe(64)
    if len(verifier) < 43:
        verifier = (verifier + secrets.token_urlsafe(32))[:128]
    verifier = verifier[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    pick_folder: bool = False,
) -> str:
    """Authorization Code URL. Loopback only, one scope, no client secret."""
    _validate_client_id(client_id)
    _require_loopback_redirect(redirect_uri)
    if not state or any(ch.isspace() for ch in state):
        raise GoogleAuthError("Sign-in could not be started.")
    if not code_challenge or any(ch.isspace() for ch in code_challenge):
        raise GoogleAuthError("Sign-in could not be started.")
    fields = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": DRIVE_FILE_SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    if pick_folder:
        # Google's desktop Picker. drive.file can see the folder the user
        # selects here. This does not upload.
        fields["trigger_onepick"] = "true"
        fields["allow_folder_selection"] = "true"
        fields["mimetypes"] = FOLDER_MIME
    return AUTH_URL + "?" + urllib.parse.urlencode(fields)


def apply_user_only_permissions(path: Path) -> None:
    """Make ``path`` readable and writable only by the current user."""
    path = Path(path)
    if os.name == "nt":
        _lock_down_windows(path)
        return
    mode = 0o600 if path.is_file() else 0o700
    os.chmod(path, mode)


def save_token_file(data: dict, path: Path | None = None) -> Path:
    """Write the token file atomically, readable only by the user.

    ``client_secret`` keys are dropped if a caller ever includes them.
    The file is refused inside a library's ``.media_organizer`` folder.
    """
    if not isinstance(data, dict):
        raise GoogleAuthError("Could not save the Google sign-in.")
    path = Path(path) if path is not None else token_file_path()
    _refuse_library_token_path(path)
    cleaned = {
        key: value
        for key, value in data.items()
        if str(key).strip().lower() not in _DROPPED_TOKEN_KEYS
    }
    if not cleaned.get("access_token") and not cleaned.get("refresh_token"):
        raise GoogleAuthError("Could not save the Google sign-in.")
    payload = json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    apply_user_only_permissions(path.parent)
    tmp = path.with_name(path.name + ".part")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fd = -1
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
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
    _fsync_dir(path.parent)
    return path


def load_token_file(path: Path | None = None) -> dict | None:
    """Return the saved token, or None when there is nothing usable.

    A corrupt file is treated as signed out. It is not deleted here.
    """
    path = Path(path) if path is not None else token_file_path()
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > _MAX_TOKEN_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not _nonempty_token(access) and not _nonempty_token(refresh):
        return None
    return data


def delete_token_file(path: Path | None = None) -> None:
    """Sign-out: remove the token file and a leftover partial write."""
    path = Path(path) if path is not None else token_file_path()
    _refuse_library_token_path(path)
    for candidate in (path, path.with_name(path.name + ".part")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise GoogleAuthError("Could not forget the Google sign-in on this computer.") from exc


def public_error_message(exc: BaseException) -> str:
    """A message safe to show. Token material is not included."""
    from .drive_sync import SyncRecordError
    from .drive_upload import DriveUploadError

    if isinstance(exc, (GoogleAuthError, DriveFolderError, DriveUploadError, SyncRecordError)):
        text = str(exc).strip() or "Something went wrong talking to Google Drive."
    else:
        text = "Something went wrong talking to Google Drive."
    lowered = text.lower()
    if any(mark in lowered for mark in ("access_token", "refresh_token", "client_secret", "bearer ")):
        return "Something went wrong talking to Google Drive."
    return text


def default_post_form(url: str, fields: dict, timeout: float = 30) -> dict:
    """POST form fields to Google's token endpoint. Refuses a client secret."""
    if not isinstance(fields, dict) or "client_secret" in fields or "client_secret_json" in fields:
        raise GoogleAuthError("This app does not use a client secret.")
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("User-Agent", "MediaOrganizer")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise GoogleAuthError("Google did not accept the sign-in.") from exc
    except urllib.error.URLError as exc:
        raise GoogleAuthError("Could not reach Google. Check your internet connection.") from exc
    return _parse_json_object(raw)


def default_get_json(url: str, access_token: str, timeout: float = 30) -> dict:
    """GET a Drive JSON URL. The token is a header, never part of the message."""
    if not _nonempty_token(access_token):
        raise GoogleAuthError("Sign in to Google Drive again.")
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", "Bearer " + access_token)
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "MediaOrganizer")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise GoogleAuthError("Sign in to Google Drive again.") from exc
        raise GoogleAuthError("Google Drive did not answer that request.") from exc
    except urllib.error.URLError as exc:
        raise GoogleAuthError(
            "Could not reach Google Drive. Check your internet connection."
        ) from exc
    return _parse_json_object(raw)


class GoogleSession:
    """Sign-in, sign-out, and the calls the folder picker needs.

    ``post_form`` and ``get_json`` are injected in tests so nothing here has
    to reach Google.
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        token_path: Path | None = None,
        post_form=None,
        get_json=None,
        open_browser=None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ):
        self._client_id_override = client_id
        self._token_path = Path(token_path) if token_path is not None else None
        self._post_form = post_form or default_post_form
        self._get_json = get_json or default_get_json
        self._open_browser = open_browser or webbrowser.open
        self.timeout = timeout

    def signed_in_account(self) -> GoogleAccount | None:
        """Account from the local token file. Does not call Google."""
        data = load_token_file(self._path())
        if data is None:
            return None
        return GoogleAccount(
            email=_text(data.get("email")),
            display_name=_text(data.get("display_name")),
        )

    def sign_in(self) -> GoogleAccount:
        """Open the browser, take the loopback redirect, and store the token."""
        client_id = self._require_client_id()
        verifier, challenge = make_pkce_pair()
        state = secrets.token_urlsafe(32)
        callback, redirect_uri = self._authorize(
            client_id, challenge, state, pick_folder=False,
        )
        self._store_from_code(client_id, callback.code, verifier, redirect_uri)
        email, name = self._try_account()
        self._write_account(email, name)
        return GoogleAccount(email=email, display_name=name)

    def sign_out(self) -> None:
        """Delete the local token file. The sync record is left alone."""
        delete_token_file(self._path())

    def access_token(self) -> str:
        """Access token for Drive calls, refreshed when it is about to expire."""
        return self._access_token()

    def list_folders(self) -> list[DriveFolder]:
        """Folders this app can already see. Does not create or upload files."""
        return list_drive_folders(self._access_token(), get_json=self._get_json)

    def pick_drive_folder(self) -> DriveFolder:
        """Google's desktop folder Picker. Returns one folder. Does not upload."""
        client_id = self._require_client_id()
        verifier, challenge = make_pkce_pair()
        state = secrets.token_urlsafe(32)
        callback, redirect_uri = self._authorize(
            client_id, challenge, state, pick_folder=True,
        )
        self._store_from_code(client_id, callback.code, verifier, redirect_uri)
        try:
            folder_id = parse_one_folder_id(callback.picked_file_ids)
        except DriveFolderError:
            raise
        except ValueError as exc:
            raise DriveFolderError("Choose one Drive folder.") from exc
        try:
            return fetch_drive_folder(self._access_token(), folder_id, get_json=self._get_json)
        except DriveFolderError:
            raise
        except GoogleAuthError:
            return DriveFolder(id=folder_id, name=folder_id)

    def _authorize(self, client_id, code_challenge, state, *, pick_folder: bool):
        server = _RedirectServer(self.timeout, pick_folder=pick_folder)
        redirect_uri = server.redirect_uri
        server.start()
        try:
            url = build_authorization_url(
                client_id=client_id,
                redirect_uri=redirect_uri,
                state=state,
                code_challenge=code_challenge,
                pick_folder=pick_folder,
            )
            self._launch_browser(url)
            callback = server.wait()
        finally:
            server.close()
        self._check_callback(callback, state, pick_folder=pick_folder)
        return callback, redirect_uri

    def _store_from_code(self, client_id, code, verifier, redirect_uri) -> None:
        previous = load_token_file(self._path()) or {}
        body = self._post_form(TOKEN_URL, {
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })
        save_token_file(_normalize_token(body, previous=previous), self._path())

    def _access_token(self) -> str:
        data = load_token_file(self._path())
        if data is None or not _nonempty_token(data.get("access_token")):
            raise GoogleAuthError("Sign in to Google Drive first.")
        expires_at = _as_int(data.get("expires_at"))
        if expires_at > time.time() + 30:
            return data["access_token"]
        refresh = data.get("refresh_token") or ""
        if not _nonempty_token(refresh):
            raise GoogleAuthError("Sign in to Google Drive again.")
        client_id = self._require_client_id()
        body = self._post_form(TOKEN_URL, {
            "client_id": client_id,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        })
        saved = _normalize_token(body, previous=data)
        save_token_file(saved, self._path())
        return saved["access_token"]

    def _try_account(self) -> tuple[str, str]:
        try:
            payload = self._get_json(ABOUT_URL, self._access_token())
        except GoogleAuthError:
            return "", ""
        user = payload.get("user") if isinstance(payload, dict) else None
        if not isinstance(user, dict):
            return "", ""
        return _text(user.get("emailAddress")), _text(user.get("displayName"))

    def _write_account(self, email: str, name: str) -> None:
        data = load_token_file(self._path())
        if data is None:
            return
        data["email"] = email
        data["display_name"] = name
        save_token_file(data, self._path())

    def _require_client_id(self) -> str:
        if self._client_id_override is None:
            client_id = resolve_client_id()
        else:
            override = self._client_id_override.strip()
            client_id = "" if override in _UNCONFIGURED_CLIENT_IDS else _validate_client_id(override)
        if not client_id:
            raise GoogleAuthError(CLIENT_ID_HELP)
        return client_id

    def _path(self) -> Path:
        if self._token_path is not None:
            return self._token_path
        return token_file_path()

    def _launch_browser(self, url: str) -> None:
        try:
            opened = self._open_browser(url)
        except GoogleAuthError:
            raise
        except Exception as exc:
            raise GoogleAuthError("Could not open the browser for Google sign-in.") from exc
        if opened is False:
            raise GoogleAuthError("Could not open the browser for Google sign-in.")

    @staticmethod
    def _check_callback(callback: OAuthCallback | None, state: str, *, pick_folder: bool) -> None:
        if callback is None:
            if pick_folder:
                raise GoogleAuthError(
                    "The browser did not return to Media Organizer. "
                    "No Drive folder was chosen."
                )
            raise GoogleAuthError(
                "The browser did not return to Media Organizer. Sign-in was cancelled."
            )
        if callback.error:
            if callback.error == "access_denied":
                if pick_folder:
                    raise GoogleAuthError("No Drive folder was chosen.")
                raise GoogleAuthError("Sign-in was cancelled.")
            raise GoogleAuthError("Google did not finish signing in.")
        if callback.scope:
            _require_drive_file_scope(callback.scope)
        if not callback.code or any(ch.isspace() for ch in callback.code):
            raise GoogleAuthError("Google did not finish signing in.")
        if callback.state != state:
            raise GoogleAuthError("Sign-in could not be verified. Please try again.")


class _RedirectServer:
    """One-shot loopback on 127.0.0.1. The authorization code is not logged."""

    def __init__(self, timeout: float, *, pick_folder: bool):
        self.timeout = timeout
        self.pick_folder = pick_folder
        self._callback: OAuthCallback | None = None
        self._event = threading.Event()
        self._thread: threading.Thread | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path not in ("/", ""):
                    _reply(self, 404, b"")
                    return
                query = urllib.parse.parse_qs(parsed.query)
                callback = OAuthCallback(
                    code=_first(query, "code"),
                    state=_first(query, "state"),
                    error=_first(query, "error"),
                    picked_file_ids=_first(query, "picked_file_ids"),
                    scope=_first(query, "scope"),
                )
                if not callback.code and not callback.error:
                    _reply(self, 204, b"")
                    return
                owner._callback = callback
                if callback.error:
                    page = _PAGE_CANCELLED
                elif owner.pick_folder:
                    page = _PAGE_FOLDER
                else:
                    page = _PAGE_SIGNED_IN
                _reply(self, 200, page.encode("utf-8"))
                owner._event.set()

        try:
            self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        except OSError as exc:
            raise GoogleAuthError("Could not start sign-in on this computer.") from exc
        port = self._httpd.server_address[1]
        self.redirect_uri = f"http://127.0.0.1:{port}/"

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._serve, name="google-oauth-loopback", daemon=True,
        )
        self._thread.start()

    def wait(self) -> OAuthCallback | None:
        self._event.wait(self.timeout + 0.5)
        return self._callback

    def close(self) -> None:
        try:
            self._httpd.server_close()
        except OSError:
            pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _serve(self) -> None:
        deadline = time.monotonic() + self.timeout
        while not self._event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._httpd.timeout = remaining
            try:
                self._httpd.handle_request()
            except OSError:
                return


def _reply(handler: BaseHTTPRequestHandler, status: int, body: bytes) -> None:
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        if body:
            handler.wfile.write(body)
    except OSError:
        return


def _first(query: dict, key: str) -> str:
    values = query.get(key) or []
    if not values:
        return ""
    value = values[0]
    return value if isinstance(value, str) else ""


def _validate_client_id(value: str) -> str:
    if not isinstance(value, str):
        raise GoogleAuthError("The Google client id is not valid.")
    text = value.strip()
    if not text or any(ch.isspace() for ch in text):
        raise GoogleAuthError("The Google client id is not valid.")
    return text


def _require_loopback_redirect(redirect_uri: str) -> None:
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise GoogleAuthError("Sign-in must return to this computer.")
    if parsed.username or parsed.password:
        raise GoogleAuthError("Sign-in must return to this computer.")


def _require_drive_file_scope(scope: str) -> None:
    parts = [part for part in scope.replace(",", " ").split() if part]
    if any(part in _BROAD_DRIVE_SCOPES for part in parts):
        raise GoogleAuthError("Google offered broader Drive access than this app uses.")
    if DRIVE_FILE_SCOPE not in parts:
        raise GoogleAuthError("Google did not grant access to files this app works with.")


def _normalize_token(body: dict, *, previous: dict | None) -> dict:
    if not isinstance(body, dict):
        raise GoogleAuthError("Google did not finish signing in.")
    access = body.get("access_token")
    if not _nonempty_token(access):
        raise GoogleAuthError("Google did not finish signing in.")
    scope = body.get("scope")
    if scope is not None:
        _require_drive_file_scope(str(scope))
    refresh = body.get("refresh_token")
    if refresh is None and previous:
        refresh = previous.get("refresh_token")
    if refresh is None:
        refresh = ""
    if refresh and not _nonempty_token(refresh):
        raise GoogleAuthError("Google did not finish signing in.")
    expires_in = _as_int(body.get("expires_in", 3600))
    if expires_in < 0:
        expires_in = 0
    stored = {
        "access_token": access,
        "refresh_token": refresh or "",
        "expires_at": int(time.time()) + expires_in,
        "token_type": "Bearer",
        "scope": DRIVE_FILE_SCOPE,
    }
    if previous:
        if _text(previous.get("email")):
            stored["email"] = _text(previous.get("email"))
        if _text(previous.get("display_name")):
            stored["display_name"] = _text(previous.get("display_name"))
    return stored


def _parse_json_object(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GoogleAuthError("Google returned something this app could not read.") from exc
    if not isinstance(data, dict):
        raise GoogleAuthError("Google returned something this app could not read.")
    return data


def _nonempty_token(value: object) -> bool:
    return isinstance(value, str) and bool(value) and not any(ch.isspace() for ch in value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _as_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _refuse_library_token_path(path: Path) -> None:
    if ".media_organizer" in path.parts or path.name in {"drive_sync.json", "operations.json"}:
        raise GoogleAuthError("Google sign-in is stored on this computer, not in the library.")


def _lock_down_windows(path: Path) -> None:
    """Drop inherited ACEs and grant the current user full control.

    ``icacls`` output is discarded so a token path is not written to a log.
    """
    import subprocess
    user = os.environ.get("USERNAME")
    if not user:
        return
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(F)"],
        check=False,
        capture_output=True,
        text=True,
    )


def _fsync_dir(directory: Path) -> None:
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
