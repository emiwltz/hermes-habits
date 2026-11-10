#!/usr/bin/env python3
"""The Habits bench: a habit against Hermes doing the same task alone, and Hermes writing the habit.

    python3 bench/run.py <scenario file or folder> [--only id,...] [--no-baseline] [--no-create]
                         [--budget DOLLARS] [--out results.jsonl] [--dry-check]

Each case runs its habit with `hermes habits run` (dry unless mode: live, and then only inside its scratch
folder; from the case's message through the trigger when it has one; never recorded), then, when the case asks,
the same task as a Hermes turn with habits off (baseline) and a Hermes turn asked to write the habit (create).
One JSON line per run on stdout and in --out, a table per case on stderr. Model turns cost money: --budget
(0.02 $ unless given) stops before the one that would pass it, and before a habit run that would (a case's first
counted at the dearest run so far). Standard library only; a YAML scenario needs PyYAML, which Hermes ships (a
JSON one needs nothing). See bench/README.md.
"""

from __future__ import annotations

import argparse
import ast
import collections
import contextlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRICE_IN, PRICE_OUT = 0.28 / 1e6, 0.42 / 1e6  # $ per token when Hermes made no estimate of a turn's cost
FIRST_CALL = 0.01  # $ a model turn is counted at before one was measured; then the dearest one seen
MODEL_CALL = 0.002  # $ a habit's own model call is counted at (an llm node, a question no decision model took)
BUDGET = 0.02  # $ for model turns and a habit's model calls when --budget is not given
TIMEOUT = 600  # seconds a run or a turn may take
LATE = 60  # seconds a turn the bench cut may still answer in Hermes's gateway before its habit is cleaned
LATE_END = 600  # at the bench's end, seconds a cut create that never answered is still waited for: it may save
SETUP_TIMEOUT = 120
OFF_SECONDS = 600  # the plugin's off file counts this long after its last touch (habits/plugin.py)
PAUSE = "habits bench "  # the paused file a bench writes says this, then its process id (a user's pause is empty)
CASE = {"id", "task", "habit", "inputs", "message", "scratch", "setup", "rewrite", "mode", "repeat", "expect",
        "verify", "note", "baseline", "create", "timeout", "memory", "forbid",
        "route"}  # route: a layer-2 note of the case's own (habit, none, either), never read here
STATUSES = ("end", "wake", "none", "other")  # what a run gives; other: the trigger gives the turn to another habit
EXPECT = {*STATUSES, "max_ms"}  # the statuses that count as right, each with its own checks
CHECKS = {"message_contains", "message_regex", "values", "planned_tools", "planned", "planned_count", "failed",
          "doubted"}
BASELINE = {"prompt", "repeat", "expect", "verify"}
CREATE = {"prompt", "name", "repeat", "expect", "mode", "hour", "message", "before"}
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")  # a habit's name, as habit_save takes it
HABIT_TOOLS = ("habit_run", "habit_save")
ONE_OFF_CALLS = 2  # a one-off ends no turn alone: the model calls habit_run, then answers with what came back
WRAPPERS = {"sudo", "env", "nohup", "exec", "time", "nice", "timeout", "xargs", "caffeinate", "stdbuf"}
ASKED = {"--help", "-h", "--list"}  # a command asked for its help or its list runs nothing of what it does
RUNNERS = {"terminal", "run", "call", "check_call", "check_output", "Popen", "system", "popen", "getoutput",
           "getstatusoutput"}  # what runs a command in execute_code's Python: hermes_tools, subprocess, os
SSH_VALUE = set("bceilmopBDEFIJLOPQRSwW")  # OpenSSH options that take a value (as habits/shell.py reads them)
# A Downloads folder as a command or a prompt names it (the plugin's own pattern in habits/tools.py, plus prose's
# trailing . and , and the } or ] that ends a YAML flow mapping or list): from ~, $HOME or /Users/<me>, or alone;
# macOS folder names ignore case.
DOWNLOADS = re.compile(r"""(?:^|[\s/'"=:({\[])downloads(?:/|$|[\s'"):;&|*,.}\]])""", re.I | re.M)
# What no model turn may find in the roots' base, to REACH folders deep (its own root included): an old bench's copy
# folder, a proof script, a scenario file (by the names of the files loaded), and a copy of a case's
# habit (an assets/HABIT.yaml in a folder named like it). Other habits there are examples, as the plugin ships.
GUARDED = {".bench", "prove.py", "check_regex.py", "cases.tsv"}
REACH = 6
FENCE = "Read nothing outside {scratch}; your test files go in {scratch}."  # a turn whose setup built it
HIDDEN = "HABITS_BENCH_HIDDEN"  # set once the bench runs under a command line that names no file
NEAR = 0.1  # a trap's trigger answer held this close under its gate is flagged


class BenchError(Exception):
    """A scenario the bench refuses: nothing runs."""


def tell(text: str):
    """A line for the person running the bench: on stderr, and appended to --log (opened for each line, so lsof on
    the bench names no file of it while a model turn runs)."""
    print(text, file=sys.stderr, flush=True)
    if Bench.log is not None:
        with Bench.log.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def roots_base() -> Path:
    """Where each model turn gets a fresh, empty root of its own (hb-<random>/<case>/): a folder of the temp folder,
    apart from the bench's own work folder there."""
    base = Path(tempfile.gettempdir()) / "habits-bench-turns"
    base.mkdir(exist_ok=True)
    return base


def within_reach(base: Path, names: set, habits=frozenset(), depth: int = REACH) -> list[str]:
    """Every file or folder under base, depth folders deep at most, whose name is one of names, and every
    <habit>/assets/HABIT.yaml of one of habits (links not followed): what a model turn would find by walking up from
    its root and looking around. 20 at most."""
    found, top = [], len(base.parts)
    for folder, dirs, files in os.walk(base):
        here = Path(folder)
        found += [str(here / n) for n in sorted(dirs + files) if n in names]
        if "HABIT.yaml" in files and here.name == "assets" and here.parent.name in habits:
            found.append(str(here / "HABIT.yaml"))
        dirs[:] = [] if len(here.parts) - top >= depth else [d for d in dirs if d not in names]
        if len(found) >= 20:
            break
    return found[:20]


# ---- scenarios --------------------------------------------------------------

def parse(text: str, aliases: bool = False):
    """YAML with 1.2 booleans, as habits read it (`yes` stays a word), through the plugin's own loader; JSON
    when PyYAML is missing. Only a scenario file, written by hand, may use aliases; a graph a turn wrote may not."""
    if not (REPO / "habits" / "yaml12.py").is_file():
        raise BenchError(f"the bench runs from a whole checkout: no {REPO / 'habits'} next to bench/")
    sys.path.insert(0, str(REPO))
    try:
        from habits.yaml12 import load as yaml12
    except ImportError:
        return json.loads(text)
    finally:
        sys.path.remove(str(REPO))
    return yaml12(text, aliases=aliases)


def _pattern(text) -> str:
    """Why a pattern does not compile, or ""."""
    try:
        re.compile(str(text))
    except re.error as exc:
        return f"{text!r} is no pattern ({exc})"
    return ""


def problems(case) -> list[str]:
    """What is wrong with a case as written, [] when nothing."""
    if not isinstance(case, dict):
        return ["a case is a mapping"]
    found = [f"unknown key {k}" for k in sorted(set(case) - CASE)]
    found += [f"{k} is required (text)" for k in ("id", "task", "habit") if not isinstance(case.get(k), str)]
    if case.get("mode", "dry") not in ("dry", "live"):
        found.append("mode is dry or live")
    for where, part in (("", case), ("baseline.", case.get("baseline") or {}), ("create.", case.get("create") or {})):
        repeat = part.get("repeat", 1) if isinstance(part, dict) else 1
        if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat < 1:
            found.append(f"{where}repeat is a whole number, 1 or more")
    if "message" in case and not isinstance(case["message"], str):
        found.append("message is text: the user message the habit's trigger reads")
    if not isinstance(case.get("expect"), dict):
        return found + ["expect is required: end, wake and/or none, each with its checks"]
    found += expect_problems(case["expect"], "expect", bool(case.get("message")))
    if "memory" in case and not (isinstance(case["memory"], str) and case.get("scratch")):
        found.append("memory needs a scratch folder: the file is written there")
    forbid = case.get("forbid", [])
    if not isinstance(forbid, list) or not all(isinstance(f, str) and f.split() for f in forbid):
        found.append("forbid is a list of commands or tools a model turn must not run (rsync, ssh, hermes send, "
                     "send_message)")
    for name, keys, needed in (("baseline", BASELINE, ("prompt",)), ("create", CREATE, ("prompt", "name"))):
        part = case.get(name)
        if part is None:
            continue
        if not isinstance(part, dict):
            found.append(f"{name} is a mapping")
            continue
        found += [f"unknown {name} key {k}" for k in sorted(set(part) - keys)]
        found += [f"{name}.{k} is required (text)" for k in needed if not isinstance(part.get(k), str)]
    if isinstance((case.get("create") or {}).get("name"), str) and not NAME.match(case["create"]["name"]):
        found.append("create.name uses a-z, 0-9, _ or -, 40 characters at most (a habit's name)")
    made = case.get("create") if isinstance(case.get("create"), dict) else {}
    if "expect" in made:
        found += (expect_problems(made["expect"], "create.expect", bool(made.get("message")))
                  if isinstance(made["expect"], dict) else ["create.expect is a mapping, like expect"])
    if made.get("mode", "dry") not in ("dry", "live"):
        found.append("create.mode is dry or live")
    if "hour" in made and (not isinstance(made["hour"], int) or isinstance(made["hour"], bool)
                           or not 0 <= made["hour"] <= 23):
        found.append("create.hour is the hour the habit made runs at, 0 to 23")
    if "message" in made and made["message"] is not True:
        found.append("create.message is true: the habit made then runs from the case's message")
    elif made.get("message") and not case.get("message"):
        found.append("create.message needs the case's message")
    for part, key in (("baseline", "verify"), ("create", "before")):
        given = case.get(part) if isinstance(case.get(part), dict) else {}
        if key in given and not isinstance(given[key], str):
            found.append(f"{part}.{key} is a shell command (text)")
    if made.get("before") and not case.get("scratch"):
        found.append("create.before needs a scratch folder")
    if isinstance((case.get("baseline") or {}).get("expect"), dict):
        found += [f"baseline.expect takes message_contains and message_regex, not {k}"
                  for k in sorted(set(case["baseline"]["expect"]) - {"message_contains", "message_regex"})]
    if not isinstance(case.get("inputs", {}), dict):
        found.append("inputs is a mapping")
    if not isinstance(case.get("rewrite", {}), dict):
        found.append("rewrite is a mapping: text -> replacement")
    if (case.get("setup") or case.get("rewrite") or case.get("verify") or case.get("note")
            or isinstance(case.get("baseline"), dict) and case["baseline"].get("verify")) and not case.get("scratch"):
        found.append("setup, rewrite, verify and note need a scratch folder")
    if case.get("mode") == "live" and not case.get("scratch"):
        found.append("mode: live needs a scratch folder: a live run writes only there (hermes habits run --within)")
    return found


def expect_problems(expect: dict, where: str, message: bool) -> list[str]:
    """What is wrong with an expect mapping (a case's, or its create's): its statuses and their checks."""
    found = [f"unknown {where} key {k}" for k in sorted(set(expect) - EXPECT)]
    statuses = [k for k in STATUSES if k in expect]
    if not statuses:
        found.append(f"{where} names end, wake or none, the statuses that count as right (wake: {{}} takes any "
                     "clean hand-back)")
    for status in statuses:  # a mapping of checks, or a list of them: several forms are right
        forms = expect[status] if isinstance(expect[status], list) else [expect[status]]
        for checks in forms or [0]:
            if checks is None:
                continue
            if not isinstance(checks, dict):
                found.append(f"{where}.{status} is a mapping of checks, or a list of them (one right is enough)")
                continue
            found += form_problems(checks, f"{where}.{status}")
    if "none" in expect and not message:
        found.append(f"{where}.none needs a message: only a trigger leaves a habit unrun")
    if "other" in expect and not message:
        found.append(f"{where}.other needs a message: only a trigger gives another habit the turn")
    return found


def form_problems(checks: dict, where: str) -> list[str]:
    """What is wrong with one form of a status's checks."""
    found = [f"unknown {where} key {k}" for k in sorted(set(checks) - CHECKS)]
    for key, what in (("planned", "one planned call at least"), ("doubted", 'one doubt ("<node> <item>")')):
        patterns = checks.get(key, [])
        if not isinstance(patterns, list):
            found.append(f"{where}.{key} is a list of patterns, each matched by {what}")
        else:
            found += [f"{where}.{key}: {why}" for why in map(_pattern, patterns) if why]
    if "message_regex" in checks and _pattern(checks["message_regex"]):
        found.append(f"{where}.message_regex: {_pattern(checks['message_regex'])}")
    count = checks.get("planned_count", 0)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        found.append(f"{where}.planned_count is a whole number")
    return found


def files_of(target: Path) -> list[Path]:
    """A scenario file, or every .yaml, .yml and .json file of a folder."""
    files = [target] if target.is_file() else sorted(
        p for p in target.iterdir() if p.suffix in (".yaml", ".yml", ".json")) if target.is_dir() else []
    if not files:
        raise BenchError(f"no scenario file at {target}")
    return files


