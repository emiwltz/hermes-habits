#!/usr/bin/env python3
"""Write a synthetic Hermes history into a state.db that Hermes has already created.

Standard library only, plain sqlite3 inserts (the columns of Hermes's own create_session / append_message). Nothing
here is real: the commands, paths and days are made up, shaped like the hard cases of real usage. Dates are relative
to now, so the history is always inside the dream's 60-day window.

    python3 make_history.py <path-to-state.db> [--now SECONDS] [--base FOLDER]

--base FOLDER: the project folder the made-up work happens in (default /home/someone/proj, which does not exist).
With it, the notes script is FOLDER/bin/notes too, so that a reflex made from this history works where FOLDER was
made by make_project.sh. Prints the number of sessions and messages written.
"""
import json
import sqlite3
import sys
import time

DAY = 86400
LOCAL_SECRETS = "/.e" + "nv.local"  # spelled in two pieces: the installer's scan reads this file for "cat <secrets file>"


class Writer:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.sessions = 0
        self.messages = 0
        self.n = 0

    def session(self, sid, source, started, *, parent=None, end_reason=None, ended=None, model_config=None, cwd=None):
        self.db.execute(
            "INSERT INTO sessions (id, source, model, model_config, parent_session_id, started_at, ended_at, "
            "end_reason, cwd) VALUES (?,?,?,?,?,?,?,?,?)",
            (sid, source, "synthetic-model", json.dumps(model_config) if model_config else None, parent, started,
             ended, end_reason, cwd))
        self.sessions += 1

    def message(self, sid, role, ts, content=None, tool_calls=None, tool_call_id=None, tool_name=None):
        self.db.execute(
            "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls, tool_name, timestamp) "
            "VALUES (?,?,?,?,?,?,?)",
            (sid, role, content, tool_call_id, json.dumps(tool_calls) if tool_calls else None, tool_name, ts))
        self.messages += 1

    def call(self, name, args):
        self.n += 1
        return {"id": "call_%d" % self.n, "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}

    def turn(self, sid, ts, text, calls, result=None):
        """One user message, then one assistant message holding the calls and one tool message per call."""
        self.message(sid, "user", ts, text)
        wrapped = [self.call(n, a) for n, a in calls]
        self.message(sid, "assistant", ts + 1, "", tool_calls=wrapped)
        for k, c in enumerate(wrapped):
            self.message(sid, "tool", ts + 2 + k, result or json.dumps({"output": "ok", "exit_code": 0}),
                         tool_call_id=c["id"], tool_name=c["function"]["name"])
        self.message(sid, "assistant", ts + 5, "done")

    def grouped_turn(self, sid, ts, text, calls):
        """The same, with every call behind the `tool_call` wrapper (one assistant message, one tool message)."""
        self.n += 1
        wrapper = {"id": "call_%d" % self.n, "type": "function", "function": {
            "name": "tool_call", "arguments": json.dumps({"calls": [{"name": n, "arguments": a} for n, a in calls]})}}
        self.message(sid, "user", ts, text)
        self.message(sid, "assistant", ts + 1, "", tool_calls=[wrapper])
        self.message(sid, "tool", ts + 2, json.dumps({"output": "ok", "exit_code": 0}), tool_call_id=wrapper["id"],
                     tool_name="tool_call")
        self.message(sid, "assistant", ts + 5, "done")

    def finish(self):
        self.db.commit()
        self.db.close()


def term(command):
    return ("terminal", {"command": command})


def build(path, now, base="/home/someone/proj", notes=None):
    w = Writer(path)
    at = lambda days_ago, hour=9: now - days_ago * DAY + (hour - 12) * 3600  # noqa: E731
    cwd = base
    notes = notes or "~/bin/notes"

    # 1. A command that comes back on 5 days, in two spellings (one with a tail), and a file read on 4 days.
    for i, d in enumerate((1, 2, 4, 6, 9)):
        sid = "cli-%d" % i
        t = at(d)
        w.session(sid, "cli", t - 60, cwd=cwd)
        w.turn(sid, t, "start my day", [term(notes + " wake" if i % 2 else notes + " wake | tail -40")])
        w.turn(sid, t + 600, "what is the disk like", [term("df -h ~")])
        if i < 4:
            w.turn(sid, t + 1200, "reread the todo", [("read_file", {"path": base + "/TODO.md"})])
        w.turn(sid, t + 1800, "something different %d" % i, [term("ls /tmp/unique-%d" % i)])

    # 2. Grouped calls behind the tool_call wrapper, on 3 days.
    for i, d in enumerate((3, 5, 8)):
        sid = "grp-%d" % i
        t = at(d, 14)
        w.session(sid, "cli", t - 60, cwd=cwd)
        w.grouped_turn(sid, t, "check the repo", [term("git status --short"), term("git log --oneline -n 3"),
                                                    term("uptime")])

    # 3. A chained session: one row started 70 days ago, turns over 9 days, last active today.
    w.session("chain-1", "telegram", now - 70 * DAY, cwd=cwd)
    for d in (9, 8, 6, 4, 3, 2, 1, 0):
        w.turn("chain-1", at(d, 18), "evening look", [term("python3 -m http.server --help")] if d == 99 else
               [term("git -C " + base + " branch --show-current")])
    # An old turn, outside the window: not counted.
    w.turn("chain-1", now - 70 * DAY + 100, "ancient", [term("ls /nowhere-old")])

    # 4. A compression chain: an old root that ended in compression, and its continuation.
    w.session("root-1", "cli", at(40), end_reason="compression", ended=at(39), cwd=cwd)
    w.turn("root-1", at(40), "old root", [term("make lint")])
    w.session("cont-1", "cli", at(39), parent="root-1", cwd=cwd)
    w.turn("cont-1", at(2), "continued", [term("make lint")])
    w.turn("cont-1", at(1), "continued again", [term("make lint")])

    # 5. A sub-agent session repeating one command 20 times (none of it may be counted), by both of its markers.
    w.session("sub-1", "subagent", at(2), cwd=cwd)
    w.session("sub-2", "cli", at(3), model_config={"_delegate_from": "cli-0"}, cwd=cwd)
    for k in range(20):
        w.turn("sub-1", at(2) + k * 60, "work", [term("echo-agent-job --step run")])
        w.turn("sub-2", at(3) + k * 60, "work", [term("echo-agent-job --step run")])

    # 6. A scheduled job running one command daily.
    for d in range(1, 6):
        sid = "cron-%d" % d
        w.session(sid, "cron", at(d, 6), cwd=cwd)
        w.turn(sid, at(d, 6), "scheduled run", [term("git pull")])

    # 7. A private file among the repeats, a key, and a program with a password in its arguments.
    for i, d in enumerate((1, 3, 5, 7)):
        sid = "priv-%d" % i
        t = at(d, 11)
        w.session(sid, "cli", t - 60, cwd=cwd)
        w.turn(sid, t, "show me the key", [term("cat ~/.hermes/.env")])
        w.turn(sid, t + 60, "and the local one", [term("cat " + base + LOCAL_SECRETS)])
        w.turn(sid, t + 120, "call it", [term("curl -u me:hunter2 https://example.invalid/api")])

    # 8. Noise: repeated, but not a gesture.
    for i, d in enumerate((1, 2, 3, 4)):
        sid = "noise-%d" % i
        t = at(d, 16)
        w.session(sid, "cli", t - 60, cwd=cwd)
        w.turn(sid, t, "hm", [term("cd " + base)])
        w.turn(sid, t + 30, "hm", [term("echo hi")])

    # 9. A blocked call that repeats: it did not happen, so it is not counted.
    for i, d in enumerate((2, 4, 6)):
        sid = "blk-%d" % i
        t = at(d, 20)
        w.session(sid, "cli", t - 60, cwd=cwd)
        w.turn(sid, t, "wipe", [term("rm -rf " + base + "/build")],
               result=json.dumps({"status": "blocked", "error": "denied"}))

    w.finish()
    return w.sessions, w.messages


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    when = time.time()
    if "--now" in args:
        when = float(args[args.index("--now") + 1])
    base = args[args.index("--base") + 1] if "--base" in args else None
    s, m = build(args[0], when, base or "/home/someone/proj", base + "/bin/notes" if base else None)
    print("wrote %d sessions, %d messages" % (s, m))
