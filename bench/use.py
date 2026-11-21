"""The use bench: does a model that has reflexes use them well?

    python bench/use.py make <folder>     build one throwaway world per scenario, print the prompts as JSON
    python bench/use.py check <folder>    say what each world shows afterwards

Each world is a home folder with a few reflexes installed and a `reflex` command that logs every call. A fresh
agent gets the scenario's request and the short note a host would put in its prompt (INDEX, below), and works with
its own shell. The checks read the world afterwards: which reflex calls were made, and what is on disk.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tasks import AGES, git  # noqa: E402

VENV = Path(__file__).resolve().parent.parent / ".venv" / "bin"

INDEX = """You have reflexes: small programs for tasks done before. One call does the whole task, with no thinking in between.
  reflex run <name> [input=value]    run one. It ends "done" (use its result as it is), "asks" (it needs one answer
                                     from you: `reflex answer <id> '<answer>'`, and it carries on) or "stopped" (it
                                     says why and what it already changed; do the rest yourself)
  reflex test <name> [input=value]   dry run: shows what it would change, changes nothing
  reflex guide                       how to write one
Yours:
{listing}
When a request matches a reflex, run it instead of doing the steps by hand."""

REFLEXES = {
    "project-health": '''def reflex(rx, project="~/proj"):
    """Report on a git project: changed files, TODO lines in its Python files, disk use."""
    changed = [line for line in rx.sh(["git", "-C", project, "status", "--porcelain"]).splitlines() if line]
    counts = rx.sh(["grep", "-rc", "--include=*.py", "TODO", project], ok=(0, 1)).splitlines()
    todos = sum(int(line.rsplit(":", 1)[1]) for line in counts if ":" in line)
    disk = int(rx.sh(["df", "-Pk", "~"]).splitlines()[-1].split()[4].rstrip("%"))
    return {"changed": len(changed), "todos": todos, "disk": disk,
            "message": f"{len(changed)} changed files, {todos} TODO lines, disk {disk}% used."}
''',
    "triage-notes": '''def reflex(rx):
    """Triage the notes of ~/notes/inbox into task, idea or reference, by their content."""
    names = [n for n in rx.sh(["ls", "~/notes/inbox"]).splitlines() if n.endswith(".txt")]
    if not names:
        return {"moved": 0, "message": "No note in the inbox."}
    texts = {name: rx.read(f"~/notes/inbox/{name}") for name in names}
    kinds = rx.ask("Is this note a task, an idea or a reference?", ["task", "idea", "reference"],
                   about=names, context=texts)
    for name in names:
        rx.do(["mkdir", "-p", f"~/notes/{kinds[name]}"])
        rx.do(["mv", "-n", f"~/notes/inbox/{name}", f"~/notes/{kinds[name]}/"])
    return {"moved": len(names), "kinds": kinds, "message": f"{len(names)} notes filed."}
''',
    "clean-tmp": '''import os.path


def reflex(rx, days=7):
    """Delete the .tmp files of ~/work that are older than some days."""
    found = rx.sh(["find", "~/work", "-maxdepth", "1", "-type", "f", "-name", "*.tmp", "-mtime", f"+{int(days)}"])
    paths = sorted(line for line in found.splitlines() if line)
    for path in paths:
        rx.do(["rm", path])
    names = [os.path.basename(path) for path in paths]
    return {"deleted": names, "message": f"{len(names)} files deleted." if names else "Nothing to delete."}
''',
}
NOTES = {"buy-milk.txt": ("task", "Buy milk and eggs before Friday."),
         "call-bank.txt": ("task", "Call the bank about the blocked card, they close at 5."),
         "offline.txt": ("idea", "What if the app kept working offline and synced later?"),
         "paper.txt": ("reference", "Vaswani et al., Attention Is All You Need, 2017. The transformer paper."),
         "wifi.txt": ("reference", "Home wifi: network Maison-5G, router admin page at 192.168.1.1")}

SCENARIOS = {
    "health": "How is my project in ~/proj doing: anything uncommitted, TODOs left in the code, and is the disk OK?",
    "triage": "Please triage my notes inbox (~/notes/inbox).",
    "preview": "I'd like to clear the old temp files out of ~/work, but first show me what would be deleted. "
               "Do not delete anything yet.",
    "input": "Clear the temp files in ~/work that are more than 20 days old.",
    "none": "How many lines does the file ~/proj/a.py have?",
    "write": "I look at ~/logs/app.log for errors all the time. Make that a reflex called log-errors: it should say "
             "how many of the last 20 lines contain ERROR, and which distinct error texts they hold. Try it, then "
             "tell me what it found.",
}


def make(root: Path) -> dict:
    prompts = {}
    for name, request in SCENARIOS.items():
        world = root / name
        home = world / "home"
        (home / ".reflexes").mkdir(parents=True)
        for reflex, source in REFLEXES.items():
            (home / ".reflexes" / f"{reflex}.py").write_text(source)
        _fixtures(home)
        wrapper = world / "reflex"
        wrapper.write_text(f"""#!/bin/sh
