"""The dream's core: reading history, keys, exclusions, spotting, moments, grouping, drafts, comparing."""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import time

import pytest

from habits import cockpit, dream, graph
from tests import dream_cases as dc
from tests.dream_cases import FakeStore, call, day, row, session, sh, store_of, tool, user, wrapped

NOTES = dc.NOTES
NOW = time.time()


def read(store, days=60, **kw):
    return dream.read_turns(store, time.time(), days, **kw)


def A(command, tool_name="terminal", args=None):
    args = args if args is not None else {"command": command}
    return dream.Action(dream.key_of(tool_name, args), tool_name, args, dream.raw_of(tool_name, args))


# ---- keys ---------------------------------------------------------------------------------------------------------

def test_key_collapses_white_space_and_trailing_trimmers():
    for command in ("~/bin/notes wake | tail -40", "~/bin/notes  wake 2>&1", '~/bin/notes wake; echo "EXIT=$?"',
                    "~/bin/notes wake 2>&1 | head -n 20", "~/bin/notes wake 2>/dev/null | cat"):
        assert dream.key_of("terminal", {"command": command}) == "terminal:~/bin/notes wake"


def test_key_of_read_file_is_its_path():
    assert dream.key_of("read_file", {"path": "~/a/b.md/"}) == "read_file:~/a/b.md"
    assert dream.key_of("read_file", {"file_path": "~/a/b.md"}) == "read_file:~/a/b.md"


def test_key_of_other_tool_is_canonical_json():
    assert dream.key_of("web_search", {"query": "x", "limit": 3}) == dream.key_of("web_search", {"limit": 3, "query": "x"})
    assert dream.key_of("mcp__files__list", {"dir": "a"}) == 'mcp__files__list:{"dir":"a"}'
    assert dream.key_of("terminal", {}) is None
    assert dream.key_of("terminal", {"command": "x" * 3000}) is None


@pytest.mark.parametrize("tool_name,args,why", [
    ("terminal", {"command": "sleep 25; echo done"}, "noise"),
    ("terminal", {"command": "cd ~/x"}, "noise"),
    ("terminal", {"command": "echo hi"}, "noise"),
    ("terminal", {"command": "cat ~/.hermes/.env"}, "secrets"),
    ("terminal", {"command": "cat ~/proj/.env.local"}, "secrets"),
    ("terminal", {"command": "cat ~/.zshenv"}, "secrets"),
    ("terminal", {"command": "grep KEY ~/.zshrc"}, "secrets"),
    ("terminal", {"command": "cat ~/.netrc"}, "secrets"),
    ("terminal", {"command": "ls ~/.ssh"}, "secrets"),
    ("terminal", {"command": "env"}, "secrets"),
    ("terminal", {"command": "printenv HOME"}, "secrets"),
    ("terminal", {"command": "curl -u me:pw https://x.test"}, "secrets"),
    ("terminal", {"command": "git clone https://me:pw@host.test/r"}, "secrets"),
    ("read_file", {"path": "~/proj/credentials.json"}, "secrets"),
    ("terminal", {"command": 'curl -H "Authorization: Bearer abcdefgh12345678" https://x.test'}, "key"),
    ("terminal", {"command": "echo $API_KEY | wc -c"}, "key"),
    ("terminal", {"command": "rm -rf ~/x"}, "deletes"),
    ("terminal", {"command": "hermes habits promote x"}, "settings"),
    ("terminal", {"command": "hermes config set a b"}, "settings"),
    ("habit_run", {"name": "x"}, "own"),
    ("todo", {}, "bookkeeping"),
    ("skill_view", {"name": "notes"}, "moment"),
    ("execute_code", {"code": "1"}, "code"),
    ("terminal", {}, "long"),
    ("terminal", {"command": NOTES}, ""),
    ("terminal", {"command": "env HOME=/x ls"}, ""),
    ("read_file", {"path": "~/notes/today.md"}, ""),
])
def test_excluded_reasons(tool_name, args, why):
    assert dream.excluded(tool_name, args) == why


def test_excluded_asks_blocked_about_paths():
    blocked = lambda path: "private" if path.endswith("config.yaml") else ""  # noqa: E731
    assert dream.excluded("terminal", {"command": "cat ~/project/config.yaml"}, blocked) == "secrets"
    assert dream.excluded("terminal", {"command": "cat ~/project/other.yaml"}, blocked) == ""


# ---- calls, failures ----------------------------------------------------------------------------------------------

def test_calls_of_unwraps_tool_call_in_order():
    message = wrapped(1.0, [("terminal", {"command": "a"}), ("read_file", {"path": "p"}), ("terminal", {"command": "b"})], "w9")
    got = dream.calls_of(message)
    assert got == [("terminal", {"command": "a"}, "w9"), ("read_file", {"path": "p"}, "w9"),
                   ("terminal", {"command": "b"}, "w9")]
    assert dream.calls_of(call(1.0, "tool_call", {"nothing": 1}, "x")) == []
    assert dream.calls_of(call(1.0, "tool_call", {"calls": "not json"}, "x")) == []
    assert dream.calls_of(call(1.0, "terminal", "{bad json", "x")) == [("terminal", {}, "x")]
    top = {"role": "assistant", "tool_calls": [{"id": "t", "name": "terminal", "arguments": {"command": "z"}}]}
    assert dream.calls_of(top) == [("terminal", {"command": "z"}, "t")]
    assert dream.calls_of({"role": "assistant", "tool_calls": 5}) == []


@pytest.mark.parametrize("content,expected", [
    ({"status": "blocked"}, True), ({"status": "pending_approval"}, True), ({"status": "denied"}, True),
    ({"error": "no"}, True), ({"success": False}, True),
    ({"exit_code": -1}, True), ({"exit_code": 126}, True), ({"exit_code": 127}, True),
    ({"output": "x", "exit_code": 1}, False), ("plain text", False), ({}, False), ({"error": ""}, False),
    ({"exit_code": 0, "success": True}, False), (None, False),
])
def test_failed_reads_status_only(content, expected):
    text = content if isinstance(content, str) or content is None else json.dumps(content)
    assert dream.failed({"role": "tool", "content": text}) is expected


# ---- reading history ----------------------------------------------------------------------------------------------

def test_read_turns_window_and_turns():
    s = session("a", [(40, 9), (3, 9), (3, 11), (1, 20)], [[sh("old")], [sh("x")], [sh("y"), sh("z")], []])
    r = read(store_of(s), days=30)
    assert r.status == "ok" and r.sessions == 1
    assert [t.index for t in r.turns] == [1, 2, 3]            # index counts from the row's first turn
    assert [len(t.calls) for t in r.turns] == [1, 2, 0]       # a turn with no call is kept
    assert [c.order for c in r.turns[1].calls] == [0, 1]
    assert r.turns[0].day == time.strftime("%Y-%m-%d", time.localtime(day(3, 9)))
    assert not r.turns[0].first and r.span == 3
    assert not r.turns[0].scheduled and r.turns[0].cwd == "~/proj"
    first = read(store_of(session("b", [(2, 9)], [[sh("x")]]))).turns[0]
    assert first.first and first.index == 0


def test_read_turns_scheduled_source():
    r = read(store_of(session("c", [(1, 6)], [[sh("git pull")]], source="cron")))
    assert r.turns[0].scheduled


def test_read_turns_old_row_before_recent_one():
    old = session("root", [(40, 9)], [[sh("old")]], end_reason="compression")
    child_row, child_msgs = session("child", [(2, 9)], [[sh("new")]], parent_session_id="root")
    other = session("recent", [(2, 10)], [[sh("recent")]])
    # the chain's tip sorts the root with its child; a fully old row sorts after
    stale = session("stale", [(45, 9)], [[sh("stale")]])
    store = store_of(stale, old, (child_row, child_msgs), other)
    r = read(store, days=30)
    assert r.status == "ok"
    assert {c.args["command"] for t in r.turns for c in t.calls} == {"new", "recent"}


def test_read_turns_old_row_listed_first_does_not_end_listing(monkeypatch):
    monkeypatch.setattr(dream, "PAGE", 2)
    rows = [session("o1", [(50, 9)], [[sh("a")]], end_reason="compression"),
            session("o2", [(51, 9)], [[sh("a")]]),
            session("recent", [(1, 9)], [[sh("keep")]], parent_session_id="o1")]
    r = read(store_of(*rows), days=30)
    assert [c.args["command"] for t in r.turns for c in t.calls] == ["keep"]


def test_read_turns_skips_subagents_and_marks_continuations():
    root = session("root", [(5, 9)], [[sh("a")]], end_reason="compression")
    cont = session("cont", [(4, 9)], [[sh("b")]], parent_session_id="root")
    sub1 = session("sub1", [(3, 9)], [[sh("c")]] * 20, source="subagent")
    sub2 = session("sub2", [(3, 9)], [[sh("c")]], model_config={"_delegate_from": "root"})
    sub3 = session("sub3", [(3, 9)], [[sh("c")]], model_config=json.dumps({"_delegate_from": "root"}))
    r = read(store_of(root, cont, sub1, sub2, sub3))
    assert r.sessions == 2
    by = {t.session: t for t in r.turns}
    assert set(by) == {"root", "cont"} and by["root"].first and not by["cont"].first


def test_read_turns_branch_reads_only_its_own_turns():
    parent = session("p", [(10, 9), (9, 9)], [[sh("x")], [sh("y")]])
    branch_row, msgs = session("br", [(10, 9), (9, 9), (1, 9)], [[sh("x")], [sh("y")], [sh("new")]],
                               model_config=json.dumps({"_branched_from": "p"}))
    branch_row["started_at"] = day(1, 8)          # created yesterday; copies keep their old timestamps
    r = read(store_of(parent, (branch_row, msgs)))
    mine = [t for t in r.turns if t.session == "br"]
    assert [c.args["command"] for t in mine for c in t.calls] == ["new"]
    assert not mine[0].first                       # it is the branch's third message, not its first


def test_read_turns_marks_failed_calls():
    s = session("f", [(1, 9)], [[sh("chmod 600 ~/x"), sh("ls")]], fail=(1,))
    r = read(store_of(s))
    calls = r.turns[0].calls
    assert [c.failed for c in calls] == [True, False]
    assert not hasattr(calls[0], "content")


def test_read_turns_chained_session_counts_each_day():
    s = session("tg", [(d, 9) for d in (69, 8, 6, 4, 2, 0)], [[sh("a")]] * 6)
    r = read(store_of(s), days=60)
    assert len({t.day for t in r.turns}) == 5 and r.status == "ok"       # the 69-day-old turn is out of the window
    assert r.span == 9


def test_read_turns_fails_soft():
    assert read(None).status == "unread" and read(None).why == "no history store"
    assert read(dream.EmptyStore()).status == "empty"
    assert read(object()).why == "unknown history shape"
    locked = FakeStore([], {}, fail_page=0, fail_with=sqlite3.OperationalError("database is locked"))
    r = read(locked)
    assert (r.status, r.why) == ("unread", "OperationalError") and locked.closed
    rows = [session(f"s{i}", [(1, 9)], [[sh("a")]])[0] for i in range(2)]
    msgs = {r["id"]: [user(day(1, 9)), call(day(1, 9), "terminal", {"command": "a"}, "c")] for r in rows}
    # a second page that raises: partial (PAGE patched small by the test below); here, unreadable messages are skipped
    store = FakeStore(rows + [{"source": "cli"}, "junk"], msgs, fail_messages={"s0"})
    r = read(store)
    assert r.skipped >= 2 and r.sessions == 1 and r.status == "ok"
    weird = FakeStore([row("w", day(1, 9))], {"w": [user(day(1, 9)), 5, {"nope": 1}, {"role": "tool"},
                                                       {"role": "assistant", "tool_calls": "[[["}]})
    r = read(weird)
    assert r.skipped == 2 and r.turns[0].calls == ()


def test_read_turns_second_page_failing_is_partial(monkeypatch):
    monkeypatch.setattr(dream, "PAGE", 2)
    rows = [session(f"s{i}", [(1, 9)], [[sh("a")]]) for i in range(5)]
    store = store_of(*rows, fail_page=1, fail_with=RuntimeError("x"))
    r = read(store)
    assert r.status == "partial" and r.turns


