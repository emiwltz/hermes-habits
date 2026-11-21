"""The writing bench: eight small real tasks. A model writes a reflex for each from a guide alone, first try, and
this file says whether the reflex works: it builds a throwaway home, runs the reflex there for real (dry, then live,
then again where the task has a memory), answers its questions as the big model would, and checks what is on disk.

A task is: its statement (what the model reads), and play(run, home) -> the list of what went wrong (empty: it works).
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import threading
import time
from pathlib import Path

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


def git(folder: Path, *words):
    subprocess.run(["git", *words], cwd=folder, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


def files(folder: Path) -> list:
    return sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []


def tree(folder: Path) -> list:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file() and ".git" not in p.parts)


def value(outcome, key):
    return outcome.result.get(key) if isinstance(outcome.result, dict) else None


def ended(outcome, wrong: list, where: str) -> bool:
    if outcome.status != "done":
        said = outcome.reason if outcome.status == "stopped" else f"it asked: {json.dumps(outcome.asks)[:160]}"
        wrong.append(f"{where}: {outcome.status}: {said[:240]}")
        return False
    return True


# ---- 1. sort a folder by extension ----------------------------------------------------------------------------

SORT = """Write a reflex that sorts the files of the folder ~/inbox by type.
- Move each FILE directly inside ~/inbox (never a sub-folder, never what is inside one) into:
  ~/sorted/images/ when its extension is jpg, jpeg, png or gif, in any letter case;
  ~/sorted/docs/ when its extension is pdf, txt or md, in any letter case;
  ~/sorted/other/ for any other file.
- Create the folders when missing. Never overwrite a file that is already there. File names may hold spaces.
- When ~/inbox holds no file, finish by saying so.
- Otherwise return {"images": n, "docs": n, "other": n, "message": "..."}: how many files went into each folder.
The reflex takes no input."""
SORTED = {"images": ["photo.jpg", "scan.PNG"], "docs": ["README.md", "my report.pdf", "notes.txt"],
          "other": ["archive.zip", "data.bin"]}


def play_sort(run, home: Path) -> list:
    wrong = []
    (home / "inbox" / "old").mkdir(parents=True)
    (home / "inbox" / "old" / "keep.txt").write_text("keep")
    empty = run()
    if ended(empty, wrong, "empty inbox") and files(home / "sorted" / "other"):
        wrong.append("empty inbox: something was moved")
    for names in SORTED.values():
        for name in names:
            (home / "inbox" / name).write_text(name)
    dry = run(dry=True)
    if ended(dry, wrong, "dry run"):
        if len(files(home / "inbox")) != 7:
            wrong.append("dry run: files were moved")
        if not dry.planned:
            wrong.append("dry run: nothing planned")
    live = run()
    if ended(live, wrong, "live"):
        for kind, names in SORTED.items():
            if files(home / "sorted" / kind) != names:
                wrong.append(f"sorted/{kind}: {files(home / 'sorted' / kind)} instead of {names}")
            if value(live, kind) != len(names):
                wrong.append(f"result[{kind!r}] is {value(live, kind)!r} instead of {len(names)}")
        if files(home / "inbox"):
            wrong.append(f"left in inbox: {files(home / 'inbox')}")
        if not (home / "inbox" / "old" / "keep.txt").exists():
            wrong.append("the sub-folder was touched")
    return wrong


# ---- 2. triage notes with the small model ---------------------------------------------------------------------

TRIAGE = """Write a reflex that triages the notes of the folder ~/notes/inbox. Each note is a small .txt file; file
names may hold spaces.
- For each note, the small decision model decides from the note's CONTENT whether it is a task, an idea or a
  reference (a judgment, not a rule: do not use keywords).
- Move each note into ~/notes/task/, ~/notes/idea/ or ~/notes/reference/ accordingly. Create the folders when
  missing. Never overwrite a file that is already there.
