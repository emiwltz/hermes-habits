"""The dream, wired into the plugin: when it runs, what it keeps, what /habits and `hermes habits dream` show.

The pure part (reading Hermes's history, spotting, drafting) is `dream.py`; the ledger holds the drafts
(`ledger.py`). This mix-in is the glue: it takes the lock so one process alone dreams, applies the caps, writes the
drafts, and says it all in the user's language. It calls no model and never raises into a hook.

The practice side (comparing a draft beside Hermes, the yes) is `practice.py`.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import catalog, graph, runner
from .tools import LEARNING, read_outside, relay
from .texts import percent, say, seconds
from .trigger import MACHINERY, reads_all  # cron and curator turns are no user's: no dream starts after them

if TYPE_CHECKING:  # the class these methods are mixed into
    from .plugin import Habits

logger = logging.getLogger(__name__)

EVERY = 20 * 3600       # a daily dream starts when the last good one is older than this
CHECK = 600             # a process looks at the ledger at most this often (3.9)
MAX_PRACTISING = 3
MAX_OPEN = 3            # change and rest proposals open at once (3.10)
NO_DAYS = 30
ACTION_CHARS = 100      # an action in a line of the dream's report; a card shows it uncut
SHOWN_MOMENTS = 2
STALE_DAYS = 30         # a draft that practices but whose action does not come back for this long rests (25)
SKILL_SECONDS = 10      # a reflex started by its skill's load answers inside the tool call: 10 s at most
SKILL_SEEN = 500        # (session, habit) pairs kept for "once per load"
# What the model reads when a reflex ran as its skill loaded. state: "automatic" or "a test run, it is
# learning"; outcome: "done" or "handed back".
SKILL_START = "The reflex {name} ran when this skill loaded ({state}): {outcome}. {message}"
ON_LOAD: contextvars.ContextVar[bool] = contextvars.ContextVar("habits_on_skill_load", default=False)


def _dream():
    """The dream's pure core (dream.py), imported when a dream or a line of /habits needs it."""
    from . import dream
    return dream


