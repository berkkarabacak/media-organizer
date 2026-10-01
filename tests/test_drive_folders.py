"""Drive folder list and the confirm-before-replace bind. No network."""

import hashlib
import urllib.parse

import pytest

from media_organizer.core.drive_folders import (
    FOLDER_MIME,
    DriveFolder,
    DriveFolderError,
    commit_folder_choice,
    commit_listed_folder,
    fetch_drive_folder,
    folder_list_url,
    list_drive_folders,
    parse_one_folder_id,
)
from media_organizer.core.drive_sync import (
    SyncRecord,
    load_sync_record,
    save_sync_record,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestFolderList:
    def test_list_url_asks_for_folders_only(self):
        url = folder_list_url()
        assert "upload" not in url.lower()
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert "application/vnd.google-apps.folder" in query["q"][0]
        assert "trashed" in query["q"][0]
        assert "mimeType" in query["q"][0]
        paged = urllib.parse.parse_qs(urllib.parse.urlparse(folder_list_url("page-2")).query)
        assert paged["pageToken"] == ["page-2"]

    def test_list_parses_pages_and_skips_files(self):
        calls = []

        def get_json(url, access_token):
            assert access_token == "test-access-token"
            calls.append(url)
            if "pageToken" not in url:
                return {
                    "nextPageToken": "page-2",
                    "files": [{
                        "id": "folder-b",
                        "name": "Beta",
                        "mimeType": FOLDER_MIME,
                    }],
                }
            return {
                "files": [
                    {"id": "folder-a", "name": "Alpha", "mimeType": FOLDER_MIME},
                    {"id": "doc-1", "name": "Notes", "mimeType": "text/plain"},
                    {"id": "folder-b", "name": "Beta again", "mimeType": FOLDER_MIME},
                ],
            }

        folders = list_drive_folders("test-access-token", get_json=get_json)
        assert folders == [
            DriveFolder("folder-a", "Alpha"),
            DriveFolder("folder-b", "Beta"),
        ]
        assert len(calls) == 2
        assert all("upload" not in url for url in calls)

    def test_a_repeated_page_token_stops_the_list(self):
        calls = []

        def get_json(url, _access_token):
            calls.append(url)
            return {
                "nextPageToken": "again",
                "files": [{"id": "folder-a", "name": "Alpha", "mimeType": FOLDER_MIME}],
            }

        folders = list_drive_folders("test-access-token", get_json=get_json, page_limit=5)
        assert folders == [DriveFolder("folder-a", "Alpha")]
        assert len(calls) == 2

    def test_fetch_refuses_a_file(self):
        def get_json(url, _access_token):
            assert "/files/" in url
            assert "upload" not in url
            return {"id": "file-1", "name": "Notes", "mimeType": "text/plain"}

        with pytest.raises(DriveFolderError, match="folder"):
            fetch_drive_folder("test-access-token", "file-1", get_json=get_json)

    def test_fetch_returns_the_folder_name(self):
        def get_json(_url, _access_token):
            return {"id": "folder-a", "name": "Vacation", "mimeType": FOLDER_MIME}

        assert fetch_drive_folder("test-access-token", "folder-a", get_json=get_json) == (
            DriveFolder("folder-a", "Vacation")
        )

    def test_picker_ids_must_be_exactly_one_folder(self):
        assert parse_one_folder_id("folder-a") == "folder-a"
        with pytest.raises(DriveFolderError):
            parse_one_folder_id("folder-a,folder-b")
        with pytest.raises(DriveFolderError):
            parse_one_folder_id("")
        with pytest.raises(DriveFolderError):
            parse_one_folder_id("has space")


class TestBindConfirm:
    def test_first_folder_binds_without_dropping_anything(self):
        record = SyncRecord()
        assert commit_folder_choice(record, "folder-a", replace_confirmed=False) == "bind"
        assert record.drive_folder_id == "folder-a"

    def test_the_same_folder_keeps_stored_file_ids(self, tmp_path):
        record = SyncRecord()
        record.bind_folder("folder-a")
        record.mark_uploaded("2024/a.jpg", 3, _digest(b"abc"), "drive-1")
        assert commit_folder_choice(record, "folder-a", replace_confirmed=False) == "unchanged"
        assert record.entry_for("2024/a.jpg").drive_file_id == "drive-1"
        save_sync_record(record, tmp_path)
        assert load_sync_record(tmp_path).drive_folder_id == "folder-a"

    def test_a_different_folder_asks_before_replacing(self, tmp_path):
        record = SyncRecord()
        record.bind_folder("folder-a")
        record.mark_uploaded("2024/a.jpg", 3, _digest(b"abc"), "drive-1")
        folders = [DriveFolder("folder-a", "Old"), DriveFolder("folder-b", "New")]

        declined = commit_listed_folder(
            record, folders, "folder-b", replace_confirmed=False,
        )
        assert declined == "declined"
        assert record.drive_folder_id == "folder-a"
        assert record.entry_for("2024/a.jpg").drive_file_id == "drive-1"

        replaced = commit_listed_folder(
            record, folders, "folder-b", replace_confirmed=True,
        )
        assert replaced == "replaced"
        assert record.drive_folder_id == "folder-b"
        assert record.entries == {}
        saved = save_sync_record(record, tmp_path)
        text = saved.read_text(encoding="utf-8")
        assert "folder-b" in text
        assert "access_token" not in text
        assert "refresh_token" not in text
        loaded = load_sync_record(tmp_path)
        assert loaded.drive_folder_id == "folder-b"
        assert loaded.decide("2024/a.jpg", 3, _digest(b"abc")) == "upload"

    def test_a_folder_that_was_not_listed_is_refused(self):
        record = SyncRecord()
        record.bind_folder("folder-a")
        with pytest.raises(DriveFolderError):
            commit_listed_folder(
                record, [DriveFolder("folder-a", "Old")], "folder-b",
                replace_confirmed=True,
            )
        assert record.drive_folder_id == "folder-a"

    def test_picker_choice_can_bind_a_folder_that_was_not_in_the_list(self):
        record = SyncRecord()
        assert commit_folder_choice(record, "picked-folder", replace_confirmed=False) == "bind"
        assert record.drive_folder_id == "picked-folder"