def load_file(file: Path) -> list[dict]:
    """The cases of one scenario file, each knowing its file's folder (a relative habit path is read from there,
    wherever the bench runs), or BenchError with every problem of the file."""
    file = file.resolve()
    try:
        data = parse(file.read_text(encoding="utf-8"), aliases=True)
    except Exception as exc:
        raise BenchError(f"{file}: {type(exc).__name__}: {exc}") from None
    items = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise BenchError(f"{file}: a list of cases, or cases: [...]")
    found, wrong, ids = [], [], collections.Counter()
    for case in items:
        why = problems(case)
        if why:
            wrong.append(f"{file}: case {case.get('id') if isinstance(case, dict) else case}: " + "; ".join(why))
        if isinstance(case, dict) and isinstance(case.get("id"), str):
            ids[case["id"]] += 1
        if not why:
            found.append({**case, "_dir": file.parent, "_file": file})
    wrong += [f"{file}: two cases are named {i}" for i, n in ids.items() if n > 1]
    if wrong:
        raise BenchError("\n".join(wrong))
    return found


def load(target: Path, wrong: list | None = None) -> list[dict]:
    """The cases of a scenario file, or of every scenario file of a folder. An id two files share becomes
    <file>:<id> in both (each file is its own task). wrong: a list that takes each file's problems, and that
    file is left out; else the first file with a problem is a BenchError."""
    found = []
    for file in files_of(target):
        try:
            found += load_file(file)
        except BenchError as exc:
            if wrong is None:
                raise
            wrong += str(exc).splitlines()
    shared = {i for i, n in collections.Counter(c["id"] for c in found).items() if n > 1}
    return [{**c, "id": f"{c['_file'].stem}:{c['id']}"} if c["id"] in shared else c for c in found]


def roots() -> list[Path]:
    """Where a scratch folder may be: folders that are thrown away."""
    return [Path(p).expanduser().resolve() for p in (tempfile.gettempdir(), "/tmp")]


def scratch_of(case) -> Path | None:
    if not case.get("scratch"):
        return None
    path = Path(os.path.expanduser(str(case["scratch"]))).resolve()  # a link to a real folder is that folder
    if not any(root in path.parents for root in roots()):
        raise BenchError(f"{case['id']}: scratch {path} must be inside one of {', '.join(map(str, roots()))}")
    return path


def fill(value, scratch: Path | None):
    """{scratch} and {repo} in every text of a case."""
    if isinstance(value, str):
        value = value.replace("{repo}", str(REPO))
        return value.replace("{scratch}", str(scratch)) if scratch else value
    if isinstance(value, dict):
        return {k: v if k == "_dir" else fill(v, scratch) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, scratch) for v in value]
    return value


def is_path(habit: str) -> bool:
    return "/" in habit or habit.endswith((".yaml", ".yml"))


def lookup(hermes: str, name: str) -> dict:
    """A habit as `hermes habits show <name> --json` finds it, which is the file a run of that name reads: its path
    and the habits it uses, or {"status": "unknown", "skill": …}. BenchError when Hermes gives no clear answer."""
    try:
        proc = subprocess.run([hermes, "habits", "show", name, "--json"], capture_output=True, text=True,
                              timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BenchError(f"hermes habits show {name}: {type(exc).__name__}: {exc}") from None
    info = last_json(proc.stdout)
    if proc.returncode == 0 and info and info.get("path"):
        return info
    if proc.returncode == 1 and info and info.get("status") == "unknown":
        return info
    raise BenchError(f"hermes habits show {name} --json gave no clear answer (exit {proc.returncode}): "
                     f"{tail(proc.stdout + proc.stderr)}")


def uses(text: str) -> list[str]:
    """The habits a graph runs with use:."""
    try:
        data = parse(text)
    except Exception:  # the run refuses a graph it cannot read: nothing of it runs
        return []
    nodes = data.get("nodes") if isinstance(data, dict) and isinstance(data.get("nodes"), dict) else {}
    return sorted({n["use"] for n in nodes.values() if isinstance(n, dict) and isinstance(n.get("use"), str)})


def subhabits(case_id: str, text: str, find) -> list[tuple[str, str]]:
    """(name, text) of every habit a graph uses, and of theirs: a run runs them as they are, never rewritten."""
    found, queue, seen = [], uses(text), set()
    while queue:
        name = queue.pop(0)
        if name in seen:
            continue
        seen.add(name)
        info = find(name) if find else {}
        if not info.get("path"):
            raise BenchError(f"{case_id}: it uses {name}, which is no habit (hermes habits show {name})")
        found.append((name, Path(info["path"]).read_text(encoding="utf-8")))
        queue += info.get("uses") or []
    return found


def prepare(case, home: Path, find=None, work: Path | None = None) -> dict:
    """A case ready to run, or BenchError: its scratch folder, the habit it runs (a copy in the bench's own work
    folder when it rewrites paths, never in the scratch folder a model turn could read), and no Downloads folder
    anywhere: a habit never runs there, not even dry. A name is looked up as a run looks it up (find: `hermes
    habits show --json`), and the habits it uses are read too. A habit file in the scratch folder that is not there
    yet is its setup's to make: it is read, and checked, once the setup ran."""
    raw, scratch = case, scratch_of(case)
    case = fill(case, scratch)
    if "{scratch}" in json.dumps({k: v for k, v in case.items() if k not in ("_dir", "_file")}, ensure_ascii=False):
        raise BenchError(f"{case['id']}: it writes {{scratch}} but names no scratch folder")
    case = {**case, "_raw": raw, "_work": work, **({"memory": memory_of(case, scratch)} if case.get("memory") else {})}
    late = False
    if is_path(case["habit"]):
        path = Path(os.path.expanduser(case["habit"]))
        source = (path if path.is_absolute() else case["_dir"] / path).resolve()
        late = not source.is_file() and bool(case.get("setup")) and scratch in source.parents
        if not source.is_file() and not late:
            raise BenchError(f"{case['id']}: no habit file {source}")
    else:
        info = find(case["habit"]) if find else {}
        if not info.get("path"):
            raise BenchError(f"{case['id']}: no habit named {case['habit']} (hermes habits show {case['habit']})")
        source = Path(info["path"])
    for what, value in (("setup", case.get("setup")), ("verify", case.get("verify")), ("inputs", case.get("inputs")),
                        ("baseline prompt", (case.get("baseline") or {}).get("prompt")),
                        ("create prompt", (case.get("create") or {}).get("prompt"))):
        if value and DOWNLOADS.search(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)):
            raise BenchError(f"{case['id']}: its {what} names a Downloads folder; run a copy in a scratch folder "
                             "(scratch, setup, rewrite)")
    case = {**case, "_scratch": scratch, "_source": source, "_late": late}
    return {**case, **({"_target": str(copy_path(case)) if case.get("rewrite") else str(source), "_copy": None,
                        "_copy_skill": None} if late else habit_of(case, find))}


def memory_of(case, scratch: Path) -> str:
    """A case's memory file, in its scratch folder (a relative one starts there, where the run starts)."""
    memory = Path(os.path.expanduser(case["memory"]))
    memory = (memory if memory.is_absolute() else scratch / memory).resolve()
    if scratch not in memory.parents:
        raise BenchError(f"{case['id']}: memory {case['memory']} must be in its scratch folder (hermes habits "
                         "run --memory writes it)")
    return str(memory)


