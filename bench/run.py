"""Run candidate reflexes against the bench's tasks.

    python bench/run.py <task> <file.py> [<file.py> ...]     one task, some candidates
    python bench/run.py --all <folder> [--json out.json]      <folder>/<guide>/<task>/<model>-<n>.py

Each candidate runs for real in a throwaway home: its commands are confined to that home, its questions are answered
by the task's stand-ins for the small and the big model. Prints one line per candidate, then a table.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reflexes import engine, readers  # noqa: E402
from reflexes.host import Local  # noqa: E402
from tasks import GIT_ENV, TASKS  # noqa: E402


class BenchHost(Local):
    """The local host, kept inside one folder: a command that changes something may not name a path outside it."""

    def run(self, argv, seconds, input=None):
        if readers.why_not(argv):
            outside = [w for w in argv[1:] if w.startswith("/") and not w.startswith(self.home + "/")
                       and w not in (self.home, "/dev/null")]
            if outside or argv[0] in ("sudo", "su"):
                return "", f"the bench refuses a change outside its home: {' '.join(argv)[:120]}", 126
        return super().run(argv, seconds, input)

    def write(self, path, text, append=False):
        full = os.path.realpath(os.path.join(self.cwd, path))
        if not full.startswith(os.path.realpath(self.home) + "/"):
            raise PermissionError(13, "the bench refuses a write outside its home")
        super().write(path, text, append)


def judge(task: str, source: str) -> list:
    """What goes wrong when this source plays the task; [] when it works."""
    statement, play = TASKS[task]
    home = Path(tempfile.mkdtemp(prefix=f"bench-{task}-"))
    host = BenchHost(cwd=str(home), home=str(home),
                     env={**GIT_ENV, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})

    def run(inputs=None, dry=False, decider=None, answer=None):
        outcome = engine.run(source, inputs or {}, host, dry=dry, decider=decider, limits={"seconds": 60})
        asked = []
        while outcome.status == "asks" and answer is not None and len(asked) < 6:
            asked.append(outcome.asks)
            outcome = engine.resume(outcome.state, answer(outcome.asks), host, decider=decider)
        outcome.asked = asked
        return outcome
    try:
        engine.describe(source)
    except engine.Refused as exc:
        return [f"not a reflex: {exc}"]
    try:
        return play(run, home)
    except Exception as exc:  # a checker that trips on an unexpected result says so, it does not crash the bench
        return [f"the check itself failed: {type(exc).__name__}: {exc}"]
    finally:
        shutil.rmtree(home, ignore_errors=True)


def verbs(source: str) -> list:
    return sorted(set(re.findall(r"\brx\.(\w+)\(", source)))


def main(argv: list) -> int:
    out = argv[argv.index("--json") + 1] if "--json" in argv else None
    if argv and argv[0] == "--all":
        root = Path(argv[1])
        rows = []
        for path in sorted(root.glob("*/*/*.py")):
            guide, task, model = path.parts[-3], path.parts[-2], path.stem.rsplit("-", 1)[0]
            source = path.read_text()
            wrong = judge(task, source)
            rows.append({"guide": guide, "task": task, "model": model, "file": str(path), "ok": not wrong,
                         "wrong": wrong, "lines": len([l for l in source.splitlines() if l.strip()]),
                         "verbs": verbs(source)})
            print(f"{'ok  ' if not wrong else 'FAIL'} {guide:8} {task:7} {path.stem:12} {'; '.join(wrong)[:150]}")
        table(rows)
        if out:
            Path(out).write_text(json.dumps(rows, indent=1))
        return 0
    task, failed = argv[0], 0
    for name in argv[1:]:
        wrong = judge(task, Path(name).read_text())
        failed += bool(wrong)
        print(f"{'ok  ' if not wrong else 'FAIL'} {name}")
        for line in wrong:
            print(f"     {line}")
    return 1 if failed else 0


def table(rows: list):
    for key in ("guide", "model", "task"):
        print(f"\nby {key}:")
        groups = defaultdict(list)
        for row in rows:
            groups[row[key]].append(row["ok"])
        for name, oks in sorted(groups.items()):
            print(f"  {name:10} {sum(oks):3}/{len(oks):<3} {100 * sum(oks) / len(oks):5.1f} %")
    print("\nby guide and model:")
    groups = defaultdict(list)
    for row in rows:
        groups[(row["guide"], row["model"])].append(row["ok"])
    for (guide, model), oks in sorted(groups.items()):
        print(f"  {guide:8} {model:8} {sum(oks):3}/{len(oks):<3} {100 * sum(oks) / len(oks):5.1f} %")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
