"""The dream wired into the plugin: the real core (dream.py) reads a hand-made history (the fake store of
dream_cases.py), the real ledger keeps the drafts, and the plugin is the real one on a tmp home."""

import argparse
import json
import sqlite3
import time

import pytest

from dream_cases import OK, FakeStore, call, row, tool, user
from habits import catalog, dream, guard, nursery, plugin
from test_plugin import FakeCtx, Inline

DAY = 86400


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A plugin on a tmp home; history(world, turns) sets what Hermes kept; world.reads lists the windows read."""
    monkeypatch.setattr(catalog, "skills", lambda: [])
    ctx = FakeCtx({"learning": "practice"})  # the default is suggest: these tests are about a draft that practices
    habits = plugin.Habits(ctx, home=tmp_path / "plugin-data" / "habits")
    clock = Clock()
    habits.ledger.clock = clock
    monkeypatch.setattr(nursery.time, "time", clock)
    habits.clock, habits.store, habits.reads = clock, FakeStore([], {}), []
    monkeypatch.setattr(dream, "open_store", lambda: habits.store)
    read_turns = dream.read_turns

    def spy(store, now, days=dream.HISTORY_DAYS, **kw):
        habits.reads.append(days)
        return read_turns(store, now, days, **kw)

    monkeypatch.setattr(dream, "read_turns", spy)
    return habits


def turn(session, index, day, *commands, result=True):
    """A turn of a day (1 to 9: the days before the clock's), each command one terminal call. result=False: the
    call's result is not stored yet (a turn in flight)."""
    return (session, index, day, commands, result)


def repeat(command, days=(1, 2, 3), per_day=1, session="s"):
    return [turn(f"{session}{d}", i, d, command) for d in days for i in range(per_day)]


def history(world, turns):
    """What Hermes kept: one session row per session name, its turns in order, as Hermes's store gives them."""
    midnight = time.mktime(time.localtime(world.clock())[:3] + (0, 0, 0, 0, 0, -1))
    sessions = {}
    for session, index, day, commands, result in sorted(turns, key=lambda t: (t[0], t[1])):
        at = midnight - (10 - day) * DAY + 9 * 3600 + index * 60
        messages = sessions.setdefault(session, [])
        messages.append(user(at))
        for n, command in enumerate(commands):
            cid = f"{session}-{index}-{n}"
            messages.append(call(at + 1, "terminal", {"command": command}, cid))
            if result:
                messages.append(tool(at + 2, cid, OK))
    rows = [row(sid, messages[-1]["timestamp"], started=messages[0]["timestamp"]) for sid, messages in sessions.items()]
    world.store = FakeStore(rows, sessions)


def dreams(world):
    return [dict(r) for r in world.ledger._do("SELECT * FROM dreams ORDER BY id")]


def cli(world, *argv):
    parser = argparse.ArgumentParser()
    world.cli_setup(parser)
    return world.cli_handle(parser.parse_args(["dream", *argv]))


# ---- the dream does what the plan says --------------------------------------------------------------------------

def test_dream_off_does_nothing(world, capsys):
    world.ctx.config["learning"] = "off"
    history(world, repeat("df -h ~"))
    assert world.dream_now()["status"] == "off"
    assert dreams(world) == [] and world.reads == []
    assert cli(world) == 0
    assert "does not run" in capsys.readouterr().out


def test_dream_now_spots_and_practises_three_at_most(world):
    turns = []
    for n, command in enumerate(["df -h ~", "uptime", "ls ~/proj", "git -C ~/p status", "notes wake"]):
        turns += repeat(command, session=f"c{n}")
    history(world, turns)
    summary = world.dream_now(manual=True)
    states = sorted(r["state"] for r in world.ledger.drafts())
    assert states == ["practising"] * 3 + ["spotted"] * 2
    assert summary["status"] == "ok" and len(summary["spotted"]) == 5 and len(summary["practising"]) == 3
    assert world.ledger.last_dream()["status"] == "ok"


def test_suggest_leaves_every_draft_spotted(world):
    world.ctx.config["learning"] = "suggest"
    history(world, repeat("df -h ~"))
    world.dream_now(manual=True)
    assert [r["state"] for r in world.ledger.drafts()] == ["spotted"]


def test_dream_counts_invalid_drafts(world, capsys):
    history(world, repeat("awk '{print}' f") + repeat("echo ${HOME}", session="e"))
    summary = world.dream_now(manual=True)
    assert world.ledger.drafts() == []
    assert summary["left"] == 6 and summary["status"] == "ok"
    assert "Left out on purpose: 6 turns" in world.dream_text(summary)


def test_a_turn_left_out_for_two_reasons_counts_once(world):
    history(world, [turn(f"s{d}", 0, d, "echo hi", "cat ~/.zshrc") for d in (1, 2, 3)])
    summary = world.dream_now(manual=True)
    assert summary["left"] == 3  # three turns, each left out for two reasons (noise, secrets)
    assert "Left out on purpose: 3 turns" in world.dream_text(summary)


def test_a_turn_with_an_invalid_draft_and_a_noise_gesture_counts_once(world):
    history(world, [turn(f"s{d}", 0, d, "awk '{print}' f", "echo hi") for d in (1, 2, 3)])
    assert world.dream_now(manual=True)["left"] == 3


def test_habits_off_says_habits_is_off_not_learning(world, monkeypatch, capsys):
    monkeypatch.setenv("HABITS_OFF", "1")
    history(world, repeat("df -h ~"))
    summary = world.dream_now(manual=True)
    assert summary["status"] == "off" and summary["why"] == "habits"
    assert world.dream_text(summary).startswith("Habits is off")
    assert "Learning" not in world.dream_text(summary)
    assert dreams(world) == []
    world.ctx.config["language"] = "fr"
    assert world.dream_text(summary).startswith("Habits est coupé")
    monkeypatch.delenv("HABITS_OFF")
    world.ctx.config["learning"] = "off"
    assert world.dream_text(world.dream_now(manual=True)).startswith("L'apprentissage est coupé")


def test_fresh_home_is_empty_not_an_error(world, capsys, monkeypatch):
    history(world, [])
    assert cli(world) == 0
    assert "holds no turn from the last 60 days" in capsys.readouterr().out
    assert world.ledger.drafts() == [] and world.ledger.news(take=False) == []
    assert cli(world, "--days", "7") == 0
    assert "last 7 days" in capsys.readouterr().out
    # `hermes habits list` with nothing in it names the dream
    monkeypatch.setattr(catalog, "examples", lambda: [])
    assert "hermes habits dream" in world.listing()
    world.store = None  # no store to open at all: unread, not an exception
    assert cli(world) == 0
    assert "could not be read (no history store)" in capsys.readouterr().out


def test_near_lines_when_nothing_is_spotted(world):
    history(world, [turn("a", 0, 1, "df -h ~"), turn("b", 0, 1, "df -h ~"), turn("c", 0, 1, "uptime"),
                    turn("d", 0, 2, "uptime")])
    summary = world.dream_now(manual=True)
    assert summary["spotted"] == [] and len(summary["near"]) == 2
    assert {n["action"]: n["missing"] for n in summary["near"]} == {"df -h ~": {"turns": 1, "days": 1},
                                                                    "uptime": {"turns": 1, "days": 0}}
    text = world.dream_text(summary)
    assert "Nothing comes back often enough yet" in text and "- closest: `df -h ~` · 2 turns on 1 day" in text


def test_dream_read_shows_the_real_span(world):
    history(world, [turn(f"s{d}", 0, d, "x") for d in range(1, 6)])
    assert "over 5 days" in world.dream_text(world.dream_now(manual=True))


def test_days_is_clamped_and_passed(world):
    history(world, [])
    world.dream_now(manual=True, days=7)
    world.dream_now(manual=True, days=900)
    assert world.reads == [7, 60]
    with pytest.raises(SystemExit):
        cli(world, "--days", "0")
    with pytest.raises(SystemExit):
        cli(world, "--days", "61")


def test_reflex_deleted_by_hand_counts_as_no(world):
    history(world, repeat("df -h ~"))
    world.dream_now(manual=True)
    key = world.ledger.drafts()[0]["key"]
    world.ledger.move_draft(key, "saved", ("practising",), habit="df")
    world.dream_now(manual=True)  # df is no skill on disk (catalog.skills is empty)
    row = world.ledger.draft(key)
    assert row["state"] == "no" and row["no_until"] > world.clock() + 29 * DAY


def test_a_part_of_a_dream_is_not_redrafted_while_it_lives(world):
    history(world, repeat("df -h ~"))
    world.dream_now(manual=True)
    row = world.ledger.drafts()[0]
    world.ledger.move_draft(row["key"], "ready", ("practising",))
    world.dream_now(manual=True)
    assert [r["state"] for r in world.ledger.drafts()] == ["ready"] and world.ledger.drafts()[0]["name"] == row["name"]


def saved_reflex(world, monkeypatch, name="df"):
    """The draft of `df -h ~` after the yes: a saved reflex, its skill on disk, its habit automatic."""
    history(world, repeat("df -h ~", days=(7, 8, 9)))
    world.dream_now(manual=True)
    key = world.ledger.drafts()[0]["key"]
    assert world.ledger.move_draft(key, "saved", ("practising",), habit=name)
    world.ledger.adopt(name, "v1", "learned")
    world.ledger.approve(name, "v1", by="user")
    find = catalog.find
    monkeypatch.setattr(catalog, "find", lambda n: object() if n == name else find(n))
    return key


def test_change_and_rest_proposals_are_capped_and_tell_one_line(world, monkeypatch):
    saved_reflex(world, monkeypatch)
    monkeypatch.setattr(dream, "propose", lambda spotted, habits, now: [
        dream.Proposal("rest", "df", {"why": "quiet", "quiet": 31}),
        dream.Proposal("change", "df", {"sibling": "terminal:df -h /", "raw": "df -h /", "quiet": 15})])
    summary = world.dream_now(manual=True)
    assert [p["kind"] for p in summary["proposals"]] == ["rest", "change"]
    news = [r for r in world.ledger.news(take=False) if r["kind"] == "dream_proposal"]
    assert len(news) == 1  # one line of news per dream
    assert world.dream_now(manual=True)["proposals"] == summary["proposals"]  # an open proposal is not made twice


def test_a_reflex_quiet_in_the_history_rests_only_if_it_did_not_run(world, monkeypatch):
    """Once Hermes calls the reflex, its action leaves the history: its own runs say it still serves."""
    saved_reflex(world, monkeypatch)
    world.clock.now += 31 * DAY
    world.ledger._do("INSERT INTO runs (habit, version, kind, mode, at, status) VALUES ('df', 'v1', 'call', 'adhoc', ?, "
                     "'end')", (world.clock() - DAY,))
    assert world.dream_now(manual=True)["proposals"] == []
    world.ledger._do("DELETE FROM runs")
    assert [p["kind"] for p in world.dream_now(manual=True)["proposals"]] == ["rest"]


def test_rest_applies_alone_when_learning_is_auto(world, monkeypatch):
    saved_reflex(world, monkeypatch)
    world.ctx.config["learning"] = "auto"
    world.clock.now += 31 * DAY
    assert world.dream_now(manual=True)["proposals"] == []
    assert world.ledger.info("df")["status"] == "retired" and world.ledger.history("df", 1)[0]["by"] == "rules"


# ---- when it runs ------------------------------------------------------------------------------------------------

def test_daily_dream_after_reply_once_per_20h(world, monkeypatch):
    world.ctx.config["dream"] = "daily"
    monkeypatch.setattr(plugin.threading, "Thread", Inline)
    history(world, repeat("df -h ~"))
    world.on_post_llm_call(session_id="s", assistant_response="ok", platform="cli")
    assert len(dreams(world)) == 1 and dreams(world)[0]["status"] == "ok"
    world.clock.now += 3 * 3600  # the process looks again after 10 minutes; the rule says 20 h
    world.dream_checked = None
    world.on_post_llm_call(session_id="s", assistant_response="ok", platform="cli")
    assert len(dreams(world)) == 1
    world.clock.now += 18 * 3600
    world.dream_checked = None
    world.on_post_llm_call(session_id="s", assistant_response="ok", platform="cli")
    assert len(dreams(world)) == 2
    assert cli(world) == 0 and len(dreams(world)) == 3  # the command ignores the rule


def test_the_daily_dream_really_starts_in_a_background_thread(world, monkeypatch):
    """No stand-in for the thread: a reply that ends with no dream in the last 20 h starts a daemon thread that
    runs the dream; and the test contexts say `manual` unless a test asks for the daily rule."""
    import threading
    history(world, repeat("df -h ~"))
    world.on_post_llm_call(session_id="s", assistant_response="ok", platform="cli")
    assert dreams(world) == []  # the test context says: dream manual
    world.ctx.config["dream"] = "daily"
    ran, started = threading.Event(), []
    real = world._dream_thread

    def thread(context):
        started.append(threading.current_thread())
        real(context)
        ran.set()

    monkeypatch.setattr(world, "_dream_thread", thread)
    world.dream_checked = None
    world.on_post_llm_call(session_id="s", assistant_response="ok", platform="cli")
    assert ran.wait(10) and started[0].daemon and started[0] is not threading.main_thread()
    assert [d["status"] for d in dreams(world)] == ["ok"] and dreams(world)[0]["manual"] == 0


def test_daily_dream_never_in_manual_cron_curator_or_off(world, monkeypatch):
    world.ctx.config["dream"] = "daily"
    monkeypatch.setattr(plugin.threading, "Thread", Inline)
    history(world, repeat("df -h ~"))
    for platform in ("cron", "curator"):
        world.dream_checked = None
        world.on_post_llm_call(session_id="s", assistant_response="ok", platform=platform)
    world.ctx.config["dream"] = "manual"
    world.dream_checked = None
    world.on_post_llm_call(session_id="s", assistant_response="ok", platform="cli")
    world.ctx.config.update(dream="daily", learning="off")
    world.dream_checked = None
    world.on_post_llm_call(session_id="s", assistant_response="ok", platform="cli")
    assert dreams(world) == []


def test_the_check_is_throttled_to_ten_minutes(world, monkeypatch):
    world.ctx.config["dream"] = "daily"
    monkeypatch.setattr(plugin.threading, "Thread", Inline)
    history(world, [])
    world.on_post_llm_call(session_id="s", assistant_response="ok", platform="cli")
    first = len(dreams(world))
    world.clock.now += 21 * 3600  # not the monotonic clock: still within 10 minutes of this process's last look
    world.on_post_llm_call(session_id="s", assistant_response="ok", platform="cli")
    assert first == 1 and len(dreams(world)) == 1


def test_failed_dream_does_not_block_the_next(world, monkeypatch):
    world.ledger._do("INSERT INTO dreams (started, status, manual) VALUES (?, 'running', 0)", (world.clock() - 2 * 3600,))
    history(world, repeat("df -h ~"))
    world.dream_now()  # the daily rule; the lock is free, so the old row is a dead process's
    assert [d["status"] for d in dreams(world)] == ["failed", "ok"]
    with monkeypatch.context() as patch:
        patch.setattr(dream, "spot", lambda *a, **k: 1 / 0)
        summary = world.dream_now(manual=True)  # never an exception
        assert (summary["status"], summary["why"], summary["spotted"]) == ("failed", "ZeroDivisionError", [])
    assert dreams(world)[-1]["status"] == "failed"
    assert world.dream_now(manual=True)["status"] == "ok"


def test_a_ledger_error_is_not_told_as_hermes_history_being_unreadable(world, monkeypatch, capsys):
    history(world, repeat("df -h ~"))
    with monkeypatch.context() as patch:
        patch.setattr(world.ledger, "dream_start", lambda *a, **k: (_ for _ in ()).throw(sqlite3.DatabaseError("disk image is malformed")))
        summary = world.dream_now(manual=True)
        assert (summary["status"], summary["why"]) == ("failed", "DatabaseError")
        for lang in ("en", "fr"):
            monkeypatch.setattr(world, "lang", lambda lang=lang: lang)
            text = world.dream_text(summary)
            assert "DatabaseError" in text and "could not be read" not in text and "pu être lu" not in text
            assert "Habits" in text
        monkeypatch.undo()
        patch.setattr(world.ledger, "dream_start", lambda *a, **k: (_ for _ in ()).throw(sqlite3.DatabaseError("x")))
        assert cli(world) == 0
        out = capsys.readouterr().out
        assert "error of Habits itself (DatabaseError)" in out and "history could not be read" not in out


def test_a_failed_dream_does_not_count_for_the_day(world, monkeypatch):
    world.ctx.config["dream"] = "daily"
    monkeypatch.setattr(plugin.threading, "Thread", Inline)
    history(world, repeat("df -h ~"))
    with monkeypatch.context() as patch:
        patch.setattr(dream, "spot", lambda *a, **k: 1 / 0)
        world.maybe_dream("cli")
    world.dream_checked = None
    world.maybe_dream("cli")  # the next reply, minutes later: a dream that failed waits an hour
    assert [d["status"] for d in dreams(world)] == ["failed"]
    world.clock.now += 3601
    world.dream_checked = None
    world.maybe_dream("cli")  # an hour later it tries again
    assert [d["status"] for d in dreams(world)] == ["failed", "ok"]


def test_dream_thread_takes_target_args_daemon(world, monkeypatch):
    world.ctx.config["dream"] = "daily"
    made = []

    class Spy(Inline):
        def __init__(self, target, args=(), daemon=None):
            made.append((target, args, daemon))
            super().__init__(target, args, daemon)

    monkeypatch.setattr(plugin.threading, "Thread", Spy)
    history(world, [])
    world.maybe_dream("cli")
    assert len(made) == 1 and made[0][2] is True and made[0][0] == world._dream_thread
    assert world.workers == []  # not kept: a process that ends drops it


def test_two_processes_dreaming_at_once(world, tmp_path, monkeypatch):
    other = plugin.Habits(FakeCtx(), home=world.home())
    history(world, repeat("df -h ~"))
    with world.held("_dream") as free:
        assert free
        assert other.dream_now(manual=True)["status"] == "busy"
        assert cli(other) == 0
    assert dreams(world) == []  # the busy one wrote nothing
    assert other.dream_now(manual=True)["status"] == "ok"
    assert len(world.ledger.drafts()) == 1  # drafts not duplicated
    assert other.dream_now(manual=True)["status"] == "ok" and len(world.ledger.drafts()) == 1


def test_turn_in_flight_while_dreaming(world):
    open_turn = turn("live", 0, 4, "df -h ~", result=False)  # stored, its result and its reply not yet
    history(world, repeat("df -h ~", days=(1, 2)) + [open_turn])
    assert world.dream_now(manual=True)["spotted"][0]["turns"] == 3
    assert world.dream_now(manual=True)["spotted"][0]["turns"] == 3  # recounted from zero, never twice


def test_history_locked_or_unknown_is_silent(world, capsys):
    world.store = FakeStore([], {}, fail_page=0, fail_with=sqlite3.OperationalError("database is locked"))
    assert cli(world) == 0
    assert "could not be read (OperationalError)" in capsys.readouterr().out
    assert world.ledger.news(take=False) == [] and world.ledger.drafts() == []
    assert dreams(world)[-1]["status"] == "unread"
    world.store = FakeStore([], {}, fail_page=0, fail_with=OSError("locked"))
    assert cli(world) == 0 and "could not be read (OSError)" in capsys.readouterr().out


# ---- how it is said ------------------------------------------------------------------------------------------------

def test_cli_dream_json_uses_contract_words(world, capsys):
    history(world, repeat("df -h ~"))
    assert cli(world, "--json") == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert set(data) == {"status", "turns", "sessions", "days", "took_ms", "spotted", "near", "left", "practicing",
                         "proposals", "why"}
    assert set(data["spotted"][0]) == {"action", "turns", "days", "moments", "name"}  # name: where its draft is
    assert set(data["practicing"][0]) == {"name", "action", "same", "shape", "needed"}
    for word in ("shadow", "active", "graph", "node", "scenario", "promote"):
        assert word not in out


def test_the_dream_report_reads_in_order(world, capsys):
    history(world, repeat("df -h ~") + repeat("uptime", days=(1, 2), per_day=2, session="u"))
    cli(world)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Read 7 turns in 5 sessions over 3 days")
    assert lines[1] == "2 actions come back:" and lines[2].startswith("- `df -h ~` · 3 turns on 3 days")
    # was "... · first gesture of a session (100%) · closes the work (100%)": each turn holds this one call, so
    # neither moment says anything and none is shown
    assert lines[2] == "- `df -h ~` · 3 turns on 3 days"
    assert "- df practices beside Hermes · 0 of 3 the same (it runs once every 10 minutes at most)" in lines  # a read: it is compared
    assert "- uptime practices beside Hermes · 0 of 3 the same (it runs once every 10 minutes at most)" in lines
    assert lines[-1].startswith("Next: keep working with Hermes")


def test_the_report_says_an_action_came_back_not_that_it_matched(world):
    history(world, repeat("git -C ~/p pull"))  # no read: the draft is planned, never run
    text = world.dream_text(world.dream_now(manual=True))
    assert "- git-pull practices beside Hermes · came back 0 of 3 times" in text and "the same" not in text


def test_chat_dream_replies_with_the_report_and_never_says_yes(world):
    history(world, repeat("df -h ~"))
    reply = world.command("dream", "cli")
    assert reply.startswith("Read 3 turns") and "yes" not in reply.lower().split("practises")[0]
    assert [r["state"] for r in world.ledger.drafts()] == ["practising"]
    assert world.command("rêve", "cli").startswith("Read 3 turns")


def test_habits_shows_what_practises_after_the_habits(world):
    history(world, repeat("df -h ~"))
    world.dream_now(manual=True)
    shown = world.command("")
    assert "- df practices beside Hermes · 0 of 3 the same (it runs once every 10 minutes at most)" in shown
    world.ledger._do("UPDATE drafts SET reads = 0")  # an action: only planned, so the line counts its returns
    assert "came back 0 of 3 times" in world.command("")
    for n in range(4):
        world.ledger._do("INSERT INTO drafts (key, name, state) VALUES (?, ?, 'rest')", (f"k{n}", f"r{n}"))
    lines = world.dream_lines("en")
    # the resting drafts are one line, after the ones that practice: they never hide them
    assert len(lines) == 2 and "df practices beside Hermes" in lines[0] and lines[1].startswith("- 4 drafts rest")


def test_french_lines(world, capsys):
    world.ctx.config["language"] = "fr"
    history(world, repeat("df -h ~"))
    cli(world)
    out = capsys.readouterr().out
    assert "Lu 3 tours" in out and "s'entraîne" in out and "Ensuite" in out
    world.ctx.config["learning"] = "off"
    assert "L'apprentissage est coupé" in world.dream_text(world.dream_now())
    assert "rests" not in "\n".join(world.dream_lines("fr"))


def test_blocked_paths_are_the_plugins_own_list(world, tmp_path):
    assert world.blocked("~/.hermes/config.yaml")
    assert world.blocked(str(tmp_path / "credentials.json"))
    assert world.blocked(str(tmp_path / "notes.txt")) == ""


# ---- the news a day ----------------------------------------------------------------------------------------------------

def test_one_news_line_a_day(world):
    world.ctx.config["dream"] = "manual"  # no dream behind these replies
    for name in ("alpha", "beta"):
        world.ledger.put_draft(name, name=name)
        world.ledger.propose("rest", name, {"quiet": 40})  # the news tells a proposal that is open
        world.ledger.tell(name, "dream_proposal")
    first = world.on_pre_llm_call(session_id="s1", user_message="hello there", platform="cli")
    assert "alpha" in first["context"] and "beta" not in first["context"]
    world.on_post_llm_call(session_id="s1", assistant_response="hi", platform="cli")
    assert world.on_pre_llm_call(session_id="s2", user_message="and again", platform="cli") is None  # same day
    world.clock.now += DAY
    second = world.on_pre_llm_call(session_id="s3", user_message="next day", platform="cli")
    assert "beta" in second["context"]


def test_dream_news_is_not_dropped_as_unknown_habit(world):
    world.ledger.put_draft("k", name="df-h")
    world.ledger.propose("rest", "df-h", {"quiet": 40})
    world.ledger.tell("df-h", "dream_proposal")
    world.ledger.tell("ghost", "dream_proposal")  # no habit, no draft: its news goes with it
    rows = world.news(daily=True)
    assert [r["habit"] for r in rows] == ["df-h"]
    assert "a proposal about df-h is waiting" in world.news_line(rows[0])
    world.ctx.config["language"] = "fr"
    assert "une proposition sur df-h" in world.news_line(rows[0])


def test_tell_counts_toward_the_day(world):
    world.ctx.config["dream"] = "manual"  # no dream behind these replies
    posted = []

    class Handle:
        ok = True

    world.ctx.post_message = lambda text: posted.append(text) or Handle()
    world.ledger.put_draft("k", name="a")
    world.ledger.put_draft("l", name="b")
    world.ledger.propose("rest", "a", {"quiet": 40})
    world.ledger.propose("rest", "b", {"quiet": 40})
    world.ledger.tell("a", "dream_proposal")
    world.ledger.tell("b", "dream_proposal")
    assert world.tell() == 1 and len(posted) == 1  # the second waits for tomorrow
    world.clock.now += DAY
    assert world.tell() == 1


# ---- the guard ---------------------------------------------------------------------------------------------------------

def blocked_run(world, command):
    found = world.on_pre_tool_call(tool_name="terminal", args={"command": command})
    return bool(found and found["action"] == "block")


def test_model_cannot_run_dream(world):
    for command in ("hermes habits dream", "hermes habits dream --json", "hermes habits dream --days 3"):
        assert blocked_run(world, command), command
    assert not blocked_run(world, "hermes habits list")


def test_guard_blocks_the_new_chat_verbs(world):
    for command in ("hermes chat -q '/habits no x'", "echo '/habits dream' | hermes chat", "hermes -z '/habits non x'",
                    "hermes -z '/habits rêve'"):
        assert guard.SLASH.search(command), command
    assert not guard.SLASH.search("/habits now") and not guard.SLASH.search("/habits dreamer")
    assert "learning" in guard.OWN_SETTINGS


def test_the_verbs_are_known_in_both_languages(world):
    from habits.cockpit import VERBS
    assert {VERBS[w] for w in ("no", "non")} == {"no"} and {VERBS[w] for w in ("dream", "rêve", "reve")} == {"dream"}


def test_rest_applied_alone_is_told_in_the_one_line_of_the_day(world, monkeypatch):
    saved_reflex(world, monkeypatch)
    world.ctx.config["learning"] = "auto"
    world.clock.now += 31 * DAY
    world.dream_now(manual=True)
    [news] = [r for r in world.ledger.news(take=False) if r["habit"] == "df"]
    assert news["kind"] == "retired" and "month" in news["note"]
    assert "df" in world.news_line(news) and "month" in world.news_line(news)


# ---- the last round (G2): the report shows an act short, a saved reflex alone is no draft, no folder known ----------------

def test_the_report_and_json_show_an_act_short_and_say_where_the_whole_command_is(world):
    command = "curl -s -H 'X-Foo: bar' http://x.test/a"
    history(world, repeat(command))
    summary = world.dream_now(manual=True)
    assert summary["spotted"][0]["action"] == "curl …"
    assert summary["practising"][0]["action"] == "curl …"
    text = world.dream_text(summary)
    name = summary["spotted"][0]["name"]
    assert command not in text and "`curl …`" in text and "acts: hermes habits show <name> prints its full text" in text
    assert f"in full: hermes habits show {name}" in text   # the sentence is true: that command prints it whole
    assert world.ledger.drafts()[0]["args"]["command"] == command           # what is stored does not change


def test_a_read_and_a_plain_act_show_as_written_and_the_note_stays_away(world):
    history(world, repeat("df -h ~") + repeat("git pull", session="g"))
    text = world.dream_text(world.dream_now(manual=True))
    assert "`df -h ~`" in text and "`git pull`" in text and "prints its full text" not in text


def test_a_group_that_is_split_does_not_draft_a_saved_reflex_on_its_own(world, monkeypatch):
    saved_reflex(world, monkeypatch, "df")
    bad = "curl -s -o /dev/null -w '%{http_code}' http://localhost:3000"
    turns = []
    for d in (4, 5, 6):
        at = time.mktime(time.localtime(world.clock())[:3] + (0, 0, 0, 0, 0, -1)) - (10 - d) * DAY + 9 * 3600
        turns.append((f"g{d}", at))
    sessions, rows = {}, []
    for sid, at in turns:
        sessions[sid] = [user(at), call(at + 1, "habit_run", {"name": "df"}, "a"), tool(at + 2, "a", OK),
                         call(at + 3, "terminal", {"command": bad}, "b"), tool(at + 4, "b", OK)]
        rows.append(row(sid, at + 4, started=at))
    world.store = FakeStore(rows, sessions)
    world.dream_now(manual=True)
    assert [d["name"] for d in world.ledger.drafts() if d["state"] in ("spotted", "practising")] == []


def test_a_command_that_depends_on_its_folder_and_has_none_is_only_counted_not_run(world):
    history(world, repeat("ls | wc -l"))
    world.store.rows = [dict(r, cwd="") for r in world.store.rows]
    world.dream_now(manual=True)
    [d] = world.ledger.drafts()
    assert d["reads"] == 0 and d["state"] == "practising"
    assert dream.local_read("terminal", d["args"], d.get("cwd") or "") is False


def test_one_command_in_three_session_folders_is_drafted_without_a_folder(world):
    history(world, [turn(f"s{d}", 0, d, "git pull") for d in (1, 2, 3)])
    for r, folder in zip(world.store.rows, ("/Users/x/a", "/Users/x/b", "/Users/x/c")):
        r["cwd"] = folder
    world.dream_now(manual=True)
    [d] = world.ledger.drafts()
    assert d["cwd"] == "" and "workdir" not in d["graph"]["nodes"]["run"]["args"]


# ---- the count of what comes back and the count of what waits both tell the truth (11 Nov) ---------------------------

def older_draft(world, command, name):
    key = dream.key_of("terminal", {"command": command})
    action = dream.Action(key, "terminal", {"command": command}, dream.raw_of("terminal", {"command": command}))
    world.ledger.put_draft(key, name=name, tool="terminal", args=action.args, raw=action.raw, parts=[],
                           graph=dream.draft_reflex([action]), reads=0, turns=4, days=3, moments={}, cwd="")


def test_the_dream_says_how_many_drafts_wait_when_that_is_not_what_came_back(world):
    world.ctx.config["learning"] = "suggest"
    history(world, repeat("df -h ~"))
    older_draft(world, "make lint", "make-lint")
    older_draft(world, "git status", "git-status")
    text = world.dream_text(world.dream_now(manual=True))
    assert "1 action comes back:" in text
    assert "3 drafts wait for you in all" in text and "hermes habits list" in text
    assert "earlier readings" not in text   # never claimed: on a first dream they all come from this reading
    world.ctx.config["language"] = "fr"
    assert "3 brouillons attendent en tout" in world.dream_text(world.dream_now(manual=True))


def test_the_dream_adds_nothing_when_the_counts_agree(world):
    world.ctx.config["learning"] = "suggest"
    history(world, repeat("df -h ~"))
    text = world.dream_text(world.dream_now(manual=True))
    assert "1 action comes back:" in text and "in all" not in text