- A note the small model is unsure about must never be moved on a guess: the big model decides it.
- Return {"moved": n, "message": "..."}.
The reflex takes no input."""
NOTES = {"buy-milk.txt": ("task", "[k:task] Buy milk before Friday"), "call-bank.txt": ("task", "[k:task] Call the bank"),
         "app-idea.txt": ("idea", "[k:idea] What if the app worked offline?"),
         "paper.txt": ("reference", "[k:reference] Attention is all you need, 2017"),
         "old draft.txt": ("idea", "[k:idea] A draft about habits"), "misc.txt": (None, "hmm")}


def triage_small(question, options, items):
    """Stands for the small model: sure when the text it is given for an item holds exactly one kind marker."""
    answers = []
    for text, context in items:
        kinds = {k for k in ("task", "idea", "reference") if f"[k:{k}]" in f"{text}\n{context}"}
        answers.append((kinds.pop(), 0.95) if len(kinds) == 1 else (options[0], 0.4))
    return answers


def triage_big(asks: dict):
    about = [entry["item"] for entry in asks.get("about") or []]
    kind = lambda item: NOTES.get(os.path.basename(str(item)), (None,))[0] or "idea"  # noqa: E731
    if about:
        return {item: kind(item) for item in about}
    return "idea"


def play_triage(run, home: Path) -> list:
    wrong = []
    (home / "notes" / "inbox").mkdir(parents=True)
    for name, (_, text) in NOTES.items():
        (home / "notes" / "inbox" / name).write_text(text + "\n")
    live = run(decider=triage_small, answer=triage_big)
    if not ended(live, wrong, "live"):
        return wrong
    expect = {"task": ["buy-milk.txt", "call-bank.txt"], "idea": ["app-idea.txt", "misc.txt", "old draft.txt"],
              "reference": ["paper.txt"]}
    for kind, names in expect.items():
        if files(home / "notes" / kind) != names:
            wrong.append(f"notes/{kind}: {files(home / 'notes' / kind)} instead of {names}")
    went_up = [sorted(os.path.basename(str(e["item"])) for e in a.get("about") or []) for a in live.asked]
    if went_up != [["misc.txt"]]:
        wrong.append(f"went up to the big model: {went_up} instead of [['misc.txt']]")
    if value(live, "moved") != 6:
        wrong.append(f"result['moved'] is {value(live, 'moved')!r} instead of 6")
    return wrong


# ---- 3. a read-only report on a project -----------------------------------------------------------------------

HEALTH = """Write a reflex that reports on the project in ~/proj (a git repository). It changes nothing.
Return {"changed": n, "todos": n, "disk": n, "message": "..."} where:
- changed is the number of files that `git status --porcelain` lists in ~/proj;
- todos is the number of lines that contain TODO in the .py files of ~/proj, sub-folders included;
- disk is the percentage used of the disk that holds the home folder, as a whole number (the Use% column of df).
The reflex takes no input."""


def play_health(run, home: Path) -> list:
    wrong = []
    proj = home / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "a.py").write_text("x = 1  # TODO one\nprint(x)\n")
    (proj / "sub" / "b.py").write_text("# TODO two\n# TODO three\n")
    (proj / "notes.txt").write_text("TODO not counted\n")
    git(proj, "init", "-q")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "first")
    (proj / "a.py").write_text("x = 2  # TODO one\nprint(x)\n")
    (proj / "notes.txt").write_text("changed\n")
    (proj / "new file.md").write_text("new\n")
    live = run()
    if not ended(live, wrong, "live"):
        return wrong
    used = int(subprocess.run(["df", "-Pk", str(home)], capture_output=True, text=True).stdout.splitlines()[-1]
               .split()[4].rstrip("%"))
    for key, expected in (("changed", 3), ("todos", 3), ("disk", used)):
        if value(live, key) != expected:
            wrong.append(f"result[{key!r}] is {value(live, key)!r} instead of {expected}")
    if any(e["verb"] in ("do", "write") for e in live.effects):
        wrong.append("it changes something")
    return wrong


# ---- 4. a watch with a memory ---------------------------------------------------------------------------------

WATCH = """Write a reflex that watches the folder ~/drop for new files.
- It remembers the file names it saw at its last run in the file ~/.cache/drop-watch.json.
- It returns {"new": [...], "message": "..."}: the names of the files that were not there at the last run, sorted.
- At its very first run (no memory yet) it reports nothing new, and just remembers what is there.
- A dry run must leave the memory file as it is.
The reflex takes no input."""


def play_watch(run, home: Path) -> list:
    wrong = []
    drop, memory = home / "drop", home / ".cache" / "drop-watch.json"
    drop.mkdir()
    (drop / "a.txt").write_text("a")
    (drop / "b c.txt").write_text("b")

    def step(where, expected, **how):
        outcome = run(**how)
        if ended(outcome, wrong, where) and value(outcome, "new") != expected:
            wrong.append(f"{where}: new is {value(outcome, 'new')!r} instead of {expected}")
    step("first run", [])
    (drop / "c.txt").write_text("c")
    before = memory.read_text() if memory.exists() else None
    step("dry run", ["c.txt"], dry=True)
    if (memory.read_text() if memory.exists() else None) != before:
        wrong.append("dry run: the memory file changed")
    step("second run", ["c.txt"])
    step("third run", [])
    (drop / "a.txt").unlink()
    (drop / "d.txt").write_text("d")
    step("fourth run", ["d.txt"])
    return wrong


# ---- 5. broken links of a page --------------------------------------------------------------------------------

LINKS = """Write a reflex that finds the broken links of a web page. It changes nothing.
- Input: page, the URL of the page.
- Fetch the page and take the links of its <a href="..."> tags that start with http:// or https:// (each one once,
  30 at most).
