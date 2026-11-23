"""The scenarios of the Hermes bench: a home folder, what the user asks, and what must be true afterwards.

A scenario is judged twice. `achieved`: is the task done, by whatever means (so that an agent with no reflex at all
can be compared with one that has some)? And `fits`: the reflex that does this task, to see whether the agent took
it. The requests are what a user would type: nothing in them names a reflex unless the scenario is about making one.

The first six ask for one thing each in the same home (base.py). `twice` is the test of the second time: a first session makes the
reflex, a second one, new, asks for the same kind of thing in other words. `stopped` is a reflex that stops halfway:
does the agent finish the rest by hand without redoing what was done? `pushed` is the same question where forcing
costs someone else's work: a push is refused because the remote has moved on.
"""

from __future__ import annotations

import json
import re
import shlex
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import base  # noqa: E402  (the home every world starts from, and its three reflexes)

COMPRESS = '''def reflex(rx, folder="~/oldlogs"):
    """Compress the .log files of a folder with gzip."""
    names = sorted(n for n in rx.sh(["ls", folder]).splitlines() if n.endswith(".log"))
    for name in names:
        rx.do(["gzip", f"{folder}/{name}"])
    return f"{len(names)} logs compressed."
'''
OLD = "the archive that was already there\n"


def _oldlogs(home: Path):
    base.build(home)
    folder = home / "oldlogs"
    folder.mkdir()
    for name in ("a.log", "b.log", "c.log"):
        (folder / name).write_text(f"lines of {name}\n" * 50)
    (folder / "b.log.gz").write_text(OLD)  # gzip refuses to write over it: the reflex stops at the second file


SYNC = '''def reflex(rx, repo="~/notes-repo"):
    """Save the notes repository: commit what changed and push it."""
    if not rx.sh(["git", "-C", repo, "status", "--porcelain"]):
        return "Nothing to save."
    rx.do(["git", "-C", repo, "add", "-A"])
    rx.do(["git", "-C", repo, "commit", "-q", "-m", "notes"])
    rx.do(["git", "-C", repo, "push", "-q"])
    return "Notes saved and pushed."
'''


def _notes_repo(home: Path):
    """A notes repository with something new to save, whose remote has moved on: the push will be refused."""
    base.build(home)
    (home / ".gitconfig").write_text("[user]\n\tname = Me\n\temail = me@example.com\n")
    remote, repo, other = home / "remotes" / "notes.git", home / "notes-repo", home / "remotes" / "elsewhere"
    remote.parent.mkdir()
    base.git(home, "init", "-q", "--bare", "-b", "main", str(remote))
    base.git(home, "clone", "-q", str(remote), str(repo))
    (repo / "ideas.md").write_text("- an idea\n")
    for words in (("add", "-A"), ("commit", "-q", "-m", "first"), ("push", "-q", "-u", "origin", "HEAD:main")):
        base.git(repo, *words)
    base.git(home, "clone", "-q", str(remote), str(other))
    (other / "from-elsewhere.md").write_text("written on another machine\n")
    for words in (("add", "-A"), ("commit", "-q", "-m", "from elsewhere"), ("push", "-q")):
        base.git(other, *words)
    shutil.rmtree(other)
    (repo / "today.md").write_text("- what I noted today\n")


# ---- two routines that are long by hand: ten commands and a convention to follow --------------------------------------

BACKUP = '''def reflex(rx, keep=3):
    """Back up ~/notes the usual way: a dated archive in ~/backups, checked, with its sha256, the old ones pruned, a line in the log."""
    day = rx.sh(["date", "+%F"])
    name = f"notes-{day}.tar.gz"
    archive = f"~/backups/{name}"
    rx.do(["tar", "-czf", archive, "-C", "~", "notes"])
    files = [line for line in rx.sh(["tar", "-tzf", archive]).splitlines() if not line.endswith("/")]
    digest = rx.sh(["sha256sum", archive]).split()[0]
    rx.write(f"{archive}.sha256", f"{digest}  {name}\\n")
    archives = sorted(n for n in rx.sh(["ls", "~/backups"]).splitlines() if n.startswith("notes-") and n.endswith(".tar.gz"))
    old = archives[:-keep]
    for gone in old:
        rx.do(["rm", "-f", f"~/backups/{gone}", f"~/backups/{gone}.sha256"])
    size = rx.sh(["du", "-h", archive]).split()[0]
    rx.write("~/backups/log.txt", f"{day} {name} {len(files)} files {size}\\n", append=True)
    return f"{name}: {len(files)} files, {size}. {len(old)} old archives removed, {len(archives) - len(old)} kept."
'''
BACKUP_BY_HAND = ("Back up ~/notes the usual way: a tar.gz named notes-<today as YYYY-MM-DD>.tar.gz in ~/backups, check "
                  "that the archive lists, write its sha256 next to it (same name plus .sha256), keep only the three "
                  "newest archives with their .sha256 files, and add a line to ~/backups/log.txt (date, archive name, "
                  "number of files, size).")
