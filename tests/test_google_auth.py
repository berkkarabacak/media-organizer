"""Google sign-in and the local token file. No real Google account or network."""

import ast
import base64
import hashlib
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from media_organizer.core.drive_folders import FOLDER_MIME
from media_organizer.core.google_auth import (
    BUNDLED_CLIENT_ID,
    CLIENT_ID_ENV,
    CLIENT_ID_HELP,
    DRIVE_FILE_SCOPE,
    TOKEN_URL,
    GoogleAuthError,
    GoogleSession,
    apply_user_only_permissions,
    build_authorization_url,
    default_post_form,
    delete_token_file,
    google_dir,
    load_token_file,
    make_pkce_pair,
    public_error_message,
    resolve_client_id,
    save_token_file,
    token_file_path,
)


def _qs(url: str) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def _open_and_return(query: dict):
    """Browser stand-in: hit the loopback redirect this process is serving."""
    seen = {}
    errors = []

    def open_browser(url: str):
        seen["url"] = url
        parsed = _qs(url)
        redirect = parsed["redirect_uri"][0]
        target = redirect + "?" + urllib.parse.urlencode(query(parsed))

        def hit():
            last = None
            for _ in range(40):
                try:
                    with urllib.request.urlopen(target, timeout=2) as response:
                        seen["body"] = response.read().decode("utf-8")
                    return
                except Exception as exc:
                    last = exc
                    time.sleep(0.05)
            errors.append(last)

        threading.Thread(target=hit, daemon=True).start()
        return True

    return open_browser, seen, errors


def _token_body(**extra):
    body = {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": DRIVE_FILE_SCOPE,
    }
    body.update(extra)
    return body


class TestClientId:
    def test_placeholder_is_not_configured(self, monkeypatch):
        monkeypatch.delenv(CLIENT_ID_ENV, raising=False)
        assert BUNDLED_CLIENT_ID == "REPLACE_WITH_YOUR_DESKTOP_CLIENT_ID"
        assert "secret" not in BUNDLED_CLIENT_ID.lower()
        assert resolve_client_id() == ""

    def test_environment_variable_wins(self, monkeypatch):
        monkeypatch.setenv(CLIENT_ID_ENV, "desktop-client-id")
        assert resolve_client_id() == "desktop-client-id"

    def test_placeholder_in_the_environment_stays_unconfigured(self, monkeypatch):
        monkeypatch.setenv(CLIENT_ID_ENV, BUNDLED_CLIENT_ID)
        assert resolve_client_id() == ""

    def test_sign_in_without_a_client_id_does_not_open_a_browser(self):
        opened = []
        session = GoogleSession(
            client_id="",
            open_browser=lambda url: opened.append(url),
            timeout=1,
        )
        with pytest.raises(GoogleAuthError) as caught:
            session.sign_in()
        assert str(caught.value) == CLIENT_ID_HELP
        assert opened == []


