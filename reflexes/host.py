"""The local host: what a reflex's effects do on this machine.

The engine decides what an effect means (a read runs in a dry run, a change is only planned, a question may go up);
a host only carries it out. Another host, an agent's own tools for one, has the same members.
"""

from __future__ import annotations

import os
import subprocess

from . import readers


class Local:
    workers = 8  # commands of one rx.sh that may run side by side

    def __init__(self, cwd: str | None = None, extra_readers: dict | None = None, home: str | None = None,
                 env: dict | None = None):
        self.cwd = cwd or os.getcwd()
        self.home = home or os.path.expanduser("~")
        self.extra_readers = extra_readers or {}
        self.env = env or {}  # variables its commands get on top of this process's own

    def path(self, word: str) -> str:
        """A path or a command word with its ~ written out."""
        return self.home + word[1:] if word == "~" or word.startswith("~/") else word

    def environment(self) -> dict:
        """The variables its commands run with; os.getenv in a reflex reads the same."""
        return {**os.environ, "HOME": self.home, **self.env}

    def reads_only(self, argv: list) -> str:
        """"" when the command only reads, else why it is not a read."""
        return readers.why_not(argv, self.extra_readers)

    def reads_outside(self, argv: list) -> bool:
        """Whether this read asks another machine."""
        return readers.outside(argv)

    def run(self, argv: list, seconds: float, input: str | None = None) -> tuple:
        """(output, errors, exit code) of one program, run without a shell."""
        try:
            done = subprocess.run(argv, cwd=self.cwd, input=input, capture_output=True, text=True, errors="replace",
                                  timeout=seconds, stdin=None if input is not None else subprocess.DEVNULL,
                                  env=self.environment())
        except FileNotFoundError:
            return "", f"{argv[0]}: command not found", 127
        except PermissionError:
            return "", f"{argv[0]}: permission denied", 126
        except NotADirectoryError:
            return "", f"{self.cwd}: not a folder", 126
        except subprocess.TimeoutExpired:
            return "", f"timed out after {seconds:.0f} s", 124
        return done.stdout, done.stderr, done.returncode

    def read(self, path: str) -> str:
        with open(os.path.join(self.cwd, path), encoding="utf-8", errors="replace") as handle:
            return handle.read()

    def write(self, path: str, text: str, append: bool = False) -> None:
        full = os.path.join(self.cwd, path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "a" if append else "w", encoding="utf-8") as handle:
            handle.write(text)