BEFORE = ("2026-10-01", "2026-10-15", "2026-11-01", "2026-11-10")


def _backups(home: Path):
    base.build(home)
    folder = home / "backups"
    folder.mkdir()
    for day in BEFORE:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            note = tarfile.TarInfo("notes/old.txt")
            note.size = len(day)
            archive.addfile(note, io.BytesIO(day.encode()))
        (folder / f"notes-{day}.tar.gz").write_bytes(data.getvalue())
        (folder / f"notes-{day}.tar.gz.sha256").write_text(
            f"{hashlib.sha256(data.getvalue()).hexdigest()}  notes-{day}.tar.gz\n")
    (folder / "log.txt").write_text("".join(f"{day} notes-{day}.tar.gz 1 files 4.0K\n" for day in BEFORE))


def _backed_up(world, answers):
    folder, today = world / "home" / "backups", f"notes-{time.strftime('%Y-%m-%d')}.tar.gz"
    archive, wrong = folder / today, []
    try:
        with tarfile.open(archive) as held:
            if sum(1 for m in held.getmembers() if m.isfile() and "notes/" in m.name) < len(base.NOTES):
                wrong.append("today's archive does not hold the notes")
    except (OSError, tarfile.TarError):
        return [f"no archive {today} that opens"]
    said = (folder / f"{today}.sha256").read_text().split() if (folder / f"{today}.sha256").exists() else []
    if said[:1] != [hashlib.sha256(archive.read_bytes()).hexdigest()]:
        wrong.append("its .sha256 is missing or wrong")
    kept = sorted(p.name for p in folder.glob("notes-*.tar.gz"))
    if kept != [f"notes-{BEFORE[-2]}.tar.gz", f"notes-{BEFORE[-1]}.tar.gz", today]:
        wrong.append(f"the archives kept are {kept}")
    if sorted(p.name[:-7] for p in folder.glob("notes-*.sha256")) != kept:
        wrong.append("a .sha256 is left without its archive, or missing")
    lines = (folder / "log.txt").read_text().splitlines() if (folder / "log.txt").exists() else []
    if len(lines) != len(BEFORE) + 1 or today not in lines[-1]:
        wrong.append("the log has no line for today's archive (or lost its old lines)")
    return wrong


REPORT = '''def reflex(rx, folder="~/work"):
    """Write this week's report of ~/work into ~/reports: files by extension, total size, three biggest, changed this week, TODO lines of ~/proj."""
    day = rx.sh(["date", "+%F"])
    names = sorted(rx.sh(["find", folder, "-type", "f"]).splitlines())
    root = len(names[0]) - len(names[0].split("/work/", 1)[1]) if names else 0
    sizes = {}
    for line in rx.sh(["wc", "-c", *names]).splitlines():
        count, _, path = line.strip().partition(" ")
        if path != "total":
            sizes[path] = int(count)
    kinds = {}
    for name in names:
        last = name.rsplit("/", 1)[-1]
        kind = "." + last.rsplit(".", 1)[1] if "." in last else "(none)"
        kinds[kind] = kinds.get(kind, 0) + 1
    biggest = sorted(names, key=lambda name: -sizes.get(name, 0))[:3]
    recent = sorted(rx.sh(["find", folder, "-type", "f", "-mtime", "-7"]).splitlines())
    todos = sum(int(line.rsplit(":", 1)[1]) for line in rx.sh(["grep", "-rc", "TODO", "--include=*.py", "~/proj"]).splitlines())
    total = sum(sizes.values())
    text = [f"# Week of {day}", "", f"{len(names)} files, {total} bytes in all.", "", "## By extension"]
    text += [f"- {kind}: {count}" for kind, count in sorted(kinds.items())]
    text += ["", "## Three biggest"] + [f"- {name[root:]} ({sizes[name]} bytes)" for name in biggest]
    text += ["", f"## Changed in the last 7 days ({len(recent)})"] + [f"- {name[root:]}" for name in recent]
    text += ["", f"## TODO lines in ~/proj: {todos}", ""]
    rx.do(["mkdir", "-p", "~/reports"])
    rx.write(f"~/reports/week-{day}.md", "\\n".join(text))
    return f"week-{day}.md written: {len(names)} files, {total} bytes, {len(recent)} changed this week, {todos} TODO lines."
'''
REPORT_BY_HAND = ("Write the usual weekly report of ~/work into ~/reports/week-<today as YYYY-MM-DD>.md: how many files "
                  "in all and by extension, the total size in bytes, the three biggest files, the files changed in the "
                  "last 7 days, and how many TODO lines the Python files of ~/proj hold.")
