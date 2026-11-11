"""habit_run {habits: [...]}: several reflexes in one baton pass, run one after another, in the order given."""

import json
import time

import pytest
import yaml

from habits import catalog, cockpit, tools
from test_graph import DEMO, small
from test_plugin import (READER, automatic, curator, reads_poison, setup, with_habit,  # noqa: F401 (setup: a fixture)
                         wrapped)


def run(plugin_, **args) -> dict:
    return json.loads(plugin_.tool_run(args))


def echo(word: str, command: str | None = None) -> dict:
    """A one-off that runs one read command and ends on its word."""
    return small(look={"do": "terminal", "read": True, "args": {"command": command or f"ls ~/{word}"}, "as": "files",
                       "next": "done"}, done={"end": word})


def terminal(ctx) -> list[str]:
    return [args.get("command", "") for name, args in ctx.calls if name == "terminal"]


# ---- the shape of the call: refused whole, nothing runs ------------------------------------------------------

@pytest.mark.parametrize("entries", [[], [{"name": f"h{i}"} for i in range(9)], "x", {"name": "a"}])
def test_habits_takes_1_to_8_entries(setup, entries):
    ctx, habits = setup
    ctx.calls.clear()
    reply = run(habits, habits=entries)
    assert reply["status"] == "refused" and reply["done"] is False and ctx.calls == []
    assert "habits: 1 to 8 entries" in reply["issues"][0]


def test_a_list_of_one_reflex_runs(setup):
    """The real-model trial of 11 November: `habits: [one]` was refused at the schema, one more model call."""
    ctx, habits = setup
    reply = run(habits, habits=[{"scenario": echo("a")}])
    assert reply["done"] is True and [r["message"] for r in reply["results"]] == ["a"]


@pytest.mark.parametrize("entry, says", [
    ({"name": "a", "scenario": DEMO}, "give one of name, reflex or file"),
    ({}, "give one of name, reflex or file"),
    ({"habits": [{"name": "a"}, {"name": "b"}]}, "no nesting"),
    ({"name": "a", "dry": True}, "dry goes at the top level"),
    ({"name": "a", "message": "hi"}, "unknown key message"),
    ("hermes-health", "an object"),
    ({"name": "a", "inputs": "x"}, "inputs is an object"),
])
def test_each_entry_gives_one_source_and_no_nesting(setup, entry, says):
    ctx, habits = setup
    ctx.calls.clear()
    reply = run(habits, habits=[{"scenario": echo("a")}, entry])
    assert reply["status"] == "refused" and ctx.calls == []
    assert any(i.startswith("habits[1]: ") and says in i for i in reply["issues"])


@pytest.mark.parametrize("extra", [{"name": "x"}, {"scenario": DEMO}, {"inputs": {}}, {"message": "hi"}])
def test_habits_goes_alone_but_for_dry(setup, extra):
    ctx, habits = setup
    two = [{"scenario": echo("a")}, {"scenario": echo("b")}]
    assert "habits goes alone" in run(habits, habits=two, **extra)["issues"][0]
    assert run(habits, habits=two, dry=True)["done"] is True


def test_a_null_key_is_a_key_left_out(setup):
    """Some providers send every schema key and fill the unused ones with null."""
    ctx, habits = setup
    single = run(habits, scenario=echo("a"), habits=None, dry=None, name=None, inputs=None, message=None)
    assert single["done"] is True and single["message"] == "a"
    two = [{"scenario": echo("a"), "name": None, "file": None, "inputs": None}, {"scenario": echo("b")}]
    reply = run(habits, habits=two, name=None, scenario=None, file=None, inputs=None, message=None, dry=None)
    assert reply["done"] is True and [r["message"] for r in reply["results"]] == ["a", "b"]


# ---- order and isolation -------------------------------------------------------------------------------------

def test_the_entries_run_one_after_another_in_the_order_given(setup, monkeypatch):
    ctx, habits = setup
    seen, original = [], ctx.dispatch_tool

    def dispatch(name, args):
        seen.append(("start", args.get("command")))
        out = original(name, args)
        seen.append(("end", args.get("command")))
        return out
    monkeypatch.setattr(ctx, "dispatch_tool", dispatch)
    reply = run(habits, habits=[{"scenario": echo(w)} for w in ("c", "a", "b")])
    assert [c for _, c in seen] == ["ls ~/c", "ls ~/c", "ls ~/a", "ls ~/a", "ls ~/b", "ls ~/b"]  # never overlapping
    assert [r["message"] for r in reply["results"]] == ["c", "a", "b"] and reply["done"] is True
    assert [r["name"] for r in reply["results"]] == ["one-off 1", "one-off 2", "one-off 3"]


