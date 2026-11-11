"""The start on a loaded skill (A5): Hermes loads a skill with skill_view, and a reflex that starts on its skill runs,
its reply appended to the skill the model receives."""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from habits import catalog, graph, nursery, plugin
from test_graph import DEMO
from test_plugin import FakeCtx

READ = """spec: habits/0.1
trigger: skill
start: look
fallback: help
limits: {seconds: 15, tools: 1, questions: 0, llm: 0}
nodes:
  look: {do: terminal, read: true, args: {command: "df -h ~"}, as: out, next: done}
  done: {end: "Disk: {out}"}
  help: {wake: "The disk check stopped: look at the disk by hand."}
"""
ACT = READ.replace("read: true, ", "").replace("df -h ~", "touch ~/seen.txt")
WEB = READ.replace("do: terminal, read: true, args: {command: \"df -h ~\"}", "do: web_search, read: true, args: {query: x}")
SKILL = json.dumps({"success": True, "name": "glance", "content": "# Glance"})


class SkillCtx(FakeCtx):
    def __init__(self):
        super().__init__()
        self.ran = []
        self.seen = None  # called inside the run: what the hook state was

    def dispatch_tool(self, name, args):
        self.calls.append((name, args))
        self.ran.append((name, args))
        if name == "terminal":
            return json.dumps({"output": "100G free", "exit_code": 0, "error": None})
        return json.dumps({"output": "", "exit_code": 0, "error": None})


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    ctx = SkillCtx()
    habits = plugin.Habits(ctx, home=tmp_path / "plugin-data" / "habits")
    monkeypatch.setattr(plugin.threading, "Thread", lambda target, args=(), daemon=None: SimpleNamespace(
        start=lambda: target(*args), join=lambda timeout=None: None, is_alive=lambda: False))
    habits.root, habits.monkeypatch = root, monkeypatch
    habits.platforms["s1"] = "cli"
    save(habits, "glance", READ)
    return ctx, habits


def commands(ctx):
    return [a["command"] for n, a in ctx.ran if n == "terminal"]


def save(habits, name, text, description="Glance at the disk", origin="skill"):
    folder = habits.root / name / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "reflex.yml").write_text(text)
    found = [catalog.read(f / "assets" / "reflex.yml", f.name, description, origin)
             for f in sorted(habits.root.iterdir()) if (f / "assets" / "reflex.yml").exists()]
    habits.monkeypatch.setattr(catalog, "skills", lambda: found)
    habits.monkeypatch.setattr(catalog, "all_habits", lambda: found)


def make(habits, state, name="glance", by="user"):
    habit = catalog.find(name)
    status = habits.ledger.status(name, habit.version)  # shadow, the first sight
    if state == "automatic":
        habits.ledger.approve(name, habit.version, by=by)
    elif state == "resting":
        habits.ledger.set_status(name, habit.version, "retired", "rested", by="user")
    return habit


def load(habits, name="glance", result=SKILL, session="s1", **args):
    return habits.on_transform_tool_result(tool_name="skill_view", args={"name": name, **args}, result=result,
                                           session_id=session)


# ---- the form ---------------------------------------------------------------------------------------------------

def issues(text):
    import yaml
    return [i.message for i in graph.validate(yaml.safe_load(text))]


def test_trigger_skill_validates_only_without_inputs_ask_or_llm():
    assert issues(READ) == []
    assert graph.starts_on_skill({"trigger": "skill"}) and not graph.starts_on_skill({"trigger": "message"})
    assert any("inputs" in m for m in issues(READ.replace("start: look", "inputs: [city]\nstart: look")))
    asking = READ.replace("done: {end", 'ask1: {ask: "Fine?", yes_no: true, next: done}\n  done: {end')
    assert any("decision model" in m for m in issues(asking))
    assert any("llm step" in m for m in issues(READ.replace("look: {do: terminal, read: true, args: {command: \"df -h ~\"}, as: out",
                                                          "look: {llm: \"Say hi\", as: out")))


def test_a_skill_start_is_a_one_off_by_name_and_other_triggers_stay_refused():
    habit = catalog.read  # the kind stays one-off: nothing else about the catalog changes
    from pathlib import Path
    assert catalog.Habit("x", "d", Path("."), {"trigger": "skill"}).kind == "one-off"
    assert any("skill" in m for m in issues(READ.replace("trigger: skill", "trigger: sometimes")))


# ---- each state ---------------------------------------------------------------------------------------------------

def test_skill_view_of_automatic_reflex_adds_a_habits_field(world):
    ctx, habits = world
    make(habits, "automatic")
    out = json.loads(load(habits))
    assert out["content"] == "# Glance" and out["success"] is True
    assert out["habits"].startswith("The reflex glance ran when this skill loaded (automatic): done. ")
    assert "100G free" in out["habits"]
    assert commands(ctx) == ["df -h ~"]


