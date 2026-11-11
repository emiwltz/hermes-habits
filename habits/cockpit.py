"""The /habits cockpit and the prompt section: where each habit stands, for the user to decide and for the model to know.

/habits lists the habits by state, then asks the decisions waiting for the user (a ready habit: let it answer on its
own?) with buttons where the chat has them (ask_choice), as text commands otherwise. Only the user's word changes a
state here, and a yes is bound to the version its card showed: the ledger takes it as a compare-and-set. The model is
told names and states, never the text of a habit the user did not ask for, and that it never changes a state itself.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING

from . import catalog, graph, runner
from .display import _no_media
from .ledger import MADE, VOUCHERS
from .practice import RANK
from .texts import count, language, percent, say, seconds, why
from .tools import _strings
from .trigger import CLOSE_CHECKS, read_only, reads_all, takes_message

if TYPE_CHECKING:  # the class these methods are mixed into
    from .plugin import Habits

logger = logging.getLogger(__name__)

# The chat verbs, French and English alike (the language setting only picks the texts), and the CLI's words.
VERBS = {"oui": "yes", "yes": "yes", "promote": "yes", "essai": "try", "try": "try", "trial": "try", "repos": "rest",
         "rest": "rest", "faux": "wrong", "wrong": "wrong", "pause": "pause", "reprends": "resume", "resume": "resume",
         "annule": "undo", "undo": "undo", "non": "no", "no": "no", "rêve": "dream", "reve": "dream", "dream": "dream"}
NEWS_OF_THE_DREAM = {"dream_learn": "news_learn", "dream_learned": "news_learned_auto",
                     "dream_proposal": "news_proposal"}  # the news kinds the dream writes, and their texts
ORDER = ("active", "shadow", "ready", "retired", "byname", "invalid")  # ━ ┄ ❓ ─ ◇ ◇
LINES = 8  # habits listed before +N: a phone screen
CARDS = 3  # decisions asked at once
MORE_NAMES = 8  # names listed for the decisions that wait beyond them
SHOWN = "/shown"  # the ledger's memory rows of the cards whose text went out (no habit can have this name)
SHOWN_MINUTES = 10  # a card that went out in another session still counts this long: a one-shot chat is one message a session
PROMPT_CHARS = 2400  # registered as max_chars: Hermes skips a section that is over, it never cuts one
PROMPT_HABITS = 12
DESCRIPTION_CHARS = 60
SIGNATURE_INPUTS = 3  # inputs shown before "…"
INPUT_CHARS = 20  # an input name is the habit author's word: cut, like a description
STEP_CHARS = 80
# A path or a long token in a description stays out of the prompt: it is the user's, and the prompt is high trust.
PATHLIKE = re.compile(r"(?<![\w/])(?:~|\$HOME|\.{1,2})?/[\w.~-]+(?:/\S*)?|\b[\w-]*[A-Za-z][\w-]*\d[\w-]{14,}\b|\b\d[\w-]*[A-Za-z][\w-]{14,}\b")

# A reflex's state as the model reads it: what it may do with it. Mirrors what habit_run does by name: learning or
# resting, a habit that starts on its own runs as a test and hands the task back whole; automatic or "call it", it runs live.
LEARNING = "learning · do this one yourself for now"
REFLEX = {"active": "automatic", "byname": "call it", "shadow": LEARNING, "ready": LEARNING, "retired": "resting · do it yourself"}
PAUSED = "paused · do it yourself"  # what the list says of an automatic reflex while the user's pause is on
REFLEX_ORDER = ("automatic", "call it", LEARNING, "resting · do it yourself")
# The exact forms of the call, here: the model uses dry, habits and message without loading the tool's description.
# Hermes keeps every plugin tool behind three bridge tools by default (tools.tool_search.enabled: auto; tool_search,
# tool_describe, tool_call): habit_run is then not in the model's tool list, and a call by its own name fails ("Tool
# 'habit_run' does not exist"). The session info a section is given does not say which it is, so the section gives
# both calls. The bridge's shape is Hermes's own (tools/tool_search.py): calls is a list of {name, arguments}, with
# exactly one entry for a tool of this machine, and a call whose arguments are known needs no tool_describe.
# {…} stands for what any habit_run {…} of the section holds: one sentence covers the call by name, message, habits
# and dry (several reflexes stay one habit_run, so one entry).
PROMPT_BRIDGE = (
    "Not in your tool list? tool_call {calls: [{name: \"habit_run\", arguments: {…}}]} with the same {…}: one entry, "
    "no tool_describe first.")
PROMPT_INTRO = (
    "A reflex does a known task in a second.\n"
    "Call one: habit_run {name, inputs}. " + PROMPT_BRIDGE + " It comes back done (trust it: use its result as it is, "
    "do not redo or re-check it) or handed back (do only what `left` lists). One that lists message: "
    "{name, message: \"<the user's words>\"}.\n"
    "When:")
PROMPT_WHEN_SEVERAL = (
    "- several reflexes for one request, different reflexes too: ONE call, not one each: "
    "habit_run {habits: [{name: hermes-health}, {name: check-links, inputs: {page: …}}]};")
# Shortened on 11 November to make room for the bridge's call (the section is skipped whole past its budget): the
# closing reflex joined the first line, and no instruction left.
PROMPT_WHEN = (
    "- a step of your plan matches a reflex below: call it, continue from its values; a closing one (sync, tidy) last;",
    PROMPT_WHEN_SEVERAL,
    "- many closed decisions (sort, triage, classify 30 items): write a one-off reflex instead of delegating;",
    "- to test without writing: habit_run {file: \"<path>\", inputs, dry: true}.",
)
PROMPT_LIST = "Your reflexes (name(inputs) · state · what for). Each replaces typing its command:"
PROMPT_TAIL = (
    "The dream makes reflexes (listed once automatic); trigger: skill starts one with its skill. States are the "
    "user's (/habits): never change one yourself (no `hermes habits` command, no write in plugin-data/habits). One "
    "got it wrong: tell the user /habits wrong. To change one: habit_save again. Never patch, merge or delete a skill "
    "that holds assets/reflex.yml, in a review or curation too.")
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
    # A reflex the dream learned, or one that starts on its skill, has a way back like any habit's (learning,
    # automatic, resting): never "called".
    if (habit.kind == "one-off" and summary.get("status") != "retired" and summary.get("origin") != "learned"
            and not graph.starts_on_skill(habit.graph)):
        return "byname"
    if summary.get("status") == "shadow" and (summary.get("ready") or summary.get("unseen")):
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
            rows.append((habit, summary, self.where(habit, summary)))
        return rows

    def where(self: Habits, habit: catalog.Habit, summary: dict) -> str:
        """state_of, once it knows whether the version on disk is one the user was shown or said yes to."""
        summary["unseen"] = self.unseen(habit, summary)
        return state_of(habit, summary)

    @staticmethod
    def copied(habit: catalog.Habit, origin) -> bool:
        """A reflex with no start of its own that the plugin did not save: the user copied it into their skills by
        hand. Hermes calls it by name only after the user's yes (the plugin saved the others itself, or learned them)."""
        return (habit.origin != "example" and habit.kind == "one-off" and not graph.starts_on_skill(habit.graph)
                and origin not in ("asked", "learned"))

    def unseen(self: Habits, habit: catalog.Habit, summary: dict) -> bool:
        """A learned reflex, or a start on a skill that writes, waits in shadow on a version the user has not said yes
        to: the model may have saved it again under a name they trust. Its card must be shown before a yes (and the
        yes then covers that version alone)."""
        try:
            if not habit.valid or summary.get("status") != "shadow" or summary.get("ready") or habit.origin == "example":
                return False
            if not (summary.get("origin") == "learned" or self.copied(habit, (self.ledger.info(habit.name) or {}).get("origin"))
                    or (graph.starts_on_skill(habit.graph) and not reads_all(habit.graph))):
                return False
            return not any(r["version"] == habit.version and r["to"] == "active" and r["by"] in VOUCHERS
                           for r in self.ledger.history(habit.name, 200))
        except Exception as exc:  # fails closed: the card is shown
            logger.warning("habits: could not tell whether %s was shown: %s", habit.name, exc)
            return True

    def counts(self: Habits, rows) -> dict:
        lang, states = self.lang(), [s for _, _, s in rows]
        return {"active": count("active", states.count("active"), lang),
                "learning": count("learning", states.count("shadow") + states.count("ready"), lang)}

    # ---- the list and its cards ------------------------------------------

    def grouped(self: Habits, rows, cli: bool = False) -> str:
        lang = self.lang()
        # what practices, what waits: in a terminal every draft by name and state, then the cards that ask
        dreaming = (self.draft_lines(lang) + self.waiting_lines(lang, names=False)) if cli else self.dream_lines(lang)
        if not rows:
            return "\n".join([self.empty(cli, bool(dreaming))] + dreaming)
        lines = [self.line(habit, summary, s) for habit, summary, s in sorted(rows, key=lambda row: ORDER.index(row[2]))]
        head = [say("head", lang, **self.counts(rows))] + ([say("paused_head", lang)] if self.paused() else [])
        if len(lines) > LINES:
            more = len(lines) - LINES
            lines = lines[:LINES] + [say("more_one" if more == 1 else "more", lang, count=more)]
        waiting = sum(1 for _, _, s in rows if s == "ready")
        return "\n".join(head + lines + dreaming + ([count("decisions", waiting, lang)] if waiting else []))

    def empty(self: Habits, cli: bool = False, drafts: bool = False) -> str:
        """No habit of one's own: the shipped reflexes that only read (they run live by name), and one to try. When
        drafts stand below, "no habits yet" is not said above them: they are what there is."""
        lang = self.lang()
        shipped = [h.name for h in catalog.examples() if h.valid and read_only(h.graph)]
        # in a terminal the first line also names the dream: it shows what comes back in the history
        writing = [h.name for h in catalog.examples() if h.valid and not read_only(h.graph)]  # the same list `run` knows
        lines = ([] if drafts else [say("list_empty" if cli else "none", lang)]) + (
            [say("paused_head", lang)] if self.paused() else []) + (
            [say("none_shipped", lang, names=", ".join(shipped))] if shipped else []) + (
            [say("none_shipped_writes", lang, names=", ".join(writing))] if writing else [])
        if "hermes-health" in shipped:
            lines.append(say("none_try_cli" if cli else "none_try", lang))
        return "\n".join(lines)

    def line(self: Habits, habit, summary, s) -> str:
        lang, name = self.lang(), habit.name
        if s == "active":
            gain = say("gain", lang, fast=seconds(summary["active_ms"], lang), slow=seconds(summary["shadow_ms"], lang)) if (
                summary.get("active_ms") and summary.get("shadow_ms")) else ""
            return say("line_active", lang, name=name, gain=gain)
        if s == "byname" and habit.origin != "example" and self.copied(habit, (self.ledger.info(name) or {}).get("origin")) \
                and summary.get("status") != "active":  # copied by hand: Hermes calls it only after the user's yes
            return say("line_byname_wait", lang, name=name)
        if s == "shadow" and habit.kind == "one-off":  # nothing runs it beside Hermes: only a yes moves it
            made = (self.ledger.info(name) or {}).get("origin") in MADE  # only a chat's yes can move a reflex Hermes made
            return say("line_held_chat" if made else "line_held", lang, name=name)
        if s == "shadow":
            numbers = dict(name=name, compared=summary.get("compared", 0), runs=self.setting("promote_runs"))
            if summary.get("agreement") is not None:
                return say("line_shadow", lang, extra=say("right", lang, percent=percent(summary["agreement"], lang)),
                           **numbers)
            return say("line_shadow", lang, extra="", **numbers)
        if s == "ready" and summary.get("unseen"):
            return say("line_unseen", lang, name=name)
        if s == "ready":
            agreed, of = self.ledger.tally(name, self.version(habit))
            return say("line_ready", lang, name=name, agreed=agreed, compared=of)
        return say(f"line_{s}", lang, name=name)

    def waiting(self: Habits, rows=None) -> list[tuple[catalog.Habit, dict, str]]:
        """The decisions waiting for the user, the oldest first: ready habits."""
        rows = self.rows() if rows is None else rows
        return sorted((row for row in rows if row[2] == "ready"),
                      key=lambda row: row[1].get("since") or 0)

    def decisions(self: Habits, rows=None) -> list[tuple]:
        """Everything waiting for the user's word, oldest first: (since, "habit", (habit, summary, state)) for a ready
        message habit, (since, "dream", card) for a dream's card (ready draft, try, change, rest)."""
        found = [(row[1].get("since") or 0, "habit", row) for row in self.waiting(rows)]
        try:
            found += [(card["since"], "dream", card) for card in self.dream_cards()]
        except Exception as exc:
            logger.warning("habits: could not read the dream's cards: %s", exc)
        return sorted(found, key=lambda entry: (0 if entry[1] == "habit" else RANK.get(entry[2]["kind"], 1), entry[0]))

    def more_waiting(self: Habits, rest: list, limit: int | None = MORE_NAMES) -> list[str]:
        """The decisions that do not fit in the three asked at once, named in one line ([] when none is left). A chat
        names the first few; a terminal (limit None) names them all."""
        names = [row[0].name if kind == "habit" else row["name"] for _, kind, row in rest]
        if not names:
            return []
        shown = ", ".join(names[:limit]) + (", …" if limit is not None and len(names) > limit else "")
        return [say("more_waiting", self.lang(), count=len(names), names=shown)]

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
        if summary.get("unseen"):
            return self.card_unseen(habit)
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
        flagged = self.flagged(habit)
        listed = f"\n{say('card_flagged', lang, commands=', '.join(f'`{c}`' for c in flagged))}" if flagged else ""
        return _no_media(question + (f"\n{writes}" if writes else "") + listed), choices

    @staticmethod
    def _all_steps(habit: catalog.Habit) -> str:
        """Every step that acts, uncut, in order: the command of a terminal step, a tool and its arguments, a reflex it
        uses. What a yes to this version covers."""
        found = []
        nodes = habit.graph.get("nodes") if isinstance(habit.graph.get("nodes"), dict) else {}
        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            args = node.get("args") if isinstance(node.get("args"), dict) else {}
            if "use" in node:
                found.append(f"use {node['use']}")
            elif node.get("do") == "terminal" and isinstance(args.get("command"), str):
                found.append(" ".join(args["command"].split()))
            elif "do" in node:
                found.append(f"{node['do']} {json.dumps(args, ensure_ascii=False, sort_keys=True)}".strip())
        return " ; ".join(found)

    def card_unseen(self: Habits, habit: catalog.Habit) -> tuple[str, list]:
        """The card of a version the user has not said yes to: all its steps, the commands that are sensitive or delete,
        and its folder. Showing it is what lets a yes (typed or pressed) approve this version and no other."""
        lang = self.lang()
        flagged = self.flagged(habit)
        listed = f"\n{say('card_flagged', lang, commands=', '.join(f'`{c}`' for c in flagged))}" if flagged else ""
        copied = self.copied(habit, (self.ledger.info(habit.name) or {}).get("origin"))
        question = say("card_copied" if copied else "card_unseen", lang, name=habit.name,
                       steps=self._all_steps(habit) or "-", folder=str(habit.path.parent.parent))
        choices = [{"value": "yes", "label": say("yes", lang)}, {"value": "later", "label": say("later", lang)},
                   {"value": "rest", "label": say("rest", lang)}]
        return _no_media(question + listed), choices

    def _entry(self: Habits, kind: str, payload) -> tuple[str, list]:
        """A waiting decision's question and buttons."""
        if kind == "habit":
            return self.card(*payload)
        return _no_media(payload["question"]), payload["choices"]

    def _entry_ask(self: Habits, kind: str, payload) -> str:
        """The text commands that answer a decision, where the surface has no buttons."""
        lang = self.lang()
        if kind == "habit":
            return say("ask_ready", lang, name=payload[0].name)
        return say("ask_dream_try" if payload["kind"] == "try" else "ask_dream", lang, name=payload["name"])

    # ---- a yes is informed: the card's text went out, or it is not a yes ---------------------------------

    @staticmethod
    def _form(kind: str, payload) -> tuple[str, str]:
        """(the name, the form its card shows): a habit's version, a dream card's id and raw form."""
        if kind == "habit":
            return payload[0].name, f"habit:{payload[0].version}"
        return payload["name"], f"{payload['id']}:{payload.get('kind')}:{payload.get('raw') or ''}"

    def went_out(self: Habits, session: str, kind: str, payload) -> None:
        """The text of this card is in a reply that leaves now: on record, so a typed yes can be an informed one."""
        name, form = self._form(kind, payload)
        try:
            self.ledger.remember(SHOWN, {name: json.dumps({"form": form, "session": str(session or ""),
                                                             "at": self.ledger.clock()})})
        except Exception as exc:  # fails closed: not on record, the card is shown again
            logger.warning("habits: could not note that the card of %s went out: %s", name, exc)

    def was_shown(self: Habits, session: str, kind: str, payload) -> bool:
        """The text of this very card (same version, same form) went out in this conversation: in this session, or
        in the last SHOWN_MINUTES minutes (a one-shot chat opens a session for each message)."""
        name, form = self._form(kind, payload)
        try:
            row = json.loads(self.ledger.memory(SHOWN).get(name) or "{}")
        except Exception as exc:  # fails closed
            logger.warning("habits: could not tell whether the card of %s went out: %s", name, exc)
            return False
        if not isinstance(row, dict) or row.get("form") != form:
            return False
        if session and row.get("session") == str(session):
            return True
        return 0 <= self.ledger.clock() - float(row.get("at") or 0) <= SHOWN_MINUTES * 60

    def cockpit(self: Habits, session: str = "") -> str:
        """/habits: up to 3 decisions, a ready reflex's yes before any proposal to try, then the names of the ones that
        wait beyond them, then the list of habits, last. Where a button answered, the list tops the first question (the
        buttons are at its foot). Where no button answered (an older Hermes, a cron job, a one-shot chat, a surface that
        cannot ask) each decision is its card as text (what the reflex would run, its sensitive or deleting commands, its
        folder, what was seen), then the words to type, and the list stands under them: it is in the reply itself, never
        posted aside where a one-shot chat would not show it."""
        rows = self.rows()
        text, decisions = self.grouped(rows), self.decisions(rows)
        waiting = decisions[:CARDS]
        if not waiting:
            return text
        ask = getattr(self.ctx, "ask_choice", None)
        head = text if ask is not None else ""  # the list, while it is still to show
        said, listed = [], False
        for at, (_, kind, payload) in enumerate(waiting):
            picked = None
            name = payload[0].name if kind == "habit" else payload["name"]
            if ask is not None:
                question, choices = self._entry(kind, payload)
                try:
                    picked = ask(f"{head}\n\n{question}" if head else question, choices)
                except Exception as exc:  # a surface that broke: the text commands still say what to do
                    logger.warning("habits: could not ask about %s: %s", name, exc)
            if picked is None:  # no buttons here, or no answer: each card as text, then the words that answer it
                said += [f"{self._entry(k, p)[0]}\n{self._entry_ask(k, p)}" for _, k, p in waiting[at:]]
                for _, k, p in waiting[at:]:
                    self.went_out(session, k, p)  # this reply carries the card: a typed yes may now answer it
                listed = True
                break
            head = ""  # it went out with the first card
            if kind == "habit":
                said.append(self.decide(picked, payload[0].name, self.version(payload[0])))
            else:  # a dream's card: the answer is bound to the form the card showed
                said.append(self.dream_decide(payload["id"], picked, raw=payload["raw"]))
        return "\n".join(said + self.more_waiting(decisions[CARDS:]) + ([text] if listed else []))

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
            # a name given is enough in a chat that has run nothing yet: the reflex's latest answer is the one
            named = catalog.find(name).name if name and catalog.find(name) is not None else None
            answered = self.ledger.wrong(session, by="user", habit=named) if (session or named) else None
            return say("after_wrong", lang, name=answered) if answered else say("wrong_none", lang)
        if verb == "undo":
            return self.undone(self.ledger.undo(name or None, by="user"))
        if verb == "dream":  # re-read Hermes's history now, whatever the daily rule says
            return self.dream_text(self.dream_now(manual=True)) or say("nothing_waits", lang)
        if not name:  # the one decision waiting, when there is only one
            waiting = self.decisions()
            if not waiting and verb in ("yes", "try", "no"):
                return say("nothing_waits", lang)
            if len(waiting) != 1:
                return say("which", lang, verb=word or verb)
            _, kind, payload = waiting[0]
            name = payload[0].name if kind == "habit" else payload["name"]
        if verb in ("yes", "no", "try"):  # a dream's card answers by the draft's name or the habit it proposes about
            try:
                card = self.dream_card_named(name)
            except Exception as exc:
                logger.warning("habits: could not read the dream's cards: %s", exc)
                card = None
            if card is not None:
                # a yes that makes something act is informed or it is not a yes: its card went out here, or goes out now
                if verb == "yes" and card["kind"] in ("learn", "change") and not self.was_shown(session, "dream", card):
                    self.went_out(session, "dream", card)
                    return f"{card['question']}\n{say('ask_dream_again', lang, name=name)}"
                return self.dream_decide(card["id"], verb, raw=card["raw"])
        if verb == "no":  # a no on a habit that is not a dream's card: it rests
            draft = self.draft_still_open(name)  # but a draft that still practices (or waits) has no card: the no is its own
            if draft is not None:
                return self.dream_decide(f"d:{draft['key']}", "no")
            return self.rest(name)
        if verb == "yes":  # typed, with no card: no version was shown, so only one that is ready now
            return self.yes(name, ready=True, session=session)[1]
        return self.rest(name) if verb == "rest" else self.again(name)

    def _decidable(self: Habits, name: str):
        """(the habit, None), or (None, why there is nothing to decide)."""
        lang = self.lang()
        habit = catalog.find(name)
        if habit is None:
            return None, self.draft_note(name) or say("unknown", lang, name=name)
        if habit.origin == "example":  # it runs by name only: nothing to decide
            return None, say("example_byname", lang, name=habit.name)
        if not habit.valid:
            return None, say("invalid", lang, name=habit.name)
        if (habit.kind == "one-off" and not graph.starts_on_skill(habit.graph)
                and not self.copied(habit, (self.ledger.info(habit.name) or {}).get("origin"))
                and (self.ledger.info(habit.name) or {}).get("origin") != "learned"):
            return None, say("byname", lang, name=habit.name)  # the plugin saved it: it runs by name at once
        return habit, None

    def yes(self: Habits, name: str, version: str | None = None, by: str = "user",
            ready: bool = False, session: str = "") -> tuple[bool, str]:
        """The user's yes: active, if it is still the version they saw, in shadow (compare-and-set). by: user in a
        chat, cli in a terminal. ready: a yes typed in a chat names no version, so it takes one only once it is ready
        (the ready line it answers may be a version's ago); the terminal's `hermes habits promote` is the user's own,
        ready or not, but for a habit Hermes made: the model has a terminal too."""
        shipped = catalog.find(name)
        if shipped is not None and shipped.origin == "example":  # not the user's: the steps that work today
            return False, say("example_yes", self.lang(), name=shipped.name, path=catalog.EXAMPLES / shipped.name)
        habit, why = self._decidable(name)
        if habit is None:
            return False, why
        lang, now = self.lang(), habit.version
        learned = (self.ledger.info(habit.name) or {}).get("origin") == "learned"
        if by == "cli" and (self.ledger.info(habit.name) or {}).get("origin") in MADE:
            return False, say("promote_made", lang, name=habit.name)
        status = self.ledger.status(habit.name, now)  # a file changed on disk is a new version, in shadow
        if version is not None and version != now:
            return False, say("changed", lang, name=habit.name)
        if version is None and status != "active":  # a typed yes names no version: only one the user was shown
            summary = self.summary(habit)
            unseen = self.unseen(habit, {**summary, "status": "shadow" if status == "retired" else status})
            # a ready habit too: a typed yes after the news line alone has seen no card (what it would run)
            typed = ready and by == "user" and status == "shadow" and not learned and bool(summary.get("ready"))
            if (unseen or typed) and not self.was_shown(session, "habit", (habit,)):
                self.went_out(session, "habit", (habit,))  # the card leaves in this very answer
                card = self.card_unseen(habit)[0] if unseen else self.card(habit, summary, "ready")[0]
                ask = "ask_copied" if self.copied(habit, (self.ledger.info(habit.name) or {}).get("origin")) else "ask_unseen"
                return False, "\n".join([card, say(ask, lang, name=habit.name)])
        copied = self.copied(habit, (self.ledger.info(habit.name) or {}).get("origin"))  # added by hand, called by name
        if status == "active":
            key = "copied_already" if copied else "active_already_called" if habit.kind == "one-off" else "active_already"
            return False, say(key, lang, name=habit.name)
        if status == "retired":
            if not learned:
                return False, say("resting", lang, name=habit.name)
            # A learned reflex put to rest comes back with the user's yes: on trial for an instant, then automatic.
            self.ledger.set_status(habit.name, now, "shadow", "back, said the user", by=by)
        if ready and not learned and not copied and not graph.starts_on_skill(habit.graph) \
                and not (self.ledger.info(habit.name) or {}).get("ready"):  # a skill start is never compared
            return False, say("learning_already", lang, name=habit.name, compared=self.summary(habit)["compared"],
                              runs=self.setting("promote_runs"))
        if not self.ledger.approve(habit.name, now, by=by):
            return False, say("changed", lang, name=habit.name)  # it moved between the read and the yes
        spot = self.setting("spot_check")
        if habit.kind == "one-off":  # called by name: it acts when Hermes calls it, and no run is checked behind
            return True, say("after_yes_copied" if copied else "after_yes_called", lang, name=habit.name)
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
        if habit.kind == "one-off" and status != "active":  # no run is compared for it: only a yes moves it
            if status == "retired":
                self.ledger.set_status(habit.name, now, "shadow", "on hold, said the user", by=by)
            return say("after_try_held", lang, name=habit.name)
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
            return self.draft_note(name) or say("unknown", self.lang(), name=name)
        if habit.origin == "example":
            return say("example_byname", self.lang(), name=habit.name)
        now = self.version(habit)
        status = self.ledger.status(habit.name, now) if habit.valid else None
        if status != "retired":
            self.ledger.set_status(habit.name, now, "retired", "put to rest by the user", by="user")
        return say("after_rest_held" if habit.kind == "one-off" else "after_rest", self.lang(), name=habit.name)

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
        habit = catalog.find(row["habit"])  # only a habit that starts on a message is checked behind
        left = CLOSE_CHECKS - self.ledger.agreed(row["habit"], row["version"]) \
            if row["from"] == "active" and not (habit is not None and habit.kind == "one-off") else 0
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
        An invalid habit cannot run (/habits shows it); a shipped example is listed only when it only reads and does not
        start on a schedule."""
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
                # The plugin's own text that only reads and has no start of its own: it runs live by name. One that starts
                # on a schedule is a template for the user to adopt, not a reflex to offer a vague "start my day" to.
                if read_only(habit.graph) and habit.kind != "watch":
                    found.append((REFLEX["byname"], 1, habit.name, f"- {signature(habit)} · call it",
                                  _described(habit.description)))
                continue
            status = self.ledger.status(habit.name, habit.version)
            row = self.ledger.info(habit.name) or {}
            state = state_of(habit, {"status": status, "ready": row.get("ready") is not None,
                                     "origin": row.get("origin")})
            if state == "byname" and self.unconfirmed(habit, row, status):
                state = "shadow"  # by name it would only run dry: the model does it itself
            if state not in REFLEX:
                continue
            told = _described(habit.description) if self.told_of(habit, row, status) else ""
            word = PAUSED if state == "active" and self.paused() else REFLEX[state]  # paused: nothing starts by itself
            found.append((REFLEX[state], 0, habit.name, f"- {signature(habit)} · {word}", told))
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
        if kind in NEWS_OF_THE_DREAM:
            return say(NEWS_OF_THE_DREAM[kind], lang, name=name)
        return f"{name}: {row.get('note') or kind}"  # a kind an older version wrote

    def gain(self: Habits, name: str, version: str) -> str:
        """ · 1.8 s instead of 19 s: the habit's own run against the model's turn, when both were measured."""
        slow, _ = self.ledger.gain(name, version)
        runs = sorted(r["ms"] for r in self.ledger.runs(name, 50) if r["version"] == version and r["ms"])
        if not slow or not runs:
            return ""
        lang = self.lang()
        return say("gain", lang, fast=seconds(runs[len(runs) // 2], lang), slow=seconds(slow, lang))

    def news(self: Habits, names=None, daily: bool = False) -> list[dict]:
        """The news nobody was told of yet, without taking it, of the habits there now (a habit deleted takes its
        news with it) and of the drafts of the dream; [] when the ledger cannot say. daily: nothing once a line
        was told since local midnight (a reply tells one line a day)."""
        try:
            rows = self.ledger.news(names, take=False, daily=daily)
            drafts = {d["name"] for d in self.ledger.drafts() if d.get("name")} if rows else set()
        except Exception as exc:
            logger.warning("habits: could not read the news to tell: %s", exc)
            return []
        there = ({h.name for h in catalog.all_habits()} if rows else set()) | drafts
        return [r for r in rows if r["habit"] in there and self._news_holds(r)]

    def _news_holds(self: Habits, row: dict) -> bool:
        """The dream's news is told only while it is still true: "practiced well" while that draft waits for the yes, "a
        proposal waits" while it is open. What stopped being true is deleted, not told."""
        kind, name = row["kind"], row["habit"]
        try:
            if kind == "dream_learn":
                holds = any(d["state"] == "ready" and d.get("name") == name for d in self.ledger.drafts())
            elif kind == "dream_proposal":
                holds = any(p["subject"] == name for p in self.ledger.proposals())
            else:
                return True
            if not holds:
                self.ledger.withdraw_news(name, (kind,))
            return holds
        except Exception as exc:
            logger.warning("habits: could not check the news of %s: %s", name, exc)
            return True

    def told_today(self: Habits) -> bool:
        """A line of news went out since local midnight; a ledger that cannot say counts as none."""
        try:
            return bool(self.ledger.told_today())
        except Exception as exc:
            logger.warning("habits: could not read the day's news: %s", exc)
            return False

    def tell(self: Habits, names=None) -> int:
        """Post each untold piece of news of these habits into the conversation bound to this context, at the
        moment it happens: a compare thread carries its turn's context. A post that fails (no gateway, no
        conversation) leaves it for the next reply. Returns how many went out."""
        post = getattr(self.ctx, "post_message", None)
        if not callable(post):
            return 0
        told = 0
        for row in self.news(names):
            if self.told_today():  # a line a day, the ones posted at once included
                break
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