def test_read_turns_falls_back_when_keywords_are_refused():
    store = store_of(session("a", [(1, 9)], [[sh("x")]]), strict=True)
    assert read(store).status == "ok"


def test_read_turns_stops_on_budget(monkeypatch):
    rows = [session(f"s{i}", [(1, 9)], [[sh("a")]]) for i in range(6)]
    monkeypatch.setattr(dream, "PAGE", 2)
    monkeypatch.setattr(dream, "MAX_SESSIONS", 4)
    r = read(store_of(*rows))
    assert r.status == "partial" and r.why

    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(dream, "MAX_SESSIONS", 3000)
    r = read(store_of(*rows), clock=lambda: next(ticks), max_seconds=25)
    assert r.status == "partial" and r.why == "out of time"


# ---- spotting -----------------------------------------------------------------------------------------------------

def turns_of(*pairs):
    """Turns from [(days_ago, [commands])]: one session per pair."""
    sessions = [session(f"t{i}", [(d, 10)], [[sh(c) for c in cmds]]) for i, (d, cmds) in enumerate(pairs)]
    return read(store_of(*sessions)).turns


def test_spot_three_turns_two_days():
    spotted, left, near = dream.spot(turns_of((0, [NOTES]), (0, [NOTES]), (0, [NOTES])))
    assert spotted == [] and near[0].turns == 3 and near[0].days == 1
    spotted, _, _ = dream.spot(turns_of((0, [NOTES]), (1, [NOTES]), (1, [NOTES])))
    assert [s.action.key for s in spotted] == ["terminal:" + NOTES] and (spotted[0].turns, spotted[0].days) == (3, 2)
    spotted, _, _ = dream.spot(turns_of((0, [NOTES] * 10), (1, [NOTES]), (2, [NOTES])))
    assert spotted[0].turns == 3                                  # 10 calls in one turn count once


def test_spot_variants_are_one_action_and_the_raw_form_is_the_most_common():
    r = read(dc.notes_wake_history())
    assert len(r.turns) == 71 and r.span == 15
    spotted, left, _ = dream.spot(r.turns)
    assert len(spotted) == 1
    s = spotted[0]
    assert s.action.key == "terminal:" + NOTES and (s.turns, s.days) == (71, 15)
    assert len(s.forms) == 3 and sorted(s.forms.values()) == [9, 12, 50]
    assert s.action.args == {"command": NOTES} and s.cwd == "~/proj"


def test_spot_leaves_out_secrets_noise_and_failed_and_counts_them():
    r = read(dc.secrets_history())
    spotted, left, _ = dream.spot(r.turns)
    assert spotted == []
    assert left == {"secrets": 27, "noise": 12, "key": 3}
    assert "chmod" not in json.dumps(left)
    r = read(dc.denied_history())
    spotted, left, near = dream.spot(r.turns)
    assert spotted == [] and left == {"failed": 3} and near == []


def test_spot_left_counts_exactly():
    s = [session("a", [(0, 9), (1, 9), (2, 9)], [[sh("cat ~/.zshenv")], [sh("sleep 5; echo done")], [sh("chmod 600 ~/x")]],
                 fail=(3,))]
    _, left, _ = dream.spot(read(store_of(*s)).turns)
    assert left == {"secrets": 1, "noise": 1, "failed": 1}


def test_near_when_nothing_is_spotted():
    _, _, near = dream.spot(turns_of((0, [NOTES]), (0, [NOTES])))
    assert [(n.action.key, n.turns, n.days) for n in near] == [("terminal:" + NOTES, 2, 1)]
    assert dream.spot(turns_of((0, [NOTES])))[2] == []
    many = turns_of(*[(0, [f"cmd{i}", f"cmd{i}"]) for i in range(1)] + [(1, [f"cmd{i} a" for i in range(8)])] * 2)
    assert len(dream.spot(many)[2]) <= dream.NEAR


def test_moments():
    sessions = []
    for i, d in enumerate((0, 1, 2, 3)):
        calls = [("todo", {}), ("skill_view", {"name": "notes"}), sh(NOTES), sh("ls ~/notes")]
        sessions.append(session(f"m{i}", [(d, 8)], [calls, [sh("ls ~/notes")]], source="cron" if i < 2 else "cli"))
    spotted, _, _ = dream.spot(read(store_of(*sessions)).turns)
    wake = next(s for s in spotted if s.action.key == "terminal:" + NOTES)
    assert wake.moments["skill"] == ["notes", 1.0]
    assert wake.moments["first"] == 0.0 and wake.moments["last"] == 0.0     # skill_view stays first; ls closes
    assert wake.moments["scheduled"] == 0.5
    ls = next(s for s in spotted if s.action.key == "terminal:ls ~/notes")
    # 4 of its 8 turns hold other calls and close with it; in the 4 turns of one call "closes the work" says nothing
    # (was 1.0: the share counted the turns that held only this call)
    assert ls.moments["last"] == 0.5 and ls.moments["skill"] is None and ls.turns == 8
    assert ls.moments["first"] == 0.0                    # never the first gesture: the skill is loaded before it


def test_moment_first_gesture_ignores_bookkeeping():
    sessions = [session(f"f{i}", [(i, 8)], [[("todo", {}), ("memory", {}), sh("ls ~/a"), sh("df -h ~")]])
                for i in range(3)]    # the turn holds a second call: "first gesture" means something
    spotted, left, _ = dream.spot(read(store_of(*sessions)).turns)
    assert next(s for s in spotted if s.action.key == "terminal:ls ~/a").moments["first"] == 1.0 and left == {}


def test_whole_turn_shapes_are_not_proposed():
    shape = [sh("df -h ~"), sh("uptime"), sh("ls ~/a")]
    sessions = [session(f"w{i}", [(i, 9)], [shape]) for i in range(4)]
    r = read(store_of(*sessions))
    spotted, _, _ = dream.spot(r.turns)
    groups = dream.group(spotted, r.turns)
    # three actions each in every turn: the only thing grown is a group of actions (never a "whole turn" key)
    assert all(g[0].action.key.startswith("terminal:") for g in groups)
    assert all(not dream.group_key(g).startswith("turn") for g in groups)
    assert len(groups) == 1 and len(groups[0]) == 3


# ---- grow ---------------------------------------------------------------------------------------------------------

def together(a_in_b: int, total: int = 100):
    """Turns on 4 days where `a` is in all of them and `b` in `a_in_b` of them (a has `total` turns)."""
    turns = []
    for i in range(total):
        calls = [sh("git pull")] + ([sh("make test")] if i < a_in_b else [])
        turns.append((i % 4, [c[1]["command"] for c in calls]))
    return turns


def test_group_together():
    r = read(store_of(*[session(f"g{i}", [(d, 9)], [[sh(c) for c in cmds]]) for i, (d, cmds) in enumerate(together(80))]))
    spotted, _, _ = dream.spot(r.turns)
    groups = dream.group(spotted, r.turns)
    assert len(groups) == 1 and [s.action.key for s in groups[0]] == ["terminal:git pull", "terminal:make test"]
    assert dream.group_key(groups[0]) == "grow:terminal:git pull+terminal:make test"
    r = read(store_of(*[session(f"g{i}", [(d, 9)], [[sh(c) for c in cmds]]) for i, (d, cmds) in enumerate(together(79))]))
    spotted, _, _ = dream.spot(r.turns)
    assert [len(g) for g in dream.group(spotted, r.turns)] == [1, 1]
    # a key in `frozen` is never absorbed
    r = read(store_of(*[session(f"g{i}", [(d, 9)], [[sh(c) for c in cmds]]) for i, (d, cmds) in enumerate(together(100))]))
    spotted, _, _ = dream.spot(r.turns)
    groups = dream.group(spotted, r.turns, frozen=frozenset({"terminal:make test"}))
    assert [len(g) for g in groups] == [1, 1]


def test_group_order_is_the_usual_order():
    sessions = [session(f"o{i}", [(i % 3, 9)], [[sh("b step"), sh("a step")]]) for i in range(6)]
    r = read(store_of(*sessions))
    spotted, _, _ = dream.spot(r.turns)
    assert [s.action.key for s in dream.group(spotted, r.turns)[0]] == ["terminal:b step", "terminal:a step"]


def test_group_reflex_item_becomes_a_use_step():
    sessions = [session(f"r{i}", [(i % 3, 9)], [[("habit_run", {"name": "morning"}), sh("make report")]]) for i in range(6)]
    r = read(store_of(*sessions))
    spotted, _, _ = dream.spot(r.turns)
    assert len(spotted) == 1                                   # habit_run itself is never an action
    groups = dream.group(spotted, r.turns, reflexes=frozenset({"morning"}))
    assert [s.action.key for s in groups[0]] == ["reflex:morning", "terminal:make report"]
    reflex = dream.draft_reflex([s.action for s in groups[0]])
    assert reflex["nodes"]["run1"]["use"] == "morning" and graph.validate(reflex) == []
    only = dream.group([], r.turns, reflexes=frozenset({"morning"}))
    assert only == []                                          # a saved reflex alone is no draft


def test_two_reflexes_called_together_are_one_group():
    sessions = [session(f"r{i}", [(i % 3, 9)], [[("habit_run", {"name": "morning"}), ("habit_run", {"name": "inbox"})]])
                for i in range(6)]
    r = read(store_of(*sessions))
    spotted, _, _ = dream.spot(r.turns)
    assert spotted == []                                       # habit_run itself is never an action
    [pair] = dream.group(spotted, r.turns, reflexes=frozenset({"morning", "inbox", "unused"}))
    assert [s.action.key for s in pair] == ["reflex:morning", "reflex:inbox"]   # B5, in their usual order
    reflex = dream.draft_reflex([s.action for s in pair])
    assert [reflex["nodes"][n]["use"] for n in ("run1", "run2")] == ["morning", "inbox"] and graph.validate(reflex) == []
    apart = [session(f"a{i}", [(i % 3, 9)], [[("habit_run", {"name": "morning"})], [("habit_run", {"name": "inbox"})]])
             for i in range(6)]
    r = read(store_of(*apart))
    assert dream.group([], r.turns, reflexes=frozenset({"morning", "inbox"})) == []   # in other turns: nothing grows


def test_group_off_gives_singles(monkeypatch):
    monkeypatch.setattr(dream, "GROW", False)
    r = read(store_of(*[session(f"g{i}", [(d, 9)], [[sh(c) for c in cmds]]) for i, (d, cmds) in enumerate(together(100))]))
    spotted, _, _ = dream.spot(r.turns)
    assert [len(g) for g in dream.group(spotted, r.turns)] == [1, 1]


# ---- may_draft, propose, siblings ---------------------------------------------------------------------------------

def spotted_with(stamps, key="terminal:" + NOTES):
    action = A(NOTES)
    return dream.Spotted(action, len(stamps), len({d for _, d in stamps}), stamps[0][0], stamps[-1][0], {}, {}, "",
                         frozenset(), tuple(stamps))


def test_may_draft_and_propose_change_rest():
    stamps = [(day(5, 9), "d5"), (day(4, 9), "d4"), (day(4, 12), "d4"), (day(3, 9), "d3")]
    s = spotted_with(stamps)
    assert dream.may_draft(s, None, NOW)
    assert not dream.may_draft(s, {"state": "ready"}, NOW)
    no = {"state": "no", "since": day(10, 9), "no_until": NOW + 86400}
    assert not dream.may_draft(s, no, NOW)                                  # the no still holds
    no["no_until"] = NOW - 1
    assert dream.may_draft(s, no, NOW)                                      # 4 turns on 3 days after the no
    no["since"] = day(4, 10)                                                # only 2 turns on 2 days after
    assert not dream.may_draft(s, no, NOW)
    no["since"] = day(4, 8)
    assert dream.may_draft(s, no, NOW)
    two_on_one = spotted_with([(day(2, 9), "d2"), (day(2, 10), "d2"), (day(2, 11), "d2")])
    assert not dream.may_draft(two_on_one, {"state": "no", "since": day(3, 9), "no_until": NOW - 1}, NOW)

    mine = A("git log -5")
    habit = {"name": "git-log", "version": 1, "status": "active", "key": mine.key, "raw": mine.raw,
             "last_seen": NOW - 20 * 86400}
    sibling = A("git log -20")
    sib = dream.Spotted(sibling, 9, 6, NOW - 21 * 86400, NOW - 86400, {}, {}, "", frozenset(), ())
    got = dream.propose([sib], [habit], NOW)
    assert [(p.kind, p.subject) for p in got] == [("change", "git-log")]
    assert got[0].detail["sibling"] == sibling.key and got[0].detail["quiet"] == 20 and got[0].detail["raw"] == sibling.raw
    habit["last_seen"] = NOW - 10 * 86400
    assert dream.propose([sib], [habit], NOW) == []
    habit["last_seen"] = NOW - 40 * 86400
    got = dream.propose([], [habit], NOW)
    assert [(p.kind, p.detail["why"]) for p in got] == [("rest", "quiet")]
    seen = dream.Spotted(mine, 5, 3, NOW - 50 * 86400, NOW - 3 * 86400, {}, {}, "", frozenset(), ())
    assert dream.propose([seen], [habit], NOW) == []


