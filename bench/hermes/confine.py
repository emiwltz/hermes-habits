#!/usr/bin/env python3
"""Run a command that can write only where it is told, and cannot read a folder kept from it.

    python confine.py --write DIR [--write DIR ...] [--hide DIR] [--read DIR ...] -- command [argument ...]
    python confine.py --check [FOLDER]

A world of the bench is a home folder the agent is told is its own. Nothing made it stay there: in the first runs
one agent, looking for the place where reflexes are saved, wrote a file into the repository the bench ran from. On
a machine in real use that is not good enough, so the operating system holds the line:

    --write DIR   the command may read and write there (and in the temporary folders and /dev)
    --hide DIR    the command may not read anything under DIR (the real home folder)
    --read DIR    except this, which is inside a hidden folder and is needed (a Python, a checkout)

Everything else can be read and not written. On Linux this is Landlock (kernel 5.13 or later; from 6.12 the command
also cannot send a signal to a process outside itself). On macOS it is sandbox-exec, with the same three rules and
no signal to others. The limits hold for everything the command starts. `--check` tries them on this machine, in
a folder it makes and removes under FOLDER (the home folder when none is given).

It is a fence for an agent that wanders, not a prison for one that attacks: the network stays open, and so does
whatever the system lets any program of this user do without touching a file.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Landlock: the system calls and the rights on files, by the version of the interface that brought them
CREATE, ADD, RESTRICT = 444, 445, 446
EXECUTE, WRITE_FILE, READ_FILE, READ_DIR, REFER, TRUNCATE, IOCTL_DEV = 1, 2, 4, 8, 1 << 13, 1 << 14, 1 << 15
READ = EXECUTE | READ_FILE | READ_DIR
ON_A_FILE = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE | IOCTL_DEV  # the rights that mean something on a file


class Unavailable(Exception):
    """This machine cannot confine a command."""


def real(path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(str(path))))


def always_written() -> list:
    """Where any program writes: the temporary folders, the devices, what the system keeps of the processes."""
    places = ["/tmp", "/var/tmp", "/dev", "/proc", tempfile.gettempdir(), os.environ.get("TMPDIR", "")]
    if sys.platform == "darwin":
        places += ["/private/tmp", "/private/var/tmp", "/private/var/folders"]
    return sorted({real(p) for p in places if p and real(p).exists()})


def inside(path: Path, folders: list) -> bool:
    return any(path == f or f in path.parents for f in folders)


def visible(hidden: list) -> list:
    """Everything but the hidden folders: the entries of /, and next to each folder that leads to a hidden one,
    its other entries."""
    found, todo = [], [Path("/")]
    while todo:
        folder = todo.pop()
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            path = real(entry)  # a link is what it leads to, or it would lead around the fence
            if inside(path, hidden) or not path.exists():
                continue
            if any(path in h.parents for h in hidden):
                todo.append(path)
            elif path not in found:
                found.append(path)
    return found


def landlock(write: list, hide: list, read: list) -> None:
    """Confine this process, and all it will start, with Landlock."""
    libc = ctypes.CDLL(None, use_errno=True)
    version = libc.syscall(CREATE, None, 0, 1)
    if version < 1:
        raise Unavailable(f"no Landlock on this kernel ({os.strerror(ctypes.get_errno())})")
    rights = (1 << 13) - 1 | (REFER if version >= 2 else 0) | (TRUNCATE if version >= 3 else 0) | \
        (IOCTL_DEV if version >= 5 else 0)

    class Ruleset(ctypes.Structure):
        _fields_ = [("files", ctypes.c_uint64), ("network", ctypes.c_uint64), ("scoped", ctypes.c_uint64)]

    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed", ctypes.c_uint64), ("folder", ctypes.c_int32)]

    # from version 6: no signal to a process outside, no socket of theirs by its abstract name
    attributes = Ruleset(rights, 0, 3 if version >= 6 else 0)
    size = 8 if version < 4 else 16 if version < 6 else 24
    ruleset = libc.syscall(CREATE, ctypes.byref(attributes), size, 0)
    if ruleset < 0:
        raise Unavailable(f"Landlock refused the rules ({os.strerror(ctypes.get_errno())})")

    def allow(path: Path, wanted: int) -> None:
        try:
            handle = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            return  # what cannot even be opened needs no rule
        try:
            allowed = wanted & rights & (ON_A_FILE if not path.is_dir() else rights)
            if allowed and libc.syscall(ADD, ruleset, 1, ctypes.byref(Beneath(allowed, handle)), 0):
                raise Unavailable(f"Landlock refused {path} ({os.strerror(ctypes.get_errno())})")
        finally:
            os.close(handle)

    for path in visible(hide) + read:
        allow(path, READ)
    for path in always_written() + write:
        allow(path, rights)
    if libc.prctl(38, 1, 0, 0, 0) or libc.syscall(RESTRICT, ruleset, 0):  # 38: no new privileges, which Landlock asks
        raise Unavailable(f"Landlock could not be put on ({os.strerror(ctypes.get_errno())})")
    os.close(ruleset)


def seatbelt(write: list, hide: list, read: list) -> list:
    """The words to put before a command on macOS so that it runs confined."""
    if not Path("/usr/bin/sandbox-exec").exists():
        raise Unavailable("no /usr/bin/sandbox-exec on this Mac")

    def paths(folders: list) -> str:
        return " ".join(f'(subpath "{folder}")' for folder in folders)

    lines = ["(version 1)", "(allow default)", '(deny file-write* (subpath "/"))',
             f"(allow file-write* {paths(always_written() + write)})"]
    if hide:
        lines.append(f"(deny file-read* {paths(hide)})")
        kept = [p for p in read + write if inside(p, hide)]
        if kept:
            lines.append(f"(allow file-read* {paths(kept)})")
            # a path is walked from the top: the folders above what is kept may be passed through, not listed
            above = sorted({str(parent) for p in kept for parent in p.parents if inside(parent, hide)})
            lines.append("(allow file-read-metadata " + " ".join(f'(literal "{a}")' for a in above) + ")")
    lines += ["(deny signal (target others))", "(allow signal (target same-sandbox))"]
    return ["/usr/bin/sandbox-exec", "-p", "\n".join(lines)]


def confined(command: list, write: list, hide: list, read: list) -> list:
    """The command to run instead, on this system: this script again on Linux, sandbox-exec on macOS."""
    write, hide, read = ([real(p) for p in group] for group in (write, hide, read))
    if sys.platform == "darwin":
        return [*seatbelt(write, hide, read), *command]
    if sys.platform.startswith("linux"):
        if ctypes.CDLL(None, use_errno=True).syscall(CREATE, None, 0, 1) < 1:
            raise Unavailable("no Landlock on this kernel")
        words = [w for flag, group in (("--write", write), ("--hide", hide), ("--read", read)) for p in group
                 for w in (flag, str(p))]
        return [sys.executable, str(Path(__file__).resolve()), *words, "--", *command]
    raise Unavailable(f"no way to confine a command on {sys.platform}")


def check(base: Path) -> int:
    """Try the fence on this machine: what must pass passes, what must be refused is refused."""
    home = real(Path.home())
    if not inside(base, [home]):
        print(f"{base} is not in the home folder: the check hides the home, and needs a folder inside it")
        return 2
    with tempfile.TemporaryDirectory(dir=base, prefix=".confine-check-") as inside_home:
        mine, kept, secret = Path(inside_home) / "mine", Path(inside_home) / "kept", Path(inside_home) / "secret"
        for folder in (mine, kept, secret):
            folder.mkdir()
            (folder / "file").write_text("text\n")
        python, tries, wrong = sys.executable, [], []
        pythons = [Path(sys.prefix), Path(sys.base_prefix), Path(__file__).resolve().parent]
        tries.append(("writes where it may", ["sh", "-c", f'echo x > "{mine}/new"'], True))
        tries.append(("reads what it is given", ["cat", str(kept / "file")], True))
        tries.append(("does not write elsewhere in the home", ["sh", "-c", f'echo x > "{inside_home}/elsewhere"'], False))
        tries.append(("does not write where it only reads", ["sh", "-c", f'echo x > "{kept}/new"'], False))
        tries.append(("does not read what is hidden", ["cat", str(secret / "file")], False))
        tries.append(("does not list what is hidden", ["ls", str(home)], False))
        tries.append(("starts a Python", [python, "-c", "import os, json; os.getcwd()"], True))
        tries.append(("does not signal a process outside", [python, "-c", f"import os; os.kill({os.getpid()}, 0)"], False))
        for what, command, passes in tries:
            try:
                ran = subprocess.run(confined(command, [mine], [home], [kept, *pythons]), capture_output=True,
                                     text=True, cwd=mine, timeout=60)
            except Unavailable as exc:
                print(f"cannot confine: {exc}")
                return 2
            good = (ran.returncode == 0) == passes
            print(f"{'ok  ' if good else 'FAIL'} {what}" + ("" if good else f"   ({ran.stderr.strip()[-200:]})"))
            wrong += [] if good else [what]
    if wrong == ["does not signal a process outside"]:
        print("the files are fenced; signals are not (an older kernel): the agent could still stop a process of yours")
        return 0
    return 1 if wrong else 0


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--check"]:
        sys.exit(check(real(args[1]) if len(args) > 1 else real(Path.home())))
    groups = {"--write": [], "--hide": [], "--read": []}
    while args and args[0] in groups and len(args) > 1:
        groups[args[0]].append(real(args[1]))
        args = args[2:]
    if args[:1] != ["--"] or len(args) < 2:
        sys.exit(__doc__.split("\n\n")[1].rstrip())
    command = args[1:]
    try:
        if sys.platform == "darwin":
            command = [*seatbelt(groups["--write"], groups["--hide"], groups["--read"]), *command]
        else:
            landlock(groups["--write"], groups["--hide"], groups["--read"])
    except Unavailable as exc:
        sys.exit(f"cannot confine: {exc}")
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
