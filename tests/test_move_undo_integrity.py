"""Move undo must not relocate a file that showed up after the journal.

The crash journal used to omit sha256 and size. Resume then hashed whatever
was at the destination when the log was seeded, and move undo trusted that
snapshot. Replacing the destination, or putting a new file there after a
seed that saw nothing, made Undo move the user's file.

A legacy move line still has no hash. Seeding must not adopt a destination
whose source is already gone. When both sides are still present and
identical, empty Move Resume records that pair's hash before it unlinks
the source, so Undo can put the file back. That hash is fsynced onto the
existing journal line before the unlink. A kill before save_log still
leaves a line the next Resume can seed. Resume does not invent a hash
from a destination whose source is already gone.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from media_organizer.core.executor import execute_plan
from media_organizer.core.journal import (
    JournalWriter, completed_operations, completed_sources,
    exclude_completed_sources, find_unfinished_journal,
    journal_path_for,
)
from media_organizer.core.organizer import OrganizeOptions, build_plan
from media_organizer.core.plan import load_log, log_path_for, undo_log
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
    before = journal_path_for(dst).read_text(encoding="utf-8")

    log, _summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    assert log.operations[0].status == "done"
    assert log.operations[0].sha256 == ""
    assert log.operations[0].size == -1
    after = journal_path_for(dst).read_text(encoding="utf-8")
    assert after.startswith(before)
    assert "sha256" not in json.loads(after.splitlines()[0])
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
    before = journal_path_for(dst).read_text(encoding="utf-8")

    log, _summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    assert log.operations[0].status == "done"
    assert log.operations[0].sha256 == ""
    assert log.operations[0].size == -1
    after = journal_path_for(dst).read_text(encoding="utf-8")
    assert after.startswith(before)
    assert "sha256" not in json.loads(after.splitlines()[0])
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
    before = journal_path_for(dst).read_text(encoding="utf-8")

    log, _summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    assert log.operations[0].status == "kept"
    assert source.read_bytes() == b"still-in-the-library"
    after = journal_path_for(dst).read_text(encoding="utf-8")
    assert after.startswith(before)
    assert json.loads(after.splitlines()[0])["sha256"] == _digest(_ORIGINAL)
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
    assert len(log.operations) == 1
    assert log.operations[0].status == "done"
    assert log.operations[0].sha256 == _digest(_ORIGINAL)
    assert log.operations[0].size == len(_ORIGINAL)
    journal_text = journal_path_for(dst).read_text(encoding="utf-8")
    journal_lines = [ln for ln in journal_text.splitlines() if ln.strip()]
    assert journal_lines[-1] == '{"run": "complete"}'
    stamped = json.loads(journal_lines[0])
    assert stamped["sha256"] == _digest(_ORIGINAL)
    assert stamped["size"] == len(_ORIGINAL)
    assert journal_text.count(str(source)) == 1
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


def test_legacy_move_hash_survives_kill_before_save_log(tmp_path, monkeypatch):
    """Kill after the legacy unlink and before save_log.

    The pair hash has to already be on the crash-journal line. The next
    Resume seeds that line and Undo puts the file back. A hash that lived
    only on the in-memory log is gone with the process, and Resume must
    not invent one from the destination. A second hashless line, whose
    source is already missing, stays hashless. The matching line is not
    duplicated.
    """
    src, dst = _dirs(tmp_path)
    source = src / "shot.jpg"
    dest = dst / "2024" / "shot.jpg"
    dest.parent.mkdir(parents=True)
    source.write_bytes(_ORIGINAL)
    dest.write_bytes(_ORIGINAL)
    missing_source = src / "gone.jpg"
    missing_dest = dst / "2024" / "gone.jpg"
    with JournalWriter(dst) as writer:
        writer.record("move", str(source), str(dest))
        writer.record("move", str(missing_source), str(missing_dest))

    class _Killed(BaseException):
        pass

    seen = {}
    armed = {"on": True}
    real_unlink = Path.unlink

    def spy_unlink(self, *args, **kwargs):
        result = real_unlink(self, *args, **kwargs)
        if armed["on"] and self == source:
            seen["journal"] = journal_path_for(dst).read_text(encoding="utf-8")
            seen["log"] = log_path_for(dst).is_file()
            raise _Killed("die before save_log")
        return result

    monkeypatch.setattr(Path, "unlink", spy_unlink)
    with pytest.raises(_Killed):
        execute_plan(
            [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))
    armed["on"] = False

    assert seen["log"] is False
    assert not log_path_for(dst).is_file()
    assert load_log(dst) is None
    assert not source.exists()
    assert dest.read_bytes() == _ORIGINAL
    killed = [ln for ln in seen["journal"].splitlines() if ln.strip()]
    assert len(killed) == 2
    stamped = json.loads(killed[0])
    untouched = json.loads(killed[1])
    assert stamped["sha256"] == _digest(_ORIGINAL)
    assert stamped["size"] == len(_ORIGINAL)
    assert stamped["source"] == str(source)
    assert "sha256" not in untouched
    assert "size" not in untouched
    assert '"run": "complete"' not in seen["journal"]
    assert seen["journal"].count(str(source)) == 1
    open_journal = find_unfinished_journal(dst)
    assert open_journal is not None
    ops = completed_operations(open_journal)
    assert [(op["source"], op["sha256"], op["size"]) for op in ops] == [
        (str(source), _digest(_ORIGINAL), len(_ORIGINAL)),
        (str(missing_source), "", -1),
    ]

    log, summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    assert summary["moved"] == 0
    assert [(op.source, op.sha256, op.size, op.status) for op in log.operations] == [
        (str(source), _digest(_ORIGINAL), len(_ORIGINAL), "done"),
        (str(missing_source), "", -1, "done"),
    ]
    resumed = journal_path_for(dst).read_text(encoding="utf-8")
    resumed_lines = [ln for ln in resumed.splitlines() if ln.strip()]
    assert resumed_lines[-1] == '{"run": "complete"}'
    assert json.loads(resumed_lines[0])["sha256"] == _digest(_ORIGINAL)
    assert "sha256" not in json.loads(resumed_lines[1])
    assert resumed.count(str(source)) == 1
    result = undo_log(log, dst)
    assert result["undone"] == 1
    assert result["kept"] == 0
    assert result["failed"] == 0
    assert source.read_bytes() == _ORIGINAL
    assert not dest.exists()
    assert not missing_dest.exists()
    assert not list(src.rglob("*restored*"))
    assert not list(dst.rglob("*restored*"))


def test_legacy_stamp_still_journals_the_next_file(tmp_path):
    """Rewriting the legacy line must leave the writer able to append.

    Resume still has another file to move. That file's done line is
    written after the stamp, and Undo restores both files once each.
    """
    src, dst = _dirs(tmp_path)
    source = src / "shot.jpg"
    dest = dst / "2024" / "shot.jpg"
    dest.parent.mkdir(parents=True)
    source.write_bytes(_ORIGINAL)
    dest.write_bytes(_ORIGINAL)
    other = make_jpeg_with_exif(
        src / "IMG_1.jpg", datetime(2024, 8, 1, 12, 0, 0))
    other_payload = other.read_bytes()
    with JournalWriter(dst) as writer:
        writer.record("move", str(source), str(dest))
    options = OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False)
    plan = build_plan(options)
    remaining = exclude_completed_sources(
        plan, completed_sources(journal_path_for(dst)))
    assert source not in [item.source for item in remaining]
    assert other in [item.source for item in remaining]

    log, summary = execute_plan(remaining, options)

    assert summary["moved"] == 1
    assert summary["errors"] == 0
    assert not source.exists()
    assert dest.read_bytes() == _ORIGINAL
    assert not other.exists()
    moved = [item.destination for item in remaining
             if item.source == other][0]
    assert moved.read_bytes() == other_payload
    text = journal_path_for(dst).read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[-1] == '{"run": "complete"}'
    assert text.count(str(source)) == 1
    assert text.count(str(other)) == 1
    legacy = json.loads(lines[0])
    assert legacy["sha256"] == _digest(_ORIGINAL)
    assert legacy["size"] == len(_ORIGINAL)
    nxt = json.loads(lines[1])
    assert nxt["source"] == str(other)
    assert nxt["sha256"] == _digest(other_payload)
    done = [op for op in log.operations if op.status == "done"]
    assert len(done) == 2
    assert done[0].sha256 == _digest(_ORIGINAL)
    assert done[1].sha256 == _digest(other_payload)
    result = undo_log(log, dst)
    assert result["undone"] == 2
    assert result["kept"] == 0
    assert result["failed"] == 0
    assert source.read_bytes() == _ORIGINAL
    assert not dest.exists()
    assert other.read_bytes() == other_payload
    assert not moved.exists()
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
    before = journal_path_for(dst).read_text(encoding="utf-8")

    log, summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=True))

    after = journal_path_for(dst).read_text(encoding="utf-8")
    assert after.startswith(before)
    assert "sha256" not in json.loads(after.splitlines()[0])
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
    before = journal_path_for(dst).read_text(encoding="utf-8")

    log, summary = execute_plan(
        [], OrganizeOptions(source_dir=src, dest_dir=dst, copy_mode=False))

    after = journal_path_for(dst).read_text(encoding="utf-8")
    assert after.startswith(before)
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