- Check each link with a HEAD request, 5 seconds at most each.
- Return {"checked": n, "broken": [...], "message": "..."}: broken holds, sorted, the links whose status is 400 or
  more, and the links that cannot be reached at all."""


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self._answer(False)

    def do_GET(self):
        self._answer(True)

    def _answer(self, body: bool):
        port = self.server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        page = (f'<html><body><a href="{base}/ok1">one</a> <a class="x" href="{base}/ok2">two</a>\n'
                f'<a href="{base}/gone">gone</a><a href="{base}/boom">boom</a> <a href="{base}/ok1">again</a>\n'
                f'<a href="http://127.0.0.1:9/refused">refused</a> <a href="/relative">rel</a>'
                f'<a href="mailto:a@b.c">mail</a></body></html>').encode()
        status = {"/page": 200, "/ok1": 200, "/ok2": 200, "/gone": 404, "/boom": 500}.get(self.path, 404)
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(page) if self.path == "/page" else 0))
        self.end_headers()
        if body and self.path == "/page":
            self.wfile.write(page)

    def log_message(self, *args):
        pass


def play_links(run, home: Path) -> list:
    wrong = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        live = run(inputs={"page": f"{base}/page"})
    finally:
        server.shutdown()
    if not ended(live, wrong, "live"):
        return wrong
    broken = sorted([f"{base}/boom", f"{base}/gone", "http://127.0.0.1:9/refused"])
    if value(live, "broken") != broken:
        wrong.append(f"broken is {value(live, 'broken')!r} instead of {broken}")
    if value(live, "checked") != 5:
        wrong.append(f"checked is {value(live, 'checked')!r} instead of 5")
    if any(e["verb"] in ("do", "write") for e in live.effects):
        wrong.append("it changes something")
    return wrong


# ---- 6. a version bump the big model decides ------------------------------------------------------------------

BUMP = """Write a reflex that bumps the version of the project in ~/app (a git repository).
- Read ~/app/VERSION, which holds a version x.y.z.
- Ask the big model which part to bump: major, minor or patch. Give it the subjects of the last 5 commits so that
  it can decide.
- Write the new version to ~/app/VERSION (the parts after the bumped one go back to 0), and add the line
  "- <new version>" at the end of ~/app/CHANGELOG.md.
