# Media Organizer — Handover Guide

**Current release: v1.5.3** · Repo: https://github.com/berkkarabacak/media-organizer · Site: https://berkkarabacak.github.io/media-organizer/

A Windows desktop app that organizes photos/videos by their **true capture date** (EXIF → video container metadata → filename patterns → mtime guess), with 7 folder strategies (incl. GPS-location sorting), content-hash duplicate detection, dry-run, undo, crash-resume journaling, free-space preflight, and a guided 3-step dark-themed UI.

---

## 1. Repository layout

```
main.py                      # entry point (python main.py)
media_organizer/
  __init__.py                # __version__ — bump here every release
  core/                      # pure logic, Qt-free, fully unit-tested
    metadata.py              # capture-date extraction (EXIF, mvhd atoms, filenames, mtime)
    organizer.py             # plan building (OrganizeOptions, collisions, strategies)
    strategies.py            # 7 folder strategies; UNDATED/UNCERTAIN folders
    duplicates.py            # SHA-256 content hashing (size + partial-hash prefilter)
    executor.py              # plan execution, free-space preflight, atomic .part copies
    journal.py               # crash-resume journal (JSONL, fsync, torn-line tolerant)
    geodata.py               # 584-city offline DB, haversine, nearest_city()
    eta.py                   # ThroughputEstimator, format_eta/rate
    display.py               # relative_destination, elide_middle, format_bytes, sort keys
  gui/
    main_window.py           # 3-step QStackedWidget flow — the whole UI
    workers.py               # ScanWorker / OrganizeWorker (QThread, throttled signals)
    theme.py                 # all QSS + palette constants
    icons.py                 # 34 embedded SVG icons → QIcon (recolor, HiDPI)
tests/                       # 183 pytest tests (Qt-free logic + QTest GUI tests)
tools/
  qa/                        # QA battery: run_all.py → 131 scenario checks
  verify_progress.py         # progress/ETA harness (incl. >2GB regression)
  verify_user_flows.py       # QTest click-through of chip/scan/organize
  capture_ui.py              # offscreen screenshot capture (registers Windows fonts!)
  real_ui_drive_uia.py       # drives the real running app via UIAutomation
  make_demo_library.py       # generates demo-library/ fixture (25 files, all cases)
  bench_parallel.py          # serial vs thread vs process benchmark
installer/
  media_organizer.spec       # PyInstaller spec (windowed, onedir)
  setup.iss                  # Inno Setup 6 script
  version_info.txt           # exe version metadata — bump with releases
website/                     # React+Vite+Tailwind landing page (digital-rain template)
  src/config.ts              # all site content
  src/sections/              # Hero/Navigation contain the download links
demo-library/                # generated test fixture (not committed)
```

## 2. Build & test (Windows)

```bat
:: env (already set up on the dev machine; to recreate:)
python -m venv .venv
.venv\Scripts\python -m pip install PySide6 pyinstaller pytest Pillow pywinauto pyautogui pyperclip

:: full verification — run ALL of these before every release:
.venv\Scripts\python -m pytest tests\ -q                  :: 183 tests
.venv\Scripts\python tools\qa\run_all.py                  :: 131 scenario checks
set QT_QPA_PLATFORM=offscreen && .venv\Scripts\python tools\verify_progress.py
set QT_QPA_PLATFORM=offscreen && .venv\Scripts\python tools\verify_user_flows.py

:: package:
.venv\Scripts\python -m PyInstaller installer\media_organizer.spec --distpath dist --workpath build --noconfirm
tools\innosetup6\ISCC.exe installer\setup.iss             :: output: installer\dist\MediaOrganizer-Setup-X.Y.Z.exe
```

