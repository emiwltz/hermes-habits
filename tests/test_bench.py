"""bench/run.py: what a run should give, the summary's maths, the budget, and whole cases with a fake hermes."""

import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import test_plugin as plugin_tests
from test_plugin import setup  # noqa: F401  (a fixture: the plugin with a fake Hermes)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bench_run", ROOT / "bench" / "run.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

MOVE = "mkdir -p /s/inbox/document && mv -n -- /s/inbox/invoice.pdf /s/inbox/document/"
OUT = {"status": "end", "message": "Downloads sorted.", "ms": 120, "failed": False,
       "values": {"kind": {"invoice.pdf": "document"}, "n": 3, "name": "a.pdf"}, "planned_count": 2,
       "planned": [{"node": "sort", "tool": "terminal", "args": {"command": MOVE}},
                   {"node": "x", "tool": "terminal", "args": {"command": "touch /s/x"}}]}


@pytest.mark.parametrize("expect, out, why", [
    ({"end": {}}, {}, []),
    ({"end": {}, "wake": {}}, {}, []),
    ({"wake": {}}, {}, ["status end, expected wake"]),
    ({"end": {"message_contains": "sorted"}}, {}, []),
    ({"end": {"message_contains": ["sorted", "trash"]}}, {}, ["message lacks 'trash'"]),
    ({"end": {"message_regex": r"^Downloads .*\.$"}}, {}, []),
    ({"end": {"message_regex": "^sorted"}}, {}, ["message does not match '^sorted'"]),
    ({"end": {"values": {"n": 3, "name": "a.pdf", "kind": "re:document"}}}, {}, []),
    ({"end": {"values": {"n": "3"}}}, {}, []),  # a text is compared with the value's text
    ({"end": {"values": {"name": "re:^b"}}}, {}, ["value name is a.pdf, expected re:^b"]),
    ({"end": {"values": {"gone": 1}}}, {}, ["no value gone"]),
    ({"end": {"values": {"n": 3}}}, {"values": '{"n": 3, "kind": {"f0.pdf": "doc'}, ["values are no mapping (an "
                                                "older hermes habits run cuts them past 6,000 characters)"]),
    ({"end": {"planned_tools": ["terminal"]}}, {}, []),
    ({"end": {"planned_tools": ["terminal", "send_message"]}}, {},
     ["planned tools ['terminal'], expected ['send_message', 'terminal']"]),
    ({"end": {"planned_tools": []}}, {}, ["planned tools ['terminal'], expected []"]),
    ({"max_ms": 200, "end": {}}, {}, []),
    ({"max_ms": 100, "end": {}}, {}, ["took 120 ms, more than 100"]),
    # What was planned, call by call: a move to the wrong folder is a wrong action, not a right run.
    ({"end": {"planned": [r"^terminal .*mv .*invoice\.pdf .*/document/$"], "planned_count": 2}}, {}, []),
    ({"end": {"planned": [r"invoice\.pdf .*/image/"]}}, {}, [r"no planned call matches 'invoice\\.pdf .*/image/'"]),
    ({"end": {"planned_count": 1}}, {}, ["2 planned calls, expected 1"]),
    ({"end": {"planned": ["x"]}}, {"planned_count": 60}, ["2 of 60 planned calls shown (an older hermes habits run "
                                                          "cuts them at 50)"]),
    # The expected result, or a clean hand-back: each status has its own checks.
    ({"end": {"message_contains": "sorted"}, "wake": {}}, {"status": "wake", "message": "2 unsure files"}, []),
    ({"end": {}, "wake": {"message_regex": "unsure"}}, {"status": "wake", "message": "2 unsure files"}, []),
    ({"end": {}, "wake": {}}, {"status": "wake", "failed": True}, ["a step failed: no clean hand-back"]),
    ({"wake": {"failed": True}}, {"status": "wake", "failed": True, "message": "x"}, []),
    ({"none": {}}, {"status": "none", "planned": [], "planned_count": 0}, []),
    ({"none": {}}, {"status": "other", "trigger": {"fired": "photo-arsorter"}},
     ["status other (the trigger runs photo-arsorter), expected none"]),
    ({"none": {}, "other": {}}, {"status": "other", "planned": [], "planned_count": 0}, []),  # another habit took it
    # planned_tools is a set: order and repeats do not count.
    ({"end": {"planned_tools": ["terminal", "terminal"]}}, {}, []),
    ({"end": {"planned_tools": ["terminal", "send_message"]}},
     {"planned": [{"tool": "send_message", "args": {}}, {"tool": "terminal", "args": {}}], "planned_count": 2}, []),
    # A value's text read the same whether or not its JSON escapes what is not ASCII (a tool's reply often does).
    ({"end": {"values": {"chosen": 're:AI workshop - Café\\.md"'}}},
     {"values": {"chosen": '{"path": "1. Projects/AI workshop - Caf\\u00e9.md"}'}}, []),
    ({"end": {"values": {"chosen": "re:^(?!.*café idea)"}}}, {"values": {"chosen": "[\"caf\\u00e9 idea.md\"]"}},
     ['value chosen is ["café idea.md"], expected re:^(?!.*café idea)']),
    ({"end": {"values": {"state": '{"t1": "naïve"}'}}}, {"values": {"state": {"t1": "naïve"}}}, []),
    ({"end": {"values": {"state": '{"t1": "naïve"}'}}}, {"values": {"state": '{"t1": "na\\u00efve"}'}}, []),
    # The doubts a run left, each read "<node> <item>" (a bench run: an unsure relay answer drops out of the value).
    ({"wake": {"doubted": ["^handoff t1$"]}}, {"status": "wake", "doubted": [{"node": "handoff", "item": "t1"}]}, []),
    ({"wake": {"doubted": ["^handoff t1$"]}}, {"status": "wake", "doubted": [{"node": "handoff", "item": "t3"}]},
     ["no doubt matches '^handoff t1$'"]),
    ({"wake": {"doubted": ["^handoff t1$"]}}, {"status": "wake"}, ["no doubt matches '^handoff t1$'"]),
    # A status may take several forms: one right is enough, and the one that misses least is said.
    ({"wake": [{"values": {"n": 3}}, {"values": {"n": 4}, "doubted": ["t1"]}]}, {"status": "wake"}, []),
    ({"wake": [{"values": {"n": 4}}, {"values": {"n": 3}, "doubted": ["t1"]}]},
     {"status": "wake", "doubted": [{"node": "handoff", "item": "t1"}]}, []),
    ({"wake": [{"values": {"n": 4}}, {"values": {"n": 5}, "doubted": ["t1"]}]}, {"status": "wake"},
     ["value n is 3, expected 4"]),
    ({"end": [{"planned_tools": []}, {"planned_count": 2}], "max_ms": 100}, {}, ["took 120 ms, more than 100"]),
])
def test_what_a_run_misses_of_what_its_case_expects(expect, out, why):
    assert bench.matches(expect, {**OUT, **out}) == why


def test_a_case_as_written_is_checked_before_anything_runs():
    good = {"id": "a", "task": "t", "habit": "h", "expect": {"end": {}}}
    assert bench.problems(good) == []
    assert bench.problems({**good, "expect": {}}) == [
        "expect names end, wake or none, the statuses that count as right (wake: {} takes any clean hand-back)"]
    assert bench.problems({**good, "expect": {"status": "end"}}) == [
        "unknown expect key status",
        "expect names end, wake or none, the statuses that count as right (wake: {} takes any clean hand-back)"]
    assert bench.problems({**good, "expect": {"end": {"message": "x", "planned": "mv"}}}) == [
        "unknown expect.end key message",
        "expect.end.planned is a list of patterns, each matched by one planned call at least"]
    assert bench.problems({**good, "expect": {"end": {"planned": ["(mv"]}}})[0].startswith(
        "expect.end.planned: '(mv' is no pattern")
    assert bench.problems({**good, "expect": {"none": {}}}) == ["expect.none needs a message: only a trigger leaves "
                                                                "a habit unrun"]
    assert bench.problems({**good, "message": "sort", "expect": {"none": None}}) == []
    assert bench.problems({**good, "setup": "touch x"}) == ["setup, rewrite, verify and note need a scratch folder"]
    assert bench.problems({**good, "mode": "live"}) == [
        "mode: live needs a scratch folder: a live run writes only there (hermes habits run --within)"]
    assert bench.problems({**good, "repeat": 0, "mode": "shadow"}) == [
        "mode is dry or live", "repeat is a whole number, 1 or more"]
    assert bench.problems({**good, "baseline": {"repeat": 2}, "create": {"prompt": "p"}}) == [
        "baseline.prompt is required (text)", "create.name is required (text)"]
    assert bench.problems({**good, "create": {"prompt": "p", "name": "../x"}}) == [
        "create.name uses a-z, 0-9, _ or -, 40 characters at most (a habit's name)"]
    assert bench.problems({**good, "baseline": {"prompt": "p", "expect": {"status": "end"}}}) == [
        "baseline.expect takes message_contains and message_regex, not status"]
    assert bench.problems({"id": "a"})[:2] == ["task is required (text)", "habit is required (text)"]
    # A layer-2 route is the case's own note; other is right only from a message, like none.
    assert bench.problems({**good, "route": "habit", "message": "m", "expect": {"other": {}, "none": {}}}) == []
    assert bench.problems({**good, "expect": {"other": {}}}) == [
        "expect.other needs a message: only a trigger gives another habit the turn"]
    assert bench.problems({**good, "memory": "{scratch}/m.json"}) == ["memory needs a scratch folder: the file is "
                                                                      "written there"]
    assert bench.problems({**good, "forbid": "ob"}) == ["forbid is a list of commands or tools a model turn must not "
                                                        "run (rsync, ssh, hermes send, send_message)"]
    assert bench.problems({**good, "forbid": ["ob", "hermes send"], "scratch": "/tmp/x", "memory": "m.json"}) == []
    assert bench.problems({**good, "expect": {"end": [{"planned_tools": []}, None, {"doubted": ["t1"]}]}}) == []
    assert bench.problems({**good, "expect": {"end": [], "wake": [{"x": 1}, 3]}}) == [
        "expect.end is a mapping of checks, or a list of them (one right is enough)",
        "unknown expect.wake key x", "expect.wake is a mapping of checks, or a list of them (one right is enough)"]
    assert bench.problems({**good, "expect": {"end": {"doubted": "t1"}}}) == [
        "expect.end.doubted is a list of patterns, each matched by one doubt (\"<node> <item>\")"]


@pytest.mark.parametrize("command, item, ran", [
    ("ob sync --path ~/Documents/vault", "ob", True),
    ("cd ~/Documents/vault && (ob sync || (sleep 20 && ob sync)) 2>&1 | grep -F 'Fully synced'", "ob", True),
    ("/opt/homebrew/bin/ob sync", "ob", True),
    ("FOO=1 timeout 30 ob sync", "ob", True),
    ("bash -c 'cd x; ob sync'", "ob", True),
    ("echo $(ob sync-status)", "ob", True),
    ("ssh -o BatchMode=yes -p 22 server 'ob sync'", "ob", True),
    ("{s}/bin/ob sync --path {s}/home/Documents/vault", "ob", False),  # the case's own stand-in
    ("sh {s}/fake-ob.sh", "ob", False),
    ("grep -c ob notes.txt > ob", "ob", False),  # an argument, a redirection's file
    ("echo 'ob sync'", "ob", False),
    ("ssh -o BatchMode=yes server hc ls", "ssh", True),
    ("hermes send -t telegram MEDIA:/x.md", "hermes send", True),
    ("~/.local/bin/hermes send -t telegram x", "hermes send", True),
    ("hermes habits run vault-save --json", "hermes send", False),
    ("cat 'unbalanced", "ob", False),
    ("ob 'unbalanced", "ob", True),  # shlex cannot read it: searched as text
    ("$HOME/.local/bin/hermes send -t telegram MEDIA:/x.md", "hermes send", True),  # never the case's stand-in
    ("${{HOME}}/.local/bin/ob sync", "ob", True),
    # Asking a command for its help or its list runs nothing (a bench run counted a send it never made).
    ("hermes send --help 2>&1 | head -60; echo '---'; ls -d ~/Documents/vault 2>&1", "hermes send", False),
    ("hermes send --list", "hermes send", False), ("ob -h", "ob", False), ("ob sync --help", "ob", False),
    ("hermes send -t telegram hi", "hermes send", True),
    # Only right after the command (or its subcommand): a -h further on is an argument. ssh's own words after the
    # host are the remote command (a bench case forbids ssh, the Pi's way to the Mac).
    ("ssh laptop df -h", "ssh", True), ("ssh server ls -h ~/Documents/vault", "ssh", True),
    ("ssh -o BatchMode=yes server 'ls -h' --help", "ssh", True), ("ob sync --path ~/x --list-files -h", "ob", True),
    # A path through a shell variable the line sets is the case's own stand-in when the variable points there.
    ("T={s}\nV=$T/vault\necho '=== sync ==='; \"$T/bin/ob\" sync --path \"$V\"; echo \"exit=$?\"", "ob", False),
    ("export T={s}; ${{T}}/bin/ob sync", "ob", False), ("T=/opt; $T/bin/ob sync", "ob", True),
    ("T={s} $T/bin/ob sync", "ob", True),  # set for that command only: the shell expanded $T before
])
def test_a_forbidden_command_is_found_where_it_runs(tmp_path, command, item, ran):
    scratch = tmp_path / "scratch"
    assert bench.runs(command.format(s=scratch), item, scratch) is ran
    calls = [("terminal", {"command": command.format(s=scratch)}), ("send_message", {"text": "x"})]
    assert bool(bench.forbidden(calls, [item], scratch)) is ran
    assert bench.forbidden(calls, ["send_message"], scratch) == ["it called send_message, which the case forbids"]


def test_a_relative_habit_path_is_read_from_the_scenario_files_folder(tmp_path, monkeypatch):
    (tmp_path / "habits" / "h" / "assets").mkdir(parents=True)
    habit = tmp_path / "habits" / "h" / "assets" / "reflex.yml"
    habit.write_text("spec: habits/0.1\n")
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "h.json").write_text(json.dumps([
        {"id": "a", "task": "t", "habit": "../habits/h/assets/reflex.yml", "expect": {"end": {}}}]))
    monkeypatch.chdir(tmp_path)
    case, = bench.load(Path("scenarios/h.json"))  # a relative scenario path, as from a tasks folder
    ready = bench.prepare(case, tmp_path)
    assert ready["_target"] == str(habit) and Path(ready["_target"]).is_absolute()


def test_two_files_of_a_folder_may_name_a_case_alike(tmp_path):
    for name in ("watch", "evening"):
        (tmp_path / f"{name}.json").write_text(json.dumps([
            {"id": "ambiguous", "task": name, "habit": "h.yaml", "expect": {"end": {}}},
            {"id": f"{name}-alone", "task": name, "habit": "h.yaml", "expect": {"end": {}}}]))
    assert [c["id"] for c in bench.load(tmp_path)] == ["evening:ambiguous", "evening-alone", "watch:ambiguous", "watch-alone"]
    (tmp_path / "watch.json").write_text(json.dumps([{"id": "x", "task": "t", "habit": "h.yaml", "expect": {}},
                                                      {"id": "x", "task": "t", "habit": "h.yaml",
                                                       "expect": {"end": {}}}]))
    found = []
    assert [c["id"] for c in bench.load(tmp_path, found)] == ["ambiguous", "evening-alone"]  # the wrong file is left out
    assert found == [f"{tmp_path / 'watch.json'}: case x: expect names end, wake or none, the statuses that count "
                     "as right (wake: {} takes any clean hand-back)", f"{tmp_path / 'watch.json'}: two cases are "
                                                                       "named x"]
    with pytest.raises(bench.BenchError, match="two cases are named x"):
        bench.load(tmp_path)


def test_a_bench_folder_copied_alone_says_it_needs_the_whole_checkout(tmp_path, monkeypatch):
    # bench/ without the plugin next to it once read every scenario as JSON and said only "JSONDecodeError".
    (tmp_path / "demo.yaml").write_text("cases:\n- id: x\n  habit: x.yaml\n", encoding="utf-8")
    monkeypatch.setattr(bench, "REPO", tmp_path / "alone")
    with pytest.raises(bench.BenchError, match="whole checkout"):
        bench.load(tmp_path)


