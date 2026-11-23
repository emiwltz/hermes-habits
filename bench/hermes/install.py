#!/usr/bin/env python3
"""The first minute: install the plugin the way a user does, and see what their agent then has.

    python bench/hermes/install.py <out> [--hermes /path/to/hermes]

In a brand-new home and a brand-new Hermes home under <out>:

  1. `hermes plugins install <this repository> --enable`, with the answer to the question about dependencies given
     up front. Hermes scans the plugin first and blocks one with a finding it rates high: this is where that shows.
  2. One session with the fake model, which runs `reflex list` and then a reflex in the agent's terminal.

It then says whether the install went through, whether the user was given the first reflexes, whether the agent's
prompt names them, and whether the `reflex` its terminal finds is the one Hermes installed. What is installed is
what is committed: commit first. Hermes builds an environment for the plugin's dependency: several minutes and
some hundreds of megabytes under <out>, the first time.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out")
    parser.add_argument("--hermes", default=shutil.which("hermes") or "hermes")
    given = parser.parse_args()
    world = Path(given.out).resolve()
    if world.exists():
        shutil.rmtree(world)
    for folder in ("home", "hermes", "bin"):
        (world / folder).mkdir(parents=True)
    env = {**{k: v for k, v in __import__("os").environ.items() if k in run.PASSED or k == "PATH"},
           "HOME": str(world / "home"), "HERMES_HOME": str(world / "hermes"), "COLUMNS": "300", "NO_COLOR": "1"}
    done = subprocess.run([given.hermes, "plugins", "install", f"file://{run.ROOT}", "--enable", "--yes-deps"],
                          env=env, cwd=world, capture_output=True, text=True)
    said = done.stdout + done.stderr
    (world / "install.log").write_text(said)
    high = sorted({line.strip() for line in said.splitlines() if line.strip().startswith(("HIGH", "CRITICAL"))})
    checks = [("the install went through", done.returncode == 0 and "BLOCKED" not in said,
               "; ".join(high) or said.strip()[-300:])]

    options = argparse.Namespace(model="mock", provider="custom", base_url="", login="", profile="", crowd=0, seen=0,
                                 writer="", keys="", hermes=given.hermes, seconds=300, unconfined=False, versions={})
    options.reads = run.needed(options, world / "aside")
    shutil.copytree(HERE / "observer", world / "hermes" / "plugins" / "reflex_bench_observer")
    (world / "hermes" / "config.yaml").write_text(json.dumps(
        {"plugins": {"enabled": ["habits", "reflex_bench_observer"]}, "agent": {"max_turns": 10},
         "security": {"allow_lazy_installs": False}, "model": {"provider": "custom", "default": "mock-1"}}))
    run.script = lambda name, arm, at: [{"calls": [{"name": "terminal", "arguments": {"command": command}}]}
                                        for command in ("command -v reflex; reflex list", "reflex run disk-space")
                                        ] + [{"say": "The disk is fine."}]
    session = run.session(world, "health", "plugin", 0, "Is the disk OK?", options)
    home = world / "home" / ".reflexes"
    given_ = sorted(p.stem for p in home.glob("*.py")) if home.is_dir() else []
    asked = [json.loads(line) for line in (world / "model.jsonl").read_text().splitlines()] \
        if (world / "model.jsonl").exists() else []
    prompt = max(asked, key=lambda entry: entry["system_chars"], default={})
    try:
        store = sqlite3.connect(f"file:{world / 'hermes' / 'state.db'}?mode=ro", uri=True)
        results = [str(row[0]) for row in store.execute("select content from messages where role = 'tool' order by id")]
    except sqlite3.Error:
        results = []
    checks += [
        ("a new user is given the first reflexes", given_ == ["disk-space", "hermes-health"], str(given_)),
        ("the agent's prompt names them", "hermes-health(" in json.dumps(prompt.get("reflex_in_system", "")), ""),
        ("its terminal finds the `reflex` Hermes installed",
         bool(results) and str(world / "hermes") in results[0] and "disk-space()" in results[0], (results or [""])[0][:300]),
        ("a reflex runs there", len(results) > 1 and "done:" in results[1], (results[1:] or [session["answer"]])[0][:300]),
    ]
    for what, good, more in checks:
        print(f"{'ok  ' if good else 'FAIL'} {what}" + ("" if good or not more else f"\n     {more}"))
    sys.exit(0 if all(good for _, good, _ in checks) else 1)


if __name__ == "__main__":
    main()
