"""Drive sign-in dialog. Offscreen, with a fake session and no network."""

import hashlib
import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

from media_organizer.core.drive_folders import DriveFolder
from media_organizer.core.drive_sync import SyncRecord, load_sync_record, save_sync_record
from media_organizer.core.drive_upload import DriveResponse, UploadProgress, UploadResult, upload_library
from media_organizer.core.google_auth import GoogleAccount
from media_organizer.gui.drive_dialog import (
    REPLACE_FOLDER_TEXT,
    DriveAccountDialog,
    FolderListDialog,
)


class FakeSession:
    def __init__(self):
        self.account = None
        self.folders = [DriveFolder("folder-a", "Vacation")]
        self.picked = DriveFolder("folder-b", "Archive")
        self.signed_out = False
        self.listed = False

    def signed_in_account(self):
        return self.account

    def sign_in(self):
        self.account = GoogleAccount(email="ada@example.com", display_name="Ada")
        return self.account

    def sign_out(self):
        self.account = None
        self.signed_out = True

    def list_folders(self):
        self.listed = True
        return list(self.folders)

    def pick_drive_folder(self):
        return self.picked

    def access_token(self):
        return "test-access-token"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _wait_until(predicate, timeout=3):
    app = QApplication.instance()
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_replace_prompt_says_remote_files_stay():
    assert "file ids" in REPLACE_FOLDER_TEXT
    assert "stay there" in REPLACE_FOLDER_TEXT


def test_folder_list_dialog_returns_the_selected_folder(qapp):
    dialog = FolderListDialog([DriveFolder("folder-a", "Vacation")], browse=None)
    dialog.show()
    assert dialog.browse_btn.isHidden()
    dialog.folder_list.setCurrentRow(0)
    dialog._use_selected()
    assert dialog.chosen == DriveFolder("folder-a", "Vacation")
    assert dialog.from_browser is False
    assert dialog.result() == QDialog.Accepted
    dialog.deleteLater()
    qapp.processEvents()


def test_dialog_signs_in_and_out_and_keeps_upload_disabled(qapp, tmp_path):
    session = FakeSession()
    dialog = DriveAccountDialog(str(tmp_path), session=session)
    dialog.show()
    assert dialog.account_label.text() == "Not signed in"
    assert dialog.sign_in_btn.isEnabled()
    assert not dialog.sign_out_btn.isEnabled()
    assert not dialog.choose_btn.isEnabled()
    assert dialog.upload_btn.text() == "Upload"
    assert not dialog.upload_btn.isEnabled()

    dialog.sign_in_btn.click()
    _wait_until(lambda: dialog.account_label.text() == "ada@example.com")
    assert session.account.email == "ada@example.com"
    assert dialog.sign_out_btn.isEnabled()
    assert dialog.choose_btn.isEnabled()
    assert not dialog.upload_btn.isEnabled()

    dialog.sign_out_btn.click()
    assert session.signed_out
    assert dialog.account_label.text() == "Not signed in"
    assert not dialog.choose_btn.isEnabled()
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


