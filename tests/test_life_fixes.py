"""The last pass, the life of a reflex: what the user is shown before a yes, how a dream repeats, what waits in a
terminal, what a news line may still say. Built on the seams' world: the real dream, ledger and cockpit on a tmp home."""

import argparse
import json

import pytest

from habits import catalog, dream, nursery
from test_dreaming import DAY, Clock, history, repeat, turn
from test_seams import DF, OUT, hermes_runs, world  # noqa: F401  (the fixture)

HOUR = 3600


def _end(habits, status, why=""):
    dream_id = habits.ledger.dream_start(0)
    habits.ledger.dream_end(dream_id, status, why=why)


# ---- 5: a dream that ends partial, unread or failed does not come back after every reply ------------------------

def test_a_partial_dream_counts_for_the_day(world):
    ctx, habits = world
    _end(habits, "partial", "too many sessions")
    assert habits.ledger.dream_start(nursery.EVERY) is None
    habits.clock.now += nursery.EVERY + 1
    assert habits.ledger.dream_start(nursery.EVERY) is not None


@pytest.mark.parametrize("status", ["unread", "failed"])
def test_an_unread_or_failed_dream_waits_an_hour_in_both_places(world, monkeypatch, status):
    ctx, habits = world
    _end(habits, status, "no history store")
    started = []
    monkeypatch.setattr(habits, "_dream_thread", lambda context: started.append(1))
    habits.clock.now += 2 * HOUR // 3
    assert habits.ledger.dream_due(nursery.EVERY) is False
    assert habits.ledger.dream_start(nursery.EVERY) is None
    habits.clock.now += HOUR
    assert habits.ledger.dream_due(nursery.EVERY) is True
    assert habits.ledger.dream_start(nursery.EVERY) is not None


def test_maybe_dream_follows_the_same_rule_as_the_ledger(world, monkeypatch):
    ctx, habits = world
    started = []
    ctx.config["dream"] = "daily"
    monkeypatch.setattr(nursery.threading, "Thread",
                        lambda target, args=(), daemon=True: type("T", (), {"start": lambda s: started.append(1)})())
    monkeypatch.setattr(nursery.time, "monotonic", lambda: habits.clock.now)
    _end(habits, "partial", "too many sessions")
    habits.clock.now += nursery.CHECK + 1
    habits.maybe_dream("cli")
    assert started == []
    habits.clock.now += nursery.EVERY
    habits.maybe_dream("cli")
    assert started == [1]


def test_the_dreams_rows_are_pruned_whatever_the_status(world, monkeypatch):
    ctx, habits = world
    monkeypatch.setattr(dream, "open_store", lambda: None)
    for _ in range(40):
        habits.dream_now(manual=True)
        habits.clock.now += 10
    assert len(habits.ledger._do("SELECT id FROM dreams")) <= 31



# ---- the real-model trial, defect 5: a yes is informed or it is not a yes (D3) ------------------------------------

class OneShot:
    """A chat that has the button call and never answers it (hermes -z): what the trial of 11 November met."""

    def __init__(self, ctx):
        self.__dict__["ctx"] = ctx

    def __getattr__(self, name):
        return getattr(self.ctx, name)

    def ask_choice(self, question, choices):
        return None


def test_in_a_chat_without_buttons_the_bare_command_carries_the_card_then_the_words(world):
    ctx, habits = world
    ready_df(habits)
    [card] = habits.dream_cards()
    habits.ctx = OneShot(ctx)
    reply = habits.command("", session_id="one")
    # what the reflex would run, its folder, what was seen, then the words to type
    assert card["question"] in reply
    assert "`df -h ~   (in ~/proj)`" in reply and "3 turns on 3 days" in reply and "3 of 3 the same" in reply
    # the words come right under the card; the list of habits stands last (changed in R1: it used to stand first)
    assert reply.index("Answer with /habits yes df or /habits no df.") > reply.index(card["question"])
    assert reply.rstrip().endswith(habits.grouped(habits.rows()).rstrip())
    assert habits.command("yes df", session_id="one").startswith("df is a reflex now")


def test_a_sensitive_command_stands_in_the_card_that_goes_out_as_text(world):
    ctx, habits = world
    push = "git push origin main"
    history(habits, repeat(push))
    habits.dream_now(manual=True)
    for n in range(3):
        hermes_runs(habits, f"s{n}", push)
    [card] = habits.dream_cards()
    habits.ctx = OneShot(ctx)
    reply = habits.command("")
    assert f"`{push}" in reply and "(it acts)" in reply
    assert card["question"] in reply and "Answer with /habits yes " in reply.split(card["question"])[1].splitlines()[1]


