"""The /habits cockpit and the prompt section: where each habit stands, for the user to decide and for the model to know.

/habits lists the habits by state, then asks the decisions waiting for the user (a ready habit: let it answer on its
own?) with buttons where the chat has them (ask_choice), as text commands otherwise. Only the user's word changes a
state here, and a yes is bound to the version its card showed: the ledger takes it as a compare-and-set. The model is
told names and states, never the text of a habit the user did not ask for, and that it never changes a state itself.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from . import catalog, graph, runner
from .display import _no_media
from .ledger import MADE
from .texts import count, language, percent, say, seconds, why
from .tools import _strings
from .trigger import CLOSE_CHECKS, read_only, takes_message

if TYPE_CHECKING:  # the class these methods are mixed into
    from .plugin import Habits

logger = logging.getLogger(__name__)

# The chat verbs, French and English alike (the language setting only picks the texts), and the CLI's words.
VERBS = {"oui": "yes", "yes": "yes", "promote": "yes", "essai": "try", "try": "try", "trial": "try", "repos": "rest",
         "rest": "rest", "faux": "wrong", "wrong": "wrong", "pause": "pause", "reprends": "resume", "resume": "resume",
         "annule": "undo", "undo": "undo"}
ORDER = ("active", "shadow", "ready", "retired", "byname", "invalid")  # ━ ┄ ❓ ─ ◇ ◇
LINES = 8  # habits listed before +N: a phone screen
CARDS = 3  # decisions asked at once
PROMPT_CHARS = 2400  # registered as max_chars: Hermes skips a section that is over, it never cuts one
PROMPT_HABITS = 12
DESCRIPTION_CHARS = 60
SIGNATURE_INPUTS = 3  # inputs shown before "…"
INPUT_CHARS = 20  # an input name is the habit author's word: cut, like a description
STEP_CHARS = 80
# A path or a long token in a description stays out of the prompt: it is the user's, and the prompt is high trust.
PATHLIKE = re.compile(r"(?<![\w/])(?:~|\$HOME|\.{1,2})?/[\w.~-]+(?:/\S*)?|\b[\w-]*[A-Za-z][\w-]*\d[\w-]{14,}\b|\b\d[\w-]*[A-Za-z][\w-]{14,}\b")

# A reflex's state as the model reads it: what it may do with it. Mirrors what habit_run does by name: learning or
# resting, a habit with a door runs dry and hands the task back whole; automatic or "call it", it runs live.
LEARNING = "learning · do this one yourself for now"
REFLEX = {"active": "automatic", "byname": "call it", "shadow": LEARNING, "ready": LEARNING, "retired": "resting · do it yourself"}
REFLEX_ORDER = ("automatic", "call it", LEARNING, "resting · do it yourself")
# The exact forms of the call, here: the model uses dry, habits and message without loading the tool's description.
PROMPT_INTRO = (
    "You have reflexes: habits. A habit does a known task in about a second, without you.\n"
    "Pass the baton: habit_run {name, inputs}. It comes back done (trust it: use its result as it is, do not redo or re-check it) or handed back "
    "(do only what `left` lists). A reflex that lists message takes the user's words: "
    "habit_run {name, message: \"<the user's words>\"}.\n"
    "When:")
PROMPT_WHEN_SEVERAL = (
    "- several reflexes for one request, different reflexes too: ONE call, not one each: "
    "habit_run {habits: [{name: hermes-health}, {name: check-links, inputs: {page: …}}]};")
PROMPT_WHEN = (
    "- a step of your plan matches a reflex below: call it and continue from its values;",
    PROMPT_WHEN_SEVERAL,
    "- many small closed decisions (sort, triage, classify 30 items): draw a one-off graph instead of delegating;",
    "- you finish work that has a closing reflex (sync, check, tidy): call it last;",
    "- to test without writing anything, add dry: true to any call (habit_run {file: \"<path>\", inputs, dry: true}).",
)
PROMPT_LIST = "Your reflexes (name(inputs) · state · what for):"
PROMPT_TAIL = (
    "The user owns the states (/habits): never change one yourself (no `hermes habits` promote, undo, resume, "
    "reset or retire, no write under plugin-data/habits). The user says a habit got it wrong: point them to "
    "/habits wrong. To change a habit, save it again with habit_save. Never patch, merge or delete a skill "
    "that holds assets/HABIT.yaml, reviewing or curating skills included.")
PROMPT_NONE_OWN = "None is the user's own yet: to keep a task the user repeats, load the skill habits:writing-habits."
PROMPT_MORE = "+{n} more reflexes, not listed here."  # /habits is the user's command, not one the model can run
PROMPT_CURATOR = "Leave these habits alone (never archive, merge, patch or delete them): "


def signature(habit: catalog.Habit) -> str:
    """name(inputs): its input names (validated variable names, no user text), and message when it reads the
    user's words."""
    names = [n if len(n) <= INPUT_CHARS else n[:INPUT_CHARS - 1] + "…"
             for n in (habit.graph.get("inputs") or []) if isinstance(n, str)]
    if reads_message(habit.graph):
        names.append("message")
    shown = names[:SIGNATURE_INPUTS] + (["…"] if len(names) > SIGNATURE_INPUTS else [])
    return f"{habit.name}({', '.join(shown)})"


