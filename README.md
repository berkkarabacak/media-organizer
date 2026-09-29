# Media Organizer

**Version 1.5.3** — a Windows desktop app that sorts photos and videos into
tidy folders using their **real capture date** (embedded metadata), not the
unreliable filesystem copy dates — or by **where they were taken** (GPS).

## What it does

Folders of photos/videos copied from phones and cameras over the years often
have wrong file dates. Media Organizer reads the actual capture date (or the
GPS location) and sorts everything into folders like `2024/07 July/`,
`2024/Q3/`, or `Istanbul, Turkey/`.

The app guides you through three steps:

1. **Choose your folders** — where the messy photos are, and where the
   organized copies should go (a suggestion chip offers `<source>_Organized`).
2. **How should we sort them?** — pick a folder structure (see below).
   Advanced options (copy vs move, subfolders, duplicates, media types) are
   tucked away under *Advanced options*.
3. **Check the plan** — review every file's destination in a preview table,
   then press **Organize now**. Both scanning and organizing show a determinate
   progress bar with percent, files done/total, speed, and an ETA.
   The completion dialog offers **Undo** and **Open folder**.

## Folder structures

| Strategy | Result |
|---|---|
| Year only | `2024/IMG_1234.jpg` |
| Year → Month *(default)* | `2024/07 July/IMG_1234.jpg` |
| Year → Quarter | `2024/Q3/IMG_1234.jpg` |
| Year → Quarter → Month (nested) | `2024/Q3/07 July/IMG_1234.jpg` |
| Monthly, single level (flat) | `2024-07 July/IMG_1234.jpg` |
| By location (GPS) | `Istanbul, Turkey/IMG_1234.jpg` |
| Location → Year | `Istanbul, Turkey/2024/IMG_1234.jpg` |

Files without a date go to `_undated/`; GPS files too far from any known place
go to `_unknown-location/`. Location matching is fully offline via a bundled
list of world cities (`core/geodata.py`).

Date detection fallback chain:

1. **EXIF** `DateTimeOriginal` / `DateTimeDigitized` / `DateTime` (JPEG, TIFF, HEIC-adjacent)
2. **PNG text chunks** (`tEXt` / `zTXt` creation-time keys)
3. **Video container** creation time — pure-Python `mvhd`/`mdhd`/`meta` parser
   for MP4 / MOV / M4V / 3GP (handles box version 0 and 1)
4. **Filename patterns** — `IMG_20240115_123000`, `VID_20240115`,
   `Screenshot_2024-01-15-12-30-00`, `PXL_20240115_…`,
   WhatsApp `IMG-20240115-WA0001`, `2024-01-15 12.30.00`, bare `YYYYMMDD`
5. **Filesystem mtime** — last resort, flagged low-confidence

## Safety guarantees

- **Copy is the default.** Move mode requires explicit opt-in and a confirmation.
- **Never overwrites.** Name collisions get `_1`, `_2`, … suffixes.
- **Dry-run preview first.** You review a sortable/filterable table of every
  file, its detected date, and its new location before anything is touched.
- **Exact duplicates (same content).** Duplicate detection compares SHA-256
  content hashes only — never file names.
- **Undo.** Every run writes an operation log; *File → Undo last run* or the
  completion dialog's **Undo** button restores it.
- Corrupt/unreadable files are logged and skipped — the run never crashes.

## Install (Windows)

Download `MediaOrganizer-Setup-1.5.3.exe` from the
[latest release](https://github.com/berkkarabacak/media-organizer/releases/latest)
and run it.

**Seeing a blue "Windows protected your PC" (SmartScreen) prompt?** That's
normal for a new, independently published app — the installer isn't signed
with a commercial certificate yet. The app is safe: it is 100% offline and
its entire source code is this repository. Click **More info** →
**Run anyway** to proceed.

**Something went wrong?** The app writes a crash log to
`%LOCALAPPDATA%\MediaOrganizer\crash.log` — attach it when reporting an
issue and it can be diagnosed in minutes.

## Running from source (developers)

```bat
.venv\Scripts\python.exe main.py
```

Requires Python 3.10+, PySide6, Pillow.

### Tests

```bat
.venv\Scripts\python.exe -m pytest tests -q
```

The suite covers EXIF extraction (real JPEGs written via Pillow's Exif class,
including GPS), hand-built synthetic MP4 files (mvhd v0 and v1), every
filename pattern, fallback ordering, every folder strategy (incl. quarter
math and flat monthly), the nearest-city mapper, content-hash duplicate
detection with renamed files, the ETA estimator, display helpers, collision
handling, undo-log round-trips, and offscreen QTest GUI click tests.
Core logic has no Qt imports.

### Project layout

```
main.py                     entry point
media_organizer/
  core/                     pure logic (no Qt): metadata, organizer,
                            strategies, geodata, duplicates, executor,
                            eta, display, plan (undo log)
  gui/                      PySide6 UI: main window, workers, theme, icons
tests/                      pytest suite for all core logic + GUI clicks
tools/
  capture_ui.py             offscreen screenshot harness (ui_shots/)
  verify_progress.py        offscreen progress/ETA verification
  verify_user_flows.py      offscreen QTest user-flow verification
installer/
  media_organizer.spec      PyInstaller spec (onedir, windowed)
  version_info.txt          exe version resource
  media_organizer.ico       app icon
  setup.iss                 Inno Setup 6 installer script
```

### Packaging

```bat
.venv\Scripts\pyinstaller.exe installer\media_organizer.spec --noconfirm
iscc installer\setup.iss
```

Produces `dist\MediaOrganizer\MediaOrganizer.exe` and
`installer\dist\MediaOrganizer-Setup-1.5.3.exe`.