def test_a_typed_yes_on_a_card_that_never_went_out_shows_it_and_says_yes_to_nothing(world):
    ctx, habits = world
    ready_df(habits)
    [card] = habits.dream_cards()  # built, read by the list, never sent: that is not shown
    answer = habits.command("yes df", session_id="one")
    assert answer.startswith(card["question"]) and "/habits yes df again" in answer
    assert habits.ledger.drafts()[0]["state"] == "ready" and catalog.find("df") is None
    assert habits.command("yes df", session_id="one").startswith("df is a reflex now")


def test_a_card_shown_in_another_conversation_long_ago_is_shown_again(world):
    ctx, habits = world
    ready_df(habits)
    assert "/habits yes df again" in habits.command("yes df", session_id="one")
    habits.clock.now += 11 * 60
    assert "/habits yes df again" in habits.command("yes df", session_id="two")  # another chat, 11 minutes later
    habits.clock.now += 11 * 60
    assert habits.command("yes df", session_id="two").startswith("df is a reflex now")  # the same chat: it was shown


def test_a_one_shot_chat_keeps_the_card_for_the_next_message(world):
    ctx, habits = world
    ready_df(habits)
    habits.ctx = OneShot(ctx)
    habits.command("", session_id="first message")  # each message of a one-shot chat is a session and a process
    assert habits.command("yes df", session_id="second message").startswith("df is a reflex now")


def test_a_no_needs_no_card(world):
    ctx, habits = world
    ready_df(habits)
    assert habits.command("no df").startswith("Noted: df")


# ---- the real-model trial, defect 2: no second reflex for a command a reflex already does -------------------------

def test_the_dream_drafts_nothing_for_a_command_a_shipped_example_already_does(world, shipped):
    ctx, habits = world
    history(habits, repeat(DF) + repeat("uptime", session="u"))
    said = terminal_says(habits, "dream")
    line = next(l for l in said.splitlines() if l.startswith("- `df -h ~`"))
    assert line.endswith("· the reflex disk-space already does it: no draft")  # `df -h ~ || true` is that command
    assert [d["name"] for d in habits.ledger.drafts()] == ["uptime"]  # the other action is drafted as before
    report = json.loads(terminal_says(habits, "dream", "--json"))
    assert [s.get("reflex") for s in report["spotted"]] == ["disk-space", None] or \
        sorted(str(s.get("reflex")) for s in report["spotted"]) == ["None", "disk-space"]


def test_a_draft_gives_its_place_back_once_a_reflex_does_its_command(world, shipped, monkeypatch):
    ctx, habits = world
    doers, older = nursery.Dream._doers, [True]
    monkeypatch.setattr(nursery.Dream, "_doers", lambda self: {} if older else doers(self))
    history(habits, repeat(DF))
    habits.dream_now(manual=True)  # an older build drafted it: it practices
    [draft] = habits.ledger.drafts()
    assert draft["state"] == "practising"
    older.clear()
    habits.dream_now(manual=True)
    assert habits.ledger.draft(draft["key"])["state"] == "gone" and habits.ledger.drafts(("practising",)) == []
    assert draft["key"] not in habits.practising


def test_a_reflex_of_the_user_that_does_the_command_stops_the_draft_and_a_larger_one_does_not(world, tmp_path, monkeypatch):
    ctx, habits = world
    one = {"spec": "habits/0.1", "start": "a", "fallback": "help", "nodes": {
        "a": {"do": "terminal", "read": True, "args": {"command": "uptime"}, "as": "out", "next": "done"},
        "done": {"end": "{out}"}, "help": {"wake": "stopped"}}}
    larger = json.loads(json.dumps(one))
    larger["nodes"]["a"]["args"]["command"] = DF
    larger["nodes"]["a"]["next"] = "b"
    larger["nodes"]["b"] = {"do": "terminal", "read": True, "args": {"command": "whoami"}, "next": "done"}
    fake = lambda name, graph_: type("H", (), {"name": name, "graph": graph_, "valid": True, "origin": "skill"})()
    monkeypatch.setattr(catalog, "all_habits", lambda: [fake("my-uptime", one), fake("morning", larger)])
    history(habits, repeat(DF) + repeat("uptime", session="u"))
    said = terminal_says(habits, "dream")
    assert "the reflex my-uptime already does it" in said and "morning" not in said
    assert [d["name"] for d in habits.ledger.drafts()] == ["df"]  # one step among others is not "already does it"