def test_the_summary_charges_a_run_that_did_not_do_the_task_with_a_hermes_turn():
    # A hand-back leaves the task to the model: its time and calls are a Hermes turn's, on top of the habit's own.
    records = [
        {"case": "c", "task": "t", "kind": "run", "ok": True, "status": "end", "ms": 100, "wall_ms": 2000,
         "model_calls": 0},
        {"case": "c", "task": "t", "kind": "run", "ok": True, "status": "end", "ms": 300, "wall_ms": 2400,
         "model_calls": 0},
        {"case": "c", "task": "t", "kind": "run", "ok": True, "status": "wake", "ms": 200, "wall_ms": 2200,
         "model_calls": 1},
        {"case": "c", "task": "t", "kind": "run", "ok": False, "why": ["setup failed"]},  # never started
        {"case": "c", "task": "t", "kind": "baseline", "ok": True, "session": "s1", "seconds": 10, "tokens": 1000,
         "api_calls": 3, "cost": 0.002},
        {"case": "c", "task": "t", "kind": "baseline", "ok": True, "session": "s2", "seconds": 20, "tokens": 3000,
         "api_calls": 5, "cost": 0.004},
        {"case": "c", "task": "t", "kind": "baseline", "ok": False, "why": ["no session"]},  # not a measure
        {"case": "c", "task": "t", "kind": "baseline", "ok": False, "why": ["hermes -z exit 1: provider error"],
         "session": "s3", "seconds": 1.5, "api_calls": 1, "tokens": 9000},  # a failed turn is no measure either
        {"case": "c", "task": "t", "kind": "create", "ok": True, "valid": True, "first_try": False, "works": True},
        {"case": "c", "task": "t", "kind": "create", "ok": True, "valid": True, "first_try": True, "works": False},
        {"case": "d", "task": "u", "kind": "run", "ok": False, "why": ["no JSON"]},
    ]
    c, d = bench.summary(records)
    assert (c["runs"], c["right"], c["done"], c["wakes"], c["ms"], c["wall_ms"]) == (4, 3, 2, 1, 200, 2200)
    assert (c["baselines"], c["base_s"], c["base_tokens"], c["base_calls"]) == (2, 15, 2000, 4)
    assert c["task_s"] == pytest.approx((2.0 + 2.4 + 2.2 + 15) / 3)  # the hand-back costs a whole Hermes turn more
    assert c["speedup"] == pytest.approx(15 / c["task_s"]) and c["engine"] == pytest.approx(75)
    assert c["avoided"] == pytest.approx(1 - ((0 + 0 + 1 + 4) / 3) / 4)
    assert c["base_cost"] == pytest.approx(0.003)
    assert (c["created"], c["valid"], c["first_try"], c["works"]) == (2, 2, 1, 1)
    assert (d["runs"], d["right"], d["done"], d["ms"], d["speedup"], d["avoided"], d["created"]) == (
        1, 0, 0, None, None, None, 0)
    text = bench.table([c, d])
    assert text.splitlines()[0].split() == ["case", "right", "done", "habit", "ms", "wall", "ms", "Hermes", "s",
                                            "tokens", "speedup", "engine", "calls", "avoided", "created"]
    assert "3/4 75%" in text and "2/4, 1 woke" in text and "2.1x" in text and "75x" in text and "58%" in text
    assert "2/2 valid, 1 first try, 1 work" in text and text.splitlines()[2].split()[:4] == ["d", "0/1", "0%", "0/1"]


def test_the_smoke_numbers_seen_like_for_like():
    # The build's smoke: three runs that all handed back two .dmg, one baseline of 6.38 s and 3 API calls. It said
    # 18x and 100% avoided; no run did the task, so the model still had all of it to do.
    runs = [{"case": "tidy", "task": "t", "kind": "run", "ok": False, "status": "wake", "ms": ms, "wall_ms": wall,
             "model_calls": 0} for ms, wall in ((355, 1824), (342, 1790), (463, 1900))]
    base = {"case": "tidy", "task": "t", "kind": "baseline", "ok": True, "session": "s", "seconds": 6.38,
            "api_calls": 3, "tokens": 54300}
    row, = bench.summary(runs + [base])
    assert row["done"] == 0 and row["speedup"] < 1 and row["avoided"] == 0 and row["engine"] is None
    # Had they done it, the like-for-like number is wall to wall, Hermes's start in both: 3.5x, not 18x.
    row, = bench.summary([{**r, "ok": True, "status": "end"} for r in runs] + [base])
    assert row["speedup"] == pytest.approx(6.38 / ((1.824 + 1.790 + 1.900) / 3)) and 3.4 < row["speedup"] < 3.6
    assert 13 < row["engine"] < 19 and row["avoided"] == 1


def test_the_budget_stops_before_the_turn_that_would_pass_it():
    budget = bench.Budget(0.02)
    assert budget.next() == bench.FIRST_CALL and budget.allows()
    budget.add(0.004)
    assert budget.next() == 0.004 and budget.allows()
    budget.add(0.015)  # 0.019 spent, the next is counted at the dearest so far
    assert budget.next() == 0.015 and not budget.allows()
    assert budget.allows(0) and not budget.allows(bench.MODEL_CALL)  # a run that calls no model is free
    budget.charge(3)
    assert budget.calls == 3 and budget.spent == pytest.approx(0.019 + 3 * bench.MODEL_CALL)
    assert not bench.Budget(0.005).allows() and bench.Budget(None).allows()
    assert bench.price({"estimated_cost_usd": 0.003, "cost_status": "estimated", "input_tokens": 10 ** 6}) == 0.003
    assert bench.price({"estimated_cost_usd": None, "input_tokens": 600_000, "cache_read_tokens": 400_000,
                        "output_tokens": 1_000_000}) == pytest.approx(0.28 + 0.42)
    # A real state.db row: cost_status unknown, 0.0 written, 138,832 tokens in. Not free.
    unknown = {"estimated_cost_usd": 0.0, "cost_status": "unknown", "input_tokens": 138_832, "output_tokens": 2_003}
    assert bench.price(unknown) == pytest.approx(138_832 * 0.28e-6 + 2_003 * 0.42e-6)
    assert bench.price({**unknown, "cost_status": "estimated"}) == bench.price(unknown)  # 0 with tokens is no estimate
    assert bench.price({**unknown, "cost_status": "unknown", "estimated_cost_usd": 0.5}) == bench.price(unknown)
    assert bench.price({"estimated_cost_usd": 0.0, "cost_status": "estimated"}) == 0.0


