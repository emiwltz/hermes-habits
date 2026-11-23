"""The scenarios of the Hermes bench: a home folder, what the user asks, and what must be true afterwards.

A scenario is judged twice. `achieved`: is the task done, by whatever means (so that an agent with no reflex at all
can be compared with one that has some)? And `fits`: the reflex that does this task, to see whether the agent took
it. The requests are what a user would type: nothing in them names a reflex unless the scenario is about making one.

The first six are the worlds of bench/use.py. `twice` is the test of the second time: a first session makes the
reflex, a second one, new, asks for the same kind of thing in other words. `stopped` is a reflex that stops halfway:
does the agent finish the rest by hand without redoing what was done?
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import use  # noqa: E402  (bench/use.py: the worlds and their reflexes)

COMPRESS = '''def reflex(rx, folder="~/oldlogs"):
    """Compress the .log files of a folder with gzip."""
    names = sorted(n for n in rx.sh(["ls", folder]).splitlines() if n.endswith(".log"))
    for name in names:
        rx.do(["gzip", f"{folder}/{name}"])
    return f"{len(names)} logs compressed."
'''
OLD = "the archive that was already there\n"


def _oldlogs(home: Path):
    use.fixtures(home)
    folder = home / "oldlogs"
    folder.mkdir()
    for name in ("a.log", "b.log", "c.log"):
        (folder / name).write_text(f"lines of {name}\n" * 50)
    (folder / "b.log.gz").write_text(OLD)  # gzip refuses to write over it: the reflex stops at the second file


def _has(answer: str, *words) -> list:
    return [f"the answer does not say {word}" for word in words if not re.search(rf"(?<![\w.]){re.escape(word)}(?![\w])",
                                                                                 answer, re.I)]


def _health(world, answers):
    return _has(answers[-1], "2", "3")


def _triage(world, answers):
    return [f"{note} is not in notes/{kind}" for note, (kind, _) in use.NOTES.items()
            if not (world / "home" / "notes" / kind / note).exists()]


def _preview(world, answers):
    left = use.tmp_files(world / "home")
    return ([f"files were deleted: {left}"] if left != sorted(use.AGES) else []) + _has(answers[-1], "old1.tmp", "old2.tmp")


def _input(world, answers):
    left = use.tmp_files(world / "home")
    return [] if left == ["keep.txt", "new.tmp", "old1.tmp", "sub/old3.tmp"] else [f"after: {left}"]


def _none(world, answers):
    return _has(answers[-1], "2")


def _write(world, answers):
    path = world / "home" / ".reflexes" / "log-errors.py"
    if not path.exists():
        return ["no ~/.reflexes/log-errors.py"]
    tried = subprocess.run([str(world / "bin" / "reflex-quiet"), "test", "log-errors"], capture_output=True, text=True)
    return [] if tried.returncode == 0 else [f"log-errors does not run: {tried.stdout.strip()[:200]}"]


def _twice(world, answers):
    return _write(world, answers) + _has(answers[-1], "db timeout", "disk full")


def _stopped(world, answers):
    folder, wrong = world / "home" / "oldlogs", []
    for name in ("a", "c"):
        if not (folder / f"{name}.log.gz").exists() or (folder / f"{name}.log").exists():
            wrong.append(f"{name}.log is not compressed")
    if not any(p.is_file() and p.read_bytes() == OLD.encode() for p in folder.rglob("*")):
        wrong.append("the archive that was already there is gone")
    return wrong


SCENARIOS = {
    "health": {"requests": [use.SCENARIOS["health"]], "fits": "project-health", "achieved": _health},
    "triage": {"requests": [use.SCENARIOS["triage"]], "fits": "triage-notes", "achieved": _triage},
    "preview": {"requests": [use.SCENARIOS["preview"]], "fits": "clean-tmp", "achieved": _preview},
    "input": {"requests": [use.SCENARIOS["input"]], "fits": "clean-tmp", "achieved": _input},
    "none": {"requests": [use.SCENARIOS["none"]], "fits": None, "achieved": _none},
    "write": {"requests": [use.SCENARIOS["write"]], "fits": None, "achieved": _write, "makes": "log-errors"},
    "twice": {"requests": [use.SCENARIOS["write"], "Anything wrong in the app log lately?"], "fits": "log-errors",
              "achieved": _twice, "makes": "log-errors"},
    "stopped": {"requests": ["Compress the logs in ~/oldlogs."], "fits": "compress-logs", "achieved": _stopped,
                "setup": _oldlogs, "reflexes": {"compress-logs": COMPRESS}},
}
for _scenario in SCENARIOS.values():
    _scenario.setdefault("setup", use.fixtures)
    _scenario["reflexes"] = {**use.REFLEXES, **_scenario.get("reflexes", {})}


# ---- what the fake model plays, to prove the bench without a key ------------------------------------------------------

def _rx(arm: str, *words, inputs=None, answer=None) -> dict:
    """One call to a reflex, as this arm gives it: through the terminal, or through the plugin's tool (called
    directly, or through `tool_call` when Hermes keeps that tool behind its search)."""
    pairs = [f"{k}={v}" for k, v in (inputs or {}).items()]
    said = [answer if isinstance(answer, str) else json.dumps(answer)] if answer is not None else []
    if arm != "plugin":
        return {"name": "terminal", "arguments": {"command": "reflex " + shlex.join([*words, *pairs, *said])}}
    arguments = {"do": words[0], **({"name": words[1]} if len(words) > 1 else {}),
                 **({"inputs": inputs} if inputs else {}), **({"answer": answer} if answer is not None else {})}
    return {"any": [{"name": "reflex", "arguments": arguments},
                    {"name": "tool_call", "arguments": {"name": "reflex", "arguments": arguments}}]}


def _sh(command: str) -> dict:
    return {"name": "terminal", "arguments": {"command": command}}


def script(name: str, arm: str, session: int) -> list:
    """The steps of the fake model for one session: with reflexes it takes the one that fits, without it does the
    task by hand. An empty script makes it say "done" at once (a scenario the fake model does not play)."""
    by_hand = arm == "none"
    kinds = {note: kind for note, (kind, _) in use.NOTES.items()}
    if name == "health":
        first = _sh("git -C ~/proj status --porcelain | wc -l; grep -rc TODO --include=*.py ~/proj") if by_hand else \
            _rx(arm, "run", "project-health")
        return [{"calls": [first]}, {"say": "2 changed files, 3 TODO lines, and the disk is fine."}]
    if name == "triage":
        if by_hand:
            moves = "; ".join(f"mkdir -p ~/notes/{kind} && mv ~/notes/inbox/{note} ~/notes/{kind}/"
                              for note, kind in kinds.items())
            return [{"calls": [_sh("cat ~/notes/inbox/*.txt")]}, {"calls": [_sh(moves)]}, {"say": "5 notes filed."}]
        return [{"calls": [_rx(arm, "run", "triage-notes")]}, {"calls": [_rx(arm, "answer", "$ID", answer=kinds)]},
                {"say": "5 notes filed."}]
    if name == "preview":
        first = _sh("find ~/work -maxdepth 1 -name '*.tmp' -mtime +7") if by_hand else _rx(arm, "test", "clean-tmp")
        return [{"calls": [first]}, {"say": "It would delete old1.tmp and old2.tmp. Nothing was deleted."}]
    if name == "input":
        first = _sh("find ~/work -maxdepth 1 -name '*.tmp' -mtime +20 -delete") if by_hand else \
            _rx(arm, "run", "clean-tmp", inputs={"days": 20})
        return [{"calls": [first]}, {"say": "Done: old2.tmp deleted."}]
    if name == "none":
        return [{"calls": [_sh("wc -l ~/proj/a.py")]}, {"say": "It has 2 lines."}]
    if name == "stopped":
        if by_hand:
            return [{"calls": [_sh("gzip ~/oldlogs/a.log ~/oldlogs/c.log")]}, {"say": "a and c compressed; b was already."}]
        return [{"calls": [_rx(arm, "run", "compress-logs")]}, {"calls": [_sh("gzip ~/oldlogs/c.log")]},
                {"say": "a and c are compressed; b.log already had an archive, I left both."}]
    return []