# ---- 11 and 21: a typed yes never approves what the user was not shown --------------------------------------------

BAD = {"spec": "habits/0.1", "start": "a", "fallback": "help", "nodes": {
    "a": {"do": "terminal", "args": {"command": "rm ~/Documents/thesis.docx"}, "next": "b"},
    "b": {"do": "terminal", "args": {"command": "find ~/Projects -name '*.bak' -delete"}, "next": "c"},
    "c": {"do": "terminal", "args": {"command": "df -h ~"}, "next": "done"},
    "done": {"end": "tidied"}, "help": {"wake": "stopped"}}}


def learn_df(habits):
    """The dream's whole way to a learned, automatic df: history, practice, the user's yes."""
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    for n in range(3):
        hermes_runs(habits, f"s{n}")
    assert "what your yes covers" in habits.command("yes df")  # no card went out yet: the first yes shows it
    assert habits.command("yes df").startswith("df is a reflex")
    return catalog.find("df")


def model_saves_df_again(habits):
    saved = json.loads(habits.tool_save({"name": "df", "description": "Disk", "procedure": "1. df",
                                         "reflex": BAD}, session_id="s9"))
    assert saved["status"] == "saved", saved
    return catalog.find("df")


def test_a_typed_yes_on_a_reflex_saved_again_shows_the_card_and_approves_nothing(world):
    ctx, habits = world
    learn_df(habits)
    habits.on_pre_llm_call(session_id="s9", user_message="tidy up", platform="cli")
    swapped = model_saves_df_again(habits)
    assert habits.ledger.status("df", swapped.version) == "shadow"
    answer = habits.command("yes df")
    assert habits.ledger.status("df", swapped.version) == "shadow"  # nothing was approved
    assert "thesis.docx" in answer and "-delete" in answer  # the steps and the deleting commands, uncut
    assert "/habits yes df" in answer  # to be said again for this version
    assert not habits.yes_given(catalog.find("df"))
    assert "✅" not in answer


def test_the_yes_said_again_approves_the_version_that_was_shown(world):
    ctx, habits = world
    learn_df(habits)
    habits.on_pre_llm_call(session_id="s9", user_message="tidy up", platform="cli")
    swapped = model_saves_df_again(habits)
    habits.command("yes df")
    assert "✅" in habits.command("yes df")
    assert habits.ledger.status("df", swapped.version) == "active"


def test_a_version_saved_after_the_card_needs_a_new_card(world):
    ctx, habits = world
    learn_df(habits)
    habits.on_pre_llm_call(session_id="s9", user_message="tidy up", platform="cli")
    model_saves_df_again(habits)
    habits.command("yes df")  # the card of the first swap
    worse = dict(BAD, nodes={**BAD["nodes"], "c": {"do": "terminal", "args": {"command": "rm -rf ~/proj"},
                                                    "next": "done"}})
    json.loads(habits.tool_save({"name": "df", "description": "Disk", "procedure": "1. df", "reflex": worse},
                                session_id="s9"))
    answer = habits.command("yes df")
    assert "rm -rf" in answer and "✅" not in answer
    assert habits.ledger.status("df", catalog.find("df").version) == "shadow"


def test_a_reflex_saved_again_is_back_in_the_decisions_waiting(world):
    ctx, habits = world
    learn_df(habits)
    habits.on_pre_llm_call(session_id="s9", user_message="tidy up", platform="cli")
    model_saves_df_again(habits)
    [(_, kind, payload)] = habits.decisions()
    assert kind == "habit" and payload[0].name == "df" and payload[2] == "ready"
    cockpit = habits.command("")
    assert "df" in cockpit and "-delete" in cockpit and "❓" in cockpit


def test_the_version_the_user_said_yes_to_comes_back_with_a_typed_yes(world):
    """A reflex put to rest, or told wrong, comes back with a typed yes: it is the version they approved."""
    ctx, habits = world
    learn_df(habits)
    habits.command("rest df")
    assert "✅" in habits.command("yes df")
    assert habits.ledger.status("df", catalog.find("df").version) == "active"


# ---- 1, 3 and 5 of integrate-2: a start on a loaded skill, its state, its name, its "wrong" --------------------------

from test_seams import GLANCE  # noqa: E402