FAKE = r'''#!PYTHON
"""A fake hermes: habits run/show/check/retire, and -z writing a session to state.db like Hermes 0.23. A run's
JSON takes run.json, a turn's tool calls and costs turn.json (both in its folder)."""
import json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path
home = Path(os.environ["FAKE_HERMES_HOME"])
argv = sys.argv[1:]
off = home / "plugin-data" / "habits" / "off"
paused = home / "plugin-data" / "habits" / "paused"
# What a process list shows of the bench, as a model turn would see it with ps.
parent = subprocess.run(["ps", "-o", "command=", "-p", str(os.getppid())], capture_output=True,
                        text=True).stdout.strip() if argv[:1] == ["-z"] else None
ran = Path(argv[2]) if argv[:2] == ["habits", "run"] else None  # the file a run read, and the SKILL.md beside it
# What a model turn finds in its temp folder ($TMPDIR, where Hermes's own tools write theirs).
import tempfile
temp = sorted(str(p) for p in Path(tempfile.gettempdir()).rglob("*") if p.name in ("reflex.yml", "SKILL.md")) \
    if argv[:1] == ["-z"] and os.environ.get("FAKE_SCAN_TMP") else None
# And around its root: each folder's names from there up to the home folder, and what its root holds.
here = Path.cwd()
around = {str(f): sorted(os.listdir(f)) for f in [here, *here.parents] if f == Path.home() or Path.home() in f.parents} \
    if argv[:1] == ["-z"] else None
inside = sorted(p.name for p in here.rglob("*")) if argv[:1] == ["-z"] else None
# What lsof tells of the process that started the turn: its folder and where its output goes.
seen = subprocess.run(["lsof", "-a", "-p", str(os.getppid()), "-d", "cwd,1,2", "-Fn"], capture_output=True,
                      text=True).stdout if argv[:1] == ["-z"] and os.environ.get("FAKE_LSOF") else None
skill = ran.parent.parent / "SKILL.md" if ran else None
with open(home / "calls.jsonl", "a") as log:
    log.write(json.dumps({"argv": argv, "off_env": os.environ.get("HABITS_OFF"), "off_file": off.exists(),
                          "paused": paused.read_text() if paused.exists() else None,
                          "cwd": os.getcwd(), "tz": os.environ.get("TZ"), "parent": parent, "temp": temp,
                          "around": around, "inside": inside, "lsof": seen,
                          "text": ran.read_text() if ran else None,
                          "peek": Path("peek").read_text() if ran and Path("peek").is_file() else None,
                          "skill": skill.read_text() if skill and skill.is_file() else None,
                          # The other files of its assets/: their bytes as text, and whether each may run.
                          "assets": {p.name: [p.read_bytes().decode("latin-1"), os.access(p, os.X_OK)]
                                     for p in sorted(ran.parent.iterdir()) if p.is_file() and p.name != "reflex.yml"}
                          if ran and ran.is_file() else None}) + "\n")


def config(name):
    path = home / name  # {cwd} is the folder the call runs in: a model turn's fresh root
    return json.loads(path.read_text().replace("{cwd}", os.getcwd())) if path.exists() else {}


def habit(name):
    for folder in (home / "skills" / name, Path("REPO") / "examples" / name):
        if (folder / "assets" / "reflex.yml").is_file():
            return folder / "assets" / "reflex.yml"


if argv[:2] == ["habits", "run"]:
    Path(argv[2]).read_text()
    time.sleep(config("run.json").get("plan_ms", 0) / 1000)  # a live run's dry pass first
    out = {"habit": "sort", "status": "end", "message": "Downloads sorted.", "ms": 50, "values": {},
           "failed": False, "planned": [{"node": "sort", "tool": "terminal", "args": {"command": "mv -n a b/"}}],
           "planned_count": 1, "counts": {"steps": 9, "tools": 2, "questions": 4, "llm": 0}, "backends": ["jev"],
           "model_calls": 0, "version": "v1", "trigger": None}
    out.update(config("run.json"))
    print("a notice Hermes printed first")
    print(json.dumps(out))
elif argv[:2] == ["habits", "show"]:
    if (home / "show-fails").exists():
        print("habits: OperationalError: database is locked")
        sys.exit(1)
    path = habit(argv[2])
    if path:
        print(json.dumps({"habit": argv[2], "path": str(path), "kind": "one-off",
                          "uses": sorted(set(re.findall(r"use: ([\w-]+)", path.read_text())))}))
    else:
        print(json.dumps({"status": "unknown", "habit": argv[2],
                          "skill": (home / "skills" / argv[2] / "SKILL.md").exists()}))
        sys.exit(1)
elif argv[:2] == ["habits", "check"]:
    sys.exit(0 if "spec: habits/0.1" in Path(argv[2]).read_text() else 1)
elif argv[:2] == ["habits", "retire"]:
    sys.exit(0 if habit(argv[2]) else 1)  # the real one finds the habit by its skill: none, "unknown habit"
elif argv[:2] == ["cron", "remove"]:
    sys.exit(1 if (home / "cron-fails").exists() else 0)
elif argv[0] == "--finish":  # what Hermes's gateway still does after the bench cut the turn
    time.sleep(float(argv[2]))
    db = sqlite3.connect(home / "state.db")
    for name in argv[3:]:  # a habit_save that lands late
        made = home / "skills" / name / "assets" / "reflex.yml"
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_text("spec: habits/0.1\n")
        (made.parent.parent / "SKILL.md").write_text("---\nname: " + name + "\n---\n")
        function = {"name": "habit_save", "arguments": json.dumps({"name": name, "scenario": {"spec": "habits/0.1"}})}
        db.execute("INSERT INTO messages (session_id, role, content, tool_calls) VALUES (?, 'assistant', '', ?)",
                   (argv[1], json.dumps([{"id": "late", "type": "function", "function": function}])))
        db.execute("INSERT INTO messages (session_id, role, content, tool_call_id, tool_name) VALUES "
                   "(?, 'tool', ?, 'late', 'habit_save')", (argv[1], json.dumps(
                       {"status": "saved", "habit": name, "kind": "watch", "path": str(made)})))
    db.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'assistant', 'Fini tard.')", (argv[1],))
    db.commit()
elif argv[0] == "-z":
    prompt, usage = argv[1], argv[argv.index("--usage-file") + 1]
    turn = config("turn.json")
    db = sqlite3.connect(home / "state.db")
    db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT, source TEXT, started_at REAL, cwd TEXT, "
               "input_tokens INT, output_tokens INT, cache_read_tokens INT, cache_write_tokens INT, "
               "api_call_count INT, tool_call_count INT, estimated_cost_usd REAL, cost_status TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
               "content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT)")
    for where in turn.get("others", []):  # other agents' turns, started at the same time
        db.execute("INSERT INTO sessions VALUES (?, 'oneshot', ?, ?, 9, 9, 0, 0, 7, 0, NULL, NULL)",
                   (f"other-{time.time_ns()}", time.time(), where))
    sid = f"local-{time.time_ns()}"
    db.execute("INSERT INTO sessions VALUES (?, 'oneshot', ?, ?, 1000, 100, 500, 0, 3, 2, ?, ?)",
               (sid, time.time(), os.getcwd(), turn.get("cost"), turn.get("cost_status")))
    calls = turn.get("calls")
    wanted = re.search(r"habit_save: ([\w-]+)", prompt)
    if wanted and not turn.get("late_save") and turn.get("saves", True):
        names = [wanted.group(1)] + turn.get("also", [])  # also: other names the turn saves
        saved = []
        for name in names:
            made = home / "skills" / turn.get("paths", {}).get(name, name) / "assets" / "reflex.yml"
            made.parent.mkdir(parents=True, exist_ok=True)
            made.write_text(turn.get("habit", "spec: habits/0.1\n"))
            (made.parent.parent / "SKILL.md").write_text("---\nname: " + name + "\n---\n")
            for file, text in turn.get("assets", {}).items():  # its own scripts, next to its graph
                (made.parent / file).write_bytes(text.encode("latin-1"))
                (made.parent / file).chmod(0o755 if file.endswith(".sh") else 0o644)
            saved.append({"tool": "habit_save", "scenario": True, "reply": {
                "status": "saved", "habit": name, "kind": turn.get("kinds", {}).get(name, turn.get("kind", "one-off")),
                "path": str(made)}})
        if calls is None:
            refused = {"status": "refused", "issues": ["no fallback"]}
            calls = [{"tool": "habit_save", "scenario": True, "reply": refused}] + saved
    for n, call in enumerate(calls or []):
        args = call.get("args") or {"name": wanted.group(1) if wanted else "x",
                                    **({"scenario": {"spec": "habits/0.1"}} if call.get("scenario") else {})}
        inner = [{"name": call["tool"], "arguments": args}]
        # Hermes 0.23's tool_call; its calls may come as a JSON text (string), one that does not parse (broken)
        wrapped = json.dumps({"calls": json.dumps(inner)[:-2] if call.get("broken") else
                                       json.dumps(inner) if call.get("string") else inner})
        function = ({"name": call["tool"], "arguments": json.dumps(args)} if call.get("direct") else
                    {"name": "tool_call", "arguments": wrapped})
        db.execute("INSERT INTO messages (session_id, role, content, tool_calls) VALUES (?, 'assistant', '', ?)",
                   (sid, json.dumps([{"id": f"c{n}", "type": "function", "function": function}])))
        db.execute("INSERT INTO messages (session_id, role, content, tool_call_id, tool_name) VALUES "
                   "(?, 'tool', ?, ?, ?)", (sid, json.dumps(call["reply"]), f"c{n}", call.get("from", call["tool"])))
    if turn.get("late"):  # the turn outlives the bench's timeout, and answers later in the gateway
        db.commit()
        subprocess.Popen([sys.executable, __file__, "--finish", sid, str(turn["late"])]
                         + ([wanted.group(1)] + turn.get("also", []) if wanted and turn.get("late_save") else []),
                         start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(30)
    db.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'assistant', 'Fait.')", (sid,))
    db.commit()
    Path(usage).write_text(json.dumps({"session_id": sid if turn.get("usage_id", True) else None, "api_calls": 3}))
    for path, text in turn.get("writes", {}).items():  # what Hermes did in the folder
        Path(path).write_text(text)
    print("Fait.")
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A fake hermes, a Hermes folder for it, a home folder (for ~/.Trash) and a temp folder inside it (for the
    model turns' roots and the scratch folders)."""
    home, user = tmp_path / "hermes-home", tmp_path / "user"
    home.mkdir()
    (user / "tmp").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("TMPDIR", str(user / "tmp"))
    monkeypatch.setattr(bench.tempfile, "tempdir", None)
    monkeypatch.setenv("FAKE_HERMES_HOME", str(home))
    hermes = tmp_path / "hermes"
    hermes.write_text(FAKE.replace("PYTHON", sys.executable).replace("REPO", str(ROOT)))
    hermes.chmod(0o755)
    case = {"id": "tidy", "task": "tidy-folder", "habit": "{repo}/examples/tidy-downloads/assets/reflex.yml",
            "scratch": "~/tmp/bench/tidy", "rewrite": {"~/Downloads": "{scratch}/inbox", "~/.Trash/": "{scratch}/trash/"},
            "setup": 'mkdir -p "$SCRATCH/inbox" && touch "$SCRATCH/inbox/a.pdf"', "repeat": 2,
            "expect": {"end": {"message_contains": "sorted", "planned_tools": ["terminal"]}},
            "baseline": {"prompt": "Sort {scratch}/inbox", "expect": {"message_contains": "Fait"}},
            "create": {"prompt": "Make it a habit with habit_save: bench-x", "name": "bench-x"}}

    def run(*extra, cases=None):
        scenario = tmp_path / "suite" / "cases.json"  # the answer key: never next to a model turn, nor above it
        scenario.parent.mkdir(exist_ok=True)
        scenario.write_text(json.dumps(cases or [case]))
        return bench.main([str(scenario), "--hermes", str(hermes), "--home", str(home), *extra])

    def calls():
        log = home / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def records(capsys):
        printed, err = capsys.readouterr()
        return [json.loads(line) for line in printed.splitlines()], err

    def configure(name, data):
        (home / name).write_text(json.dumps(data))
    return run, calls, home, user, case, records, configure


def turns(calls):
    return [c for c in calls() if c["argv"][0] == "-z"]


def test_a_whole_case_with_a_fake_hermes(fake, tmp_path, capsys):
    run, calls, home, user, case, records, configure = fake
    out = tmp_path / "runs.jsonl"
    assert run("--out", str(out), "--budget", "0.05") == 0
    printed, err = records(capsys)
    assert [r["kind"] for r in printed] == ["run", "run", "baseline", "create"] and all(r["ok"] for r in printed)
    assert [json.loads(line) for line in out.read_text().splitlines()] == printed
    scratch = user / "tmp" / "bench" / "tidy"
    made = calls()
    runs = [c for c in made if c["argv"][:2] == ["habits", "run"]]
    copy = Path(runs[0]["argv"][2])  # the rewritten copy: in the bench's own folder, gone with it
    assert copy.parts[-3:] == ("tidy-downloads", "assets", "reflex.yml") and not copy.exists()
    assert f"ls -p {scratch}/inbox" in runs[0]["text"] and "Downloads" not in runs[0]["text"]
    assert f'description: "Sort {scratch}/inbox by file type' in runs[0]["skill"]
    assert not (scratch / ".bench").exists()
    first = printed[0]
    assert (first["ms"], first["model_calls"], first["tokens"], first["planned_tools"]) == (50, 0, 0, ["terminal"])
    assert isinstance(first["wall_ms"], int) and first["version"] == "v1"
    assert runs[0]["argv"][3:] == ["--json"] and runs[0]["cwd"] == str(scratch)  # dry: no --live
    baseline = printed[2]
    assert (baseline["tokens"], baseline["api_calls"], baseline["tool_calls"], baseline["answer"]) == (1600, 3, 2, "Fait.")
    assert baseline["cost"] == pytest.approx(1500 * 0.28e-6 + 100 * 0.42e-6) and baseline["session"].startswith("local-")
    assert baseline["habit_calls"] == []
    # The baseline turn ran with habits off (the variable, and the file the gateway sees); the create turn with them on.
    assert [(t["off_env"], t["off_file"]) for t in turns(calls)] == [("1", True), (None, False)]
    assert not (home / "plugin-data" / "habits" / "off").exists()
    create = printed[3]
    assert (create["saves"], create["statuses"], create["first_try"], create["valid"]) == (2, ["refused", "saved"], False, True)
    assert not (home / "skills" / "bench-x").exists() and len(list((user / ".Trash").glob("bench-x-bench-*"))) == 1
    assert (Path(create["kept"]) / "assets" / "reflex.yml").is_file() and Path(create["kept"]).parent.parent == tmp_path
    assert create["works"] is True and "clean_error" not in create  # Hermes's habit, run dry against the expect
    works = [c for c in made if c["argv"][:2] == ["habits", "run"]][-1]  # a copy of what was kept, rewritten
    assert works["text"] == (Path(create["kept"]) / "assets" / "reflex.yml").read_text() and works["skill"]
    assert Path(works["argv"][2]).parent.parent.name == Path(create["kept"]).name
    assert Path(create["kept"]) not in Path(works["argv"][2]).parents
    assert "2/2 100%" in err and "1/1 valid, 0 first try, 1 work" in err and "model turns: 2" in err
    assert run("--only", "tidy", "--no-baseline", "--no-create") == 0  # the habit could be made again: it was moved


def test_the_budget_and_the_flags_skip_model_turns(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    assert run("--budget", "0.005", "--no-create") == 0
    printed, err = records(capsys)
    assert [r["kind"] for r in printed] == ["run", "run"]
    assert "budget: stopped before the baseline turn of tidy" in err and "1 stopped by the budget" in err
    assert not turns(calls)
    assert run() == 0 and "budget: 0.02 $" in records(capsys)[1]  # never without a budget


def test_a_habit_runs_own_model_calls_count_against_the_budget(fake, capsys, tmp_path):
    # An llm node, or the model when the decision model fails: paid calls that a run makes on its own.
    run, calls, home, user, case, records, configure = fake
    configure("run.json", {"backends": ["llm"] * 4, "model_calls": 4})
    assert run("--budget", "0.01", "--no-baseline", "--no-create", cases=[{**case, "repeat": 5}]) == 0
    printed, err = records(capsys)
    assert len(printed) == 1 and "budget: stopped before the run 1 of tidy" in err  # 4 calls: 0.008 of 0.01 $
    assert ("model turns: 0, 0.0000 $; a habit's own model calls: 4 (0 by llm nodes, 4 by questions no decision "
            "model took), about 0.0080 $ at 0.002 $ a call, not measured; 0.0080 $ counted of 0.01 $") in err
    # A bench run: each case's first run was counted at 0 $, so a file of many cases was never stopped (23
    # calls, 0.046 $ against 0.03 $). A first run is counted at the dearest run so far (0 before any).
    many = [{**case, "id": f"c{n}", "repeat": 1} for n in range(5)]
    assert run("--budget", "0.01", "--no-baseline", "--no-create", cases=many) == 0
    printed, err = records(capsys)
    assert [r["case"] for r in printed] == ["c0"] and "budget: stopped before the run 0 of c1" in err
    assert run("--budget", "0", "--no-baseline", "--no-create", cases=many) == 0  # one run over, at most
    assert [r["case"] for r in records(capsys)[0]] == ["c0"]
    # Habits that call no model all run at --budget 0 (each was counted at 1 call), and the
    # dearest run is any run's, not only each case's last one.
    configure("run.json", {"model_calls": 0})
    assert run("--budget", "0", "--no-baseline", "--no-create", cases=many + [{**case, "repeat": 2}]) == 0
    assert len(records(capsys)[0]) == 7
    bench_run = bench.Bench("hermes", home, bench.Budget(0.0125), None, tmp_path)
    calls_of = iter([3, 1, 0])  # a's runs call the model 3 times, then once; b's first is counted at 3, not 1

    def habit_run(c, target, env=None):
        calls = next(calls_of)
        bench_run.budget.charge(calls)
        return 0, {"status": "end", "model_calls": calls}, "", 5
    bench_run.setup, bench_run.habit_run = (lambda c: ""), habit_run
    for n, c in enumerate([{**case, "id": "a", "_target": "x"}] * 2 + [{**case, "id": "b", "_target": "x"}]):
        bench_run.run(c, n)
    assert bench_run.skipped == 1 and bench_run.budget.spent == pytest.approx(0.008)  # b: 0.008 + 0.006 > 0.0125


def test_the_llm_nodes_calls_and_their_estimated_cost_are_said_apart_from_the_model_turns(fake, capsys):
    # A run with no model turn can still call the model from its llm nodes at each run:
    # 62 calls, about 0.124 $, which only the budget's one total line held.
    run, calls, home, user, case, records, configure = fake
    configure("run.json", {"counts": {"llm": 2}, "model_calls": 3, "backends": ["jev", "llm"],
                           "llm_ms": {"rediger": 812, "titre": 90}})
    assert run("--no-baseline", "--no-create", "--budget", "0.1") == 0
    err = records(capsys)[1]
    assert ("model turns: 0, 0.0000 $; a habit's own model calls: 6 (4 by llm nodes, 2 by questions no decision "
            "model took), about 0.0120 $ at 0.002 $ a call, not measured") in err
    assert ("own model calls of tidy-downloads: 4 by llm nodes (rediger, titre), 2 by questions no "
            "decision model took, in 2 runs: about 0.0120 $, not measured (the CLI's llm calls write no session)"
            ) in err
    found, = bench.own_calls([{"case": "c", "kind": "run", "habit": "/r/x/assets/reflex.yml", "model_calls": 1,
                               "counts": {"llm": 1}, "llm_ms": {"words": 9}, "plan_model_calls": 2},
                              {"case": "c", "kind": "run", "habit": "/r/x/assets/reflex.yml", "model_calls": 0},
                              {"case": "d", "kind": "run", "habit": "y", "model_calls": 0, "counts": {"llm": 0}},
                              {"case": "c", "kind": "baseline", "api_calls": 9}])
    assert found == {"habit": "x", "runs": 2, "nodes": 1, "questions": 0, "plan": 2, "names": ["words"]}
    assert bench.own_lines([found]) == [
        "own model calls of x: 1 by llm nodes (words), 0 by questions no decision model took, 2 in live runs' dry "
        "passes, in 2 runs: about 0.0060 $, not measured (the CLI's llm calls write no session)"]


def test_a_create_run_never_rewrites_a_habit_or_a_skill(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    skills = home / "skills"
    for made, said in (
            (skills / "bench-x" / "assets" / "reflex.yml", 'a habit named "bench-x" exists already'),
            (skills / "bench-x" / "SKILL.md", 'a skill named "bench-x" exists already'),  # a skill, no habit
            (skills / ".archive" / "bench-x" / "notes.md", "/.archive/bench-x exists already"),
            (skills / "notes" / "SKILL.md", 'notes/SKILL.md is named "bench-x"'),  # its folder has another name
            (home / "show-fails", "hermes habits show bench-x --json gave no clear answer")):
        made.parent.mkdir(parents=True, exist_ok=True)
        made.write_text("---\nname: bench-x\n---\n" if made.name == "SKILL.md" else "spec: habits/0.1\n")
        assert run("--no-baseline") == 1, said
        create = records(capsys)[0][-1]
        assert create["kind"] == "create" and not create["ok"] and said in create["why"][0], create["why"]
        assert "pick another name" in create["why"][0] and not turns(calls)
        made.unlink()
        if made.parent != home:
            import shutil
            shutil.rmtree(made.parent if made.parent.name != "assets" else made.parent.parent)


def test_a_folder_there_before_the_turn_is_left_where_it_is(fake, tmp_path):
    run, calls, home, user, case, records, configure = fake
    folder = home / "skills" / "bench-x"
    (folder / "assets").mkdir(parents=True)
    (folder / "assets" / "reflex.yml").write_text("spec: habits/0.1\n")
    runner = bench.Bench("hermes", home, bench.Budget(0.02), None, tmp_path)
    kept, trouble = runner.clean("bench-x", folder / "assets" / "reflex.yml", "one-off", "c-0", {folder.resolve()})
    assert kept is None and "was there before the turn" in trouble and folder.is_dir()


def test_a_case_that_could_touch_downloads_or_a_real_folder_is_refused(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    # A saved habit whose SKILL.md gives another name, and one that uses a habit reading ~/Downloads.
    for name, graph in (("sort-dl", "nodes: {list: {do: terminal, read: true, args: {command: ls ~/Downloads}}}"),
                        ("morning", "nodes: {a: {use: sort-dl, next: b}}")):
        (home / "skills" / name / "assets").mkdir(parents=True)
        (home / "skills" / name / "assets" / "reflex.yml").write_text(graph + "\n")
    (home / "skills" / ".archive" / "sort-dl" / "assets").mkdir(parents=True)  # an archived, clean copy
    (home / "skills" / ".archive" / "sort-dl" / "assets" / "reflex.yml").write_text("nodes: {}\n")
    clean = {k: v for k, v in case.items() if k not in ("rewrite", "setup", "baseline", "create")}
    refused = [
        ({**case, "scratch": "/etc/bench"}, "must be inside"),
        ({**case, "setup": "cp ~/Downloads/* $SCRATCH"}, "its setup names a Downloads folder"),
        ({**case, "verify": "ls ~/Downloads"}, "its verify names a Downloads folder"),
        ({**case, "rewrite": {}}, "its habit names a Downloads folder"),  # the example as it is reads ~/Downloads
        ({**case, "baseline": {"prompt": "sort my downloads."}}, "its baseline prompt names a Downloads folder"),
        ({**case, "inputs": {"folder": "/Users/alex/Downloads"}}, "its inputs names a Downloads folder"),
        ({**case, "habit": "tidy-downloads", "rewrite": {}}, "its habit names a Downloads folder"),
        ({**clean, "habit": "sort-dl"}, "its habit names a Downloads folder"),  # the file a run reads
        ({**clean, "habit": "morning"}, "its sub-habit sort-dl names a Downloads folder"),
        ({**clean, "habit": "nope"}, "no habit named nope"),
        ({**clean, "habit": "{repo}/bench/x.yaml", "expect": {"end": {}}, "mode": "live",
          "scratch": None}, "mode: live needs a scratch folder"),
        ({**{k: v for k, v in case.items() if k not in ("scratch", "setup", "rewrite")},
          "habit": "{scratch}/x.yaml"}, "names no scratch folder"),
        ({**case, "habit": "missing/reflex.yml", "rewrite": {}}, "no habit file"),
    ]
    for wrong, said in refused:
        assert run(cases=[wrong]) == 2, said
        assert said in capsys.readouterr()[1], said
    assert not [c for c in calls() if c["argv"][:2] != ["habits", "show"]]  # nothing ran, only lookups
    assert run("--only", "nope") == 2 and "no case nope" in capsys.readouterr()[1]


def test_a_baseline_that_used_a_habit_is_not_hermes_alone(fake, capsys):
    # A one-off's SKILL.md tells the model to run it with habit_run; habits off, the tool refuses, and the bench
    # still counts the turn out: its calls and seconds are not Hermes's alone.
    run, calls, home, user, case, records, configure = fake
    configure("turn.json", {"calls": [{"tool": "habit_run", "direct": True,
                                       "reply": {"status": "refused", "issues": ["Habits is off"]}}]})
    assert run("--no-create") == 1
    baseline = records(capsys)[0][-1]
    assert baseline["kind"] == "baseline" and not baseline["ok"]
    assert baseline["why"] == ["used a habit (habit_run:refused): not Hermes alone"]
    row, = bench.summary([baseline])
    assert row["baselines"] == 0


def test_a_live_case_runs_within_its_scratch_folder_and_checks_what_was_done(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    scratch = user / "tmp" / "bench" / "tidy"
    live = {**case, "mode": "live", "repeat": 1, "verify": 'test -f "$SCRATCH/inbox/document/a.pdf"',
            "baseline": {**case["baseline"]}}
    configure("turn.json", {"writes": {}})
    assert run("--no-create", cases=[live]) == 1
    printed, _ = records(capsys)
    habit_runs = [c for c in calls() if c["argv"][:2] == ["habits", "run"]]
    assert habit_runs[0]["argv"][-2:] == ["--live", f"--within={scratch}"]
    assert [r["why"][-1][:20] for r in printed] == ["verify failed (exit ", "verify failed (exit "]
    (scratch / "inbox" / "document").mkdir(parents=True)
    live["setup"] += ' && mkdir -p "$SCRATCH/inbox/document" && touch "$SCRATCH/inbox/document/a.pdf"'
    assert run("--no-create", cases=[live]) == 0


def test_a_stale_off_file_is_removed_and_never_left_fresh(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    off = home / "plugin-data" / "habits" / "off"
    off.parent.mkdir(parents=True)
    off.touch()
    os.utime(off, (time.time() - 3600, time.time() - 3600))  # left by a bench that died an hour ago: habits are on
    assert run("--budget", "0.05") == 0
    printed, err = records(capsys)
    assert "removed a stale" in err and all(r["ok"] for r in printed)
    assert [(t["off_env"], t["off_file"]) for t in turns(calls)] == [("1", True), (None, False)]
    assert not off.exists()
    off.touch()  # another bench measures right now: its file stays, and no create turn runs meanwhile
    assert run("--budget", "0.05") == 1
    printed, _ = records(capsys)
    assert off.exists() and "habits are off" in printed[-1]["why"][0]


def test_a_create_runs_with_habits_paused_until_its_habit_is_retired(fake, capsys):
    # C-night-watch saved a watch that would quit the Claude app, and its real cron job ran until the bench
    # retired it; a shadow wake tells the woken turn to make the planned calls itself. Paused, every check is silent.
    run, calls, home, user, case, records, configure = fake
    paused = home / "plugin-data" / "habits" / "paused"
    configure("turn.json", {"kind": "watch"})
    assert run("--budget", "0.05", cases=[{**case, "repeat": 1}]) == 0
    made = calls()
    start = next(k for k, c in enumerate(made) if c["paused"])
    assert [(c["argv"][0], c["off_env"]) for c in made[:start]] == [("habits", None), ("-z", "1")]
    assert all(c["paused"] == f"habits bench {os.getpid()}\n" for c in made[start:])  # its run and baseline: none
    assert [c["argv"][0] for c in made[start:]].count("-z") == 1  # the create's turn, its check, works and retire
    assert [c["argv"][:2] for c in made[start:]][-2:] == [["habits", "retire"], ["habits", "run"]]  # works last
    assert not paused.exists()
    paused.write_text("")  # the user paused habits: the bench leaves it as it is
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 0
    assert paused.read_text() == "" and all(c["paused"] == "" for c in calls()[len(made):])
    paused.write_text("habits bench 999999\n")  # a bench that died mid-create: its pause goes
    assert run("--no-baseline", "--no-create") == 0
    assert not paused.exists() and "removed a stale" in records(capsys)[1]
    paused.write_text(f"habits bench {os.getppid()}\n")  # another bench, still running: its pause stays
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 0
    assert paused.read_text() == f"habits bench {os.getppid()}\n"


def test_while_the_bench_pauses_habits_a_watch_check_is_silent_and_the_cli_runs(setup, tmp_path, monkeypatch, capsys):
    ctx, habits = setup  # the plugin's own folder is tmp_path/plugin-data/habits, as the bench sees ~/.hermes
    plugin_tests.with_habit(tmp_path, monkeypatch, "watch", plugin_tests.VEILLE)
    runner = bench.Bench("hermes", tmp_path, bench.Budget(0.02), None, tmp_path)
    ctx.calls.clear()
    with runner.habits_paused():
        assert habits.paused()
        assert habits.tick("watch") == [plugin_tests.watch.GATE_SILENT] and ctx.calls == []  # nothing read
        code, out, _ = plugin_tests.cli_run(habits, capsys, "tidy-downloads")
        assert code == 0 and out["status"] == "end"  # hermes habits run still works
    assert not habits.paused()
    # A pause the user makes during a create is theirs, and stays after it.
    with runner.habits_paused():
        assert habits.command("pause").startswith("⏸")
    assert habits.paused() and runner.pause_of() is None
    habits.pause(False)
    runner.pending.append({})  # a cut create may still save: the bench's pause outlives the create...
    with runner.habits_paused():
        pass
    assert runner.pause_of() == os.getpid()
    habits.pause(True)  # ...until the user pauses habits himself: the next create leaves his pause as it is
    runner.pending.clear()
    with runner.habits_paused():
        assert runner.pause_of() is None
    assert habits.paused() and not runner.pausing


def test_first_try_is_the_first_graph_hermes_wrote_and_works_runs_the_habit_it_made(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    saved = {"status": "saved", "habit": "bench-x", "kind": "one-off"}
    for tried, first in (
            ([{"tool": "habit_run", "scenario": True, "reply": {"status": "refused", "issues": ["rule 3"]}}], False),
            ([{"tool": "habit_run", "scenario": True, "reply": {"status": "wake"}, "direct": True}], True),
            ([{"tool": "habit_run", "scenario": False, "reply": {"status": "refused", "issues": ["no habit"]}}], True)):
        configure("turn.json", {"calls": tried + [{"tool": "habit_save", "scenario": True, "reply": saved}]})
        assert run("--no-baseline") == 0
        create = records(capsys)[0][-1]
        assert (create["first_try"], create["statuses"], create["works"]) == (first, ["saved"], True), tried
    configure("run.json", {"status": "wake", "failed": True, "node": "unsynced", "reason": "",
                           "message": "The note was written, but the sync is not confirmed."})
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 1  # the habit Hermes made breaks on the task
    create = records(capsys)[0][-1]
    assert create["ok"] and create["works"] is False and create["works_why"] == ["status wake, expected end"]
    # Handed back on its works case, and its record could not say at which step: now it does.
    assert (create["works_node"], create["works_reason"]) == ("unsynced", "")
    assert create["works_message"] == "The note was written, but the sync is not confirmed."


def test_a_message_case_goes_through_the_trigger(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    message = {**case, "repeat": 1, "message": "sort everything but the pdfs", "expect": {"none": {}}}
    configure("run.json", {"status": "none", "planned": [], "planned_count": 0,
                           "trigger": {"picked": "sort", "fired": None, "ms": 900}})
    assert run("--no-baseline", "--no-create", cases=[message]) == 0
    printed, _ = records(capsys)
    assert printed[0]["trigger"]["picked"] == "sort" and printed[0]["status"] == "none"
    argv = [c["argv"] for c in calls() if c["argv"][:2] == ["habits", "run"]][0]
    assert argv[-1] == "--message=sort everything but the pdfs"
    configure("run.json", {"status": "other", "trigger": {"picked": "photo-arsorter", "fired": "photo-arsorter"}})
    assert run("--no-baseline", "--no-create", cases=[message]) == 1
    assert records(capsys)[0][0]["why"] == ["status other (the trigger runs photo-arsorter), expected none"]


def test_two_benches_in_one_second_keep_their_own_copies(fake, capsys, monkeypatch, tmp_path):
    run, calls, home, user, case, records, configure = fake
    monkeypatch.setattr(bench.time, "strftime", lambda f, *a: "1108-120000")  # both in the same second
    out = tmp_path / "o" / "runs.jsonl"
    out.parent.mkdir()
    assert run("--no-baseline", "--out", str(out)) == 0
    assert run("--no-baseline", "--out", str(out)) == 0
    kept = [r["kept"] for r in records(capsys)[0] if r["kind"] == "create"]
    assert len(set(kept)) == 2 and all((Path(k) / "assets" / "reflex.yml").is_file() for k in kept)
    assert not (home / "skills" / "bench-x").exists() and len(list((user / ".Trash").glob("bench-x-bench-*"))) == 2
    # A copy that fails still lets the habit go, retired first: a watch's cron job never outlives the bench.
    configure("turn.json", {"kind": "watch", "calls": None})
    monkeypatch.setattr(bench.shutil, "copytree", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 0
    create = records(capsys)[0][-1]
    assert create["kept"] is None and "disk full" in create["clean_error"]
    assert not (home / "skills" / "bench-x").exists()
    retired = [c["argv"] for c in calls() if c["argv"][:2] == ["habits", "retire"]]
    assert retired == [["habits", "retire", "bench-x"]] * 3  # the two one-offs above, then the watch


def test_without_an_id_the_session_is_the_one_started_in_the_case_folder(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    configure("turn.json", {"usage_id": False, "others": ["/Users/alex"]})  # another agent's turn, meanwhile
    assert run("--no-create") == 0
    baseline = records(capsys)[0][-1]
    assert baseline["session"].startswith("local-") and baseline["api_calls"] == 3
    configure("turn.json", {"usage_id": False, "others": ["{cwd}"]})  # two in the same folder: none is taken
    assert run("--no-create") == 1
    printed, err = records(capsys)
    assert "no session found in state.db" in printed[-1]["why"] and "none is taken" in err


def test_the_demo_scenario_loads_and_runs_its_tidy_case_on_a_scratch_copy(tmp_path):
    cases = bench.load(ROOT / "bench" / "scenarios" / "demo.yaml")
    assert [c["id"] for c in cases] == ["tidy", "tidy-not-now", "release", "links"]
    tidy = bench.prepare(cases[0], Path(os.path.expanduser("~/.hermes")), work=tmp_path)
    assert tidy["_target"] == str(tmp_path / "copies" / "tidy" / "tidy-downloads" / "assets" / "reflex.yml")
    assert "/habits-bench/tidy/inbox" in tidy["_copy"] and not bench.DOWNLOADS.search(tidy["_copy"])
    assert "{scratch}" not in json.dumps(tidy["baseline"]) and str(tidy["_scratch"]) in tidy["create"]["prompt"]
    assert cases[3]["mode"] == "live" and cases[3]["scratch"]  # a live case keeps its writes in a scratch folder
    assert "{scratch}" not in bench.prepare(cases[1], Path(os.path.expanduser("~/.hermes")), work=tmp_path)["message"]


def test_a_one_off_is_charged_the_two_model_calls_around_it():
    # Nothing runs a one-off but the model: it calls habit_run, then answers with what came back. A request habit
    # answers in the hook, a watch in its tick: neither needs the model when it ends.
    base = [{"case": c, "task": "t", "kind": "baseline", "ok": True, "session": "s", "seconds": 20, "api_calls": 10}
            for c in ("one", "req")]
    runs = [{"case": "one", "task": "t", "kind": "run", "ok": True, "status": "end", "ms": 900, "wall_ms": 2000,
             "model_calls": 1, "habit_kind": "one-off"},
            {"case": "req", "task": "t", "kind": "run", "ok": True, "status": "end", "ms": 900, "wall_ms": 2000,
             "model_calls": 1, "habit_kind": "request"}]
    one, req = bench.summary(runs + base)
    assert one["task_s"] == pytest.approx(2 + 2 * 20 / 10) and req["task_s"] == pytest.approx(2)
    # Its ceiling: a 0 ms run still pays the two model calls around it.
    assert (one["ceiling"], one["ceiling_avoided"]) == (pytest.approx(20 / 4), pytest.approx(1 - 3 / 10))
    assert req["ceiling"] is None and "3.3x (max 5.0x)" in bench.table([one]) and "70% (max 70%)" in bench.table([one])
    assert one["avoided"] == pytest.approx(1 - 3 / 10) and req["avoided"] == pytest.approx(1 - 1 / 10)
    assert one["engine"] == req["engine"]  # the engine alone is the same work
    woke, = bench.summary([{**runs[0], "status": "wake"}, base[0]])
    assert woke["task_s"] == pytest.approx(2 + 20 + 4)  # a hand-back: the task by hand on top


def test_a_one_off_has_its_own_goal_line_next_to_its_capped_figures():
    # The 2 model calls around a one-off cap its speedup, so
    # it is also judged on what it can reach: right, never a silent wrong end, and its own model calls per run.
    habit = "/r/habits/vault-save/assets/reflex.yml"
    runs = [{"case": c, "task": c, "kind": "run", "ok": ok, "status": status, "wall_ms": 900, "model_calls": calls,
             "habit": habit, "habit_kind": "one-off"}
            for c, ok, status, calls in (("racine", True, "end", 1), ("racine", True, "wake", 1),
                                         ("bien-etre", False, "end", 2), ("piege", True, "wake", 0))]
    other = {**runs[0], "case": "req", "habit": "/r/habits/t1/assets/reflex.yml", "habit_kind": "request"}
    found, = bench.one_offs(runs + [other, {"case": "racine", "task": "racine", "kind": "baseline", "ok": True}])
    assert (found["habit"], found["cases"], found["runs"], found["right"], found["silent"]) == ("vault-save", 3, 4, 3, 1)
    assert found["calls"] == 1.0 and found["slow"] == 0
    line, = bench.one_off_lines([found])
    assert line == ("one-off vault-save, its own goal (3 cases): 3/4 right (75%), of which 0 slow (only over max_ms), "
                    "1 silent wrong (ended wrong), 1.0 model calls per run, plus the 2 around it; its speedup "
                    "and calls avoided stay capped (max in the table): the 5x and 80% goals are checked on the reference machine")
    # A bench run: two runs had the right folder and were only over their 30 s; slow, not a silent wrong action.
    # One over its time with a wrong value too ended wrong.
    late = [{**runs[0], "ok": False, "why": ["took 31012 ms, more than 30000"]},
            {**runs[0], "ok": False, "why": ["value folder is 2. Areas, expected 3. Resource",
                                              "took 31012 ms, more than 30000"]}]
    found, = bench.one_offs(runs + late)
    assert (found["runs"], found["right"], found["slow"], found["silent"]) == (6, 4, 1, 2)
    assert "4/6 right (67%), of which 1 slow (only over max_ms), 2 silent wrong (ended wrong)" in bench.one_off_lines(
        [found])[0]
    assert bench.slow(late[0]) and not bench.slow(late[1]) and not bench.slow(runs[0])
    assert bench.right(late[0]) and not bench.right(late[1])
    assert bench.one_offs([other]) == []


def test_a_run_only_over_its_time_is_slow_and_right_everywhere():
    # Slow, not wrong, everywhere and not only on the one-off line: a run 31005 ms against 30000 counted as neither
    # right nor done in the table, and a request habit's run (22250 ms against 15000), stayed one of its 3 wrong runs, with no slow mark.
    base = {"case": "typo", "task": "t", "kind": "baseline", "ok": True, "session": "s", "seconds": 20, "api_calls": 10}
    run = {"case": "typo", "task": "t", "kind": "run", "status": "end", "ms": 22250, "wall_ms": 23000,
           "model_calls": 1, "habit_kind": "request"}
    old = {**run, "ok": False, "why": ["took 22250 ms, more than 15000"]}  # an older record
    new = {**run, "ok": True, "slow": True, "why": ["took 22250 ms, more than 15000"]}
    wrong = {**run, "ok": False, "why": ["message lacks 'x'", "took 22250 ms, more than 15000"]}
    for slow in (old, new):
        row, = bench.summary([slow, wrong, base])
        assert (row["right"], row["done"], row["slow"]) == (1, 1, 1)
        assert row["task_s"] == pytest.approx((23 + 23 + 20) / 2)  # the wrong one is done by hand on top, not the slow
        assert "1/2 50%, 1 slow" in bench.table([row])
    assert bench.finished(old) and not bench.finished(wrong)
    assert bench.matches({"end": {}, "max_ms": 15000}, {"status": "end", "ms": 22250}) == [
        "took 22250 ms, more than 15000"]  # matches still says it; run() takes it for slow


def test_a_kept_copy_keeps_a_link_as_a_link_and_works_leaves_it_out(tmp_path):
    # Copytree followed links, so a habit's link to a file outside it came into the kept copy
    # as a plain file, and works() ran it.
    folder, outside = tmp_path / "skills" / "made", tmp_path / "outside.sh"
    (folder / "assets").mkdir(parents=True)
    outside.write_text("echo outside\n")
    (folder / "assets" / "helper.sh").symlink_to(outside)
    kept = tmp_path / "kept"
    bench.copy_kept(folder, kept)
    assert (kept / "assets" / "helper.sh").is_symlink()


def test_the_one_off_line_is_printed_under_the_table(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    configure("run.json", {"kind": "one-off"})
    assert run("--no-baseline", "--no-create") == 0
    assert ("one-off tidy-downloads, its own goal (1 case): 2/2 right (100%), of which 0 slow (only over "
            "max_ms), 0 silent wrong") in records(capsys)[1]


def test_a_create_turn_takes_away_every_habit_it_saved(fake, capsys):
    # A watch saved under another name than asked would keep a real cron job: each habit the turn saved goes,
    # retired first, while its skill is there to be found. One there before the turn is the user's: left as it is.
    # Asked for a watch, Hermes wrote a request, saved active since it only reads; the bench
    # retired watches only, and the user's ledger kept bench-agents-stuck active with its skill in the Trash.
    run, calls, home, user, case, records, configure = fake
    (home / "skills" / "mine" / "assets").mkdir(parents=True)
    (home / "skills" / "mine" / "assets" / "reflex.yml").write_text("spec: habits/0.1\n")
    configure("turn.json", {"also": ["bench-x-watch", "mine"], "kinds": {"bench-x": "request",
                                                                          "bench-x-watch": "watch"}})
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 0
    create = records(capsys)[0][-1]
    assert create["ok"] and create["works"] and create["also_saved"] == ["bench-x-watch", "mine"]
    assert not (home / "skills" / "bench-x").exists() and not (home / "skills" / "bench-x-watch").exists()
    assert len(list((user / ".Trash").glob("bench-x-watch-bench-*"))) == 1
    assert [c["argv"] for c in calls() if c["argv"][:2] == ["habits", "retire"]] == [
        ["habits", "retire", "bench-x"], ["habits", "retire", "bench-x-watch"]]
    assert (home / "skills" / "mine" / "assets" / "reflex.yml").is_file()
    assert "was there before the turn" in create["clean_error"] and "mine" in create["clean_error"]
    assert "retire" not in create["clean_error"]  # each found by its skill: retired before the Trash


def test_a_model_turn_that_runs_what_its_case_forbids_is_wrong(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    ran = [{"tool": "terminal", "args": {"command": "cd ~/Documents/vault && ob sync"}, "reply": {"output": ""}},
           {"tool": "terminal", "args": {"command": "{cwd}/bin/ob sync"}, "reply": {"output": ""}}]  # its stand-in
    configure("turn.json", {"calls": ran})
    assert run("--no-create", cases=[{**case, "repeat": 1, "forbid": ["ob", "ssh"]}]) == 1
    baseline = records(capsys)[0][-1]
    said = "it ran ob (cd ~/Documents/vault && ob sync), which the case forbids"
    assert baseline["kind"] == "baseline" and not baseline["ok"] and baseline["why"] == [said]
    assert run("--no-create", cases=[{**case, "repeat": 1, "forbid": ["ssh"]}]) == 0  # the stand-in is the case's
    configure("turn.json", {"calls": ran + [{"tool": "habit_save", "scenario": True, "reply": {
        "status": "saved", "habit": "bench-x", "kind": "one-off"}}]})
    assert run("--no-baseline", cases=[{**case, "repeat": 1, "forbid": ["ob"]}]) == 1
    create = records(capsys)[0][-1]
    assert create["kind"] == "create" and not create["ok"] and create["valid"] and create["why"] == [said]


def test_dry_check_loads_every_file_and_runs_nothing(fake, tmp_path, capsys):
    run, calls, home, user, case, records, configure = fake
    folder = tmp_path / "scenarios"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps([case, {**case, "id": "b", "repeat": 3, "baseline": None}]))
    (folder / "b.json").write_text(json.dumps([{**case, "scratch": "/etc/x"}]))
    (folder / "c.json").write_text(json.dumps([{**case, "expect": {"status": "end"}}]))
    hermes, home_ = str(tmp_path / "hermes"), str(home)
    assert bench.main([str(folder), "--dry-check", "--hermes", hermes, "--home", home_]) == 2
    printed, err = capsys.readouterr()
    assert printed == "" and not calls()  # no run, no setup, no turn, no lookup
    assert f"{folder / 'a.json'}: 2 cases, 5 habit runs, 1 baseline and 2 create turns: no problem" in err
    assert "b.json: 1 problem:\n  b:tidy: scratch " in err and "etc/x must be inside" in err  # /private/etc
    assert "c.json: 1 problem:\n  case tidy: unknown expect key status" in err
    assert not (user / "tmp" / "bench" / "tidy").exists()
    (folder / "b.json").unlink()
    (folder / "c.json").unlink()
    assert bench.main([str(folder), "--dry-check", "--hermes", hermes, "--home", home_]) == 0


def test_a_habit_its_setup_makes_is_read_once_the_setup_ran(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    late = {**case, "habit": "{scratch}/made/assets/reflex.yml", "repeat": 1, "baseline": None, "create": None,
            "setup": case["setup"] + ' && mkdir -p "$SCRATCH/made/assets" && cp '
                     '"$REPO/examples/tidy-downloads/assets/reflex.yml" "$SCRATCH/made/assets/"'}
    assert run(cases=[late]) == 0
    scratch = user / "tmp" / "bench" / "tidy"
    ran, = [c for c in calls() if c["argv"][:2] == ["habits", "run"]]
    assert Path(ran["argv"][2]).parts[-3:] == ("made", "assets", "reflex.yml") and scratch not in Path(
        ran["argv"][2]).parents
    assert f"{scratch}/inbox" in ran["text"] and "Downloads" not in ran["text"]
    capsys.readouterr()
    import shutil
    shutil.rmtree(scratch / "made")
    assert run(cases=[{**late, "rewrite": {}}]) == 1  # read after the setup, it is still checked
    assert "its habit names a Downloads folder" in records(capsys)[0][0]["why"][0]
    assert run(cases=[{**late, "habit": "{scratch}/none.yaml"}]) == 1
    assert "no habit file" in records(capsys)[0][0]["why"][0]


def test_a_case_memory_file_reaches_the_run(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    assert run("--no-baseline", "--no-create", cases=[{**case, "memory": "{scratch}/memory.json", "repeat": 1}]) == 0
    argv = [c["argv"] for c in calls() if c["argv"][:2] == ["habits", "run"]][0]
    assert f"--memory={user / 'tmp' / 'bench' / 'tidy' / 'memory.json'}" in argv
    assert run("--no-baseline", "--no-create", cases=[{**case, "memory": "m/memory.json", "repeat": 1}]) == 0
    argv = [c["argv"] for c in calls() if c["argv"][:2] == ["habits", "run"]][-1]
    assert f"--memory={user / 'tmp' / 'bench' / 'tidy' / 'm' / 'memory.json'}" in argv  # from the folder
    assert run("--no-baseline", "--no-create", cases=[{**case, "memory": "/tmp/elsewhere.json"}]) == 2
    assert "memory /tmp/elsewhere.json must be in its scratch folder" in capsys.readouterr()[1]


def test_a_send_inside_a_habit_run_is_a_forbidden_send_too():
    # Seen in review: habit_run runs live, and the habit's terminal calls are not the model's tool calls.
    scenario = {"spec": "habits/0.1", "start": "deliver", "fallback": "stuck", "nodes": {
        "deliver": {"do": "terminal", "args": {"command": "hermes send -t telegram MEDIA:/x.md"}, "next": "finished"},
        "say": {"do": "send_message", "args": {"text": "x"}, "next": "finished"},
        "finished": {"end": "Sent."}, "stuck": {"wake": "No."}}}
    for given in (scenario, json.dumps(scenario)):
        found = bench.forbidden([("habit_run", {"scenario": given})], ["hermes send", "send_message", "ssh"])
        assert found == ["its habit_run ran hermes send (hermes send -t telegram MEDIA:/x.md), which the case forbids",
                         "its habit_run called send_message, which the case forbids"]
    assert bench.forbidden([("habit_run", {"name": "x"})], ["hermes send"]) == []


# ---- What a model turn can see ---------------------------------------------------------------------

def test_a_model_turn_runs_in_a_fresh_empty_root_far_from_the_answer_key(fake, tmp_path, capsys):
    # 6 of 8 create turns read the bench's copy of the reference habit, which sat in their own folder, and
    # one walked up from it to the scenario file and a prove.py.
    run, calls, home, user, case, records, configure = fake
    assert run("--budget", "0.05") == 0
    printed, _ = records(capsys)
    scratch = user / "tmp" / "bench" / "tidy"
    copy = Path([c for c in calls() if c["argv"][:2] == ["habits", "run"]][0]["argv"][2])
    model = turns(calls)
    assert len(model) == 2 and len({t["cwd"] for t in model}) == 2  # a root of its own for each turn
    for turn, record in zip(model, [r for r in printed if r["kind"] in ("baseline", "create")]):
        cwd = Path(turn["cwd"])
        assert cwd.name == "tidy" and cwd.parent.name.startswith("hb-") and cwd.parent.parent == user / "tmp" / "habits-bench-turns"
        assert record["root"] == str(cwd) and str(ROOT) not in str(cwd)
        usage = Path(turn["argv"][turn["argv"].index("--usage-file") + 1])
        assert usage.parent == cwd.parent and cwd not in copy.parents and cwd.parent not in copy.parents
        assert len(turn["around"]) == 5  # tidy, hb-…, the turns' folder, the temp folder, the home folder, as the turn saw them
        for folder, names in turn["around"].items():  # up to the home folder
            assert not set(names) & {"reflex.yml", "SKILL.md", ".bench", "cases.json", "suite"}, folder
            assert not [n for n in names if n.endswith((".yaml", ".yml"))] and "run.py" not in names
        assert set(turn["inside"]) == {"inbox", "a.pdf"}  # what its setup made, nothing more
        assert not cwd.parent.exists()  # gone once its record was out
        assert f"Read nothing outside {cwd};" in turn["argv"][1] and str(scratch) not in turn["argv"][1]
    assert model[0]["argv"][1].startswith(f"Sort {model[0]['cwd']}/inbox ")  # {scratch} is the turn's root


def test_no_habit_file_sits_in_the_temp_folder_while_a_model_turn_runs(fake, tmp_path, monkeypatch, capsys):
    # The rewritten reference sat in the bench's work folder, in $TMPDIR, from a case's first
    # run until the bench ended; a turn's own temp files go there too. The copies a create made, kept for its works
    # check, sat there for the next create.
    run, calls, home, user, case, records, configure = fake
    temp = user / "tmp"  # the fixture's temp folder: the turns' roots and the scratch folders hold no habit file
    monkeypatch.setenv("FAKE_SCAN_TMP", "1")
    assert run("--budget", "0.05", cases=[{**case, "create": {**case["create"], "repeat": 2}}]) == 0
    model = turns(calls)
    assert len(model) == 3 and [t["temp"] for t in model] == [[], [], []]
    runs = [c for c in calls() if c["argv"][:2] == ["habits", "run"]]
    assert all(Path(c["argv"][2]).is_relative_to(temp) for c in runs)  # the copies did sit there, between turns
    printed, _ = records(capsys)
    assert [r["works"] for r in printed if r["kind"] == "create"] == [True, True]  # each create still checked


def test_no_model_turn_runs_while_an_old_copy_of_a_habit_sits_near_its_root(fake, tmp_path, capsys):
    # The roots' folder is the grandparent of every fresh root: `ls ../..` then a find would give the answer.
    run, calls, home, user, case, records, configure = fake
    old = user / "tmp" / "habits-bench-turns" / "habits-bench" / "dashboard-publish" / "published" / ".bench" / "dashboard-publish" / "assets"
    old.mkdir(parents=True)
    (old / "reflex.yml").write_text("spec: habits/0.1\n")
    other = user / "tmp" / "habits-bench-turns" / "habits-demo" / "zz" / "assets"  # another habit: an example, as the plugin ships
    other.mkdir(parents=True)
    (other / "reflex.yml").write_text("spec: habits/0.1\n")
    assert run("--budget", "0.05") == 1
    printed, err = records(capsys)
    turned = [r for r in printed if r["kind"] in ("baseline", "create")]
    assert len(turned) == 2 and not turns(calls)  # no turn ran, none was paid
    assert all(not r["ok"] and ".bench" in r["why"][0] and "move or delete them first" in r["why"][0] for r in turned)
    assert "no model turn while these sit within 6 folders" in err
    assert all(r["ok"] for r in printed if r["kind"] == "run")  # habit runs need no such care
    assert bench.main([str(tmp_path / "suite" / "cases.json"), "--dry-check", "--hermes", "x"]) == 0
    assert "warning: no model turn will run" in capsys.readouterr().err
    copy = user / "tmp" / "habits-bench-turns" / "old" / "tidy-downloads" / "assets"  # a copy of the case's own habit
    copy.mkdir(parents=True)
    (copy / "reflex.yml").write_text("spec: habits/0.1\n")
    shutil.rmtree(user / "tmp" / "habits-bench-turns" / "habits-bench")
    assert run("--budget", "0.05", "--no-baseline") == 1
    create = [r for r in records(capsys)[0] if r["kind"] == "create"][0]
    assert "old/tidy-downloads/assets/reflex.yml" in create["why"][0] and "habits-demo" not in create["why"][0]
    shutil.rmtree(user / "tmp" / "habits-bench-turns" / "old")
    assert run("--budget", "0.05", "--no-baseline") == 0  # an example habit nearby stops nothing


def test_a_turn_never_finds_what_an_earlier_turn_wrote(fake, tmp_path, capsys):
    run, calls, home, user, case, records, configure = fake
    configure("turn.json", {"writes": {"{cwd}/reflex.yml": "spec: habits/0.1\n"}})  # a create's draft, in its root
    assert run("--budget", "0.05", cases=[{**case, "create": {**case["create"], "repeat": 2}}]) == 0
    model = turns(calls)
    assert len(model) == 3 and all(set(t["inside"]) == {"inbox", "a.pdf"}
                                   for t in model)
    assert not list((user / "tmp" / "habits-bench-turns").glob("hb-*"))


@pytest.mark.skipif(not shutil.which("lsof"), reason="lsof is not installed")
def test_lsof_on_the_bench_names_neither_its_folder_nor_its_results(fake, tmp_path, monkeypatch, capsys):
    # Hide() kept the scenario path out of ps, but lsof on the bench gave its cwd (the
    # tasks folder) and its stdout and stderr files (results/…). With --out and --log, nothing is held open,
    # and the bench works from /.
    run, calls, home, user, case, records, configure = fake
    monkeypatch.setenv("FAKE_LSOF", "1")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "results").mkdir()
    assert run("--budget", "0.05", "--out", "results/x.jsonl", "--log", "results/x.stderr") == 0
    assert os.getcwd() == str(tmp_path)  # given back to its caller
    model = turns(calls)
    assert len(model) == 2
    for turn in model:
        names = [line[1:] for line in turn["lsof"].splitlines() if line.startswith("n")]
        assert names[0] == "/" and not [n for n in names if str(tmp_path) in n], names
    records_ = [json.loads(line) for line in (tmp_path / "results" / "x.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in records_] == ["run", "run", "baseline", "create"]
    log = (tmp_path / "results" / "x.stderr").read_text()
    assert "bench: 1 cases, 2 habit runs" in log and "model turns: 2" in log and "case " in log  # notes and table


def test_a_turn_on_a_folder_its_setup_builds_is_told_to_stay_there(fake, tmp_path, capsys):
    run, calls, home, user, case, records, configure = fake
    assert run("--no-create") == 0
    turn, = turns(calls)
    root = turn["cwd"]
    assert turn["argv"][1] == (f"Sort {root}/inbox Read nothing outside {root}; your test files go in "
                               f"{root}.")
    capsys.readouterr()
    # A case that reads the real machine (no setup) keeps its prompt: the line would forbid its task.
    (tmp_path / "sante" / "assets").mkdir(parents=True)
    (tmp_path / "sante" / "assets" / "reflex.yml").write_text("spec: habits/0.1\n")
    alone = {k: v for k, v in case.items() if k not in ("setup", "rewrite", "scratch")}
    assert run("--no-create", cases=[{**alone, "habit": str(tmp_path / "sante" / "assets" / "reflex.yml"),
                                      "baseline": {"prompt": "Hermes va bien ?"}}]) == 0
    assert turns(calls)[-1]["argv"][1] == "Hermes va bien ?" and "/hb-" in turns(calls)[-1]["cwd"]
    # A prompt that says it already is not told twice.
    for told in (f"Sort {{scratch}}/inbox. ({bench.FENCE})", "Sort {scratch}/inbox. (Read nothing outside "
                 "{scratch} and ~/Documents/vault; your test files go in {scratch}.)"):
        assert run("--no-create", cases=[{**case, "baseline": {"prompt": told}}]) == 0
        assert turns(calls)[-1]["argv"][1] == told.replace("{scratch}", turns(calls)[-1]["cwd"])


def test_a_process_list_shows_no_scenario_path(fake, tmp_path):
    # A model turn that runs ps sees the bench's command line: no scenario file, no bench file.
    run, calls, home, user, case, records, configure = fake
    scenario = tmp_path / "suite" / "secret-scenario.json"
    scenario.parent.mkdir()
    scenario.write_text(json.dumps([{**case, "repeat": 1}]))
    proc = subprocess.run([sys.executable, str(ROOT / "bench" / "run.py"), "secret-scenario.json", "--no-create",
                           "--hermes", str(tmp_path / "hermes"), "--home", str(home), "--budget", "0.05"],
                          cwd=scenario.parent, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert [json.loads(line)["kind"] for line in proc.stdout.splitlines()] == ["run", "baseline"]
    shown = turns(calls)[0]["parent"]
    assert shown and "secret-scenario" not in shown and "run.py" not in shown and str(tmp_path) not in shown
    assert "suite" not in shown


def test_an_installed_plugin_that_ships_its_bench_is_said(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    (home / "plugins" / "habits" / "bench").mkdir(parents=True)
    assert run("--no-create") == 0
    assert "plugins/habits ships bench/" in records(capsys)[1]
    assert run("--no-baseline", "--no-create") == 0
    assert "ships bench/" not in records(capsys)[1]  # no model turn: nothing to say


def test_a_watch_is_measured_on_its_checks_that_end_and_a_night_line():
    # One bench run's only baselines sat on wake cases, where both sides pay one Hermes turn: 0.9x by construction.
    def runs(case, status, n, wall):
        return [{"case": case, "task": "watch", "kind": "run", "ok": True, "status": status, "ms": 300,
                 "wall_ms": wall, "model_calls": 0, "habit_kind": "watch", "expect": [status]} for _ in range(n)]

    def base(case, seconds, calls):
        return {"case": case, "task": "watch", "kind": "baseline", "ok": True, "session": "s", "seconds": seconds,
                "api_calls": calls}
    records = (runs("e-already", "end", 2, 1800) + [base("e-already", 25.5, 6)] + runs("e-child", "end", 1, 1800)
               + [base("e-child", 25.5, 6)] + runs("t-day", "end", 1, 1800)  # no baseline: no figure
               + runs("p-done", "wake", 3, 1800) + [base("p-done", 18.7, 5)])
    rows = bench.summary(records)
    deja, enfant, jour, fini = rows
    assert (deja["side"], fini["side"]) == ("end", "wake")
    assert deja["speedup"] == pytest.approx(25.5 / 1.8) and deja["avoided"] == 1
    assert fini["speedup"] is None and fini["avoided"] is None and fini["base_s"] == 18.7  # apart: a turn each side
    night, = bench.nights(rows, 40, 2)
    assert (night["task"], night["checks"], night["wakes"]) == ("watch", 40, 2)
    assert night["cases"] == ["e-already", "e-child"] and night["woke"] == ["p-done"]
    assert night["speedup"] == pytest.approx(40 * 25.5 / (40 * 1.8 + 2 * 25.5)) and 8 < night["speedup"] < 8.5
    assert night["avoided"] == pytest.approx(1 - 2 / 40)
    text = bench.table(rows)
    assert text.splitlines()[4].split()[-4:] == ["(wake)", "-", "(wake)", "-"]  # speedup, engine, avoided, created
    line, = bench.night_lines([night])
    assert line.startswith("watch: a night of 40 checks, 2 wakes: 8.3x, 95% calls avoided") and "p-done" in line
    # Not a watch: no side, no night.
    alone = bench.summary([{**r, "habit_kind": "request"} for r in records])
    assert {r["side"] for r in alone} == {None} and bench.nights(alone, 40, 2) == []
    assert bench.night_lines(bench.nights(bench.summary(runs("t-day", "end", 1, 1800)), 40, 2)) == [
        "watch: no end case with a right baseline, so no night figure"]


def test_a_watch_run_that_says_no_expected_side_gives_no_watch_figure():
    # Run records carry no expect. Summarized again, every watch wake case went on
    # the end side, with speedups and a night figure for cases that should only wake.
    old = [{"case": "p-done", "task": "watch", "kind": "run", "ok": True, "status": "wake", "ms": 300,
            "wall_ms": 20000, "model_calls": 0, "habit_kind": "watch"},
           {"case": "p-done", "task": "watch", "kind": "baseline", "ok": True, "session": "s", "seconds": 18.7,
            "api_calls": 5}]
    row, = bench.summary(old)
    assert row["side"] == "unknown" and row["speedup"] is None and row["avoided"] is None
    assert bench.nights([row], 40, 2) == []
    assert bench.table([row]).splitlines()[1].split()[-4:] == ["(side?)", "-", "(side?)", "-"]


def test_the_night_line_is_printed_under_the_table(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    configure("run.json", {"kind": "watch"})
    assert run("--no-create", "--night", "24,1") == 0
    assert "tidy-folder: a night of 24 checks, 1 wake: " in records(capsys)[1]
    for wrong in ("2,5", "40", "0,0"):
        with pytest.raises(SystemExit):
            run("--night", wrong)


# ---- Creates scored honestly (real call shapes) ------------------------------------

def said(tool, args, reply, by=None):
    """A recorded call: its tool, arguments, reply text and the tool the reply came back from."""
    return {"id": "c", "tool": tool, "args": args, "reply": reply if isinstance(reply, str) else json.dumps(reply),
            "from": by or tool}


S = "/private/tmp/scratchpad/real/mac-agents-status/c-status"
SAFETY = ('safety: "scan" is marked read: true but may write: ' + S + '/bin/hc is a path used as the command: '
          'only a reader in PURE or with a rule')
BROKEN = '[{"name": "habit_save", "arguments": {"name": "vault-find-send-made", "description": "Send me'
NOT_JSON = {"error": "tool_call 'calls' is not valid JSON: Expecting ',' delimiter: line 1 column 4067 (char 4066)"}
MAC_CREATE = [said("terminal", {"command": f"cd {S} && hermes habits check {S}/status.yaml; echo exit=$?"},
           {"output": SAFETY + "\nexit=1", "exit_code": 0}),
      said("terminal", {"command": f"cd {S} && hermes habits check {S}/status.yaml; echo exit=$?"},
           {"output": "valid: the graph follows every rule.\nexit=0", "exit_code": 0}),
      said("terminal", {"command": f"cd {S} && hermes habits run {S}/status.yaml --json"},
           {"output": '{"habit": "c-status", "kind": "request", "mode": "dry", "status": "end"}', "exit_code": 0}),
      said("habit_run", {"scenario": {"spec": "habits/0.1"}}, {"status": "end", "message": "Your agents on the Mac"}),
      said("habit_save", {"name": "bench-mac-agents-status", "scenario": {"spec": "habits/0.1"}},
           {"status": "saved", "habit": "bench-mac-agents-status", "kind": "request"})]
FIND_CREATE = [said("tool_call", {"calls": BROKEN}, NOT_JSON)] * 3 + [
    said("habit_save", {"name": "vault-find-send-made", "scenario": {"spec": "habits/0.1"}},
         {"status": "saved", "habit": "vault-find-send-made", "kind": "request"}),
    said("terminal", {"command": "hermes habits check /Users/alex/.hermes/skills/vault-find-send-made/assets/reflex.yml"},
         {"error": "vault-find-send-made is a habit: the Habits plugin checks and versions its files"})]
PROBE = '[{"arguments": {"description": "probe", "name": "probe-simple", "procedure": "probe", "scenario": {"'
D5 = "/tmp/habits-bench/vault-watch-triage/hour-kind/watch.yaml"
CHECKED = said("terminal", {"command": f"hermes habits check {D5}"},
               {"output": "valid: the graph follows every rule.", "exit_code": 0})
# A real create of the watch: its draft checked valid, then run (wake), then 4 saves that never
# reached the plugin, a check again, and probes under other names. It never saved.
WATCH_CREATE = [CHECKED, said("terminal", {"command": f"hermes habits run {D5} --json"},
                    {"output": '{"habit": "watch", "kind": "watch", "status": "wake"}', "exit_code": 0})] + [
    said("tool_call", {"calls": BROKEN.replace("vault-find-send-made", "bench-vault-watch-triage")}, NOT_JSON)] * 4 + [
    CHECKED,
    said("habit_save", {"name": "probe-string-scenario", "scenario": "spec: habits/0.1\nstart: x"},
         {"error": "tool_call to 'habit_save' failed argument validation at arguments.scenario (type)"}, "tool_call"),
    said("tool_call", {"calls": BROKEN.replace("vault-find-send-made", "bench-vault-watch-triage")}, NOT_JSON),
    said("tool_call", {"calls": PROBE}, NOT_JSON),
    said("habit_save", {"name": "probe-empty", "scenario": {}},
         {"status": "refused", "issues": ['format: spec must be "habits/0.1".']}),
    said("habit_save", {"name": "probe-onekey", "scenario": {"spec": "habits/0.1"}},
         {"status": "refused", "issues": ["format: nodes must be a non-empty mapping of named nodes."]})]


def test_first_try_is_the_first_graph_the_plugin_judged_and_save_tries_count_what_never_landed():
    # MAC_CREATE: its first check said safety:, the next one valid: its first graph was not valid.
    assert bench.first_try(MAC_CREATE, "bench-mac-agents-status") is False
    assert bench.first_try(MAC_CREATE[1:], "bench-mac-agents-status") is True
    assert bench.save_tries(MAC_CREATE, "bench-mac-agents-status") == 1
    # FIND_CREATE: three saves that never reached the plugin (calls was no JSON), then saved: valid at the first graph judged.
    assert bench.first_try(FIND_CREATE, "vault-find-send-made") is True and bench.save_tries(FIND_CREATE, "vault-find-send-made") == 4
    # WATCH_CREATE: its first graph judged was valid (a terminal check), but 5 tries under its name never landed, and the
    # probes under other names (refused) are no graph of its own. The record's first_try needs the habit saved too.
    assert bench.first_try(WATCH_CREATE, "bench-vault-watch-triage") is True
    assert bench.first_try(WATCH_CREATE[2:6] + WATCH_CREATE[7:], "bench-vault-watch-triage") is False  # nothing of its own judged
    assert bench.save_tries(WATCH_CREATE, "bench-vault-watch-triage") == 5
    landed = said("habit_save", {"name": "bench-vault-watch-triage", "scenario": {"spec": "habits/0.1"}},
                  {"status": "saved", "habit": "bench-vault-watch-triage", "kind": "watch"})
    assert bench.first_try(WATCH_CREATE[2:6] + WATCH_CREATE[7:] + [landed], "bench-vault-watch-triage") is True
    # A habit_run with a scenario is a draft, whatever its name; one by name is not.
    refused = {"status": "refused", "issues": ["rule 3 (exits): x has no exit"]}
    assert bench.first_try([said("habit_run", {"scenario": "spec: habits/0.1"}, refused)] + FIND_CREATE, "x") is False
    assert bench.first_try([said("habit_run", {"name": "sort-demo"}, refused), landed],
                           "bench-vault-watch-triage") is True
    # What a terminal check or run says, line by line.
    for output, verdict in (("valid: the graph follows every rule.", True), (SAFETY, False),
                            ("rule 2 (exits): node a has no exit", False), ("format: nodes must be a mapping", False),
                            ("format: [Errno 2] No such file or directory: 'x.yaml'", None),
                            # a draft in Hermes's own scratch folder, refused for its place
                            ("format: file /Users/alex/.hermes/cache/scratch/b/reflex.yml looks like a secrets or "
                             "config file (in Hermes's home, beside its config file and keys; only a skill's "
                             "assets/reflex.yml is read there), not a habit graph", None),
                            ("format: file x.txt is not a graph: give a .yaml, .yml or .json file", None),
                            ('{"status": "refused", "message": "invalid graph: rule 1"}', False),
                            ('{"status": "refused", "message": "no file /x.yaml"}', None),
                            ('{"habit": "x", "status": "wake", "message": "?"}', True),
                            ("x · request · dry run: writes planned, not done\n…", True), ("", None),
                            # the answer first, then the summary line: dry, live, or a habit that only reads
                            ("Rangés.\nx · done in 0.2 s · 6 questions (llm) · 2 tool calls · dry run: writes planned, "
                             "not done\nsteps:", True),
                            ("Seen.\nx · handed back in 0.1 s · 0 questions · 1 tool call · it only reads: nothing "
                             "to write", True), ("Done.\nx · done in 1.0 s · 0 questions · 3 tool calls · live run", True)):
        assert bench.verdict(output) is verdict, output
    # Another program's output in the same call is not the check's word.
    mixed = 'hour-kind OK\n{"status": "wake"}\n=== check ===\n' + SAFETY
    assert bench.verdict(mixed, ["check"]) is False and bench.verdict(mixed, ["run"]) is True
    assert bench.first_try([said("terminal", {"command": "hermes habits check --help"}, {"output": SAFETY})], "x") is False
    assert bench.first_try([said("terminal", {"command": "hermes habits run sort-demo"}, {"output": SAFETY})],
                           "x") is False  # a name, not a draft file: nothing judged, nothing valid


def test_a_create_that_saved_nothing_is_never_valid_at_the_first_try(fake, capsys):
    # Create checked its draft valid first, then never saved: main gave it
    # first_try true with valid false, and the table read "0/1 valid, 1 first try".
    run, calls, home, user, case, records, configure = fake
    configure("turn.json", {"saves": False, "calls": [
        {"tool": "terminal", "direct": True, "args": {"command": f"hermes habits check {D5}"},
         "reply": {"output": "valid: the graph follows every rule.", "exit_code": 0}},
        {"tool": "habit_save", "broken": True, "from": "tool_call", "reply": NOT_JSON}]})
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 1
    printed, err = records(capsys)
    assert (printed[-1]["valid"], printed[-1]["first_try"]) == (False, False)
    assert "0/1 valid, 0 first try" in err
    # Records written before count a first try only among the valid creates.
    row, = bench.summary([{"case": "c", "task": "t", "kind": "create", "ok": False, "valid": False, "first_try": True}])
    assert (row["created"], row["valid"], row["first_try"]) == (1, 0, 0)


def test_a_skill_that_was_there_before_the_turn_is_never_taken_away(fake, capsys):
    # A save under the name of a plain skill (no habit) landed in its folder, the plugin's
    # edit fallback, and the bench moved the whole folder, the user's script with it, to the Trash.
    run, calls, home, user, case, records, configure = fake
    mine = home / "skills" / "notes" / "probe-x"
    (mine / "scripts").mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: probe-x\n---\n")
    (mine / "scripts" / "tool.sh").write_text("echo mine\n")
    configure("turn.json", {"also": ["probe-x"], "paths": {"probe-x": "notes/probe-x"}})
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 0
    create = records(capsys)[0][-1]
    assert (mine / "scripts" / "tool.sh").read_text() == "echo mine\n" and (mine / "SKILL.md").exists()
    assert create["also_saved"] == ["probe-x"] and "was there before the turn" in create["clean_error"]
    assert not (home / "skills" / "bench-x").exists()  # its own habit still went


def test_a_create_turn_reads_string_calls_and_counts_its_save_tries(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    broken = {"tool": "habit_save", "broken": True, "from": "tool_call", "reply": NOT_JSON}
    saved = {"tool": "habit_save", "scenario": True, "string": True,
             "reply": {"status": "saved", "habit": "bench-x", "kind": "one-off"}}
    configure("turn.json", {"calls": [broken, broken, saved]})
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 0
    create = records(capsys)[0][-1]
    assert (create["save_tries"], create["first_try"], create["statuses"], create["valid"]) == (3, True, ["saved"], True)
    configure("turn.json", {"calls": [{"tool": "habit_save", "scenario": True, "string": True,
                                       "reply": {"status": "refused", "issues": ["rule 3"]}}, saved]})
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 0
    printed, err = records(capsys)
    assert (printed[-1]["save_tries"], printed[-1]["first_try"]) == (2, False)
    assert "1/1 valid, 0 first try, 1 work, 2 save tries" in err


def test_works_takes_its_own_expect_mode_rewrite_hour_and_message(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    request = {**case, "repeat": 1, "baseline": None, "message": "sort my downloads",
               "verify": 'test -f "$SCRATCH/inbox/a.pdf"'}
    configure("turn.json", {"kind": "request", "habit": "spec: habits/0.1\ncommand: ls -p ~/Downloads\n"})
    assert run(cases=[{**request, "create": {**case["create"], "expect": {"wake": {}}}}]) == 0  # works is no ok
    create = records(capsys)[0][-1]
    assert create["works"] is False and create["works_why"] == ["status end, expected wake"]
    ran = [c for c in calls() if c["argv"][:2] == ["habits", "run"]][-1]
    root = create["root"]
    # The case's rewrite reaches the copy Hermes made: it reads the root, not ~/Downloads, and runs in it.
    assert f"ls -p {root}/inbox" in ran["text"] and ran["cwd"] == root and "--message" not in " ".join(ran["argv"])
    assert Path(create["kept"]) not in Path(ran["argv"][2]).parents and "--live" not in ran["argv"]
    assert ran["tz"] == os.environ.get("TZ")  # no hour asked: the machine's own zone, untouched
    made = {**case["create"], "mode": "live", "hour": 2, "message": True,
            "expect": {"end": {"message_contains": "sorted"}}}
    assert run(cases=[{**request, "create": made}]) == 0
    create = records(capsys)[0][-1]
    assert create["works"] is True and create["works_status"] == "end"
    ran = [c for c in calls() if c["argv"][:2] == ["habits", "run"]][-1]
    assert ran["argv"][-3:] == ["--message=sort my downloads", "--live", f"--within={create['root']}"]
    hour = subprocess.run(["date", "+%H"], env={**os.environ, "TZ": ran["tz"]}, capture_output=True, text=True)
    assert hour.stdout.strip() == "02"  # the hour a night check sees
    assert run(cases=[{**request, "verify": "false", "create": made}]) == 0  # live: the case's verify judges it too
    create = records(capsys)[0][-1]
    assert create["works"] is False and create["works_why"][0].startswith("verify failed")
    configure("turn.json", {"kind": "watch"})  # a watch hears no message
    assert run(cases=[{**request, "create": made}]) == 0
    assert "only a request habit starts from a message" in records(capsys)[0][-1]["works_why"][0]


def test_works_copies_the_habits_own_scripts_and_points_its_paths_at_the_copy(fake, capsys):
    # T8: Hermes saved its checks as scripts in the habit's assets/ and named them by their saved path.
    # works copied only reflex.yml and SKILL.md, so --within refused check_markers.py as outside the root, and the
    # saved folder had gone to the Trash anyway.
    run, calls, home, user, case, records, configure = fake
    saved = home / "skills" / "bench-x" / "assets"
    configure("turn.json", {"habit": f"spec: habits/0.1\ncommand: python3 {saved}/check.py && sh {saved}/run.sh\n",
                            "assets": {"check.py": f"open('{saved}/data.txt')\nprint('~/Downloads')\n",
                                       "run.sh": "echo ok\n", "data.txt": "x\n", "logo.png": "\xff\x00\xd8"}})
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 0
    create = records(capsys)[0][-1]
    assert create["works"] is True
    ran = [c for c in calls() if c["argv"][:2] == ["habits", "run"]][-1]
    copy = Path(ran["argv"][2]).parent  # the copy's assets/, in the bench's own folder
    assert f"python3 {copy}/check.py && sh {copy}/run.sh" in ran["text"] and str(saved) not in ran["text"]
    assert ran["assets"]["check.py"][0] == f"open('{copy}/data.txt')\nprint('{create['root']}/inbox')\n"
    assert ran["assets"]["run.sh"] == ["echo ok\n", True] and ran["assets"]["logo.png"][0] == "\xff\x00\xd8"
    assert ran["assets"]["data.txt"] == ["x\n", False] and Path(create["kept"]) not in copy.parents
    # A script that names a Downloads folder the case's rewrite does not map is never run, like such a graph.
    configure("turn.json", {"assets": {"x.sh": 'ls "$HOME/Downloads"\n'}})
    assert run("--no-baseline", cases=[{**case, "repeat": 1}]) == 0
    create = records(capsys)[0][-1]
    assert create["works"] is None and create["works_why"] == ["it names a Downloads folder: not run"]


def test_a_folder_is_named_whole_resolved_and_from_the_home_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert bench.spelled(tmp_path / "link" / "x") == [f"{tmp_path}/link/x/", f"{tmp_path.resolve()}/real/x/",
                                                      "~/link/x/", "$HOME/link/x/", "~/real/x/", "$HOME/real/x/"]
    assert bench.spelled(Path("/opt/x")) == ["/opt/x/"]


def test_works_first_runs_one_check_before_the_cases_last_change(fake, capsys):
    # A bench run: Hermes's watch woke on what changed since its last check; with no check before, the case's
    # vault was its first reference and it ended. create.before builds the state before the change.
    run, calls, home, user, case, records, configure = fake
    configure("turn.json", {"kind": "watch"})
    configure("run.json", {"status": "wake", "kind": "watch"})
    made = {**case["create"], "before": 'echo before > "$SCRATCH/peek"', "expect": {"wake": {}}}
    watch = {**case, "repeat": 1, "baseline": None, "setup": case["setup"] + ' && echo after > "$SCRATCH/peek"',
             "expect": {"wake": {}}}
    assert run(cases=[{**watch, "create": made}]) == 0
    create = records(capsys)[0][-1]
    assert create["works"] is True and create["works_before"] == "wake"  # the first check's status, not judged
    first, judged = [c for c in calls() if c["argv"][:2] == ["habits", "run"]][-2:]
    assert (first["peek"], judged["peek"]) == ("before\n", "after\n") and first["argv"][2] == judged["argv"][2]
    memory = [a for a in first["argv"] if a.startswith("--memory=")]
    assert memory and memory == [a for a in judged["argv"] if a.startswith("--memory=")]  # the same references
    assert Path(create["root"]) not in Path(memory[0][9:]).parents  # in the bench's own folder, not the turn's
    assert bench.problems({**watch, "create": {**made, "before": 3}}) == ["create.before is a shell command (text)"]
    bare = {k: v for k, v in case.items() if k not in ("scratch", "setup", "rewrite")}
    assert bench.problems({**bare, "create": made}) == ["create.before needs a scratch folder"]


def test_a_baseline_may_have_its_own_verify_and_a_run_keeps_its_llm_times(fake, capsys):
    # Conflit-verrou: Hermes alone stopped at the held lock and wrote no note, as the vault's AGENTS.md
    # says; the case's verify, the habit's own check, wanted the note.
    run, calls, home, user, case, records, configure = fake
    configure("run.json", {"llm_ms": {"rediger": 812}})
    stops = {**case, "repeat": 1, "verify": "false", "baseline": {**case["baseline"], "verify": "true"}}
    assert run("--no-create", cases=[stops]) == 0
    printed = records(capsys)[0]
    assert printed[0]["llm_ms"] == {"rediger": 812} and printed[1]["kind"] == "baseline" and printed[1]["ok"]
    assert run("--no-create", cases=[{**stops, "baseline": case["baseline"]}]) == 1  # the case's verify judges it
    assert records(capsys)[0][-1]["why"][0].startswith("verify failed")
    assert bench.problems({**stops, "baseline": {"prompt": "p", "verify": ["x"]}}) == [
        "baseline.verify is a shell command (text)"]


def test_a_rewrite_leaves_a_path_that_names_its_stand_in_already():
    pairs = {"~/": "/r/home/", "ob sync": "/r/bin/ob sync"}
    # The reference names the real ob; a habit Hermes made from the prompt names the root's stand-in already.
    assert bench.rewritten("ob sync --path ~/vault && ob sync", pairs) == (
        "/r/bin/ob sync --path /r/home/vault && /r/bin/ob sync")
    assert bench.rewritten("/r/bin/ob sync --path /r/home/vault; ob sync", pairs) == (
        "/r/bin/ob sync --path /r/home/vault; /r/bin/ob sync")
    assert bench.rewritten("hc ls --json | jq .", {"hc ls --json | jq": "cat /r/hc/ls.json | jq"}) == (
        "cat /r/hc/ls.json | jq .")
    assert bench.rewritten("a . >= 23 b", {". >= 23": "true"}) == "a true b"


def test_a_create_part_is_checked_like_a_case():
    good = {"id": "a", "task": "t", "habit": "h", "expect": {"end": {}}, "scratch": "/tmp/x",
            "create": {"prompt": "p", "name": "n"}}
    assert bench.problems({**good, "message": "m", "create": {**good["create"], "expect": {"end": {}, "none": {}},
                                                              "mode": "live", "hour": 2, "message": True}}) == []
    assert bench.problems({**good, "create": {**good["create"], "expect": {"status": "end"}, "mode": "shadow",
                                              "hour": 24, "message": "x"}}) == [
        "unknown create.expect key status",
        "create.expect names end, wake or none, the statuses that count as right (wake: {} takes any clean hand-back)",
        "create.mode is dry or live", "create.hour is the hour the habit made runs at, 0 to 23",
        "create.message is true: the habit made then runs from the case's message"]
    assert bench.problems({**good, "create": {**good["create"], "message": True}}) == [
        "create.message needs the case's message"]
    now = 1762659000  # 2025-11-09 03:30 UTC
    for hour in range(24):
        zone = bench.zone(hour, now)  # POSIX: local time is UTC minus the offset
        assert re.fullmatch(r"HAB[+-]\d+", zone) and (3 - int(zone[3:])) % 24 == hour and abs(int(zone[3:])) <= 12


# ---- False counts --------------------------------------------------------------------------------------

def test_execute_code_that_runs_a_forbidden_command_counts(tmp_path):
    scratch = tmp_path / "scratch"
    for code, item, found in (
            ('from hermes_tools import terminal\nterminal("cd ~/Documents/vault && ob sync")', "ob", True),
            ('import subprocess\nsubprocess.run(["ob", "sync", "--path", "/x"], check=True)', "ob", True),
            ('import os\nos.system("hermes send -t telegram MEDIA:/x.md")', "hermes send", True),
            ('from hermes_tools import send_message\nsend_message(target="telegram", message="x")', "send_message", True),
            (f'from hermes_tools import terminal\nT = "{scratch}"\nterminal(command=f"{{T}}/bin/ob sync")', "ob", False),
            ('import shutil\nprint(shutil.which("ob"), "ob sync")', "ob", False),  # named, never run
            ('terminal("ob sync" if x else "ls")', "ob", False),  # what it runs is not a plain text
            ("terminal('ob sync'", "ob", True)):  # no Python: searched as text
        assert bool(bench.forbidden([("execute_code", {"code": code})], [item], scratch)) is found, code
    said, = bench.forbidden([("execute_code", {"code": 'terminal("ob sync")'})], ["ob"], scratch)
    assert said == "it ran ob through execute_code (ob sync), which the case forbids"


def test_a_live_runs_dry_pass_is_not_its_time_and_its_model_calls_are_paid(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    configure("run.json", {"plan_ms": 1000, "plan_model_calls": 2, "model_calls": 1})
    assert run("--no-baseline", "--no-create", cases=[{**case, "repeat": 1, "mode": "live"}]) == 0
    printed, err = records(capsys)
    assert printed[0]["plan_ms"] == 1000 and printed[0]["wall_ms"] < 1000 <= printed[0]["wall_ms"] + 1000
    assert printed[0]["model_calls"] == 1 and printed[0]["plan_model_calls"] == 2
    assert ("a habit's own model calls: 3 (0 by llm nodes, 1 by questions no decision model took, 2 in live runs' "
            "dry passes)") in err  # the dry pass's calls cost money too


def test_the_cli_says_how_long_a_live_runs_dry_pass_took(setup, monkeypatch, tmp_path, capsys):
    # Here rather than in test_plugin.py, whose helpers it borrows: another branch edits that file meanwhile.
    ctx, habits = setup
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    plugin_tests.with_habit(tmp_path, monkeypatch, "sort-scratch", plugin_tests.scratch_demo(tmp_path))
    code, out, _ = plugin_tests.cli_run(habits, capsys, "sort-scratch", live=True, within=str(scratch))
    assert code == 0 and isinstance(out["plan_ms"], int) and out["plan_model_calls"] == 0
    code, out, _ = plugin_tests.cli_run(habits, capsys, "tidy-downloads")
    assert code == 0 and (out["plan_ms"], out["plan_model_calls"]) == (0, 0)  # dry: no dry pass first


# ---- Hygiene ----------------------------------------------------------------------------------------

def test_a_turn_the_bench_cut_is_waited_for_before_its_habit_is_cleaned(fake, capsys, monkeypatch):
    # T8's create was cut at 600 s and answered at 603 s, after the bench had put its skill in the Trash.
    run, calls, home, user, case, records, configure = fake
    monkeypatch.setattr(bench, "LATE", 20)
    configure("turn.json", {"late": 3})  # it answers 1 s after the bench cut it at 2 s
    began = time.monotonic()
    assert run("--no-baseline", cases=[{**case, "repeat": 1, "timeout": 2}]) == 1
    create = records(capsys)[0][-1]
    assert create["why"][0] == "hermes -z exit None: timed out after 2 s" and 0 < create["late_s"] < 20
    assert create["answer"] == "Fini tard." and time.monotonic() - began < 15  # waited for it, not for LATE
    assert create["kept"] and not (home / "skills" / "bench-x").exists()  # cleaned once it had answered
    monkeypatch.setattr(bench, "LATE", 1)
    configure("turn.json", {"late": 5})
    assert run("--no-baseline", "--no-create", cases=[{**case, "repeat": 1, "timeout": 2}]) == 0  # no turn: no wait
    assert run("--no-create", cases=[{**case, "repeat": 1, "timeout": 2}]) == 1
    printed, err = records(capsys)
    assert printed[-1]["late_s"] is None and "no answer 1 s after the bench cut it" in err


def test_a_habit_a_cut_create_saves_late_never_stays_installed(fake, capsys, monkeypatch):
    # Hermes 0.23's gateway goes on with a turn the bench killed; a habit_save that landed
    # after the 60 s wait stayed installed, a watch's cron job with it, and a create.repeat > 1 was refused.
    run, calls, home, user, case, records, configure = fake
    monkeypatch.setattr(bench, "LATE", 1)
    monkeypatch.setattr(bench, "LATE_END", 20)
    configure("turn.json", {"late": 4, "late_save": True, "also": ["bench-y"]})  # saves 2 s after the bench gave up
    began = time.monotonic()
    assert run("--no-baseline", cases=[{**case, "repeat": 1, "timeout": 2}]) == 1
    printed, err = records(capsys)
    create = printed[-1]
    assert create["late_s"] is None and create["may_save_late"] and "no habit_save call" in create["why"]
    assert time.monotonic() - began < 18  # waited for its answer at the end, not for LATE_END
    assert not (home / "skills" / "bench-x").exists() and not (home / "skills" / "bench-y").exists()
    assert len(list((user / ".Trash").glob("bench-x-bench-*"))) == 1 and list((user / ".Trash").glob("bench-y-bench-*"))
    retired = [c["argv"][2] for c in calls() if c["argv"][:2] == ["habits", "retire"]]
    assert sorted(retired) == ["bench-x", "bench-y"]  # watches: their cron jobs go first
    assert "create bench-x: saved after the bench cut its turn; taken away" in err
    # Two creates in a row: the late save of the first is taken away before the second, which then runs. The save
    # must land after the bench gave up on the first turn (cut at 2 s, waited 1 s more) and before the second
    # create looks: it comes 4 s after the turn began, and the second create's sweep waits until it is there, so
    # no machine's speed decides (on CI it once came between that sweep and the name check: "exists already").
    def late_answers():
        with sqlite3.connect(home / "state.db") as db:
            return db.execute("SELECT count(*) FROM messages WHERE content = 'Fini tard.'").fetchone()[0]
    landed, sweep = late_answers(), bench.Bench.sweep

    def sweep_once_saved(self, wait=0):
        if self.pending and not wait:  # the second create's own sweep, not the end's
            limit = time.monotonic() + 15
            while late_answers() == landed and time.monotonic() < limit:
                time.sleep(0.1)
            assert late_answers() > landed, "the first create's late save never landed"
        return sweep(self, wait)
    monkeypatch.setattr(bench.Bench, "sweep", sweep_once_saved)
    configure("turn.json", {"late": 4, "late_save": True})
    assert run("--no-baseline", cases=[{**case, "repeat": 1, "timeout": 2, "create": {**case["create"], "repeat": 2}}])
    second = [r for r in records(capsys)[0] if r["kind"] == "create"][-1]
    assert "exists already" not in " ".join(second["why"]) and len(turns(calls)) == 3
    assert turns(calls)[-1]["paused"] and not (home / "plugin-data" / "habits" / "paused").exists()
    monkeypatch.setattr(bench.Bench, "sweep", sweep)
    # A cut create still silent at the end may yet save: habits stay paused, and the bench says how to resume.
    monkeypatch.setattr(bench, "LATE_END", 1)
    configure("turn.json", {"late": 8, "late_save": True})
    assert run("--no-baseline", cases=[{**case, "repeat": 1, "timeout": 2}]) == 1
    assert "habits stay paused" in records(capsys)[1] and (home / "plugin-data" / "habits" / "paused").exists()
    time.sleep(5)  # its late save lands, in the test's own folder


@pytest.mark.parametrize("command, paths", [
    ("echo x > /tmp/y", ["/tmp/y"]), ("echo x >> ~/notes.md 2>&1", ["~/notes.md"]),
    ("cat a | tee b /tmp/c", ["b", "/tmp/c"]), ("mkdir -p a/b && touch a/b/c", ["a/b", "a/b/c"]),
    ("cp -r src /x/dst", ["/x/dst"]), ("mv -n -- a.md ~/.Trash/", ["~/.Trash/"]),
    ("sed -i '' 's/a/b/' f.yaml", ["f.yaml"]), ("sed 's/a/b/' f.yaml > g.yaml", ["g.yaml"]),
    ("cd /etc && touch x", ["/etc/x"]), ("ls -la ~ && cat ~/.hermes/x 2>/dev/null", ["/dev/null"]),
    ("cd /etc; echo a > ~/x; cd sub; touch y", ["~/x", "/etc/sub/y"]),
    ("rm -f $T/x", ["$T/x"]), ("cat 'unbalanced", []),
])
def test_what_a_shell_line_writes(command, paths):
    assert bench.written(command) == paths


def test_a_turn_that_writes_outside_its_root_is_said(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    configure("turn.json", {"calls": [
        {"tool": "write_file", "args": {"path": "/Users/alex/.hermes/cache/scratch/draft.yaml", "content": "x"},
         "reply": {"ok": True}},
        {"tool": "terminal", "args": {"command": "echo x > {cwd}/ok.txt; sed -i '' s/a/b/ ~/notes.md"},
         "reply": {"output": ""}},
        {"tool": "terminal", "args": {"command": "touch ok2.txt > /dev/null", "workdir": "{cwd}"}, "reply": {}}]})
    assert run("--no-create", cases=[{**case, "repeat": 1}]) == 0  # a note, not a failure
    printed, err = records(capsys)
    assert printed[-1]["outside"] == ["/Users/alex/.hermes/cache/scratch/draft.yaml", "~/notes.md"]
    assert "tidy baseline 0 wrote outside its root: /Users/alex/.hermes/cache/scratch/draft.yaml, ~/notes.md" in err


def test_a_note_is_a_check_whose_failure_is_said_not_counted(fake, capsys):
    # A bench run: a real ob sync of the vault lands every 16 min or so; its trace under ~/.sync-state is a note.
    run, calls, home, user, case, records, configure = fake
    noted = {**case, "repeat": 1, "mode": "live", "note": 'test ! -e "$SCRATCH/inbox/a.pdf"'}
    assert run("--no-create", cases=[noted]) == 0
    printed, err = records(capsys)
    assert [r["kind"] for r in printed] == ["run", "baseline"] and all(r["ok"] for r in printed)
    assert all(r["notes"][0].startswith("note failed (exit 1)") for r in printed)
    assert "tidy run 0: note failed (exit 1)" in err and "tidy baseline 0: note failed (exit 1)" in err
    assert run("--no-create", cases=[{**noted, "note": "true"}]) == 0
    assert all("notes" not in r for r in records(capsys)[0])
    assert bench.problems({"id": "a", "task": "t", "habit": "h", "expect": {"end": {}}, "note": "true"}) == [
        "setup, rewrite, verify and note need a scratch folder"]


def test_a_run_that_read_the_web_is_read_back_whole(setup, monkeypatch, tmp_path, capsys):
    # After a read outside, `hermes habits run --json` puts its texts in an untrusted block: the model reads that
    # output in a terminal. The bench reads every field as before.
    plugin_tests.test_hermes_habits_run_gives_what_a_web_read_brought_back_as_data(setup, monkeypatch, tmp_path,
                                                                                    capsys, True)
    ctx, habits = setup
    code, out, _ = plugin_tests.cli_run(habits, capsys, str(tmp_path / "news" / "reflex.yml"))
    back = bench.last_json("notice\n" + json.dumps(out, ensure_ascii=False))
    assert "untrusted" in out and "untrusted" not in back
    assert back["doubted"][0]["item"] == plugin_tests.INJECTED and back["asks"][0]["node"] == "judge"
    assert back["message"].startswith("Nouvelles") or back["status"] == "wake"
    assert bench.last_json(json.dumps(OUT)) == OUT  # a run that read nothing outside is as it was


# ---- What decided each run, and traps held by less than 0.1 -------------------------------------

def seen(answer, confidence, gate, passed, **odds):
    return {"answer": answer, "confidence": confidence, "probabilities": odds, "backend": "jev", "gate": gate,
            "passed": passed}


# T-workshop-voice (the landlord message) and t-summary.
VOICE = {"picked": "vault-find-send", "fired": None, "ms": 900,
         "pick": seen("vault-find-send", 0.95, 0.7, True, none=0.05, **{"vault-find-send": 0.95}),
         "confirm": seen("run", 0.74, 0.8, False, run=0.74, changed=0.15, more=0.08, not_now=0.03),
         "also": seen("nothing", 0.69, 0.8, False, nothing=0.69, other=0.31)}
SUMMARY = {"picked": None, "fired": None, "pick": seen("vault-find-send", 0.68, 0.7, False, none=0.32,
                                                       **{"vault-find-send": 0.68})}
CLEAR = {"picked": None, "fired": None, "pick": seen("none", 0.94, 0.7, False, none=0.94, **{"vault-find-send": 0.06})}


@pytest.mark.parametrize("trigger, expect, found", [
    (VOICE, ["none"], ["confirm run 0.74 under its gate 0.8"]),
    (SUMMARY, ["none", "wake"], ["pick vault-find-send 0.68 under its gate 0.7"]),
    (CLEAR, ["none", "wake"], []),
    ({**CLEAR, "fired": "vault-find-send"}, ["none", "wake"], ["fired: the trigger ran vault-find-send"]),
    (VOICE, ["end", "wake"], []),  # a plain ask is no trap: its near misses are in its trigger, not flagged
    (None, ["none"], []),  # no message, no trigger
    ({**CLEAR, "pick": seen("vault-find-send", 0.7, 0.8, False)}, ["none"], [
        "pick vault-find-send 0.70 under its gate 0.8"]),  # no probabilities: the answer's own confidence
    ({**CLEAR, "pick": seen("none", 0.75, 0.8, False)}, ["none"], []),
    # The landlord message's confirm passed at its gate and only the second question held it,
    # 0.12 under: one question of the two asked together held the trap alone.
    ({**VOICE, "confirm": seen("run", 0.8, 0.8, True, run=0.8, changed=0.1, more=0.07, not_now=0.03),
      "also": seen("nothing", 0.68, 0.8, False, nothing=0.68, other=0.32)}, ["none"],
     ["confirm run 0.80 passed its gate 0.8; only also held it (nothing 0.68)"]),
    ({**VOICE, "confirm": seen("not_now", 0.9, 0.8, False, run=0.05, not_now=0.9, changed=0.03, more=0.02),
      "also": seen("nothing", 0.95, 0.8, True, nothing=0.95, other=0.05)}, ["none"],
     ["also nothing 0.95 passed its gate 0.8; only confirm held it (run 0.05)"]),
    ({**VOICE, "also": seen("nothing", 0.85, 0.8, True, nothing=0.85, other=0.15)}, ["none"],
     ["confirm run 0.74 under its gate 0.8", "also nothing 0.85 passed its gate 0.8; only confirm held it (run 0.74)"]),
])
def test_a_trap_whose_trigger_held_by_less_than_a_tenth_is_flagged(trigger, expect, found):
    assert bench.near(trigger, "vault-find-send", expect) == found


def recorded(answer, habit="vault-find-send"):
    return {**answer, "recorded": True, "habit": habit}


@pytest.mark.parametrize("trigger, found", [
    # After a pick that held, the CLI records the confirmation it made needless; one that would
    # pass is the pick holding the trap alone, and the two recorded answers never held it by themselves.
    ({**SUMMARY, "confirm": recorded(seen("changed", 0.7, 0.8, False, run=0.2, changed=0.7, more=0.05, not_now=0.05)),
      "also": recorded(seen("nothing", 0.94, 0.8, True, nothing=0.94, other=0.06))},
     ["pick vault-find-send 0.68 under its gate 0.7"]),
    ({**CLEAR, "confirm": recorded(seen("run", 0.75, 0.8, False, run=0.75, changed=0.25))},
     ["confirm run 0.75 under its gate 0.8"]),
    ({**CLEAR, "confirm": recorded(seen("run", 0.9, 0.8, True, run=0.9, changed=0.1))},
     ["confirm run 0.90 would pass its gate 0.8; only the pick held it"]),
    ({**CLEAR, "confirm": recorded(seen("run", 0.9, 0.8, True, run=0.9, changed=0.1)),
      "also": recorded(seen("nothing", 0.85, 0.8, True, nothing=0.85, other=0.15))},
     ["confirm run 0.90 would pass its gate 0.8; only the pick held it"]),
    # The second question would hold it, 0.3 under: two gates of three still hold the trap, nothing to say.
    ({**CLEAR, "confirm": recorded(seen("run", 0.9, 0.8, True, run=0.9, changed=0.1)),
      "also": recorded(seen("nothing", 0.5, 0.8, False, nothing=0.5, other=0.5))}, []),
    # One about another habit (a tie of the pick) says nothing of this one's second gate.
    ({**CLEAR, "confirm": recorded(seen("run", 0.85, 0.8, True, run=0.85, changed=0.15), "tidy-downloads")},
     []),
])
def test_a_recorded_confirmation_shows_the_second_gate_of_a_trap_the_pick_held(trigger, found):
    assert bench.near(trigger, "vault-find-send", ["none"]) == found


def test_a_recorded_confirmation_is_not_the_runs_time(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    configure("run.json", {"status": "none", "trigger": {**CLEAR, "recorded_ms": 600000}})
    trap = {**case, "repeat": 1, "message": "Send me a summary of the note", "expect": {"none": {}}}
    assert run("--no-baseline", "--no-create", cases=[trap]) == 0
    printed, err = records(capsys)
    assert printed[0]["wall_ms"] == 0  # the whole process, less the question the hook never asks


def test_a_run_record_keeps_its_asks_and_trigger_and_says_a_near_trap(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    trap = {**case, "id": "t-voice", "repeat": 1, "message": "Fine with the landlord. Send the AI workshop",
            "expect": {"none": {}}}
    asks = [{"node": "pick", "answers": [["a.md", "1", 0.62]], "options": {"1": "a.md", "2": "b.md"},
             "probabilities": [{"1": 0.62, "2": 0.3, "none": 0.08}], "margin": 0.32, "summary": "unsure"}]
    configure("run.json", {"habit": "vault-find-send", "status": "none", "planned": [], "planned_count": 0,
                           "trigger": VOICE, "asks": asks})
    assert run("--no-baseline", "--no-create", cases=[trap]) == 0
    printed, err = records(capsys)
    assert printed[0]["asks"] == asks and printed[0]["trigger"] == VOICE
    assert printed[0]["near"] == ["confirm run 0.74 under its gate 0.8"]
    assert "near a gate: t-voice 1/1 run (confirm run 0.74 under its gate 0.8)" in err
    configure("run.json", {"habit": "vault-find-send", "status": "none", "planned": [], "planned_count": 0,
                           "trigger": CLEAR})
    assert run("--no-baseline", "--no-create", cases=[trap]) == 0
    printed, err = records(capsys)
    assert printed[0]["near"] == [] and "near a gate: t-voice" not in err


# After refusals the tools say "do the task the usual way", and for a watch that is a Hermes
# cron job of the turn's own, which runs model turns on its schedule after the bench (the pause does not reach it).
def test_a_cron_job_a_model_turn_made_is_taken_away_and_named(fake, capsys):
    run, calls, home, user, case, records, configure = fake
    made = {"tool": "cronjob_manage", "direct": True, "args": {"action": "create", "schedule": "every 5m", "prompt": "x"},
            "reply": {"success": True, "job_id": "a1b2c3", "next_run": "in 5 minutes"}}
    edited = {"tool": "cronjob_manage", "direct": True, "args": {"action": "update", "job_id": "e5e5e5"},
              "reply": {"success": True, "job_id": "e5e5e5"}}  # an existing job of the user's: never touched
    failed = {"tool": "cronjob_manage", "direct": True, "args": {"action": "create", "schedule": "x"},
              "reply": {"success": False, "error": "bad schedule"}}
    configure("turn.json", {"kind": "watch", "calls": [made, edited, failed]})
    run("--budget", "0.05", "--no-baseline", cases=[{**case, "repeat": 1, "forbid": ["cronjob_manage"]}])
    printed, err = records(capsys)
    create = printed[-1]
    assert create["cron_jobs"] == ["a1b2c3"] and "cron_left" not in create
    assert any("cronjob_manage" in w for w in create["why"])
    removed = [c["argv"] for c in calls() if c["argv"][:1] == ["cron"]]
    assert removed == [["cron", "remove", "a1b2c3"]] and "cron job a1b2c3" in err
    (home / "cron-fails").write_text("")
    configure("turn.json", {"calls": [made]})
    run("--budget", "0.05", "--no-create", cases=[{**case, "repeat": 1}])  # a baseline's too
    printed, err = records(capsys)
    assert printed[-1]["kind"] == "baseline" and printed[-1]["cron_left"] == ["a1b2c3"]
    assert "hermes cron remove a1b2c3" in err


def test_the_bench_sees_a_habit_file_under_either_name(tmp_path):
    # reflex.yml, and the HABIT.yaml that v0.1 wrote: what a model turn finds near its root, and a habit's folder
    for name in ("reflex.yml", "HABIT.yaml"):
        assets = tmp_path / f"in-{name}" / "assets"
        assets.mkdir(parents=True)
        (assets / name).write_text("spec: habits/0.1\n")
    found = bench.within_reach(tmp_path, set(), {"in-reflex.yml", "in-HABIT.yaml"})
    assert sorted(Path(f).name for f in found) == ["HABIT.yaml", "reflex.yml"]
