"""Reflexes for Hermes.

All of it: at the start of each session the agent is told which reflexes it has, in its prompt. It runs them with
the `reflex` command in its terminal, like any command.

Why so little. Given reflexes through a tool or a skill, a cheap model never went looking for them (0 times of 48);
with their names and what they do in front of it, it took the one that fitted 60 times of 60. And a list written
once goes stale: this one is asked of `reflex note` each time, so a reflex made yesterday is in it today.
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOM = 3800  # Hermes drops a plugin's section past 4,000 characters: the note is cut to fit, never lost
STARTERS = ("hermes-health", "disk-space")  # what a new user finds in ~/.reflexes: something to run, and to read
logger = logging.getLogger(__name__)


def command() -> str:
    """The `reflex` the agent will find: the one installed with Hermes's own Python comes first on its PATH."""
    beside = Path(sys.executable).parent / "reflex"
    return os.environ.get("REFLEX_COMMAND") or (str(beside) if beside.exists() else shutil.which("reflex") or "")


def note(session=None) -> str:
    found = command()
    if not found:
        logger.warning("habits: no `reflex` command found, so the agent is told nothing about reflexes")
        return ""
    try:
        said = subprocess.run([found, "note", "--chars", str(ROOM)], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("habits: `reflex note` did not run: %s", exc)
        return ""
    return said.stdout.strip() if said.returncode == 0 else ""


def start() -> None:
    """The first time, the folder of reflexes is made with two in it. Only then: a folder emptied stays empty."""
    home = Path(os.environ.get("REFLEX_HOME") or "~/.reflexes").expanduser()
    if home.exists():
        return
    try:
        home.mkdir(parents=True)
        for name in STARTERS:
            shutil.copy(Path(__file__).parent / "examples" / f"{name}.py", home / f"{name}.py")
    except OSError as exc:
        logger.warning("habits: the first reflexes could not be put in %s: %s", home, exc)


def register(ctx):
    start()
    ctx.register_system_prompt_section("habits.reflexes", note, position="after_memory", max_chars=4000)