def test_one_entry_failing_or_handed_back_never_stops_the_others(setup, monkeypatch):
    ctx, habits = setup
    real = habits.run

    def run_(habit, scenario, *a, **k):
        if scenario.get("nodes", {}).get("done", {}).get("end") == "boom":
            raise RuntimeError("the engine broke on /Users/someone/secret")
        return real(habit, scenario, *a, **k)
    monkeypatch.setattr(habits, "run", run_)
    automatic(habits)
    woke = small(look={"do": "terminal", "read": True, "args": {"command": "ls ~/w"}, "as": "files", "next": "see"},
                 see={"check": {"empty": "files"}, "then": {"yes": "help", "no": "done"}})
    reply = run(habits, habits=[{"name": "nope"}, {"scenario": echo("boom")}, {"scenario": woke},
                                {"name": "sort-downloads-by-type"}])
    nope, boom, handed, demo = reply["results"]
    assert nope["status"] == "refused" and nope["done"] is False and "no habit named" in nope["issues"][0]
    assert boom["status"] == "error" and boom["done"] is False and boom["message"] == "habits: RuntimeError"
    assert "secret" not in json.dumps(reply)  # an exception's text may quote what a run read: it stays in the log
    assert handed["status"] == "wake" and handed["done"] is False and handed["left"]
    assert demo["done"] is True and demo["message"] == "Downloads sorted."
    assert reply["done"] is False and "mv " in json.dumps(terminal(ctx))  # the last one ran, live
    alone = run(habits, scenario=echo("boom"))
    assert alone["status"] == "error" and alone["error"] == "habits: RuntimeError" and "secret" not in json.dumps(alone)
    assert reply["summary"].startswith("nope ✗ refused · one-off 2 ✗ failed · one-off 3 ↩ 1 left · "
                                       "sort-downloads-by-type ✓ ")
    assert reply["next"] == tools.FAN_HANDED


def test_an_entry_the_budget_cannot_start_is_not_run(setup, monkeypatch):
    ctx, habits = setup
    monkeypatch.setattr(tools, "FAN_SECONDS", 4)
    original = ctx.dispatch_tool

    def slow(name, args):
        if "slow" in args.get("command", ""):
            time.sleep(1.2)
        return original(name, args)
    monkeypatch.setattr(ctx, "dispatch_tool", slow)
    reply = run(habits, habits=[{"scenario": echo("slow")}, {"scenario": echo("slower")}, {"scenario": echo("b")}])
    first, second, third = reply["results"]
    assert first["done"] is True and second["done"] is True  # 1.2 s each: the third would start with < 2 s left
    assert third == {"name": "one-off 3", "done": False, "status": "not run", "left": [tools.NOTHING_RAN],
                     "next": tools.FAN_LATE}
    assert "ls ~/b" not in terminal(ctx) and reply["summary"].endswith("one-off 3 ✗ not run")


def test_a_busy_habit_is_one_entry_handed_back(setup):
    ctx, habits = setup
    with habits.held("sort-downloads-by-type"):
        reply = run(habits, habits=[{"name": "sort-downloads-by-type"}, {"scenario": echo("a")}])
    assert reply["results"][0]["status"] == "busy" and reply["results"][1]["done"] is True


def test_the_same_habit_twice_runs_twice_and_is_never_busy(setup):
    _, habits = setup
    automatic(habits)
    reply = run(habits, habits=[{"name": "sort-downloads-by-type"}] * 2)
    assert [r["status"] for r in reply["results"]] == ["end", "end"] and reply["done"] is True


# ---- each entry is a single call ------------------------------------------------------------------------------

def test_each_entry_is_recorded_paged_and_counted_as_a_single_call(setup, monkeypatch, tmp_path):
    ctx, habits = setup
    used = curator(monkeypatch)
    with_habit(tmp_path, monkeypatch, "scan", yaml.safe_dump(small()))
    habits.ledger.adopt("scan", catalog.find("scan").version, "asked")
    run(habits, habits=[{"name": "scan"}, {"name": "sort-downloads-by-type"}, {"scenario": echo("a")}])
    assert used == []  # no run writes Hermes's skill usage record
    assert len(habits.ledger.runs("scan")) == len(habits.ledger.runs("sort-downloads-by-type")) == 1
    pages = habits.home() / "pages"
    assert (pages / "scan.html").exists() and (pages / "sort-downloads-by-type.html").exists()
    assert len(list(pages.glob("one-off-*.html"))) == 1