**Machine quirks:** npm/node live at `C:\Users\OdinLocal\AppData\Local\Programs\kimi-desktop\resources\resources\runtime\` (call `npm.cmd`, not `npm`). Inno Setup 6.7.3 is portable at `tools\innosetup6\` (reinstall: run `tools\innosetup-6.7.3.exe /VERYSILENT /PORTABLE=1 /DIR=...`). Kill any running `MediaOrganizer.exe` before PyInstaller or it locks files and you silently package the OLD build (this actually bit us once).

## 3. Release procedure (proven, do exactly this)

1. Bump version in **4 places**: `media_organizer/__init__.py`, `installer/setup.iss` (`MyAppVersion`), `installer/version_info.txt` (both tuples + strings), README.
2. Run the full verification battery above.
3. PyInstaller + Inno (commands above).
4. Update download links in `website/src/sections/Hero.tsx` + `Navigation.tsx` to the new asset name, then:
   ```bat
   cd website && npm.cmd run build
   cd dist && git init && git add -A && git commit -m "..." && git remote add origin <repo> && git push -f origin HEAD:gh-pages
   ```
   (site is served from the `gh-pages` branch, `dist/` only, `base: './'` in vite.config — do not change)
5. GitHub release via API: `POST /repos/berkkarabacak/media-organizer/releases` (tag `vX.Y.Z`), upload installer asset to `uploads.github.com/.../releases/<id>/assets?name=MediaOrganizer-Setup-X.Y.Z.exe`.
6. Commit source to `main`. Verify: `releases/latest/download/MediaOrganizer-Setup-X.Y.Z.exe` returns 200 and the Pages site serves the new bundle hash.

**Auth:** releases/git pushes used a personal access token that the owner pasted in chat — it should be considered compromised; owner was told to revoke it. Generate a fresh token (repo scope) when needed. Never commit tokens.

## 4. Architecture rules (don't break these)

- `core/` stays **Qt-free** — all logic unit-testable; GUI only in `gui/`.
- **Never overwrite files** — collisions get `_1`, `_2` suffixes (plan-time AND execute-time).
- Copy mode is default; move is opt-in. Per-file copies go to `.part` then atomic rename.
- Every organize run writes an operation log (undo) + journal (crash resume). Dry run writes NOTHING.
- GUI thread never does I/O — all work in workers; progress signals throttled (~150 ms); byte counts travel as `object` in Qt signals (32-bit `int` overflowed above 2 GB — regression test exists).
- The strategy-card example pills must not use "→" (U+2192) — it garbles on scaled displays; use "›" (U+203A). Regression test exists.
- Fonts in offscreen captures: Qt's offscreen QPA has zero fonts — `capture_ui.py` registers Windows fonts explicitly.

## 5. Testing philosophy (what "tested" means here)

- pytest: logic units + QTest GUI tests (real clicks on real widgets, offscreen).
- QA battery (`tools/qa/`): 131 checks — hostile filenames (Turkish/Unicode/emoji/CON.jpg), corrupt files, locked files, flow abuse (cancel mid-run, double-organize, undo-twice), all strategies, 2,000-file stress, settings persistence.
- Real-desktop drive (`tools/real_ui_drive_uia.py`): UIAutomation against the installed app (works even on a locked desktop; physical clicks don't).
- Demo fixture: `tools/make_demo_library.py` → 25 files covering every detection path.

## 6. Known limitations / roadmap ideas

- Location sorting needs EXIF GPS (photos only; videos rarely have it). City DB = 584 cities, 250 km cutoff → `_unknown-location/` beyond that.
- Installer is unsigned → SmartScreen warning. Buying a code-signing cert (~$200-400/yr) is the #1 commercial next step.
- HEIC/RAW dates read only if EXIF parses via Pillow; otherwise filename/mtime fallback.
- No cloud/network features at all (by design — "100% offline" is a selling point).
- Ideas: thumbnail previews in the table, light theme, pause/resume, Watch-folder mode, context-menu ("Send to → Media Organizer"), localization (owner speaks Turkish), Gumroad/Lemon Squeezy license keys.

## 7. Website

React 19 + Vite + Tailwind + shadcn/ui, "digital rain" template. All content in `src/config.ts`. Sections in `src/sections/`. Images in `public/images/` (AI-generated thematic JPGs + real app screenshots captured via `tools/capture_ui.py`). Uses **HashRouter** (BrowserRouter breaks on GitHub Pages subpaths — this caused a black-screen incident). Dev preview: `npm.cmd run dev` (port 3000 default).
