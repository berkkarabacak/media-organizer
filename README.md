# Media Organizer

**Version 1.0.0** — a Windows desktop app that sorts photos and videos into
date-based folders using their **real capture date** (embedded metadata), not
the unreliable filesystem copy dates.

## What it does

Folders of photos/videos copied from phones and cameras over the years often
have wrong file dates. Media Organizer reads the actual capture date and
sorts everything into tidy folders like `2024/01 (January)/` or `2024/Q1/`.

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

- **Copy is the default.** Move mode requires explicit opt-in.
- **Never overwrites.** Name collisions get `_1`, `_2`, … suffixes.
- **Dry-run preview first.** You review a sortable/filterable table of every
  file, its detected date, confidence badge, and destination before anything
  is touched.
- **Undo.** Every run writes an operation log; *File → Undo last run* restores it.
- Corrupt/unreadable files are logged and skipped — the run never crashes.

## Using the app

1. Choose the **source folder** (enable *Include subfolders* for recursion).
2. Choose the **destination folder**.
3. Pick options: copy vs move, folder pattern (year/month, **year/quarter**,
   or year/month/quarter), skip exact duplicates, media types.
4. Press **Scan & Preview** and review the table.
5. Press **Organize**. A summary reports copied/moved, duplicates skipped,
   and undated files (placed in an `Undated` folder).

## Running from source (developers)

```bat
.venv\Scripts\python.exe main.py
```

Requires Python 3.10+, PySide6, Pillow.

### Tests

```bat
.venv\Scripts\python.exe -m pytest tests -q
```

The suite covers EXIF extraction (real JPEGs written via Pillow's Exif class),
hand-built synthetic MP4 files (mvhd v0 and v1), every filename pattern,
fallback ordering, quarter computation, collision handling, duplicate
detection, and undo-log round-trips. Core logic has no Qt imports.

### Project layout

```
main.py                     entry point
media_organizer/
  core/                     pure logic (no Qt): metadata, organizer,
                            duplicates, executor, plan (undo log)
  gui/                      PySide6 UI: main window, workers, theme
tests/                      pytest suite for all core logic
installer/
  media_organizer.spec      PyInstaller spec (onedir, windowed)
  version_info.txt          exe version resource
  media_organizer.ico       generated app icon (make_icon.py)
  setup.iss                 Inno Setup 6 installer script
```

### Packaging

```bat
.venv\Scripts\pyinstaller.exe installer\media_organizer.spec --noconfirm
iscc installer\setup.iss
```

Produces `dist\MediaOrganizer\MediaOrganizer.exe` and
`installer\dist\MediaOrganizer-Setup-1.0.0.exe`.
