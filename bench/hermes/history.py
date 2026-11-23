#!/usr/bin/env python3
"""What a real Hermes history says reflexes could take over.

    python bench/hermes/history.py ~/.hermes [--least 5] [--show]

Reads the sessions a Hermes home has kept (its state.db), and counts. It never writes: the database is opened as a
file that cannot change (no lock, no journal file of its own), so it can be read while Hermes runs.

What it prints holds no text of yours: names of tools, names of programs with their subcommand (`git status`, never
its arguments), counts, tokens and dollars. With --show it adds the first words of each repeated request, for your
eyes: leave it out of anything you send on.

Three questions:

  1. What does the agent do with its hands? Tool calls, and for the terminal the commands by shape, with the share
     that is plain reading: what a reflex may run without asking anyone.
  2. What comes back? The same request asked again and again (a cron job, a habit of yours): how often, what each
     time costs in model requests and in dollars, and whether the agent then takes the same steps each time
     (the same tools and commands, whatever their order).
  3. What would a reflex save? For each of those routines, the requests and dollars beyond one call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from reflexes import readers  # noqa: E402

WITH_SUBCOMMAND = {"git", "docker", "kubectl", "systemctl", "journalctl", "npm", "pnpm", "yarn", "pip", "pip3", "uv",
                   "brew", "apt", "apt-get", "gh", "cargo", "go", "hermes", "launchctl", "tmux", "crontab", "reflex"}
JOINTS = {"&&", "||", ";", "|", "&", ";;"}
OPENING = {"do", "then", "else", "elif", "if", "while", "until", "{", "(", "!", "time"}  # the command comes after
SHELL = {"for", "done", "fi", "case", "esac", "in", "}", "[", "[[", "test", "true", "false", ":", "cd", "echo",
         "printf", "export", "set", "local", "read"}  # the shell's own words: no program is run
VALUED = {"-C", "-c", "-n", "--namespace", "--context", "--git-dir", "--work-tree"}  # options whose next word is theirs
ASSIGNED = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
QUIET = re.compile(r"(\d*>>?\s*/dev/null|\d*>&\d|&>\s*/dev/null)")  # output thrown away: not a change


def commands(line: str) -> list:
    """The simple commands of a shell line, each as its words. A line that cannot be read gives none. What follows
    a `<<` is text handed to a program, not commands: it is left out."""
    try:
        lexer = shlex.shlex(QUIET.sub(" ", line.split("<<")[0]).replace("\n", " ; "), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return []
    found, now = [], []
    for word in words:
        if word in JOINTS:
            found.append(now)
            now = []
        elif now or (word not in OPENING and not ASSIGNED.match(word)):  # `do gzip f`, `FOO=1 make` start further on
            now.append(word)
    return [argv for argv in [*found, now] if argv and readers.program(argv[0]) not in SHELL]


@lru_cache(maxsize=None)
def known(name: str) -> bool:
    """Only the name of a program this machine has is ever printed: no stray word of a command line can be."""
    return name in WITH_SUBCOMMAND or shutil.which(name) is not None


def shape(argv: list) -> str:
    """`git status`, `ls`: the program and, where it has them, its subcommand. Never an argument."""
    name = readers.program(argv[0])
    if not known(name):
        return "(other)"
    rest = [w for at, w in enumerate(argv[1:]) if not w.startswith("-") and argv[at] not in VALUED]
    return f"{name} {rest[0]}" if name in WITH_SUBCOMMAND and rest and rest[0].replace("-", "").isalpha() else name


def is_read(argv: list) -> bool:
    try:
        return not any(w in (">", ">>", "<") or w.startswith((">", "2>")) for w in argv) and readers.why_not(argv) == ""
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("home", help="a Hermes home (or a state.db): it is only read")
    parser.add_argument("--least", type=int, default=5, help="a request counts as a routine from this many times")
    parser.add_argument("--show", action="store_true", help="also print the first words of each routine (private)")
    options = parser.parse_args()
    path = Path(options.home).expanduser()
    path = path / "state.db" if path.is_dir() else path
    if not path.exists():
        sys.exit(f"no {path}")
    db = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    columns = {row["name"] for row in db.execute("pragma table_info(sessions)")}
    wanted = [c for c in ("id", "source", "model", "api_call_count", "tool_call_count", "input_tokens", "output_tokens",
                          "cache_read_tokens", "estimated_cost_usd") if c in columns]
    sessions = {row["id"]: dict(row) for row in db.execute(f"select {', '.join(wanted)} from sessions")}
    first, did = {}, defaultdict(list)  # the first request of each session, and what it then did, in order
    tools, shapes, reads, changes, unread = Counter(), Counter(), Counter(), Counter(), 0
    for row in db.execute("select session_id, role, content, tool_calls from messages order by session_id, id"):
        session = row["session_id"]
        if row["role"] == "user" and session not in first and isinstance(row["content"], str):
            first[session] = row["content"]
        if row["role"] != "assistant" or not row["tool_calls"]:
            continue
        try:
            calls = json.loads(row["tool_calls"])
        except ValueError:
            continue
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") or {}
            name = str(function.get("name") or "?")
            tools[name] += 1
            if name != "terminal":
                did[session].append(name)
                continue
            try:
                line = str(json.loads(function.get("arguments") or "{}").get("command") or "")
            except ValueError:
                line = ""
            simple = commands(line)
            unread += not simple
            for argv in simple:
                what = shape(argv)
                shapes[what] += 1
                (reads if is_read(argv) else changes)[what] += 1
                did[session].append(what)

    def total(counter):
        return sum(counter.values())

    def top(counter, most=15):
        return ", ".join(f"{name} {count}" for name, count in counter.most_common(most)) or "-"

    by_source = Counter(s.get("source") or "?" for s in sessions.values())
    print(f"{len(sessions)} sessions: {top(by_source)}")
    print(f"\n1. Its hands: {total(tools)} tool calls")
    print(f"   {top(tools)}")
    if total(shapes):
        print(f"   terminal: {total(shapes)} commands, {total(reads)} plain reads ({100 * total(reads) // total(shapes)} %), "
              f"{total(changes)} that change something or are not known as reads; {unread} lines not understood")
        print(f"   read most:    {top(reads)}")
        print(f"   changed most: {top(changes)}")

    routines = defaultdict(list)
    for session, text in first.items():
        if session in sessions:
            source = sessions[session].get("source") or "?"
            routines[(source, hashlib.sha256(text.strip().encode()).hexdigest()[:8])].append(session)
    repeated = sorted(((key, ids) for key, ids in routines.items() if len(ids) >= options.least),
                      key=lambda item: -len(item[1]))
    inside = sum(len(ids) for _, ids in repeated)
    print(f"\n2. What comes back: {len(repeated)} requests asked {options.least} times or more, "
          f"{inside} sessions of {len(sessions)}")
    saved_requests = saved_dollars = 0.0
    for (source, key), ids in repeated[:40]:
        rows = [sessions[i] for i in ids]

        def mean(field):
            values = [r.get(field) or 0 for r in rows]
            return sum(values) / len(values)

        cost = sum(r.get("estimated_cost_usd") or 0 for r in rows)
        ways = Counter(" + ".join(dict.fromkeys(did.get(i, []))) for i in ids)  # which steps, whatever their order
        way, same = ways.most_common(1)[0]
        steady = 100 * same // len(ids)
        if steady >= 50:  # the agent mostly does the same thing: what a reflex is for
            saved_requests += sum(max(0, (r.get("api_call_count") or 0) - 1) for r in rows)
            saved_dollars += cost * max(0.0, 1 - 1 / max(1.0, mean("api_call_count")))
        print(f"   {key} {source:8} {len(ids):5} times, {mean('api_call_count'):4.1f} requests and "
              f"{mean('tool_call_count'):4.1f} tool calls each, ${cost:8.2f} in all; the same steps {steady:3} % of the time"
              + (f": {way[:150]}" if way else ": no tool at all"))
        if options.show:
            print(f"            {first[ids[0]].strip()[:100]!r}")
    print(f"\n3. Where the agent does the same steps at least half the time, one call instead of a session would "
          f"spare about {int(saved_requests)} model requests and ${saved_dollars:.2f} (a rough upper bound).")


if __name__ == "__main__":
    main()
