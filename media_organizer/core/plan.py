"""Operation log + undo support.

Every executed run writes a JSON log of the file operations performed, so a
run can be undone later (files moved back to their original locations).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIRNAME = ".media_organizer"
LOG_FILENAME = "operation_log.json"


@dataclass
class Operation:
    action: str  # "copy" | "move"
    source: str
    destination: str
    status: str = "pending"  # pending | done | skipped | error
    error: str = ""


@dataclass
class RunLog:
    started_at: str
    dest_dir: str
    operations: list[Operation] = field(default_factory=list)
    undone: bool = False

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "dest_dir": self.dest_dir,
            "undone": self.undone,
            "operations": [vars(op) for op in self.operations],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunLog":
        return cls(
            started_at=data["started_at"],
            dest_dir=data["dest_dir"],
            operations=[Operation(**op) for op in data.get("operations", [])],
            undone=data.get("undone", False),
        )


def log_path_for(dest_dir: Path | str) -> Path:
    return Path(dest_dir) / LOG_DIRNAME / LOG_FILENAME


def new_log(dest_dir: Path | str) -> RunLog:
    return RunLog(started_at=datetime.now().isoformat(timespec="seconds"),
                  dest_dir=str(dest_dir))


def save_log(log: RunLog, dest_dir: Path | str) -> Path:
    path = log_path_for(dest_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log.to_dict(), indent=2), encoding="utf-8")
    return path


def load_log(dest_dir: Path | str) -> Optional[RunLog]:
    path = log_path_for(dest_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunLog.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _move_back(src: Path, dest: Path) -> None:
    """Move `dest` back to `src`, never overwriting anything at `src`."""
    target = src
    if target.exists():
        stem, suffix = src.stem, src.suffix
        n = 0
        while target.exists():
            n += 1
            target = src.with_name(f"{stem}_restored_{n}{suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dest), str(target))


def undo_log(log: RunLog, dest_dir: Path | str) -> dict:
    """Undo every successful operation in the log. Returns a summary dict.

    Copies are undone by deleting the copied file; moves by moving the file
    back. Nothing is ever overwritten.
    """
    undone = skipped = failed = 0
    # Reverse order so suffix chains unwind cleanly
    for op in reversed(log.operations):
        if op.status != "done":
            skipped += 1
            continue
        src, dest = Path(op.source), Path(op.destination)
        try:
            if op.action == "copy":
                if dest.exists():
                    dest.unlink()
                    undone += 1
                else:
                    skipped += 1
            elif op.action == "move":
                if dest.exists():
                    _move_back(src, dest)
                    undone += 1
                else:
                    skipped += 1
            else:
                skipped += 1
        except OSError:
            failed += 1
    log.undone = True
    save_log(log, dest_dir)
    return {"undone": undone, "skipped": skipped, "failed": failed}