# The reflex command of this world: same home for every call, and a log of the calls.
printf '%s\\t%s\\n' "$(date +%s)" "$*" >> "{world}/calls.log"
HOME="{home}" REFLEX_HOME="{home}/.reflexes" REFLEX_PATH="{home}/.reflexes" REFLEX_COMMAND="{world}/reflex" exec "{VENV}/reflex" "$@"
""")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
        listing = os.popen(f'"{wrapper}" list').read().rstrip()
        (world / "calls.log").unlink()
        index = INDEX.format(listing="\n".join("  " + line for line in listing.splitlines()))
        prompts[name] = (f"For this task your home folder is {home} : wherever the request below says ~, it means "
                         f"that folder, and you must not touch anything outside it. The `reflex` command is not on "
                         f"your PATH here: call it as {wrapper}\n\n{index}\n\nThe request: {request}")
        (world / "prompt.md").write_text(prompts[name] + "\n")
    return prompts


def _fixtures(home: Path):
    proj = home / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "a.py").write_text("x = 1  # TODO one\nprint(x)\n")
    (proj / "sub" / "b.py").write_text("# TODO two\n# TODO three\n")
    (proj / "notes.txt").write_text("plain\n")
    git(proj, "init", "-q")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "first")
    (proj / "notes.txt").write_text("changed\n")
    (proj / "new.md").write_text("new\n")
    (home / "notes" / "inbox").mkdir(parents=True)
    for name, (_, text) in NOTES.items():
        (home / "notes" / "inbox" / name).write_text(text + "\n")
    for name, days in AGES.items():
        path = home / "work" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        then = time.time() - days * 86400
        os.utime(path, (then, then))
    lines = [f"2026-11-21 10:00:{i:02d} INFO tick" for i in range(30)]
    for at, text in ((3, "ERROR old failure"), (12, "ERROR db timeout"), (17, "ERROR disk full"), (21, "ERROR db timeout"),
                     (29, "ERROR db timeout")):
        lines[at] = f"2026-11-21 10:00:{at:02d} {text}"
    (home / "logs").mkdir()
    (home / "logs" / "app.log").write_text("\n".join(lines) + "\n")


def calls(world: Path) -> list:
    log = world / "calls.log"
    return [line.split("\t", 1)[1] for line in log.read_text().splitlines()] if log.exists() else []


def tmp_files(home: Path) -> list:
    return sorted(str(p.relative_to(home / "work")) for p in (home / "work").rglob("*") if p.is_file())


def check(root: Path) -> dict:
    report = {}
    for name in SCENARIOS:
        world, wrong = root / name, []
        home, made = world / "home", calls(world)
        runs = [c for c in made if c.startswith("run ")]
        if name == "health":
            if not any(c.startswith("run project-health") for c in made):
                wrong.append("project-health was not run")
        elif name == "triage":
            if not any(c.startswith("run triage-notes") for c in made):
                wrong.append("triage-notes was not run")
            if not any(c.startswith("answer ") for c in made):
                wrong.append("its question was not answered through the reflex")
            for note, (kind, _) in NOTES.items():
                if not (home / "notes" / kind / note).exists():
                    wrong.append(f"{note} is not in notes/{kind}")
        elif name == "preview":
            if not any(c.startswith("test clean-tmp") for c in made):
                wrong.append("no dry run of clean-tmp")
            if tmp_files(home) != sorted(AGES):
                wrong.append(f"files were deleted: {tmp_files(home)}")
        elif name == "input":
            if not any(c.startswith("run clean-tmp") and "days=20" in c for c in made):
                wrong.append(f"clean-tmp was not run with days=20: {runs}")
            if tmp_files(home) != ["keep.txt", "new.tmp", "old1.tmp", "sub/old3.tmp"]:
                wrong.append(f"after: {tmp_files(home)}")
        elif name == "none":
            if runs:
                wrong.append(f"a reflex was run for a request none fits: {runs}")
        elif name == "write":
            path = home / ".reflexes" / "log-errors.py"
            if not path.exists():
                wrong.append("no ~/.reflexes/log-errors.py")
            if not any(c.startswith(("run log-errors", "test log-errors")) for c in made):
                wrong.append("the new reflex was never run")
        report[name] = {"ok": not wrong, "wrong": wrong, "calls": made}
    return report


if __name__ == "__main__":
    command, root = sys.argv[1], Path(sys.argv[2]).resolve()
    if command == "make":
        root.mkdir(parents=True, exist_ok=True)
        print(json.dumps(make(root), indent=1))
    else:
        for name, result in check(root).items():
            print(f"{'ok  ' if result['ok'] else 'FAIL'} {name:8} {len(result['calls'])} reflex calls: "
                  f"{' | '.join(result['calls'])[:150]}")
            for line in result["wrong"]:
                print(f"     {line}")
