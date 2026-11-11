"""Watches: a Hermes cron job per watch, whose script runs `hermes habits tick <name>`, and that check.

A check answers Hermes's wakeAgent gate on its last line: silent, or a wake with what the watch found.
"""

from __future__ import annotations

import json
import logging
import random
import shlex
import shutil
import time
from pathlib import Path

from . import catalog, graph, runner, texts
from .display import _load_tools, _no_media
from .tools import _json, _untrusted, read_outside, saved_graph, tools_of

logger = logging.getLogger(__name__)

GATE_SILENT = json.dumps({"wakeAgent": False})
GATE_WAKE = json.dumps({"wakeAgent": True})
WATCH_JUDGE_SHADOW = 0.1  # share of silent checks the LLM judges while the habit is learning
WATCH_JUDGE_ACTIVE = 0.05  # share of silent checks the LLM judges once it is automatic
FAILED_CHECKS = 3  # checks in a row a failed read must wake before the model hears of it: a flaky read wakes no one
HOLD_MINUTES = 360  # only a watch that checks more often holds them: 3 silent days is too long for a daily one

WATCH_PROMPT = (
    "A Habits watch woke you. The script output above says what the watch found and what is left to do. "
    "Do what is left, or tell the user in two sentences at most what changed. If the watch stopped because of "
    "its own steps (a page that moved, a case it does not know), propose a repair to the user: what is wrong and "
    "what you would change in its reflex.yml. Save it with habit_save under the same name only once the user agrees."
)


