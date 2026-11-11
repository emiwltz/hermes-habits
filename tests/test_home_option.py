"""`hermes habits dream --home <folder>`: the history of ANOTHER Hermes home is read (its state.db, read only, nothing
written there) while the drafts and proposals land in the current home. Two temporary homes; Hermes's SessionDB is
replaced by a small read-only reader of a plain sqlite file, since the unit tests never import Hermes."""

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest

from habits import catalog, dream, nursery, plugin
from test_plugin import FakeCtx

DAY = 86400


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


CURRENT = [None]


class ReadOnlyDB:
    """What the dream needs of SessionDB(db_path, read_only=True), over a plain sqlite file opened mode=ro."""

    opened = []

    def __init__(self, db_path=None, read_only=False):
        db_path = db_path or CURRENT[0] / "state.db"      # like Hermes: the current home by default
        ReadOnlyDB.opened.append((Path(db_path), read_only))
        self.conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def list_sessions_rich(self, limit=20, offset=0, **kw):
        rows = self.conn.execute("SELECT * FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?", (limit, offset))
        return [dict(r, last_active=r["started_at"]) for r in rows]

    def get_messages(self, session_id, **kw):
        rows = self.conn.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp", (session_id,))
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()


def make_home(path, commands=("df -h ~",), days=(1, 2, 3)):
    """A Hermes home with a state.db holding one session a day, each with one terminal call."""
    path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path / "state.db")
    db.executescript("CREATE TABLE sessions (id TEXT, source TEXT, started_at REAL, model_config TEXT, "
                     "parent_session_id TEXT, end_reason TEXT, cwd TEXT);"
                     "CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, tool_calls TEXT, "
                     "tool_call_id TEXT, timestamp REAL);")
    now = time.time()
    for d in days:
        at = now - d * DAY
        sid = f"s{d}"
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)", (sid, "cli", at, None, None, None, "~/proj"))
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (sid, "user", "go", None, None, at))
        for n, command in enumerate(commands):
            cid = f"{sid}-{n}"
            calls = [{"id": cid, "function": {"name": "terminal", "arguments": json.dumps({"command": command})}}]
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (sid, "assistant", "", json.dumps(calls), None, at + 1))
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                       (sid, "tool", json.dumps({"output": "x", "exit_code": 0}), None, cid, at + 2))
    db.commit()
    db.close()
    return path


@pytest.fixture
def fake_hermes(monkeypatch, tmp_path):
    """hermes_state / hermes_constants as modules: the current home is tmp_path/current."""
    ReadOnlyDB.opened = []
    CURRENT[0] = tmp_path / "current"
    state = types.ModuleType("hermes_state")
    state.SessionDB = ReadOnlyDB
    consts = types.ModuleType("hermes_constants")
    consts.get_hermes_home = lambda: tmp_path / "current"
    monkeypatch.setitem(sys.modules, "hermes_state", state)
    monkeypatch.setitem(sys.modules, "hermes_constants", consts)
    return tmp_path


def test_open_store_home_opens_that_state_db_read_only(fake_hermes):
    other = make_home(fake_hermes / "other")
    (fake_hermes / "current").mkdir()
    store = dream.open_store(other)
    assert isinstance(store, ReadOnlyDB)
    assert ReadOnlyDB.opened == [(other / "state.db", True)]
    store.close()


def test_open_store_home_accepts_text_and_a_home_sign(fake_hermes, monkeypatch):
    other = make_home(fake_hermes / "other")
    assert isinstance(dream.open_store(str(other)), ReadOnlyDB)
    monkeypatch.setenv("HOME", str(fake_hermes))
    assert isinstance(dream.open_store("~/other"), ReadOnlyDB)


def test_open_store_home_without_a_state_db_cannot_be_read(fake_hermes):
    (fake_hermes / "bare").mkdir()
    assert dream.open_store(fake_hermes / "bare") is None          # not "empty": the user named a folder
    assert dream.open_store(fake_hermes / "missing") is None


def test_open_store_without_home_is_unchanged(fake_hermes):
    assert isinstance(dream.open_store(), dream.EmptyStore)        # the current home has no state.db yet
    make_home(fake_hermes / "current")
    assert isinstance(dream.open_store(), ReadOnlyDB)
    assert ReadOnlyDB.opened[-1][0] == fake_hermes / "current" / "state.db"


@pytest.fixture
def world(fake_hermes, monkeypatch):
    monkeypatch.setattr(catalog, "skills", lambda: [])
    monkeypatch.setattr(nursery, "_dream", lambda: dream)
    ctx = FakeCtx({"learning": "practice"})
    habits = plugin.Habits(ctx, home=fake_hermes / "current" / "plugin-data" / "habits")
    return habits


def run_cli(habits, *argv):
    parser = argparse.ArgumentParser()
    habits.cli_setup(parser)
    return habits.cli_handle(parser.parse_args(["dream", *argv]))


def test_dream_now_reads_the_other_home_and_writes_nothing_in_this_one(world, fake_hermes):
    other = make_home(fake_hermes / "other")
    before = {p.name: digest(p) for p in other.iterdir()}
    summary = world.dream_now(manual=True, home=other)
    assert summary["status"] == "ok" and summary["turns"] == 3 and summary["sessions"] == 3
    assert [s["action"] for s in summary["spotted"]] == ["df -h ~"]
    assert summary["practising"] == [] and summary["look"] == "home"      # a look: no draft lands in this home
    assert world.ledger.drafts() == [] and world.ledger.last_dream() is None
    assert {p.name: digest(p) for p in other.iterdir()} == before          # nothing written there, nothing added
    assert [p[1] for p in ReadOnlyDB.opened] == [True]
    assert (fake_hermes / "current" / "plugin-data" / "habits").is_dir()
    assert not (other / "plugin-data").exists()
    assert "drafts" not in world.dream_text(summary).split("This was a look")[0]


def test_cli_home_option(world, fake_hermes, capsys):
    other = make_home(fake_hermes / "other")
    assert run_cli(world, "--json", "--home", str(other)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and out["turns"] == 3
    assert run_cli(world, "--home", str(other), "--days", "30") == 0    # text, a narrower window
    assert "df -h ~" in capsys.readouterr().out


def test_cli_home_without_history_says_it_could_not_read(world, fake_hermes, capsys):
    (fake_hermes / "bare").mkdir()
    assert run_cli(world, "--json", "--home", str(fake_hermes / "bare")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "unread" and out["why"]


def test_dream_now_without_home_still_reads_this_one(world, fake_hermes):
    make_home(fake_hermes / "current")
    assert world.dream_now(manual=True)["turns"] == 3


def test_cli_home_is_a_look_it_adds_no_draft_to_this_home(world, fake_hermes, capsys):
    other = make_home(fake_hermes / "other")
    assert run_cli(world, "--home", str(other)) == 0
    text = capsys.readouterr().out
    assert "df -h ~" in text and "This was a look at another home: nothing was saved here." in text
    assert "wait for you" not in text
    assert world.ledger.drafts() == [] and world.ledger.proposals() == []
    assert run_cli(world, "--home", str(other), "--json") == 0
    assert json.loads(capsys.readouterr().out)["look"] == "home"