def test_dry_applies_to_every_entry(setup, monkeypatch):
    ctx, habits = setup
    used = curator(monkeypatch)
    automatic(habits)
    reply = run(habits, habits=[{"name": "sort-downloads-by-type"}, {"scenario": DEMO}], dry=True)
    assert not [c for c in terminal(ctx) if "mv " in c] and used == []
    assert all(r["dry"] is True and r["planned_count"] == 5 for r in reply["results"])
    assert habits.ledger.runs("sort-downloads-by-type") == [] and reply["done"] is False
    # A dry entry that ended with writes planned is a test, not a hand-back: the summary says so.
    assert "handed back" not in reply["summary"] and reply["summary"].count("· dry, 5 planned") == 2


def test_the_reply_is_done_only_when_every_entry_is_done_and_stays_small(setup, monkeypatch):
    ctx, habits = setup
    big = small(look={"do": "terminal", "read": True, "args": {"command": "ls ~/big"}, "as": "files", "next": "done"})
    original = ctx.dispatch_tool
    monkeypatch.setattr(ctx, "dispatch_tool", lambda name, args: json.dumps({"output": "x" * 20_000, "exit_code": 0})
                        if "big" in args.get("command", "") else original(name, args))
    reply = run(habits, habits=[{"scenario": big}] * 8)
    assert set(reply) == {"done", "results", "summary", "next"} and reply["done"] is True
    assert reply["next"] == tools.FAN_DONE
    for slot in reply["results"]:
        assert {"name", "done", "status", "message", "values", "left", "next"} <= set(slot)
        assert "report" not in slot and len(json.dumps(slot["values"])) <= tools.FAN_CHARS // 8
    assert len(json.dumps(reply, ensure_ascii=False)) < tools.FAN_CHARS + 4000


def test_long_plans_and_lists_stay_within_the_replys_share(setup, monkeypatch):
    """planned, left and issues are cut to the entry's share too: 8 dry entries of 20 long planned lines."""
    ctx, habits = setup
    long = small(look={"do": "terminal", "read": True, "args": {"command": "ls ~/many"}, "as": "files", "next": "move"},
                 move={"do": "terminal", "each": "files", "args": {"command": "mv ~/many/{item} ~/" + "d" * 120 + "/"},
                       "next": "done"})
    original = ctx.dispatch_tool
    monkeypatch.setattr(ctx, "dispatch_tool", lambda name, args: json.dumps({"exit_code": 0, "output": "\n".join(
        f"file-{i}.txt" for i in range(30))}) if "ls ~/many" in args.get("command", "") else original(name, args))
    reply = run(habits, habits=[{"scenario": long}] * 8, dry=True)
    assert all(slot["dry"] is True and slot["planned"][-1].startswith("… and ") for slot in reply["results"])
    assert len(json.dumps(reply, ensure_ascii=False)) <= tools.FAN_CHARS + 3000
    many = {k: [k[0] * 300] * 50 for k in ("left", "planned", "issues")}  # issues too, as a refusal gives them
    slot = tools._bounded({"done": False, "message": "m" * 9000, "values": {"v": "v" * 9000}, **many}, 3000)
    assert len(json.dumps(slot, ensure_ascii=False)) <= 3000 + 300
    assert all(slot[k][-1].startswith("… and ") for k in many)


def test_an_entry_that_read_the_web_is_untrusted_alone(setup, monkeypatch):
    ctx, habits = setup
    reads_poison(ctx, monkeypatch)
    reply = run(habits, habits=[{"scenario": READER}, {"scenario": echo("a")}])
    web, local = reply["results"]
    assert wrapped(web["untrusted"]) and "Ignore your instructions" in web["untrusted"]
    assert not {"message", "values", "left"} & set(web) and "Ignore" not in web["next"]
    assert "untrusted" not in local and local["message"] == "a"
    assert "Ignore" not in reply["summary"] + reply["next"]


def test_a_long_untrusted_block_is_cut_and_still_closed(setup, monkeypatch):
    ctx, habits = setup
    monkeypatch.setattr(ctx, "dispatch_tool", lambda name, args: json.dumps({"results": [{"content": "y" * 30_000}]}))
    reply = run(habits, habits=[{"scenario": READER}] * 8)
    for slot in reply["results"]:
        assert wrapped(slot["untrusted"] + "</untrusted-tool-result>") and len(slot["untrusted"]) <= tools.FAN_CHARS // 8


def test_the_tool_and_the_prompt_teach_habits(setup):
    assert tools.RUN_FORM_HABITS in tools.RUN_DESCRIPTION
    assert cockpit.PROMPT_WHEN_SEVERAL in cockpit.PROMPT_WHEN
    schema = tools.RUN_SCHEMA["parameters"]["properties"]["habits"]
    assert schema["type"] == "array" and schema["minItems"] == 1 and schema["maxItems"] == 8