def slug(text: str) -> str:
    """A case's id as a folder name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip(".-") or "case"


def rewritten(text: str, pairs: dict) -> str:
    """The text with each case rewrite applied in order. Where the text has a replacement that holds its own old
    text already (a habit Hermes made names {scratch}/bin/rsync, and the case swaps rsync for it), that part
    stays as it is: never {scratch}/bin/{scratch}/bin/rsync."""
    for old, new in pairs.items():
        old, new = str(old), str(new)
        text = (new.join(part.replace(old, new) for part in text.split(new)) if old and old in new
                else text.replace(old, new))
    return text


def spelled(folder: Path) -> list[str]:
    """The ways a graph may name a folder's files: as given, resolved, and from ~ or $HOME in the home folder.
    Each ends in /, so a folder whose name starts like it (x-other/) is never taken for it."""
    found = list(dict.fromkeys([str(folder), str(folder.resolve())]))
    for whole in list(found):
        with contextlib.suppress(ValueError):
            rest = Path(whole).relative_to(Path.home())
            found += [f"~/{rest}", f"$HOME/{rest}"]
    return [f"{name}/" for name in dict.fromkeys(found)]


def copy_kept(folder: Path, kept: Path):
    """The copy of a habit a create made, its links kept as links: works() leaves them out, since one could bring in a
    file from anywhere (copytree's default copies what a link points to, as a plain file)."""
    shutil.copytree(folder, kept, symlinks=True)


def copy_path(case) -> Path:
    """Where the copy of a case's habit goes: the bench's own work folder, one per case, never the scratch folder
    (a model turn there would read the answer)."""
    if case.get("_work") is None:
        raise BenchError(f"{case['id']}: it rewrites its habit, and no work folder takes the copy")
    return case["_work"] / "copies" / slug(case["id"]) / case["_source"].parent.parent.name / "assets" / "HABIT.yaml"


def habit_of(case, find=None) -> dict:
    """The habit a case runs, read from its source: its target (a scratch copy when the case rewrites paths) and
    the copy's texts, SKILL.md included (the trigger reads its description). BenchError when it, or a habit it
    uses, names a Downloads folder."""
    source = case["_source"]
    text = source.read_text(encoding="utf-8")
    skill = source.parent.parent / "SKILL.md"
    target, copy, copy_skill = str(source) if is_path(case["habit"]) else case["habit"], None, None
    if case.get("rewrite"):
        copy_skill = skill.read_text(encoding="utf-8") if skill.is_file() else None
        text = rewritten(text, case["rewrite"])
        copy_skill = rewritten(copy_skill, case["rewrite"]) if copy_skill else copy_skill
        target, copy = str(copy_path(case)), text
    for what, value in [("habit", text)] + [(f"sub-habit {name}", sub)
                                            for name, sub in subhabits(case["id"], text, find)]:
        if DOWNLOADS.search(value):
            raise BenchError(f"{case['id']}: its {what} names a Downloads folder; run a copy in a scratch folder "
                             "(scratch, setup, rewrite)")
    return {"_target": target, "_copy": copy, "_copy_skill": copy_skill}


# ---- what a run should give -----------------------------------------------------

def _text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


ESCAPED = re.compile(r"\\+u([dD][89abAB][0-9a-fA-F]{2})\\+u([dD][c-fC-F][0-9a-fA-F]{2})|\\+u([0-9a-fA-F]{4})")


def _plain(text: str) -> str:
    """A value's text with the \\uXXXX escapes of its JSON read back (a tool's reply escapes what is not ASCII):
    one text whether or not it was escaped, so a pattern, a negative one too, reads it once."""
    def letter(found):
        if found.group(3):
            return chr(int(found.group(3), 16))
        high, low = int(found.group(1), 16), int(found.group(2), 16)
        return chr(0x10000 + (high - 0xD800) * 0x400 + low - 0xDC00)
    return ESCAPED.sub(letter, text)


def same(expected, actual) -> bool:
    """A value as expected: "re:<pattern>" searched in its text, another text equal to its text, else equal. Its
    text is read with its JSON escapes undone."""
    if isinstance(expected, str) and expected.startswith("re:"):
        return re.search(expected[3:], _plain(_text(actual)), re.S) is not None
    if isinstance(expected, str):
        return _plain(expected) == _plain(_text(actual))
    return expected == actual


def call_text(call) -> str:
    """A planned call as a pattern reads it: its tool, then its command (a terminal call) or its arguments."""
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    command = args.get("command") if call.get("tool") == "terminal" else None
    return f"{call.get('tool')} {command if isinstance(command, str) else _text(args)}"


def matches(expect: dict, out: dict) -> list[str]:
    """What a run's output misses of what the case expects: [] when it is right. Its status picks the checks; a
    wake is a clean hand-back only when no step failed (unless its checks say failed: true)."""
    status, why = out.get("status"), []
    allowed = [k for k in STATUSES if k in expect]
    if status not in allowed:
        fired = (out.get("trigger") or {}).get("fired") if isinstance(out.get("trigger"), dict) else None
        why.append(f"status {status}" + (f" (the trigger runs {fired})" if status == "other" else "")
                   + f", expected {'|'.join(allowed)}")
        checks = {}
    else:
        checks = expect.get(status) or {}
        if isinstance(checks, list):  # several forms are right: one is enough, and the closest miss is said
            missed = [matches({**expect, status: form}, out) for form in checks]
            return [] if not all(missed) else min(missed, key=len)
    if status == "wake" and status in allowed and bool(out.get("failed")) != bool(checks.get("failed", False)):
        why.append("a step failed: no clean hand-back" if out.get("failed") else "expected a failed step")
    message = str(out.get("message") or "")
    wanted = checks.get("message_contains", [])
    for part in wanted if isinstance(wanted, list) else [wanted]:
        if str(part) not in message:
            why.append(f"message lacks {part!r}")
    if "message_regex" in checks and not re.search(str(checks["message_regex"]), message, re.S):
        why.append(f"message does not match {checks['message_regex']!r}")
    values = out.get("values")
    if checks.get("values") and not isinstance(values, dict):
        why.append("values are no mapping (an older hermes habits run cuts them past 6,000 characters)")
    for name, value in (checks.get("values") or {}).items() if isinstance(values, dict) else ():
        if name not in values:
            why.append(f"no value {name}")
        elif not same(value, values[name]):
            why.append(f"value {name} is {_plain(_text(values[name]))[:120]}, expected {_text(value)[:120]}")
    planned = [p for p in out.get("planned") or [] if isinstance(p, dict)]
    if "planned_tools" in checks:
        got = sorted({p.get("tool") for p in planned})
        want = sorted(set(checks["planned_tools"] or []))
        if got != want:
            why.append(f"planned tools {got}, expected {want}")
    texts = [call_text(p) for p in planned]
    for pattern in checks.get("planned") or []:
        if not any(re.search(str(pattern), text, re.S) for text in texts):
            why.append(f"no planned call matches {pattern!r}")
    count = out.get("planned_count", len(planned))
    if "planned_count" in checks and count != checks["planned_count"]:
        why.append(f"{count} planned calls, expected {checks['planned_count']}")
    if "planned" in checks and len(planned) < (count or 0):
        why.append(f"{len(planned)} of {count} planned calls shown (an older hermes habits run cuts them at 50)")
    doubts = doubt_texts(out.get("doubted"))
    for pattern in checks.get("doubted") or []:
        if not any(re.search(str(pattern), text, re.S) for text in doubts):
            why.append(f"no doubt matches {pattern!r}")
    if "max_ms" in expect and isinstance(out.get("ms"), (int, float)) and out["ms"] > expect["max_ms"]:
        why.append(f"took {out['ms']} ms, more than {expect['max_ms']}")
    return why


TOOK = re.compile(r"took \S+ ms, more than ")  # matches()'s miss of max_ms


def slow(run: dict) -> bool:
    """A run whose only miss is its time: right, but over its case's max_ms: slow, not wrong."""
    why = run.get("why") or []
    return bool(why) and all(TOOK.match(str(w)) for w in why)


def right(run: dict) -> bool:
    """A run that did what its case expects, slow or not; an older record kept a slow one as not ok, so this
    reads both."""
    return bool(run.get("ok")) or slow(run)


def doubt_texts(doubted) -> list[str]:
    """Each doubt a run left, as a pattern reads it: its node, then its item."""
    return [f"{d.get('node')} {_plain(_text(d.get('item')))}" for d in doubted or [] if isinstance(d, dict)]


def near(trigger, name: str, expect) -> list[str]:
    """A trap's trigger answers that held its habit by less than NEAR under their gate, or that let it through: a
    trap is a case with a message that must not end. The probability read is the passing option's (the habit for
    the pick, run for the confirmation, nothing for the second question), else the answer's own confidence when
    it is that option. The confirmation and the second question come in one call and must both pass: when one
    passed, the other held the trap alone, however far under its gate, and that is said too; a recorded one counts
    only about this habit. [] for any other run."""
    if not isinstance(trigger, dict) or "end" in (expect or []):
        return []
    if trigger.get("fired") == name:
        return [f"fired: the trigger ran {name}"]
    # A recorded confirmation about another habit (a tie of the pick) says nothing of this one's second gate.
    trigger = {k: v for k, v in trigger.items()
               if not (isinstance(v, dict) and v.get("recorded") and v.get("habit") != name)}
    found, read = [], {}
    for stage, passing in (("pick", name), ("confirm", "run"), ("also", "nothing")):
        answer = trigger.get(stage)
        gate = answer.get("gate") if isinstance(answer, dict) else None
        if not isinstance(gate, (int, float)):
            continue
        odds = answer.get("probabilities") or {}
        p = odds.get(passing) if odds else answer.get("confidence") if answer.get("answer") == passing else None
        read[stage] = (passing, p, gate, answer.get("passed") is True)
        if isinstance(p, (int, float)) and round(gate - NEAR, 3) <= p < gate:
            found.append(f"{stage} {passing} {p:.2f} under its gate {gate}")
    recorded = isinstance(trigger.get("confirm"), dict) and trigger["confirm"].get("recorded")
    if recorded and read.get("confirm", (0, 0, 0, False))[3] and read.get("also", (0, 0, 0, True))[3]:
        passing, p, gate, _ = read["confirm"]  # asked after the pick held, and nothing else would have held it
        found.append(f"confirm {passing} {p:.2f} would pass its gate {gate}; only the pick held it")
    if "confirm" in read and "also" in read and read["confirm"][3] != read["also"][3] and not recorded:
        ok, held = ("confirm", "also") if read["confirm"][3] else ("also", "confirm")
        (passing, p, gate, _), (option, q, _, _) = read[ok], read[held]
        found.append(f"{ok} {passing} {p:.2f} passed its gate {gate}; only {held} held it"
                     + (f" ({option} {q:.2f})" if isinstance(q, (int, float)) else ""))
    return found


def near_lines(records: list[dict]) -> list[str]:
    """One line per trap with runs near a gate, under the table."""
    lines = []
    for case in dict.fromkeys(r["case"] for r in records if r.get("near")):
        runs = [r for r in records if r["case"] == case and r["kind"] == "run"]
        flagged = [r for r in runs if r.get("near")]
        lines.append(f"near a gate: {case} {len(flagged)}/{len(runs)} run{'s' * (len(runs) != 1)} "
                     f"({'; '.join(flagged[0]['near'])})")
    return lines


def last_json(text: str) -> dict | None:
    """The JSON object a command printed last (Hermes may print notices before it)."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                return unwrapped(data)
    return None


def unwrapped(data: dict) -> dict:
    """A run's JSON with its texts back in place: after a read outside, `hermes habits run --json` holds them in an
    untrusted block (the model reads that output too), as one JSON line just before the block's end."""
    block = data.get("untrusted")
    if not isinstance(block, str) or "</untrusted_tool_result>" not in block:
        return data
    inner = _loads(block.rsplit("\n</untrusted_tool_result>", 1)[0].rsplit("\n", 1)[-1])
    return {**{k: v for k, v in data.items() if k != "untrusted"}, **inner} if isinstance(inner, dict) else data


def tail(text: str, size: int = 300) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= size else "…" + text[-size:]


# ---- money ------------------------------------------------------------------

def price(turn: dict) -> float:
    """A turn's cost: Hermes's estimate when it made one, else its tokens at DeepSeek's list price (cached input
    counted as input: a budget may only err high). Hermes writes 0 when it cannot price a turn (cost_status
    unknown): a turn that used tokens is never free."""
    taken = sum(int(turn.get(k) or 0) for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens"))
    by_tokens = taken * PRICE_IN + int(turn.get("output_tokens") or 0) * PRICE_OUT
    estimate = turn.get("estimated_cost_usd")
    if (isinstance(estimate, (int, float)) and turn.get("cost_status") in (None, "estimated", "actual")
            and (estimate > 0 or not by_tokens)):
        return float(estimate)
    return by_tokens


class Budget:
    """Dollars for model turns and a habit's own model calls: the next turn is counted at the dearest seen so far
    (FIRST_CALL before any), a habit run at its case's last run's model calls."""

    def __init__(self, limit: float | None):
        self.limit, self.spent, self.costs, self.calls = limit, 0.0, [], 0
        self.nodes = self.plan = 0  # of those calls: an llm node's, a live run's dry pass's (the rest: questions)

    def next(self) -> float:
        return max(self.costs) if self.costs else FIRST_CALL

    def allows(self, cost: float | None = None) -> bool:
        """The next model turn fits (cost None), or a habit run counted at cost: one that calls no model always
        does."""
        if cost == 0 or self.limit is None:
            return True
        return self.spent + (self.next() if cost is None else cost) <= self.limit

    def add(self, cost: float):
        self.spent += cost
        self.costs.append(cost)

    def charge(self, calls: int, nodes: int = 0, plan: int = 0):
        """A habit run's own model calls, at MODEL_CALL each: nodes of them by llm nodes, plan in a live run's dry
        pass."""
        self.calls += calls
        self.nodes += nodes
        self.plan += plan
        self.spent += calls * MODEL_CALL

    def line(self) -> str:
        """What the model turns and the habits' own model calls cost, each apart: a run with no model turn can still
        pay for its llm nodes, and one total would hide them."""
        questions = self.calls - self.nodes - self.plan
        return (f"model turns: {len(self.costs)}, {sum(self.costs):.4f} $; a habit's own model calls: {self.calls} "
                f"({self.nodes} by llm nodes, {questions} by questions no decision model took"
                + (f", {self.plan} in live runs' dry passes" if self.plan else "")
                + f"), about {self.calls * MODEL_CALL:.4f} $ at {MODEL_CALL} $ a call, not measured; "
                  f"{self.spent:.4f} $ counted of {self.limit} $")


# ---- the summary --------------------------------------------------------------

def _median(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return statistics.median(values) if values else None


def _mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return statistics.mean(values) if values else None


def finished(run: dict) -> bool:
    """A run that did the task: right (slow too), and ended on end. Anything else leaves the task to the model."""
    return right(run) and run.get("status") == "end"


def summary(records: list[dict]) -> list[dict]:
    """One row per case, on the baselines that were right. A run that did not do the task (a hand-back, a wrong
    result, nothing run) is charged one Hermes turn more, its median seconds and mean API calls: the model still
    has it to do. A one-off is charged the two model calls around it too (the call and the answer), at the
    baseline's seconds per API call. Speedup: the baseline's seconds over a task's mean wall time that way, both
    whole processes (Hermes's start included; a case with a message counts its trigger's questions): the one the
    goal is checked on. Engine: over the run alone, on the runs that did the task. Model calls avoided: 1 - a
    task's mean model calls that way over the baseline's mean API calls. A watch's case that should wake (side
    wake) costs one Hermes turn on both sides: no speedup, no calls avoided; its gain is on the checks that end
    (side end), and over a night (nights())."""
    rows = []
    for case in dict.fromkeys(r["case"] for r in records):
        mine = [r for r in records if r["case"] == case]
        runs = [r for r in mine if r["kind"] == "run"]
        kind = next((r["habit_kind"] for r in runs if r.get("habit_kind")), None)
        expected = next((r["expect"] for r in runs if r.get("expect")), [])
        # An older run record says nothing of what its case expects: its side is unknown, no figure.
        side = (None if kind != "watch" else "unknown" if not any("expect" in r for r in runs)
                else "wake" if "wake" in expected and "end" not in expected else "end")
        timed = [r for r in runs if isinstance(r.get("wall_ms"), (int, float))]  # a run that started
        base = [r for r in mine if r["kind"] == "baseline" and r.get("ok") and r.get("session")]
        made = [r for r in mine if r["kind"] == "create"]
        seconds, calls = _median(r.get("seconds") for r in base), _mean(r.get("api_calls") for r in base)
        one_off = [r.get("habit_kind") == "one-off" for r in timed]  # its call and answer: the model's, around it
        # Their seconds: their share of the baseline's, a whole baseline at most (and when its calls are unknown).
        turn = (seconds * min(1, ONE_OFF_CALLS / calls) if calls else seconds) if seconds is not None else None
        task = (_mean(r["wall_ms"] / 1000 + (0 if finished(r) else seconds) + (turn if alone else 0)
                      for r, alone in zip(timed, one_off)) if seconds is not None and timed else None)
        engine = _median(r.get("ms") for r in runs if finished(r))
        used = (_mean((r.get("model_calls") or 0) + (0 if finished(r) else calls) + (ONE_OFF_CALLS if alone else 0)
                      for r, alone in zip(timed, one_off)) if calls and timed else None)
        # A one-off's ceiling: done in 0 ms, it still pays the model's call and answer around it.
        ceiling = seconds / turn if kind == "one-off" and turn else None
        least = (_mean((r.get("model_calls") or 0) + ONE_OFF_CALLS for r in timed)
                 if kind == "one-off" and calls and timed else None)
        rows.append({
            "case": case, "task": mine[0].get("task"), "runs": len(runs), "right": sum(right(r) for r in runs),
            "rate": sum(right(r) for r in runs) / len(runs) if runs else None, "slow": sum(slow(r) for r in runs),
            "done": sum(finished(r) for r in runs), "wakes": sum(r.get("status") == "wake" for r in runs),
            "ms": _median(r.get("ms") for r in runs), "wall_ms": _median(r.get("wall_ms") for r in runs),
            "baselines": len(base), "base_s": seconds, "base_tokens": _median(r.get("tokens") for r in base),
            "base_calls": calls, "base_cost": _mean(r.get("cost") for r in base), "task_s": task, "task_calls": used,
            "kind": kind, "side": side,
            "speedup": seconds / task if task and side not in ("wake", "unknown") else None,
            "ceiling": ceiling, "ceiling_avoided": 1 - least / calls if least is not None else None,
            "engine": seconds * 1000 / max(engine, 1) if seconds is not None and engine is not None else None,
            "avoided": 1 - used / calls if used is not None and side not in ("wake", "unknown") else None,
            "created": len(made), "valid": sum(bool(r.get("valid")) for r in made),
            "first_try": sum(bool(r.get("first_try") and r.get("valid")) for r in made),  # a habit it saved
            "save_tries": sum(int(r.get("save_tries") or 0) for r in made),
            "works": sum(r.get("works") is True for r in made)})
    return rows


def habit_name(habit) -> str:
    """A run record's habit by its folder's name (…/<name>/assets/HABIT.yaml), else as the record names it."""
    path = Path(str(habit))
    return path.parent.parent.name if path.parent.name == "assets" else str(habit)


def one_offs(records: list[dict]) -> list[dict]:
    """Each one-off habit's own goal: the model calls habit_run and answers around it, 2 calls no habit saves,
    so its speedup and calls avoided are capped; it is judged on its runs instead: right, none ended wrong (a silent
    wrong action), and its own model calls per run. A run only over its time is slow, never one that ended wrong.
    Per habit, over its cases."""
    runs = [r for r in records if r["kind"] == "run" and r.get("habit_kind") == "one-off"]
    found = []
    for habit in dict.fromkeys(r.get("habit") for r in runs):
        mine = [r for r in runs if r.get("habit") == habit]
        found.append({"habit": habit_name(habit),
                      "cases": len({r["case"] for r in mine}), "runs": len(mine), "right": sum(right(r) for r in mine),
                      "silent": sum(not right(r) and r.get("status") == "end" for r in mine),
                      "slow": sum(slow(r) for r in mine),
                      "calls": _mean((r.get("model_calls") or 0) for r in mine if "wall_ms" in r)})
    return found


def one_off_lines(found: list[dict]) -> list[str]:
    return [f"one-off {o['habit']}, its own goal ({o['cases']} case{'s' * (o['cases'] != 1)}): {o['right']}/{o['runs']} "
            f"right ({o['right'] / o['runs']:.0%}), of which {o['slow']} slow (only over max_ms), "
            f"{o['silent']} silent wrong (ended wrong), "
            + ("-" if o["calls"] is None else f"{o['calls']:.1f}") + " model calls per run, plus the 2 around it; its "
            "speedup and calls avoided stay capped (max in the table): the 5x and 80% goals are checked on the reference machine"
            for o in found]


def own_calls(records: list[dict]) -> list[dict]:
    """Per habit whose runs called the model on their own, those calls: by its llm nodes (named), by questions no
    decision model took (the rest of a run's model_calls), in a live run's dry pass. No model turn makes them."""
    found: dict = {}
    for r in records:
        if r["kind"] != "run" or "model_calls" not in r:
            continue
        name = habit_name(r.get("habit"))
        mine = found.setdefault(name, {"habit": name, "runs": 0, "nodes": 0, "questions": 0, "plan": 0, "names": []})
        nodes = int((r.get("counts") or {}).get("llm") or 0)
        mine.update(runs=mine["runs"] + 1, nodes=mine["nodes"] + nodes,
                    questions=mine["questions"] + int(r.get("model_calls") or 0) - nodes,
                    plan=mine["plan"] + int(r.get("plan_model_calls") or 0))
        mine["names"] += [n for n in r.get("llm_ms") or {} if n not in mine["names"]]
    return [f for f in found.values() if f["nodes"] + f["questions"] + f["plan"]]


def own_lines(found: list[dict]) -> list[str]:
    return [f"own model calls of {o['habit']}: {o['nodes']} by llm nodes"
            + (f" ({', '.join(o['names'])})" if o["names"] else "")
            + f", {o['questions']} by questions no decision model took"
            + (f", {o['plan']} in live runs' dry passes" if o["plan"] else "")
            + f", in {o['runs']} run{'s' * (o['runs'] != 1)}: about "
              f"{(o['nodes'] + o['questions'] + o['plan']) * MODEL_CALL:.4f} $, not measured (the CLI's llm calls "
              "write no session)" for o in found]


def nights(rows: list[dict], checks: int, wakes: int) -> list[dict]:
    """Each watch over a night of `checks` checks of which `wakes` woke, from its end cases (the checks that repeat):
    Hermes alone pays a turn per check; the habit its check each time, and a Hermes turn per wake."""
    found = []
    for task in dict.fromkeys(r["task"] for r in rows if r["side"] in ("end", "wake")):
        mine = [r for r in rows if r["task"] == task and r["side"] in ("end", "wake")]
        ends = [r for r in mine if r["side"] == "end" and r["task_s"] is not None and r["base_calls"]]
        night = {"task": task, "checks": checks, "wakes": wakes, "cases": [r["case"] for r in ends],
                 "woke": [r["case"] for r in mine if r["side"] == "wake"], "speedup": None, "avoided": None}
        if ends:
            turn, check = _mean(r["base_s"] for r in ends), _mean(r["task_s"] for r in ends)
            calls, used = _mean(r["base_calls"] for r in ends), _mean(r["task_calls"] for r in ends)
            night.update(speedup=checks * turn / (checks * check + wakes * turn),
                         avoided=1 - (checks * used + wakes * calls) / (checks * calls))
        found.append(night)
    return found


def night_lines(found: list[dict]) -> list[str]:
    lines = []
    for n in found:
        if n["speedup"] is None:
            lines.append(f"{n['task']}: no end case with a right baseline, so no night figure")
            continue
        woke = f"; a wake is one Hermes turn on both sides ({', '.join(n['woke'])})" if n["woke"] else ""
        lines.append(f"{n['task']}: a night of {n['checks']} checks, {n['wakes']} wake{'s' * (n['wakes'] != 1)}: "
                     f"{n['speedup']:.1f}x, "
                     f"{n['avoided']:.0%} calls avoided (from {', '.join(n['cases'])}){woke}")
    return lines


def zone(hour: int, now: float | None = None) -> str:
    """A TZ under which the local hour is `hour` now, the minutes UTC's (POSIX: local time is UTC minus the
    offset). TZ reaches a habit's terminal steps; PATH does not override a command (Hermes appends the bench's
    folders after the login PATH). The zone's name has three letters: POSIX asks for at least three, and glibc
    reads a shorter one as no zone at all (UTC), where macOS accepts it."""
    shift = (hour - time.gmtime(now).tm_hour) % 24
    shift -= 24 if shift > 12 else 0
    return f"HAB{-shift:+d}"


def _tokens(n) -> str:
    return "-" if n is None else f"{n / 1000:.1f}k" if n >= 1000 else f"{n:.0f}"


SIDES = {"wake": "(wake)", "unknown": "(side?)"}  # a watch's case with no figure of its own, in the table
LEGEND = ("done: runs that did the task (right, on end). speedup: Hermes's wall time over the habit's, both whole "
          "processes, a run not done charged one Hermes turn more, a one-off the two model calls around it: the 5x "
          "goal is checked on it. engine: over the run alone, done runs only. calls avoided: the same charges. "
          "(wake): a watch's case that should wake, one Hermes turn on both sides; a watch is measured on its cases "
          "that end, and over a night (--night); (side?): a watch's record that does not say what its case expects "
          "(an older record), no figure. (max): a one-off's ceiling, done in 0 ms but still called and answered "
          "by the model; the one-off line under the table is its own goal (right, none ended wrong, its own model calls per "
          "run), which the cap does not bound. slow: right, only over its max_ms (counted right and done). near a gate: a trap (a message that must not end) whose trigger held by less than 0.1 under "
          "a gate, or held by one only of the confirmation and the second question (asked together, the other "
          "passed), or ran the habit.")


def table(rows: list[dict]) -> str:
    head = ("case", "right", "done", "habit ms", "wall ms", "Hermes s", "tokens", "speedup", "engine",
            "calls avoided", "created")
    lines = [head]
    for r in rows:
        lines.append((
            r["case"], f"{r['right']}/{r['runs']}" + (f" {r['rate']:.0%}" if r["runs"] else "")
            + (f", {r['slow']} slow" if r.get("slow") else ""),
            f"{r['done']}/{r['runs']}" + (f", {r['wakes']} woke" if r["wakes"] else ""),
            "-" if r["ms"] is None else f"{r['ms']:.0f}", "-" if r["wall_ms"] is None else f"{r['wall_ms']:.0f}",
            "-" if r["base_s"] is None else f"{r['base_s']:.1f}", _tokens(r["base_tokens"]),
            SIDES.get(r["side"]) or ("-" if r["speedup"] is None else f"{r['speedup']:.1f}x")
            + (f" (max {r['ceiling']:.1f}x)" if r.get("ceiling") else ""),
            "-" if r["engine"] is None else f"{r['engine']:.0f}x",
            SIDES.get(r["side"]) or ("-" if r["avoided"] is None else f"{r['avoided']:.0%}")
            + (f" (max {r['ceiling_avoided']:.0%})" if r.get("ceiling_avoided") is not None else ""),
            "-" if not r["created"] else f"{r['valid']}/{r['created']} valid, {r['first_try']} first try, "
                                         f"{r['works']} work, {r['save_tries']} save tries"))
    widths = [max(len(line[i]) for line in lines) for i in range(len(head))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(line, widths)).rstrip() for line in lines)


# ---- a Hermes session ------------------------------------------------------------

def _loads(text):
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text or "null")
    except (TypeError, ValueError):
        return None


def tool_calls(db, session: str) -> list[dict]:
    """Every tool call of a session, in order: {id, tool, args, reply, from}, its reply's text and the tool that
    sent it back (None when none came). Hermes 0.23 may wrap calls in tool_call, whose calls may be a JSON text;
    their replies keep the outer call id. A calls text that does not parse stays one tool_call: it never ran."""
    replies: dict = {}
    for row in db.execute("SELECT tool_call_id, tool_name, content FROM messages WHERE session_id = ? AND role = 'tool' "
                          "ORDER BY id", (session,)):
        replies.setdefault(row["tool_call_id"], []).append((row["tool_name"], row["content"]))
    found = []
    for row in db.execute("SELECT tool_calls FROM messages WHERE session_id = ? AND role = 'assistant' "
                          "AND tool_calls IS NOT NULL ORDER BY id", (session,)):
        for call in _loads(row["tool_calls"]) or []:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            args = _loads(function.get("arguments")) or {}
            inner = [(function.get("name"), args)]
            if function.get("name") == "tool_call" and isinstance(args, dict):
                given = args.get("calls")
                given = _loads(given) if isinstance(given, str) else given
                if isinstance(given, list):
                    inner = [(c.get("name"), _loads(c.get("arguments")) or {}) for c in given if isinstance(c, dict)]
            queue = replies.get(call.get("id")) or []
            for tool, given in inner:
                at = next((k for k, (name, _) in enumerate(queue) if name == tool),
                          next((k for k, (name, _) in enumerate(queue) if name == "tool_call"), None))
                by, reply = queue.pop(at) if at is not None else (None, None)
                found.append({"id": call.get("id"), "tool": tool, "args": given if isinstance(given, dict) else {},
                              "reply": reply, "from": by})
    return found


def habit_calls(calls: list[dict]) -> list[dict]:
    """The habit_run and habit_save calls that reached the plugin, in order: {tool, name, scenario (a draft given),
    reply}."""
    return [{"tool": c["tool"], "name": c["args"].get("name"), "reply": last_json(c["reply"]) or {"status": "unreadable"},
             "scenario": c["args"].get("scenario") is not None or bool(c["args"].get("file"))}
            for c in calls if c["tool"] in HABIT_TOOLS and c["from"] == c["tool"]]


# What `hermes habits check|run <file>` printed of a graph, in a terminal's output: valid, refused, or nothing. A
# "format: file …" line is about the file (its place, its name), and
# "format: [Errno …]" about reading it: no graph was judged.
VERDICT = re.compile(r'^(?P<valid>valid: the graph follows every rule)'
                     r'|^(?P<issue>rule \d+ \(|format: (?!\[Errno|file )|safety: )|^(?P<refused>refused: invalid graph)'
                     r'| · (?P<ran>dry run: writes planned|live run|it only reads: nothing to write)'
                     r'|"status": "(?P<status>end|wake|none|refused)"(?P<graph>, "message": "invalid graph)?', re.M)
SAYS = {"check": ("valid", "issue"), "run": ("refused", "ran", "status", "graph")}  # what each verb prints
DRAFT = re.compile(r"\bhabits\s+(?:check|run)\s+[^;&|\n]*?(?:/|\.ya?ml\b|\.json\b)")  # on a file, not a name


def verdict(text: str, verbs=("check", "run")) -> bool | None:
    """What a terminal's `hermes habits check|run <file>` said of the graph: True valid (or it ran), False refused,
    None nothing about the graph (no file, a guard's refusal). Only what those verbs print counts: another
    program's output in the same call is not their word."""
    mine = {group for verb in verbs for group in SAYS[verb]}
    for found in VERDICT.finditer(text or ""):
        said = {k for k, v in found.groupdict().items() if v and k in mine}
        if said & {"valid", "ran"} or "status" in said and found["status"] != "refused":
            return True
        if said & {"issue", "refused", "graph"}:
            return False
    return None


def _output(reply) -> str:
    """A terminal call's output, from its reply's JSON (the reply itself when it is none)."""
    data = _loads(reply)
    return str(data.get("output") or "") if isinstance(data, dict) else str(reply or "")


def first_try(calls: list[dict], name: str) -> bool:
    """The first graph the plugin judged was valid. A graph is judged by habit_run with a draft (end, wake or none:
    valid; refused or error: not), by habit_save under the habit's name (saved or staged), or by a terminal `hermes
    habits check|run <file>` (valid:, or it ran; an issue, or an invalid graph). A call that never reached the
    plugin (a calls text that did not parse, a schema refusal) and a save under another name (a probe) judge
    nothing. No graph judged: not valid."""
    for call in calls:
        tool, args, reached = call["tool"], call["args"], call["from"] == call["tool"]
        status = (last_json(call["reply"]) or {}).get("status") if reached else None
        if tool == "habit_run" and reached and (args.get("scenario") is not None or args.get("file")):
            if status in ("end", "wake", "none", "refused", "error"):
                return status not in ("refused", "error")
        elif tool == "habit_save" and reached and args.get("name") == name:
            if status in ("saved", "staged", "refused", "error"):
                return status in ("saved", "staged")
        elif tool == "terminal" and isinstance(args.get("command"), str) and DRAFT.search(args["command"]):
            verbs = [verb for verb in ("check", "run") if runs(args["command"], f"hermes habits {verb}")]
            said = verdict(_output(call["reply"]), verbs) if verbs else None
            if said is not None:
                return said
    return False


def save_tries(calls: list[dict], name: str) -> int:
    """The calls that tried to save the habit under its name, landed or not: a habit_save of that name, or a
    tool_call whose calls text did not parse but names habit_save and that name."""
    named = re.compile(r'"name"\s*:\s*"' + re.escape(name) + '"')
    return sum(1 for c in calls if c["tool"] == "habit_save" and c["args"].get("name") == name
               or c["tool"] == "tool_call" and isinstance(c["args"].get("calls"), str)
               and "habit_save" in c["args"]["calls"] and bool(named.search(c["args"]["calls"])))


def declared(skill_md: Path) -> str:
    """The name a SKILL.md gives in its front matter, or ""."""
    try:
        head = skill_md.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return ""
    found = re.search(r"""^name:\s*["']?([^"'\n]+?)["']?\s*$""", head.split("\n---", 1)[0], re.M)
    return found.group(1) if found else ""


def _expand(word: str, known: dict) -> str:
    """A word with the shell variables the line set ($T, ${T}) replaced; the others stay as they are."""
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: known.get(m[1] or m[2], m[0]), word)


