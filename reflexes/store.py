"""Where reflexes live on disk, and where a run that asked waits for its answer.

A reflex is a file `<name>.py`, or a folder `<name>/reflex.py` (a skill folder can hold one next to its SKILL.md).
They are looked for in the folders of REFLEX_PATH, else in ~/.reflexes then ./.reflexes: the user's own come first,
so a project's folder can add reflexes but never replace one by taking its name.
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
    """Where the user's own reflexes and the runs that wait are kept: the same place from any folder."""
    return Path(os.path.abspath(os.path.expanduser(os.environ.get("REFLEX_HOME") or "~/.reflexes")))


def folders() -> list:
    listed = os.environ.get("REFLEX_PATH")
    if listed:
        found = [Path(os.path.expanduser(p)) for p in listed.split(os.pathsep) if p]  # ~nobody stays as written
    else:
        found = [home()]
        try:
            found.append(Path.cwd() / ".reflexes")
        except OSError:  # the folder we are in is gone: the user's own reflexes are still there
            pass
    return list(dict.fromkeys(found))  # each folder once (at home, both are the same)


def survey() -> tuple:
    """(name -> file, notes): every reflex, the first folder that has a name wins; the notes say what was left out
    (a name taken by an earlier folder, a file whose name is no reflex name)."""
    found, notes = {}, []
    for folder in folders():
        try:
            entries = sorted(folder.iterdir()) if folder.is_dir() else []
        except OSError:
            continue
        for entry in entries:
            if entry.is_file() and entry.suffix == ".py":
                name, path = entry.stem, entry
            elif entry.is_dir() and (entry / "reflex.py").is_file():
                name, path = entry.name, entry / "reflex.py"
            else:
                continue
            if not NAME.match(name):
                notes.append(f"{path} is left out: a reflex name is lowercase letters, digits, - and _")
            elif name in found:
                notes.append(f"{path} is hidden by {found[name]}")
            else:
                found[name] = path
    return found, notes


def everything() -> dict:
    return survey()[0]


def find(target: str) -> Path | None:
    """A reflex by name, or by the path of its file."""
    if target.endswith(".py") or os.sep in target:
        try:
            path = Path(os.path.expanduser(target))
            if path.is_file():
                return path
        except (OSError, ValueError):  # a name too long to be a file, a null byte in it
            return None
        target = target[:-3] if target.endswith(".py") and os.sep not in target else target
    return everything().get(target)


def _runs() -> Path:
    return home() / ".runs"


def _path(run_id: str) -> Path | None:
    return _runs() / f"{run_id}.run" if re.fullmatch(r"[0-9a-f]{6}", run_id) else None


def keep(state: bytes) -> str:
    """Keep a run that asked; the short id it is answered by. The file is the owner's alone from its first byte."""
    folder = _runs()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = time.time()
    for old in [*folder.glob("*.run"), *folder.glob("*.taken")]:  # a question nobody answered in a day is dropped
        try:
            if now - old.stat().st_mtime > KEPT:
                old.unlink()
        except OSError:
            pass
    for _ in range(20):
        run_id = secrets.token_hex(3)
        try:
            handle = os.open(folder / f"{run_id}.run", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue  # another run waits under that id
        with os.fdopen(handle, "wb") as file:
            file.write(state)
        return run_id
    raise OSError("no free id for the run")


def take(run_id: str) -> bytes | None:
    """The run that waits under this id, taken out of the store: whoever takes it is the only one to answer it.
    None when there is none (never asked, already answered, or older than a day)."""
    path = _path(run_id)
    if path is None:
        return None
    mine = path.with_suffix(f".{os.getpid()}.{secrets.token_hex(2)}.taken")
    try:
        os.rename(path, mine)  # one caller wins: the others find no file
    except OSError:
        return None
    try:
        fresh = time.time() - mine.stat().st_mtime <= KEPT
        return mine.read_bytes() if fresh else None
    finally:
        try:
            mine.unlink()
        except OSError:
            pass


def put_back(run_id: str, state: bytes) -> bool:
    """Keep a taken run under its id again (its answer did not fit, the question still stands)."""
    path = _path(run_id)
    try:
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except (OSError, TypeError):
        return False
    with os.fdopen(handle, "wb") as file:
        file.write(state)
    return True
