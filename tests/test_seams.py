"""The seams between the parts of the last pass, each crossed once on the real code, with nothing put into the
ledger by hand: Hermes's history -> the dream -> a draft that practices beside Hermes -> the card -> the user's yes ->
a learned reflex that acts when Hermes calls it. And the settings that reach each part."""

import json

import pytest

from dream_cases import FakeStore
from habits import catalog, dream, nursery, plugin
from test_dreaming import Clock, history, repeat
from test_plugin import Inline
from test_practice import DreamCtx, scan

DF = "df -h ~"
OUT = {"output": "Filesystem 100G", "exit_code": 0, "error": None}


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    ctx = DreamCtx(root, {"learning": "practice"})  # the default is suggest
    habits = plugin.Habits(ctx, home=tmp_path / "plugin-data" / "habits")
    habits.clock = habits.ledger.clock = Clock()
    monkeypatch.setattr(nursery.time, "time", habits.clock)
    monkeypatch.setattr(catalog, "skills", lambda: scan(root))
    monkeypatch.setattr(plugin.threading, "Thread", Inline)
    habits.store = FakeStore([], {})
    monkeypatch.setattr(dream, "open_store", lambda: habits.store)
    ctx.outputs[DF] = OUT
    return ctx, habits


def hermes_runs(habits, session, command=DF):
    """One turn of Hermes with this command: the tool hook, then the reply hook."""
    habits.on_post_tool_call(tool_name="terminal", session_id=session, args={"command": command}, result=json.dumps(OUT))
    habits.on_post_llm_call(session_id=session, assistant_response="done", platform="cli")
    habits.last_practice.clear()  # the 10 minutes between two real runs are not what these tests wait for


def test_no_two_parts_of_the_plugin_define_the_same_name():
    """Habits is made of mix-ins: a name two of them define would silently be the first one's."""
    seen = {}
    for part in plugin.Habits.__mro__[:-1]:
        for name in vars(part):
            if not name.startswith("__"):
                seen.setdefault(name, []).append(part.__name__)
    assert {name: parts for name, parts in seen.items() if len(parts) > 1} == {}


def test_from_the_history_to_a_reflex_that_acts(world, monkeypatch, capsys):
    ctx, habits = world
    saves = []
    save = plugin.Habits.save
    monkeypatch.setattr(plugin.Habits, "save", lambda self, *a, **k: saves.append(k.get("origin")) or save(self, *a, **k))
    # 1. the dream reads the history (the terminal command reaches Dream.dream_now) and drafts what comes back
    history(habits, repeat(DF))
    import argparse
    parser = argparse.ArgumentParser()
    habits.cli_setup(parser)
    assert habits.cli_handle(parser.parse_args(["dream", "--json"])) == 0
    report = json.loads(capsys.readouterr().out)
    assert [s["action"] for s in report["spotted"]] == [DF] and [p["name"] for p in report["practicing"]] == ["df"]
    [draft] = habits.ledger.drafts()
    key = draft["key"]
    assert (draft["state"], draft["reads"], draft["raw"]) == ("practising", 1, dream.raw_of("terminal", {"command": DF}))
    # 2. it practices beside Hermes: the hook's call reaches the ledger, a verdict each time, never an output
    for n in range(3):
        assert habits.ledger.draft(key)["state"] == "practising"
        hermes_runs(habits, f"s{n}")
    assert [(p["kind"], p["verdict"]) for p in habits.ledger.practice(key)] == [("read", "same")] * 3
    assert ctx.ran == [DF] * 3 and habits.ledger.draft(key)["state"] == "ready"
    hermes_runs(habits, "s3")
    assert ctx.ran == [DF] * 3  # ready: it waits for the answer, nothing more runs
    # 3. one line of news, then the card with the whole command and the moments
    told = habits.on_pre_llm_call(session_id="n", user_message="hello there", platform="cli")
    assert "df practiced well beside Hermes" in told["context"]
    [card] = habits.dream_cards()
    # was "... 3 days · first gesture of a session (100%) · closes the work (100%). Beside ...": a turn of this one call
    # has no such moment to tell
    assert card["question"].startswith("df runs `df -h ~   (in ~/proj)`: Hermes did it in 3 turns on 3 days. "
                                       "Beside Hermes: 3 of 3 the same.")
    # 4. the user's yes saves it by habit_save's path as a learned reflex, automatic at once
    shown = habits.command("")  # a chat with no buttons: the card itself, then the words to type
    assert card["question"] in shown and "Answer with /habits yes df or /habits no df." in shown.splitlines()
    assert habits.command("yes df") == "df is a reflex now: it acts on its own when Hermes calls it."
    assert saves == ["learned"]
    habit = catalog.find("df")
    info = habits.ledger.info("df")
    assert (info["origin"], info["status"], info["version"]) == ("learned", "active", habit.version)
    assert habit.graph == draft["graph"] and habits.yes_given(habit)
    assert habits.ledger.draft(key)["state"] == "saved" and habits.dream_cards() == []
    # 5. Hermes calls it: it acts, and its run is on record (what later tells the dream it still serves)
    reply = json.loads(habits.tool_run({"name": "df"}))
    assert reply["done"] is True and "Filesystem 100G" in json.dumps(reply)
    assert [r["habit"] for r in habits.ledger.runs("df", 1)] == ["df"]
    # 6. the next dream leaves the saved reflex alone
    assert habits.dream_now(manual=True)["proposals"] == []
    assert habits.ledger.draft(key)["state"] == "saved"


