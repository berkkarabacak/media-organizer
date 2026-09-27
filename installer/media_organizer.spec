# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Media Organizer (onedir, windowed).
# Build: .venv\Scripts\pyinstaller.exe installer\media_organizer.spec --noconfirm
# NOTE: reconstructed after a workspace incident; verify against CI before release.

from pathlib import Path

ROOT = Path(SPECPATH).parent  # project root (spec lives in installer/)

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=["PIL", "PySide6.QtSvg"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MediaOrganizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=str(ROOT / "installer" / "media_organizer.ico"),
    version=str(ROOT / "installer" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MediaOrganizer",
)