def test_binding_asks_before_replacing_a_different_folder(qapp, tmp_path):
    settings = QSettings("MediaOrganizer", "MediaOrganizer")
    session = FakeSession()
    session.account = GoogleAccount(email="ada@example.com", display_name="Ada")
    dialog = DriveAccountDialog(str(tmp_path), session=session)
    asked = []

    def confirm():
        asked.append(True)
        return asked.count(True) >= 2

    assert dialog._apply_folder(
        DriveFolder("folder-a", "Vacation"),
        from_browser=False,
        folders=session.folders,
        confirm=confirm,
    )
    assert asked == []
    record = load_sync_record(tmp_path)
    assert record.drive_folder_id == "folder-a"
    record.mark_uploaded("2024/a.jpg", 3, _digest(b"abc"), "drive-1")
    save_sync_record(record, tmp_path)

    assert dialog._apply_folder(
        DriveFolder("folder-a", "Vacation"),
        from_browser=False,
        folders=session.folders,
        confirm=confirm,
    )
    assert asked == []
    assert load_sync_record(tmp_path).entry_for("2024/a.jpg").drive_file_id == "drive-1"

    assert dialog._apply_folder(
        DriveFolder("folder-b", "Archive"),
        from_browser=True,
        folders=session.folders,
        confirm=lambda: False,
    ) is False
    kept = load_sync_record(tmp_path)
    assert kept.drive_folder_id == "folder-a"
    assert kept.entry_for("2024/a.jpg").drive_file_id == "drive-1"

    assert dialog._apply_folder(
        DriveFolder("folder-b", "Archive"),
        from_browser=True,
        folders=session.folders,
        confirm=lambda: True,
    )
    replaced = load_sync_record(tmp_path)
    assert replaced.drive_folder_id == "folder-b"
    assert replaced.entries == {}
    text = (tmp_path / ".media_organizer" / "drive_sync.json").read_text(encoding="utf-8")
    assert "ada@example.com" not in text
    assert "access_token" not in text
    assert dialog.folder_label.text() == "Archive"

    again = DriveAccountDialog(str(tmp_path), session=FakeSession())
    assert again.folder_label.text() == "Archive"
    assert again.folder_id_label.text() == "folder-b"
    assert again.account_label.text() == "Not signed in"
    again.close()
    dialog.close()
    settings.remove("driveFolderName/folder-a")
    settings.remove("driveFolderName/folder-b")
    dialog.deleteLater()
    again.deleteLater()
    qapp.processEvents()


def test_upload_enables_only_when_signed_in_and_a_folder_is_bound(qapp, tmp_path):
    record = SyncRecord()
    record.bind_folder("folder-a")
    save_sync_record(record, tmp_path)
    (tmp_path / "2024").mkdir()
    (tmp_path / "2024" / "a.jpg").write_bytes(b"abcd")

    signed_out = DriveAccountDialog(str(tmp_path), session=FakeSession())
    signed_out.show()
    assert signed_out.upload_btn.text() == "Upload"
    assert not signed_out.upload_btn.isEnabled()
    signed_out.close()

    session = FakeSession()
    session.account = GoogleAccount(email="ada@example.com", display_name="Ada")
    dialog = DriveAccountDialog("", session=session)
    dialog.show()
    assert not dialog.upload_btn.isEnabled()
    dialog.library_edit.setText(str(tmp_path))
    dialog._show_bound_folder()
    assert dialog.upload_btn.isEnabled()
    assert "deleted" in dialog.upload_btn.toolTip().lower()
    assert "comes later" not in dialog.upload_btn.toolTip().lower()
    dialog.close()
    signed_out.deleteLater()
    dialog.deleteLater()
    qapp.processEvents()


def test_upload_button_reports_progress_and_cancel(qapp, tmp_path):
    record = SyncRecord()
    record.bind_folder("folder-a")
    save_sync_record(record, tmp_path)
    (tmp_path / "2024").mkdir()
    (tmp_path / "2024" / "a.jpg").write_bytes(b"abcd")
    session = FakeSession()
    session.account = GoogleAccount(email="ada@example.com", display_name="Ada")
    state = {"token": "", "cancelled": False}

    def uploader(library_dir, *, access_token, progress, cancel, **kwargs):
        state["token"] = access_token() if callable(access_token) else access_token
        progress(UploadProgress("2024/a.jpg", 0, 1, 0, 4, uploading=True))
        deadline = time.time() + 2
        while time.time() < deadline:
            if cancel():
                state["cancelled"] = True
                return UploadResult(
                    uploaded=0, skipped=0, cancelled=True, files_done=0, files_total=1,
                )
            time.sleep(0.01)
        return UploadResult(uploaded=1, skipped=0, cancelled=False, files_done=1, files_total=1)

    dialog = DriveAccountDialog(str(tmp_path), session=session, uploader=uploader)
    dialog.show()
    assert dialog.upload_btn.isEnabled()
    dialog.upload_btn.click()
    _wait_until(lambda: "2024/a.jpg" in dialog.status_label.text())
    assert dialog.upload_btn.text() == "Cancel"
    assert "0 of 1" in dialog.status_label.text()
    dialog.upload_btn.click()
    _wait_until(
        lambda: state["cancelled"] and dialog.upload_btn.text() == "Upload"
        and "stopped" in dialog.status_label.text().lower()
    )
    assert state["token"] == "test-access-token"
    assert dialog.upload_btn.isEnabled()
    assert (tmp_path / "2024" / "a.jpg").read_bytes() == b"abcd"
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