def test_siblings():
    assert dream.siblings(A("git log -5"), A("git log -20"))
    assert not dream.siblings(A("git status"), A("git log"))
    assert not dream.siblings(A("git log -5"), A("git log -5"))
    assert dream.siblings(A("", "web_extract", {"url": "https://a.test", "n": 1}),
                          A("", "web_extract", {"url": "https://a.test", "n": 2}))
    assert not dream.siblings(A("", "web_extract", {"url": "https://a.test"}), A("", "web_extract", {"url": "https://b.test"}))
    assert not dream.siblings(A("ls"), A("", "web_extract", {"url": "https://a.test"}))


# ---- local reads and the draft ------------------------------------------------------------------------------------

def test_local_read():
    for tool_name, args in (("terminal", {"command": "df -h ~"}), ("terminal", {"command": "uptime"}),
                            ("terminal", {"command": "git status"}), ("terminal", {"command": "git -C ~/p log -5"}),
                            ("read_file", {"path": "~/a"}), ("search_files", {"pattern": "x"})):
        assert dream.local_read(tool_name, args), (tool_name, args)
    for tool_name, args in (("terminal", {"command": "git pull"}), ("web_search", {"query": "x"}),
                            ("web_extract", {"url": "https://a.test"}), ("kanban_list", {}), ("mcp__x__y", {}),
                            ("terminal", {"command": NOTES}), ("terminal", {})):
        assert not dream.local_read(tool_name, args), (tool_name, args)


DRAFTS = [("terminal", {"command": c}) for c in (
    "df -h ~", "uptime", NOTES, "git status", "git log -5", "brew outdated", "docker ps", "gh pr list",
    "ls ~/Downloads | wc -l", "python3 ~/bin/report.py --quiet")] + [
    ("read_file", {"path": "~/notes/today.md"}), ("mcp__files__list", {"dir": "docs"}),
    ("web_search", {"query": "hermes"}), ("web_extract", {"urls": ["https://example.com/feed"]}),
    ("search_files", {"pattern": "TODO", "path": "~/proj"})]


@pytest.mark.parametrize("tool_name,args", DRAFTS)
def test_draft_reflex_validates(tool_name, args):
    assert dream.excluded(tool_name, args) == ""
    action = A("", tool_name, args)
    reflex = dream.draft_reflex([action], cwd="~/proj")
    assert graph.validate(reflex) == []
    assert "inputs" not in reflex
    node = reflex["nodes"]["run"]
    assert bool(node.get("read")) == dream.local_read(tool_name, args)
    if tool_name == "terminal":
        assert node["args"] == {"command": args["command"], "workdir": "~/proj"}
    assert reflex["nodes"]["done"] == {"end": "{out}"}


def test_draft_reflex_two_steps_and_without_workdir():
    steps = [A("df -h ~"), A(NOTES)]
    reflex = dream.draft_reflex(steps)
    assert graph.validate(reflex) == []
    nodes = reflex["nodes"]
    assert reflex["start"] == "run1" and nodes["run1"]["next"] == "run2" and nodes["run2"]["next"] == "done"
    assert nodes["run1"]["read"] is True and "read" not in nodes["run2"]
    assert "workdir" not in nodes["run1"]["args"]
    assert "{out1}" in nodes["done"]["end"] and "{out2}" in nodes["done"]["end"]
    assert "df" in nodes["done"]["end"]


def test_draft_reflex_with_uses_mapping():
    reflex = dream.draft_reflex([A(NOTES), A("df -h ~")], uses={A(NOTES).key: "notes-wake"})
    assert reflex["nodes"]["run1"] == {"use": "notes-wake", "as": "out1", "next": "run2"}


@pytest.mark.parametrize("command", ["awk '{print}' f", "echo ${HOME}"])
def test_draft_reflex_with_braces_gives_issues(command):
    assert graph.validate(dream.draft_reflex([A(command)]))


def test_draft_name():
    assert dream.draft_name([A(NOTES)], set()) == "notes-wake"
    assert dream.draft_name([A(NOTES)], {"notes-wake"}) == "notes-wake-2"
    assert dream.draft_name([A(NOTES)], {"notes-wake", "notes-wake-2"}) == "notes-wake-3"
    from habits.tools import SKILL_NAME
    long_steps = [A("a-very-long-program-name-that-goes-on-and-on-forever subcommand"), A("another-long-program-name x")]
    names = [dream.draft_name(s, set()) for s in ([A(c)] for c in ("df -h ~", "git log -5", "uptime", "~/x/Y_Z"))]
    names += [dream.draft_name(long_steps, {"a"}), dream.draft_name([A("", "read_file", {"path": "~/n/Today.md"})], set()),
              dream.draft_name([A("", "mcp__files__list", {})], set()), dream.draft_name([A("")], set())]
    for name in names:
        assert SKILL_NAME.match(name) and len(name) <= 40, name
    assert names[0] == "df" and names[1] == "git-log" and names[5] == "read-today"
    taken = {dream.draft_name(long_steps, set())}
    again = dream.draft_name(long_steps, taken)
    assert again not in taken and len(again) <= 40


def test_describe_has_no_path():
    for steps in ([A(NOTES)], [A("git log -5"), A("df -h ~")], [A("cat /Users/sam/some/long/path.txt")],
                  [A("", "read_file", {"path": "~/a/b.md"})], [A("a-very-long-program-name-that-goes-on-forever x")] * 3):
        text = dream.describe(steps)
        assert cockpit._described(text) == text and len(text) <= 60
        assert "/" not in text


def test_shown_is_the_raw_command_uncut():
    command = "echo " + "x" * 300 + " | wc -c"
    assert dream.shown([A(command)]) == command
    assert dream.shown([A("a"), A("b")]) == "a\nb"
    assert dream.shown([A("", "web_search", {"query": "q"})]) == 'web_search {"query":"q"}'


def test_procedure_uses_the_contract_words():
    text = dream.procedure([A(NOTES), A("df -h ~")])
    for banned in ("graph", "scenario", "node", "shadow", "promote", "script"):
        assert banned not in text.lower()
    assert "1. " in text and "2. " in text


# ---- compare ------------------------------------------------------------------------------------------------------

def test_compare():
    assert dream.compare("up 3 days\n", "up 3 days") == "same"
    assert dream.compare("at 2026-11-11T05:12:03Z ok", "at 2026-11-11T05:13:44Z ok") == "shape"
    assert dream.compare("now 12:04 ok", "now 12:05 ok") == "shape"
    assert dream.compare("3 files", "5 files") == "shape"
    assert dream.compare("3 files", "3 folders") == "differs"
    assert dream.compare("a\nhabits-pipe 0 0 0\n", "a") == "same"
    assert dream.compare(None, "") == "same"
    assert dream.compare("\x1b[32mok\x1b[0m", "ok") == "same"


# ---- imports ------------------------------------------------------------------------------------------------------

def test_dream_imports_nothing_from_hermes():
    class Guard:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in {"hermes_state", "hermes_constants", "hermes_cli", "agent", "tools", "gateway", "plugins"}:
                raise ImportError(f"{name} is Hermes's")
            return None

    import habits
    original = sys.modules.pop("habits.dream")
    guard = Guard()
    sys.meta_path.insert(0, guard)
    try:
        module = importlib.import_module("habits.dream")
        assert module.open_store() is None            # Hermes cannot be reached: None, not an exception
    finally:  # the module every other test holds comes back: a second copy would make their patches miss
        sys.meta_path.remove(guard)
        sys.modules["habits.dream"] = habits.dream = original


# ---- the last pass: secrets in every spelling, the network, the folder, the names -----------------------------------
# (findings 1, 2, 8, 9, 14 of the review; D5: the dream never proposes, drafts, stores or runs what touches a secret)

SECRET_COMMANDS = [
    # a secrets file glued to a shell operator, in a substitution, in a string for a shell
    "cat ~/.zshrc;ls", "cat ~/.zshrc&&ls", "(cat ~/.zshrc)", "cat ~/.zsh_history|tail -50", "cat ." "env|grep KEY",
    "cat ~/.pg" "pass|cut -d: -f5", "cat creden" "tials.json|jq .", "echo $(cat ~/.zshrc)", "echo `cat ~/.zshrc`",
    "bash -c 'cat ~/.zshrc | grep x'", "sh -c 'cat ~/.net" "rc'", "cat < ~/.zshrc", "cat ~/.zshrc > /dev/null",
    "ls; cat ~/.bash_history", "x=$(cat ~/.zshenv)", 'echo "$(cat ~/.zshrc)"', "cat ~/.zshrc 2>&1",
    # the environment, however it is asked for
    "env|sort", "ls; env", "bash -c env", "sh -c 'env | grep K'", "set", "set | grep K", "export -p", "export",
    "declare -p", "declare -x", "typeset", "printenv", "printenv | wc -l", "env -i", "env FOO=1", "history",
    "history | tail", "ssh pi env", "docker exec c env", "xargs printenv", "sudo env", "nice env", "ps eww",
    "ps auxe", "jq -n env", "jq -n '$ENV.PATH'", "python3 -c 'import os; print(os.environ)'",
    "node -e 'console.log(process.env)'", "cat /proc/1/environ", "awk 'BEGIN{print ENVIRON[\"A\"]}'",
    # a name that carries a secret, a credential glued to an option
    "GITHUB_TOKEN=abcdefgh12345678 gh api user", "DB_PASSWORD=correcthorse9 ./migrate.sh status",
    "PGPASSWORD=hunter2 psql -h db", "GITLAB_TOKEN=glpat-AbCdEfGhIjKlMnOpQrSt glab mr list",
    "AWS_SECRET_ACCESS_KEY=abcdEFGH1234/ijklMNOP5678 aws s3 ls", "OPENAI_API_KEY=sk-test12345678 python x.py",
    "env API_TOKEN=abc123 ./run", "export TOKEN=abc && ./run", "curl --api-key=abc123 https://x.test",
    "curl --password hunter2 https://x.test", "curl --token abc123 https://x.test",
    "curl -s -H 'Authorization: Basic dXNlcjpwYXNzd29yZA==' https://api.example.com/v1/me",
    "curl -H 'X-Api-Key: abc' https://x.test", "curl -H 'Cookie: session=abc' https://x.test",
    "curl -umyuser:pw https://x.test", "curl --user=me:pw https://x.test", "curl 'https://x.test/?token=abc123'",
    "mysql -u root -phunter2 -e 'show tables'", "sshpass -p hunter2 ssh pi uptime", "docker login -u me -p pw",
    "docker login registry.test", "gh auth token", "gh auth status", "aws configure get aws_access_key_id",
    "aws secretsmanager get-secret-value --secret-id x", "gcloud auth print-access-token",
    "kubectl get secrets -o yaml", "security find-generic-password -w", "pass show x", "op read op://v/i/p",
    "git config --global --list", "git credential fill", "pbpaste", "rclone config show", "cat ~/.config/rclone/rclone.conf",
    "curl -H 'Authorization: Bearer abcdefgh12345678' https://x.test", "echo $API_KEY",
    "curl https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXX", "gpg --export-secret-keys",
    # Hermes's own private files however they are spelled, and private files reached through a folder
    "cat $HOME/.hermes/config.yaml", "cat ${HOME}/.hermes/config.yaml", "cat ~/.hermes/config.yaml",
    "cat $HERMES_HOME/config.yaml", "cat ${HERMES_HOME}/config.yaml", "cat ${HERMES_HOME:-$HOME/.hermes}/config.yaml",
    "cd ~/.hermes && cat config.yaml", "cd ~/.hermes; grep -r model .", "cd ~ && cat .hermes/config.yaml",
    "grep -r model ~/.hermes", "grep -ri key ~/.hermes", "rg token ~/.hermes", "find ~/.hermes -name '*.yaml'",
    "ls ~/.hermes", "cat ../.hermes/config.yaml", "cat ~/proj/../.hermes/auth.json", "cd ~/.ssh; cat id_ed25519",
    "cat id_rsa", "ls ~/.aws", "cat ~/.aws/creden" "tials", "tar cf x.tar ~/.ssh", "git -C ~/.hermes show HEAD:config.yaml",
    "grep -ri token ~", "grep -ri token /", "find ~ -name '*.env'", "cat ~/.Zshrc", "cat ~/.SSH/id_rsa",
    "cat .hermes/config.yaml",
    # the folders of keys and logins, one list (dream.PRIVATE_FOLDERS): the coding agents' too, and by a pattern
    "ls ~/.claude/projects", "cat ~/.claude/settings.json", "grep -r model ~/.claude", "cat ~/.config/glab-cli/config.yml",
    "cat ~/.config/g*/hosts.yml", "ls ~/Library/Key*", "cat ~/.config/rcl*/rclone.conf",
]


