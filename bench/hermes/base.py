"""The home folder every world of the Hermes bench starts from: a small project, an inbox of notes, a folder of
temporary files, a log; the three reflexes a user of that home would have; and what they ask for.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
AGES = {"old1.tmp": 10, "old2.tmp": 30, "new.tmp": 1, "keep.txt": 40, "sub/old3.tmp": 20}  # ~/work, and days old


def git(folder: Path, *words):
    subprocess.run(["git", *words], cwd=folder, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


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


REQUESTS = {
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



def build(home: Path):
    """The files every world starts from."""
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



def tmp_files(home: Path) -> list:
    return sorted(str(p.relative_to(home / "work")) for p in (home / "work").rglob("*") if p.is_file())
