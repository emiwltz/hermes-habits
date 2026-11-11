"""What a person sees of the dream's drafts: a look writes nothing, every draft is named, `show` says what would run in
full, the moments are all printed, and a test run of a draft says what it only planned."""

import re

import pytest

from test_dreaming import cli, dreams, history, repeat, turn, world  # noqa: F401  (the fixture and the history helpers)
from habits import display, dream

LONG = ("grep -rn --include=*.py 'needs review' /Users/someone/work/a-very-long-project-name/src/package/subpackage "
        "/Users/someone/work/a-very-long-project-name/tests/unit/test_something_rather_long.py")


def draft(world, command=None, name="a-draft", tool="terminal", args=None, moments=None, cwd="", state=None):
    """A draft written straight into the ledger, as the dream would have."""
    args = args if args is not None else {"command": command}
    action = dream.Action(dream.key_of(tool, args), tool, args, dream.raw_of(tool, args))
    world.ledger.put_draft(action.key, name=name, tool=tool, args=args, raw=action.raw, parts=[],
                           graph=dream.draft_reflex([action], cwd), reads=0, turns=4, days=3,
                           moments=moments or {}, cwd=cwd)
    if state:
        world.ledger.move_draft(action.key, state, ("spotted",))
    return action.key


# ---- 1. a look never writes ------------------------------------------------------------------------------------------

def snapshot(world):
    return ([dict(r) for r in world.ledger.drafts()], dreams(world), world.ledger.news(take=False))


def test_a_look_at_some_days_prints_that_reading_and_writes_nothing(world, capsys):
    history(world, repeat("df -h ~", days=(1, 2, 3, 4, 5, 6)))
    world.dream_now(manual=True)                        # the plain dream: 6 turns on 6 days
    before = snapshot(world)
    assert cli(world, "--days", "8") == 0               # a look at the last 8 days: 4 of those days
    text = capsys.readouterr().out
    assert "4 turns on 4 days" in text and "6 turns on 6 days" not in text
    assert "This was a look: nothing was saved." in text and "`hermes habits dream`" in text
    assert "wait for you" not in text and "Next:" not in text      # no card, no next step: nothing was kept
    assert snapshot(world) == before                    # the 60-day counts, the dream rows, the news: as they were
    assert cli(world, "--days", "8", "--json") == 0
    assert '"look": "days"' in capsys.readouterr().out.replace('":"', '": "')
    world.ctx.config["language"] = "fr"
    cli(world, "--days", "8")
    assert "Ceci était un coup d'œil : rien n'a été gardé." in capsys.readouterr().out


def test_a_look_on_a_fresh_ledger_leaves_it_empty(world, capsys):
    history(world, repeat("df -h ~"))
    assert cli(world, "--days", "30") == 0
    assert "df -h ~" in capsys.readouterr().out
    assert world.ledger.drafts() == [] and dreams(world) == [] and world.ledger.proposals() == []


def test_the_plain_dream_still_writes_its_drafts(world, capsys):
    history(world, repeat("df -h ~"))
    assert cli(world) == 0
    assert "This was a look" not in capsys.readouterr().out
    assert len(world.ledger.drafts()) == 1 and len(dreams(world)) == 1


# ---- 2. a person can see everything that waits ------------------------------------------------------------------------

def fourteen(world):
    commands = [f"echo-{n}" for n in range(14)]
    turns = []
    for n, command in enumerate(commands):
        turns += repeat(f"ls ~/folder{n}", session=f"c{n}")
    history(world, turns)


def test_the_first_dream_names_every_draft_and_never_claims_earlier_readings(world, capsys):
    world.ctx.config["learning"] = "suggest"
    fourteen(world)
    assert cli(world) == 0
    text = capsys.readouterr().out
    names = [d["name"] for d in world.ledger.drafts()]
    assert len(names) == 14 and all(name in text for name in names)         # the 3 cards and the others, by name
    more = next(line for line in text.splitlines() if line.startswith("+11 more wait"))
    assert "…" not in more
    assert "earlier readings" not in text and "come from" not in text


def test_the_dream_that_practices_names_the_drafts_that_wait_for_a_place(world):
    turns = []
    for n in range(6):
        turns += repeat(f"ls ~/folder{n}", session=f"c{n}")
    history(world, turns)
    text = world.dream_text(world.dream_now(manual=True))
    names = [d["name"] for d in world.ledger.drafts()]
    assert all(name in text for name in names)
    assert "3 more actions wait for a place (3 practice at a time): " in text


def test_list_prints_each_draft_with_its_state_names_first(world):
    world.ctx.config["learning"] = "suggest"
    history(world, repeat("ls ~/a") + repeat("ls ~/b", session="b"))
    world.dream_now(manual=True)
    text = world.listing()
    rows = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(rows) == 2 and all(re.match(r"- [\w-]+ · waits for your try · `ls ~/[ab]`$", row) for row in rows)
    assert "No habits of your own yet" not in text and "hermes habits show <name>" in text
    world.ctx.config["learning"] = "practice"
    world.dream_now(manual=True)
    rows = [line for line in world.listing().splitlines() if line.startswith("- ")]
    assert all(" · practices 0 of 3 · " in row for row in rows)
    world.ctx.config["language"] = "fr"
    assert "s'entraîne : 0 sur 3" in world.listing() and "Pas encore d'habitude" not in world.listing()


