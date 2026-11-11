"""The ledger's nursery: schema 3, the caution table, drafts, practice, proposals, dreams, one news line a day."""

import sqlite3
import threading
from datetime import datetime

import pytest

from habits.ledger import Ledger, Rules, rules_for

# The schema as version 2 left it (the one a user's real file has), written out so the test owns it.
SCHEMA_V2 = """
CREATE TABLE runs (
  id INTEGER PRIMARY KEY, habit TEXT, version TEXT, kind TEXT, mode TEXT, at REAL,
  ms INTEGER, turn_ms INTEGER, status TEXT, reason TEXT, failed INTEGER,
  s1 TEXT, planned INTEGER, matched INTEGER, judged INTEGER, compared INTEGER DEFAULT 0,
  woke INTEGER DEFAULT 0, trace TEXT, turn_tokens INTEGER, turn_calls INTEGER, outage INTEGER DEFAULT 0,
  verdict TEXT, session TEXT, said TEXT);
CREATE TABLE habits (
  habit TEXT PRIMARY KEY, version TEXT, status TEXT, since REAL, note TEXT, allowed INTEGER DEFAULT 0,
  start REAL, origin TEXT DEFAULT 'asked', ready REAL, wait INTEGER);
CREATE TABLE memory (habit TEXT, node TEXT, value TEXT, PRIMARY KEY (habit, node));
CREATE TABLE calls (session TEXT, at REAL, tool TEXT, args TEXT);
CREATE TABLE failures (habit TEXT PRIMARY KEY, checks INTEGER);
CREATE TABLE news (
  id INTEGER PRIMARY KEY, habit TEXT, version TEXT, kind TEXT, note TEXT, at REAL, told INTEGER DEFAULT 0);
CREATE TABLE history (
  id INTEGER PRIMARY KEY, habit TEXT, version TEXT, "from" TEXT, "to" TEXT, "by" TEXT, note TEXT, at REAL);
"""


class Clock:
    def __init__(self, t=None):
        self.t = t if t is not None else datetime(2026, 11, 11, 9, 0).timestamp()

    def __call__(self):
        return self.t


def v2_file(path, runs=178):
    """A real-looking schema-2 file: habits of both origins, runs, history, news, memory."""
    db = sqlite3.connect(path)
    db.executescript(SCHEMA_V2)
    db.execute("INSERT INTO habits (habit, version, status, since, origin) VALUES ('a', 'a1', 'active', 5, 'asked')")
    db.execute("INSERT INTO habits (habit, version, status, since, origin) VALUES ('b', 'b1', 'shadow', 5, 'learned')")
    db.execute('INSERT INTO history (habit, version, "from", "to", "by", note, at) VALUES '
               "('a', 'a1', 'shadow', 'active', 'user', 'approved', 5)")
    for i in range(runs):
        db.execute("INSERT INTO runs (habit, version, kind, mode, at, status, failed, planned, matched, compared, trace) "
                   "VALUES ('a', 'a1', 'message', 'active', ?, 'end', 0, 1, 1, 1, '{}')", (i,))
    db.execute("INSERT INTO news (habit, version, kind, note, at, told) VALUES ('a', 'a1', 'retired', 'x', 1, 0)")
    db.execute("INSERT INTO news (habit, version, kind, note, at, told) VALUES ('a', 'a1', 'ready', 'old', 1, 1)")
    db.execute("INSERT INTO memory VALUES ('a', 'n', 'v')")
    db.execute("PRAGMA user_version = 2")
    db.commit()
    db.close()


