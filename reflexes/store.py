"""Where reflexes live on disk, and where a run that asked waits for its answer.

A reflex is a file `<name>.py`, or a folder `<name>/reflex.py` (a skill folder can hold one next to its SKILL.md).
They are looked for in the folders of REFLEX_PATH, else in ./.reflexes then ~/.reflexes.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,59}$")
KEPT = 24 * 3600  # seconds a run that asked waits for its answer


def home() -> Path:
    return Path(os.environ.get("REFLEX_HOME") or Path.home() / ".reflexes")


def folders() -> list:
    listed = os.environ.get("REFLEX_PATH")
    if listed:
        return [Path(p).expanduser() for p in listed.split(os.pathsep) if p]
    return [Path.cwd() / ".reflexes", home()]


def everything() -> dict:
    """name -> file, the first folder that has a name wins."""
    found = {}
    for folder in folders():
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            if entry.is_file() and entry.suffix == ".py" and NAME.match(entry.stem):
                found.setdefault(entry.stem, entry)
            elif entry.is_dir() and NAME.match(entry.name) and (entry / "reflex.py").is_file():
                found.setdefault(entry.name, entry / "reflex.py")
    return found


def find(target: str) -> Path | None:
    """A reflex by name, or by the path of its file."""
    if target.endswith(".py") or os.sep in target:
        path = Path(target).expanduser()
        return path if path.is_file() else None
    return everything().get(target)


def _runs() -> Path:
    return home() / ".runs"


def keep(state: bytes) -> str:
    """Keep a run that asked; the short id it is answered by."""
    folder = _runs()
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in folder.glob("*.run"):  # a question nobody answered in a day is dropped
        try:
            if now - old.stat().st_mtime > KEPT:
                old.unlink()
        except OSError:
            pass
    run_id = secrets.token_hex(3)
    path = folder / f"{run_id}.run"
    path.write_bytes(state)
    path.chmod(0o600)
    return run_id


def kept(run_id: str) -> bytes | None:
    path = _runs() / f"{run_id}.run"
    return path.read_bytes() if re.fullmatch(r"[0-9a-f]{6}", run_id) and path.is_file() else None


def drop(run_id: str) -> None:
    try:
        (_runs() / f"{run_id}.run").unlink()
    except OSError:
        pass