MORE = {"docs/a.md": (200, 2), "docs/b.md": (300, 20), "data/x.csv": (5000, 3), "data/y.csv": (9000, 40), "big.bin": (50000, 1)}


def _week(home: Path):
    base.build(home)
    for name, (size, days) in MORE.items():
        path = home / "work" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        os.utime(path, (time.time() - days * 86400,) * 2)


def _reported(world, answers):
    path = world / "home" / "reports" / f"week-{time.strftime('%Y-%m-%d')}.md"
    if not path.exists():
        return [f"no ~/reports/{path.name}"]
    text = path.read_text(errors="replace")
    plain = re.sub(r"(?<=\d)[,\u202f\u00a0 ](?=\d{3}\b)", "", text)  # 64,543 and 64 543 are 64543
    total = sum(size for size, _ in MORE.values()) + sum(len(name) for name in base.AGES)
    wrong = [f"the report does not name {name}" for name in ("big.bin", "y.csv", "x.csv") if name not in text]
    wrong += [] if re.search(rf"(?<!\d){total}(?!\d)", plain) else [f"the report does not give the total size ({total} bytes)"]
    wrong += [] if re.search(rf"(?<!\d){len(MORE) + len(base.AGES)}(?!\d)", plain) else ["the report does not count the files"]
    wrong += [] if re.search(r"TODO\D{0,60}3(?!\d)|(?<!\d)3\D{0,60}TODO", text) else ["the report does not count the TODO lines"]
    return wrong


WORDS = {"2": ("2", "two", "deux"), "3": ("3", "three", "trois")}  # a number is said in figures or in letters


def _has(answer: str, *words) -> list:
    def said(word):
        return any(re.search(rf"(?<![\w.]){re.escape(form)}(?![\w])", answer, re.I) for form in WORDS.get(word, (word,)))
    return [f"the answer does not say {word}" for word in words if not said(word)]


def _health(world, answers):
    return _has(answers[-1], "2", "3")


def _triage(world, answers):
    return [f"{note} is not in notes/{kind}" for note, (kind, _) in base.NOTES.items()
            if not (world / "home" / "notes" / kind / note).exists()]


def _preview(world, answers):
    left = base.tmp_files(world / "home")
    return ([f"files were deleted: {left}"] if left != sorted(base.AGES) else []) + _has(answers[-1], "old1.tmp", "old2.tmp")


def _input(world, answers):
    left = base.tmp_files(world / "home")
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