class Watch:
    """The watches of Habits: their cron jobs and scripts, and each check."""

    def schedule(self, habit: catalog.Habit) -> dict:
        """Create (or keep) the Hermes cron job whose script runs `hermes habits tick <name>`."""
        hermes_home = self.home().parent.parent
        scripts = hermes_home / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        script = scripts / f"habits-{habit.name}.sh"
        binary = shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
        script.write_text(
            f"#!/bin/bash\n# Written by the Habits plugin for the watch \"{habit.name}\". "
            f"Remove with: hermes habits rest {habit.name}\n"
            "# A failure (or the plugin removed) prints a silent gate and exits 0: it never wakes the model.\n"
            f"out=$({shlex.quote(binary)} habits tick {shlex.quote(habit.name)} 2>/dev/null) "
            "|| out='{\"wakeAgent\": false}'\n"
            "printf '%s\\n' \"$out\"\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        job_name = f"habits-{habit.name}"
        job = {"schedule": cron_schedule(habit.graph["trigger"]), "script": script.name, "prompt": WATCH_PROMPT,
               "skills": [habit.name]}
        if habit.graph["trigger"].get("at"):  # a check at a time of day, hours late, is wrong: a missed one is skipped
            job["catch_up"] = False
        listed = _json(self.ctx.dispatch_tool("cronjob_manage", {"action": "list"}))
        same = [j.get("id") or j.get("job_id") for j in listed.get("jobs", []) or []
                if isinstance(j, dict) and j.get("name") == job_name]
        # A saved change (a new interval) updates the job in place: what the user set by hand (--deliver telegram,
        # --model) stays. A second job of that name, left by an older save, goes.
        for extra in same[1:]:
            self.ctx.dispatch_tool("cronjob_manage", {"action": "remove", "job_id": extra})
        if same:
            reply = _json(self.ctx.dispatch_tool("cronjob_manage", {"action": "update", "job_id": same[0], **job}))
        else:
            reply = _json(self.ctx.dispatch_tool("cronjob_manage", {"action": "create", "name": job_name,
                                                                    "deliver": "origin", **job}))
        if not reply.get("success"):
            return {"error": f"cron job not {'updated' if same else 'created'}: {reply.get('error') or reply}",
                    "script": str(script)}
        return {"job_id": reply.get("job_id") or (same[0] if same else None),
                "schedule": reply.get("schedule") or job["schedule"], "script": str(script)}

    def unschedule(self, name: str) -> str:
        removed = []
        listed = _json(self.ctx.dispatch_tool("cronjob_manage", {"action": "list"}))
        for job in listed.get("jobs", []) or []:
            if isinstance(job, dict) and job.get("name") == f"habits-{name}":
                job_id = job.get("id") or job.get("job_id")
                self.ctx.dispatch_tool("cronjob_manage", {"action": "remove", "job_id": job_id})
                removed.append(str(job_id))
        script = self.home().parent.parent / "scripts" / f"habits-{name}.sh"
        if script.exists():
            script.unlink()  # our own one-line script, never user data
            removed.append(script.name)
        return ", ".join(removed)

    def tick(self, name: str) -> list[str]:
        """One check of a watch; the last line is the wakeAgent gate. A wake that a failed read caused (a read that
        timed out, or an each read whose failed calls left the only doubts) waits: only the FAILED_CHECKS-th such
        check in a row wakes, once. A check that worked starts over; an empty read that check: changed set aside
        leaves the row as it is."""
        habit = catalog.find(name)
        if habit is None or not habit.valid or habit.kind != "watch":
            return [f"habits: no valid scheduled habit named {name!r}", GATE_SILENT]
        status = self.ledger.status(habit.name, habit.version)
        if status not in ("shadow", "active") or self.paused():  # resting, or a state an older version wrote
            return [GATE_SILENT]
        active = status == "active"
        with self.held(habit.name) as free:
            if not free:
                return ["habits: the previous check is still running", GATE_SILENT]
            result = self.run(habit, habit.graph, dry=not active,
                              trigger=f"watch check {time.strftime('%Y-%m-%d %H:%M')}")
        self.used(habit)
        # Only a read waits: a write or a message that failed is news, and the next check may not try it again.
        nodes = habit.graph["nodes"]
        failed = result.step_failed and runner._reads(nodes[result.steps[-1]["node"]])
        # An end that woke only for the doubts of read calls that failed is the same source down, item by item.
        doubted = not failed and _failed_reads_only(nodes, result)
        failed = failed or doubted
        # An empty or partly failed read tells neither news nor a failure: a source down in turns (a timeout,
        # then an error page grep finds nothing in) still reaches the row's end.
        holds = minutes(habit.graph["trigger"]["every"]) < HOLD_MINUTES
        row = self.ledger.failed_checks(habit.name, failed) if holds and (failed or not result.kept) else 0
        held = holds and failed and row != FAILED_CHECKS
        would_wake = not held and (result.status == "wake" or bool(result.planned))
        # In shadow the habit only read and planned: when it would act, Hermes is woken to do the task
        # itself, as it would with no habit, and the decision is judged.
        woke = (not held and result.status == "wake") if active else would_wake
        run_id = self.ledger.record(habit.name, habit.version, "watch", "active" if active else "shadow", result,
                                    woke=woke)
        share = WATCH_JUDGE_ACTIVE if active else WATCH_JUDGE_SHADOW
        judged = not failed and ((not active and would_wake) or (not would_wake and random.random() < share))
        if judged and not result.failed:  # a check that broke is an error, not a decision to judge
            right = self.judge_watch(habit, result)
            if right is not None:
                self.ledger.compared(run_id, 1 if right else 0, 1, planned=1)
        if active or judged:
            status = self.ledger.evaluate(habit.name, habit.version)  # promotion, or retirement after 3 errors in a row
        retired = status == "retired"  # by this check: a retired watch checks nothing
        if not woke and not retired:
            return [GATE_SILENT]
        # Its own retirement goes first: a retired watch never wakes again, so a silent check that retires it still
        # wakes, once, to say so (unless the failed-read line below says it). Other habits' news waits for a turn
        # of the user's: this wake goes to a cron job's origin, and its reply may be [SILENT].
        said_below = retired and failed and holds and woke
        try:  # its own line, as the user reads it, in the language setting
            rows = self.ledger.news([habit.name], ("retired",))
        except Exception as exc:
            logger.warning("habits: could not read the retirement to tell: %s", exc)
            rows = []
        told = [texts.MODEL_TELL.format(line=self.news_line(row)) for row in rows if not said_below]
        if not woke:
            return told + ["Nothing else is left to do.", GATE_WAKE] if told else [GATE_SILENT]
        found = [_no_media(result.message), "", _no_media(result.report())]
        if result.values:
            found += ["", "values: " + json.dumps(result.values, ensure_ascii=False, default=str)[:6000]]
        wrap = read_outside(result)  # what it read from the web or a server reaches the model as data
        lines = ([f'Habits watch "{habit.name}" woke; what it found:', _untrusted("\n".join(found))]
                 if wrap else [f'Habits watch "{habit.name}": {found[0]}'] + found[1:])
        if failed and holds:  # the earlier failures of the row were held
            then = "the next ones will not until a check works"
            if status == "retired":  # no error exit: this failure was its 3rd error in a row, and no check wakes now
                then = (f"it is now resting ({(self.ledger.info(habit.name) or {}).get('note')}): the user brings "
                        f"it back with /habits try {habit.name}")
            step = result.doubts[0]["node"] if doubted else result.steps[-1]["node"]
            head = (f'had calls of its step "{step}" fail {FAILED_CHECKS} checks in a row' if doubted
                    else f'failed {FAILED_CHECKS} checks in a row, at its step "{step}"')
            lines.insert(0, f'Habits watch "{habit.name}" {head}: the earlier ones woke no one, and {then}.')
        lines = told + lines
        lines += ["", f"Its reflex: {habit.path}"]  # cron gives the skill's text, not its folder
        # A cron job saved before repairs were proposed still says "repair it": every wake says otherwise.
        lines.append("Do not call habit_save for this watch before the user agrees to the change.")
        if not active:
            lines += ["", "The watch is learning: it only read, and changed nothing. Do what is left yourself."]
            if result.planned:
                # Apart from what it read, one JSON object per line (newlines escaped): a page cannot add a call.
                calls = "\n".join(json.dumps(p, ensure_ascii=False, default=str)[:300] for p in result.planned[:10])
                lines += ["It planned these calls, one JSON object per line; make them yourself if they are right:",
                          _untrusted(calls) if wrap else calls]
        return lines + [GATE_WAKE]

    def judge_watch(self, habit: catalog.Habit, result: runner.Result):
        decision = "wake the user" if result.status == "wake" else "stay silent"
        planned = "\n".join(json.dumps(p, ensure_ascii=False)[:300] for p in result.planned[:10]) or "none"
        text = (f"Watch: {habit.name} - {habit.description}\nDecision: {decision}\nMessage: {result.message}\n"
                f"Planned actions:\n{planned}\n\nRun report:\n{result.report()}")
        try:
            reply = self.complete("A watch checked something on a schedule and made a decision. Judge from the report "
                                  "whether the decision (and its planned actions) is what a careful assistant would do.",
                                  text[:12000], {"type": "object", "properties": {"right": {"type": "boolean"}},
                                                 "required": ["right"]})
            return bool(reply.get("right"))
        except Exception as exc:
            logger.warning("habits: watch judge failed: %s", exc)
            return None

    def cli_tick(self, name: str) -> int:
        """The cron script's command: whatever happens, the last line is a gate and the exit code 0."""
        try:
            graph_of = saved_graph(name)  # its own tools alone: every check pays for them
            _load_tools(tools_of(graph_of, saved_graph) if graph_of else None)
            print("\n".join(self.tick(name)))
        except Exception as exc:
            logger.warning("habits: tick of %s failed: %s", name, exc, exc_info=True)
            print(f"habits: the check of {name} failed: {type(exc).__name__}")
            print(GATE_SILENT)
        return 0

    def unschedule_all(self) -> str:
        names = set()
        listed = _json(self.ctx.dispatch_tool("cronjob_manage", {"action": "list"}))
        for job in listed.get("jobs", []) or []:
            if isinstance(job, dict) and str(job.get("name", "")).startswith("habits-"):
                names.add(str(job["name"])[len("habits-"):])
        scripts = self.home().parent.parent / "scripts"
        names |= {p.stem[len("habits-"):] for p in scripts.glob("habits-*.sh")} if scripts.is_dir() else set()
        return ", ".join(filter(None, (self.unschedule(n) for n in sorted(names))))


def minutes(every: str) -> int:
    """A watch's interval in minutes: 5m, 2h, 1d."""
    return int(every[:-1]) * {"m": 1, "h": 60, "d": 1440}[every[-1]]


def cron_schedule(trigger: dict) -> str:
    """The schedule Hermes's cronjob_manage takes: "every 30m", or for a time of day (every: 1d alone) a cron
    expression ("0 21 * * *")."""
    every, at = trigger["every"], trigger.get("at")
    if not at:
        return f"every {every}"
    hour, minute = (int(part) for part in at.split(":"))
    return f"{minute} {hour} * * *"


def _failed_reads_only(nodes: dict, result: runner.Result) -> bool:
    """It woke only for doubts that read calls left when they ran and failed: an end, or the wake a check: {empty:
    doubts} sent it to right then (the graph's own hand-back of doubts), nothing planned, and every doubt a failed
    call of a do that reads. A value refused before any call (- lait, a path with ..) fails the same way at every
    check, and a decision under its threshold or an invalid llm answer is news: they wake at once."""
    if result.status != "wake" or not result.doubts or result.planned:
        return False
    if graph.kind_of(nodes.get(result.node)) != "end" and not _doubts_wake(nodes, result):
        return False
    failed = {tuple(call) for call in result.failed_calls}  # calls of this graph's do nodes only, never an ask's
    return all((d.get("node"), runner._key(d.get("item"))) in failed and runner._reads(nodes[d["node"]])
               for d in result.doubts)


def _doubts_wake(nodes: dict, result: runner.Result) -> bool:
    last = nodes.get(result.steps[-1].get("node")) if result.steps else None
    return (graph.kind_of(nodes.get(result.node)) == "wake" and isinstance(last, dict)
            and last.get("check") == {"empty": "doubts"} and (last.get("then") or {}).get("no") == result.node)
