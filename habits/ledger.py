"""The ledger: runs, scores, statuses and memory, in one SQLite file.

A habit earns its autonomy: it starts in shadow, becomes active after enough
compared runs that agree with the LLM (or, with promote: ask, once the user says
yes to it when they are met), and retires when it stops agreeing. Status is kept
per version (the graph's fingerprint): any change to the graph sends it back to
shadow with fresh scores. It also keeps each status change and the news still to
tell.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, habit TEXT, version TEXT, kind TEXT, mode TEXT, at REAL,
  ms INTEGER, turn_ms INTEGER, status TEXT, reason TEXT, failed INTEGER,
  s1 TEXT, planned INTEGER, matched INTEGER, judged INTEGER, compared INTEGER DEFAULT 0,
  woke INTEGER DEFAULT 0, trace TEXT, turn_tokens INTEGER, turn_calls INTEGER, outage INTEGER DEFAULT 0,
  verdict TEXT, session TEXT, said TEXT);
CREATE INDEX IF NOT EXISTS runs_habit ON runs (habit, version, id);
CREATE TABLE IF NOT EXISTS habits (
  habit TEXT PRIMARY KEY, version TEXT, status TEXT, since REAL, note TEXT, allowed INTEGER DEFAULT 0,
  start REAL, origin TEXT DEFAULT 'asked', ready REAL, wait INTEGER);
CREATE TABLE IF NOT EXISTS memory (habit TEXT, node TEXT, value TEXT, PRIMARY KEY (habit, node));
CREATE TABLE IF NOT EXISTS calls (session TEXT, at REAL, tool TEXT, args TEXT);
CREATE TABLE IF NOT EXISTS failures (habit TEXT PRIMARY KEY, checks INTEGER);
CREATE TABLE IF NOT EXISTS news (
  id INTEGER PRIMARY KEY, habit TEXT, version TEXT, kind TEXT, note TEXT, at REAL, told INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY, habit TEXT, version TEXT, "from" TEXT, "to" TEXT, "by" TEXT, note TEXT, at REAL);
"""

logger = logging.getLogger(__name__)

# Origins older versions wrote (the writer, the side door): such a habit still acts on its own only after the user's
# yes, or the rules' in auto.
MADE = ("learned", "portal")
VOUCHERS = ("user", "cli", "rules")  # who may make such a habit active: the user, in a chat or a terminal; the rules
# The user's own yes, which shows the model a habit Hermes made: the chat's alone. A terminal is the model's too (a
# guard on its words misses xargs or a variable), and the rules' promotion is none.
YES = ("user",)
SCHEMA_VERSION = 2  # 1: an active state counts only with its move in the history; 2: the learning layer is gone
OLD_PROPOSED = "proposed by an older version: /habits try lets it practise"
NEWS = ("ready", "retired", "demoted")  # the kinds of news this version tells
GRAPH = 12  # a version's first characters: its graph's fingerprint, the whole version before descriptions counted
DAY = 86400
TURN_DAYS = 14  # how long a run keeps its request
MESSAGE_CHARS = 300
UNDO_SECONDS = DAY


@dataclass(frozen=True)
class Rules:
    promote_runs: int = 12
    promote_agreement: float = 0.9
    retire_window: int = 20
    retire_agreement: float = 0.75
    retire_errors: int = 3
    retire_low_spots: int = 2
    demote_wakes: int = 3
    promote: str = "ask"  # auto: the thresholds alone make a habit active; anything else waits for the user's yes