@pytest.mark.parametrize("caution, needed", [("relaxed", 2), ("normal", 3), ("strict", 5)])
def test_caution_sets_how_many_matches_make_a_draft_ready(world, caution, needed):
    ctx, habits = world
    ctx.config["caution"] = caution
    history(habits, repeat(DF))
    assert habits.dream_now(manual=True)["practising"][0]["needed"] == needed
    [draft] = habits.ledger.drafts()
    for n in range(needed):
        assert habits.ledger.draft(draft["key"])["state"] == "practising"
        hermes_runs(habits, f"s{n}")
    assert habits.ledger.draft(draft["key"])["state"] == "ready"


def test_a_long_output_is_compared_on_what_both_show(world):
    ctx, habits = world
    long = "\n".join(f"line {n}" for n in range(3000))  # past the cap of a reflex's message
    ctx.outputs[DF] = {"output": long, "exit_code": 0, "error": None}
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    [draft] = habits.ledger.drafts()
    habits.on_post_tool_call(tool_name="terminal", session_id="s", args={"command": DF},
                             result=json.dumps({"output": long, "exit_code": 0, "error": None}))
    assert [p["verdict"] for p in habits.ledger.practice(draft["key"])] == ["same"]


def test_learning_off_stops_the_dream_and_the_practice_but_not_a_reflex_that_acts(world):
    ctx, habits = world
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    [draft] = habits.ledger.drafts()
    ctx.config["learning"] = "off"
    hermes_runs(habits, "s0")
    assert habits.ledger.practice(draft["key"]) == [] and ctx.ran == []
    assert habits.dream_now(manual=True)["status"] == "off"


def test_suggest_waits_for_try_before_anything_practises(world):
    ctx, habits = world
    ctx.config["learning"] = "suggest"
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    hermes_runs(habits, "s0")
    assert ctx.ran == [] and [d["state"] for d in habits.ledger.drafts()] == ["spotted"]
    assert habits.command("try df") == habits._say("after_try", name="df")
    hermes_runs(habits, "s1")
    assert ctx.ran == [DF]


# ---- growing (B5): two actions that come back together become one reflex of two steps -------------------------------

def hermes_turn(habits, session, *commands):
    for command in commands:
        habits.on_post_tool_call(tool_name="terminal", session_id=session, args={"command": command},
                                 result=json.dumps(OUT))
    habits.on_post_llm_call(session_id=session, assistant_response="done", platform="cli")