def reads_message(habit: dict) -> bool:
    """The graph reads the user's words: an ask or llm does (takes_message, which the trigger's confirm question
    reads too), or {trigger} is in a do's arguments, or a use is given trigger."""
    if takes_message(habit):
        return True
    for node in (habit.get("nodes") or {}).values() if isinstance(habit.get("nodes"), dict) else ():
        if not isinstance(node, dict):
            continue
        if "do" in node and any(m.group(1) == "trigger" for text in _strings(node.get("args"))
                                for m in graph.TEMPLATE.finditer(text)):
            return True
        if "use" in node and "trigger" in (node.get("with") if isinstance(node.get("with"), list) else []):
            return True
    return False


def _described(text) -> str:
    """A description fit for the high-trust prompt: one line, no path or token, DESCRIPTION_CHARS at most."""
    text = PATHLIKE.sub("…", " ".join(str(text or "").split()))
    return text if len(text) <= DESCRIPTION_CHARS else text[:DESCRIPTION_CHARS - 1] + "…"


def state_of(habit: catalog.Habit, summary: dict) -> str:
    """Where a habit stands for the user: one of ORDER."""
    if not habit.valid:
        return "invalid"
    if habit.kind == "one-off" and summary.get("status") != "retired":
        return "byname"
    if summary.get("status") == "shadow" and summary.get("ready"):
        return "ready"
    status = summary.get("status") or "shadow"
    return status if status in ORDER else "retired"  # a state an older version wrote: resting


def _step(node: dict) -> str:
    """One step as the card shows it: a command with its {values} as <values>, a tool's name, a habit it uses."""
    if "use" in node:
        return str(node["use"])
    command = (node.get("args") or {}).get("command") if node.get("do") == "terminal" else None
    if not isinstance(command, str):
        return str(node.get("do"))
    command = re.sub(r"\{(\w+)\}", r"<\1>", " ".join(command.split()))
    return command if len(command) <= STEP_CHARS else command[:STEP_CHARS - 1] + "…"


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