def test_the_run_is_recorded_like_a_run_by_name(world):
    ctx, habits = world
    habit = make(habits, "automatic")
    load(habits)
    rows = habits.ledger._do("SELECT kind, mode, status FROM runs WHERE habit = ?", ("glance",))
    assert [(r["kind"], r["mode"], r["status"]) for r in rows] == [("skill", "adhoc", "end")]


def test_learning_reflex_runs_as_a_test_only_if_it_reads(world):
    ctx, habits = world
    make(habits, "learning")
    out = json.loads(load(habits))
    assert "(a test run, it is learning): done." in out["habits"] and "do the task yourself" in out["habits"]
    assert commands(ctx) == ["df -h ~"]
    save(habits, "glance", ACT)
    make(habits, "learning")
    ctx.ran.clear()
    habits.loaded.clear()
    assert load(habits) is None and ctx.ran == []


def test_learning_off_runs_no_test(world, monkeypatch):
    ctx, habits = world
    make(habits, "learning")
    original = habits.setting
    monkeypatch.setattr(habits, "setting", lambda k: "off" if k == "learning" else original(k))
    assert load(habits) is None and ctx.ran == []


def test_an_automatic_reflex_that_acts_runs_for_real(world):
    ctx, habits = world
    save(habits, "glance", ACT)
    make(habits, "automatic")
    out = json.loads(load(habits))
    assert "(automatic)" in out["habits"] and commands(ctx) == ["touch ~/seen.txt"]


def test_resting_reflex_adds_nothing(world):
    ctx, habits = world
    make(habits, "resting")
    assert load(habits) is None and ctx.ran == []


def test_paused_or_off_adds_nothing(world, monkeypatch):
    ctx, habits = world
    make(habits, "automatic")
    monkeypatch.setenv("HABITS_OFF", "1")
    assert load(habits) is None
    monkeypatch.delenv("HABITS_OFF")
    (habits.home()).mkdir(parents=True, exist_ok=True)
    (habits.home() / "paused").write_text("")
    assert load(habits) is None and ctx.ran == []


def test_curator_cron_or_unseen_session_adds_nothing(world):
    ctx, habits = world
    make(habits, "automatic")
    for platform in ("cron", "curator"):
        habits.platforms["s2"] = platform
        assert load(habits, session="s2") is None
    assert load(habits, session="never-seen") is None
    assert ctx.ran == []


def test_the_pre_llm_call_remembers_the_platform(world):
    ctx, habits = world
    habits.platforms.clear()
    habits.on_pre_llm_call(session_id="s9", user_message="hi", platform="telegram")
    assert habits.platforms["s9"] == "telegram"


def test_other_skill_or_error_result_unchanged(world):
    ctx, habits = world
    save(habits, "plain", DEMO.replace("trigger: message\n", ""))
    make(habits, "automatic")
    make(habits, "automatic", name="plain")
    assert load(habits, name="plain") is None
    assert load(habits, name="nobody", result=json.dumps({"success": True, "name": "nobody"})) is None
    for bad in ('{"success": false, "error": "not found"}', '{"success": true, "error": "x"}', "not json", "[1]", None,
                json.dumps({"success": True, "status": "unchanged", "dedup": True, "content_returned": False})):
        habits.loaded.clear()
        assert load(habits, result=bad) is None
    assert load(habits, file_path="references/x.md") is None  # a side file is not the skill loading
    assert habits.on_transform_tool_result(tool_name="terminal", args={"name": "glance"}, result=SKILL,
                                           session_id="s1") is None
    assert ctx.ran == []


def test_a_shipped_example_is_never_started(world):
    ctx, habits = world
    save(habits, "glance", READ, origin="example")
    make(habits, "automatic")
    assert load(habits) is None and ctx.ran == []


def test_a_qualified_name_finds_the_habit(world):
    ctx, habits = world
    make(habits, "automatic")
    assert "habits" in json.loads(load(habits, name="devops/glance"))


def test_once_per_session(world):
    ctx, habits = world
    make(habits, "automatic")
    assert load(habits) is not None
    assert load(habits) is None
    habits.platforms["s2"] = "cli"
    assert load(habits, session="s2") is not None
    assert len(ctx.ran) == 2


# ---- the web, the cap, the failures --------------------------------------------------------------------------------

def test_a_reflex_that_read_the_web_replies_inside_the_untrusted_block(world):
    ctx, habits = world
    save(habits, "glance", WEB)
    make(habits, "automatic")
    note = json.loads(load(habits))["habits"]
    assert "(automatic): done." in note and "<untrusted_tool_result" in note


def test_a_handed_back_reflex_lists_what_is_left(world, monkeypatch):
    ctx, habits = world
    make(habits, "automatic")
    monkeypatch.setattr(ctx, "dispatch_tool", lambda n, a: json.dumps({"output": "", "exit_code": 1, "error": "boom"}))
    note = json.loads(load(habits))["habits"]
    assert "ran when this skill loaded (automatic): handed back." in note and "Left:" in note