def test_a_grown_draft_is_drafted_practised_shown_and_saved_as_one_reflex(world, monkeypatch):
    ctx, habits = world
    assert dream.GROW is True
    pull, make = "git pull", "make test"
    history(habits, [(f"g{d}", 0, d, (pull, make), True) for d in (1, 2, 3)])
    summary = habits.dream_now(manual=True)
    [draft] = habits.ledger.drafts()  # one draft of two steps, not two drafts of one
    assert draft["key"].startswith("grow:") and len(draft["parts"]) == 2 and draft["state"] == "practising"
    assert [n["do"] for n in draft["graph"]["nodes"].values() if "do" in n] == ["terminal", "terminal"]
    assert len(summary["practising"]) == 1
    # practiced at the end of a turn: the steps in the draft's order are a match, the other order differs
    for n in range(2):
        hermes_turn(habits, f"p{n}", pull, make)
    hermes_turn(habits, "wrong-order", make, pull)
    row = habits.ledger.draft(draft["key"])
    assert (row["same"], row["differs"], row["state"]) == (2, 1, "practising")
    hermes_turn(habits, "p3", pull, "ls", make)  # other gestures between the steps are fine
    assert habits.ledger.draft(draft["key"])["state"] == "ready"
    assert ctx.ran == []  # a grown draft never runs beside Hermes
    # the card shows each step, plainly
    [card] = habits.dream_cards()
    # each step, uncut, in the order it runs, with the folder it runs in
    assert f"`{pull}   (in ~/proj) then {make}   (in ~/proj)`" in card["question"]
    # the yes saves one reflex that runs its steps in order
    assert card["question"] in habits.command("")  # the card went out as text
    assert habits.command(f"yes {draft['name']}").endswith("acts on its own when Hermes calls it.")
    habit = catalog.find(draft["name"])
    assert habit is not None and habit.valid and habit.graph == draft["graph"]
    reply = json.loads(habits.tool_run({"name": habit.name}))
    assert reply["done"] is True
    assert ctx.ran == [pull, make]


# ---- what the dream must never draft stays out, whichever way it comes ------------------------------------------------

EXCLUDED = ["cat ~/.zshrc", "rm -r build", "echo hello", "hermes habits list",
            "curl -u me:hunter2 https://x.example", "export K=sk-abcdefghijkl1234 && run"]


@pytest.mark.parametrize("command", EXCLUDED)
def test_an_excluded_action_is_never_drafted_and_try_cannot_make_it_practise(world, command):
    ctx, habits = world
    ctx.config["learning"] = "suggest"
    history(habits, repeat(command))
    habits.dream_now(manual=True)
    assert habits.ledger.drafts() == []  # nothing spotted, so no try card either
    assert habits.dream_cards() == [] and habits.command("try " + command.split()[0]) != ""
    assert habits.ledger.drafts() == []


@pytest.mark.parametrize("command", EXCLUDED)
def test_a_draft_that_got_in_anyway_cannot_be_tried_or_brought_in_by_a_change(world, command):
    ctx, habits = world
    ctx.config["learning"] = "suggest"
    action = dream.Action(dream.key_of("terminal", {"command": command}) or "k", "terminal", {"command": command},
                          dream.raw_of("terminal", {"command": command}))
    habits.ledger.put_draft(action.key, name="odd", tool="terminal", args=action.args, raw=action.raw, parts=[],
                            graph=dream.draft_reflex([action]), reads=0, turns=4, days=3, moments={}, cwd="")
    habits.dream_decide(f"d:{action.key}", "try")
    assert habits.ledger.draft(action.key)["state"] == "gone" and habits.practising == {}


# ---- the second wave's seams: the words the model reads against the start on a loaded skill -----------------------

GLANCE = """spec: habits/0.1
trigger: skill
start: look
fallback: help
nodes:
  look: {do: terminal, read: true, args: {command: "df -h ~"}, as: out, next: done}
  done: {end: "Disk: {out}"}
  help: {wake: "The disk check stopped: look at the disk by hand."}
"""