- Return {"version": "<new version>", "message": "..."}.
The reflex takes no input."""
SUBJECTS = ["Add offline mode", "Fix crash on empty note", "Add export to PDF", "Speed up search", "Update the docs"]


def bump_big(asks: dict):
    about = [entry["item"] for entry in asks.get("about") or []]
    return {item: "minor" for item in about} if len(about) > 1 else "minor"


def play_bump(run, home: Path) -> list:
    wrong = []
    app = home / "app"
    app.mkdir()
    (app / "VERSION").write_text("1.4.2\n")
    (app / "CHANGELOG.md").write_text("# Changelog\n\n- 1.4.2\n")
    git(app, "init", "-q")
    for at, subject in enumerate(SUBJECTS):
        (app / "f.txt").write_text(str(at))
        git(app, "add", "-A")
        git(app, "commit", "-q", "-m", subject)
    dry = run(dry=True, answer=bump_big)
    if ended(dry, wrong, "dry run"):
        if (app / "VERSION").read_text().strip() != "1.4.2":
            wrong.append("dry run: VERSION changed")
        if len(dry.planned) < 2:
            wrong.append(f"dry run: {len(dry.planned)} changes planned instead of 2")
    live = run(answer=bump_big)
    if not ended(live, wrong, "live"):
        return wrong
    if not live.asked:
        wrong.append("the big model was not asked")
    elif sum(subject in json.dumps(live.asked[0]) for subject in SUBJECTS) < 5:
        wrong.append("the question does not carry the 5 commit subjects")
    if (app / "VERSION").read_text().strip() != "1.5.0":
        wrong.append(f"VERSION is {(app / 'VERSION').read_text().strip()!r} instead of '1.5.0'")
    if (app / "CHANGELOG.md").read_text().rstrip().splitlines()[-1] != "- 1.5.0":
        wrong.append(f"CHANGELOG ends with {(app / 'CHANGELOG.md').read_text().rstrip().splitlines()[-1]!r}")
    if not (app / "CHANGELOG.md").read_text().startswith("# Changelog\n\n- 1.4.2"):
        wrong.append("CHANGELOG lost its earlier lines")
    if value(live, "version") != "1.5.0":
        wrong.append(f"result['version'] is {value(live, 'version')!r}")
    return wrong


# ---- 7. delete old temporary files ----------------------------------------------------------------------------

CLEAN = """Write a reflex that deletes old temporary files.
- Input: days, 7 by default.
- Delete the .tmp files directly in ~/work (not in its sub-folders) that were last modified more than `days` days ago.
- Return {"deleted": [...], "message": "..."}: the names of the deleted files, sorted. When there is nothing to
  delete, say so."""
AGES = {"old1.tmp": 10, "old2.tmp": 30, "new.tmp": 1, "keep.txt": 40, "sub/old3.tmp": 20}


def _work(home: Path):
    for name, days in AGES.items():
        path = home / "work" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        then = time.time() - days * 86400
        os.utime(path, (then, then))


def play_clean(run, home: Path) -> list:
    wrong = []
    _work(home)
    dry = run(dry=True)
    if ended(dry, wrong, "dry run"):
        if tree(home / "work") != sorted(AGES):
            wrong.append("dry run: files were deleted")
        if len(dry.planned) not in (1, 2) or not all("old" in p for p in dry.planned):
            wrong.append(f"dry run: planned {dry.planned}")
    live = run()
    if ended(live, wrong, "live"):
        if tree(home / "work") != ["keep.txt", "new.tmp", "sub/old3.tmp"]:
            wrong.append(f"after the run: {tree(home / 'work')}")
        if value(live, "deleted") != ["old1.tmp", "old2.tmp"]:
            wrong.append(f"deleted is {value(live, 'deleted')!r}")
    again = run()
    if ended(again, wrong, "second run") and value(again, "deleted") != []:
        wrong.append(f"second run: deleted is {value(again, 'deleted')!r} instead of []")
    _work(home)
    longer = run(inputs={"days": 20})
    if ended(longer, wrong, "days=20") and value(longer, "deleted") != ["old2.tmp"]:
        wrong.append(f"days=20: deleted is {value(longer, 'deleted')!r} instead of ['old2.tmp']")
    return wrong


# ---- 8. summarize a log, which may be missing -----------------------------------------------------------------

LOG = """Write a reflex that summarizes the end of a log. It changes nothing.
- Look at the last 20 lines of ~/logs/app.log. A line looks like "2026-11-21 10:00:01 ERROR db timeout".
- Return {"errors": n, "kinds": [...], "message": "..."}: errors is the number of those lines that contain ERROR,
  kinds is the distinct texts that follow "ERROR " on those lines, sorted.
- When the file does not exist, return {"errors": 0, "kinds": [], "message": "..."} with a message that says the
  log is missing.
The reflex takes no input."""


def play_log(run, home: Path) -> list:
    wrong = []
    missing = run()
    if ended(missing, wrong, "no log"):
        if value(missing, "errors") != 0 or value(missing, "kinds") != []:
            wrong.append(f"no log: {missing.result!r}")
        if "missing" not in str(value(missing, "message")).lower() and "not" not in str(value(missing, "message")).lower():
            wrong.append(f"no log: the message does not say so: {value(missing, 'message')!r}")
    lines = [f"2026-11-21 09:00:{i:02d} INFO start {i}" for i in range(9)] + ["2026-11-21 09:00:09 ERROR old failure"]
    tail = [f"2026-11-21 10:00:{i:02d} INFO tick" for i in range(20)]
    for at, text in ((2, "ERROR db timeout"), (7, "ERROR disk full"), (11, "ERROR db timeout"), (19, "ERROR db timeout")):
        tail[at] = f"2026-11-21 10:00:{at:02d} {text}"
    (home / "logs").mkdir()
    (home / "logs" / "app.log").write_text("\n".join(lines + tail) + "\n")
    live = run()
    if ended(live, wrong, "with a log"):
        if value(live, "errors") != 4:
            wrong.append(f"errors is {value(live, 'errors')!r} instead of 4")
        if value(live, "kinds") != ["db timeout", "disk full"]:
            wrong.append(f"kinds is {value(live, 'kinds')!r}")
        if any(e["verb"] in ("do", "write") for e in live.effects):
            wrong.append("it changes something")
    return wrong


TASKS = {
    "sort": (SORT, play_sort), "triage": (TRIAGE, play_triage), "health": (HEALTH, play_health),
    "watch": (WATCH, play_watch), "links": (LINKS, play_links), "bump": (BUMP, play_bump),
    "clean": (CLEAN, play_clean), "log": (LOG, play_log),
}
