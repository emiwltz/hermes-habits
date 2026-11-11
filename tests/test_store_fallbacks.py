"""Risk 6.2 of the plan: a Hermes whose listing refuses an option still gets read, step by step: planned listing,
whole rows (compact_rows off), the order of started_at, Hermes's own defaults. A locked store is not retried."""

import sqlite3
import time

from dream_cases import OK, call, row, tool, user
from habits import dream

DAY = 86400


class Picky:
    """A store that raises for the listings it does not accept (by option), and records each try."""

    def __init__(self, refuses, error=ValueError("not like this"), rows=None, messages=None):
        self.refuses, self.error = refuses, error
        self.rows, self.messages = rows or [], messages or {}
        self.tries = []

    def list_sessions_rich(self, **kw):
        self.tries.append({k: v for k, v in kw.items() if k not in ("limit", "offset")})
        if self.refuses(kw):
            raise self.error
        return self.rows[kw.get("offset", 0):kw.get("offset", 0) + kw.get("limit", 20)]

    def get_messages(self, session_id):
        return self.messages.get(session_id, [])


def one_session(days=3):
    at = time.time() - days * DAY
    messages = [user(at), call(at + 1, "terminal", {"command": "df -h ~"}, "c1"), tool(at + 2, "c1", OK)]
    return [row("s1", at + 2, started=at)], {"s1": messages}


def read(store, **kw):
    return dream.read_turns(store, time.time(), **kw)


def test_planned_listing_first():
    rows, messages = one_session()
    store = Picky(lambda kw: False, rows=rows, messages=messages)
    assert read(store).status == "ok" and len(store.tries) == 1 and store.tries[0]["compact_rows"] is True


def test_whole_rows_when_compact_rows_is_refused():
    rows, messages = one_session()
    store = Picky(lambda kw: kw.get("compact_rows"), rows=rows, messages=messages)
    r = read(store)
    assert (r.status, len(r.turns)) == ("ok", 1)
    assert store.tries[-1]["compact_rows"] is False and store.tries[-1]["order_by_last_active"] is True


def test_started_order_when_the_chain_order_is_refused():
    rows, messages = one_session()
    store = Picky(lambda kw: kw.get("order_by_last_active"), rows=rows, messages=messages)
    r = read(store)
    assert (r.status, len(r.turns)) == ("ok", 1) and len(store.tries) == 3
    assert store.tries[-1]["order_by_last_active"] is False


def test_started_order_keeps_a_row_a_month_older_than_the_window():
    # a chained session started 70 days ago, active today, sits after 60-day-old rows in started order
    at = time.time() - 1 * DAY
    old = [row(f"o{i}", time.time() - 80 * DAY, started=time.time() - 80 * DAY) for i in range(3)]
    chained = row("chain", at, started=time.time() - 70 * DAY)
    store = Picky(lambda kw: kw.get("order_by_last_active"), rows=[chained] + old,
                  messages={"chain": [user(at), call(at + 1, "terminal", {"command": "ls"}, "c"), tool(at + 2, "c", OK)]})
    assert len(read(store).turns) == 1


def test_defaults_last():
    rows, messages = one_session()
    store = Picky(lambda kw: bool(set(kw) - {"limit", "offset"}), rows=rows, messages=messages)
    r = read(store)
    assert (r.status, len(r.turns)) == ("ok", 1) and store.tries[-1] == {} and len(store.tries) == 4


def test_every_listing_refused_says_why_with_the_first_error():
    store = Picky(lambda kw: True, error=KeyError("x"))
    r = read(store)
    assert (r.status, r.why) == ("unread", "KeyError") and len(store.tries) == 4


def test_a_locked_store_is_asked_again_a_few_times_then_given_up_without_climbing_the_ladder():
    store = Picky(lambda kw: True, error=sqlite3.OperationalError("database is locked"))
    r = read(store, pause=0)
    assert (r.status, r.why) == ("unread", "OperationalError") and len(store.tries) == 3 and len(
        {repr(sorted(kw.items())) for kw in store.tries}) == 1  # the same listing each time: a lock is not a missing option