def version_of(ledger):
    with ledger._db() as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def tables_of(ledger):
    with ledger._db() as db:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_v2_file_upgrades_to_v3_once(tmp_path):
    path = tmp_path / "habits.db"
    v2_file(path)
    errors, ledgers = [], []

    def open_it():
        try:
            ledgers.append(Ledger(path))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=open_it) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    ledger = ledgers[0]
    assert version_of(ledger) == 3
    assert {"drafts", "proposals", "practice", "dreams"} <= tables_of(ledger)
    with ledger._db() as db:
        assert "told_at" in {r[1] for r in db.execute("PRAGMA table_info(news)")}
    # nothing lost: the 178 runs, both habits, the history, the memory, the untold and the told news
    assert len(ledger._do("SELECT 1 FROM runs")) == 178
    assert ledger.info("a")["origin"] == "asked" and ledger.info("b")["origin"] == "learned"
    assert ledger.status("a", "a1") == "active"
    assert ledger.memory("a") == {"n": "v"}
    assert [n["kind"] for n in ledger.news(take=False)] == ["retired"]
    assert len(ledger._do("SELECT 1 FROM news")) == 2


def test_v1_file_passes_v2_then_v3(tmp_path):
    path = tmp_path / "habits.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA_V2)
    db.execute("INSERT INTO habits (habit, version, status, since, origin) VALUES ('p', 'p1', 'proposed', 1, 'learned')")
    db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, session TEXT)")
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    ledger = Ledger(path)
    assert version_of(ledger) == 3
    assert ledger.info("p")["status"] == "retired"  # v2 ran
    assert "turns" not in tables_of(ledger)
    assert "drafts" in tables_of(ledger)  # v3 ran


def test_a_fresh_file_is_v3_and_reopening_changes_nothing(tmp_path):
    ledger = Ledger(tmp_path / "h.db")
    ledger.put_draft("k", name="n", tool="terminal", raw="{}", turns=3, days=2)
    again = Ledger(tmp_path / "h.db")
    assert version_of(again) == 3
    assert again.draft("k")["turns"] == 3


def test_rules_for_caution_and_learning():
    normal = rules_for("normal", "practise")
    default = Rules()
    for field in ("promote_runs", "promote_agreement", "retire_window", "retire_agreement", "retire_errors",
                  "retire_low_spots", "demote_wakes", "promote", "ready_matches", "rest_differs", "spot_check",
                  "min_confidence", "trigger_confidence"):
        assert getattr(normal, field) == getattr(default, field), field
    assert rules_for("normal", "auto").promote == "auto"
    assert rules_for("normal", "suggest").promote == "ask"
    assert rules_for("nonsense", "practise") == normal
    assert rules_for("strict", "practise").ready_matches == 5
    assert rules_for("relaxed", "practise").rest_differs == 3
    assert rules_for("strict", "practise", {"promote_runs": 4}).promote_runs == 4  # an override wins
    assert rules_for("strict", "practise", {"promote_runs": 4}).promote_agreement == .95


def test_adopt_learned(tmp_path):
    ledger = Ledger(tmp_path / "h.db", Rules(), clock=Clock())
    ledger.adopt("l", "v1", "learned")
    assert ledger.info("l")["origin"] == "learned" and ledger.info("l")["status"] == "shadow"
    assert ledger.approve("l", "v1", by="user")
    assert ledger.status("l", "v1") == "active" and ledger.vouched("l")
    ledger.adopt("r", "v1", "learned")
    assert ledger.approve("r", "v1", by="rules")
    assert ledger.status("r", "v1") == "active"  # the rules are a voucher
    assert not ledger.vouched("r")  # the model does not read its text before a user's yes
    with pytest.raises(ValueError):
        ledger.adopt("x", "v1", "portal")