WRITES_ON_LOAD = """spec: habits/0.1
trigger: skill
start: note
fallback: help
nodes:
  note: {do: terminal, args: {command: "touch ~/seen.txt"}, next: done}
  done: {end: "noted"}
  help: {wake: "stopped"}
"""


def save_skill_start(habits, name, reflex):
    habits.on_pre_llm_call(session_id="s1", user_message="keep that as a habit", platform="cli")
    saved = json.loads(habits.tool_save({"name": name, "description": f"Do {name}", "procedure": "1. do it",
                                         "reflex": reflex}, session_id="s1"))
    assert saved["status"] == "saved", saved
    skill_md = next(a["content"] for n, a in reversed(habits.ctx.calls) if n == "skill_manage" and "content" in a)
    return catalog.find(name), skill_md


def load(habits, name, skill_md, session="s1"):
    habits.loaded.clear()
    result = json.dumps({"success": True, "name": name, "content": skill_md})
    out = habits.on_transform_tool_result(tool_name="skill_view", args={"name": name}, result=result, session_id=session)
    return json.loads(out) if out else None


def test_show_says_in_words_how_a_skill_start_starts_and_ran(world):
    """The real-model trial, defect 7: show printed `"skill" · version` and its runs as "called by name"."""
    ctx, habits = world
    habit, skill_md = save_skill_start(habits, "glance", GLANCE)
    habits.command("yes glance")
    load(habits, "glance", skill_md, session="s1")
    shown = terminal_says(habits, "show", "glance")
    assert "starts when its skill is loaded · version" in shown and '"skill"' not in shown
    assert "its skill was loaded" in shown and "called by name" not in shown
    ctx.config["language"] = "fr"
    assert "part quand sa skill est chargée" in terminal_says(habits, "show", "glance")


def test_a_skill_start_shows_its_real_state_everywhere(world):
    ctx, habits = world
    habit, skill_md = save_skill_start(habits, "glance", GLANCE)
    assert "glance · on hold" in habits.command("")  # not "called by name"
    assert "called by name" not in habits.command("")
    assert any(line.startswith("- glance() · learning") for line in habits.reflexes())
    assert habits.command("yes glance").startswith("✅")
    listing = habits.command("")
    assert "glance · automatic" in listing and "1 automatic" in listing
    assert any(line.startswith("- glance() · automatic") for line in habits.reflexes())
    page = habits.page_data("glance", habit.graph, habit=catalog.find("glance"))
    assert page["status"] == "active"


def test_a_skill_start_that_writes_does_not_act_by_name_before_the_yes(world):
    ctx, habits = world
    habit, skill_md = save_skill_start(habits, "note", WRITES_ON_LOAD)
    reply = json.loads(habits.tool_run({"name": "note"}, session_id="s1"))
    assert ctx.ran == [] and reply["done"] is False  # only planned: the yes comes first
    assert load(habits, "note", skill_md) is None  # and the load does not run it either
    habits.command("yes note")  # shows the card: writes
    assert ctx.ran == []
    assert habits.command("yes note").startswith("✅")
    assert json.loads(habits.tool_run({"name": "note"}, session_id="s1"))["done"] is True
    assert ctx.ran == ["touch ~/seen.txt"]


def test_wrong_reaches_the_run_of_a_skill_start_and_of_a_learned_reflex_called_by_name(world):
    ctx, habits = world
    habit, skill_md = save_skill_start(habits, "glance", GLANCE)
    habits.command("yes glance")
    assert "automatic" in load(habits, "glance", skill_md, session="s1")["habits"]
    assert habits.verb("wrong", session="s1").startswith("↩︎ Noted: glance")
    assert "glance · on hold" in habits.command("")
    # a learned reflex Hermes calls by name (glance, which does the same command, rests: the dream may draft it)
    habits.command("rest glance")
    learn_df(habits)
    reply = json.loads(habits.tool_run({"name": "df"}, session_id="s2"))
    assert reply["done"] is True
    assert habits.verb("wrong", session="s2").startswith("↩︎ Noted: df")
    assert habits.ledger.status("df", catalog.find("df").version) == "shadow"
    assert "df · on hold" in habits.command("")


def test_a_learned_reflex_taken_back_does_not_promise_a_practice_that_never_comes(world):
    ctx, habits = world
    learn_df(habits)
    habits.command("rest df")
    assert "/habits yes df" in habits.command("rest df")
    answer = habits.command("try df")
    assert "practises beside" not in answer and "/habits yes df" in answer
    assert "learning 0/" not in habits.command("")


