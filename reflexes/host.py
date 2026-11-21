"""The local host: what a reflex's effects do on this machine.

The engine decides what an effect means (a read runs in a dry run, a change is only planned, a question may go up);
a host only carries it out. Another host, an agent's own tools for one, has the same members.
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import threading

from . import readers

MOST = 8_000_000  # bytes of one output or one file a reflex may be handed; past it the command is stopped


class Local:
    workers = 8  # commands of one rx.sh that may run side by side

    def __init__(self, cwd: str | None = None, extra_readers: dict | None = None, home: str | None = None,
                 env: dict | None = None):
        self.cwd = cwd or os.getcwd()  # OSError when the folder we are in is gone: there is nowhere to run
        self.home = home or os.path.expanduser("~")
        self.extra_readers = extra_readers or {}
        self.env = env or {}  # variables its commands get on top of this process's own

    def path(self, word: str) -> str:
        """A path or a command word with its ~ written out."""
        return self.home + word[1:] if word == "~" or word.startswith("~/") else word

    def environment(self) -> dict:
        """The variables its commands run with; os.getenv in a reflex reads the same. git is told not to refresh its
        index behind a read (status and diff would otherwise write .git/index)."""
        return {**os.environ, "HOME": self.home, "GIT_OPTIONAL_LOCKS": "0", **self.env}

    def reads_only(self, argv: list) -> str:
        """"" when the command only reads, else why it is not a read."""
        return readers.why_not(argv, self.extra_readers)

    def reads_outside(self, argv: list) -> bool:
        """Whether this read asks another machine."""
        return readers.outside(argv)

    def run(self, argv: list, seconds: float, input: str | None = None) -> tuple:
        """(output, errors, exit code) of one program, run without a shell. Its output is read as it comes and the
        program is stopped past MOST bytes: an endless one (cat /dev/zero) must not fill this process. Stopped
        means the program and whatever it started: it runs in a group of its own, and the group is killed."""
        try:
            child = subprocess.Popen(argv, cwd=self.cwd, env=self.environment(), stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True,
                                     stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL)
        except FileNotFoundError:
            return "", f"{argv[0]}: command not found", 127
        except PermissionError:
            return "", f"{argv[0]}: permission denied", 126
        except NotADirectoryError:
            return "", f"{self.cwd}: not a folder", 126
        kept, over = {"out": bytearray(), "err": bytearray()}, threading.Event()

        def stop():
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (OSError, AttributeError):
                child.kill()

        def drain(stream, name):
            try:
                while chunk := stream.read1(65536):
                    if len(kept[name]) + len(chunk) > MOST:
                        over.set()
                        stop()
                        break
                    kept[name] += chunk
            except (OSError, ValueError):
                pass

        def feed():
            try:
                child.stdin.write(input.encode())
                child.stdin.close()
            except (OSError, ValueError):
                pass
        threads = [threading.Thread(target=drain, args=(child.stdout, "out"), daemon=True),
                   threading.Thread(target=drain, args=(child.stderr, "err"), daemon=True)]
        if input is not None:
            threads.append(threading.Thread(target=feed, daemon=True))
        for thread in threads:
            thread.start()
        late = False
        try:
            child.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            late = True
            stop()
            child.wait()
        except BaseException:  # interrupted (Ctrl-C): nothing of the command is left behind
            stop()
            raise
        for thread in threads:
            thread.join(1.0)  # a child of the program may still hold the pipes: what came so far is the output
        out, err = (bytes(kept[name]).decode("utf-8", "replace") for name in ("out", "err"))
        if over.is_set():
            return "", f"more than {MOST // 1_000_000} MB of output: narrow the command", 125
        if late:
            return out, f"timed out after {seconds:.0f} s", 124
        return out, err, child.returncode

    def read(self, path: str) -> str:
        """The text of a regular file. A pipe or a device is refused: it may never end, or never start."""
        handle = os.open(os.path.join(self.cwd, path), os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(handle, "rb") as file:
            if not stat.S_ISREG(os.fstat(handle).st_mode):
                raise OSError("not a regular file")
            data = file.read(MOST + 1)
        if len(data) > MOST:
            raise OSError(f"more than {MOST // 1_000_000} MB: read a part of it with rx.sh(['head', ...]) or grep")
        return data.decode("utf-8", "replace")

    def write(self, path: str, text: str, append: bool = False) -> None:
        full = os.path.join(self.cwd, path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "a" if append else "w", encoding="utf-8") as handle:
            handle.write(text)