@pytest.mark.parametrize("command", SECRET_COMMANDS)
def test_excluded_secret_in_every_spelling(command):
    assert dream.excluded("terminal", {"command": command}, None, "~/proj") != "", command


@pytest.mark.parametrize("command,workdir", [
    ("cat config.yaml", "~/.hermes"), ("cat id_ed25519", "~/.ssh"), ("ls", "~/.ssh"), ("ls", "~/.hermes"),
    ("grep -r model .", "~/.hermes"), ("rg token", "~"), ("cat .hermes/config.yaml", "~"),
    ("cat ../.hermes/config.yaml", "~/proj"), ("cat config.yaml", "$HOME/.hermes"),
])
def test_excluded_private_file_reached_through_the_calls_own_folder(command, workdir):
    assert dream.excluded("terminal", {"command": command, "workdir": workdir}, None, "~/proj") != ""


@pytest.mark.parametrize("command,cwd", [
    ("cat config.yaml", "~/.hermes"), ("cat id_rsa", "~/.ssh"), ("cat .hermes/config.yaml", "~"),
    ("cat .hermes/config.yaml", "$HOME"), ("grep -r token .", "~"),
])
def test_excluded_private_file_reached_through_the_session_folder(command, cwd):
    assert dream.excluded("terminal", {"command": command}, None, cwd) != ""


def test_excluded_relative_name_is_read_from_its_folder_by_blocked():
    asked = []

    def blocked(path):
        asked.append(path)
        return "private" if path.endswith("/secret-notes/x.md") else ""
    assert dream.excluded("terminal", {"command": "cat x.md"}, blocked, "~/secret-notes") == "secrets"
    assert dream.excluded("terminal", {"command": "cat x.md"}, blocked, "~/notes") == ""


@pytest.mark.parametrize("tool_name,args", [
    ("search_files", {"pattern": "api_key", "path": "~/.hermes"}), ("search_files", {"pattern": "x", "path": "$HOME/.hermes"}),
    ("search_files", {"pattern": "x", "path": "~"}), ("read_file", {"path": ".hermes/config.yaml"}),
    ("read_file", {"path": "$HERMES_HOME/config.yaml"}), ("read_file", {"path": "id_rsa"}),
    ("web_request", {"url": "https://x.test", "headers": {"Authorization": "Basic abc"}}),
    ("mcp__db__connect", {"user": "me", "password": "hunter2"}), ("mcp__x__call", {"api_key": "abc"}),
])
def test_excluded_secret_in_other_tools(tool_name, args):
    assert dream.excluded(tool_name, args, None, "~") != "", (tool_name, args)


def test_excluded_private_file_with_no_known_folder_still_by_its_hermes_folder():
    assert dream.excluded("read_file", {"path": ".hermes/config.yaml"}) != ""
    assert dream.excluded("terminal", {"command": "cat .ssh/id_rsa"}) != ""


def test_blocked_is_asked_once_per_path_in_one_dream():
    asked = []

    def blocked(path):
        asked.append(path)
        return ""
    turns = [session(f"s{i}", [(i, 9)], [[sh("cat ~/proj/a.md"), sh("cat ~/proj/a.md | head -3"), sh("wc -l ~/proj/a.md")]])
             for i in range(4)]
    dream.spot(read(store_of(*turns)).turns, blocked=blocked)
    assert len(asked) == len(set(asked))


def test_excluded_hermes_home_follows_the_environment(monkeypatch, tmp_path):
    home = tmp_path / "throwaway"
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert dream.excluded("terminal", {"command": f"grep -r token {home}"}) != ""
    assert dream.excluded("terminal", {"command": f"cd {home} && cat config.yaml"}) != ""
    assert dream.excluded("terminal", {"command": "cat $HERMES_HOME/config.yaml"}) != ""
    assert dream.excluded("search_files", {"pattern": "x", "path": str(home)}) != ""


ORDINARY = [
    NOTES, "git status", "ls -la", "make test", "git -C ~/blog pull", "python ~/scripts/backup_photos.py",
    'echo "== status ==" && git status', "echo start; make deploy", "echo ---; git status; echo ---; git diff",
    "cd ~/proj && make test", "sleep 5; make deploy", "env python3 x.py", "/usr/bin/env python3 x.py",
    "env FOO=1 python3 x.py", "env -i python3 x.py", "mkdir -p dir", "docker run -p 80:80 nginx", "ssh -p 22 pi uptime",
    "pytest -q tests/test_x.py", "uptime", "df -h ~", "ls ~/Downloads | wc -l", "git log -5", "git commit -m 'fix login'",
    "set -e; make deploy", "export PATH=$PATH:/x && make", "ps -ef", "ps aux", "grep -rn TODO src", "cat ~/notes/today.md",
    "head -n 5 README.md", "ssh -o BatchMode=yes pi uptime", "npm test", "python3 -m pytest -q",
]


@pytest.mark.parametrize("command", ORDINARY)
def test_excluded_leaves_ordinary_commands_alone(command):
    assert dream.excluded("terminal", {"command": command}, None, "~/proj") in ("", "noise", "deletes", "settings"), command
    assert dream.excluded("terminal", {"command": command}, None, "~/proj") != "secrets", command
    assert dream.excluded("terminal", {"command": command}, None, "~/proj") != "key", command


def test_a_secret_is_counted_never_kept():
    """A command that carries something that looks like a secret is left out before anything is stored: the dream
    holds a count by reason and nothing else, and no draft, key, form or note holds the value."""
    s = [session(f"s{i}", [(i, 9)], [[sh("PGPASSWORD=Tr0ub4dor psql -h db -c 'select 1'")], ]) for i in range(3)]
    s += [session(f"t{i}", [(i, 10)], [[sh("cat ~/.zshrc;ls")]]) for i in range(3)]
    spotted, left, near = dream.spot(read(store_of(*s)).turns)
    assert spotted == [] and near == [] and left == {"key": 3, "secrets": 3}
    assert "Tr0ub4dor" not in json.dumps([left, near]) and "zshrc" not in json.dumps([left, near])


def test_spot_asks_once_per_distinct_call_and_folder():
    asked = []

    def blocked(path):
        asked.append(path)
        return ""
    s = [session(f"s{i}", [(d, 9)], [[sh("cat ~/proj/notes.md"), sh("cat ~/proj/notes.md")]]) for i, d in enumerate((0, 1, 2, 3))]
    spotted, _, _ = dream.spot(read(store_of(*s)).turns, blocked=blocked)
    first = len(asked)
    assert spotted and first > 0
    s2 = s * 5
    asked.clear()
    dream.spot(read(store_of(*[session(f"z{i}", [(i % 5, 9)], [[sh("cat ~/proj/notes.md")]]) for i in range(40)])).turns,
               blocked=blocked)
    assert len(asked) <= first + 2  # forty turns of the same call cost the same as four


# ---- practice runs only what stays on this machine (findings 3, 10) -----------------------------------------------

@pytest.mark.parametrize("command", [
    "curl -sL 'https://api.example.com/v1/deploy/trigger?id=7'", "curl -I https://x.test", "curl -s https://x.test | jq .",
    "curl -s http://192.168.1.20/relay/0?turn=toggle", "curl -s -X GET https://hooks.test/notify", "ssh pi uptime",
    "ssh -i ~/k pi df -h", "ssh pi 'cat /var/log/syslog | tail -5'", "ssh pi cat /var/log/syslog",
    "hc read pi/t3", "hc ls", "cat < /dev/tcp/10.0.0.1/80", "wget -qO- https://x.test", "nc -z host 22",
    "rsync -an a b:c", "scp pi:/x .", "echo $(curl -s https://x.test)", "nice curl -s https://x.test", "ping -c1 host",
    "dig example.com", "cd /tmp; ssh pi ls",
])
def test_local_read_refuses_what_reaches_the_network(command):
    assert not dream.local_read("terminal", {"command": command}), command
    assert not dream.is_local(command)


def test_is_local():
    for command in ("df -h ~", "uptime", "cat ~/notes/today.md", "ls | wc -l", "grep -r TODO ~/proj"):
        assert dream.is_local(command), command
    for bad in ("", "   ", None, 3):
        assert not dream.is_local(bad)


def test_a_network_read_is_drafted_as_a_plan_not_a_read():
    reflex = dream.draft_reflex([A("curl -s https://x.test/api")], cwd="~/proj")
    assert graph.validate(reflex) == [] and "read" not in reflex["nodes"]["run"]
    assert dream.draft_reflex([A("df -h ~")])["nodes"]["run"]["read"] is True


# ---- the drafted reflex runs what the history shows (finding 15) --------------------------------------------------

def test_draft_keeps_the_calls_own_folder_timeout_and_background():
    one = A("", "terminal", {"command": "ls | wc -l", "workdir": "/Users/x/projA"})
    assert dream.draft_reflex([one], cwd="/Users/x")["nodes"]["run"]["args"] == {
        "command": "ls | wc -l", "workdir": "/Users/x/projA"}
    assert dream.draft_reflex([one])["nodes"]["run"]["args"]["workdir"] == "/Users/x/projA"
    two = A("", "terminal", {"command": "pytest -q", "workdir": "/srv/app", "timeout": 900})
    assert dream.draft_reflex([two], cwd="/Users/x")["nodes"]["run"]["args"] == {
        "command": "pytest -q", "workdir": "/srv/app", "timeout": 900}
    three = A("", "terminal", {"command": "npm run dev", "background": True})
    node = dream.draft_reflex([three], cwd="~/proj")["nodes"]["run"]
    assert node["args"] == {"command": "npm run dev", "workdir": "~/proj", "background": True}
    for step in (one, two, three):
        assert graph.validate(dream.draft_reflex([step], cwd="~/proj")) == []
    assert dream.draft_reflex([A("ls")], cwd="~/proj")["nodes"]["run"]["args"] == {"command": "ls", "workdir": "~/proj"}


def test_the_card_shows_the_folder_and_the_options():
    one = A("", "terminal", {"command": "git pull", "workdir": "/Users/x/projA"})
    assert dream.shown([one]) == "git pull   (in /Users/x/projA)"
    assert dream.shown([A("git pull")], cwd="~/proj") == "git pull   (in ~/proj)"
    assert dream.shown([A("", "terminal", {"command": "npm run dev", "background": True, "timeout": 30})]) == \
        "npm run dev   (timeout 30, in the background)"
    assert dream.shown([A("a")]) == "a"