def test_the_cap_is_ten_seconds(world, monkeypatch):
    ctx, habits = world
    make(habits, "automatic")
    seen = {}
    run = habits.run

    def spy(*a, **kw):
        seen.update(kw)
        return run(*a, **kw)
    monkeypatch.setattr(habits, "run", spy)
    load(habits)
    assert seen["max_seconds"] == 10 and seen["dry"] is False


def test_the_skill_text_comes_back_unchanged_on_any_failure(world, monkeypatch):
    ctx, habits = world
    make(habits, "automatic")
    monkeypatch.setattr(habits, "run", lambda *a, **k: 1 / 0)
    assert load(habits) is None
    habits.loaded.clear()
    monkeypatch.undo()
    # a ledger that cannot answer, a catalog that raises
    monkeypatch.setattr(catalog, "find", lambda n: 1 / 0)
    assert load(habits) is None


def test_a_ledger_that_cannot_record_still_gives_the_reply(world, monkeypatch):
    ctx, habits = world
    make(habits, "automatic")
    monkeypatch.setattr(habits.ledger, "record", lambda *a, **k: 1 / 0)
    assert "habits" in json.loads(load(habits))


def test_a_busy_reflex_adds_nothing(world):
    ctx, habits = world
    make(habits, "automatic")
    with habits.held("glance"):
        assert load(habits) is None
    assert ctx.ran == []


# ---- no loop ---------------------------------------------------------------------------------------------------------

def test_no_reentry_when_the_reflex_views_its_own_skill(world, monkeypatch):
    ctx, habits = world
    make(habits, "automatic")
    inner = []
    original = ctx.dispatch_tool

    def dispatch(name, args):  # the reflex's own step loads the skill: the hook fires again inside the run
        inner.append(habits.on_transform_tool_result(tool_name="skill_view", args={"name": "glance"}, result=SKILL,
                                                     session_id="s1"))
        return original(name, args)
    monkeypatch.setattr(ctx, "dispatch_tool", dispatch)
    habits.loaded.clear()
    assert load(habits) is not None
    assert inner == [None] and nursery.ON_LOAD.get() is False


def test_no_reentry_even_in_another_session_while_it_runs(world, monkeypatch):
    ctx, habits = world
    make(habits, "automatic")
    habits.platforms["s2"] = "cli"
    inner = []
    original = ctx.dispatch_tool

    def dispatch(name, args):
        inner.append(load(habits, session="s2"))
        return original(name, args)
    monkeypatch.setattr(ctx, "dispatch_tool", dispatch)
    load(habits)
    assert inner == [None] and commands(ctx) == ["df -h ~"]


def test_a_skill_start_does_not_write_hermess_skill_usage_record(world, monkeypatch):
    ctx, habits = world
    make(habits, "automatic")
    fired = []
    import sys
    import types
    module = types.ModuleType("tools.skill_usage")
    module.bump_use = fired.append
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.skill_usage", module)
    assert load(habits) is not None
    time.sleep(0.05)
    assert fired == []
    assert commands(ctx) == ["df -h ~"]


# ---- the yes, the wiring ---------------------------------------------------------------------------------------------

def test_yes_makes_a_skill_start_automatic(world):
    ctx, habits = world
    make(habits, "learning")
    out = habits.command("yes glance")
    assert "byname" not in out and "called by name" not in out
    habit = catalog.find("glance")
    assert habits.ledger.status("glance", habit.version) == "active"
    assert habits.ledger.history("glance", 1)[0]["by"] == "user"
    habits.loaded.clear()
    assert "(automatic)" in json.loads(load(habits))["habits"]


def test_the_hook_is_registered_and_never_raises(tmp_path):
    ctx = FakeCtx()
    plugin.register(ctx)
    assert "transform_tool_result" in ctx.hooks and "transform_tool_result" in plugin.HOOKS
    hook = ctx.hooks["transform_tool_result"]
    assert hook(tool_name="skill_view", args=None, result=None, session_id=None) is None
    assert hook(tool_name="skill_view", args="x", result=object(), session_id=3) is None
    assert hook(tool_name="terminal", args={}, result="x", session_id="s") is None


def test_an_older_hermes_that_refuses_the_hook_name_changes_nothing():
    class Old(FakeCtx):
        def register_hook(self, name, callback):
            if name == "transform_tool_result":
                raise ValueError("unknown hook")
            super().register_hook(name, callback)
    ctx = Old()
    plugin.register(ctx)
    assert "transform_tool_result" not in ctx.hooks and "pre_llm_call" in ctx.hooks and "habit_run" in ctx.tools


def test_the_manifest_lists_the_hook():
    from pathlib import Path
    text = (Path(plugin.__file__).resolve().parent.parent / "plugin.yaml").read_text()
    assert "  - transform_tool_result" in text