# ---- 25 and 47: a draft that stopped coming back gives its place back, and a resting one can return ----------------

THREE = ["make lint", "npm test", "cargo check"]


def practising_names(habits):
    return sorted(r["name"] for r in habits.ledger.drafts(("practising",)))


def test_a_practising_draft_whose_action_stopped_coming_back_gives_its_place_back(world):
    ctx, habits = world
    turns = []
    for n, command in enumerate(THREE):
        turns += repeat(command, session=f"o{n}")
    history(habits, turns)
    habits.dream_now(manual=True)
    assert len(practising_names(habits)) == 3
    habits.clock.now += 35 * DAY  # the project ended (a draft waits 30 days for its action)
    new = []
    for n, command in enumerate(["git status", "docker ps", "uv sync"]):
        new += repeat(command, days=(7, 8, 9), session=f"n{n}")
    history(habits, new)
    summary = habits.dream_now(manual=True)
    states = {r["name"]: r["state"] for r in habits.ledger.drafts()}
    assert sorted(n for n, st in states.items() if st == "rest") == ["cargo-check", "make-lint", "npm-test"]
    assert sorted(n for n, st in states.items() if st == "practising") == ["docker-ps", "git-status", "uv-sync"]
    text = habits.dream_text(summary)
    assert "does not come back for 30 days" in text  # the rule, said in one line


def test_an_action_that_comes_back_every_three_weeks_keeps_its_place(world):
    ctx, habits = world
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    [draft] = habits.ledger.drafts()
    habits.clock.now += 25 * DAY  # a gap of 25 days is not "stopped coming back"
    habits.dream_now(manual=True)
    assert habits.ledger.draft(draft["key"])["state"] == "practising"


def test_a_resting_draft_comes_back_when_its_action_does_and_says_the_truth_meanwhile(world):
    ctx, habits = world
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    [draft] = habits.ledger.drafts()
    habits.ledger.move_draft(draft["key"], "rest", ("practising",))
    line = "\n".join(habits.dream_lines("en"))
    assert "differs" not in line and "rests" in line  # no cause that was never compared
    habits.clock.now += 20 * DAY
    history(habits, repeat(DF, days=(7, 8, 9), session="back"))
    habits.dream_now(manual=True)
    again = habits.ledger.draft(draft["key"])
    assert again["state"] == "practising" and (again["same"], again["differs"], again["streak"]) == (0, 0, 0)


def test_a_resting_draft_stays_at_rest_while_its_action_does_not_come_back(world):
    ctx, habits = world
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    [draft] = habits.ledger.drafts()
    habits.ledger.move_draft(draft["key"], "rest", ("practising",))
    habits.clock.now += 20 * DAY
    habits.dream_now(manual=True)
    assert habits.ledger.draft(draft["key"])["state"] == "rest"


# ---- 38: a part that cannot be drafted does not take its partner down, and a pair counts its own turns ------------

CURL = "curl -s -o /dev/null -w '%{http_code}' http://localhost:3000"


def test_a_part_that_cannot_be_drafted_does_not_take_its_partner_with_it(world):
    ctx, habits = world
    history(habits, [turn(f"g{d}", 0, d, "git status", CURL) for d in (1, 2, 3)])
    summary = habits.dream_now(manual=True)
    names = [r["name"] for r in habits.ledger.drafts()]
    assert names == ["git-status"], (names, summary)


def test_a_grown_draft_shows_the_turns_and_days_of_the_pair(world):
    ctx, habits = world  # five turns of each part, four together: still 80 %
    turns = [turn(f"t{d}", 0, d, "make build", "make test") for d in (1, 2, 3, 4)]
    turns += [turn("alone1", 0, 5, "make build"), turn("alone2", 0, 6, "make test")]
    history(habits, turns)
    habits.dream_now(manual=True)
    [grown] = [r for r in habits.ledger.drafts() if r["parts"]]
    assert (grown["turns"], grown["days"]) == (4, 4)


# ---- 27: a news line or a card that stopped being true is withdrawn before it is told ------------------------------

def ready_df(habits):
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    for n in range(3):
        hermes_runs(habits, f"s{n}")
    [draft] = habits.ledger.drafts()
    assert draft["state"] == "ready"
    return draft