def test_list_says_it_when_a_draft_rests_or_is_ready(world):
    key = draft(world, "ls ~/a", "a-ready")
    world.ledger.move_draft(key, "ready", ("spotted",))
    draft(world, "ls ~/b", "b-rest", state="rest")
    draft(world, "ls ~/c", "c-wait")
    rows = [line for line in world.listing().splitlines() if line.startswith("- ")]
    assert [row.split(" · ")[:2] for row in rows] == [["- a-ready", "ready: waits for your yes"],
                                                      ["- c-wait", "waits for a place"], ["- b-rest", "rests"]]


def test_no_habits_yet_is_still_said_when_nothing_waits(world):
    assert "No habits of your own yet" in world.listing()


# ---- 3. show says what would run, in full ------------------------------------------------------------------------------

def test_show_prints_a_long_command_whole_wrapped_between_words(world):
    draft(world, LONG, "long-grep", cwd="/Users/someone/work")
    shown = world.draft_detail("long-grep")
    assert "…" not in shown
    lines = shown.splitlines()
    assert max(len(line) for line in lines) <= display.WIDTH
    flat = " ".join(" ".join(shown.split()).split())
    assert LONG in flat                                                     # every word, in order
    for path in re.findall(r"/\S+", LONG):                                  # a path is on one line, never cut
        assert any(path in line for line in lines)
    assert "(in /Users/someone/work)" in flat                              # and the folder


def test_show_prints_the_path_of_a_read_and_the_arguments_of_a_tool_call(world):
    draft(world, name="read-it", tool="read_file", args={"path": "/Users/someone/docs/runbook.md", "limit": 200})
    shown = world.draft_detail("read-it")
    assert '"path": "/Users/someone/docs/runbook.md"' in shown and '"limit": 200' in shown
    draft(world, name="co2", tool="mcp_homeassistant_get_state", args={"entity_id": "sensor.office_co2"})
    shown = world.draft_detail("co2")
    assert "do mcp_homeassistant_get_state" in shown and '"entity_id": "sensor.office_co2"' in shown


def test_show_prints_the_whole_end_and_hand_back_texts(world):
    draft(world, "uptime", "up")
    shown = world.draft_detail("up")
    assert 'wake "The reflex could not run uptime: do it the usual way."' in shown


def test_a_cut_always_ends_with_an_ellipsis_and_never_inside_a_word():
    text = "The reflex could not run /Users/someone/a-very-long-path/that/never/ends/here.txt then go on and on"
    words = text.split()
    for size in range(5, 90):
        cut = display._cut(text, size)
        if cut == text:
            continue
        assert cut.endswith("…") and cut[:-1].split()[-1] in words, (size, cut)
    assert display._cut("/Users/someone/a-very-long-path/that/never/ends", 10) == "/Users/someone/a-very-long-path/that/never/ends"


def test_the_dream_list_line_that_says_where_the_whole_text_is_is_true(world):
    history(world, repeat("curl -s -H 'X-Foo: bar' http://x.test/a"))
    summary = world.dream_now(manual=True)
    name = summary["spotted"][0]["name"]
    assert f"hermes habits show {name}" in world.dream_text(summary)
    shown = world.draft_detail(name)
    assert "curl -s -H 'X-Foo: bar' http://x.test/a" in shown


# ---- 4. the moments ------------------------------------------------------------------------------------------------------

def test_show_prints_the_moment_after_a_skill_the_ledger_keeps_as_a_list(world):
    draft(world, "ls ~/a", "after-skill", moments={"skill": ["data-cleaning", 1.0], "last": 0.6})
    shown = world.draft_detail("after-skill")
    assert "after the skill data-cleaning (100%)" in shown
    key = draft(world, "ls ~/b", "other", moments={"skill": ["data-cleaning", 1.0]}, state="ready")
    assert "after the skill data-cleaning" in world.listing()               # the card of a list says it too


def test_a_moment_that_says_something_wins_a_tie_over_first_and_last(world):
    draft(world, "ls ~/a", "tie", moments={"first": 1.0, "last": 1.0, "skill": ["x-skill", 1.0], "scheduled": 1.0})
    shown = world.draft_detail("tie")
    assert "after the skill x-skill" in shown and "in a scheduled job" in shown
    assert "closes the work (" not in shown and "first gesture of a session (" not in shown


def test_the_legend_is_printed_only_when_a_card_above_it_uses_a_moment_it_explains(world):
    draft(world, "ls ~/a", "skill-only", moments={"skill": ["x-skill", 1.0]})
    draft(world, "ls ~/b", "closes", moments={"last": 1.0}, cwd="")
    assert "closes the work:" not in world.draft_detail("skill-only")
    assert "closes the work: it was the last call of its turn" in world.draft_detail("closes")
    assert world.legend_for([{"scheduled": 1.0}], "en") == ""
    assert world.legend_for([{"skill": {"name": "x", "share": 1.0}}, {"first": 0.9}], "en") != ""