class Ledger:
    def __init__(self, path, rules=Rules(), clock=time.time):
        """rules: the thresholds, or a function that reads them where they apply (the plugin's settings, which the
        user may change in Settings while the gateway runs)."""
        self.path, self._rules, self.clock = str(path), rules, clock
        self.lock = threading.Lock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            self._schema(db)  # also brings an older file up to date

    @property
    def rules(self) -> Rules:
        return self._rules() if callable(self._rules) else self._rules

    @staticmethod
    def _schema(db):
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        columns = {row[1] for row in db.execute("PRAGMA table_info(habits)")}
        for column, kind in (("start", "REAL"), ("origin", "TEXT DEFAULT 'asked'"), ("ready", "REAL"),
                             ("wait", "INTEGER")):
            if column not in columns:  # a habit from before origins was saved on the user's ask: asked
                Ledger._add(db, "habits", column, kind)
        if "news" in columns:  # an older file kept its one piece of news on the habit: a retirement untold
            db.execute("INSERT INTO news (habit, version, kind, note, at) SELECT habit, version, 'retired', note, "
                       "since FROM habits WHERE news = 1 AND status = 'retired'")
            db.execute("UPDATE habits SET news = 0 WHERE news = 1")
        columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
        for column, kind in (("turn_tokens", "INTEGER"), ("turn_calls", "INTEGER"),
                             ("outage", "INTEGER DEFAULT 0"), ("verdict", "TEXT"), ("session", "TEXT"),
                             ("said", "TEXT")):
            if column not in columns:
                Ledger._add(db, "runs", column, kind)
        db.execute("CREATE INDEX IF NOT EXISTS runs_session ON runs (session, id)")  # after its column
        current = db.execute("PRAGMA user_version").fetchone()[0]
        if current < 1:  # never again on a file past it: it would vouch an active state written by hand since
            # Once: a habit made active before its moves were kept (asked for, or promoted by the rules) keeps its
            # state, with the move on record. From now on, an active state with no move on record is not trusted.
            for row in db.execute("SELECT habit, version, since, origin FROM habits WHERE status = 'active'").fetchall():
                last = db.execute('SELECT version, "to" FROM history WHERE habit = ? ORDER BY id DESC LIMIT 1',
                                  (row[0],)).fetchone()
                if row[3] not in MADE and (last is None or tuple(last) != (row[1], "active")):  # asked, or before origins
                    db.execute('INSERT INTO history (habit, version, "from", "to", "by", note, at) VALUES '
                               "(?, ?, NULL, 'active', 'habits', 'active before the history was kept', ?)",
                               (row[0], row[1], row[2]))
            db.execute("PRAGMA user_version = 1")
        if current < 2:
            db.commit()  # what came before is its own transaction
            Ledger._v2(db)

    @staticmethod
    def _v2(db):
        """Once, under the write lock (the gateway and a cron tick may open the file at once): a habit the learning
        layer proposed rests (it never ran), its untold news goes, and the turns it kept go."""
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] >= 2:
            db.commit()
            return
        now = time.time()
        for habit, version in db.execute("SELECT habit, version FROM habits WHERE status = 'proposed'").fetchall():
            db.execute("UPDATE habits SET status = 'retired', since = ?, note = ?, ready = NULL, wait = NULL "
                       "WHERE habit = ?", (now, OLD_PROPOSED, habit))
            db.execute('INSERT INTO history (habit, version, "from", "to", "by", note, at) VALUES '
                       "(?, ?, 'proposed', 'retired', 'habits', ?, ?)", (habit, version, OLD_PROPOSED, now))
        db.execute("DELETE FROM news WHERE told = 0 AND kind NOT IN (?, ?, ?)", NEWS)
        db.execute("DROP TABLE IF EXISTS turns")
        db.execute("DROP TABLE IF EXISTS candidates")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        db.commit()

    @staticmethod
    def _add(db, table, column, kind):
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        except sqlite3.OperationalError as exc:  # the gateway and a cron tick upgrading the same file at once
            if "duplicate column" not in str(exc):
                raise

    @contextmanager
    def _db(self):
        """One connection per operation, committed and closed: safe across threads."""
        missing = not os.path.exists(self.path)  # deleted under a live process: the gateway lives for days
        if missing:  # its folder too, maybe: sqlite creates the file, not the folder
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            if missing:
                self._schema(db)  # an empty file again; the statuses it held are lost
            with db:
                yield db
        finally:
            db.close()

    def _do(self, sql, params=()):
        with self.lock, self._db() as db:
            return db.execute(sql, params).fetchall()

    @contextmanager
    def _tx(self):
        """One write transaction that holds the file's write lock from its first read: what it read is still true
        when it writes, across threads and processes (a compare-and-set)."""
        with self.lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    # ---- status ---------------------------------------------------------

    def status(self, habit: str, version: str) -> str:
        """shadow, active or retired. A new version always starts in shadow. An active state the history does not
        record (written into the file by hand) goes back to shadow."""
        rows = self._do("SELECT version, status FROM habits WHERE habit = ?", (habit,))
        if rows and len(version) > GRAPH and rows[0]["version"] == version[:GRAPH]:
            self._rename(habit, rows[0]["version"], version)  # the same graph, named before its description counted
            rows = self._do("SELECT version, status FROM habits WHERE habit = ?", (habit,))
        if not rows or rows[0]["version"] != version:
            status = "shadow"
            # First sight: every run of this version counts. A changed version: only new runs.
            self.set_status(habit, version, status, "new version" if rows else "saved", start=None if rows else 0)
            return status
        if rows[0]["status"] == "active":
            with self._tx() as db:
                if not self._vouched(db, habit, version):
                    logger.warning("habits: %s is active with no move on record: back to shadow", habit)
                    self._move(db, habit, version, "shadow", "active with no yes on record", by="habits")
                    return "shadow"
        return rows[0]["status"]

    def _rename(self, habit, old, new):
        """A version as the plugin named it before, carried over to its new name with its state, runs and news."""
        with self._tx() as db:
            for table in ("habits", "runs", "news", "history"):
                db.execute(f"UPDATE {table} SET version = ? WHERE habit = ? AND version = ?", (new, habit, old))

    @staticmethod
    def _vouched(db, habit, version) -> bool:
        """The habit's last move on record made this version active, and for a habit Hermes made, the user or the
        rules made it."""
        row = db.execute("SELECT version, status, origin FROM habits WHERE habit = ?", (habit,)).fetchone()
        if row is None or (row["version"], row["status"]) != (version, "active"):
            return True  # moved meanwhile: nothing to check here
        last = db.execute('SELECT version, "to", "by" FROM history WHERE habit = ? ORDER BY id DESC LIMIT 1',
                          (habit,)).fetchone()
        if last is None or (last["version"], last["to"]) != (version, "active"):
            return False
        made = row["origin"] in MADE or db.execute(
            'SELECT 1 FROM history WHERE habit = ? AND "by" IN (?, ?) LIMIT 1', (habit, *MADE)).fetchone()
        return not made or last["by"] in VOUCHERS

    def set_status(self, habit, version, status, note="", start=None, by="habits"):
        """Entering shadow (a new version, a reset) restarts the scores and a watch's row of failed checks;
        other moves keep them. by: who moved it, for the history (user, rules, habits for the plugin's own
        bookkeeping, or an origin). Any move drops the habit's untold news, now stale, and its readiness: a
        retirement by the thresholds tells its own after it."""
        with self._tx() as db:
            self._move(db, habit, version, status, note, start, by)

    def _move(self, db, habit, version, status, note="", start=None, by="habits"):
        was = db.execute("SELECT version, status FROM habits WHERE habit = ?", (habit,)).fetchone()
        last = db.execute("SELECT COALESCE(MAX(id), 0) FROM runs").fetchone()[0] if start is None else start
        # A first sight has no origin: only habit_save says the habit is the user's.
        db.execute("INSERT INTO habits (habit, version, status, since, note, start, origin) VALUES (?, ?, ?, ?, ?, ?, NULL) "
                   "ON CONFLICT(habit) DO UPDATE SET version = excluded.version, status = excluded.status, "
                   "since = excluded.since, note = excluded.note, ready = NULL, wait = NULL, "
                   "start = CASE WHEN excluded.status = 'shadow' THEN excluded.start ELSE COALESCE(start, excluded.start) END",
                   (habit, version, status, self.clock(), note, last))
        if was is None or (was["version"], was["status"]) != (version, status):  # a reset in place is no change
            self._history(db, habit, version, was["status"] if was else None, status, by, note)
        db.execute("DELETE FROM news WHERE habit = ? AND told = 0", (habit,))
        if status == "shadow":  # a repair counts its own failures: the old row, past 3, would hold them all
            db.execute("DELETE FROM failures WHERE habit = ?", (habit,))

    def _history(self, db, habit, version, was, now, by, note=""):
        db.execute('INSERT INTO history (habit, version, "from", "to", "by", note, at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                   (habit, version, was, now, by, note, self.clock()))

    def _tell(self, db, habit, version, kind, note=""):
        db.execute("INSERT INTO news (habit, version, kind, note, at) VALUES (?, ?, ?, ?, ?)",
                   (habit, version, kind, note, self.clock()))

    def adopt(self, habit, version, origin="asked", status="shadow", note=""):
        """A habit habit_save just saved on the user's ask: on record as asked, in shadow. Pass the version the
        catalog reads, or the next look at it starts it again."""
        if origin != "asked":
            raise ValueError(origin)
        with self._tx() as db:
            self._move(db, habit, version, status, note, by=origin)
            db.execute("UPDATE habits SET origin = ? WHERE habit = ?", (origin, habit))

    def vouched(self, habit) -> bool:
        """The user said yes to the habit as it is: active by their own last move on record, in a chat. Only then
        does the model read the text of a habit Hermes made."""
        row = self.info(habit)
        if row is None or row["status"] != "active":
            return False
        last = self.history(habit, 1)
        return bool(last) and (last[0]["version"], last[0]["to"]) == (row["version"], "active") and last[0]["by"] in YES

    def approve(self, habit, version, by="user") -> bool:
        """The user's yes to this version, as a compare-and-set: it becomes active only if it is still this version,
        in shadow. False when it changed meanwhile (the caller first passes the version the catalog reads now
        through status(), so a changed file is seen), or is not in shadow. Its scores and readiness are kept: an
        undo brings back the habit as it was before the yes."""
        with self._tx() as db:
            moved = db.execute("UPDATE habits SET status = 'active', since = ?, note = ? WHERE habit = ? AND "
                               "version = ? AND status = 'shadow'", (self.clock(), "approved", habit, version)).rowcount
            if moved:
                self._history(db, habit, version, "shadow", "active", by, "approved")
                db.execute("DELETE FROM news WHERE habit = ? AND told = 0", (habit,))
        return bool(moved)

    def later(self, habit, version) -> bool:
        """Not yet, says the user to a ready habit: it keeps learning, and is ready again after promote_runs/2 more
        counted runs. False when it is not this version, in shadow, ready."""
        _, counted = self.agreement(habit, version)
        with self._tx() as db:
            moved = db.execute("UPDATE habits SET ready = NULL, wait = ? WHERE habit = ? AND version = ? AND "
                               "status = 'shadow' AND ready IS NOT NULL",
                               (counted + max(1, self.rules.promote_runs // 2), habit, version)).rowcount
            if moved:
                db.execute("DELETE FROM news WHERE habit = ? AND told = 0 AND kind = 'ready'", (habit,))
        return bool(moved)

    def wrong(self, session, by="user") -> str | None:
        """The user says the last habit that answered in this session got it wrong: that run counts as a
        disagreement and, still active in that version, the habit goes back to shadow, its new scores starting
        with that run. Returns its name, or None when no habit answered in this session."""
        with self._tx() as db:
            run = db.execute("SELECT id, habit, version FROM runs WHERE session = ? AND mode = 'active' "
                             "ORDER BY id DESC LIMIT 1", (session,)).fetchone()
            if run is None:
                return None
            note = "wrong, said the user"
            db.execute("UPDATE runs SET compared = 1, planned = MAX(COALESCE(planned, 0), 1), matched = 0, judged = 1, "
                       "verdict = ? WHERE id = ?", (note, run["id"]))
            row = db.execute("SELECT version, status FROM habits WHERE habit = ?", (run["habit"],)).fetchone()
            if row is not None and (row["version"], row["status"]) == (run["version"], "active"):
                self._move(db, run["habit"], run["version"], "shadow", note, start=run["id"] - 1, by=by)
        return run["habit"]

    def undo(self, habit=None, by="user", of=None) -> dict | None:
        """Undoes the user's last status change of the last 24 h (of that habit, when given), if the habit is still
        where that change put it: the history row undone, or None. of: whose changes it may undo (by's alone by
        default: the chat's annule undoes the chat's, a terminal's undo both). Scores and readiness stay as they
        are, so a yes undone leaves the habit ready, as before. Its undo is a change too: a second undo puts it
        back."""
        of = tuple(of or (by,))
        with self._tx() as db:
            sql = (f'SELECT * FROM history WHERE "by" IN ({", ".join("?" for _ in of)}) AND at >= ?'
                   + (" AND habit = ?" if habit else ""))
            params = (*of, self.clock() - UNDO_SECONDS) + ((habit,) if habit else ())
            last = db.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
            if last is None or last["from"] is None:
                return None
            back = "retired" if last["from"] == "proposed" else last["from"]  # a state an older version had
            moved = db.execute("UPDATE habits SET status = ?, since = ?, note = ? WHERE habit = ? AND version = ? "
                               "AND status = ?", (back, self.clock(), "undone", last["habit"],
                                                  last["version"], last["to"])).rowcount
            if not moved:
                return None
            self._history(db, last["habit"], last["version"], last["to"], back, by, "undone")
            db.execute("DELETE FROM news WHERE habit = ? AND told = 0", (last["habit"],))
        return dict(last)

    def history(self, habit=None, limit=20) -> list[dict]:
        """Status changes, the newest first: habit, version, from, to, by, note, at."""
        sql, params = "SELECT * FROM history", ()
        if habit:
            sql, params = sql + " WHERE habit = ?", (habit,)
        return [dict(r) for r in self._do(sql + " ORDER BY id DESC LIMIT ?", params + (limit,))]

    def retire(self, habit, version, note, by="rules"):
        """A retirement nobody asked for (the thresholds): news to tell, once even when two threads see it."""
        with self._tx() as db:
            was = db.execute("SELECT version, status FROM habits WHERE habit = ?", (habit,)).fetchone()
            if was is not None and (was["version"], was["status"]) == (version, "retired"):
                return
            self._move(db, habit, version, "retired", note, by=by)
            self._tell(db, habit, version, "retired", note)

    def news(self, names=None, kinds=None, take=True) -> list[dict]:
        """The news nobody was told of yet (of these habits and kinds, when given), oldest first: id, habit,
        version, kind (ready, retired, demoted), note, at. Taken once: two processes (the
        gateway and a cron tick) never both tell it. take=False only looks; told() then takes what went out."""
        with self.lock, self._db() as db:
            rows = [dict(r) for r in db.execute("SELECT id, habit, version, kind, note, at FROM news WHERE told = 0 "
                                                "ORDER BY id")
                    if (names is None or r["habit"] in names) and (kinds is None or r["kind"] in kinds)]
            return [r for r in rows if not take or
                    db.execute("UPDATE news SET told = 1 WHERE id = ? AND told = 0", (r["id"],)).rowcount]

    def told(self, ids) -> list[int]:
        """Takes these news rows: the ids that nobody had taken yet."""
        with self.lock, self._db() as db:
            return [i for i in ids if db.execute("UPDATE news SET told = 1 WHERE id = ? AND told = 0", (i,)).rowcount]

    def untold(self, names=None, take=True) -> list[tuple[str, str]]:
        """(habit, why) of each retirement by the thresholds nobody was told of yet (of names alone, when given),
        taken once. take=False only looks: a turn takes its news once its reply went out."""
        return [(r["habit"], r["note"] or "") for r in self.news(names, ("retired",), take)]

    def fresh(self, habit) -> bool:
        """Never measured: no run recorded under this name, and no status but the shadow a first sight gives."""
        row = self.info(habit)
        return (row is None or row["status"] == "shadow") and not self._do(
            "SELECT 1 FROM runs WHERE habit = ? LIMIT 1", (habit,))

    def info(self, habit) -> dict | None:
        rows = self._do("SELECT * FROM habits WHERE habit = ?", (habit,))
        return dict(rows[0]) if rows else None

    # ---- runs -----------------------------------------------------------

    def record(self, habit, version, kind, mode, result, turn_ms=None, woke=False, session=None, said=None) -> int:
        """Store one run; mode is shadow, active, spot (an active run checked in shadow) or adhoc. session: the
        conversation a request run answered in, so that "wrong" finds it. said: the user's request (300 characters
        at most, kept 14 days, as a turn is), so that the yes card shows what the habit answered."""
        if mode != "adhoc":
            self.status(habit, version)  # a run of a new version first moves the habit to it
        trace = json.dumps(result.to_dict(), ensure_ascii=False, default=str)
        s1 = ",".join(sorted(set(result.backends)))
        now = self.clock()
        with self.lock, self._db() as db:
            db.execute("UPDATE runs SET said = NULL WHERE said IS NOT NULL AND at < ?", (now - TURN_DAYS * DAY,))
            cur = db.execute(
                "INSERT INTO runs (habit, version, kind, mode, at, ms, turn_ms, status, reason, failed, s1, "
                "planned, trace, woke, outage, session, said) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (habit, version, kind, mode, now, result.ms, turn_ms, result.status, result.reason,
                 1 if result.failed else 0, s1, len(result.planned), trace, 1 if woke else 0,
                 1 if getattr(result, "outage", False) else 0, session or None,
                 str(said)[:MESSAGE_CHARS] if said else None))
            return cur.lastrowid

    def asks(self, habit, version, limit=2) -> list[str]:
        """The latest distinct requests this version answered (14 days at most), the newest first."""
        rows = self._do("SELECT said FROM runs WHERE habit = ? AND version = ? AND said IS NOT NULL ORDER BY id DESC "
                        "LIMIT 50", (habit, version))
        return list(dict.fromkeys(r["said"] for r in rows))[:limit]

    def compared(self, run_id: int, matched: int, judged: int = 0, planned: int | None = None, verdict=None):
        """Store the comparison of a shadow run with the LLM's own calls, or a judge's verdict and its reason."""
        if planned is None:
            self._do("UPDATE runs SET matched = ?, judged = ?, compared = 1 WHERE id = ?", (matched, judged, run_id))
        else:
            self._do("UPDATE runs SET matched = ?, judged = ?, planned = ?, compared = 1, verdict = ? WHERE id = ?",
                     (matched, judged, planned, verdict, run_id))

    def set_turn(self, run_id: int, turn_ms: int, tokens: int | None = None, calls: int | None = None):
        """The whole turn of a request run: its time, and the model tokens and calls it took."""
        self._do("UPDATE runs SET turn_ms = ?, turn_tokens = ?, turn_calls = ? WHERE id = ?",
                 (turn_ms, tokens, calls, run_id))

    def runs(self, habit, limit=5) -> list[dict]:
        rows = self._do("SELECT * FROM runs WHERE habit = ? ORDER BY id DESC LIMIT ?", (habit, limit))
        return [dict(r) for r in rows]

    def last_trace(self, habit) -> dict | None:
        rows = self._do("SELECT trace FROM runs WHERE habit = ? ORDER BY id DESC LIMIT 1", (habit,))
        return json.loads(rows[0]["trace"]) if rows else None

    # ---- scores ---------------------------------------------------------

    def _start(self, habit) -> int:
        """Scores count from the last time the habit (re)entered shadow: runs after that run id."""
        rows = self._do("SELECT start FROM habits WHERE habit = ?", (habit,))
        return (rows[0]["start"] or 0.0) if rows else 0.0

    def _scored(self, habit, version, limit=None, mode=None):
        """Compared runs that count: some planned writes and no answer from the LLM fallback."""
        sql = ("SELECT planned, matched, s1 FROM runs WHERE habit = ? AND version = ? AND compared = 1 "
               "AND planned > 0 AND id > ?")
        current = (self.info(habit) or {}).get("version")
        params = [habit, version, self._start(habit) if current == version else 0]
        if mode:
            sql += " AND mode = ?"
            params.append(mode)
        rows = [r for r in self._do(sql + " ORDER BY id DESC", tuple(params))
                if "llm" not in (r["s1"] or "").split(",")]
        return rows[:limit] if limit else rows

    def agreed(self, habit, version) -> int:
        """Compared runs of this version since its last shadow that agree with the LLM on their own: matched writes
        at least promote_agreement of planned (a judged run: its judge agreed). Unlike the scores, a run whose
        questions the LLM fallback answered counts: the comparison is the judge's, whoever decided."""
        return self.tally(habit, version)[0]

    def tally(self, habit, version) -> tuple[int, int]:
        """(runs that agree, compared runs) over the same runs: what "12 times out of 12" says."""
        current = (self.info(habit) or {}).get("version")
        rows = self._do("SELECT planned, matched FROM runs WHERE habit = ? AND version = ? AND compared = 1 "
                        "AND planned > 0 AND id > ?", (habit, version, self._start(habit) if current == version else 0))
        agree = self.rules.promote_agreement
        return sum(1 for r in rows if min(r["matched"] or 0, r["planned"]) / r["planned"] >= agree), len(rows)

    def agreement(self, habit, version, window=None) -> tuple[float | None, int]:
        """Pooled agreement (matched writes / planned writes) and the number of runs counted."""
        rows = self._scored(habit, version, window)
        planned = sum(r["planned"] for r in rows)
        matched = sum(min(r["matched"] or 0, r["planned"]) for r in rows)
        return (matched / planned if planned else None), len(rows)

    def gain(self, habit, version, column="turn_ms") -> tuple[int | None, int | None]:
        """Median whole turn with the LLM alone (shadow) and with the habit (active): its time by
        default, or its model tokens (turn_tokens) or model calls (turn_calls)."""
        if column not in ("turn_ms", "turn_tokens", "turn_calls"):
            raise ValueError(column)
        current = (self.info(habit) or {}).get("version")
        start = self._start(habit) if current == version else 0

        def median(*modes):
            marks = ", ".join("?" for _ in modes)
            # Only since the last reset: a gain compares turns of the same task, measured the same way.
            rows = self._do(f"SELECT {column} AS v FROM runs WHERE habit = ? AND version = ? AND mode IN ({marks}) "
                            f"AND {column} IS NOT NULL AND id > ? ORDER BY {column}",
                            (habit, version, *modes, start))
            values = [r["v"] for r in rows]
            if not values:
                return None
            middle = len(values) // 2
            return values[middle] if len(values) % 2 else round((values[middle - 1] + values[middle]) / 2)
        return median("shadow", "spot"), median("active")

    def saved(self, habit, version) -> dict:
        """What the active runs saved, against the median turn the LLM took alone: time and tokens."""
        current = (self.info(habit) or {}).get("version")
        start = self._start(habit) if current == version else 0
        rows = self._do("SELECT turn_ms, turn_tokens FROM runs WHERE habit = ? AND version = ? AND mode = 'active' "
                        "AND id > ?", (habit, version, start))
        found = {"runs": len(rows), "ms": None, "tokens": None}
        for column, key in (("turn_ms", "ms"), ("turn_tokens", "tokens")):
            alone, _ = self.gain(habit, version, column)
            done = [r[column] for r in rows if r[column] is not None]
            if alone is not None and done:
                found[key] = max(0, sum(alone - v for v in done))
        return found

    def evaluate(self, habit, version) -> str:
        """Apply the thresholds after a run; return the (possibly new) status. With promote: ask, a shadow habit
        that meets them stays in shadow, ready for the user's yes."""
        status = self.status(habit, version)
        r = self.rules
        if status == "shadow":
            agreement, n = self.agreement(habit, version)
            if n >= r.promote_runs and agreement is not None and agreement >= r.promote_agreement:
                if str(r.promote).strip().lower() != "auto":
                    self._ready(habit, version, f"{agreement:.0%} over {n} runs", n)
                    return "shadow"
                self.set_status(habit, version, "active", f"{agreement:.0%} over {n} runs", by="rules")
                return "active"
        elif status == "active":
            agreement, n = self.agreement(habit, version, r.retire_window)
            if n >= r.retire_window and agreement is not None and agreement < r.retire_agreement:
                self.retire(habit, version, f"{agreement:.0%} over the last {n}")
                return "retired"
            spots = self._scored(habit, version, r.retire_low_spots, mode="spot")
            if len(spots) == r.retire_low_spots and all((s["matched"] or 0) / s["planned"] < r.retire_agreement
                                                        for s in spots):
                self.retire(habit, version, f"{r.retire_low_spots} spot checks in a row under "
                            f"{r.retire_agreement:.0%}")
                return "retired"
            # A run no decision model answered says nothing of the habit: it neither counts nor breaks the row.
            last = self._do("SELECT failed FROM runs WHERE habit = ? AND version = ? AND mode = 'active' "
                            "AND id > ? AND NOT COALESCE(outage, 0) ORDER BY id DESC LIMIT ?",
                            (habit, version, self._start(habit), r.retire_errors))
            if len(last) == r.retire_errors and all(row["failed"] for row in last):
                self.retire(habit, version, f"{r.retire_errors} errors in a row")
                return "retired"
            # A request habit that hands every request back costs more than no habit: measured again in shadow.
            # Failures count toward retirement above; an end, or a spot check that ends, breaks the row.
            woke = 0
            for row in self._do("SELECT mode, status, failed FROM runs WHERE habit = ? AND version = ? AND kind != "
                                "'watch' AND mode IN ('active', 'spot') AND id > ? ORDER BY id DESC LIMIT 20",
                                (habit, version, self._start(habit))):
                if row["status"] == "end":
                    break
                if row["mode"] == "active" and not row["failed"]:
                    woke += 1
                if woke == r.demote_wakes:
                    note = f"woke the model {r.demote_wakes} times in a row"
                    with self._tx() as db:  # back to learning: it needs a new yes, so the user hears of it
                        self._move(db, habit, version, "shadow", note, by="rules")
                        self._tell(db, habit, version, "demoted", note)
                    return "shadow"
        return status

    def _ready(self, habit, version, note, counted):
        """The thresholds are met and the user decides: the habit keeps learning in shadow, ready, with one ready
        row of news, whichever thread or process sees it first. A "later" waits for its own count of runs."""
        with self._tx() as db:
            if db.execute("UPDATE habits SET ready = ? WHERE habit = ? AND version = ? AND status = 'shadow' AND "
                          "ready IS NULL AND COALESCE(wait, 0) <= ?",
                          (self.clock(), habit, version, counted)).rowcount:
                self._tell(db, habit, version, "ready", note)

    def summary(self, habit, version, start_new=True) -> dict:
        if start_new:
            self.status(habit, version)  # a changed graph is a new version, in shadow
        info = self.info(habit) or {}
        agreement, n = self.agreement(habit, version)
        shadow_ms, active_ms = self.gain(habit, version)
        shadow_tokens, active_tokens = self.gain(habit, version, "turn_tokens")
        shadow_calls, active_calls = self.gain(habit, version, "turn_calls")
        total = self._do("SELECT COUNT(*) AS n, SUM(woke) AS woke FROM runs WHERE habit = ? AND version = ?",
                         (habit, version))[0]
        return {"status": info.get("status", "shadow"), "since": info.get("since"), "note": info.get("note", ""),
                "agreement": agreement, "compared": n, "runs": total["n"] or 0, "woke": total["woke"] or 0,
                "shadow_ms": shadow_ms, "active_ms": active_ms, "shadow_tokens": shadow_tokens,
                "active_tokens": active_tokens, "shadow_calls": shadow_calls, "active_calls": active_calls,
                "saved": self.saved(habit, version),
                "origin": info.get("origin") or "asked",
                "ready": info.get("status") == "shadow" and info.get("ready") is not None}

    # ---- memory and turn calls --------------------------------------------

    def memory(self, habit) -> dict:
        return {r["node"]: r["value"] for r in self._do("SELECT node, value FROM memory WHERE habit = ?", (habit,))}

    def remember(self, habit, memory: dict):
        with self.lock, self._db() as db:
            db.executemany("INSERT INTO memory (habit, node, value) VALUES (?, ?, ?) "
                           "ON CONFLICT(habit, node) DO UPDATE SET value = excluded.value",
                           [(habit, k, v) for k, v in memory.items()])

    def failed_checks(self, habit, failed: bool) -> int:
        """A watch's checks in a row that a failed read woke: this one added, or the row ended by one that did not."""
        with self.lock, self._db() as db:
            if failed:
                db.execute("INSERT INTO failures (habit, checks) VALUES (?, 1) "
                           "ON CONFLICT(habit) DO UPDATE SET checks = checks + 1", (habit,))
            else:
                db.execute("DELETE FROM failures WHERE habit = ?", (habit,))
            row = db.execute("SELECT checks FROM failures WHERE habit = ?", (habit,)).fetchone()
        return row["checks"] if row else 0

    def note_call(self, session, tool, args):
        text = json.dumps(args, ensure_ascii=False, default=str)
        if len(text) > 200_000:  # keep it parseable: drop the biggest value instead of cutting the JSON
            text = json.dumps({k: (v if len(str(v)) < 2000 else f"<{len(str(v))} characters>")
                               for k, v in (args or {}).items()}, ensure_ascii=False, default=str)
        self._do("INSERT INTO calls (session, at, tool, args) VALUES (?, ?, ?, ?)", (session, self.clock(), tool, text))

    def take_calls(self, session) -> list[dict]:
        """The calls noted for a session since the last take, then forget them."""
        with self.lock, self._db() as db:
            rows = db.execute("SELECT tool, args FROM calls WHERE session = ? ORDER BY at", (session,)).fetchall()
            db.execute("DELETE FROM calls WHERE session = ? OR at < ?", (session, self.clock() - 86400))
        calls = []
        for r in rows:
            try:
                args = json.loads(r["args"])
            except ValueError:
                args = {}
            calls.append({"tool": r["tool"], "args": args})
        return calls

