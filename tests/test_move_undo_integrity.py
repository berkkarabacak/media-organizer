"""Move undo must not relocate a file that showed up after the journal.

The crash journal used to omit sha256 and size. Resume then hashed whatever
was at the destination when the log was seeded, and move undo trusted that
snapshot. Replacing the destination, or putting a new file there after a
seed that saw nothing, made Undo move the user's file.

A legacy move line still has no hash. Seeding must not adopt a destination
whose source is already gone. When both sides are still present and
identical, empty Move Resume records that pair's hash before it unlinks
the source, so Undo can put the file back.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from media_organizer.core.executor import execute_plan
from media_organizer.core.journal import (
    JournalWriter, completed_operations, find_unfinished_journal,
    journal_path_for,
)
from media_organizer.core.organizer import OrganizeOptions, build_plan
from media_organizer.core.plan import load_log, undo_log
from tests.helpers import make_jpeg_with_exif

_ORIGINAL = b"original-bytes"
_REPLACEMENT = b"REPLACED-bytes"  # same length: a size check cannot tell them apart


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _dirs(tmp_path: Path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    return src, dst


def _assert_not_relocated(src: Path, dest: Path, payload: bytes):
    assert dest.is_file()
    assert dest.read_bytes() == payload
    assert not src.exists() or src.read_bytes() != payload
    assert not list(src.rglob("*restored*"))
    assert not list(dest.parent.rglob("*restored*"))


def test_replaced_destination_is_not_moved_back(tmp_path):
    """Unfinished move, then the destination bytes change, then empty Resume.

    The seeded log keeps the hash from the journal line. Undo must not
    move the replacement onto the source or a ``_restored`` name.
    """
    src, dst = _dirs(tmp_path)
    source = src / "shot.jpg"
    dest = dst / "2024" / "shot.jpg"
    dest.parent.mkdir(parents=True)
    source.write_bytes(_ORIGINAL)
    dest.write_bytes(_ORIGINAL)
    with JournalWriter(dst) as writer:
        writer.record(
            "move", str(source), str(dest),
            sha256=_digest(_ORIGINAL), size=len(_ORIGINAL))
    source.unlink()
    dest.write_bytes(_REPLACEMENT)
    assert len(_REPLACEMENT) == len(_ORIGINAL)

    log, summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    assert summary["moved"] == 0
    assert log.operations[0].status == "done"
    assert log.operations[0].sha256 == _digest(_ORIGINAL)
    assert log.operations[0].sha256 != _digest(_REPLACEMENT)
    assert log.operations[0].size == len(_ORIGINAL)
    result = undo_log(log, dst)
    assert result["undone"] == 0
    assert result["kept"] == 1
    _assert_not_relocated(source, dest, _REPLACEMENT)


def test_legacy_journal_does_not_hash_the_replacement(tmp_path):
    """A journal line with no sha256 must not adopt the file at dest now."""
    src, dst = _dirs(tmp_path)
    source = src / "shot.jpg"
    dest = dst / "2024" / "shot.jpg"
    dest.parent.mkdir(parents=True)
    source.write_bytes(_ORIGINAL)
    dest.write_bytes(_REPLACEMENT)
    with JournalWriter(dst) as writer:
        writer.record("move", str(source), str(dest))
    source.unlink()

    log, _summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    assert log.operations[0].status == "done"
    assert log.operations[0].sha256 == ""
    assert log.operations[0].size == -1
    result = undo_log(log, dst)
    assert result["undone"] == 0
    assert result["kept"] == 1
    _assert_not_relocated(source, dest, _REPLACEMENT)


def test_missing_destination_at_seed_does_not_relocate_a_later_file(tmp_path):
    """Seed while dest is gone (empty hash), then a new file appears there."""
    src, dst = _dirs(tmp_path)
    source = src / "shot.jpg"
    dest = dst / "2024" / "shot.jpg"
    source.write_bytes(_ORIGINAL)
    with JournalWriter(dst) as writer:
        writer.record("move", str(source), str(dest))
    source.unlink()
    assert not dest.exists()

    log, _summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    assert log.operations[0].status == "done"
    assert log.operations[0].sha256 == ""
    assert log.operations[0].size == -1
    dest.parent.mkdir(parents=True)
    dest.write_bytes(_REPLACEMENT)
    result = undo_log(load_log(dst), dst)
    assert result["undone"] == 0
    assert result["kept"] == 1
    _assert_not_relocated(source, dest, _REPLACEMENT)


def test_source_with_different_bytes_is_not_an_undoable_move(tmp_path):
    """Source still present and not the destination: leave both files."""
    src, dst = _dirs(tmp_path)
    source = src / "shot.jpg"
    dest = dst / "2024" / "shot.jpg"
    dest.parent.mkdir(parents=True)
    source.write_bytes(b"still-in-the-library")
    dest.write_bytes(_REPLACEMENT)
    with JournalWriter(dst) as writer:
        writer.record(
            "move", str(source), str(dest),
            sha256=_digest(_ORIGINAL), size=len(_ORIGINAL))

    log, _summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    assert log.operations[0].status == "kept"
    assert source.read_bytes() == b"still-in-the-library"
    result = undo_log(log, dst)
    assert result["undone"] == 0
    assert dest.read_bytes() == _REPLACEMENT
    assert source.read_bytes() == b"still-in-the-library"
    assert not list(src.rglob("*restored*"))
    assert not list(dst.rglob("*restored*"))


def test_legacy_matching_move_resume_unlinks_and_undo_restores(tmp_path):
    """Pre-hash journal, both sides still identical, then empty Move Resume.

    Resume removes the source. The saved log must carry the hash of that
    pair, and Undo must put those bytes back at the source and remove
    the destination. Same end state as a journal that stored the hash.
    """
    src, dst = _dirs(tmp_path)
    source = src / "shot.jpg"
    dest = dst / "2024" / "shot.jpg"
    dest.parent.mkdir(parents=True)
    source.write_bytes(_ORIGINAL)
    dest.write_bytes(_ORIGINAL)
    with JournalWriter(dst) as writer:
        writer.record("move", str(source), str(dest))
    recorded = json.loads(
        journal_path_for(dst).read_text(encoding="utf-8").strip())
    assert "sha256" not in recorded
    assert "size" not in recorded

    log, summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    assert summary["moved"] == 0
    assert not source.exists()
    assert dest.read_bytes() == _ORIGINAL
    assert log.operations[0].status == "done"
    assert log.operations[0].sha256 == _digest(_ORIGINAL)
    assert log.operations[0].size == len(_ORIGINAL)
    saved = load_log(dst)
    assert saved is not None
    assert saved.operations[0].sha256 == _digest(_ORIGINAL)
    assert saved.operations[0].size == len(_ORIGINAL)
    result = undo_log(saved, dst)
    assert result["undone"] == 1
    assert result["kept"] == 0
    assert result["failed"] == 0
    assert source.read_bytes() == _ORIGINAL
    assert not dest.exists()
    assert not list(src.rglob("*restored*"))
    assert not list(dst.rglob("*restored*"))


def test_legacy_matching_move_copy_resume_does_not_hash_or_unlink(tmp_path):
    """Copy Resume does not remove the source, so it must not invent a hash.

    Undo of that log leaves both copies where they are. A hash here would
    relocate the destination beside the source.
    """
    src, dst = _dirs(tmp_path)
    source = src / "shot.jpg"
    dest = dst / "2024" / "shot.jpg"
    dest.parent.mkdir(parents=True)
    source.write_bytes(_ORIGINAL)
    dest.write_bytes(_ORIGINAL)
    with JournalWriter(dst) as writer:
        writer.record("move", str(source), str(dest))

    log, summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True))

    assert summary["copied"] == 0
    assert source.read_bytes() == _ORIGINAL
    assert dest.read_bytes() == _ORIGINAL
    assert log.operations[0].status == "done"
    assert log.operations[0].sha256 == ""
    assert log.operations[0].size == -1
    result = undo_log(log, dst)
    assert result["undone"] == 0
    assert result["kept"] == 1
    assert source.read_bytes() == _ORIGINAL
    assert dest.read_bytes() == _ORIGINAL
    assert not list(src.rglob("*restored*"))
    assert not list(dst.rglob("*restored*"))


def test_seeded_matching_move_still_undoes(tmp_path):
    """A journaled move whose destination still matches the recorded hash.

    The source is still on disk, as in a cross-volume move journaled
    before the unlink. Empty Resume unlinks that source and Undo puts
    the same bytes back.
    """
    src, dst = _dirs(tmp_path)
    source = src / "shot.jpg"
    dest = dst / "2024" / "shot.jpg"
    dest.parent.mkdir(parents=True)
    source.write_bytes(_ORIGINAL)
    dest.write_bytes(_ORIGINAL)
    with JournalWriter(dst) as writer:
        writer.record(
            "move", str(source), str(dest),
            sha256=_digest(_ORIGINAL), size=len(_ORIGINAL))

    log, summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    assert summary["moved"] == 0
    assert not source.exists()
    assert dest.read_bytes() == _ORIGINAL
    assert log.operations[0].status == "done"
    assert log.operations[0].sha256 == _digest(_ORIGINAL)
    result = undo_log(log, dst)
    assert result["undone"] == 1
    assert result["kept"] == 0
    assert result["failed"] == 0
    assert source.read_bytes() == _ORIGINAL
    assert not dest.exists()
    assert not list(src.rglob("*restored*"))


def test_normal_move_journals_hash_and_undo_restores(tmp_path):
    src, dst = _dirs(tmp_path)
    photo = make_jpeg_with_exif(
        src / "IMG_1.jpg", datetime(2024, 7, 15, 10, 30, 0))
    payload = photo.read_bytes()
    options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False)
    plan = build_plan(options)
    log, summary = execute_plan(plan, options)
    moved = plan[0].destination
    assert summary["moved"] == 1
    assert not photo.exists()
    assert moved.read_bytes() == payload

    journal = journal_path_for(dst).read_text(encoding="utf-8").splitlines()
    recorded = json.loads(journal[0])
    assert recorded["sha256"] == _digest(payload)
    assert recorded["size"] == len(payload)
    assert recorded["action"] == "move"
    assert find_unfinished_journal(dst) is None

    result = undo_log(log, dst)
    assert result["undone"] == 1
    assert result["kept"] == 0
    assert photo.read_bytes() == payload
    assert not moved.exists()


def test_copy_undo_still_compares_source_and_destination(tmp_path):
    """Copy undo does not use the move-hash rule.

    A matching copy is removed. A destination that no longer matches the
    source is left in place, including when the journal stored no hash.
    """
    src, dst = _dirs(tmp_path)
    source = src / "shot.jpg"
    dest = dst / "2024" / "shot.jpg"
    dest.parent.mkdir(parents=True)
    source.write_bytes(_ORIGINAL)
    dest.write_bytes(_ORIGINAL)
    with JournalWriter(dst) as writer:
        writer.record("copy", str(source), str(dest))

    log, _summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True))
    assert log.operations[0].action == "copy"
    assert log.operations[0].sha256 == ""
    result = undo_log(log, dst)
    assert result["undone"] == 1
    assert not dest.exists()
    assert source.read_bytes() == _ORIGINAL

    source.write_bytes(_ORIGINAL)
    dest.write_bytes(_REPLACEMENT)
    with JournalWriter(dst) as writer:
        writer.record("copy", str(source), str(dest))
    log, _summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True))
    result = undo_log(log, dst)
    assert result["undone"] == 0
    assert result["kept"] == 1
    assert dest.read_bytes() == _REPLACEMENT
    assert source.read_bytes() == _ORIGINAL


def test_completed_operations_keep_journaled_integrity(tmp_path):
    dst = tmp_path / "dst"
    dst.mkdir()
    with JournalWriter(dst) as writer:
        writer.record(
            "move", "S", "D", sha256=_digest(_ORIGINAL), size=len(_ORIGINAL))
        writer.record("move", "legacy-source", "legacy-dest")
    ops = completed_operations(journal_path_for(dst))
    assert ops[0]["sha256"] == _digest(_ORIGINAL)
    assert ops[0]["size"] == len(_ORIGINAL)
    assert ops[1]["sha256"] == ""
    assert ops[1]["size"] == -1