def test_move_draft_is_compare_and_set(tmp_path):
    ledger = Ledger(tmp_path / "h.db")
    ledger.put_draft("k", name="n")
    assert ledger.move_draft("k", "practising", ("spotted",))
    wins, barrier = [], threading.Barrier(2)

    def move(to):
        barrier.wait()
        wins.append(ledger.move_draft("k", to, ("practising",)))

    threads = [threading.Thread(target=move, args=(to,)) for to in ("ready", "rest")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(wins) == [False, True]
    assert not ledger.move_draft("k", "no", ("spotted",))


def test_put_draft_freezes_past_spotted(tmp_path):
    ledger = Ledger(tmp_path / "h.db")
    ledger.put_draft("k", name="one", tool="terminal", args={"command": "df"}, raw='{"command":"df"}',
                     graph={"nodes": 1}, reads=True, turns=3, days=2, moments={"first": .6})
    row = ledger.draft("k")
    assert row["state"] == "spotted" and row["args"] == {"command": "df"} and row["graph"] == {"nodes": 1}
    assert row["moments"] == {"first": .6} and row["reads"] == 1
    ledger.put_draft("k", name="two", raw='{"command":"df -h"}', turns=4)  # still spotted: written
    assert ledger.draft("k")["name"] == "two" and ledger.draft("k")["turns"] == 4
    assert ledger.move_draft("k", "practising", ("spotted",))
    ledger.put_draft("k", name="three", raw="other", graph={"nodes": 2}, args={"x": 1}, turns=9, days=5,
                     last_seen=7.0)
    row = ledger.draft("k")
    assert (row["name"], row["raw"], row["graph"], row["args"]) == ("two", '{"command":"df -h"}', {"nodes": 1},
                                                                    {"command": "df"})
    assert (row["turns"], row["days"], row["last_seen"]) == (9, 5, 7.0)  # counts still move
    assert row["state"] == "practising"  # put_draft never sets the state
    with pytest.raises(ValueError):
        ledger.put_draft("k", state="saved")


def test_practised_counts_and_streak(tmp_path):
    ledger = Ledger(tmp_path / "h.db", clock=Clock())
    ledger.put_draft("k", name="n")
    steps = [("same", {"same": 1, "streak": 0}), ("differs", {"differs": 1, "streak": 1}),
             ("error", {"errors": 1, "streak": 2}), ("seen", {"seen": 1, "streak": 2}),
             ("shape", {"shape": 1, "streak": 0})]
    for verdict, expect in steps:
        row = ledger.practised("k", "plan", verdict, "s1")
        for column, value in expect.items():
            assert row[column] == value, (verdict, column)
    assert (row["same"], row["shape"], row["differs"], row["errors"], row["seen"]) == (1, 1, 1, 1, 1)
    assert [r["verdict"] for r in ledger.practice("k")] == ["shape", "seen", "error", "differs", "same"]
    assert len(ledger.practice("k", limit=2)) == 2
    assert ledger.practised("nope", "plan", "same") is None
    with pytest.raises(ValueError):
        ledger.practised("k", "plan", "great")


def test_practice_keeps_no_output(tmp_path):
    ledger = Ledger(tmp_path / "h.db")
    with ledger._db() as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(practice)")}
    assert columns == {"id", "draft", "at", "session", "kind", "verdict"}
    with pytest.raises(TypeError):
        ledger.practised("k", "read", "same", "s", "some output")


def test_propose_one_open_per_subject_and_no_holds(tmp_path):
    clock = Clock()
    ledger = Ledger(tmp_path / "h.db", clock=clock)
    first = ledger.propose("change", "h", {"why": "sibling"})
    assert isinstance(first, int) and ledger.propose("change", "h", {}) is None
    assert ledger.propose("rest", "h", {}) is not None  # another kind is another proposal
    assert [p["detail"] for p in ledger.proposals() if p["kind"] == "change"] == [{"why": "sibling"}]
    assert ledger.decide(first, "no") and not ledger.decide(first, "yes")  # once only
    assert ledger.proposals(("no",))[0]["by"] == "user"
    clock.t += 29 * 86400
    assert ledger.propose("change", "h", {}) is None  # the no holds 30 days
    clock.t += 2 * 86400
    assert ledger.propose("change", "h", {}) is not None
    with pytest.raises(ValueError):
        ledger.propose("delete", "h", {})


def test_dream_start_once_per_window(tmp_path):
    clock = Clock()
    ledger = Ledger(tmp_path / "h.db", clock=clock)
    window = 20 * 3600
    first = ledger.dream_start(window)
    assert first is not None
    ledger.dream_end(first, "ok", turns=10, sessions=2, spotted=1, proposed=0)
    assert ledger.last_dream(ok_only=True)["turns"] == 10
    clock.t += 3600
    assert ledger.dream_start(window) is None
    clock.t += 20 * 3600
    second = ledger.dream_start(window)
    assert second is not None
    ledger.dream_end(second, "failed", why="x")
    assert ledger.last_dream()["status"] == "failed" and ledger.last_dream(ok_only=True)["id"] == first
    assert ledger.dream_start(window) is None  # a failed dream waits an hour...
    clock.t += 3601
    third = ledger.dream_start(window)  # ...and does not block after it
    assert third is not None
    clock.t += 3601
    fourth = ledger.dream_start(window)  # `third` was left running: a dead process's, marked failed
    assert ledger._do("SELECT status FROM dreams WHERE id = ?", (third,))[0]["status"] == "failed"
    assert fourth is not None
    assert ledger.dream_start(0, manual=True) is not None  # manual with no window always starts
    assert ledger.last_dream()["manual"] == 1


def test_news_one_a_day(tmp_path):
    clock = Clock(datetime(2026, 11, 11, 22, 0).timestamp())
    ledger = Ledger(tmp_path / "h.db", clock=clock)
    with ledger._tx() as db:
        ledger._tell(db, "a", "v", "dream_learn", "one")
        ledger._tell(db, "b", "v", "dream_proposal", "two")
    rows = ledger.news(take=False, daily=True)
    assert rows and rows[0]["note"] == "one"
    assert not ledger.told_today()
    ledger.told([rows[0]["id"]])
    assert ledger.told_today()
    assert ledger.news(take=False, daily=True) == []
    assert [r["note"] for r in ledger.news(take=False)] == ["two"]  # without daily: unchanged
    clock.t = datetime(2026, 11, 12, 0, 5).timestamp()  # past local midnight
    assert not ledger.told_today()
    assert [r["note"] for r in ledger.news(take=False, daily=True)] == ["two"]


def test_taking_news_notes_the_day(tmp_path):
    clock = Clock()
    ledger = Ledger(tmp_path / "h.db", clock=clock)
    with ledger._tx() as db:
        ledger._tell(db, "a", "v", "ready", "x")
    assert len(ledger.news()) == 1  # take=True
    assert ledger.told_today()


def test_news_kinds_survive_v2_cleanup(tmp_path):
    path = tmp_path / "h.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA_V2)
    for kind in ("dream_learn", "dream_learned", "dream_proposal", "forgotten"):
        db.execute("INSERT INTO news (habit, version, kind, note, at) VALUES ('a', 'v', ?, '', 1)", (kind,))
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    ledger = Ledger(path)
    assert sorted(n["kind"] for n in ledger.news(take=False)) == ["dream_learn", "dream_learned", "dream_proposal"]


def test_prune_forgets_old_practice_and_old_dreams(tmp_path):
    clock = Clock()
    ledger = Ledger(tmp_path / "h.db", clock=clock)
    ledger.put_draft("k", name="n")
    ledger.practised("k", "plan", "same")
    clock.t += 61 * 86400
    ledger.practised("k", "plan", "same")
    for _ in range(35):
        ledger.dream_end(ledger.dream_start(0), "ok")
    ledger.prune()
    assert len(ledger.practice("k")) == 1
    assert len(ledger._do("SELECT 1 FROM dreams")) == 30


def test_tell_writes_a_line_of_news_of_a_known_kind(tmp_path):
    ledger = Ledger(tmp_path / "habits.db")
    ledger.tell("df", "dream_learn")
    assert [(n["habit"], n["kind"], n["version"]) for n in ledger.news(take=False)] == [("df", "dream_learn", "")]
    with pytest.raises(ValueError):
        ledger.tell("df", "anything")