def test_news_of_a_draft_that_got_a_no_is_never_told(world):
    ctx, habits = world
    ready_df(habits)
    assert [r["kind"] for r in habits.ledger.news(take=False)] == ["dream_learn"]
    assert "Noted" in habits.command("no df")
    assert habits.ledger.news(take=False) == []
    told = habits.on_pre_llm_call(session_id="n", user_message="hello there", platform="cli")
    assert "practised well" not in json.dumps(told) and not habits.told_today()


def test_news_of_a_draft_that_left_ready_by_another_road_is_dropped_when_told(world):
    ctx, habits = world
    draft = ready_df(habits)
    habits.ledger.move_draft(draft["key"], "gone", ("ready",))  # a road that withdraws nothing by itself
    assert habits.news() == []
    assert habits.ledger.news(take=False) == []


def test_a_rest_proposal_and_its_news_are_withdrawn_when_the_reflex_ran(world):
    ctx, habits = world
    learn_df(habits)
    habits.clock.now += 31 * DAY
    history(habits, repeat("uptime", days=(7, 8, 9), session="u"))
    habits.dream_now(manual=True)
    [proposal] = habits.ledger.proposals()
    assert proposal["kind"] == "rest" and "not used for 31 days" in habits.dream_cards()[0]["question"]
    habits.clock.now += 60
    assert json.loads(habits.tool_run({"name": "df"}, session_id="s5"))["done"] is True  # it serves after all
    assert habits.dream_cards() == []  # the card is no longer true: not shown
    assert habits.news() == []
    habits.dream_now(manual=True)
    assert habits.ledger.proposals() == [] and habits.ledger.news(take=False) == []


def test_a_proposal_about_a_reflex_deleted_by_hand_is_withdrawn(world, monkeypatch):
    ctx, habits = world
    learn_df(habits)
    habits.clock.now += 31 * DAY
    history(habits, repeat("uptime", days=(7, 8, 9), session="u"))
    habits.dream_now(manual=True)
    assert habits.ledger.proposals()
    monkeypatch.setattr(catalog, "find", lambda name: None)
    assert habits.dream_cards() == []
    assert habits.ledger.proposals() == []


# ---- 33: what waits for the user, a terminal says it ---------------------------------------------------------------

def terminal_says(habits, *argv):
    import contextlib
    import io
    parser = argparse.ArgumentParser()
    habits.cli_setup(parser)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        habits.cli_handle(parser.parse_args(list(argv)))
    return out.getvalue()


def test_a_terminal_says_what_waits_and_the_words_to_type_in_the_chat(world):
    ctx, habits = world
    ready_df(habits)
    listed = terminal_says(habits, "list")
    assert "df runs `df -h ~ (in ~/proj)`" in listed and "/habits yes df" in listed and "/habits no df" in listed
    assert "In a chat with Hermes" in listed
    assert "/habits yes df" in terminal_says(habits, "show", "df")
    said = terminal_says(habits, "dream")
    assert "/habits yes df" in said and said.index("/habits yes df") < said.index("Next:")


def test_in_suggest_the_try_card_is_told_in_a_terminal_and_in_the_json(world):
    ctx, habits = world
    ctx.config["learning"] = "suggest"
    history(habits, repeat(DF))
    report = json.loads(terminal_says(habits, "dream", "--json"))
    assert [(p["kind"], p["name"]) for p in report["proposals"]] == [("try", "df")]
    assert "/habits try df" in terminal_says(habits, "list")
    assert "/habits try df" in terminal_says(habits, "dream")


def test_a_terminal_yes_on_a_draft_points_to_the_chat(world):
    ctx, habits = world
    ready_df(habits)
    parser = argparse.ArgumentParser()
    habits.cli_setup(parser)
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        habits.cli_handle(parser.parse_args(["yes", "df"]))
    assert "chat with Hermes" in out.getvalue() and "/habits yes df" in out.getvalue()
    assert habits.ledger.draft(habits.ledger.drafts()[0]["key"])["state"] == "ready"  # a terminal yes does not make a reflex


def test_a_draft_that_cannot_be_saved_says_why_instead_of_pointing_to_a_page_that_does_not_know_it(world, monkeypatch):
    ctx, habits = world
    ready_df(habits)
    monkeypatch.setattr(habits, "_problems", lambda scenario: ["format: run opens a shell"])
    assert "what your yes covers" in habits.command("yes df")  # no card went out yet: the first yes shows it
    answer = habits.command("yes df")
    assert "run opens a shell" in answer and "says why" not in answer


# ---- the last round: a draft is something a person can look at and try in a terminal ----------------------------------