def test_compare_forgives_what_moves_by_itself_never_two_contents():
    assert dream.compare("12", "57") == "differs"
    assert dream.compare("0", "1") == "differs"
    assert dream.compare("12\n", "12") == "same"
    assert dream.compare("1731300000", "1731300999") == "shape"          # an epoch clock
    assert dream.compare("2026-11-11 09:00", "2026-11-12 10:30") == "shape"
    assert dream.compare("pid 4812 ok", "pid 4999 ok") == "shape"
    assert dream.compare("3 files", "5 files") == "shape"
    assert dream.compare("commit 9fceb02a1 ok", "commit 3a1d7e442 ok") == "shape"
    assert dream.compare("12 files in projA", "57 folders in ~") == "differs"
    assert dream.compare("12 13", "57 58") == "differs"


# ---- a new form is the same gesture, not any gesture of the same program (finding 17) -----------------------------

def test_siblings_need_the_same_words_apart_from_options_and_numbers():
    assert dream.siblings(A("ls"), A("ls -la"))
    assert dream.siblings(A("uptime"), A("uptime -p"))
    assert dream.siblings(A("tail -n 5 f.log"), A("tail -n 20 f.log"))
    for old, new in (("git -C ~/blog pull", "git -C ~/work push --force"), ("git -C ~/blog pull", "git -C ~/work pull"),
                     ("python -m pytest -q", "python -m http.server 8000"), ("ssh pi uptime", "ssh pi 'sudo reboot'"),
                     ("docker compose up -d", "docker compose down -v"),
                     ("kubectl -n prod get pods", "kubectl -n prod delete pod x"),
                     ("bash -c 'make test'", "bash -c 'make deploy'"), ("git status", "git log")):
        assert not dream.siblings(A(old), A(new)), (old, new)


# ---- names and descriptions tell two commands of one program apart (finding 35) -----------------------------------

def test_two_commands_of_one_program_get_two_names_and_two_descriptions():
    pairs = [("git -C ~/blog pull", "git -C ~/work push"),
             ("python ~/scripts/backup_photos.py", "python ~/scripts/send_invoices.py")]
    for one, two in pairs:
        a, b = A(one), A(two)
        assert dream.draft_name([a], set()) != dream.draft_name([b], set())
        assert dream.describe([a]) != dream.describe([b])
        assert dream.procedure([a]) != dream.procedure([b])
    assert dream.draft_name([A("git -C ~/blog pull")], set()) == "git-pull"
    assert dream.describe([A("python ~/scripts/backup_photos.py")]) == "Runs `python backup_photos`"
    assert dream.draft_name([A("", "search_files", {"pattern": "x", "path": "~/notes"})], set()) == "search-notes"
    assert dream.describe([A("", "search_files", {"pattern": "x", "path": "~/notes"})]) == "Runs search_files notes"
    # nothing private or secret-looking enters a name
    assert dream.draft_name([A("cat ~/.zshrc")], set()) == "cat"
    assert "ghp_" not in dream.describe([A("gh api -H ghp_abcdefghijklmnop /user")])


# ---- the left-out commands are the secret ones, not the ones that begin with an echo (finding 37) -----------------

@pytest.mark.parametrize("command", ['echo "== status ==" && git status', "echo start; make deploy",
                                     "echo ---; git status; echo ---; git diff", "echo hi && echo $(make deploy)",
                                     "echo a | wc -c", "echo hi > f"])
def test_an_echo_header_does_not_hide_the_command_after_it(command):
    assert dream.excluded("terminal", {"command": command}) != "noise"


@pytest.mark.parametrize("command", ["echo hi", "echo", "echo 'done'", "sleep 5", "sleep 5; echo done", "cd ~/x", "pwd"])
def test_plain_noise_stays_noise(command):
    assert dream.excluded("terminal", {"command": command}) == "noise"


# ---- the last pass: a damaged timestamp, a row listed twice, a store Hermes holds for a moment ----------------------

def _history(*stamps):
    """One session whose user messages carry these timestamps, each followed by one terminal call."""
    messages = []
    for i, at in enumerate(stamps):
        messages += [user(at), call(at + 1 if at == at and abs(at) != float("inf") else 0, "terminal",
                                    {"command": "df -h ~"}, f"c{i}")]
    return FakeStore([row("s1", day(1))], {"s1": messages})


@pytest.mark.parametrize("bad", [day(1) * 1000, float("inf"), 1e18, NOW + 400 * 86400, float("nan")])
def test_one_timestamp_out_of_range_costs_one_turn_not_the_read(bad):
    r = read(_history(day(2), bad, day(1)))
    assert r.status == "ok" and len(r.turns) == 2 and r.skipped >= 1
    assert r.span == 2 and all(t.day.startswith("20") for t in r.turns)


def test_a_session_row_listed_twice_is_read_once():
    rows = [row("s1", day(1)), row("s1", day(1))]
    store = FakeStore(rows, {"s1": [user(day(1)), call(day(1) + 1, "terminal", {"command": "df -h ~"})]})
    r = read(store)
    assert (len(r.turns), r.sessions, r.skipped) == (1, 1, 0)


class Locked(FakeStore):
    """Locked for its first `times` listings, as a store Hermes is writing to."""

    def __init__(self, times, *args, **kw):
        super().__init__(*args, **kw)
        self.times = times

    def list_sessions_rich(self, **kw):
        if self.times:
            self.times -= 1
            raise sqlite3.OperationalError("database is locked")
        return super().list_sessions_rich(**kw)


def test_a_store_that_is_locked_for_a_moment_is_asked_again():
    store = Locked(2, [row("s1", day(1))], {"s1": [user(day(1)), call(day(1) + 1, "terminal", {"command": "ls"})]})
    r = dream.read_turns(store, time.time(), 60, pause=0)
    assert r.status == "ok" and len(r.turns) == 1 and store.closed
    stuck = Locked(99, [row("s1", day(1))], {})
    r = dream.read_turns(stuck, time.time(), 60, pause=0)
    assert (r.status, r.why) == ("unread", "OperationalError")  # three tries, then it says so


def test_open_store_asks_again_when_the_database_is_locked(monkeypatch, tmp_path):
    import types
    tries = []

    class DB:
        def __init__(self, *a, **kw):
            tries.append(1)
            if len(tries) < 3:
                raise sqlite3.OperationalError("database is locked")

    (tmp_path / "state.db").write_text("")
    monkeypatch.setitem(sys.modules, "hermes_state", types.SimpleNamespace(SessionDB=DB))
    monkeypatch.setitem(sys.modules, "hermes_constants", types.SimpleNamespace(get_hermes_home=lambda: tmp_path))
    monkeypatch.setattr(dream, "BUSY_PAUSE", 0)
    monkeypatch.setattr(dream.time, "sleep", lambda s: None)
    assert isinstance(dream.open_store(), DB) and len(tries) == 3
    tries.clear()
    DB.__init__ = lambda self, *a, **k: (_ for _ in ()).throw(ValueError("not a lock"))
    assert dream.open_store() is None  # any other error: unreadable at once


# ---- the last round (G2): secrets that still passed, ordinary commands left out for nothing, names, the report --------
# Strings that are secret reads are assembled from pieces so that the install scanner does not read this file as one.

HOME = __import__("os").path.expanduser("~")
DOT = "~/."

GLOBBED = [
    "cat " + DOT + "zsh*", "cat " + DOT + "zsh{rc,en" + "v}", "cat " + DOT + "z?hrc", "cat " + DOT + "[z]shrc",
    "cat " + DOT + "{zshrc,zsh" + "env}", "cat .e*", "cat .en?", "head -3 " + DOT + "zsh* | cut -c1-80",
    "grep -h export " + DOT + "z*", "grep KEY " + DOT + "*rc", "cat " + DOT + "*history | tail", "cat " + DOT + "bash*",
    "cat " + DOT + "h*/config.yaml", "cat " + DOT + "herm?s/config.yaml", "cat " + DOT + "{hermes,x}/config.yaml",
    "cat " + DOT + "h*s/.e*", "grep -ri key " + DOT + "h*", "grep -i key " + DOT + "*/config.yaml",
    "cat " + DOT + "$(echo hermes)/config.yaml", "cd; cat .zsh*", "cat " + DOT + "ss*/id_*", "cat id_*",
    # names matched by their start, and the missing ones
    "cat " + DOT + "zshrc.local", "cat " + DOT + "zshrc.pre-oh-my-zsh", "cat " + DOT + "bash_history.bak",
    "cat " + DOT + "en" + "vrc", "cat .en" + "vrc", "cat " + DOT + "git" + "config", "cat " + DOT + "vault-token",
]
PRINTERS = ["ps -E", "ps -Eww -p 12", "busybox env", "busybox printenv", "docker compose config", "podman inspect web",
            "tmux show-environment", "tmux showenv -g", "systemctl show-environment", "compgen -v", "compgen -e",
            "heroku config", "vercel env pull", "kubectl config view --raw", "launchctl export", "direnv export json",
            "php -r 'print_r($_ENV);'", "ruby -e 'puts ENV.to_h'", "deno eval 'console.log(Deno.env.toObject())'"]
PASSWORDS = ["redis-cli -a correcthorse9 ping", "redis-cli -h cache.internal -a Tr0ub4dor ping",
             "sqlcmd -S db -U sa -P Corr3ctHorse -Q 'select 1'", "mongosh -u admin -p correcthorse9", "unzip -P hunter2 a.zip",
             "ldapsearch -x -w S3same -b dc=x", "mysql -u root -phunter2 -e 'select 1'", "7z x -phunter2 a.7z",
             "mosquitto_sub -h b -u me -P hunter2 -t x", "openssl enc -d -aes-256-cbc -k correcthorse9 -in f.enc",
             "echo hunter2 | su" "do -S apt update", "smbclient //nas/share -U me%hunter2 -c ls",
             "curl -b 'sid=AbCd1234EfGh5678' https://x.test", "ssh-keygen -y -P hunter2 -f k"]


@pytest.mark.parametrize("command", GLOBBED + PRINTERS + PASSWORDS)
def test_excluded_a_secret_reached_through_a_pattern_a_printer_or_a_short_option(command):
    assert dream.excluded("terminal", {"command": command}, None, "~/proj") != "", command


@pytest.mark.parametrize("tool_name,args", [
    ("read_file", {"path": DOT + "h*/config.yaml"}), ("search_files", {"pattern": "x", "path": DOT + "h*"}),
    ("read_file", {"path": DOT + "{zshrc,zshenv}"}), ("search_files", {"pattern": "password", "path": "~/.config"}),
    ("search_files", {"pattern": "password", "path": "~/Library"}), ("search_files", {"pattern": "x", "path": "/"}),
])
def test_excluded_a_pattern_or_a_folder_that_holds_a_private_one_in_another_tool(tool_name, args):
    assert dream.excluded(tool_name, args, None, "~/proj") != ""


@pytest.mark.parametrize("tool_name,args", [
    ("web_extract", {"urls": ["https://admin:hunter2secret@intranet.example/status"]}),
    ("browser_navigate", {"url": "https://admin:hunter2secret@intranet.example/"}),
    ("mcp_db_query", {"dsn": "postgres://me:pw12345@db/app", "sql": "select 1"}),
    ("web_extract", {"urls": ["https://api.x.test/v1?api_key=AbCd1234EfGh5678"]}),
    ("web_extract", {"urls": ["https://api.x.test/v1?token=abc12345"]}),
    ("web_extract", {"urls": ["https://api.x.test/v1?key=AbCd1234EfGh"]}),
    ("terminal", {"command": "git clone https://abcdef0123456789abcd@github.com/a/b"}),
    ("terminal", {"command": "curl 'https://api.x.test/v1?key=AbCd1234EfGh'"}),
    ("terminal", {"command": "curl https://api.telegram.org/bot8123456789:AAHk9xyzABC/getMe"}),
    ("send_message", {"target": "x", "message": "see https://u:secretpw1@h.test/a"}),
])
def test_excluded_a_password_or_a_key_in_a_url_in_any_tools_arguments(tool_name, args):
    assert dream.excluded(tool_name, args, None, "~/proj") != ""