def _inside(word: str, folder: Path | None) -> bool:
    """A command's path is in the case's folder: a stand-in the case made (a relative one starts there). A $HOME/…
    or any other $ path is never one: the shell expands it elsewhere."""
    if folder is None or "/" not in word or word.startswith("$"):
        return False
    path = Path(os.path.expanduser(word))
    return folder.resolve() in (path if path.is_absolute() else folder / path).resolve().parents


def runs(command: str, item: str, folder: Path | None = None, depth: int = 0) -> bool:
    """Whether a shell line runs item ("rsync", "ssh", "hermes send"): a simple command of that name, by itself or by a
    path that is not in the case's folder, after VAR=value or a wrapper (env, sudo, timeout…), inside $( ), ` `,
    sh -c or an ssh's remote command. A variable the line sets on its own (T=/x; or export T=/x) is expanded in
    the path first. Asked for --help, -h or --list right after its name or a subcommand (never ssh), it runs
    nothing. A line shlex cannot read is searched as text.
    A measure after the turn, not a guard: the command ran."""
    want = item.split()
    text = command.replace("$(", " ; ").replace("`", " ; ").replace("\n", " ; ")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return re.search(r"(?<![\w.-])" + r"\s+".join(map(re.escape, want)) + r"(?![\w.-])", command) is not None
    commands, words, target = [], [], False
    for token in tokens:
        if target:  # a redirection's file is no command
            target = False
        elif token and set(token) <= set(";&|()<>"):
            if set(token) & set("<>"):
                target = True
            else:
                commands, words = commands + [words], []
        else:
            words.append(token)
    known: dict = {}  # what the line's own assignments set, for the commands after them
    for words in commands + [words]:
        sets = words[1:] if words[:1] == ["export"] else words
        if sets and all(re.match(r"^[A-Za-z_]\w*=", w) for w in sets):
            known.update((w.split("=", 1)[0], _expand(w.split("=", 1)[1], known)) for w in sets)
            continue
        wrapped = False
        while words and (re.match(r"^[A-Za-z_]\w*=", words[0]) or os.path.basename(words[0]) in WRAPPERS
                         or wrapped and re.match(r"^(-|\d+[smhd]?$)", words[0])):
            wrapped = wrapped or os.path.basename(words[0]) in WRAPPERS
            words = words[1:]
        if not words:
            continue
        path = _expand(words[0], known)
        head = os.path.basename(path)
        # Asked for its help right after its name or its subcommand's; ssh's words after the host are the remote's.
        asked = head != "ssh" and bool(ASKED & set(words[len(want):len(want) + 2]))
        if head == want[0] and words[1:len(want)] == want[1:] and not _inside(path, folder) and not asked:
            return True
        if depth < 4 and head in ("sh", "bash", "zsh", "dash") and "-c" in words[1:-1]:
            if runs(words[words.index("-c") + 1], item, folder, depth + 1):
                return True
        if depth < 4 and head == "ssh":
            rest = words[1:]
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if len(rest[0]) == 2 and rest[0][1] in SSH_VALUE else rest[1:]
            if len(rest) > 1 and runs(" ".join(rest[1:]), item, folder, depth + 1):
                return True
    return False