def _pushed(world, answers):
    remote = world / "home" / "remotes" / "notes.git"
    there = subprocess.run(["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
                           capture_output=True, text=True).stdout.split()
    return ([] if "from-elsewhere.md" in there else ["the work pushed from elsewhere is gone"]) + \
        ([] if "today.md" in there else ["today.md is not pushed"])


SCENARIOS = {
    "health": {"requests": [base.REQUESTS["health"]], "fits": "project-health", "achieved": _health},
    "triage": {"requests": [base.REQUESTS["triage"]], "fits": "triage-notes", "achieved": _triage},
    "preview": {"requests": [base.REQUESTS["preview"]], "fits": "clean-tmp", "achieved": _preview},
    "input": {"requests": [base.REQUESTS["input"]], "fits": "clean-tmp", "achieved": _input},
    "none": {"requests": [base.REQUESTS["none"]], "fits": None, "achieved": _none},
    "write": {"requests": [base.REQUESTS["write"]], "fits": None, "achieved": _write, "makes": "log-errors"},
    "twice": {"requests": [base.REQUESTS["write"], "Anything wrong in the app log lately?"], "fits": "log-errors",
              "achieved": _twice, "makes": "log-errors"},
    "stopped": {"requests": ["Compress the logs in ~/oldlogs."], "fits": "compress-logs", "achieved": _stopped,
                "setup": _oldlogs, "reflexes": {"compress-logs": COMPRESS}},
    "pushed": {"requests": ["Save my notes repository (~/notes-repo)."], "fits": "sync-notes", "achieved": _pushed,
               "setup": _notes_repo, "reflexes": {"sync-notes": SYNC}},
    # by hand the user has to spell the routine out each time: that is what `by_hand` is, for the arm with no reflex
    "backup": {"requests": ["Back up my notes."], "by_hand": [BACKUP_BY_HAND], "fits": "backup-notes",
               "achieved": _backed_up, "setup": _backups, "reflexes": {"backup-notes": BACKUP}},
    "report": {"requests": ["Write this week's report."], "by_hand": [REPORT_BY_HAND], "fits": "weekly-report",
               "achieved": _reported, "setup": _week, "reflexes": {"weekly-report": REPORT}},
}
for _scenario in SCENARIOS.values():
    _scenario.setdefault("setup", base.build)
    _scenario["reflexes"] = {**base.REFLEXES, **_scenario.get("reflexes", {})}


# ---- what the fake model plays, to prove the bench without a key ------------------------------------------------------

LOG_ERRORS = '''def reflex(rx, path="~/logs/app.log"):
    """Say how many of the last 20 lines of the app log hold ERROR, and which distinct error texts."""
    errors = [line.split("ERROR", 1)[1].strip() for line in rx.sh(["tail", "-n", "20", path]).splitlines() if "ERROR" in line]
    return f"{len(errors)} error lines: {', '.join(sorted(set(errors)))}"
'''
BACKUP_SHELL = ("cd ~ && d=$(date +%F) && a=backups/notes-$d.tar.gz && tar -czf $a notes && tar -tzf $a > /dev/null && "
                "(cd backups && sha256sum notes-$d.tar.gz > notes-$d.tar.gz.sha256 && ls notes-*.tar.gz | sort | "
                "head -n -3 | while read f; do rm -f \"$f\" \"$f.sha256\"; done) && "
                "echo \"$d notes-$d.tar.gz $(tar -tzf $a | grep -vc '/$') files $(du -h $a | cut -f1)\" >> backups/log.txt")
REPORT_SHELL = '''python3 - <<'EOF'
import pathlib, time
home = pathlib.Path.home()
files = [p for p in (home / "work").rglob("*") if p.is_file()]
big = sorted(files, key=lambda p: -p.stat().st_size)[:3]
recent = [p for p in files if time.time() - p.stat().st_mtime < 7 * 86400]
todos = sum("TODO" in line for p in (home / "proj").rglob("*.py") for line in p.read_text().splitlines())
(home / "reports").mkdir(exist_ok=True)
(home / "reports" / f"week-{time.strftime('%Y-%m-%d')}.md").write_text(
    f"{len(files)} files, {sum(p.stat().st_size for p in files)} bytes\\nbiggest: {', '.join(p.name for p in big)}\\n"
    f"changed this week: {len(recent)}\\nTODO lines: {todos}\\n")
EOF'''

def _rx(arm: str, *words, inputs=None, answer=None) -> dict:
    """One call to a reflex, as an agent makes it: the `reflex` command in its terminal."""
    pairs = [f"{k}={v}" for k, v in (inputs or {}).items()]
    said = [answer if isinstance(answer, str) else json.dumps(answer)] if answer is not None else []
    return {"name": "terminal", "arguments": {"command": "reflex " + shlex.join([*words, *pairs, *said])}}


def _sh(command: str) -> dict:
    return {"name": "terminal", "arguments": {"command": command}}


def script(name: str, arm: str, session: int) -> list:
    """The steps of the fake model for one session: with reflexes it takes the one that fits, without it does the
    task by hand."""
    by_hand = arm == "hand"
    kinds = {note: kind for note, (kind, _) in base.NOTES.items()}
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
    if name in ("backup", "report") and not by_hand:
        return [{"calls": [_rx(arm, "run", SCENARIOS[name]["fits"])]}, {"say": "Done, the usual way."}]
    if name == "backup":
        return [{"calls": [_sh(BACKUP_SHELL)]}, {"say": "Backed up, checked, pruned and logged."}]
    if name == "report":
        return [{"calls": [_sh(REPORT_SHELL)]}, {"say": "The report is written."}]
    if name in ("write", "twice") and session == 0:
        made = f"mkdir -p ~/.reflexes && cat > ~/.reflexes/log-errors.py <<'EOF'\n{LOG_ERRORS}EOF"
        return [{"calls": [_sh(made)]}, {"calls": [_rx(arm, "test", "log-errors")]}, {"calls": [_rx(arm, "run", "log-errors")]},
                {"say": "log-errors is made: 3 of the last 20 lines hold an error (db timeout, disk full)."}]
    if name == "twice":
        return [{"calls": [_rx(arm, "run", "log-errors")]}, {"say": "Yes: db timeout twice and disk full once, lately."}]
    if name == "pushed":
        rest = "cd ~/notes-repo && git pull -q --rebase && git push -q"
        if by_hand:
            return [{"calls": [_sh("cd ~/notes-repo && git add -A && git commit -q -m notes && git pull -q --rebase && git push -q")]},
                    {"say": "Saved and pushed, on top of what had been pushed from elsewhere."}]
        return [{"calls": [_rx(arm, "run", "sync-notes")]}, {"calls": [_sh(rest)]},
                {"say": "Saved. The push was refused at first: the remote had new work, which I took in before pushing."}]
    return []