class Cockpit:
    """/habits: the list, the decision cards and the verbs; `hermes habits promote|pause|resume|undo`; the prompt
    section."""

    def lang(self: Habits) -> str:
        return language(self.setting("language"))

    def rows(self: Habits) -> list[tuple[catalog.Habit, dict, str]]:
        """The user's habits (examples aside), each with its numbers and its state."""
        rows = []
        for habit in catalog.all_habits():
            if habit.origin == "example":
                continue
            summary = self.summary(habit)
            rows.append((habit, summary, state_of(habit, summary)))
        return rows

    def counts(self: Habits, rows) -> dict:
        lang, states = self.lang(), [s for _, _, s in rows]
        return {"active": count("active", states.count("active"), lang),
                "learning": count("learning", states.count("shadow") + states.count("ready"), lang)}

    # ---- the list and its cards ------------------------------------------

    def grouped(self: Habits, rows, cli: bool = False) -> str:
        lang = self.lang()
        if not rows:
            return self.empty(cli)
        lines = [self.line(habit, summary, s) for habit, summary, s in sorted(rows, key=lambda row: ORDER.index(row[2]))]
        head = [say("head", lang, **self.counts(rows))] + ([say("paused_head", lang)] if self.paused() else [])
        if len(lines) > LINES:
            more = len(lines) - LINES
            lines = lines[:LINES] + [say("more_one" if more == 1 else "more", lang, count=more)]
        waiting = sum(1 for _, _, s in rows if s == "ready")
        return "\n".join(head + lines + ([count("decisions", waiting, lang)] if waiting else []))

    def empty(self: Habits, cli: bool = False) -> str:
        """No habit of one's own: the shipped reflexes that only read (they run live by name), and one to try."""
        lang = self.lang()
        shipped = [h.name for h in catalog.examples() if h.valid and read_only(h.graph)]
        lines = [say("none", lang)] + ([say("paused_head", lang)] if self.paused() else []) + (
            [say("none_shipped", lang, names=", ".join(shipped))] if shipped else [])
        if "hermes-health" in shipped:
            lines.append(say("none_try_cli" if cli else "none_try", lang))
        return "\n".join(lines)

    def line(self: Habits, habit, summary, s) -> str:
        lang, name = self.lang(), habit.name
        if s == "active":
            gain = say("gain", lang, fast=seconds(summary["active_ms"], lang), slow=seconds(summary["shadow_ms"], lang)) if (
                summary.get("active_ms") and summary.get("shadow_ms")) else ""
            return say("line_active", lang, name=name, gain=gain)
        if s == "shadow":
            numbers = dict(name=name, compared=summary.get("compared", 0), runs=self.setting("promote_runs"))
            if summary.get("agreement") is not None:
                return say("line_shadow", lang, extra=say("right", lang, percent=percent(summary["agreement"], lang)),
                           **numbers)
            return say("line_shadow", lang, extra="", **numbers)
        if s == "ready":
            agreed, of = self.ledger.tally(name, self.version(habit))
            return say("line_ready", lang, name=name, agreed=agreed, compared=of)
        return say(f"line_{s}", lang, name=name)

    def waiting(self: Habits, rows=None) -> list[tuple[catalog.Habit, dict, str]]:
        """The decisions waiting for the user, the oldest first: ready habits."""
        rows = self.rows() if rows is None else rows
        return sorted((row for row in rows if row[2] == "ready"),
                      key=lambda row: row[1].get("since") or 0)

    def access(self: Habits, habit: catalog.Habit) -> tuple[str, str]:
        """(what a habit touches, as its card says it, the writes it will make or ""): read-only (the commands it
        reads), or it writes."""
        lang = self.lang()
        nodes = [n for n in (habit.graph.get("nodes") or {}).values() if isinstance(n, dict)]
        if read_only(habit.graph):
            steps = list(dict.fromkeys(_step(n) for n in nodes if "do" in n))[:3]
            return (say("reads", lang, steps=", ".join(steps)) if steps else say("reads_alone", lang)), ""
        steps = list(dict.fromkeys(_step(n) for n in nodes if ("do" in n and not runner._reads(n)) or "use" in n))
        return say("writes", lang), say("card_writes", lang, steps=", ".join(steps[:2]))

    def card(self: Habits, habit: catalog.Habit, summary: dict, s: str) -> tuple[str, list]:
        """A decision's question and its buttons."""
        lang, name = self.lang(), habit.name
        access, writes = self.access(habit)
        version = self.version(habit)
        took = _median(r["ms"] for r in self.ledger.runs(name, 20) if r["version"] == version and r["mode"] == "shadow")
        gain = say("gain", lang, fast=seconds(took, lang), slow=seconds(summary["shadow_ms"], lang)) if (
            took and summary.get("shadow_ms")) else ""
        asks = ", ".join(say("quote", lang, text=" ".join(a.split())[:60]) for a in self.ledger.asks(name, version))
        agreed, of = self.ledger.tally(name, version)
        fields = dict(name=name, agreed=agreed, compared=of, gain=gain, access=access)
        question = say("card_ready", lang, asks=asks, **fields) if asks else say("card_plain", lang, **fields)
        choices = [{"value": "yes", "label": say("yes", lang)}, {"value": "later", "label": say("later", lang)},
                   {"value": "rest", "label": say("rest", lang)}]
        return _no_media(question + (f"\n{writes}" if writes else "")), choices

    def cockpit(self: Habits) -> str:
        """/habits: the list, then up to 3 cards. The list goes first, so it stands above the cards: posted, or on
        top of the first card where a message cannot be posted (the plugin host). With no buttons (an older
        Hermes, a cron job, a surface that cannot ask) each decision is a line of text commands."""
        rows = self.rows()
        text, waiting = self.grouped(rows), self.waiting(rows)[:CARDS]
        if not waiting:
            return text
        ask = getattr(self.ctx, "ask_choice", None)
        head = "" if ask is not None and self._post(text) else text  # the list, while it is still to show
        said = []
        for at, (habit, summary, s) in enumerate(waiting):
            picked = None
            if ask is not None:
                question, choices = self.card(habit, summary, s)
                try:
                    picked = ask(f"{head}\n\n{question}" if head else question, choices)
                except Exception as exc:  # a surface that broke: the text commands still say what to do
                    logger.warning("habits: could not ask about %s: %s", habit.name, exc)
            if picked is None:  # no buttons here (each card as text, then its commands), or no answer: the commands
                said = ([head] if head else []) + said + [
                    (f"{self.card(h, sm, w)[0]}\n" if ask is None else "")
                    + say("ask_ready", self.lang(), name=h.name)
                    for h, sm, w in waiting[at:]]
                break
            head = ""  # it went out with the first card
            said.append(self.decide(picked, habit.name, self.version(habit)))
        return "\n".join(said)

    def _post(self: Habits, text: str) -> bool:
        """Post the list into this chat; False when it cannot be (no gateway, or the plugin host, which refuses
        post_message)."""
        post = getattr(self.ctx, "post_message", None)
        if post is None:
            return False
        try:
            return bool(getattr(post(text), "ok", False))
        except Exception as exc:
            logger.info("habits: the list goes with the first card: %s", exc)
            return False

    def decide(self: Habits, picked: str, name: str, version: str | None = None) -> str:
        """A card's answer, bound to the version the card showed."""
        if picked == "yes":
            return self.yes(name, version)[1]
        if picked == "rest":  # a no holds whatever changed meanwhile: the habit rests, its new version too
            return self.rest(name)
        return self.again(name, version)  # later on a ready habit

    # ---- the verbs -----------------------------------------------------------

    def verb(self: Habits, verb: str, name: str = "", session: str = "", word: str = "") -> str:
        lang = self.lang()
        if verb == "pause":
            self.pause(True)
            return say("paused", lang)
        if verb == "resume":
            self.pause(False)
            return say("resumed", lang, **self.counts(self.rows()))
        if verb == "wrong":
            name = self.ledger.wrong(session, by="user") if session else None
            return say("after_wrong", lang, name=name) if name else say("wrong_none", lang)
        if verb == "undo":
            return self.undone(self.ledger.undo(name or None, by="user"))
        if not name:  # the one decision waiting, when there is only one
            waiting = self.waiting()
            if not waiting and verb in ("yes", "try"):
                return say("nothing_waits", lang)
            if len(waiting) != 1:
                return say("which", lang, verb=word or verb)
            name = waiting[0][0].name
        if verb == "yes":  # typed, with no card: no version was shown, so only one that is ready now
            return self.yes(name, ready=True)[1]
        return self.rest(name) if verb == "rest" else self.again(name)

    def _decidable(self: Habits, name: str):
        """(the habit, None), or (None, why there is nothing to decide)."""
        lang = self.lang()
        habit = catalog.find(name)
        if habit is None:
            return None, say("unknown", lang, name=name)
        if habit.origin == "example":  # it runs by name only: nothing to decide
            return None, say("example_byname", lang, name=habit.name)
        if not habit.valid:
            return None, say("invalid", lang, name=habit.name)
        if habit.kind == "one-off":
            return None, say("byname", lang, name=habit.name)
        return habit, None

    def yes(self: Habits, name: str, version: str | None = None, by: str = "user",
            ready: bool = False) -> tuple[bool, str]:
        """The user's yes: active, if it is still the version they saw, in shadow (compare-and-set). by: user in a
        chat, cli in a terminal. ready: a yes typed in a chat names no version, so it takes one only once it is ready
        (the ready line it answers may be a version's ago); the terminal's `hermes habits promote` is the user's own,
        ready or not, but for a habit Hermes made: the model has a terminal too."""
        habit, why = self._decidable(name)
        if habit is None:
            return False, why
        lang, now = self.lang(), habit.version
        if by == "cli" and (self.ledger.info(habit.name) or {}).get("origin") in MADE:
            return False, say("promote_made", lang, name=habit.name)
        status = self.ledger.status(habit.name, now)  # a file changed on disk is a new version, in shadow
        if version is not None and version != now:
            return False, say("changed", lang, name=habit.name)
        if status == "active":
            return False, say("active_already", lang, name=habit.name)
        if status == "retired":
            return False, say("resting", lang, name=habit.name)
        if ready and not (self.ledger.info(habit.name) or {}).get("ready"):
            return False, say("learning_already", lang, name=habit.name, compared=self.summary(habit)["compared"],
                              runs=self.setting("promote_runs"))
        if not self.ledger.approve(habit.name, now, by=by):
            return False, say("changed", lang, name=habit.name)  # it moved between the read and the yes
        spot = self.setting("spot_check")
        if spot <= 0:
            return True, say("after_yes_unchecked", lang, name=habit.name)
        left = CLOSE_CHECKS - self.ledger.agreed(habit.name, now)  # the trigger checks each run until 3 agree
        key = "after_yes" if left <= 0 else "after_yes_close_one" if left == 1 else "after_yes_close"
        return True, say(key, lang, name=habit.name, n=left, every=max(1, round(1 / spot)))

    def again(self: Habits, name: str, version: str | None = None, by: str = "user") -> str:
        """essai: a ready habit keeps learning and is asked again later; a retired one goes on trial, in
        shadow with fresh scores."""
        habit, why = self._decidable(name)
        if habit is None:
            return why
        lang, now = self.lang(), habit.version
        status = self.ledger.status(habit.name, now)
        if version is not None and version != now:
            return say("changed", lang, name=habit.name)
        if status == "active":
            return say("active_already", lang, name=habit.name)
        if status == "shadow":
            if self.ledger.later(habit.name, now):
                runs = max(1, self.setting("promote_runs") // 2)
                return say("after_later_one" if runs == 1 else "after_later", lang, runs=runs)
            return say("learning_already", lang, name=habit.name, compared=self.summary(habit)["compared"],
                       runs=self.setting("promote_runs"))
        self.ledger.set_status(habit.name, now, "shadow", "on trial, said the user", by=by)
        return say("after_try", lang, name=habit.name)

    def rest(self: Habits, name: str) -> str:
        """repos: retired by the user."""
        habit = catalog.find(name)
        if habit is None:
            return say("unknown", self.lang(), name=name)
        if habit.origin == "example":
            return say("example_byname", self.lang(), name=habit.name)
        now = self.version(habit)
        status = self.ledger.status(habit.name, now) if habit.valid else None
        if status != "retired":
            self.ledger.set_status(habit.name, now, "retired", "put to rest by the user", by="user")
        return say("after_rest", self.lang(), name=habit.name)

    def undone(self: Habits, row: dict | None) -> str:
        lang = self.lang()
        if row is None:
            return say("undo_none", lang)
        note = str(row.get("note") or "")
        change = ("undo" if note == "undone" else "yes" if row["to"] == "active" else "rest" if row["to"] == "retired"
                  else "wrong" if note.startswith("wrong") else "try" if row["to"] == "shadow" else "other")
        state = row["from"] if row["from"] in ("active", "shadow", "retired") else "retired"  # an older version's state
        said = say("after_undo", lang, name=row["habit"], state=say(f"state_{state}", lang),
                   change=say(f"change_{change}", lang), time=time.strftime("%H:%M", time.localtime(row["at"])))
        left = CLOSE_CHECKS - self.ledger.agreed(row["habit"], row["version"]) if row["from"] == "active" else 0
        return said + (say("undo_close_one" if left == 1 else "undo_close", lang, n=left) if left > 0 else "")

    def pause(self: Habits, on: bool):
        """The paused file: the trigger and the watches' checks stay silent while it is there. The user's is empty:
        a bench that paused habits for a create writes its own id there, and lifts only a pause still its own."""
        path = self.home() / "paused"
        if on:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)

    # ---- the prompt section ----------------------------------------------------

    def prompt(self: Habits, info=None) -> str:
        """The habits section of a new session's system prompt: names and states, the rules, and for the curator the
        habits to leave alone. "" when Habits is off or anything fails: Hermes then leaves the section out."""
        try:
            if self.off():
                return ""
            return self._prompt(dict(info or {}))
        except Exception as exc:
            logger.warning("habits: no prompt section: %s", exc)
            return ""

    def reflexes(self: Habits) -> list[str]:
        """One line per reflex the model may hear of, callable ones first: name(inputs) · state · description.
        An invalid habit cannot run (/habits shows it); a shipped example is listed only when it only reads."""
        return [line for _, _, line in self._reflexes()]

    def callable_names(self: Habits) -> list[str]:
        """The reflexes the prompt tells the model to call (automatic, call it), in its order: what an unknown name's
        reply lists, never a shipped example it leaves out on purpose."""
        return [name for state, name, _ in self._reflexes() if state in (REFLEX["active"], REFLEX["byname"])]

    def _reflexes(self: Habits) -> list[tuple[str, str, str]]:
        """(state as the model reads it, name, line) for each reflex of the prompt, in its order."""
        found = []
        for habit in catalog.all_habits():
            if not habit.valid:
                continue
            if habit.origin == "example":
                if read_only(habit.graph):  # the plugin's own text, read-only: it runs live by name
                    found.append((REFLEX["byname"], 1, habit.name, f"- {signature(habit)} · call it",
                                  _described(habit.description)))
                continue
            status = self.ledger.status(habit.name, habit.version)
            row = self.ledger.info(habit.name) or {}
            state = state_of(habit, {"status": status, "ready": row.get("ready") is not None})
            if state == "byname" and self.unconfirmed(habit, row, status):
                state = "shadow"  # by name it would only run dry: the model does it itself
            if state not in REFLEX:
                continue
            told = _described(habit.description) if self.told_of(habit, row, status) else ""
            found.append((REFLEX[state], 0, habit.name, f"- {signature(habit)} · {REFLEX[state]}", told))
        found.sort(key=lambda f: (REFLEX_ORDER.index(f[0]), f[1], f[2]))
        return [(state, name, line + (f" · {told}" if told else "")) for state, _, name, line, told in found]

    def _prompt(self: Habits, info: dict) -> str:
        if info.get("platform") == "curator":  # the curator needs the names to leave alone, not the relay
            habits = [h for h in catalog.all_habits() if h.origin != "example"]
            if not habits:
                return ""
            room = PROMPT_CHARS - len(PROMPT_TAIL) - len(PROMPT_CURATOR) - 4
            names = ""
            for habit in habits:
                if len(names) + len(habit.name) + 2 > room:
                    names += ", …"
                    break
                names += (", " if names else "") + habit.name
            return "\n".join([PROMPT_TAIL, PROMPT_CURATOR + names])
        lines = self.reflexes()
        own = [] if any(h.origin != "example" for h in catalog.all_habits()) else [PROMPT_NONE_OWN]
        head = "\n".join([PROMPT_INTRO, *PROMPT_WHEN])
        if not lines:  # no reflex to list (no example shipped either): the relay, and how to make one
            return "\n".join([head, *own, PROMPT_TAIL])
        budget = PROMPT_CHARS - len(head) - len(PROMPT_LIST) - len(PROMPT_TAIL) - sum(len(o) + 1 for o in own) - 3
        shown, used = [], 0
        for at, line in enumerate(lines[:PROMPT_HABITS]):
            rest = len(lines) - at - 1
            more = len("\n" + PROMPT_MORE.format(n=rest)) if rest else 0
            if used + len(line) + 1 + more > budget:
                break
            shown.append(line)
            used += len(line) + 1
        if len(shown) < len(lines):
            shown.append(PROMPT_MORE.format(n=len(lines) - len(shown)))
        return "\n".join([head, PROMPT_LIST, *shown, *own, PROMPT_TAIL])

    def told_of(self: Habits, habit: catalog.Habit, row: dict, status: str) -> bool:
        """The model reads a habit's description only when the user asked for the habit or said yes to it (in a
        chat or a terminal): a habit nobody asked for may hold any text, and the prompt is high trust."""
        if row.get("origin") == "asked":  # a habit seen with no save is no one's ask
            return True
        return status == "active" and self.ledger.vouched(habit.name)

    # ---- news: what happened that nobody was told yet -----------------------------

    def news_line(self: Habits, row: dict) -> str:
        """One piece of news as the user reads it, in the language setting."""
        lang, name, kind = self.lang(), row["habit"], row["kind"]
        if kind == "ready":
            agreed, of = self.ledger.tally(name, row["version"])
            return say("ready", lang, name=name, gain=self.gain(name, row["version"]), agreed=agreed, n=of)
        if kind in ("retired", "demoted"):
            return say(kind, lang, name=name, why=why(row.get("note"), lang))
        return f"{name}: {row.get('note') or kind}"  # a kind an older version wrote

    def gain(self: Habits, name: str, version: str) -> str:
        """ · 1.8 s instead of 19 s: the habit's own run against the model's turn, when both were measured."""
        slow, _ = self.ledger.gain(name, version)
        runs = sorted(r["ms"] for r in self.ledger.runs(name, 50) if r["version"] == version and r["ms"])
        if not slow or not runs:
            return ""
        lang = self.lang()
        return say("gain", lang, fast=seconds(runs[len(runs) // 2], lang), slow=seconds(slow, lang))

    def news(self: Habits, names=None) -> list[dict]:
        """The news nobody was told of yet, without taking it, of the habits there now (a habit deleted takes its
        news with it); [] when the ledger cannot say."""
        try:
            rows = self.ledger.news(names, take=False)
        except Exception as exc:
            logger.warning("habits: could not read the news to tell: %s", exc)
            return []
        there = {h.name for h in catalog.all_habits()} if rows else set()
        return [r for r in rows if r["habit"] in there]

    def tell(self: Habits, names=None) -> int:
        """Post each untold piece of news of these habits into the conversation bound to this context, at the
        moment it happens: a compare thread carries its turn's context. A post that fails (no gateway, no
        conversation) leaves it for the next reply. Returns how many went out."""
        post = getattr(self.ctx, "post_message", None)
        if not callable(post):
            return 0
        told = 0
        for row in self.news(names):
            try:
                handle = post(self.news_line(row))
            except Exception as exc:
                logger.info("habits: could not post the news of %s: %s", row["habit"], exc)
                break
            # Still being sent (the gateway's wait ran out, the send goes on): it lands, so it is told.
            sending = getattr(handle, "pending", False) or getattr(handle, "error", None) == "timeout"
            if not getattr(handle, "ok", False) and not sending:
                logger.info("habits: news of %s left for the next reply (%s)", row["habit"],
                            getattr(handle, "error", None))
                break
            told += len(self.ledger.told([row["id"]]))
        return told