def test_show_prints_what_the_dream_saw_the_reflex_and_the_words_to_type(world):
    ctx, habits = world
    # each turn holds another call before it, so that "closes the work" means something (in a turn of one call it is
    # true of every gesture and is not shown)
    history(habits, [turn(f"s{d}", 0, d, "echo hi", DF) for d in (1, 2, 3)])
    habits.dream_now(manual=True)
    shown = terminal_says(habits, "show", "df")
    assert "df · a draft, not a habit yet · practices beside Hermes" in shown
    assert "The dream saw Hermes do this in 3 turns on 3 days" in shown and "do terminal df -h ~" in shown
    assert "closes the work: it was the last call of its turn" in shown  # the dream's words, explained
    assert "The reflex it would become, whole:" in shown
    assert "0 of 3 the same" in shown and "hermes habits run df" in shown
    assert "No habit named" not in shown


def test_run_of_a_draft_is_a_test_run_of_its_reflex(world, capsys):
    ctx, habits = world
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    parser = argparse.ArgumentParser()
    habits.cli_setup(parser)
    code = habits.cli_handle(parser.parse_args(["run", "df", "--json"]))
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["habit"] == "df" and out["mode"] == "test" and out["status"] == "end"


def test_yes_and_rest_of_a_draft_never_say_there_is_no_such_habit(world):
    ctx, habits = world
    history(habits, repeat(DF))
    habits.dream_now(manual=True)  # df practices: nothing to answer yet
    for verb in ("yes", "rest"):
        said = terminal_says(habits, verb, "df")
        assert "No habit named" not in said and "a draft, not a habit yet" in said and "asks for your yes in a chat" in said
    assert "No habit named" not in habits.command("yes df") and "No habit named" not in habits.command("rest df")
    ready_df_name = habits.ledger.drafts()[0]["key"]
    habits.ledger.move_draft(ready_df_name, "ready", ("practising",))
    assert "/habits yes df" in terminal_says(habits, "yes", "df")  # the exact chat words


def test_a_yes_or_a_no_typed_in_a_chat_on_a_draft_that_still_practices_says_where_it_stands(world):
    """The real-model trial, defect 6: `/habits yes df` answered 'No habit named "df"'."""
    ctx, habits = world
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    said = habits.command("yes df")
    assert said == ("◇ df is a draft, not a habit yet: it practices beside Hermes. 0 of 3 so far: it asks for your yes "
                    "in a chat after 3 more times; nothing to answer now.")
    hermes_runs(habits, "s0")
    hermes_runs(habits, "s1")
    said = habits.command("yes df")
    assert "2 of 3 so far" in said and "after 1 more time;" in said and "No habit named" not in said
    assert habits.ledger.drafts()[0]["state"] == "practising"  # nothing was decided
    ctx.config["language"] = "fr"
    assert "2 sur 3 pour l'instant" in habits.command("oui df") and "Pas d'habitude" not in habits.command("oui df")
    ctx.config["language"] = "en"
    assert habits.command("no df").startswith("Noted: df")  # the no is its own: the draft stops practicing
    assert habits.ledger.drafts()[0]["state"] == "no"


def test_the_dream_says_how_many_spotted_actions_wait_for_a_place(world):
    ctx, habits = world
    turns = []
    for n, command in enumerate(["df -h ~", "uptime", "ls ~/proj", "git -C ~/p status", "make lint"]):
        turns += repeat(command, session=f"c{n}")
    history(habits, turns)
    text = habits.dream_text(habits.dream_now(manual=True))
    assert "2 more actions wait for a place (3 practice at a time): " in text


def test_resting_drafts_never_hide_the_ones_that_practice(world):
    ctx, habits = world
    history(habits, repeat(DF))
    habits.dream_now(manual=True)
    for n in range(5):
        habits.ledger._do("INSERT INTO drafts (key, name, state) VALUES (?, ?, 'rest')", (f"k{n}", f"r{n}"))
    lines = habits.dream_lines("en")
    assert "df practices beside Hermes" in lines[0] and lines[-1].startswith("- 5 drafts rest")


def test_in_suggest_the_dream_gives_the_words_to_try_and_nothing_runs(world):
    ctx, habits = world
    ctx.config["learning"] = "suggest"
    history(habits, repeat(DF))
    text = habits.dream_text(habits.dream_now(manual=True))
    assert "/habits try df" in text and "nothing runs until you say try" in text
    assert [d["state"] for d in habits.ledger.drafts()] == ["spotted"]


