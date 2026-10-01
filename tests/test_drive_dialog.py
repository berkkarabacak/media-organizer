"""Drive sign-in dialog. Offscreen, with a fake session and no network."""

import hashlib
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

from media_organizer.core.drive_folders import DriveFolder
from media_organizer.core.drive_sync import load_sync_record, save_sync_record
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