def test_excluded_a_relative_workdir_that_lands_in_a_private_folder():
    for workdir in (".ssh", "../.ssh", ".hermes", "./.aws/"):
        assert dream.excluded("terminal", {"command": "cat config", "workdir": workdir}, None, "") != "", workdir
        assert dream.excluded("terminal", {"command": "cat config", "workdir": workdir}, None, HOME) != "", workdir
    assert dream.excluded("terminal", {"command": "git status", "workdir": "src"}, None, "/Users/x/proj") == ""
    assert dream.excluded("terminal", {"command": "git status", "workdir": ".."}, None, "/Users/x/proj") == ""


def test_excluded_a_recursive_search_from_a_folder_that_holds_a_private_folder():
    assert dream.excluded("terminal", {"command": "grep -rn token"}, None, HOME + "/.config") != ""
    assert dream.excluded("terminal", {"command": "grep -rn token ."}, None, HOME + "/Library") != ""
    assert dream.excluded("terminal", {"command": "grep -rn token ~/.config"}, None, "/Users/x/proj") != ""


# ---- ordinary commands stay candidates ------------------------------------------------------------------------------

ORDINARY_IN_HOME = ["du -sh ~/Downloads", "find ~/proj -name '*.md' -newer ~/proj/README.md", "grep -rn TODO ~/proj/src",
                    "cp ~/proj/a.txt ~/proj/b.txt", "tar czf /tmp/p.tgz -C ~/proj src", "tree -L 2 ~/proj",
                    "rg -l TODO ~/proj/src", "rsync -av ~/proj/ /tmp/backup/", "zip -r /tmp/p.zip ~/proj/src",
                    "grep -rn 'a/b' ~/proj/src", "find ~/proj/*/src -name '*.py'", "grep -rn TODO src/*.py"]


@pytest.mark.parametrize("command", ORDINARY_IN_HOME)
def test_a_search_that_names_its_own_folder_is_a_candidate_in_a_session_started_at_home(command):
    assert dream.excluded("terminal", {"command": command}, None, HOME) == "", command


@pytest.mark.parametrize("command", ["grep -rn TODO", "grep -rn TODO .", "find . -name '*.py'", "du -sh .", "rg token ~",
                                     "find / -name x", "tar czf /tmp/p.tgz *", "grep -rn TODO ../"])
def test_a_search_with_no_folder_or_with_dot_home_or_root_is_left_out_from_the_home_folder(command):
    assert dream.excluded("terminal", {"command": command}, None, HOME) == "secrets", command


ORDINARY = [
    "find . -name '*.py' -print", "find . -type f -print0 | xargs -0 wc -l", "find src -perm 644", "ls -plah",
    "go test -parallel 4 ./...", "xcodebuild -project App.xcodeproj -scheme App build", "gcc -pthread -o x x.c",
    "ssh -p2222 pi uptime", "tar -pczf out.tgz src", "terraform plan -parallelism=10", "docker run -p8080:80 nginx",
    "kubectl get pods -l app=web", "kubectl logs -l app=web --tail=20", "docker ps -l", "gh pr list -l bug",
    "aws s3 ls s3://my-bucket/export/", "npm run export", "npm run build:export", "yarn export", "npm run auth:test",
    "rg --passthru TODO notes.md", "llm --max-tokens 200 'say hi'", "python train.py --tokenizer gpt2",
    "sort --key=2 data.tsv", "jq '{key: .id, value: .name}' items.json", "jq -r '.[] | .name' a.json",
    "PWD=/tmp ./run.sh", "docker run -u 1000:1000 img id", "git commit -m 'fix token refresh'",
    "git commit -am 'update password reset copy'", "pytest -k token", "grep -rn 'api_key' src", "rg -n secret docs",
    "grep -n password src/login.py", "git log --author=bob -5", "git push -u origin HEAD", "ps aux | grep node",
    "ps -ef | grep hermes", "echo '{\"a\": 1}' | jq .", "set -e; make test",
]


@pytest.mark.parametrize("command", ORDINARY)
def test_ordinary_options_and_words_are_no_secrets(command):
    assert dream.excluded("terminal", {"command": command}, None, "/Users/x/proj") == "", command


@pytest.mark.parametrize("tool_name,args,why", [
    ("search_files", {"pattern": "token", "path": "/Users/x/proj/src"}, ""),
    ("web_search", {"query": "oauth"}, ""),
    # was "": these carry text someone typed, so they are never a draft (the reason is "free text", not "secrets")
    ("web_search", {"query": "python oauth token refresh best practice"}, "free text"),
    ("write_file", {"path": "~/notes/daily.md", "content": "standup: fixed the token refresh bug"}, "free text"),
    ("session_search", {"query": "password reset flow"}, "free text"),
    ("web_search", {"query": "secret: reset the password"}, "free text"),
])
def test_ordinary_words_in_other_tools_are_no_secrets(tool_name, args, why):
    assert dream.excluded(tool_name, args, None, "/Users/x/proj") == why


def test_a_bare_word_is_a_private_file_only_where_such_a_file_exists(tmp_path):
    asked = []
    assert dream.excluded("terminal", {"command": "cat credentials"}, lambda p: asked.append(p) or "", str(tmp_path)) == ""
    assert asked == []                                        # a search term: the disk is not asked about it
    (tmp_path / "credentials").write_text("x")
    assert dream.excluded("terminal", {"command": "cat credentials"}, None, str(tmp_path)) == "secrets"


def test_a_bare_folder_name_is_a_search_root_when_it_is_a_folder_there(tmp_path):
    (tmp_path / "src").mkdir()
    assert dream.excluded("terminal", {"command": "grep -rn TODO src"}, None, HOME) == "secrets"   # no such folder at home
    assert dream.excluded("terminal", {"command": "grep -rn TODO src"}, None, str(tmp_path)) == ""


def test_noise_is_noise_only_when_every_line_is():
    for command in ('echo "== status =="\ngit status', "echo start\nmake deploy", "cd ~/proj\nmake test"):
        assert dream.excluded("terminal", {"command": command}) == "", command
    assert dream.excluded("terminal", {"command": "echo a\necho b"}) == "noise"
    assert dream.excluded("terminal", {"command": "echo hi"}) == "noise"


# ---- names carry only a program and its sub-command words -----------------------------------------------------------

@pytest.mark.parametrize("command,name", [
    ("redis-cli -a correcthorse9 ping", "redis-cli-ping"), ("redis-cli -h cache.internal -a Tr0ub4dor ping", "redis-cli-ping"),
    ("ldapsearch -x -w S3same -b dc=x", "ldapsearch"), ("unzip -P hunter2 a.zip", "unzip"),
    ("mongosh -u admin -p correcthorse9", "mongosh"), ("echo hunter2 | su" "do -S apt update", "echo"),
    ("mysql -u root prod_db", "mysql-prod-db"), ("git -C ~/blog pull", "git-pull"),   # was git-blog-pull: -C's value is no sub-command
    ("git -C ~/work push --force", "git-push"), ("python3 ~/bin/backup_photos.py", "python3-backup-photos"),
    ("python3 -m pytest -q", "python3-pytest"), ("bash -c 'make test'", "bash-make"),
    ("curl -s -o /dev/null -w '%{http_code}' http://x.test", "curl"), ("tar czf /tmp/p.tgz -C ~/proj src", "tar-czf-p"),
])
def test_a_draft_is_named_by_its_program_and_sub_command_never_by_a_value(command, name):
    assert dream.draft_name([A(command)], set()) == name
    assert not any(word in dream.describe([A(command)]) for word in ("correcthorse", "hunter", "Tr0ub4dor", "S3same"))


def test_a_name_never_holds_a_word_a_secret_rule_flags_or_that_mixes_digits_and_letters():
    for command in ("mytool Corr3ctHorse", "mytool abc123def456", "mytool passwordx", "mytool " + "x" * 30,
                    "mytool fooBar", "mytool ~/." + "net" + "rc"):
        assert dream.draft_name([A(command)], set()) == "mytool", command


def test_a_name_that_is_taken_takes_the_first_option_name_before_a_number():
    a, b = A("git log --oneline"), A("git log --stat")
    taken = {dream.draft_name([A("git log")], set())}
    assert taken == {"git-log"}
    assert dream.draft_name([a], taken) == "git-log-oneline" and dream.draft_name([b], taken) == "git-log-stat"
    assert dream.draft_name([a], taken | {"git-log-oneline"}) == "git-log-oneline-2"
    assert dream.describe([a]) == "Runs `git log --oneline`" and dream.describe([b]) == "Runs `git log --stat`"


# ---- the report shows an act short ----------------------------------------------------------------------------------

def test_brief_shows_a_read_as_written_and_an_act_short_unless_every_word_is_plain():
    assert dream.brief(A("df -h ~")) == "df -h ~"
    assert dream.brief(A("git -C ~/proj status --short")) == "git -C ~/proj status --short"
    assert dream.brief(A("git pull")) == "git pull" and dream.brief(A("make test")) == "make test"
    assert dream.brief(A("git push origin main")) == "git push origin main"
    assert dream.brief(A("redis-cli -a correcthorse9 ping")) == "redis-cli ping …"
    assert dream.brief(A("curl -s https://x.test/a?b=c")) == "curl …"
    assert dream.brief(A("echo hunter2 | su" "do -S apt update")) == "echo …"
    assert dream.brief(A("FOO=bar make deploy")) == "make deploy …" or dream.brief(A("FOO=bar make deploy")).endswith("…")
    args = {"urls": ["https://x.test/a"]}
    assert dream.brief(A("", "web_extract", args)) == "web_extract …"
    assert dream.brief(A("", "send_message", {"target": "telegram", "message": "hi there"})) == "send_message …"
    assert dream.brief(A("", "read_file", {"path": "~/notes/a.md"})) == dream.shown([A("", "read_file", {"path": "~/notes/a.md"})])


def test_what_a_card_shows_is_still_the_whole_command():
    assert dream.shown([A("redis-cli -a correcthorse9 ping")]) == "redis-cli -a correcthorse9 ping"


# ---- counts per folder, a draft that does not know where it runs -----------------------------------------------------

def folders_history(folders_by_day, command="git pull"):
    sessions = [session(f"w{i}", [(d, 10)], [[("terminal", {"command": command, "workdir": w})]], cwd="~/proj")
                for i, (d, w) in enumerate(folders_by_day)]
    return read(store_of(*sessions)).turns


def test_one_command_once_in_each_of_three_folders_comes_back_three_times_and_has_no_folder_of_its_own():
    # was: "did not come back three times there" -- it pinned a draft that ran in the folder of one form. A user with
    # three repositories repeats `git pull` in each: it comes back, and it runs where Hermes is when it is called.
    turns = folders_history([(0, "/Users/x/projA"), (1, "/Users/x/projB"), (2, "/Users/x/projC")])
    [s], _, near = dream.spot(turns)
    assert (s.turns, s.days, s.cwd) == (3, 3, "") and "workdir" not in s.action.args
    assert "workdir" not in dream.draft_reflex([s.action], s.cwd)["nodes"]["run"]["args"]
    assert near == []


def test_a_kept_folder_counts_what_came_back_there():
    turns = folders_history([(0, "/Users/x/a"), (0, "/Users/x/a"), (1, "/Users/x/a"), (2, "/Users/x/a"),
                             (3, "/Users/x/a"), (4, "/Users/x/other")])
    [s] = dream.spot(turns)[0]
    assert (s.cwd, s.turns, s.days) == ("/Users/x/a", 5, 4)
    one_day = folders_history([(0, "/Users/x/a")] * 4 + [(1, "/Users/x/other")])
    assert dream.spot(one_day)[0] == []                         # there it came back on one day only


def test_the_same_command_without_a_workdir_in_three_session_folders_is_one_gesture_without_a_folder():
    sessions = [session(f"w{i}", [(i, 10)], [[sh("uptime")]], cwd=f"/Users/x/p{i}") for i in range(3)]
    [s] = dream.spot(read(store_of(*sessions)).turns)[0]
    assert (s.turns, s.days, s.cwd) == (3, 3, "")