def test_a_skill_start_saved_by_the_model_is_told_as_it_behaves(world):
    """habit_save -> the reply and the SKILL.md say how it starts -> loading the skill tests it -> the user's yes ->
    loading the skill runs it for real. Nothing put into the ledger by hand."""
    from habits import tools
    ctx, habits = world
    habits.on_pre_llm_call(session_id="s1", user_message="keep that as a habit", platform="cli")
    saved = json.loads(habits.tool_save({"name": "glance", "description": "Glance at the disk", "procedure": "1. df",
                                         "reflex": GLANCE}, session_id="s1"))
    assert saved["status"] == "saved"
    assert "when its skill is loaded" in saved["next"] and "/habits yes glance" in saved["next"]
    assert "no learning phase" not in saved["next"]
    skill_md = next(a["content"] for n, a in ctx.calls if n == "skill_manage" and "content" in a)
    assert "`habits` field" in skill_md and "do not run the reflex again" in skill_md
    assert "`habits` field" not in tools.footer("x", "one-off")

    def load():
        habits.loaded.clear()
        result = json.dumps({"success": True, "name": "glance", "content": skill_md})
        return json.loads(habits.on_transform_tool_result(tool_name="skill_view", args={"name": "glance"},
                                                          result=result, session_id="s1"))
    assert "a test run, it is learning" in load()["habits"]  # as the save's reply said: a test while it reads
    yes = habits.command("yes glance")
    assert "✅" in yes and "check behind" not in yes  # nothing compares a skill start: the yes promises no check
    told = load()["habits"]
    assert "(automatic): done. Disk: Filesystem 100G" in told and "do not redo" in told  # for real after the yes


def test_a_one_off_reflex_acts_for_real_as_its_description_says(world):
    """The tool description once said a one-off only plans what is not a read: it runs it, unless dry: true."""
    from habits import tools
    ctx, habits = world
    one_off = {"spec": "habits/0.1", "start": "note", "fallback": "help", "nodes": {
        "note": {"do": "terminal", "args": {"command": "touch ~/seen.txt"}, "next": "done"},
        "done": {"end": "noted"}, "help": {"wake": "stopped"}}}
    assert json.loads(habits.tool_run({"reflex": one_off, "dry": True}, session_id="s1"))["planned"]
    assert ctx.ran == []
    assert json.loads(habits.tool_run({"reflex": one_off}, session_id="s1"))["done"] is True
    assert ctx.ran == ["touch ~/seen.txt"]
    assert "acts for real unless dry: true" in tools.RUN_DESCRIPTION and "only plans" not in tools.RUN_DESCRIPTION
    assert "(no inputs, no ask or llm step)" in tools.SAVE_DESCRIPTION
    assert "starts on a message or a schedule begins learning" in tools.SAVE_DESCRIPTION