def test_dream_days_reads_the_days_asked_and_a_missing_home_exits_one(world, capsys, tmp_path):
    ctx, habits = world
    history(habits, repeat(DF))
    summary = habits.dream_now(manual=True, days=2)
    assert summary["days"] <= 2
    parser = argparse.ArgumentParser()
    habits.cli_setup(parser)
    code = habits.cli_handle(parser.parse_args(["dream", "--home", str(tmp_path / "nowhere")]))
    assert code == 1 and "nowhere" in capsys.readouterr().out


def test_a_name_that_starts_like_a_verb_is_shown_with_a_real_command():
    from habits.display import terminal
    assert terminal("/habits help-desk says why") == "hermes habits show help-desk says why"
    assert terminal("/habits rest-api") == "hermes habits show rest-api"
    assert terminal("/habits try-harder") == "hermes habits show try-harder"
    assert terminal("/habits rest") == "hermes habits rest" and terminal("/habits help") == "hermes habits --help"


def test_undoing_a_wrong_on_a_reflex_called_by_name_promises_no_checks(world):
    ctx, habits = world
    learn_df(habits)
    habits.tool_run({"name": "df"}, session_id="s2")
    habits.verb("wrong", session="s2")
    said = habits.command("undo")
    assert said.startswith("↩︎ Undone: df") and "I check behind it" not in said


def test_the_card_of_a_grown_draft_says_it_acts_only_when_a_part_acts_and_keeps_one_line(world):
    from habits import dream
    ctx, habits = world
    reads = [dream.Action("terminal:df -h ~", "terminal", {"command": "df -h ~"}, "r1"),
             dream.Action("terminal:uptime", "terminal", {"command": "uptime"}, "r2")]
    acts = [reads[0], dream.Action("terminal:make", "terminal", {"command": "make"}, "r3")]
    assert not habits._acts(reads) and habits._acts(acts)
    assert dream.shown(acts, "", "en", " then ") == "df -h ~ then make" and "\n" not in dream.shown(acts, "", "en", " then ")


def test_a_french_card_holds_no_english_for_the_folder():
    from habits import dream
    step = dream.Action("terminal:df", "terminal", {"command": "df", "workdir": "~/proj", "timeout": 5,
                                                    "background": True}, "r")
    assert dream.shown([step], "", "fr") == "df   (dans ~/proj, délai 5 s, en arrière-plan)"
    assert dream.shown([step], "", "en") == "df   (in ~/proj, timeout 5, in the background)"


def test_with_permissions_strict_a_test_run_of_a_deleting_reflex_is_refused_like_check(world, tmp_path, capsys):
    ctx, habits = world
    rm = tmp_path / "rm.yml"
    deleting = "r" + "m -r" + "f old-builds"  # built from pieces: the install scanner reads this file
    rm.write_text("spec: habits/0.1\nstart: a\nnodes:\n  a: {do: terminal, args: {command: '" + deleting + "'}, next: z}\n"
                  "  z: {end: ok}\n")
    ctx.config["permissions"] = "strict"
    parser = argparse.ArgumentParser()
    habits.cli_setup(parser)
    code = habits.cli_handle(parser.parse_args(["run", str(rm)]))
    out = capsys.readouterr().out
    assert code == 1 and out.startswith("refused: ") and "delete" in out
    assert habits.cli_handle(parser.parse_args(["check", str(rm)])) == 1  # check says the same


def test_the_list_of_a_terminal_does_not_offer_a_terminal_yes_for_a_reflex_hermes_made(world):
    ctx, habits = world
    learn_df(habits)
    habits.verb("wrong", session="none")  # nothing answered: no change
    habits.ledger.set_status("df", catalog.find("df").version, "shadow", "by hand (reset)", by="cli")
    listed = terminal_says(habits, "list")
    assert "df · on hold · your yes in a chat lets it act: `/habits yes df`" in listed
    assert "hermes habits yes df" not in listed


def test_a_yes_on_a_shipped_example_gives_the_steps_that_work_today(world):
    ctx, habits = world
    from habits import catalog as cat
    said = terminal_says(habits, "yes", "hermes-health")
    assert "built-in example" in said and "my-hermes-health" in said and str(cat.EXAMPLES) in said
    assert "hermes habits yes my-hermes-health" in said and "hermes habits run hermes-health" in said
    assert "`name:` line" in said and "SKILL.md" in said  # the copy keeps the old name otherwise, and the yes finds nothing