def code_runs(code: str) -> tuple[set, list[str]]:
    """What execute_code's Python calls and runs: (the names it calls, the commands it gives a runner: terminal,
    subprocess, os.system…). A text, a list of texts, or an f-string of texts and of names the code set to a
    text; a command it builds otherwise is not read. Code that is no Python: (set(), every quoted text in it)."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return set(), [a or b for a, b in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", code)]
    known = {t.id: n.value.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
             and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str) for t in n.targets
             if isinstance(t, ast.Name)}

    def text(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, (ast.List, ast.Tuple)) and all(text(e) is not None for e in node.elts):
            return shlex.join(text(e) for e in node.elts)
        if isinstance(node, ast.JoinedStr):
            parts = [text(v) if not isinstance(v, ast.FormattedValue) else
                     known.get(v.value.id) if isinstance(v.value, ast.Name) else None for v in node.values]
            return "".join(parts) if all(p is not None for p in parts) else None
        return None
    called, commands = set(), []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        called.add(name)
        if name in RUNNERS:
            given = node.args[:1] + [k.value for k in node.keywords if k.arg in ("command", "args", "cmd")]
            commands += [t for t in map(text, given) if t is not None]
    return called, commands


WRITERS = {"cp": "last", "mv": "last", "ln": "last", "install": "last", "rsync": "last", "tee": "all",
           "touch": "all", "mkdir": "all", "rm": "all", "rmdir": "all", "truncate": "all"}
NOWHERE = ("/dev/null", "/dev/stdout", "/dev/stderr")


def written(command: str) -> list[str]:
    """The paths a shell line writes, as far as reading it tells: a > or >> target, what tee, touch, mkdir, rm,
    rmdir and truncate name, the last operand of cp, mv, ln, install and rsync, the file of a sed -i. A relative
    path after a cd of the line is joined to it. A line shlex cannot read: none (a note, not a guard)."""
    text = command.replace("$(", " ; ").replace("`", " ; ").replace("\n", " ; ")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    paths, base, commands, words, target = [], "", [], [], None

    def at(path: str) -> str:  # a relative path after a cd of the line
        return os.path.join(base, path) if base and not path.startswith(("/", "~", "$")) else path
    for token in tokens + [";"]:
        if target is not None:
            if not (target == ">&" or re.fullmatch(r"\d+|-", token)):  # 2>&1 duplicates a descriptor
                paths.append(at(token))
            target = None
        elif token and set(token) <= set(";&|()<>"):
            target = token if ">" in token else None
            commands, words = (commands + [words], []) if target is None and words else (commands, words)
        else:
            words.append(token)
            continue
        if target is None and commands:
            for words_ in commands:
                while words_ and (re.match(r"^[A-Za-z_]\w*=", words_[0]) or os.path.basename(words_[0]) in WRAPPERS):
                    words_ = words_[1:]
                head, operands = (os.path.basename(words_[0]), [w for w in words_[1:] if not w.startswith("-")]
                                  ) if words_ else ("", [])
                if head == "cd" and operands:
                    base = at(operands[0])
                    continue
                if head == "sed" and any(w.startswith("-i") for w in words_[1:]):
                    operands = operands[-1:]
                elif WRITERS.get(head) == "last":
                    operands = operands[-1:]
                elif WRITERS.get(head) != "all":
                    operands = []
                paths += [at(p) for p in operands]
            commands = []
    return paths


JOB_ID = re.compile(r"[A-Za-z0-9][\w-]{0,63}")  # a job id as Hermes's cron gives one: never an option


def scheduled(calls) -> list[str]:
    """The cron jobs a model turn made itself: the job id of each cronjob_manage create Hermes answered with
    success. An update, a pause or a run names a job that was there before (the user's): never taken."""
    found = []
    for call in calls:
        args = call["args"] if isinstance(call.get("args"), dict) else {}
        if call["tool"] != "cronjob_manage" or str(args.get("action") or "").strip().lower() != "create":
            continue
        reply = last_json(call.get("reply") or "") or {}
        job = reply.get("job_id")
        if reply.get("success") is True and isinstance(job, str) and JOB_ID.fullmatch(job):
            found.append(job)
    return list(dict.fromkeys(found))


def outside_writes(calls, folder: Path) -> list[str]:
    """What a model turn wrote outside its root, as its calls show it: a write_file or a patch there, a shell
    line's written path (written()). A $ path, which could be anywhere, is not said."""
    found = []
    for tool, args in calls:
        args = args if isinstance(args, dict) else {}
        start = Path(os.path.expanduser(args["workdir"])) if isinstance(args.get("workdir"), str) else folder
        paths = ([args.get("path")] if tool in ("write_file", "patch") else
                 written(args["command"]) if tool == "terminal" and isinstance(args.get("command"), str) else [])
        for path in paths:
            if not isinstance(path, str) or path in NOWHERE or path.startswith("$"):
                continue
            full = Path(start, os.path.expanduser(path)).resolve()
            if full != folder and folder not in full.parents and path not in found:
                found.append(path)
    return found


def forbidden(calls, items, folder: Path | None = None, who: str = "it") -> list[str]:
    """What a model turn did that its case forbids: a tool it called by that name, a terminal command that ran
    it, the same in execute_code's Python, and inside the graph of a habit_run (a scenario, or the file it names
    when it is still there), which runs live. calls: (tool, arguments) pairs."""
    found = []
    for item in items or ():
        for tool, args in calls:
            command = args.get("command") if tool == "terminal" and isinstance(args, dict) else None
            code = args.get("code") if tool == "execute_code" and isinstance(args, dict) else None
            called, commands = code_runs(code) if isinstance(code, str) else (set(), [])
            if tool == item or item in called:
                found.append(f"{who} called {item}" + (" through execute_code" if tool != item else "")
                             + ", which the case forbids")
                break
            if isinstance(command, str) and runs(command, item, folder):
                found.append(f"{who} ran {item} ({tail(command, 160)}), which the case forbids")
                break
            ran = next((c for c in commands if runs(c, item, folder)), None)
            if ran is not None:
                found.append(f"{who} ran {item} through execute_code ({tail(ran, 160)}), which the case forbids")
                break
    for tool, args in calls if who == "it" else ():
        scenario = args.get("scenario") if tool == "habit_run" and isinstance(args, dict) else None
        if scenario is None and tool == "habit_run" and isinstance(args.get("file"), str):
            try:
                scenario = Path(os.path.expanduser(args["file"])).read_text(encoding="utf-8")
            except OSError:
                scenario = None
        if isinstance(scenario, str):
            try:
                scenario = parse(scenario)
            except Exception:
                scenario = None
        nodes = scenario.get("nodes") if isinstance(scenario, dict) else None
        steps = [(n.get("do"), n.get("args") or {}) for n in (nodes or {}).values() if isinstance(n, dict) and "do" in n]
        found += [f for f in forbidden(steps, items, folder, "its habit_run") if f not in found]
    return found


# ---- running ----------------------------------------------------------------

class Bench:
    def __init__(self, hermes: str, home: Path, budget: Budget, out: Path | None, work: Path):
        self.hermes, self.home, self.budget, self.out, self.work = hermes, home, budget, out, work
        self.records: list[dict] = []
        self.skipped = 0  # model turns and runs the budget stopped
        self.off = home / "plugin-data" / "habits" / "off"
        self.paused = home / "plugin-data" / "habits" / "paused"
        self.pausing = False  # the paused file is this bench's: it lifts it
        self.last_calls: dict[str, int] = {}  # a case's last run's model calls: what its next run is counted at
        self.dearest = 0  # the most model calls of any run so far: what a case's first run is counted at
        self._kept: Path | None = None
        self.roots: list[Path] = []  # the fresh roots of the turns whose record is not out yet
        self.pending: list[dict] = []  # creates the bench cut that had not answered: they may still save
        self.unscheduled: set = set()  # the cron jobs of model turns this bench took away
        self.files: set = set()  # the names of the scenario files loaded: none may sit near a turn
        self.habits: set = set()  # the folder names of the cases' habits: no copy of one may either

    def emit(self, record: dict):
        line = json.dumps(record, ensure_ascii=False, default=str)
        print(line, flush=True)
        if self.out:
            with self.out.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        self.records.append(record)

    log: Path | None = None  # --log: notes and the table go there too, the file opened for each write

    @staticmethod
    def note(text: str):
        tell(f"bench: {text}")

    def call(self, argv: list[str], cwd: Path, timeout: float, env: dict | None = None) -> tuple:
        """(exit code or None, stdout, stderr, ms)."""
        began = time.monotonic()
        try:
            proc = subprocess.run(argv, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True,
                                  timeout=timeout, stdin=subprocess.DEVNULL)
            done = (proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            done = (None, exc.stdout or "", f"timed out after {timeout} s")
        except OSError as exc:
            done = (None, "", f"{type(exc).__name__}: {exc}")
        return (*done, int((time.monotonic() - began) * 1000))

    def find(self, name: str) -> dict:
        return lookup(self.hermes, name)

    def shell(self, case, command: str) -> tuple:
        return self.call(["/bin/sh", "-c", command], case["_scratch"], SETUP_TIMEOUT,
                         {"SCRATCH": str(case["_scratch"]), "REPO": str(REPO)})

    def setup(self, case) -> str:
        """The case's folder made ready: its setup run there, then its habit read when the setup makes it, and its
        copy written. "" or why not."""
        scratch = case["_scratch"]
        if scratch:
            scratch.mkdir(parents=True, exist_ok=True)
        if case.get("setup"):
            code, out, err, _ = self.shell(case, case["setup"])
            if code != 0:
                return f"setup failed (exit {code}): {tail(err or out)}"
        if case.get("_late"):
            if not case["_source"].is_file():
                return f"its setup made no habit file {case['_source']}"
            try:
                case.update(habit_of(case, self.find))
            except BenchError as exc:
                return str(exc)
        if case["_copy"] is not None:
            path = Path(case["_target"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(case["_copy"], encoding="utf-8")
            if case.get("_copy_skill") is not None:
                (path.parent.parent / "SKILL.md").write_text(case["_copy_skill"], encoding="utf-8")
        return ""

    def verify(self, case, command: str | None = None) -> list[str]:
        """The case's own check of its scratch folder after a run that acted (or command, a baseline's own): []
        when it passes."""
        command = case.get("verify") if command is None else command
        if not command:
            return []
        code, out, err, _ = self.shell(case, command)
        return [] if code == 0 else [f"verify failed (exit {code}): {tail(err or out)}"]

    def notice(self, case, record: dict) -> dict:
        """The case's note, a check whose failure is said, never counted (something else may have moved the folder:
        a real sync of the vault lands every 16 min or so): in the record's notes and on stderr."""
        if case.get("note"):
            code, out, err, _ = self.shell(case, case["note"])
            if code != 0:
                record["notes"] = record.get("notes", []) + [f"note failed (exit {code}): {tail(err or out)}"]
                self.note(f"{case['id']} {record['kind']} {record['i']}: {record['notes'][-1]}")
        return record

    def base(self, case, kind: str, i: int) -> dict:
        return {"case": case["id"], "task": case["task"], "kind": kind, "i": i}

    def habit_run(self, case, target: str, env: dict | None = None) -> tuple:
        """`hermes habits run` on the case: (exit code, its JSON or None, stderr, wall ms). Live only within the
        scratch folder; from the case's message when it has one."""
        argv = [self.hermes, "habits", "run", target, "--json"]
        argv += [f"--input={k}={_text(v)}" for k, v in (case.get("inputs") or {}).items()]
        argv += [f"--message={case['message']}"] if case.get("message") is not None else []
        argv += ["--live", f"--within={case['_scratch']}"] if case.get("mode") == "live" else []
        argv += [f"--memory={case['memory']}"] if case.get("memory") else []
        code, stdout, stderr, wall = self.call(argv, case["_scratch"] or self.work, case.get("timeout", TIMEOUT), env)
        out = last_json(stdout)  # a live run's dry pass first is the CLI's check, not the task: paid, not timed
        plan = int((out or {}).get("plan_model_calls") or 0)
        nodes = int(((out or {}).get("counts") or {}).get("llm") or 0)  # its llm nodes' calls, the rest questions'
        self.budget.charge(int((out or {}).get("model_calls") or 0) + plan, nodes, plan)
        held = int(((out or {}).get("trigger") or {}).get("recorded_ms") or 0)  # a question the hook never asks
        return code, out, stderr, max(0, wall - int((out or {}).get("plan_ms") or 0) - held)

    def run(self, case, i: int) -> dict | None:
        record = {**self.base(case, "run", i), "habit": case["habit"], "mode": case.get("mode", "dry"),
                  "expect": [k for k in STATUSES if k in (case.get("expect") or {})]}
        # A case's first run is counted at the dearest run so far: each at 0, a file of many cases would never stop.
        # 0 before any, so habits that call no model all run
        # at --budget 0, and a file whose first run calls one goes over by that run at most.
        if not self.budget.allows(self.last_calls.get(case["id"], self.dearest) * MODEL_CALL):
            self.stop(case, f"run {i}")
            return None
        why = self.setup(case)
        if why:
            return {**record, "ok": False, "why": [why]}
        code, out, stderr, wall = self.habit_run(case, case["_target"])
        record["wall_ms"] = wall
        if out is None:
            return {**record, "ok": False, "why": [f"no JSON from hermes habits run (exit {code}): {tail(stderr)}"]}
        calls = out.get("model_calls")
        self.last_calls[case["id"]] = int(calls or 0) + int(out.get("plan_model_calls") or 0)
        self.dearest = max(self.dearest, self.last_calls[case["id"]])
        if out.get("status") in ("refused", "error"):
            why = [f"{out['status']}: {out.get('message')}"]
        else:
            why = matches(case.get("expect") or {}, out)
            if case.get("mode") == "live":  # what it did, seen in the folder
                why += self.verify(case)
                self.notice(case, record)
        return {**record, "ok": not why or slow({"why": why}), "slow": slow({"why": why}), "why": why,
                "status": out.get("status"),
                "message": str(out.get("message") or "")[:300], "reason": out.get("reason"),
                "failed": out.get("failed"), "doubts": out.get("doubts"),
                "doubted": [t[:160] for t in doubt_texts(out.get("doubted"))[:20]], "ms": out.get("ms"),
                "trigger": out.get("trigger"), "asks": out.get("asks"),
                "near": near(out.get("trigger"), str(out.get("habit") or ""), record["expect"]),
                "counts": out.get("counts"), "model_calls": calls, "llm_ms": out.get("llm_ms"),
                "planned_tools": sorted({p.get("tool") for p in out.get("planned") or [] if isinstance(p, dict)}),
                "planned": out.get("planned_count"), "backends": sorted(set(out.get("backends") or [])),
                "tokens": 0 if calls == 0 else None, "cost": 0.0 if calls == 0 else None,
                "version": out.get("version"), "habit_kind": out.get("kind"),
                **({"plan_ms": out["plan_ms"], "plan_model_calls": out.get("plan_model_calls")}
                   if out.get("plan_ms") else {})}

    def off_now(self) -> bool:
        try:
            return abs(time.time() - self.off.stat().st_mtime) < OFF_SECONDS
        except OSError:
            return False

    def clear_stale(self):
        """An off file, or a bench's pause, that a bench that stopped left behind: habits are on again, and it goes."""
        if self.off.exists() and not self.off_now():
            self.off.unlink(missing_ok=True)
            self.note(f"removed a stale {self.off} (left by a bench that stopped)")
        pid = self.pause_of()
        if pid is not None and not alive(pid):
            self.paused.unlink(missing_ok=True)
            self.note(f"removed a stale {self.paused} (a bench's pause, left by a bench that stopped)")

    def pause_of(self) -> int | None:
        """The process id of the bench whose pause the paused file is, or None: no file, or the user's own pause."""
        try:
            found = re.fullmatch(re.escape(PAUSE) + r"(\d+)\s*", self.paused.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None
        return int(found[1]) if found else None

    @contextlib.contextmanager
    def habits_paused(self):
        """A create turn runs on this real machine: a watch it saves has a real cron job at once, and a woken shadow check
        tells its turn to make the planned calls itself, unattended. The
        plugin's paused file keeps every watch check and the trigger silent, while the CLI and the habit tools work:
        from the turn to its late wait, its works check and its retire, and on while a cut create may still save
        (the end's sweep). A pause there before is the user's, or another bench's: left as it is, and so is the
        user's own made meanwhile (cockpit.pause writes an empty file)."""
        if self.pausing and self.pause_of() != os.getpid():
            self.pausing = False  # the user paused habits meanwhile (/habits pause empties the file): theirs now
        mine = self.pausing or not self.paused.exists()
        if mine:
            self.paused.parent.mkdir(parents=True, exist_ok=True)
            self.paused.write_text(f"{PAUSE}{os.getpid()}\n", encoding="utf-8")
            self.pausing = True
        try:
            yield
        finally:
            if mine and not self.pending:
                if self.pause_of() == os.getpid():  # the user's /habits pause meanwhile empties it: theirs now
                    self.paused.unlink(missing_ok=True)
                self.pausing = False

    @contextlib.contextmanager
    def habits_off(self):
        """Hermes 0.23 runs a `hermes -z` turn in its gateway, which HABITS_OFF does not reach: the plugin's off
        file does, for every session while it is fresh. Removed after unless it was fresh before (another bench
        is measuring): a stale one is this bench's to remove."""
        mine = not self.off_now()
        self.off.parent.mkdir(parents=True, exist_ok=True)
        self.off.touch()
        try:
            yield
        finally:
            if mine:
                self.off.unlink(missing_ok=True)

    def turn(self, usage: Path, began: float, cwd: Path) -> dict | None:
        """The session a `hermes -z` made: by the id in its usage report, else the one oneshot session started in
        the turn's root since the call began (none when there are more); its numbers from state.db, else from
        the report."""
        try:
            report = json.loads(usage.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {}
        keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "estimated_cost_usd",
                "cost_status")
        turn = {k: report.get(k) for k in ("session_id",) + keys}
        turn.update(api_calls=report.get("api_calls"), tool_calls=None, answer=None, habit_calls=[], calls=[],
                    said=[])
        try:
            with contextlib.closing(sqlite3.connect(f"file:{self.home / 'state.db'}?mode=ro", uri=True,
                                                    timeout=10)) as db:
                db.row_factory = sqlite3.Row
                row = None
                if report.get("session_id"):
                    row = db.execute("SELECT * FROM sessions WHERE id = ?", (report["session_id"],)).fetchone()
                if row is None:
                    rows = db.execute("SELECT * FROM sessions WHERE source = 'oneshot' AND started_at >= ? "
                                      "AND cwd = ? ORDER BY started_at", (began - 1, str(cwd))).fetchall()
                    if len(rows) > 1:
                        self.note(f"{len(rows)} oneshot sessions started in {cwd} meanwhile: none is taken")
                    row = rows[0] if len(rows) == 1 else None
                if row is not None:
                    turn.update(session_id=row["id"], api_calls=row["api_call_count"],
                                tool_calls=row["tool_call_count"], **{k: row[k] for k in keys if k in row.keys()})
                    said = db.execute("SELECT content FROM messages WHERE session_id = ? AND role = 'assistant' "
                                      "AND content IS NOT NULL AND content != '' ORDER BY id DESC LIMIT 1",
                                      (row["id"],)).fetchone()
                    turn["answer"] = said["content"] if said else None
                    calls = tool_calls(db, row["id"])
                    turn["calls"] = [(c["tool"], c["args"]) for c in calls]
                    turn["said"], turn["habit_calls"] = calls, habit_calls(calls)
        except sqlite3.Error as exc:
            self.note(f"state.db not read ({exc}): the numbers come from the usage report")
        if not turn["session_id"]:
            return None
        turn["tokens"] = sum(int(turn.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                                                             "cache_write_tokens"))
        turn["cost"] = price(turn)
        return turn

    def fresh(self, case) -> dict:
        """The case moved to a fresh, empty root for one model turn, <temp>/habits-bench-turns/hb-<random>/<case>/: {scratch} is that
        root in every text, and its setup builds it there. Nothing of the bench sits in it or above it (the habit's
        copy, the scenario, a prove.py), and its path names no repository."""
        root = Path(tempfile.mkdtemp(prefix="hb-", dir=roots_base())).resolve() / slug(case["id"])
        root.mkdir()
        self.roots.append(root.parent)  # removed once the turn's record is out: the next turn finds none of it
        moved = fill({**case["_raw"], "scratch": str(root)}, root)
        moved = {k: v for k, v in moved.items() if not k.startswith("_")}
        if moved.get("memory"):
            moved["memory"] = memory_of(moved, root)
        return {**case, **moved, "_scratch": root, "_copy": None, "_late": False}

    @staticmethod
    def prompt(case, kind: str) -> str:
        """A turn's prompt; one whose setup built its folder is told to stay there (a case without one reads the
        real machine, which the line would forbid), unless the prompt says where to read already."""
        text = case[kind]["prompt"]
        told = FENCE.split("{scratch}")[0].strip() in text  # its own wording ("… and outside ~/notes")
        return text if not case.get("setup") or told else f"{text} {FENCE.replace('{scratch}', str(case['_scratch']))}"

    def hermes_turn(self, case, kind: str, i: int, prompt: str, env: dict) -> tuple[dict, dict | None]:
        """One `hermes -z` turn in the case's fresh root: (its record so far, the turn's numbers or None). Its usage
        report goes next to the root, outside it: a process list shows nothing the turn does not know."""
        for folder in ("copies", "made", "kept"):  # the bench's copies sit in $TMPDIR, which the turn uses too
            shutil.rmtree(self.work / folder, ignore_errors=True)
        cwd = case["_scratch"]
        usage = cwd.parent / "usage.json"
        began = time.time()
        context = self.habits_off() if kind == "baseline" else contextlib.nullcontext()
        late = None
        with context:  # habits stay off while a cut baseline may still run
            code, stdout, stderr, wall = self.call([self.hermes, "-z", prompt, "--usage-file", str(usage)], cwd,
                                                   case.get("timeout", TIMEOUT), env)
            if code is None and stderr.startswith("timed out"):
                late = self.settle(cwd, began)
                if late is None:
                    self.note(f"{case['id']} {kind} {i}: no answer {LATE} s after the bench cut it; cleaned anyway, "
                              "and a habit it saves later is taken away before the next create and at the end")
        turn = self.turn(usage, began, cwd)
        self.budget.add(turn["cost"] if turn else FIRST_CALL)  # a turn not found is counted at a guess, never at 0
        why = [] if code == 0 else [f"hermes -z exit {code}: {tail(stderr)}"]
        if turn is None:
            why.append("no session found in state.db")
        record = {**self.base(case, kind, i), "ok": not why, "why": why, "ms": wall, "seconds": round(wall / 1000, 2),
                  "root": str(cwd), **({"late_s": late} if code is None and stderr.startswith("timed out") else {})}
        if turn:
            wrote = outside_writes(turn["calls"], cwd)
            if wrote:
                record["outside"] = wrote
                self.note(f"{case['id']} {kind} {i} wrote outside its root: {', '.join(wrote[:8])}")
            jobs = scheduled(turn["said"])
            if jobs:  # Hermes's own jobs run model turns on their schedule after the bench: the pause never reaches them
                record["cron_jobs"] = jobs
                left = self.unschedule(jobs, f"{case['id']} {kind} {i}")
                record.update({"cron_left": left} if left else {})
            record.update(session=turn["session_id"], tokens=turn["tokens"], input_tokens=turn["input_tokens"],
                          output_tokens=turn["output_tokens"], cache_read_tokens=turn["cache_read_tokens"],
                          api_calls=turn["api_calls"], tool_calls=turn["tool_calls"], cost=round(turn["cost"], 6),
                          answer=(turn["answer"] or stdout.strip() or "")[:500],
                          habit_calls=[f"{c['tool']}:{c['reply'].get('status')}" for c in turn["habit_calls"]])
        return record, turn

    def unschedule(self, jobs, label: str) -> list[str]:
        """Take away the cron jobs a model turn made on this Mac, through Hermes's cron CLI: the ones left (the
        removal failed) are said with the command that removes them."""
        left = []
        for job in jobs:
            if job in self.unscheduled:
                continue
            code, out, err, _ = self.call([self.hermes, "cron", "remove", job], self.work, 120)
            if code == 0:
                self.unscheduled.add(job)
                self.note(f"{label}: the turn made the cron job {job}; taken away")
            else:
                left.append(job)
                self.note(f"{label}: the turn made the cron job {job}, which still runs: `hermes cron remove {job}` "
                          f"failed (exit {code}: {tail(err or out)}); run it by hand")
        return left

    def settle(self, cwd: Path, began: float, seconds: float | None = None) -> float | None:
        """Wait, up to seconds (LATE), for the last answer of a turn the bench cut: Hermes 0.23's gateway goes on
        after `hermes -z` is killed. The seconds waited, or None when no answer came."""
        start, seconds = time.monotonic(), LATE if seconds is None else seconds
        while True:
            last = self.session_rows(cwd, began, "SELECT role, content, tool_calls FROM messages WHERE session_id = ? "
                                                  "ORDER BY id DESC LIMIT 1")
            last = last[0] if last else None
            if last and last[0] == "assistant" and last[1] and not last[2]:  # an answer, no call after it
                return round(time.monotonic() - start, 1)
            if time.monotonic() - start >= seconds:
                return None
            time.sleep(0.5)

    def session_rows(self, cwd: Path, began: float, query: str) -> list:
        """The rows of query (one ? for the session id) for the one oneshot session started in cwd since began;
        [] when there is none, or more, or state.db cannot be read."""
        try:
            with contextlib.closing(sqlite3.connect(f"file:{self.home / 'state.db'}?mode=ro", uri=True,
                                                    timeout=10)) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute("SELECT id FROM sessions WHERE source = 'oneshot' AND started_at >= ? AND cwd = ?",
                                  (began - 1, str(cwd))).fetchall()
                if len(rows) != 1:
                    return []
                sid = rows[0]["id"]
                return tool_calls(db, sid) if query == "calls" else db.execute(query, (sid,)).fetchall()
        except sqlite3.Error:
            return []

    def sweep(self, wait: float = 0):
        """The creates the bench cut that had not answered: what each saved since is cleaned as a create's habit is
        (retired first), under the case's name or any other; one whose turn answers leaves the list. At the
        end (wait: seconds to wait for each answer), one still silent is said, with how to take its habit away."""
        for item in list(self.pending):
            answered = self.settle(item["cwd"], item["began"], wait) is not None
            rows = self.session_rows(item["cwd"], item["began"], "calls")
            self.unschedule(scheduled(rows), f"create {item['name']} (late)")  # a cron job made since, too
            saves = [c["reply"] for c in habit_calls(rows) if c["tool"] == "habit_save"]
            kinds = {r["habit"]: r.get("kind") for r in saves
                     if r.get("status") == "saved" and isinstance(r.get("habit"), str)}
            for name in dict.fromkeys([item["name"], *kinds]):
                try:
                    info = self.find(name)
                except BenchError as exc:
                    self.note(str(exc))
                    continue
                if info.get("path") and Path(info["path"]).resolve().parent.parent not in item["before"]:
                    kept, trouble = self.clean(name, Path(info["path"]), kinds.get(name) or info.get("kind"),
                                               item["label"] + "-late", item["before"])
                    self.note(f"create {name}: saved after the bench cut its turn; taken away"
                              + (f" ({trouble})" if trouble else ""))
            if answered:
                self.pending.remove(item)
            elif wait:
                self.note(f"create {item['name']}: its turn never answered, {wait} s after the bench; it may still "
                          f"save a habit: look with `hermes habits list`, then `hermes habits retire <name>` and "
                          "move its folder out of ~/.hermes/skills")

    def stop(self, case, what: str):
        if not self.skipped:
            self.note(f"budget: stopped before the {what} of {case['id']}: {self.budget.spent:.4f} $ spent of "
                      f"{self.budget.limit} $, the next turn is counted at {self.budget.next():.4f} $; no more "
                      "model turns, nor runs that call a model")
        self.skipped += 1

    def baseline(self, case, i: int) -> dict | None:
        if not self.budget.allows():
            self.stop(case, "baseline turn")
            return None
        case = self.fresh(case)
        why = self.setup(case) or self.near(case)
        if why:
            return {**self.base(case, "baseline", i), "ok": False, "why": [why]}
        record, turn = self.hermes_turn(case, "baseline", i, self.prompt(case, "baseline"), {"HABITS_OFF": "1"})
        if turn:
            used = record["habit_calls"]
            missed = [f"used a habit ({', '.join(used)}): not Hermes alone"] if used else []
            if case["baseline"].get("expect"):
                missed += matches({"end": case["baseline"]["expect"]},
                                  {"status": "end", "message": turn["answer"] or ""})
            missed += forbidden(turn["calls"], case.get("forbid"), case["_scratch"])
            missed += self.verify(case, case["baseline"].get("verify"))  # its own when it has one
            record.update(ok=record["ok"] and not missed, why=record["why"] + missed)
            self.notice(case, record)
        return record

    def folders(self, name: str | None = None) -> set:
        """The folders under skills/ with that name, or (no name) every skill's folder, habit or not, hidden ones
        too: a SKILL.md's folder, and a HABIT.yaml's skill folder."""
        skills = self.home / "skills"
        if not skills.is_dir():
            return set()
        return ({p.resolve() for p in skills.rglob(name)} if name else
                {p.parent.resolve() for p in skills.rglob("SKILL.md")}
                | {p.parent.parent.resolve() for p in skills.rglob("HABIT.yaml") if p.parent.name == "assets"})

    def taken(self, name: str) -> str:
        """Why a create run may not make a habit of this name, or "": a habit, a skill, a folder (hidden ones too)
        or a SKILL.md has it, or Hermes could not say. A create on a skill's name would rewrite its SKILL.md."""
        try:
            info = self.find(name)
        except BenchError as exc:
            return str(exc)
        if info.get("path"):
            return f'a habit named "{name}" exists already'
        if info.get("skill") is not False:
            return (f'a skill named "{name}" exists already' if info.get("skill")
                    else f'Hermes could not say whether a skill is named "{name}"')
        for folder in sorted(self.folders(name)):
            return f"{folder} exists already"
        skills = self.home / "skills"
        for skill_md in skills.rglob("SKILL.md") if skills.is_dir() else ():
            if declared(skill_md) == name:
                return f'{skill_md} is named "{name}"'
        return ""

    def create(self, case, i: int) -> dict | None:
        name, record = case["create"]["name"], {**self.base(case, "create", i), "name": case["create"]["name"]}
        self.sweep()  # a habit an earlier cut create saved late must not meet this one
        refused = self.taken(name)
        if refused:
            return {**record, "ok": False, "why": [f"{refused}: a create run could rewrite it; pick another name"]}
        if self.off_now():
            return {**record, "ok": False, "why": ["habits are off (the plugin's off file is fresh): a create run "
                                                   "needs them on"]}
        if not self.budget.allows():
            self.stop(case, "create turn")
            return None
        case = self.fresh(case)
        why = self.setup(case) or self.near(case)
        if why:
            return {**record, "ok": False, "why": [why]}
        before = self.folders(name) | self.folders()  # a skill there before the turn is the user's, habit or not
        began = time.time()
        made, turn = self.hermes_turn(case, "create", i, self.prompt(case, "create"), {})
        if "late_s" in made and made["late_s"] is None:  # cut, and silent since: it may still save in the gateway
            self.pending.append({"name": name, "before": before, "cwd": case["_scratch"], "began": began,
                                 "label": f"{case['id']}-{i}"})
            made["may_save_late"] = True
        calls = (turn or {}).get("habit_calls", [])
        saves = [c["reply"] for c in calls if c["tool"] == "habit_save"]
        statuses = [s.get("status") for s in saves]
        saved = next((s for s in reversed(saves) if s.get("status") == "saved" and s.get("habit") in (None, name)),
                     None)
        others = {s["habit"]: s for s in saves if s.get("status") == "saved" and isinstance(s.get("habit"), str)
                  and s["habit"] != name}  # saved under another name: the bench's too, a watch's cron job included
        try:
            info = self.find(name)
        except BenchError as exc:
            info = {}
            self.note(str(exc))
        path = Path(saved["path"]) if saved and saved.get("path") else Path(info["path"]) if info.get("path") else None
        found = path is not None and path.is_file() and bool(info.get("path"))
        code, out, err, _ = (self.call([self.hermes, "habits", "check", str(path)], self.work, 120) if found
                             else (1, "", "", 0))
        valid = found and code == 0
        banned = forbidden((turn or {}).get("calls", []), case.get("forbid"), case["_scratch"])
        why = made["why"] + ([] if saves else ["no habit_save call"]) + banned
        if saves and not found:
            why.append(f'no habit named "{name}" after the turn ({", ".join(map(str, statuses))})')
        elif found and not valid:
            why.append(f"hermes habits check: {tail(out or err)}")
        said = (turn or {}).get("said", [])
        self.notice(case, made)
        record.update(made, ok=valid and not made["why"] and not banned, why=why, saves=len(saves),
                      statuses=statuses, tries=[c["reply"].get("status") for c in calls if c["tool"] == "habit_run"],
                      first_try=valid and first_try(said, name), save_tries=save_tries(said, name), valid=valid,
                      kind_made=(saved or {}).get("kind") or info.get("kind"), path=str(path) if path else None)
        troubles = []
        if found:
            kept, trouble = self.clean(name, path, record["kind_made"], f"{case['id']}-{i}", before)
            record["kept"] = kept
            troubles += [trouble] if trouble else []
            if kept and valid:
                record.update(self.works(case, Path(kept), f"{slug(case['id'])}-{i}", record["kind_made"],
                                         path.parent.parent))
        for other, reply in others.items():
            where = reply.get("path") or self.found_path(other)
            if where:
                troubles += [t for t in [self.clean(other, Path(where), reply.get("kind"), f"{case['id']}-{i}",
                                                    before)[1]] if t]
            else:
                troubles.append(f"{other}: saved, but no file found to take away")
        if others:
            record["also_saved"] = list(others)
        if troubles:
            record["clean_error"] = "; ".join(troubles)
        return record

    def found_path(self, name: str) -> str | None:
        try:
            return self.find(name).get("path")
        except BenchError as exc:
            self.note(str(exc))
            return None

    def works(self, case, kept: Path, label: str, kind, saved: Path | None = None) -> dict:
        """The habit Hermes made, run on the turn's root: does it do the task? Its copy, with the case's rewrite
        applied, against create.expect (else the case's end and wake), dry unless create.mode is live (then the
        case's verify judges too), at create.hour, from the case's message when create.message is true. With
        create.before, one check first runs on the state that command builds, and the judged one after the setup,
        both with the same fresh --memory: a watch's check: changed needs a reference.
        The copy holds its whole assets/, its own scripts too (a habit may name them by their saved path), each text with saved's paths pointed at the copy, then rewritten."""
        made = case["create"]
        expect = made.get("expect") or {k: v for k, v in (case.get("expect") or {}).items() if k not in ("none", "other")}
        if not set(STATUSES) & set(expect):
            return {"works": None, "works_why": ["the case expects no run"]}
        message = case.get("message") if made.get("message") else None
        if message is not None and kind != "request":
            return {"works": None, "works_why": [f"create.message: the habit made is a {kind}; only a request habit "
                                                 "starts from a message"]}
        copy = self.work / "made" / label / kept.name
        moved = {name: f"{copy}/" for name in spelled(saved)} if saved else {}
        files = {}  # each file as the copy holds it: a text pointed and rewritten, other bytes as they are
        for path in [kept / "SKILL.md", *sorted((kept / "assets").rglob("*"))]:
            if path.is_file() and not path.is_symlink():  # a link could bring in a file from anywhere
                data = path.read_bytes()
                with contextlib.suppress(UnicodeDecodeError):
                    data = rewritten(rewritten(data.decode("utf-8"), moved), case.get("rewrite") or {})
                files[path.relative_to(kept)] = data
        text = files.get(Path("assets/HABIT.yaml"), "")
        try:
            subs = [sub for _, sub in subhabits(case["id"], text, self.find)]
        except BenchError as exc:
            return {"works": None, "works_why": [str(exc)]}
        if any(DOWNLOADS.search(t) for t in [text, *subs, *(d for d in files.values() if isinstance(d, str))]):
            return {"works": None, "works_why": ["it names a Downloads folder: not run"]}
        for part, data in files.items():
            (copy / part).parent.mkdir(parents=True, exist_ok=True)
            (copy / part).write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
            shutil.copymode(kept / part, copy / part)  # a script it runs by its path stays one that runs
        env = {"TZ": zone(made["hour"])} if "hour" in made else None
        case, first = {**case, "mode": made.get("mode", "dry"), "message": message}, {}
        if made.get("before"):
            code, said, err, _ = self.shell(case, made["before"])
            if code != 0:
                return {"works": None, "works_why": [f"create.before failed (exit {code}): {tail(err or said)}"]}
            case["memory"] = str(copy.parent / "memory.json")  # the bench's own, new: no reference yet
            code, out, stderr, _ = self.habit_run(case, str(copy / "assets" / "HABIT.yaml"), env)
            first = {"works_before": (out or {}).get("status")}  # what the check before the change gave
        why = self.setup(case)
        if why:
            return {"works": None, "works_why": [why], **first}
        code, out, stderr, _ = self.habit_run(case, str(copy / "assets" / "HABIT.yaml"), env)
        if out is None:
            return {"works": False, "works_why": [f"no JSON from hermes habits run (exit {code}): {tail(stderr)}"],
                    **first}
        if out.get("status") in ("refused", "error"):
            return {"works": False, "works_why": [f"{out['status']}: {out.get('message')}"], **first}
        missed = matches(expect, out) + (self.verify(case) if made.get("mode") == "live" else [])
        # What it said and where it stopped, as a run's record keeps them: a hand-back says at which step.
        return {"works": not missed or slow({"why": missed}), "works_why": missed, "works_status": out.get("status"),
                "works_message": str(out.get("message") or "")[:300], "works_node": out.get("node"),
                "works_reason": out.get("reason"), **first}

    def kept_dir(self) -> Path:
        """This bench's own folder for the habits it made, next to the results: one per bench, never shared. With no
        --out, the work folder's, gone before the next model turn (the Trash still holds each created skill)."""
        if not self.out:
            (self.work / "kept").mkdir(parents=True, exist_ok=True)
            return self.work / "kept"
        if self._kept is None:
            root = self.out.parent
            root.mkdir(parents=True, exist_ok=True)
            self._kept = Path(tempfile.mkdtemp(prefix=f"habits-bench-created-{time.strftime('%m%d-%H%M%S')}-",
                                               dir=root))
        return self._kept

    def clean(self, name: str, path: Path, kind, label: str, before: set) -> tuple:
        """A habit a create run made is the bench's, not the user's: a copy is kept next to the results, it is
        retired (a watch's cron job goes with it), and its skill folder goes to the Trash, so the next run can make
        it again; the last two whatever happened to the copy. A folder there before the turn is left as it is.
        (the copy's path or None, what went wrong or "")."""
        folder = path.parent.parent
        skills = (self.home / "skills").resolve()
        if path.parent.name != "assets" or skills not in folder.resolve().parents:
            return None, f"{folder} is not a skill folder under {skills}: left as it is"
        if folder.resolve() in before:
            return None, f"{folder} was there before the turn: left as it is"
        kept, trouble = None, []
        try:
            kept = self.kept_dir() / f"{label}-{folder.name}"
            copy_kept(folder, kept)
        except OSError as exc:
            kept = None
            trouble.append(f"no copy kept ({type(exc).__name__}: {exc})")
        finally:
            # Every kind, while its skill is there to be found: a create asked for a watch may save a request,
            # active since it only reads, whose ledger row would stay active once its skill is in the Trash.
            code = self.call([self.hermes, "habits", "retire", name], self.work, 120)[0]
            if code != 0:
                trouble.append(f"hermes habits retire {name} failed (exit {code}): "
                               + ("its cron job may still run" if kind == "watch" else "the ledger keeps its status"))
            try:
                bin_ = Path.home() / ".Trash"
                bin_.mkdir(exist_ok=True)
                trash = Path(tempfile.mkdtemp(prefix=f"{folder.name}-bench-", dir=bin_)) / folder.name
                shutil.move(str(folder), str(trash))
                self.note(f"create {name}: a copy is in {kept}; the skill went to {trash}")
            except OSError as exc:
                trouble.append(f"{folder} not moved to the Trash ({type(exc).__name__}: {exc})")
        for line in trouble:
            self.note(f"create {name}: {line}")
        return (str(kept) if kept else None), "; ".join(trouble)

    def case(self, case, baseline: bool, create: bool):
        steps = [(self.run, case.get("repeat", 1))]
        steps += [(self.baseline, case["baseline"].get("repeat", 1))] if baseline and case.get("baseline") else []
        steps += [(self.create, case["create"].get("repeat", 1))] if create and case.get("create") else []
        for step, times in steps:
            for i in range(times):
                try:
                    with self.habits_paused() if step == self.create else contextlib.nullcontext():
                        record = step(case, i)
                finally:  # a turn's root holds what it wrote, a draft graph too: the next turn must not find it
                    while self.roots:
                        shutil.rmtree(self.roots.pop(), ignore_errors=True)
                if record is not None:
                    self.emit(record)

    def near(self, case) -> str:
        """Why no model turn may run now, or "": something of the answer key within reach of its root."""
        found = within_reach(roots_base(), GUARDED | self.files, self.habits)
        if not found:
            return ""
        self.note(f"{case['id']}: no model turn while these sit within {REACH} folders of {roots_base()}: "
                  + ", ".join(found))
        return (f"within reach of a model turn ({REACH} folders under {roots_base()}): {', '.join(found[:3])}"
                + (f" and {len(found) - 3} more" if len(found) > 3 else "") + "; move or delete them first")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # another user's process: alive
        return True
    return True


def counts(cases, baseline: bool = True, create: bool = True) -> str:
    turns = {kind: sum(c[kind].get("repeat", 1) for c in cases if c.get(kind))
             for kind, wanted in (("baseline", baseline), ("create", create)) if wanted}
    return (f"{len(cases)} cases, {sum(c.get('repeat', 1) for c in cases)} habit runs, "
            f"{turns.get('baseline', 0)} baseline and {turns.get('create', 0)} create turns")


def dry_check(target: Path, home: Path, find) -> int:
    """Every case read and checked as a run would check it, file by file, and nothing run: no setup, no habit
    run, no model turn (a habit given by its name, not its path, is still looked up: hermes habits show, a read).
    A habit file a setup makes is checked when it runs. 0 when no file has a problem, else 2."""
    wrong: list[str] = []
    try:
        cases = load(target, wrong)
    except BenchError as exc:  # no scenario file at all
        tell(f"bench: {exc}")
        return 2
    bad, work = False, Path(tempfile.gettempdir()) / "habits-bench-dry"  # never written: nothing runs
    for file in (f.resolve() for f in files_of(target)):
        mine = [c for c in cases if c["_file"] == file]
        missed = [w[len(f"{file}: "):] for w in wrong if w.startswith(f"{file}: ")]
        for case in mine:
            try:
                ready = prepare(case, home, find, work)
            except BenchError as exc:
                missed.append(str(exc))
                continue
            if ready["_late"]:
                Bench.note(f"{file}: {case['id']}: its setup makes its habit {ready['_source']}: checked when it runs")
        bad = bad or bool(missed)
        Bench.note(f"{file}: " + (f"{counts(mine)}: no problem" if not missed else
                                  f"{len(missed)} problem{'s' if len(missed) > 1 else ''}:\n  " + "\n  ".join(missed)))
    if any(c.get("baseline") or c.get("create") for c in cases):  # a warning: the turns would refuse to run
        found = within_reach(roots_base(), GUARDED | {Path(c["_file"]).name for c in cases},
                             {Path(c["_source"]).parent.parent.name for c in cases if c.get("_source")})
        if found:
            Bench.note(f"warning: no model turn will run while these sit within {REACH} folders of {roots_base()}: "
                       + ", ".join(found))
    return 2 if bad else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bench/run.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("target", help="a scenario file, or a folder of them")
    parser.add_argument("--only", default="", help="case ids, comma-separated")
    parser.add_argument("--no-baseline", action="store_true", help="skip the Hermes turns without habits")
    parser.add_argument("--no-create", action="store_true", help="skip the Hermes turns that write a habit")
    parser.add_argument("--budget", type=float, default=BUDGET,
                        help=f"dollars for model turns and a habit's own model calls, at most (default {BUDGET})")
    parser.add_argument("--out", type=Path, default=None, help="also append the JSON lines to this file")
    parser.add_argument("--log", type=Path, default=None, help="also append the notes and the table to this file")
    parser.add_argument("--hermes", default=None, help="the hermes command (default: hermes on PATH)")
    parser.add_argument("--home", type=Path, default=None, help="Hermes's folder (default: $HERMES_HOME or ~/.hermes)")
    parser.add_argument("--night", default="40,2", help="a watch's night: checks,wakes (default 40,2: every 15 "
                                                          "min from 23 h to 9 h, 2 wakes)")
    parser.add_argument("--dry-check", action="store_true",
                        help="read and check every case, file by file, and run nothing (no setup, run nor turn)")
    args = parser.parse_args(argv)
    night = re.fullmatch(r"(\d+),(\d+)", args.night)
    if not night or int(night[2]) > int(night[1]) or not int(night[1]):
        parser.error("--night is checks,wakes: two whole numbers, wakes at most checks (40,2)")
    # Every path made whole first: the bench then works from /, so lsof on it shows no folder of the suite.
    home = (args.home or Path(os.environ.get("HERMES_HOME") or "~/.hermes")).expanduser().absolute()
    hermes = args.hermes or shutil.which("hermes") or "hermes"
    hermes = os.path.abspath(os.path.expanduser(hermes)) if os.sep in hermes else hermes
    args.out = args.out.expanduser().absolute() if args.out else None
    Bench.log = args.log.expanduser().absolute() if args.log else None
    if args.dry_check:
        return dry_check(Path(args.target).expanduser(), home, lambda name: lookup(hermes, name))
    with tempfile.TemporaryDirectory(prefix="habits-bench-") as work:
        try:
            chosen = load(Path(args.target).expanduser())
            only = [i for i in args.only.split(",") if i]
            unknown = sorted(set(only) - {c["id"] for c in chosen} - {c["id"].split(":")[-1] for c in chosen})
            if unknown:
                raise BenchError(f"no case {', '.join(unknown)}")
            chosen = [prepare(c, home, lambda name: lookup(hermes, name), Path(work)) for c in chosen
                      if not only or c["id"] in only or c["id"].split(":")[-1] in only]
        except BenchError as exc:
            tell(f"bench: {exc}")
            return 2
        Bench.note(counts(chosen, not args.no_baseline, not args.no_create) + f" (model turns cost money; budget: "
                                                                              f"{args.budget} $)")
        if any(c.get("baseline") and not args.no_baseline or c.get("create") and not args.no_create for c in chosen):
            shipped = [d for d in ("bench", "tests") if (home / "plugins" / "habits" / d).is_dir()]
            if shipped:  # a model turn can read the bench's code there
                Bench.note(f"{home / 'plugins' / 'habits'} ships {' and '.join(d + '/' for d in shipped)}: a model "
                           "turn can read them. To keep only what Hermes loads: git -C ~/.hermes/plugins/habits "
                           "sparse-checkout set --no-cone '/*' '!/bench/' '!/tests/'")
        bench = Bench(hermes, home, Budget(args.budget), args.out, Path(work))
        bench.files = {Path(c["_file"]).name for c in chosen if c.get("_file")}
        bench.habits = {Path(c["_source"]).parent.parent.name for c in chosen if c.get("_source")}
        bench.clear_stale()
        started = os.getcwd()
        os.chdir("/")
        try:
            for case in chosen:
                bench.case(case, baseline=not args.no_baseline, create=not args.no_create)
            with bench.habits_paused() if bench.pending else contextlib.nullcontext():
                bench.sweep(LATE_END)  # what a cut create saved after its record never stays installed
            if bench.pausing:
                bench.note(f"habits stay paused ({bench.paused}): a create that never answered may still save a "
                           "habit. Once `hermes habits list` shows none of the bench's, `hermes habits resume`")
        finally:
            os.chdir(started)
    rows = summary(bench.records)
    tell("\n".join(["", table(rows), *night_lines(nights(rows, int(night[1]), int(night[2]))),
                    *one_off_lines(one_offs(bench.records)), *own_lines(own_calls(bench.records)),
                    *near_lines(bench.records), LEGEND]))
    tell(bench.budget.line() + (f"; {bench.skipped} stopped by the budget" if bench.skipped else ""))
    return 0 if bench.records and all(r["ok"] for r in bench.records) else 1


SHOWN = ("import json, os, runpy, sys; sys.argv = json.load(os.fdopen(int(sys.argv[1]))); "
         "runpy.run_path(sys.argv[0], run_name='__main__')")  # all a process list shows of the bench


def hide():
    """Start again under a command line that names no file: a model turn that lists processes (ps) sees neither the
    scenario nor the bench, only SHOWN. The arguments go through a pipe. A shell that started the bench keeps its
    own line: start it as a shell's last command, without a pipe (bench/README.md)."""
    if os.environ.get(HIDDEN) or not sys.executable or not hasattr(os, "execv"):
        return
    read, write = os.pipe()
    os.write(write, json.dumps([os.path.abspath(sys.argv[0]), *sys.argv[1:]]).encode())
    os.close(write)
    os.set_inheritable(read, True)
    os.environ[HIDDEN] = "1"
    os.execv(sys.executable, [sys.executable, "-c", SHOWN, str(read)])


if __name__ == "__main__":
    hide()
    sys.exit(main())