def test_two_saved_reflexes_used_together_grow_into_a_larger_one(world):
    """B5: two reflexes often used together give a proposal for a larger one, made of two `use` steps."""
    import time
    from test_dreaming import DAY, call, row, tool, user, OK
    ctx, habits = world
    up = "uptime"
    ctx.outputs[up] = OUT
    # two small reflexes, each learned the usual way
    history(habits, repeat(DF) + repeat(up, session="u"))
    habits.dream_now(manual=True)
    for n in range(3):
        hermes_runs(habits, f"a{n}")
        hermes_runs(habits, f"b{n}", up)
    names = sorted(d["name"] for d in habits.ledger.drafts())
    assert [d["state"] for d in habits.ledger.drafts()] == ["ready", "ready"]
    habits.command("")  # the cards go out as text
    for name in names:
        assert habits.command(f"yes {name}").endswith("acts on its own when Hermes calls it.")
    ctx.ran.clear()
    # Hermes now calls both by name, together, on three days
    midnight = time.mktime(time.localtime(habits.clock())[:3] + (0, 0, 0, 0, 0, -1))
    sessions = {}
    for d in (5, 6, 7):
        at = midnight - (10 - d) * DAY + 9 * 3600
        messages = [user(at)]
        for n, name in enumerate(names):
            messages += [call(at + 1 + n, "habit_run", {"name": name}, f"t{d}-{n}"), tool(at + 2 + n, f"t{d}-{n}", OK)]
        sessions[f"t{d}"] = messages
    habits.store = FakeStore([row(sid, m[-1]["timestamp"], started=m[0]["timestamp"]) for sid, m in sessions.items()], sessions)
    habits.dream_now(manual=True)
    [draft] = [d for d in habits.ledger.drafts() if d["key"].startswith("grow:")]
    assert draft["parts"] == [f"reflex:{n}" for n in names] and draft["state"] == "practising"
    assert [node["use"] for node in draft["graph"]["nodes"].values() if "use" in node] == names
    assert draft["turns"] == 3 and draft["days"] == 3 and draft["reads"] == 0  # nothing of it is run beside Hermes
    # practiced at the end of a turn, through the keys reflex:NAME
    habits.refresh_practising()  # the hook re-reads the drafts every 60 s at most: the test does not wait for it
    for n in range(3):
        for name in names:
            habits.on_post_tool_call(tool_name="habit_run", session_id=f"p{n}", args={"name": name}, result="{}")
        habits.on_post_llm_call(session_id=f"p{n}", assistant_response="done", platform="cli")
    row_ = habits.ledger.draft(draft["key"])
    assert (row_["same"], row_["differs"], row_["state"]) == (3, 0, "ready") and ctx.ran == []
    # its card shows each reflex it calls, and the yes saves one reflex of two `use` steps
    card = next(c for c in habits.dream_cards() if c["name"] == draft["name"])
    assert "`" + " then ".join(f"reflex {n}" for n in names) + "`" in card["question"]
    assert card["question"] in habits.command("")  # the card went out as text
    assert habits.command(f"yes {draft['name']}").endswith("acts on its own when Hermes calls it.")
    habit = catalog.find(draft["name"])
    assert habit is not None and habit.valid and habit.graph == draft["graph"]
    reply = json.loads(habits.tool_run({"name": habit.name}))
    assert reply["done"] is True and sorted(ctx.ran) == sorted([DF, up])
    text = (ctx.root / habit.name / "SKILL.md").read_text()
    assert f"description: \"Runs {names[0]}, then {names[1]}\"" in text
    assert f"1. {names[0]}\n2. {names[1]}" in text  # the skill text names each reflex it calls, in order


def test_the_card_of_a_grown_draft_shows_the_reflex_it_calls_beside_its_commands(world):
    ctx, habits = world
    make = dream.Action(dream.key_of("terminal", {"command": "make report"}), "terminal", {"command": "make report"},
                        dream.raw_of("terminal", {"command": "make report"}))
    called = dream.Action("reflex:morning", "habit_run", {"name": "morning"}, dream.raw_of("habit_run", {"name": "morning"}))
    habits.ledger.put_draft("grow:m", name="morning-make", tool="habit_run", args=called.args, raw=called.raw,
                            parts=[called.key, make.key], graph=dream.draft_reflex([called, make]), reads=0, turns=3,
                            days=3, moments={}, cwd="")
    habits.ledger.move_draft("grow:m", "ready", ("spotted",))
    [card] = habits.dream_cards()
    assert "`reflex morning then make report`" in card["question"]  # every step the yes covers, none hidden


def test_a_dream_typed_in_a_terminal_counts_the_very_next_call(world, capsys):
    """R1: for 60 s after `hermes habits dream` the running Hermes still held the old list of drafts that practice."""
    ctx, habits = world
    hermes_runs(habits, "before")  # the hook has read the ledger: no draft practices, and it will not look again for 60 s
    assert habits.practising == {} and habits.practising_at is not None
    history(habits, repeat(DF))
    import argparse
    parser = argparse.ArgumentParser()
    habits.cli_setup(parser)
    assert habits.cli_handle(parser.parse_args(["dream", "--json"])) == 0
    capsys.readouterr()
    [draft] = habits.ledger.drafts()
    assert draft["state"] == "practising"
    hermes_runs(habits, "right-after")  # no refresh_practising() by hand, no wait
    assert habits.ledger.draft(draft["key"])["same"] == 1


def test_a_try_typed_in_a_chat_counts_the_very_next_call(world):
    ctx, habits = world
    ctx.config["learning"] = "suggest"
    from test_practice import terminal
    key = terminal(habits, DF, state="spotted", name="df")
    hermes_runs(habits, "before")
    assert habits.dream_decide(f"d:{key}", "try").startswith("┄ df practices")
    hermes_runs(habits, "after")
    assert habits.ledger.draft(key)["same"] == 1