class TestPkceAndUrl:
    def test_pkce_challenge_is_s256(self):
        verifier, challenge = make_pkce_pair()
        assert 43 <= len(verifier) <= 128
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        assert challenge == expected
        assert "=" not in challenge

    def test_authorization_url_is_loopback_pkce_and_one_scope(self):
        _verifier, challenge = make_pkce_pair()
        url = build_authorization_url(
            client_id="desktop-client-id",
            redirect_uri="http://127.0.0.1:8765/",
            state="state-1",
            code_challenge=challenge,
        )
        query = _qs(url)
        assert query["scope"] == [DRIVE_FILE_SCOPE]
        assert query["response_type"] == ["code"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"] == [challenge]
        assert query["redirect_uri"] == ["http://127.0.0.1:8765/"]
        assert query["access_type"] == ["offline"]
        assert "client_secret" not in url
        assert "trigger_onepick" not in query

    def test_folder_picker_url_asks_for_one_folder(self):
        _verifier, challenge = make_pkce_pair()
        url = build_authorization_url(
            client_id="desktop-client-id",
            redirect_uri="http://127.0.0.1:8765/",
            state="state-1",
            code_challenge=challenge,
            pick_folder=True,
        )
        query = _qs(url)
        assert query["scope"] == [DRIVE_FILE_SCOPE]
        assert query["trigger_onepick"] == ["true"]
        assert query["allow_folder_selection"] == ["true"]
        assert query["prompt"] == ["consent"]
        assert query["mimetypes"] == [FOLDER_MIME]
        assert "allow_multiple" not in query
        assert "client_secret" not in url

    def test_redirect_must_be_loopback(self):
        _verifier, challenge = make_pkce_pair()
        for redirect in ("https://example.com/callback", "http://localhost:8765/"):
            with pytest.raises(GoogleAuthError):
                build_authorization_url(
                    client_id="desktop-client-id",
                    redirect_uri=redirect,
                    state="state-1",
                    code_challenge=challenge,
                )

    def test_post_refuses_a_client_secret_without_calling_google(self, monkeypatch):
        def boom(*_args, **_kwargs):
            raise AssertionError("network")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        with pytest.raises(GoogleAuthError):
            default_post_form(TOKEN_URL, {"client_id": "desktop-client-id", "client_secret": "nope"})


class TestTokenFile:
    def test_path_follows_localappdata_and_sits_beside_the_crash_log(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        from media_organizer.crashlog import crash_log_path

        assert google_dir() == tmp_path / "MediaOrganizer" / "google"
        assert token_file_path() == google_dir() / "token.json"
        assert google_dir().parent == crash_log_path().parent

    def test_path_falls_back_when_localappdata_is_unset(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert google_dir() == tmp_path / ".local" / "share" / "MediaOrganizer" / "google"

    def test_save_is_user_only_even_with_a_loose_umask(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        previous = os.umask(0)
        try:
            path = save_token_file({
                "access_token": "test-access-token",
                "refresh_token": "test-refresh-token",
                "expires_at": 10,
                "token_type": "Bearer",
                "scope": DRIVE_FILE_SCOPE,
                "client_secret": "must-not-be-stored",
            })
        finally:
            os.umask(previous)
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert "client_secret" not in saved
        assert saved["refresh_token"] == "test-refresh-token"

    def test_permission_helper_locks_a_file_and_a_directory(self, tmp_path):
        folder = tmp_path / "google"
        folder.mkdir()
        handle = folder / "token.json"
        handle.write_text("{}", encoding="utf-8")
        os.chmod(folder, 0o755)
        os.chmod(handle, 0o644)
        apply_user_only_permissions(folder)
        apply_user_only_permissions(handle)
        assert folder.stat().st_mode & 0o077 == 0
        assert handle.stat().st_mode & 0o077 == 0

    def test_sign_out_deletes_the_token_and_a_partial_write(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        path = save_token_file(_token_body())
        partial = path.with_name(path.name + ".part")
        partial.write_text("leftover", encoding="utf-8")
        GoogleSession(client_id="desktop-client-id", token_path=path).sign_out()
        assert not path.exists()
        assert not partial.exists()
        assert load_token_file(path) is None

    def test_token_is_refused_inside_the_library(self, tmp_path):
        library = tmp_path / "library" / ".media_organizer" / "token.json"
        library.parent.mkdir(parents=True)
        with pytest.raises(GoogleAuthError):
            save_token_file(_token_body(), library)
        assert not library.exists()

    def test_public_errors_do_not_repeat_token_material(self):
        message = public_error_message(GoogleAuthError("bad access_token=test-access-token"))
        assert "test-access-token" not in message
        assert public_error_message(RuntimeError("boom")) == (
            "Something went wrong talking to Google Drive."
        )


class TestSignIn:
    def test_loopback_sign_in_stores_a_user_only_token(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        posts = {}

        def post_form(url, fields):
            posts["url"] = url
            posts["fields"] = dict(fields)
            return _token_body()

        def get_json(url, access_token):
            assert access_token == "test-access-token"
            assert "about" in url
            return {"user": {"emailAddress": "ada@example.com", "displayName": "Ada"}}

        def query(parsed):
            return {
                "code": "auth-code-1",
                "state": parsed["state"][0],
                "scope": DRIVE_FILE_SCOPE,
            }

        open_browser, seen, errors = _open_and_return(query)
        session = GoogleSession(
            client_id="desktop-client-id",
            post_form=post_form,
            get_json=get_json,
            open_browser=open_browser,
            timeout=5,
        )
        account = session.sign_in()
        assert errors == []
        assert account.email == "ada@example.com"
        assert account.label == "ada@example.com"
        query_string = _qs(seen["url"])
        assert query_string["redirect_uri"][0].startswith("http://127.0.0.1:")
        assert query_string["scope"] == [DRIVE_FILE_SCOPE]
        assert "client_secret" not in seen["url"]
        assert "auth-code-1" not in seen["body"]
        assert posts["url"] == TOKEN_URL
        assert posts["fields"]["grant_type"] == "authorization_code"
        assert posts["fields"]["code"] == "auth-code-1"
        assert "client_secret" not in posts["fields"]
        verifier = posts["fields"]["code_verifier"]
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        assert query_string["code_challenge"] == [challenge]

        path = token_file_path()
        assert path.is_file()
        assert path.stat().st_mode & 0o077 == 0
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["refresh_token"] == "test-refresh-token"
        assert saved["email"] == "ada@example.com"
        assert saved["scope"] == DRIVE_FILE_SCOPE
        assert "client_secret" not in saved
        assert "code" not in saved
        repo = Path(__file__).resolve().parents[1]
        assert repo not in path.parents
        session.sign_out()
        assert not path.exists()

    def test_state_mismatch_does_not_save_a_token(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        def query(_parsed):
            return {"code": "auth-code-1", "state": "not-the-state", "scope": DRIVE_FILE_SCOPE}

        open_browser, _seen, errors = _open_and_return(query)
        session = GoogleSession(
            client_id="desktop-client-id",
            post_form=lambda *_args, **_kwargs: pytest.fail("token endpoint"),
            open_browser=open_browser,
            timeout=5,
        )
        with pytest.raises(GoogleAuthError, match="could not be verified"):
            session.sign_in()
        assert errors == []
        assert not token_file_path().exists()

    def test_cancelled_sign_in_does_not_save_a_token(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        def query(parsed):
            return {"error": "access_denied", "state": parsed["state"][0]}

        open_browser, _seen, errors = _open_and_return(query)
        session = GoogleSession(
            client_id="desktop-client-id",
            post_form=lambda *_args, **_kwargs: pytest.fail("token endpoint"),
            open_browser=open_browser,
            timeout=5,
        )
        with pytest.raises(GoogleAuthError, match="cancelled"):
            session.sign_in()
        assert errors == []
        assert not token_file_path().exists()

    def test_broader_drive_scope_is_refused(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        def query(parsed):
            return {
                "code": "auth-code-1",
                "state": parsed["state"][0],
                "scope": "https://www.googleapis.com/auth/drive",
            }

        open_browser, _seen, errors = _open_and_return(query)
        session = GoogleSession(
            client_id="desktop-client-id",
            post_form=lambda *_args, **_kwargs: pytest.fail("token endpoint"),
            open_browser=open_browser,
            timeout=5,
        )
        with pytest.raises(GoogleAuthError, match="broader"):
            session.sign_in()
        assert errors == []
        assert not token_file_path().exists()

    def test_refresh_sends_no_secret_and_keeps_the_refresh_token(self, tmp_path):
        path = tmp_path / "token.json"
        save_token_file({
            "access_token": "old-access",
            "refresh_token": "keep-refresh",
            "expires_at": 1,
            "token_type": "Bearer",
            "scope": DRIVE_FILE_SCOPE,
            "email": "ada@example.com",
        }, path)
        posts = []

        def post_form(_url, fields):
            posts.append(dict(fields))
            return {
                "access_token": "new-access",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": DRIVE_FILE_SCOPE,
            }

        def get_json(_url, access_token):
            assert access_token == "new-access"
            return {"files": []}

        session = GoogleSession(
            client_id="desktop-client-id",
            token_path=path,
            post_form=post_form,
            get_json=get_json,
        )
        assert session.list_folders() == []
        assert posts[0]["grant_type"] == "refresh_token"
        assert posts[0]["refresh_token"] == "keep-refresh"
        assert "client_secret" not in posts[0]
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["access_token"] == "new-access"
        assert saved["refresh_token"] == "keep-refresh"
        assert saved["email"] == "ada@example.com"

    def test_a_fresh_access_token_is_not_refreshed(self, tmp_path):
        path = tmp_path / "token.json"
        save_token_file({
            "access_token": "still-good",
            "refresh_token": "keep-refresh",
            "expires_at": int(time.time()) + 3600,
            "token_type": "Bearer",
            "scope": DRIVE_FILE_SCOPE,
        }, path)

        def post_form(*_args, **_kwargs):
            raise AssertionError("refresh")

        def get_json(_url, access_token):
            assert access_token == "still-good"
            return {"files": []}

        session = GoogleSession(
            client_id="desktop-client-id",
            token_path=path,
            post_form=post_form,
            get_json=get_json,
        )
        assert session.list_folders() == []
        assert session.signed_in_account().label == "Signed in"


class TestNoSecretsInTree:
    def test_gitignore_keeps_credential_filenames(self):
        text = Path(__file__).resolve().parents[1].joinpath(".gitignore").read_text(encoding="utf-8")
        for name in (
            "client_secret.json",
            "client_secret*.json",
            "credentials.json",
            "token.json",
            "drive_token.json",
        ):
            assert name in text

    def test_repository_has_no_credential_files(self):
        root = Path(__file__).resolve().parents[1]
        skip = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
        banned = {"client_secret.json", "credentials.json", "token.json", "drive_token.json"}
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in skip]
            for name in filenames:
                if name in banned or name.startswith("client_secret"):
                    found.append(str(Path(dirpath) / name))
        assert found == []

    def test_sign_in_modules_do_not_upload_or_import_qt(self):
        root = Path(__file__).resolve().parents[1]
        modules = [
            root / "media_organizer/core/google_auth.py",
            root / "media_organizer/core/drive_folders.py",
        ]
        for path in modules:
            text = path.read_text(encoding="utf-8")
            assert "uploadType" not in text
            assert "googleapis.com/upload" not in text
            assert "resumable" not in text
            tree = ast.parse(text)
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            assert imported.isdisjoint({"PySide6", "PyQt6", "PyQt5"})

    def test_dialog_source_has_no_upload_endpoint(self):
        text = (
            Path(__file__).resolve().parents[1] / "media_organizer/gui/drive_dialog.py"
        ).read_text(encoding="utf-8")
        assert "uploadType" not in text
        assert "googleapis.com/upload" not in text
        assert "resumable" not in text