def clip(text: str, chars: int = ACTION_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= chars else text[:chars - 1] + "…"


def _json(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return default if value is None else value


def _row_action(row: dict) -> str:
    """A draft row's gesture as one line: the command of a terminal step, else the tool and its arguments."""
    args = _json(row.get("args"), {})
    args = args if isinstance(args, dict) else {}
    tool = row.get("tool") or ""
    text = _dream().brief(_dream().Action(str(row.get("key") or ""), tool, args, ""))  # an act is shown short
    parts = _json(row.get("parts"), [])
    return clip(text) + (f" +{len(parts) - 1}" if isinstance(parts, list) and len(parts) > 1 else "")


class Dream:
    """Mixed into Habits: dream_now, maybe_dream, the lines /habits shows, the paths a dream never reads."""

    def _learning(self: Habits) -> str:
        return str(self.setting("learning"))

    def _ready_matches(self: Habits) -> int:
        return int(self.rules().ready_matches)

    # ---- what a dream never reads -------------------------------------------------------------------------

    def blocked(self: Habits, path: str) -> str:
        """Why a path is never a candidate (a secrets or config file Hermes's own tools may not read), or ""."""
        from .tools import _kept_from_tools
        try:
            return _kept_from_tools(Path(str(path)).expanduser()) or ("a secrets file" if runner.secret_name(path) else "")
        except Exception:
            return "unreadable path"

    # ---- one dream ----------------------------------------------------------------------------------------

    def dream_now(self: Habits, manual: bool = False, days: int | None = None, home=None,
                  look: bool | None = None) -> dict:
        """One dream. Returns the summary `hermes habits dream --json` prints. Never raises. `home`: the
        folder of another Hermes whose history is read (read only). A look (by default: a window named with --days, or
        another home) shows what THAT reading finds and writes nothing in the ledger: only the plain dream, and the
        daily one, write drafts."""
        began = time.monotonic()
        look = (home is not None or days is not None) if look is None else look
        out = {"status": "off", "turns": 0, "sessions": 0, "days": 0, "took_ms": 0, "spotted": [], "near": [],
               "left": 0, "practising": [], "proposals": [], "why": ""}
        try:
            dream = _dream()
            days = dream.HISTORY_DAYS if days is None else max(1, min(int(days), dream.HISTORY_DAYS))
            if self.off():  # HABITS_OFF: Habits itself is off, not only its learning
                return {**out, "why": "habits"}
            if self._learning() == "off":
                return out
            with self.held("_dream") as free:
                if not free:
                    return {**out, "status": "busy"}
                if look:
                    return self._look(dream, out, days, began, home)
                return self._dream_locked(dream, out, manual, days, began, home)
        except Exception as exc:
            logger.warning("habits: the dream failed: %s", exc, exc_info=True)
            # an error of the plugin itself (its ledger, its code): never told as Hermes's history being unreadable
            return {**out, "status": "failed", "why": type(exc).__name__}

    def _read_history(self: Habits, dream, now: float, days: int, home=None):
        """Hermes's history (another home's when `home` names one), read only, closed after."""
        store = dream.open_store() if home is None else dream.open_store(home)
        if store is None:
            return dream.Read(turns=[], sessions=0, skipped=0, status="unread", why="no history store")
        try:
            return dream.read_turns(store, now, days)
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _look(self: Habits, dream, out: dict, days: int, began: float, home=None) -> dict:
        """A look: read, spot, say. No dream is recorded, no draft is written or moved, no proposal opened: the ledger
        is as it was. What it finds is this reading's own, so a look at 7 days says 7 days' counts."""
        try:
            read = self._read_history(dream, time.time(), days, home)
            out.update(status=read.status, turns=len(read.turns), sessions=read.sessions, days=read.span,
                       why=read.why or "", look="home" if home is not None else "days")
            if read.status in ("unread", "empty"):
                return out
            spotted, left, near = dream.spot(read.turns, blocked=self.blocked)
            out.update(
                spotted=(lambda doers: [self._spotted_dict(s, doers, True) for s in spotted[:5]])(self._doers()),
                near=[] if spotted else [self._near_dict(n) for n in near[:dream.NEAR]],
                left=len(set(getattr(left, "turns", ()))) if hasattr(left, "turns") else sum(dict(left).values()))
            return out
        finally:
            out["took_ms"] = int((time.monotonic() - began) * 1000)

    def _dream_locked(self: Habits, dream, out: dict, manual: bool, days: int, began: float, home=None) -> dict:
        ledger, now = self.ledger, time.time()
        dream_id = ledger.dream_start(0 if manual else EVERY, manual)
        if dream_id is None:  # a daily dream that ran lately: nothing to do, nothing to say
            return {**out, "status": "skipped"}
        counts: dict = {}
        status, why = "failed", ""
        try:
            read = self._read_history(dream, now, days, home)
            out.update(status=read.status, turns=len(read.turns), sessions=read.sessions, days=read.span,
                       why=read.why or "")
            status, why = read.status, read.why or ""
            if read.status in ("unread", "empty"):
                return out
            spotted, left, near = dream.spot(read.turns, blocked=self.blocked)
            left_read, left_turns = left, set(getattr(left, "turns", ()))  # distinct turns: one left out twice is one
            left = dict(left)
            self._finish_old_drafts(now)
            self.finish_staged()  # a yes whose skill files waited for approval (practice.py)
            self._write_drafts(dream, spotted, read, now, left, left_turns)
            proposed = self._propose(dream, spotted, now)
            out.update(
                spotted=(lambda doers: [self._spotted_dict(s, doers) for s in spotted[:5]])(self._doers()),
                near=[] if spotted else [self._near_dict(n) for n in near[:dream.NEAR]],
                left=len(left_turns) if hasattr(left_read, "turns") else sum(left.values()),
                practising=self._practising(), proposals=self._open_cards())
            counts = dict(turns=len(read.turns), sessions=read.sessions, spotted=len(spotted), proposed=proposed)
            return out
        except Exception as exc:
            status, why = "failed", type(exc).__name__
            raise
        finally:
            out["took_ms"] = int((time.monotonic() - began) * 1000)
            try:
                ledger.dream_end(dream_id, status, why=why, **counts)
                ledger.prune()  # whatever the status: unread and failed dreams leave rows too
            except Exception as exc:
                logger.warning("habits: could not close the dream: %s", exc)

    def _finish_old_drafts(self: Habits, now: float):
        """A reflex the user deleted by hand after the yes counts as a no for 30 days."""
        for row in self.ledger.drafts(("saved",)):
            name = row.get("habit")
            if name and catalog.find(name) is None:
                self.ledger.move_draft(row["key"], "no", ("saved",), no_until=now + NO_DAYS * 86400)

    def _rest_stale(self: Habits, now: float) -> None:
        """A draft that practices keeps its place only while its action comes back: with no sighting and no
        comparison for STALE_DAYS it rests and gives the place back (25). It can come back when its action does."""
        for row in self.ledger.drafts(("practising",)):
            last = max([row.get("since") or 0] + [p.get("at") or 0 for p in self.ledger.practice(row["key"], 1)])
            if now - last > STALE_DAYS * 86400 and self.ledger.move_draft(row["key"], "rest", ("practising",)):
                self.practising.pop(row["key"], None)

    def _doers(self: Habits) -> dict:
        """The reflexes that already do one command: {the command's key: [(name, folder)]} for each shipped example and
        each habit of the user's that is not resting and whose only acting step is that terminal command (a trailing
        `|| true` aside). The dream drafts no second reflex for such a command."""
        dream, found = _dream(), {}
        try:
            for habit in catalog.all_habits():
                nodes = habit.graph.get("nodes") if habit.valid and isinstance(habit.graph, dict) else None
                acts = [n for n in (nodes or {}).values() if isinstance(n, dict) and ("do" in n or "use" in n)]
                if len(acts) != 1 or acts[0].get("do") != "terminal":
                    continue
                args = acts[0].get("args") if isinstance(acts[0].get("args"), dict) else {}
                command = args.get("command")
                if not isinstance(command, str) or not command.strip():
                    continue
                if habit.origin != "example" and (self.ledger.info(habit.name) or {}).get("status") == "retired":
                    continue
                key = dream.command_key(re.sub(r"\s*\|\|\s*true\s*$", "", command.strip()))
                found.setdefault(f"terminal:{key}", []).append((habit.name, str(args.get("workdir") or "")))
        except Exception as exc:  # the dream drafts as before
            logger.warning("habits: could not list the reflexes that already do a command: %s", exc)
        return found

    @staticmethod
    def _done_by(doers: dict, key: str, folder: str = "") -> str:
        """The name of a reflex that already does this action (same command, and same folder when both name one)."""
        from .practice import _same_folder
        for name, where in doers.get(str(key), ()):
            if not where or not folder or _same_folder(where, folder):
                return name
        return ""

    def _write_drafts(self: Habits, dream, spotted, read, now: float, left: dict, left_turns: set) -> None:
        """Group what was spotted, turn each group into a draft; a draft that does not validate is counted in
        `left["invalid"]`. A draft past `spotted` keeps what the user was shown: the ledger freezes it, this
        only passes the counts."""
        ledger, learning = self.ledger, self._learning()
        self._rest_stale(now)
        doers = self._doers()
        for r in ledger.drafts(("spotted", "practising")):  # a reflex does it already: the draft gives its place back
            folder = str(r.get("cwd") or (_json(r.get("args"), {}) or {}).get("workdir") or "")
            if not _json(r.get("parts"), []) and self._done_by(doers, r["key"], folder) not in ("", r.get("habit")):
                if ledger.move_draft(r["key"], "gone", ("spotted", "practising")):
                    self.practising.pop(r["key"], None)
        rows = {r["key"]: r for r in ledger.drafts()}
        learned = {r["habit"]: r for r in rows.values() if r["state"] == "saved" and r.get("habit")}
        frozen = frozenset(k for k, r in rows.items() if r["state"] != "spotted")
        groups = dream.group(spotted, read.turns, reflexes=frozenset(learned), frozen=frozen)
        day_of = {(t.session, t.index): t.day for t in read.turns}
        taken = {r["name"] for r in rows.values() if r.get("name")} | {h.name for h in catalog.all_habits()}
        uses = {r["key"]: r["habit"] for r in learned.values()}
        practicing = sum(1 for r in rows.values() if r["state"] == "practising")
        queue = list(groups)
        while queue:
            group = queue.pop(0)
            steps = [s.action for s in group]
            key = dream.group_key(group)
            row, first = rows.get(key), group[0]
            if row is not None and row["state"] in ("no", "rest"):
                # a no that ran out, or a rest, ends when the action came back since: 3 turns on 2 days
                asked = row if row["state"] == "no" else {**row, "state": "no"}
                fresh = {} if row["state"] == "no" else dict(same=0, shape=0, differs=0, errors=0, seen=0, streak=0)
                if not dream.may_draft(first, asked, now) or not ledger.move_draft(
                        key, "spotted", (row["state"],), since=now, **fresh):
                    continue
                row = ledger.draft(key)
            if row is not None and row["state"] != "spotted":
                if row["state"] == "saved":  # it still shows up: its last_seen moves, nothing else
                    ledger.put_draft(key, turns=first.turns, days=first.days, last_seen=first.last_seen)
                continue  # practicing, ready, asked, gone: left as the user saw it
            if len(group) > 1:  # the pair's own figures: the turns they share, not each part's
                shared = frozenset.intersection(*(s.where for s in group))
                turns, days = len(shared), len({day_of[w] for w in shared if w in day_of})
            else:
                turns, days = first.turns, first.days
            if len(group) == 1 and self._done_by(doers, key, first.cwd or str(first.action.args.get("workdir") or "")):
                continue  # a shipped example or a reflex of the user's does this very command: no second reflex
            why = self.excluded_step(steps)  # the second look: spot already leaves these out
            reflex = None if why else dream.draft_reflex(steps, first.cwd, uses)
            invalid = bool(reflex is not None and (graph.validate(reflex) or graph.drafting(reflex)))
            if (why or invalid) and len(group) > 1:  # one part that cannot be drafted takes no partner with it
                # a saved reflex alone is no draft (dream.group): its part goes back only with a partner
                queue[0:0] = [[s] for s in group if not s.action.key.startswith("reflex:")]
                continue
            if why:
                left[why] = left.get(why, 0) + turns
                left_turns.update(*(s.where for s in group))
                continue
            if invalid:
                left["invalid"] = left.get("invalid", 0) + turns
                left_turns.update(*(s.where for s in group))
                continue
            name = row["name"] if row is not None and row.get("name") else dream.draft_name(steps, taken)
            taken.add(name)
            ledger.put_draft(
                key, name=name, tool=first.action.tool, args=first.action.args, raw=first.action.raw,
                parts=[s.action.key for s in group] if len(group) > 1 else [], graph=reflex,
                reads=int(all(dream.local_read(a.tool, a.args, first.cwd or str(a.args.get("workdir") or ""))
                              for a in steps)), turns=turns, days=days,
                first_seen=min(s.first_seen for s in group), last_seen=max(s.last_seen for s in group),
                moments=first.moments, cwd=first.cwd)
            row = ledger.draft(key)
            if row is not None and row["state"] == "spotted" and learning in ("practice", "auto") and \
                    practicing < MAX_PRACTISING:
                if ledger.move_draft(key, "practising", ("spotted",), since=now):
                    practicing += 1
                    self.practising_changed()

    def _propose(self: Habits, dream, spotted, now: float) -> int:
        """Change and rest proposals for the reflexes the dream made (3.5); at most 3 open, one line of news."""
        ledger = self.ledger
        habits = []
        for row in ledger.drafts(("saved",)):
            name = row.get("habit")
            info = ledger.info(name) if name else None
            if info is None:
                continue
            # last seen in the history or in its own runs: once Hermes calls the reflex, the action itself is gone
            # from the history, and the runs alone say it still serves
            seen = max([row.get("last_seen") or 0, row.get("since") or 0] +
                       [s.last_seen for s in spotted if s.action.key == row["key"]] +
                       [r.get("at") or 0 for r in ledger.runs(name, 1)])
            habits.append({"name": name, "version": info["version"], "status": info["status"], "key": row["key"],
                           "raw": row.get("raw") or "", "last_seen": seen})
        made, told = 0, False
        proposals = dream.propose(spotted, habits, now)
        wanted = {(p.kind, p.subject) for p in proposals}
        for old in ledger.proposals():  # one that is no longer proposed (it ran, it is gone) is withdrawn, news too
            if (old["kind"], old["subject"]) not in wanted or catalog.find(old["subject"]) is None:
                ledger.decide(old["id"], "gone", "rules")
                ledger.withdraw_news(old["subject"], ("dream_proposal",))
        for proposal in proposals:
            if proposal.kind == "rest" and self._learning() == "auto":  # alone under auto: the rules rest it
                version = next((h["version"] for h in habits if h["name"] == proposal.subject), "")
                ledger.retire(proposal.subject, version, "not used for a month", by="rules")
                made += 1
                continue
            if len(ledger.proposals()) >= MAX_OPEN:
                break
            if ledger.propose(proposal.kind, proposal.subject, proposal.detail) is not None:
                made += 1
                if not told:  # one line of news per dream
                    told = True
                    ledger.tell(proposal.subject, "dream_proposal")
        return made

    # ---- the summary --------------------------------------------------------------------------------------

    @staticmethod
    def _moments(shares: dict | None) -> dict:
        """The moments of a spotted action or of a draft's row, as the report and `--json` give them."""
        moments = {}
        for key, value in (shares or {}).items():
            if key == "skill":
                if value:
                    moments["skill"] = {"name": value[0], "share": round(float(value[1]), 2)}
            elif value:
                moments[key] = round(float(value), 2)
        return moments

    def _spotted_dict(self: Habits, s, doers: dict | None = None, look: bool = False) -> dict:
        out = {"action": clip(_dream().brief(s.action)), "turns": s.turns, "days": s.days,
               "moments": self._moments(s.moments)}
        doer = self._done_by(doers or {}, s.action.key, s.cwd or str(s.action.args.get("workdir") or ""))
        if doer:  # no draft for it: this reflex does that very command
            out["reflex"] = doer
        elif not look:
            name = self._draft_named(s.action.key)
            if name:  # where its full text is
                out["name"] = name
        return out

    def _draft_named(self: Habits, key: str) -> str:
        """The name of the live draft that holds this action (alone or as one part of a grown draft), or ""."""
        try:
            for row in self.ledger.drafts(("spotted", "practising", "ready", "rest")):
                parts = _json(row.get("parts"), [])
                if row["key"] == key or (isinstance(parts, list) and key in parts):
                    return str(row.get("name") or "")
        except Exception as exc:
            logger.debug("habits: no draft name: %s", exc)
        return ""

    def _near_dict(self: Habits, n) -> dict:
        dream = _dream()
        return {"action": clip(dream.brief(n.action)), "turns": n.turns, "days": n.days,
                "missing": {"turns": max(0, dream.MIN_TURNS - n.turns), "days": max(0, dream.MIN_DAYS - n.days)}}

    def _practising(self: Habits) -> list[dict]:
        needed = self._ready_matches()
        return [{"name": r["name"], "action": _row_action(r), "same": int(r.get("same") or 0),
                 "shape": int(r.get("shape") or 0), "needed": needed}
                for r in self.ledger.drafts(("practising",))]

    def _open_cards(self: Habits) -> list[dict]:
        out = [{"kind": "create", "name": r["name"], "action": _row_action(r)}
               for r in self.ledger.drafts(("ready",))]
        if self._learning() == "suggest":  # learning is suggest: the try card is what waits
            out += [{"kind": "try", "name": r["name"], "action": _row_action(r)}
                    for r in self.ledger.drafts(("spotted",))]
        out += [{"kind": p["kind"], "name": p["subject"], "action": clip((p.get("detail") or {}).get("raw") or "")}
                for p in self.ledger.proposals()]
        for card, text in zip(out, _dream().distinct([c["action"] for c in out])):  # two cards never read the same
            card["action"] = text
        return out

    # ---- how it is said -----------------------------------------------------------------------------------

    # a tie goes to the moment that says something: after a skill and a schedule before first and last, which a
    # turn of one call has at 100 % without meaning anything
    MOMENT_RANK = {"skill": 0, "scheduled": 1, "first": 2, "last": 3}

    def _moments_shown(self: Habits, moments, lang: str) -> list[tuple[str, str]]:
        """The (moment, words) a card shows, the two strongest at 50 % or more. `moments` is a row's stored form (the
        skill as a list) or the report's (the skill as a dict)."""
        moments = moments if isinstance(moments, dict) else {}
        if isinstance(moments.get("skill"), (list, tuple)):
            moments = self._moments(moments)
        shown = []
        for key in ("first", "last", "scheduled"):
            value = moments.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0.5:
                shown.append((value, key, say(f"moment_{key}", lang, percent=percent(value, lang))))
        skill = moments.get("skill")
        if isinstance(skill, dict) and skill.get("share", 0) >= 0.5:
            shown.append((skill["share"], "skill", say("moment_skill", lang, skill=skill["name"],
                                                       percent=percent(skill["share"], lang))))
        shown.sort(key=lambda t: (-t[0], self.MOMENT_RANK[t[1]]))
        return [(key, text) for _, key, text in shown[:SHOWN_MOMENTS]]

    def moments_text(self: Habits, moments: dict, lang: str) -> str:
        return "".join(" " + text for _, text in self._moments_shown(moments, lang))

    def legend_for(self: Habits, moments_of: list, lang: str) -> str:
        """The line that explains "closes the work" and "first gesture of a session", only when a card above it says
        one of them."""
        used = any(key in ("first", "last") for moments in moments_of for key, _ in self._moments_shown(moments, lang))
        return say("draft_legend", lang) if used else ""

    def dream_text(self: Habits, summary: dict, days: int | None = None) -> str:
        """The summary as the user reads it: what was read, what comes back, what practices, then the next step."""
        lang, status = self.lang(), summary.get("status")
        if status == "off":
            return say("dream_habits_off" if summary.get("why") == "habits" else "dream_off", lang)
        if status == "busy":
            return say("dream_busy", lang)
        if status == "skipped":
            return ""
        if status == "unread":
            return say("dream_unread", lang, why=summary.get("why") or "?")
        if status == "failed":
            return say("dream_failed", lang, why=summary.get("why") or "?")
        if status == "empty":
            return say("dream_empty", lang, days=days or _dream().HISTORY_DAYS)
        lines = [say("dream_read", lang, turns=summary["turns"], sessions=summary["sessions"], days=summary["days"],
                      took=seconds(summary["took_ms"], lang))]
        if status == "partial":
            lines.append(say("dream_partial", lang, why=summary.get("why") or "?"))
        spotted = summary.get("spotted") or []
        if spotted:
            lines.append(say("spotted_one" if len(spotted) == 1 else "spotted_many", lang, count=len(spotted)))
            lines += [say("spotted_line", lang, action=s["action"], turns=s["turns"], days=s["days"],
                           moments=self.moments_text(s["moments"], lang))
                      + (say("spotted_done_by", lang, name=s["reflex"]) if s.get("reflex") else "")
                      + (say("spotted_full", lang, name=s["name"]) if s.get("name") and s["action"].endswith("…") else "")
                      for s in spotted]
            legend = self.legend_for([s["moments"] for s in spotted], lang)  # said once, under the list
            if legend:
                lines.append(legend)
        else:
            lines.append(say("spotted_none", lang))
            lines += [say("near_line", lang, action=n["action"], turns=n["turns"], days=n["days"])
                      for n in summary.get("near") or []]
        if summary.get("look"):  # a look keeps nothing: no drafts, no cards, no next step to take from it
            if summary.get("left"):
                lines.append(say("left_line", lang, count=summary["left"]))
            lines.append(say("dream_look_home" if summary["look"] == "home" else "dream_look", lang,
                             max=_dream().HISTORY_DAYS))
            return "\n".join(lines)
        try:  # what this reading found is not all that waits: older drafts and grown ones wait too
            open_drafts = len(self.ledger.drafts(("spotted", "practising", "ready")))
        except Exception:
            open_drafts = 0
        if open_drafts and open_drafts != sum(1 for s in spotted if not s.get("reflex")):
            lines.append(say("drafts_in_all", lang, count=open_drafts))
        shown_texts = [str(x.get("action")) for key in ("spotted", "near", "practising", "proposals")
                       for x in summary.get(key) or [] if isinstance(x, dict)]
        if any(text.endswith("…") for text in shown_texts):  # a short form: `show <name>` has the whole text
            lines.append(say("cut_note", lang))
        if summary.get("left"):
            lines.append(say("left_line", lang, count=summary["left"]))
        lines += self.dream_lines(lang)  # a read says how many were the same, an action how often it came back
        if summary.get("practising"):  # the rule that frees a place, in one line
            lines.append(say("practising_rule", lang, days=STALE_DAYS))
        try:  # spotted and drafted, but no place yet: the dream says which wait (when it practices on its own)
            waiting = [r["name"] for r in self.ledger.drafts(("spotted",))] if self._learning() in ("practice", "auto") else []
        except Exception:
            waiting = []
        if waiting:
            lines.append(say("spotted_waiting", lang, count=len(waiting), places=MAX_PRACTISING,
                             names=", ".join(waiting)))
        lines += self.waiting_lines(lang)  # what only the user can answer, and where
        lines.append(say("dream_next_suggest" if self._learning() == "suggest" else "dream_next", lang))
        return "\n".join(lines)

    def waiting_lines(self: Habits, lang: str, names: bool = True) -> list[str]:
        """What waits for the user's word (a reflex that practiced well, a proposal to try, a change or a rest), each as
        its card's question and the exact words to type in a chat: a terminal cannot answer it, so it says so. Never
        raises; three cards at most and "+N"."""
        try:
            cards = self.dream_cards()  # a ready reflex's yes first, then changes and rests, then the tries
            lines = []
            for card in cards[:MAX_OPEN]:
                lines.append("❓ " + " ".join(str(card["question"]).split()))
                lines.append("   " + say("waits_chat_try" if card["kind"] == "try" else "waits_chat", lang,
                                         name=card["name"]))
            if names:  # the others, every one by name (a list of drafts, when it stands beside, names them itself)
                lines += self.more_waiting([(0, "dream", card) for card in cards[MAX_OPEN:]], limit=None)
            return lines
        except Exception as exc:
            logger.debug("habits: no waiting lines: %s", exc)
            return []

    def draft_row(self: Habits, name: str) -> dict | None:
        """The draft of that name that is still alive (waiting, practicing, ready or at rest), else None."""
        try:
            return next((d for d in self.ledger.drafts(("spotted", "practising", "ready", "rest"))
                         if d.get("name") == name), None)
        except Exception as exc:
            logger.debug("habits: no draft row: %s", exc)
            return None

    def _draft_state(self: Habits, row: dict) -> tuple[str, str]:
        """(what the draft is doing, in words; the next step, in words): the chat words when only a chat can answer."""
        lang, state, name = self.lang(), row["state"], row["name"]
        if state == "ready":
            return say("draft_state_ready", lang), say("waits_chat", lang, name=name)
        if state == "spotted":
            if self._learning() == "suggest":
                return say("draft_state_spotted_try", lang), say("waits_chat_try", lang, name=name)
            return say("draft_state_spotted", lang), say("draft_next_spotted", lang)
        if state == "practising":
            needed = self._ready_matches()  # where it stands, and how many times more before it asks
            matches = min(needed, int(row.get("same") or 0) + int(row.get("shape") or 0))
            return say("draft_state_practising", lang), say("draft_next_practising", lang, needed=needed,
                                                            matches=matches, left=max(1, needed - matches))
        return say("draft_state_rest", lang), say("draft_next_rest", lang)

    def draft_note(self: Habits, name: str) -> str:
        """What a draft's name answers to a yes, a rest or a test that has no habit to act on: what it is, where it stands
        and the exact words that work. "" when no draft has the name."""
        row = self.draft_row(name)
        if row is None:
            return ""
        state, nxt = self._draft_state(row)
        return say("draft_note", self.lang(), name=name, state=state, next=nxt)

    def draft_detail(self: Habits, name: str) -> str:
        """`hermes habits show <draft>`: what the dream saw, the reflex it would become, where it stands, and the exact
        words to type next. "" when no draft has the name."""
        from .display import steps
        row = self.draft_row(name)
        if row is None:
            return ""
        lang = self.lang()
        state, nxt = self._draft_state(row)
        moments = _json(row.get("moments"), {})
        moments = moments if isinstance(moments, dict) else {}
        graph_ = _json(row.get("graph"), {})
        lines = [say("draft_head", lang, name=name, state=state),
                 say("draft_saw", lang, turns=int(row.get("turns") or 0), days=int(row.get("days") or 0),
                     moments=self.moments_text(moments, lang))]
        legend = self.legend_for([moments], lang)
        if legend:
            lines.append(legend)
        if isinstance(graph_, dict) and graph_.get("nodes"):  # whole: what would run, step by step, and where
            lines += [say("draft_steps", lang)] + steps(graph_, True, lang)
        if row["state"] == "practising":
            matches = int(row.get("same") or 0) + int(row.get("shape") or 0)
            lines.append(say(self._practising_key(row), lang, name=name, matches=matches, needed=self._ready_matches()).lstrip("- "))
        lines += [nxt, say("draft_try_here", lang, name=name)]
        return "\n".join(lines)

    def _practising_key(self: Habits, row: dict) -> str:
        """The line of a draft that practices: "the same" for a read whose runs were compared, "came back" for an action
        that was only counted (an act, a grown draft, a read that could not be run beside Hermes)."""
        if row.get("reads") and not row.get("parts") and not self._came_back(row):
            return "practising_line"
        return "practising_plan_line"

    def dream_lines(self: Habits, lang: str) -> list[str]:
        """What /habits shows of the dream, three lines at most and "+N": the drafts that practice (a read says how
        many were the same, an action how many times it came back) and the ones that rest. Never raises."""
        try:
            needed, lines, resting, names = self._ready_matches(), [], [], []
            for row in self.ledger.drafts(("practising", "rest")):
                if row["state"] == "rest":
                    resting.append(row["name"])
                    continue
                matches = int(row.get("same") or 0) + int(row.get("shape") or 0)
                names.append(row["name"])
                lines.append(say(self._practising_key(row), lang,
                                  name=row["name"], matches=matches, needed=needed))
            if len(lines) > MAX_PRACTISING:
                more = len(lines) - MAX_PRACTISING
                lines = lines[:MAX_PRACTISING] + [say("more_drafts", lang, count=more, names=", ".join(names[MAX_PRACTISING:]))]
            if resting:  # the resting ones are one line, so they never hide the ones that practice
                lines.append(say("resting_drafts", lang, count=len(resting), names=", ".join(resting)))
            return lines
        except Exception as exc:
            logger.debug("habits: no dream lines: %s", exc)
            return []

    def draft_lines(self: Habits, lang: str) -> list[str]:
        """`hermes habits list`: every draft that waits, practices, is ready or rests, one per line, its name first and
        where it stands, a ready one before the ones that practice before the ones that wait. Never raises."""
        try:
            order = {"ready": 0, "practising": 1, "spotted": 2, "rest": 3}
            rows = sorted(self.ledger.drafts(("spotted", "practising", "ready", "rest")),
                          key=lambda r: (order.get(r["state"], 9), r.get("since") or 0, r["name"]))
            if not rows:
                return []
            needed, lines = self._ready_matches(), [say("drafts_head", lang)]
            for row in rows:
                state = row["state"]
                matches = min(needed, int(row.get("same") or 0) + int(row.get("shape") or 0))
                words = (say("draft_row_try" if self._learning() == "suggest" else "draft_row_place", lang)
                         if state == "spotted" else
                         say("draft_row_practising", lang, matches=matches, needed=needed) if state == "practising" else
                         say("draft_row_ready", lang) if state == "ready" else say("draft_row_rest", lang))
                lines.append(say("draft_row", lang, name=row["name"], state=words, action=_row_action(row)))
            return lines + [say("drafts_tail", lang)]
        except Exception as exc:
            logger.debug("habits: no draft lines: %s", exc)
            return []

    # ---- when it runs -------------------------------------------------------------------------------------

    def maybe_dream(self: Habits, platform: str = "") -> None:
        """After a reply went out: dream in the background when the daily rule says so. Never raises."""
        try:
            if self.off() or self.paused() or self._learning() == "off" or self.setting("dream") == "manual":
                return
            if str(platform or "") in MACHINERY:
                return
            checked = getattr(self, "dream_checked", None)
            if checked is not None and time.monotonic() - checked < CHECK:
                return
            self.dream_checked = time.monotonic()
            if not self.ledger.dream_due(EVERY):  # the ledger's own rule, the one dream_start applies
                return
            # not in self.workers: a process that ends drops it; the next dream marks its `running` row failed
            threading.Thread(target=self._dream_thread, args=(contextvars.copy_context(),), daemon=True).start()
        except Exception as exc:
            logger.warning("habits: could not start the dream: %s", exc)

    def _dream_thread(self: Habits, context) -> None:
        try:
            context.run(self.dream_now)
        except Exception as exc:
            logger.warning("habits: the dream failed: %s", exc)

    # ---- the start on a loaded skill --------------------------------------------------------------------------------

    def on_transform_tool_result(self: Habits, tool_name="", args=None, result=None, session_id="", **_):
        """Hermes loaded a skill (skill_view): when it is the skill of a habit of the user's that starts on its
        skill, the reflex runs and its reply goes into the result's "habits" field. None (the result as it was) on
        anything else, and on any failure: this hook never raises."""
        token = None
        try:
            if tool_name != "skill_view" or ON_LOAD.get() or not isinstance(result, str):
                return None
            if self.off() or self.paused():
                return None
            args = args if isinstance(args, dict) else {}
            session = str(session_id or "")
            platform = self.platforms.get(session)
            if not platform or platform in MACHINERY:  # a session whose turn this plugin never saw, or no user's
                return None
            if args.get("file_path"):  # a side file of the skill: the skill itself is not loaded
                return None
            parsed = json.loads(result)
            if (not isinstance(parsed, dict) or parsed.get("success") is not True or parsed.get("error")
                    or parsed.get("dedup") or parsed.get("status") == "unchanged"
                    or parsed.get("content_returned") is False):  # Hermes's stub for a second view: nothing to add
                return None
            habit = self._skill_habit(args.get("name"), parsed.get("name"))
            if habit is None:
                return None
            if (session, habit.name) in self.loaded:
                return None
            row = self.ledger.info(habit.name) or {}
            status = self.ledger.status(habit.name, habit.version)
            if status == "retired":
                return None
            dry = self.unconfirmed(habit, row, status) or status != "active"
            if dry and (self._learning() == "off" or not reads_all(habit.graph)):
                return None
            if len(self.loaded) >= SKILL_SEEN:
                self.loaded.clear()
            self.loaded.add((session, habit.name))  # one run per load, even when this one is busy or fails
            token = ON_LOAD.set(True)
            with self.held(habit.name) as free:
                if not free:
                    return None
                run = self.run(habit, habit.graph, dry=dry, max_seconds=SKILL_SECONDS, keep=not dry, turn=True)
            try:
                self.ledger.record(habit.name, habit.version, "skill", "adhoc", run, session=session or None)
            except Exception as exc:  # the steps ran: the reply still goes to the model
                logger.warning("habits: could not record the skill start of %s: %s", habit.name, exc)
            parsed["habits"] = SKILL_START.format(
                name=habit.name, state="a test run, it is learning" if dry else "automatic",
                outcome="done" if run.status == "end" else "handed back", message=self._skill_message(run, dry))
            return json.dumps(parsed, ensure_ascii=False)
        except Exception as exc:
            logger.warning("habits: the start on a loaded skill failed: %s", exc)
            return None
        finally:
            if token is not None:
                ON_LOAD.reset(token)

    def _skill_habit(self: Habits, *names) -> catalog.Habit | None:
        """The user's own valid habit that starts on its skill, by the name the model gave or the canonical one."""
        for name in names:
            if not isinstance(name, str) or not name:
                continue
            for tried in (name, name.rsplit("/", 1)[-1]):
                habit = catalog.find(tried)
                if habit is not None:
                    return habit if habit.origin == "skill" and habit.valid and graph.starts_on_skill(habit.graph) else None
        return None

    @staticmethod
    def _skill_message(run, dry: bool) -> str:
        """The reply as habit_run says it: done, or handed back with what is left; a test only says it was one."""
        reply = relay(run, LEARNING if dry else "")
        if dry:
            return str(reply["next"])
        if read_outside(run):  # it read the web: all of it goes inside the untrusted block
            return f"{reply['untrusted']}\n{reply['next']}"
        left = reply["left"]
        return "\n".join([str(reply["message"])] + ([f"Left: {'; '.join(left)}"] if left else []) + [str(reply["next"])])