def test_a_command_that_depends_on_its_folder_is_no_read_when_no_folder_is_known():
    assert dream.local_read("terminal", {"command": "ls | wc -l"}) is True            # the caller does not know
    assert dream.local_read("terminal", {"command": "ls | wc -l"}, "") is False       # nothing says where
    assert dream.local_read("terminal", {"command": "ls | wc -l"}, "/Users/x/p") is True
    assert dream.local_read("terminal", {"command": "ls | wc -l", "workdir": "/Users/x/p"}, "") is True
    assert dream.local_read("terminal", {"command": "df -h ~"}, "") is True           # it depends on no folder
    assert dream.local_read("read_file", {"path": "~/a.md"}, "") is True
    node = dream.draft_reflex([A("ls | wc -l")], "")["nodes"]["run"]
    assert "read" not in node
    assert dream.draft_reflex([A("ls | wc -l")], "/Users/x/p")["nodes"]["run"]["read"] is True


# ---- siblings through an option's value, a nested wrapper -------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("curl -s -XGET https://api.x.test/v1/items/7", "curl -s -XDELETE https://api.x.test/v1/items/7"),
    ("kubectl get pods --namespace=staging", "kubectl get pods --namespace=prod"),
    ("git --git-dir=/a/.git pull", "git --git-dir=/b/.git pull"), ("npm publish --dry-run", "npm publish"),
    ("git push", "git push --force"), ("rsync -n -av a b", "rsync -av a b"),
])
def test_two_forms_that_differ_by_an_options_value_or_a_rehearsal_are_not_siblings(a, b):
    assert not dream.siblings(A(a), A(b)) and not dream.siblings(A(b), A(a))


def test_a_wrapper_inside_a_wrapper_is_no_call():
    inner = {"calls": [{"name": "terminal", "arguments": {"command": "df -h ~"}}]}
    msg = call(day(1), "tool_call", {"calls": [{"name": "tool_call", "arguments": inner},
                                               {"name": "terminal", "arguments": {"command": "uptime"}}]})
    assert [(t, a) for t, a, _ in dream.calls_of(msg)] == [("terminal", {"command": "uptime"})]


# ---- the real-model trial, defect 1: the description says the exact command the reflex replaces, and when ------------

def test_the_description_of_a_one_step_reflex_says_the_command_it_replaces_and_when_it_was_seen():
    start, end = {"first": 1.0, "last": 0.2}, {"first": 0.1, "last": 1.0}
    assert dream.describe([A("~/bin/notes wake | tail -40")], start) == "Runs `notes wake | tail -40` at session start"
    assert dream.describe([A("git -C /home/someone/proj branch --show-current")], end) == \
        "Runs `git branch --show-current` at the end of work"   # was `git -C proj ...`: -C's value is not a word of it
    assert dream.describe([A("df -h ~")], {"scheduled": 0.8}) == "Runs `df -h ~` on a schedule"
    assert dream.describe([A("df -h ~")], {"first": 0.3, "last": 0.4}) == "Runs `df -h ~`"  # no moment holds half the time
    assert dream.describe([A("df -h ~")]) == "Runs `df -h ~`"
    for text in (dream.describe([A("git -C ~/a/very/long/folder-name-indeed log --oneline --stat")], start),
                 dream.describe([A("make " + "target " * 30)], start)):
        assert len(text) <= dream.DESCRIPTION_CHARS == 60  # the moment goes first, then the command is the short form


@pytest.mark.parametrize("command", [
    "redis-cli -a hunter ping", "mysql -psecretword shop", "gh api -H ghp_abcdefghijklmnop /user",
    "curl -u bob:pw https://example.org", "notify --title Bonjour", "echo hello world", "deploy --env=prod",
    "ssh box 'cat notes'", "git commit -m wip", "cat ~/work/Budget2026.xlsx"])
def test_a_description_never_carries_an_option_value_or_a_word_a_name_would_refuse(command):
    text = dream.describe([A(command)], {"first": 1.0})
    for word in ("hunter", "secretword", "ghp_", "bob", "pw", "Bonjour", "hello", "prod", "notes'", "wip", "Budget"):
        assert word not in text, text
    assert "`" not in text and len(text) <= 60  # the short form of before: program, sub-command, option names


def test_a_grown_reflex_keeps_the_short_description():
    text = dream.describe([A("git pull"), A("make test")], {"first": 1.0})
    assert text.startswith("Runs git pull, then make test") and "`" not in text


# ======================================================================================================================
# The last round (the personas' reports of 11 November): what may become a draft, what is no habit, where a draft
# runs, names and words. Private-looking strings are built from pieces: the install scanner reads this file too.
# ======================================================================================================================

PW = "Zebra" + "-Quartz-" + "77"     # a password someone typed
ETC = "/etc/"


def why(command, **more):
    return dream.excluded("terminal", {"command": command, **more}, None, "/Users/x/proj")


# ---- 1. secrets, by construction: what may become a draft ---------------------------------------------------------

@pytest.mark.parametrize("tool_name,args", [
    ("send_message", {"message": "wifi pass" "word is " + PW, "target": "telegram:me"}),
    ("send_message", {"message": "hello", "target": "telegram:me"}),                       # whatever it says
    ("browser_type", {"ref": "e12", "text": "MyLogin" "Passw0rd" "Z!"}),
    ("browser_type", {"ref": "e12", "text": "x"}),
    ("browser_click", {"ref": "e3"}), ("browser_snapshot", {}), ("browser_scroll", {"direction": "down"}),
    ("write_file", {"path": "~/n.md", "content": "x"}), ("patch", {"path": "~/n.md", "old_string": "a", "new_string": "b"}),
    ("text_to_speech", {"text": "hi"}), ("image_generate", {"prompt": "a cat"}), ("vision_analyze", {"url": "https://x.test/a.png"}),
    ("mcp_notes_add", {"note": "buy milk and eggs"}),                                      # a sentence
    ("web_search", {"query": "two words"}),
])
def test_a_tool_that_carries_free_text_or_only_moves_a_screen_never_gives_a_draft(tool_name, args):
    assert dream.excluded(tool_name, args) == "free text"


@pytest.mark.parametrize("tool_name,args", [
    ("terminal", {"command": "git status"}), ("read_file", {"path": "~/notes/today.md"}),
    ("search_files", {"pattern": "TODO", "path": "~/proj"}),
    ("mcp_homeassistant_get_state", {"entity_id": "sensor.office_co2"}),
    ("mcp_tasks_list_tasks", {"status": "open", "assignee": "me", "limit": 20}),
    ("mcp_calendar_list_events", {}), ("mcp_x_get", {"all": True, "depth": 2.5, "tags": ["a", "b"]}),
    ("web_extract", {"urls": ["https://example.com/feed"]}), ("browser_navigate", {"url": "https://app.example.com/dash"}),
    ("mcp_files_list", {"dir": "~/docs/reports", "flag": "--all"}),
])
def test_a_call_whose_arguments_are_all_plain_may_be_a_draft(tool_name, args):
    assert dream.excluded(tool_name, args) == ""


@pytest.mark.parametrize("value,plain", [
    (3, True), (2.5, True), (True, True), (None, True), ("open", True), ("--all", True), ("get_state", True),
    ("sensor.office_co2", True), ("~/docs/a.md", True), ("/var/log/syslog", True), ("https://x.test/a/b", True),
    ("two words", False), ("hello\nworld", False), ("a" * 300, False), ("https://me:pw@x.test/", False),
    ("https://x.test/a?b=c", False), ("https://x.test/a#top", False), ("Passw0rdZ9", False), ("a1b2c3d4e5", False),
    ("it's", False), ({"a": {"b": {"c": {"d": 1}}}}, False),
    ([1, 2, 3], True), ({"limit": 20, "q": "open"}, True), ({"q": "Passw0rdZ9"}, False), (b"bytes", False),
])
def test_what_is_a_plain_value(value, plain):
    assert dream._call_value(value) is plain


def test_an_mcp_login_with_a_password_argument_is_left_out_as_a_key():
    assert dream.excluded("mcp_login", {"user": "me", "password": "hunter2"}) == "key"


def test_a_password_in_any_free_text_never_reaches_a_draft_or_the_ledger():
    # the five tools of the report: the password stays in the history, which the dream only reads
    store = store_of(*[session(f"p{i}", [(i, 9)], [[("send_message", {"message": "the code is " + PW, "target": "me"}),
                                                    ("browser_type", {"ref": "e1", "text": PW + "x"})]])
                       for i in range(4)])
    spotted, left, near = dream.spot(read(store).turns)
    assert spotted == [] and near == [] and "free text" in left
    assert PW not in repr((spotted, near))


@pytest.mark.parametrize("command", [
    'awk -v key="XyZ9-apikey-4455aabbcc" \'BEGIN{print length(key)}\'',
    'awk -v key=XyZ9apikey4455aabbcc \'BEGIN{print length(key)}\'',
    "sqlite3 ~/compta.db \"update config set valeur='hunter2-compta' where cle='pwd'\"",
    "sqlite3 db.sqlite \"insert into users(name, password) values('bob', 'x1')\"",
    'wget -q --header="Cookie: sid=s%3AkR2m9Zabc123xyz" https://x.test/a',
    "wget -q --header 'Cookie: sid=abc' https://x.test/a", 'curl -H "Cookie: a=b" https://x.test',
    "restic -r /mnt/nas/restic --password-file /home/pi/.restic-" "pass check", "restic --password-file=/p/x check",
    "tool --ssh-key ~/k run", "tool --api-token abc run",
    "tool --proxy-header x run", "tool --secret x run", "tool --cookie x run", "tool --auth x run",
    "cat /home/pi/.restic-" "pass", "cat ~/vault.kdbx", "cat client.ovpn", "less cert.p12", "head server.pem",
    "cat " + ETC + "wire" "guard/wg0.conf", "ls " + ETC + "ssl/" "private", "cat " + ETC + "letsencrypt/live/x/fullchain.pem",
    "cat identifiants.txt", "grep x \"mots de passe.txt\"", "tail passwords.csv",
])
def test_the_shapes_the_personas_planted_stay_out(command):
    assert why(command) in ("secrets", "key"), command


@pytest.mark.parametrize("tool_name,args", [
    ("read_file", {"path": "/home/pi/.restic-" "pass"}), ("read_file", {"path": "~/Compta/identifiants impôts.txt"}),
    ("read_file", {"path": "~/Compta/mots de passe banque.txt"}), ("read_file", {"path": ETC + "wire" "guard/wg0.conf"}),
    ("read_file", {"path": "~/vault.kdbx"}), ("read_file", {"path": "~/deploy.pem"}),
    ("read_file", {"path": "~/work/credentials-bank.md"}),
])
def test_a_private_file_the_name_rules_could_not_know_is_not_read_into_a_draft(tool_name, args):
    assert dream.excluded(tool_name, args, None, "/Users/x/proj") == "secrets"


@pytest.mark.parametrize("command", [
    "date -u +%FT%T", "date -u +%Y-%m-%dT%H:%M:%SZ", "docker compose config --quiet && echo ok",
    "python3 scripts/count_tokens.py", "python3 scripts/check_secrets_policy.py", "git log --grep=credentials",
    "git log --oneline --grep='token'", "git commit -m \"docs: rotate passwords policy\"", "grep -rn password docs/",
    "kubectl get pods -n staging", "make test", "rg -n TODO src", "pytest -x -q", "ls -la data",
    "curl -s localhost:8000/health", "git -C ~/proj log --oneline", "tail -n 20 notes.md",
    "git commit -m \"fix the build\"", "git log --since=\"2 weeks ago\"", "date -d \"2026-11-01T10:00:00\"",
])
def test_ordinary_look_alikes_pass(command):
    assert why(command) == "", command


@pytest.mark.parametrize("command", ["cat docs/tokens.md", "cat docs/passwords-policy.md", "ls secrets-of-style/"])
def test_a_file_that_says_token_or_password_in_its_name_and_is_only_read_stays_out(command):
    # kept on purpose: a name cannot tell a policy from a password list
    assert why(command) == "secrets"


def test_docker_compose_config_without_quiet_still_prints_the_environment():
    assert why("docker compose config") == "secrets"
    assert why("docker compose config | head") == "secrets"