def test_upload_progress_shows_unchanged_and_sent(qapp, tmp_path):
    record = SyncRecord()
    record.bind_folder("folder-a")
    save_sync_record(record, tmp_path)
    session = FakeSession()
    session.account = GoogleAccount(email="ada@example.com", display_name="Ada")
    dialog = DriveAccountDialog(str(tmp_path), session=session)
    dialog.show()
    dialog._on_upload_progress(UploadProgress(
        "2024/Q3/07 July/edit.jpg",
        2,
        4,
        7,
        16,
        uploading=True,
        skipped=1,
        uploaded=1,
    ))
    text = dialog.status_label.text()
    assert "Uploading 2024/Q3/07 July/edit.jpg" in text
    assert "2 of 4" in text
    assert "1 unchanged" in text
    assert "1 sent" in text
    assert "7 B of 16 B to send" in text
    dialog._on_upload_finished(UploadResult(
        uploaded=2, skipped=2, cancelled=False, files_done=4, files_total=4,
        bytes_sent=16,
    ))
    finished = dialog.status_label.text()
    assert "2 sent (16 B)" in finished
    assert "2 unchanged" in finished
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


def test_upload_failure_shows_a_plain_message(qapp, tmp_path, monkeypatch):
    record = SyncRecord()
    record.bind_folder("folder-a")
    save_sync_record(record, tmp_path)
    (tmp_path / "2024").mkdir()
    (tmp_path / "2024" / "a.jpg").write_bytes(b"abcd")
    warnings = []

    def _warning(*args, **kwargs):
        warnings.append(args)

    monkeypatch.setattr("media_organizer.gui.drive_dialog.QMessageBox.warning", _warning)

    def request(method, url, headers, body, timeout=None):
        payload = json.dumps({
            "error": {
                "errors": [{
                    "reason": "storageQuotaExceeded",
                    "message": "The user's Drive storage quota has been exceeded.",
                }],
                "code": 403,
                "message": "The user's Drive storage quota has been exceeded.",
            }
        }).encode("utf-8")
        return DriveResponse(403, {"content-type": "application/json"}, payload)

    def uploader(library_dir, *, access_token, progress=None, cancel=None, **kwargs):
        return upload_library(
            library_dir,
            access_token=access_token,
            request=request,
            progress=progress,
            cancel=cancel,
            sessions_path=tmp_path / "sessions.json",
        )

    session = FakeSession()
    session.account = GoogleAccount(email="ada@example.com", display_name="Ada")
    dialog = DriveAccountDialog(str(tmp_path), session=session, uploader=uploader)
    dialog.show()
    dialog.upload_btn.click()
    expected = "Google Drive is full. Free some space, then try the upload again."
    _wait_until(lambda: dialog.status_label.text() == expected and dialog.upload_btn.text() == "Upload")
    assert warnings
    assert any(expected in args for args in warnings)
    assert "403" not in dialog.status_label.text()
    assert "storageQuotaExceeded" not in dialog.status_label.text()
    assert (tmp_path / "2024" / "a.jpg").read_bytes() == b"abcd"
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


def test_drive_menu_is_on_the_main_window(qapp):
    from media_organizer.gui.main_window import MainWindow

    QSettings("MediaOrganizer", "MediaOrganizer").clear()
    window = MainWindow()
    window.show()
    matches = [action for action in window.findChildren(type(window.drive_action))
               if action.text() == "Google Drive…"]
    assert window.drive_action.text() == "Google Drive…"
    assert matches
    window.close()
    window.deleteLater()
    qapp.processEvents()
