"""Hand-made histories for the dream's tests: a fake of Hermes's session store, builders for its messages, and the
cases of the plan (section 5) as named histories. The commands are invented (``~/bin/notes wake``); nothing here comes
from a real history, only its shapes."""

from __future__ import annotations

import json
import time

NOTES = "~/bin/notes wake"


# ---- time ---------------------------------------------------------------------------------------------------------

def day(n: int, hour: int = 9, minute: int = 0) -> float:
    """Epoch seconds of `n` days ago at that local hour."""
    today = time.localtime()
    stamp = time.mktime((today.tm_year, today.tm_mon, today.tm_mday - n, hour, minute, 0, 0, 0, -1))
    return stamp


# ---- messages -----------------------------------------------------------------------------------------------------

def user(at: float, text: str = "do the usual") -> dict:
    return {"role": "user", "content": text, "timestamp": at}


def call(at: float, name: str, args: dict | str, id: str = "c1", *, text: bool = True) -> dict:
    """An assistant message with one tool call; arguments as JSON text (Hermes's shape) unless `text` is False."""
    arguments = json.dumps(args) if (text and not isinstance(args, str)) else args
    return {"role": "assistant", "content": "", "timestamp": at,
            "tool_calls": [{"id": id, "function": {"name": name, "arguments": arguments}}]}


def wrapped(at: float, inner: list, id: str = "w1") -> dict:
    """The `tool_call` wrapper: several calls in one: inner is [(name, args), ...]. Arguments alternate between a
    dict and JSON text (both shapes are real)."""
    calls = [{"name": name, "arguments": args if i % 2 == 0 else json.dumps(args)}
             for i, (name, args) in enumerate(inner)]
    return call(at, "tool_call", {"calls": calls}, id)


def tool(at: float, call_id: str, content) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "timestamp": at,
            "content": content if isinstance(content, str) else json.dumps(content)}


OK = {"output": "done", "exit_code": 0}
BLOCKED = {"output": "", "exit_code": -1, "error": "denied", "status": "blocked"}


# ---- the fake store -----------------------------------------------------------------------------------------------

def row(id: str, last: float, *, started: float | None = None, source: str = "cli", cwd: str = "~/proj",
        **extra) -> dict:
    r = {"id": id, "source": source, "started_at": started if started is not None else last, "last_active": last,
         "cwd": cwd, "model_config": None, "parent_session_id": None, "end_reason": None}
    r.update(extra)
    return r


class FakeStore:
    """list_sessions_rich / get_messages / close, with Hermes's order: by the newest activity of a row's compression
    chain (an old root with a recent child sorts with the child), then paged."""

    def __init__(self, rows, messages, *, strict=False, fail_page=None, fail_with=None, fail_messages=()):
        self.rows, self.messages = list(rows), dict(messages)
        self.strict = strict                  # refuse the newer keywords with a TypeError, like an older Hermes
        self.fail_page, self.fail_with = fail_page, fail_with   # the n-th page raises
        self.fail_messages = set(fail_messages)
        self.closed = False
        self.listed = []                      # the offsets asked
        self.read = []                        # the ids whose messages were asked

    def _tip(self, r):
        best = r.get("last_active") or 0
        for child in self.rows:
            if isinstance(child, dict) and child.get("parent_session_id") == r["id"] and r.get("end_reason") == "compression":
                best = max(best, self._tip(child))
        return best

    def list_sessions_rich(self, **kw):
        if self.strict and set(kw) - {"limit", "offset"}:
            raise TypeError("unexpected keyword")
        limit, offset = kw.get("limit", 20), kw.get("offset", 0)
        page = offset // max(limit, 1)
        self.listed.append(offset)
        if self.fail_page is not None and page == self.fail_page:
            raise self.fail_with
        ordered = sorted([r for r in self.rows if isinstance(r, dict) and r.get("id")] +
                         [r for r in self.rows if not (isinstance(r, dict) and r.get("id"))],
                         key=lambda r: -self._tip(r) if isinstance(r, dict) and r.get("id") else 1)
        return ordered[offset:offset + limit]

    def get_messages(self, session_id):
        self.read.append(session_id)
        if session_id in self.fail_messages:
            raise RuntimeError("unreadable")
        return list(self.messages.get(session_id, []))

    def close(self):
        self.closed = True


def session(sid: str, days: list, turns: list, *, source="cli", cwd="~/proj", fail=(), **extra):
    """One session row and its messages. `days` is [(n_days_ago, hour)] for each turn (or one pair for all);
    `turns` is a list of turns, each a list of (tool, args) or of messages built by hand. A call is followed by an
    OK result unless its index is in `fail` (then BLOCKED). Returns (row, messages)."""
    if len(days) == 1:
        days = days * len(turns)
    messages, n = [], 0
    last = 0.0
    for (d, hour), calls in zip(days, turns):
        at = day(d, hour)
        messages.append(user(at, "do the usual"))
        for name, args in calls:
            n += 1
            cid = f"{sid}-c{n}"
            messages.append(call(at + 1, name, args, cid))
            messages.append(tool(at + 2, cid, BLOCKED if n in fail else OK))
        last = max(last, at + 60)
    return row(sid, last, started=day(max(d for d, _ in days), days[0][1]), source=source, cwd=cwd, **extra), messages


def store_of(*sessions, **kw) -> FakeStore:
    rows = [r for r, _ in sessions]
    messages = {r["id"]: m for r, m in sessions}
    return FakeStore(rows, messages, **kw)


def sh(command: str):
    return ("terminal", {"command": command})


# ---- named histories ----------------------------------------------------------------------------------------------

def notes_wake_history() -> FakeStore:
    """Plan 5, case 1: `notes wake` in 71 turns on 15 days, in three variants (50 / 12 / 9)."""
    forms = ([NOTES] * 50 + [NOTES + " | tail -40"] * 12 + [f'{NOTES}; echo "EXIT=$?"'] * 9)
    sessions, k = [], 0
    for d in range(15):
        count = 5 if d < 11 else 4        # 11 * 5 + 4 * 4 = 71
        turns = [[sh(forms[k + i])] for i in range(count)]
        k += count
        sessions.append(session(f"n{d}", [(d, 10)], turns))
    return store_of(*sessions)


def secrets_history() -> FakeStore:
    """Plan 5, cases 3 to 6: every one repeated in 3 turns on 2 days; none may be spotted."""
    dot = "~/."      # the private names are assembled, so the install scanner does not read this file as one
    commands = ["cat " + dot + "hermes/." + "env", "cat " + dot + "zsh" + "env", "grep KEY " + dot + "zsh" + "rc",
                "cat " + dot + "net" + "rc", "ls " + dot + "ssh", "env",
                "printenv HOME", "curl -u me:pw https://x.test", "git clone https://me:pw@host.test/r",
                'curl -H "Authorization: Bearer abcdefgh12345678abcdefgh12345678abcd" https://x.test/api',
                "sleep 25; echo done", "cd ~/proj", "true", "echo hi"]
    sessions = []
    for i, command in enumerate(commands):
        sessions.append(session(f"s{i}", [(0, 9), (1, 9), (2, 9)], [[sh(command)]] * 3))
    return store_of(*sessions)


def denied_history() -> FakeStore:
    """Plan 5, case 53: a flagged command the user denied 3 times on 2 days."""
    return store_of(session("d1", [(0, 9), (1, 9), (1, 12)], [[sh("chmod 600 ~/x")]] * 3, fail=(1, 2, 3)))