# ---- 2. what is not a habit ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "tail -f /var/log/syslog", "tail -F app.log", "tail -n 50 -f app.log", "docker logs -f api",
    "docker compose logs -f web", "kubectl logs -f pod/web", "journalctl -u nginx -f", "journalctl --follow",
    "watch -n 5 df -h", "top", "htop", "python3 -m http.server 8000", "npm run dev", "npm start", "flask run",
    "uvicorn app:app", "docker compose up", "ping example.org", "kubectl get pods -w", "mkdocs serve",
])
def test_a_command_that_never_ends_is_never_drafted(command):
    assert why(command) == "endless", command


@pytest.mark.parametrize("command", [
    "tail -n 50 app.log", "docker logs --tail 50 api", "journalctl -u nginx | tail -40", "ping -c 3 example.org",
    "docker compose up -d", "top -l 1", "npm test", "npm run build", "docker ps", "git status",
])
def test_a_command_that_ends_is_not_called_endless(command):
    assert why(command) == "", command


def test_a_command_sent_to_the_background_does_end():
    assert why("npm run dev", background=True) == ""
    assert why("python3 -m http.server 8000 &") == ""


@pytest.mark.parametrize("command", [
    "docker system prune -af", "docker image prune", "docker volume prune -f", "docker rmi old",
    "rsync -avh --delete src/ dst/", "rsync -a --delete-after a b", "rsync --remove-source-files a b",
    "git clean -fd", "git branch -D old", "git stash drop", "git stash clear", "git reset --hard HEAD~1",
    "truncate -s 0 app.log", ": > app.log", "> app.log", "echo -n > app.log", "cat /dev/null > app.log",
    "kubectl delete pod web", "helm uninstall web", "brew uninstall wget", "apt-get purge x", "npm uninstall left-pad",
    "git -C ~/proj clean -fdx", "find . -name '*.tmp' -delete", "git push origin --delete old",
])
def test_a_deleting_command_the_word_list_missed_is_never_drafted(command):
    assert why(command) == "deletes", command


@pytest.mark.parametrize("command", [
    "git status", "git log -5", "git branch -vv", "git stash list", "docker system df", "rsync -avh src/ dst/",
    "echo hi > out.txt && cat out.txt", "make test > build.log 2>&1", "ls > /dev/null", "git diff --stat",
    "docker compose ps", "kubectl get pods", "brew list", "npm ls",
])
def test_a_command_that_only_reads_or_writes_a_new_file_is_not_called_a_delete(command):
    assert why(command) in ("", "noise"), command


def test_an_action_last_seen_more_than_30_days_ago_is_not_proposed():
    old = [session(f"o{i}", [(d, 9)], [[sh("git branch -vv")]]) for i, d in enumerate((41, 42, 43, 44))]
    fresh = [session(f"f{i}", [(d, 9)], [[sh("git status --short")]]) for i, d in enumerate((29, 30, 31, 32))]
    spotted, _, near = dream.spot(read(store_of(*old, *fresh)).turns)
    assert [s.action.args["command"] for s in spotted] == ["git status --short"]   # last seen 29 days ago
    assert near == []
    assert dream.spot(read(store_of(*old)).turns)[0] == []
    now = NOW + 10 * 86400    # the same history read ten days later
    assert dream.spot(read(store_of(*fresh)).turns, now=now)[0] == []


def test_a_share_never_goes_above_100_percent():
    # 13 of 16 turns in one folder: the figures count 13, the moments are shares of those 13
    sessions = [session(f"c{i}", [(i, 9)], [[sh("curl -s localhost:8000/health"), sh("git status")]], cwd="/Users/x/a")
                for i in range(13)]
    sessions += [session(f"d{i}", [(i, 9)], [[sh("curl -s localhost:8000/health"), sh("git status")]], cwd="/Users/x/b")
                 for i in range(3)]
    spotted, _, _ = dream.spot(read(store_of(*sessions)).turns)
    s = next(s for s in spotted if "curl" in s.action.key)
    assert s.turns == 13 and s.cwd == "/Users/x/a"
    for key in ("first", "last", "scheduled"):
        assert 0.0 <= s.moments[key] <= 1.0
    assert s.moments["first"] == 1.0 and s.moments["last"] == 0.0


def test_closes_the_work_and_first_gesture_say_nothing_when_every_turn_holds_only_that_call():
    alone = [session(f"a{i}", [(i, 9)], [[sh("df -h ~")]]) for i in range(4)]
    s = dream.spot(read(store_of(*alone)).turns)[0][0]
    assert s.moments["first"] == 0.0 and s.moments["last"] == 0.0
    both = [session(f"b{i}", [(i, 9)], [[sh("df -h ~"), sh("git status")]]) for i in range(4)]
    s = next(s for s in dream.spot(read(store_of(*both)).turns)[0] if s.action.args["command"] == "df -h ~")
    assert s.moments["first"] == 1.0 and s.moments["last"] == 0.0


def test_a_skill_load_alone_does_not_make_a_turn_hold_other_calls():
    skill = [session(f"k{i}", [(i, 9)], [[("skill_view", {"name": "deploy"}), sh("kubectl rollout status deploy/web")]])
             for i in range(4)]
    s = dream.spot(read(store_of(*skill)).turns)[0][0]
    assert s.moments["skill"] == ["deploy", 1.0] and s.moments["first"] == 0.0 and s.moments["last"] == 0.0


def test_after_skill_and_on_a_schedule_win_a_tie_over_closes_the_work_and_first_gesture():
    from habits import plugin
    text = plugin.Habits.moments_text
    ctx = object.__new__(plugin.Habits)                 # the text needs no state, only the class's own helpers
    moments = {"first": 1.0, "last": 1.0, "scheduled": 1.0, "skill": {"name": "deploy", "share": 1.0}}
    said = text(ctx, moments, "en")
    assert "after the skill deploy" in said or "deploy" in said
    assert "on a schedule" in said or "schedule" in said
    assert "closes the work" not in said and "first gesture" not in said    # two moments are shown at most
    only = text(ctx, {"first": 1.0, "last": 1.0, "scheduled": 1.0}, "en")
    assert "schedule" in only


# ---- 3. where a draft runs ----------------------------------------------------------------------------------------

def three_repos(command, n=4, explicit=False):
    sessions = []
    for i in range(n * 3):
        repo = "/Users/x/" + "abc"[i % 3]
        args = {"command": command, **({"workdir": repo} if explicit else {})}
        sessions.append(session(f"r{i}", [(i, 9)], [[("terminal", args)]], cwd=repo))
    return read(store_of(*sessions)).turns


@pytest.mark.parametrize("explicit", [False, True])
def test_a_command_that_ran_in_three_repositories_has_no_fixed_folder(explicit):
    [s] = dream.spot(three_repos("git status --short", explicit=explicit))[0]
    assert s.cwd == "" and "workdir" not in s.action.args and s.turns == 12
    reflex = dream.draft_reflex([s.action], s.cwd)
    assert "workdir" not in reflex["nodes"]["run"]["args"]
    assert "workdir" not in dream.draft_reflex([s.action], s.cwd)["nodes"]["run"]["args"]


def test_a_command_that_names_its_folder_itself_needs_none():
    [s] = dream.spot(three_repos("git -C /Users/x/a status --short"))[0]
    assert s.cwd == "" and "workdir" not in dream.draft_reflex([s.action], s.cwd)["nodes"]["run"]["args"]
    from habits.practice import _depends_on_folder
    assert _depends_on_folder("git -C /Users/x/a status --short") is False
    assert _depends_on_folder("git status --short") is True


def test_a_folder_that_holds_80_percent_of_the_turns_is_kept():
    turns = folders_history([(i, "/Users/x/a") for i in range(8)] + [(8, "/Users/x/b"), (9, "/Users/x/c")], "git status")
    [s] = dream.spot(turns)[0]
    assert s.cwd == "/Users/x/a" and s.turns == 8
    assert dream.draft_reflex([s.action], s.cwd)["nodes"]["run"]["args"]["workdir"] == "/Users/x/a"


# ---- 4. names and words -------------------------------------------------------------------------------------------

def test_a_name_never_ends_in_the_middle_of_a_word():
    steps = [A("", "mcp_calendar_list_events", {}), A("", "mcp_reminders_list_reminders", {})]
    name = dream.draft_name(steps, set())
    assert len(name) <= 34 and name == "mcp-calendar-list-events"
    long = [A("systemctl status sshd"), A("systemctl status pihole-ftl"), A("systemctl status tailscaled")]
    name = dream.draft_name(long, set())
    assert len(name) <= 34 and all(word in "systemctl status sshd pihole ftl tailscaled".split() for word in name.split("-"))
    assert dream.draft_name([A("", "x" * 50, {})], set()) == "reflex"


def test_git_dash_c_value_is_not_a_sub_command():
    a = A("git -C /Users/x/blog log --oneline")
    assert dream.draft_name([a], set()) == "git-log"
    assert dream._gist(a) == ["git", "log"]
    assert dream.describe([a]) == "Runs `git log --oneline`"
    b = A("git -C /Users/x/blog commit -am \"fix the build\"")
    assert dream.brief(b) == "git commit …" and dream.draft_name([b], set()) == "git-commit"


def test_the_words_of_a_short_name_are_whole():
    action = A("", "mcp_homeassistant_get_state", {"entity_id": "sensor.office_co2"})
    assert dream._short(action) == "mcp_homeassistant_get_state"      # longer than 24: kept whole, never cut
    assert dream._short(A("systemctl status sshd then more words here for the limit")) in ("systemctl status sshd",)
    assert dream.describe([A("", "mcp_homeassistant_get_state", {"entity_id": "sensor.office_co2"})]).endswith("state") \
        or "…" in dream.describe([A("", "mcp_homeassistant_get_state", {"entity_id": "sensor.office_co2"})])


@pytest.mark.parametrize("steps", [
    [A("git status")], [A("", "mcp_calendar_list_events", {}), A("", "mcp_reminders_list_reminders", {})],
    [A("systemctl status tailscaled"), A("systemctl status pihole-ftl"), A("systemctl status sshd")],
    [A("", "mcp_homeassistant_get_state", {"entity_id": "sensor.office_co2"})],
    [A("rsync -avh --delete-excluded /Users/x/some/long/folder /Volumes/backup/with/another/long/path")],
])
def test_what_a_reflex_says_when_it_hands_back_is_a_whole_sentence_under_the_limit(steps):
    sentence = dream.draft_reflex(steps)["nodes"]["back"]["wake"]
    assert len(sentence) <= dream.WAKE_CHARS == 60
    assert sentence.startswith("The reflex could not run") and sentence.endswith(": do it the usual way.")


def test_the_limit_of_a_hand_back_sentence_is_the_one_a_step_line_holds():
    from habits import display
    assert dream.WAKE_CHARS == display.STEP_TEXT
    assert dream.draft_reflex([A("df -h ~")])["nodes"]["back"]["wake"] == \
        "The reflex could not run df: do it the usual way."


def test_a_description_is_cut_on_a_whole_word():
    text = dream.describe([A("curl -s -o /dev/null -w '%{http_code}' http://x.test"), A("pg_dump --verbose mydatabase"),
                           A("rsync --archive source destination")])
    assert len(text) <= 60 and (text.endswith("…") is False or text[:-1].split()[-1] in text)
    assert not text.rstrip("…").endswith(("-", "_"))


def test_distinct_numbers_a_text_that_stood_before():
    assert dream.distinct(["git commit …", "make", "git commit …", "git commit …"]) == \
        ["git commit …", "make", "git commit … (2)", "git commit … (3)"]


def test_two_spotted_actions_never_show_the_same_label():
    sessions = [session(f"g{i}", [(i, 9)], [[sh('git commit -m "first thing"'), sh('git commit -am "second thing"')]])
                for i in range(4)]
    sessions += [session(f"h{i}", [(i, 9)], [[sh('grep -rn "alpha beta" docs'), sh('grep -rn "gamma delta" docs')]])
                 for i in range(4)]
    spotted, _, _ = dream.spot(read(store_of(*sessions)).turns)
    labels = [dream.brief(s.action) for s in spotted]
    assert len(labels) == len(set(labels)) == 4, labels
    assert any(label.startswith("git commit") for label in labels)

