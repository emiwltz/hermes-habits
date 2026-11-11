"""Request habits: the turn hooks that pick a habit for a user message, confirm it and run it before the
model, then measure the turn (its tokens and calls, and while learning the model's own steps against the plan, or
its reply against the answer of a habit that only reads).
"""

from __future__ import annotations

import logging
import random
import re
import sys
import time
import urllib.parse

from . import catalog, deciders, graph, replay, runner, shell, texts
from .tools import REPAIR, _json, _strings, _untrusted, attachments, brief, left_of, next_of, read_outside, with_files

logger = logging.getLogger(__name__)

HOOK_SECONDS = 20  # Hermes abandons a hook after 30 s
TRIGGER_CHARS = 2000
SAID = 200  # sessions whose last user message is kept, for habit_save
SILENT = "[SILENT]"  # a cron job's reply that delivers nothing
# The replies the gateway keeps to itself (gateway/response_filters.py LIVE_GATEWAY_SILENT_MARKERS), and [SILENT]
# opening a short note: a news line in one never reached the user.
QUIET = {"[SILENT]", "SILENT", "NO_REPLY", "NO REPLY"}
MACHINERY = ("cron", "curator")  # their turns are no user's: no news told


def silent(reply: str) -> bool:
    said = reply.strip()
    return not said or said.upper().startswith(SILENT) or said.strip(" .!").upper() in QUIET
MODEL_FRESH = 600  # seconds a model answer stays proof that the model answers
# An automatic habit with fewer runs that agree with the model is checked on every run, then 1 in 10. A habit
# saved active has none; a promoted one has its shadow runs. A status habit created in a trial would have reported a
# problem about an error 19 h old from its first day, on 1 check in 10.
CLOSE_CHECKS = 3
# Asked at habit_save of a read-only request habit: yes, and it starts active.
ASKED = ("Does this message ask to keep a task as a habit (a routine, a shortcut, an automation), or to remember "
         "how to do it next time?")


def ignored(text: str) -> bool:
    """A message no habit takes: empty, a command, or longer than what the decision model reads (its "but not…" could be
    past the cut: the model takes it)."""
    return not text or text.startswith("/") or len(text) > TRIGGER_CHARS


def _text(message) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return " ".join(p.get("text", "") for p in message if isinstance(p, dict) and p.get("type") == "text")
    return ""


def can_answer() -> bool:
    """True when this Hermes ends a turn on a pre_llm_call block with its message (not 0.22.5):
    an active habit that did the whole task then answers without any model call."""
    module = sys.modules.get("agent.turn_context")
    return module is not None and hasattr(module, "PRE_LLM_BLOCK_EXIT_REASON")


# How the gateway quotes the message a user answers: the whole text, which can span lines (a direct answer does).
REPLY = re.compile(r'^\[Replying to[^:\n]*: ".*?"\]\n\n', re.S)


def named(name: str, text: str) -> bool:
    """The habit's name written as a whole word in the text (names hold - or _, so plain words never match)."""
    return ("-" in name or "_" in name) and bool(
        re.search(rf"(?<![\w-]){re.escape(name.lower())}(?![\w-])", text))


# A literal path or URL in a tool argument: quoted (spaces allowed), or a bare word; a {template} ends it.
TARGET = re.compile(r"""(?:'|")((?:https?://|~?/)[^'"{}]*)(?=['"{])"""
                    r"""|(?:^|(?<=[\s=(]))((?:https?://|~?/)[^\s'"{}()<>|;&]*)""")
# A choice, not a yes/no: a yes/no followed by its list of no cases made Jev say no to plain requests
# (8 of 16 demo messages right); four named cases gave 15 and 16 of 16 (Mac and Pi demo habits).
CONFIRM = 'What does this message ask about the saved task "{task}"?'
# "…or only ask or talk about it" caught every status question ("How is the backup doing?"): run at 0.54-0.68 on
# Jev, under 0.8, so a habit that answers a question never ran (two status trials). "…or a question about the saved
# habit itself" still gave 0.75-0.77; the plain not_now 0.89-0.98, with the 16 demo messages still right but one (15 of
# 16). The decision model sees only the paths a habit acts on, so the place a message names ("in the
# dashboard") read as another way: "How is the bakcup doing in the dashboard?" got run at 0.71-0.74. Saying that a
# place is no change gave 0.92-0.94, "How is the backup doing?" 0.85-0.90 (0.77-0.83), and no trap of the two status
# trials came nearer a gate that held it.
# Not taken: "changed" as "another place than where it acts" took a note-sending ask to 0.64-0.71; a not_now the
# message must say lifted a typo-ridden ask (0.82-0.86) to 0.89, and the "??" ping's confirmation over its gate.
# Not taken: a fifth case, for a message that names no subject or another one (5 wordings, 3 runs each
# on Jev). The four that held "is it stuck?" (run 0.89-0.92 before, 0.22-0.66 after) and "dashboard OK?"
# (0.82-0.83, then 0.43-0.69) each held a plain ask under 0.8 too, in 1 to 3 runs of 3: "How is the backup doing?",
# "How is the bakcup doing in the dashboard?" or "why no answer?". The mildest, "It names no subject at all, or a
# subject this task is not about", ran every ask, but left "any news?" at 0.82-0.88, the two others 0.06-0.13 under
# the gate, and "How is the bakcup doing in the dashboard?" at 0.81-0.87.
CONFIRM_CASES = (
    ("run", "Run it now as it is: any wording, typo or language; the message need not name every step; saying where "
            "it is (an app, a vault, a folder, even misheard) is no change"),
    ("changed", "Run it differently: skip part of it, another folder, file or page, or another way, order or rule"),
    ("more", "Run it and also do something else"),
    ("not_now", "Not now: do not run it, or run it later"),
)
# A habit that takes what to act on from the message (a trial: which note to send): naming it is the task, not a
# change. Every trial ask named its note, and "another folder, file or page" made Jev say changed (0.61-0.75); with
# these cases it says run (0.71-0.86), and "more" keeps a second thing to answer ("Friday works for the plumber.")
# for the model. Where the note is ("in the notebook") is no change either: 0.88-0.93
# (0.81-0.87). Alone, that also raised the two-part message (a reply plus a request) to 0.80-0.84, over the gate; a
# "more" that names a reply about another matter holds it at 0.61-0.65 (0.73-0.77), with the plain asks at 0.86-0.93
# (alone, it took them under 0.85).
NAMED = "the message names which one"
NAMED_CASES = {
    "run": "Run it now as it is, on the item the message names: any wording, typo or language; the message need not "
           "name every step; saying where the item is (an app, a vault, a folder, even misheard) is no change",
    "changed": "Run it differently: skip part of it, or another way, order or rule",
    "more": "Run it and also do something else, or answer something else the message says or replies to (news, a "
            "reply or a confirmation about another matter)",
}
# Such a habit runs only when the same call's second question answers "nothing" at
# min_confidence or more, as sure as the "run". An earlier, looser gate (0.7) is gone: on a free bench pass "Friday works for
# the plumber. Meanwhile… can you send me the garden note?" got "run" 0.74 and "nothing" 0.69 (over D1's 0.5), so it
# would have sent the note and dropped the reply about the plumber.
ALSO = "Besides asking for the saved task, does this message say anything else that needs an answer or an action?"
ALSO_CASES = (
    ("nothing", "Nothing else: only the request, with its context (where the item is, a greeting, thanks)"),
    ("other", "Something else to answer or do: news, a reply or a confirmation about something else, another "
              "request or question"),
)
READS = "changes nothing"  # item 3a (09/11): "Restart the relay" ran a read-only check at 0.78 against 0.8


def targets(habit: dict, most: int = 3) -> list[str]:
    """The literal paths and URL hosts a habit's do nodes act on, in graph order (~/Downloads/{kind} is
    ~/Downloads): a few, shortened, a folder in place of the paths under it. The decision model sees them, so another folder
    is told apart. A folder it only reads counts when it writes in there, or when the graph writes nowhere named."""
    def words(node):
        args = node.get("args") if isinstance(node.get("args"), dict) else {}
        command = args.get("command") if node.get("do") == "terminal" else None
        # A command's operands, not its sed or jq program ('s/x/FREQUENT/' is no path); text the reader cannot
        # read, and other arguments, by the pattern.
        found = shell.operands(command) if isinstance(command, str) else None
        texts = _strings({k: v for k, v in args.items() if k != "command"} if found is not None else args)
        for text in found or ():
            text = re.split(f"[{shell.EXPANSION}{shell.MARK}]", text)[0]  # a {template} or a $VAR ends it
            yield text.partition("=")[2] if text.startswith("-") else text  # --output=/x
        for text in texts:
            for match in TARGET.finditer(text):
                yield match.group(1) or match.group(2)

    def paths(node):
        for path in words(node):
            if not path.startswith(("http://", "https://", "/", "~/")):
                continue
            if path.startswith(("http://", "https://")):  # its user, path and query stay out: a token hides there
                host = urllib.parse.urlsplit(path).hostname
                path = f"{path.split(':', 1)[0]}://{host}" if host else ""
            path = path.rstrip("/")
            if len(path) >= 2 and not path.startswith("/dev/") and path not in ("~", "/dev"):
                yield path  # not the root, the home folder alone or /dev/null
    nodes = habit.get("nodes") if isinstance(habit.get("nodes"), dict) else {}
    cited = [(path, runner._reads(node)) for node in nodes.values() if isinstance(node, dict) and "do" in node
             for path in paths(node)]
    writes = [path for path, read in cited if not read]
    found = []
    for path, read in cited:
        if read and writes and not any(w == path or w.startswith(path + "/") for w in writes):
            continue  # ls /Applications, to compare: not what it acts on
        if not any(path == kept or path.startswith(kept + "/") for kept in found):
            under = [k for k, kept in enumerate(found) if kept.startswith(path + "/")]
            at = under[0] if under else len(found)  # where its first path stood: the cut keeps what is cited first
            found = [kept for kept in found[:at] if not kept.startswith(path + "/")] + [path] + [
                kept for kept in found[at:] if not kept.startswith(path + "/")]
    return [p if len(p) <= 60 else p[:59] + "…" for p in found[:most]]


def reads_all(habit: dict, depth: int = 0) -> bool:
    """Every do of the graph reads, and so does every sub-habit it uses: a shadow run of it does the whole task,
    so its answer can be judged against the model's reply."""
    nodes = habit.get("nodes") if isinstance(habit.get("nodes"), dict) else {}
    for node in nodes.values():
        if isinstance(node, dict) and "do" in node and not runner._reads(node):
            return False
        if isinstance(node, dict) and "use" in node:
            sub = catalog.find(node["use"]) if isinstance(node["use"], str) and depth < runner.MAX_DEPTH else None
            if sub is None or not sub.valid or not reads_all(sub.graph, depth + 1):
                return False
    return True


def read_only(habit: dict) -> bool:
    """It only reads and sends nothing: no end of it has a MEDIA: line (a file sent cannot be taken back), so
    nothing it does needs undoing."""
    nodes = habit.get("nodes") if isinstance(habit.get("nodes"), dict) else {}
    sends = any(isinstance(node, dict) and runner.MEDIA_LINE.match(line)
                for node in nodes.values() for line in str(node.get("end", "")).splitlines())
    return not sends and reads_all(habit)


def task(habit: catalog.Habit, named: bool = False) -> str:
    """A request habit as the decision model sees it: its description, then what it acts on, or what it only reads and that it
    changes nothing (no write, no file sent), and (named) that the message names which item."""
    where = ", ".join(targets(habit.graph))
    text = habit.description or habit.name
    acts = (f"only reads {where}, {READS}" if where else f"only reads, {READS}") if read_only(habit.graph) else (
        f"acts on {where}" if where else "")
    told = "; ".join(filter(None, [acts, NAMED if named else ""]))
    return f"{text} ({told})" if told else text


def _seen(answer: deciders.Answer, gate: float, passed: bool) -> dict:
    """What a decision-model answer of the start was, for `hermes habits run --message --json`: a near miss shows there."""
    return {"answer": answer.value, "confidence": round(answer.confidence, 3), "probabilities": answer.probabilities,
            "backend": answer.backend, "gate": gate, "passed": passed}


def _odds(answer: deciders.Answer) -> str:
    """Each option's probability, for the log line, when the model gave them."""
    return " [" + ", ".join(f"{k} {v:.2f}" for k, v in answer.probabilities.items()) + "]" if answer.probabilities else ""


def takes_message(habit: dict, depth: int = 0) -> bool:
    """An ask or an llm node of the graph reads the message (with: [trigger], or {trigger} in its question or its
    instruction), or a use runs a habit that does (it hears the same message, as deep as use runs): what to act
    on comes from it."""
    nodes = habit.get("nodes") if isinstance(habit.get("nodes"), dict) else {}
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        text = node.get("ask", node.get("llm"))
        if ("ask" in node or "llm" in node) and ("trigger" in (node.get("with") or []) or isinstance(text, str) and any(
                match.group(1) == "trigger" for match in graph.TEMPLATE.finditer(text))):
            return True
        sub = catalog.find(node["use"]) if isinstance(node.get("use"), str) and depth + 1 < runner.MAX_DEPTH else None
        if sub is not None and sub.valid and takes_message(sub.graph, depth + 1):
            return True
    return False


class Trigger:
    """The turn hooks of Habits: pre_llm_call runs a request habit, the others measure its turn."""

    def on_pre_llm_call(self, session_id="", user_message=None, parent_session_id="", turn_id="", platform="", **_):
        try:
            return self._pre_llm_call(str(session_id or ""), _text(user_message).strip(), parent_session_id,
                                      str(turn_id or ""), str(platform or ""))
        except Exception as exc:
            logger.warning("habits: trigger skipped: %s", exc, exc_info=True)
            return None

    def _pre_llm_call(self, session: str, text: str, parent: str, turn: str = "", platform: str = ""):
        self.pending.pop(session, None)
        self.telling.pop(session, None)
        if session and platform:  # the start on a loaded skill needs to know whose turn this is
            self.platforms.pop(session, None)
            self.platforms[session] = platform
            while len(self.platforms) > 500:
                self.platforms.pop(next(iter(self.platforms)))
        if parent:
            return None  # a subagent's turn: its reply is not the user's
        self.said.pop(session, None)  # the newest last: the oldest session goes first
        self.said[session] = REPLY.sub("", text, count=1)  # what the user says now: habit_save asks if it was a habit
        while len(self.said) > SAID:
            self.said.pop(next(iter(self.said)))
        # News no message could tell when it happened (a habit ready, retired, demoted): this reply tells the oldest,
        # once it went out, one line per reply. Never a cron job's turn (its reply is often [SILENT], or has nowhere
        # to go) nor the curator's (no one reads it), nor a message no habit takes.
        news = [] if platform in MACHINERY or ignored(text) else self.news(daily=True)[:1]
        try:
            reply = None if platform == "curator" else self._turn(session, text, turn, platform)  # no user's ask
        except Exception as exc:
            if not news:
                raise
            logger.warning("habits: trigger skipped: %s", exc, exc_info=True)
            reply = None
        if not news:
            return reply
        try:  # the news never costs the turn its habit: an active run that acted must still be heard
            line = self.news_line(news[0])
            if reply and "message" in reply:  # a direct answer: no model to tell it, and Hermes sends it as it is
                self.ledger.told([news[0]["id"]])
        except Exception as exc:
            logger.warning("habits: could not tell the news of %s: %s", news[0].get("habit"), exc)
            return reply
        reply = dict(reply or {})
        reply["context"] = texts.MODEL_TELL.format(line=line) + (f"\n{reply['context']}" if reply.get("context") else "")
        if "message" in reply:
            reply["message"] = f"{line}\n{reply['message']}"
        else:
            self.telling[session] = [news[0]["id"]]
        return reply

    def _turn(self, session: str, text: str, turn: str = "", platform: str = ""):
        deadline = time.monotonic() + HOOK_SECONDS  # Hermes abandons the hook at 30 s
        if ignored(text) or self.paused():  # /habits pause: the model answers alone, and nothing says why
            return None
        saved, candidates, learning = catalog.skills(), [], self.setting("learning")
        for habit in saved:
            if habit.valid and habit.kind == "request":
                status = self.ledger.status(habit.name, habit.version)
                if status == "active" or (status == "shadow" and learning != "off"):  # learning off: no practice run
                    candidates.append((habit, status))
        if not candidates:
            return None
        started = time.monotonic()
        chain = self.decider()  # one chain for both questions: a model that failed on the first is not waited for again
        chosen, _ = self.choose(text, [h for h, _ in candidates], saved, chain, deadline)
        if chosen is None:
            return None
        status = dict((h.name, s) for h, s in candidates)[chosen.name]
        mode = "shadow"
        if status == "active":
            close = self.ledger.agreed(chosen.name, chosen.version) < CLOSE_CHECKS
            mode = "spot" if close or random.random() < self.setting("spot_check") else "active"
            # A spot check hands the turn to the model alone: for a habit whose dry run does the whole task, only
            # while the model is seen answering, so one kept for an outage (hermes-health) still answers then.
            if mode == "spot" and reads_all(chosen.graph) and not self.answering():
                mode = "active"
        left = deadline - time.monotonic()
        if left < 2:
            return None
        with self.held(chosen.name) as free:
            if not free:
                return None
            result = self.run(chosen, chosen.graph, dry=mode != "active", trigger=text, max_seconds=left)
        self.used(chosen, background=True)
        files = attachments(chosen.graph, result)  # what its end may send, checked; any other MEDIA: line goes
        try:
            run_id = self.ledger.record(chosen.name, chosen.version, "request", mode, result, session=session,
                                        said=REPLY.sub("", text, count=1))
            self.ledger.take_calls(session)  # forget calls left by an interrupted turn
        except Exception as exc:
            logger.warning("habits: could not record the run of %s: %s", chosen.name, exc)
            if mode != "active":
                return None  # a dry run did nothing: the model does the task as with no habit
            run_id = None  # an active run did act: the model must still hear it, unmeasured
        if mode != "active":
            started = time.monotonic()  # a shadow turn times the LLM alone, not the habit's dry run
        if run_id is not None:
            self.pending[session] = {"run_id": run_id, "habit": chosen.name, "version": chosen.version, "mode": mode,
                                     "planned": result.planned, "started": started, "tokens": 0, "calls": 0,
                                     "turn": turn, "platform": platform}
            if mode != "active" and result.status == "end" and not result.planned and reads_all(chosen.graph):
                # Nothing planned to compare: its answer is judged against the model's reply after the turn.
                answer = with_files(result.message, files)  # what it read from the web reaches the judge as data
                self.pending[session]["answer"] = {"request": text,
                                                   "message": _untrusted(answer) if read_outside(result) else answer}
        self.page(chosen.name, chosen.graph, result, chosen)
        if mode != "active":
            # Shadow: the habit only ran dry. The model does the task itself, by the habit's own procedure,
            # so its steps can be compared with the habit's plan; for a habit the plugin did not save (copied in,
            # synced, cloned), its own way: none of its text reaches the model before the user's yes.
            if (self.ledger.info(chosen.name) or {}).get("origin") != "asked":  # seen with no save, or an older one
                return {"context": texts.MODEL_LEARNING}
            return {"context": f'[Habits] The habit "{chosen.name}" is learning on this request. '
                               f"Do the task yourself, following its procedure (skill {chosen.name}), step by step "
                               "with the same commands; do not call habit_run for it."}
        # The same words as habit_run's reply (tools.relay): done, or handed back with what is left.
        took, report = f"{result.ms / 1000:.1f} s", brief(result)
        message = with_files(result.message, files)
        wrap = read_outside(result)  # a page can hold orders: what came of it reaches the model as data
        left = left_of(result)
        text = f"{message}\n{report}" + ("\nLeft:\n" + "\n".join(f"- {e}" for e in left) if left else "")
        found = _untrusted(text) if wrap else text
        if result.status == "end":
            context = (f'[Habits] The habit "{chosen.name}" already did this request, before you, in {took}.\n'
                       f"{found}\n"
                       f"{next_of(result, wrap)} Confirm to the user in one sentence."
                       + (" Its MEDIA: line sends the file: put it in your reply, alone on a line." if files else ""))
            if self.setting("direct") and can_answer() and not wrap:
                # The whole task is done: answer with the habit's own message and skip the model. A Hermes
                # that does not know the block action ignores it and reads the context instead. Hermes keeps
                # that message as the model's reply of the turn, outside any block: never after a web read.
                lang = self.lang()
                # What the answer cost, as it was: its llm nodes, and the model standing in for the decision model (the
                # trigger's questions included).
                used = result.counts.get("llm", 0) + result.backends.count("llm") + chain.used.count("llm")
                answered = {*result.backends, *chain.used}
                reply = f"{message}\n\n" + texts.say("footer", lang, name=chosen.name, took=texts.seconds(result.ms, lang),
                                                      calls=texts.calls(used, lang, "jev" in answered))
                return {"action": "block", "message": reply, "context": context}
            return {"context": context}
        context = (f'[Habits] The habit "{chosen.name}" started this request in {took} and handed it back.\n'
                   f"{found}\n{next_of(result, wrap)} Do not redo the steps it completed.")
        # A wake the graph planned (a name clash, a case to decide) is no fault, nor decision models that were down.
        if (result.failed and not result.outage) or result.doubts:
            context += "\n" + REPAIR.format(path=chosen.path)
        return {"context": context}

    def choose(self, text: str, habits: list, saved: list, chain: deciders.Chain, deadline: float,
               seen: dict | None = None, measured: catalog.Habit | None = None) -> tuple:
        """The habit a user message runs, as the hook decides it (`hermes habits run --message` too): (that habit
        or None, the one the decision model picked or None). seen gets each answer that decided it (pick, confirm, also), and
        after a pick that held, the confirmation of measured (the CLI's habit), recorded."""
        said = REPLY.sub("", text, count=1).lower()  # a quoted reply is not what the user says now
        if "habit_" in said:
            return None, None  # the user talks about the habit tools: the model's call
        # A saved habit named in the message (run it, fix it, ask about it) leaves the turn to the model: no other
        # habit takes it either (seen: naming a one-off let a shadow habit do the task its own way).
        if any(named(h.name, said) for h in saved):
            return None, None
        chosen = self.pick(text, habits, chain.within(deadline - time.monotonic()), seen)
        if chosen is None:
            if seen is not None:
                self.recorded(text, habits, chain, deadline, seen, measured)
            return None, None
        # Shadow too: a dry run there still tells the model to do the habit's task. 2 s stay for the run.
        left = deadline - time.monotonic()
        if left < 3 or not self.confirm(text, chosen, chain.within(left - 2), seen):
            return None, chosen
        return chosen, chosen

    def pick(self, text: str, habits: list, chain: deciders.Chain, seen: dict | None = None) -> catalog.Habit | None:
        """One choice question to the decision model: which habit fits this message, "none" first."""
        options = ("none",) + tuple(h.name for h in habits)
        # Not taken: "a greeting before a request is no chat" left "Hi! send me the notbook note
        # on sourdough" at 0.33-0.66 (0.48-0.52), and saying the habit's item comes from the message 0.62-0.76.
        # Not taken either: asking it without "Hi! " gave 0.55-0.59 (0.38-0.58). "notbook" was what it
        # missed: with the notebook named in the habit's description, the whole message got 0.98.
        descriptions = (("none", "None of them: the message asks something else (a question, a chat, another task)"),
                        ) + tuple((h.name, task(h)) for h in habits)
        question = deciders.Question("Which saved task does this user message ask to run now?",
                                     "choice", options, descriptions)
        answer = chain.ask(f"User message:\n{text}", [question])[0]
        gate = self.setting("trigger_confidence")
        passed = answer.value != "none" and answer.confidence >= gate
        logger.info("habits: trigger %s at %.2f (%s)%s", answer.value, answer.confidence, answer.backend,
                    _odds(answer))
        if seen is not None:
            seen["pick"] = _seen(answer, gate, passed)
        return next((h for h in habits if h.name == answer.value), None) if passed else None

    def recorded(self, text: str, habits: list, chain: deciders.Chain, deadline: float, seen: dict,
                 measured: catalog.Habit | None = None):
        """After a pick that held, the confirmation it made needless, on the habit measured
        (else the one it rated highest; a tie took the rival listed first), so a trap shows both gates (a bench run
        saw a summary trap's pick at 0.66-0.70, never its confirmation); each answer names its habit. For
        `hermes habits run --message` only, and recorded only: the decision stays the pick's. No model call pays
        for it (never the llm backend), and its time is said apart, for the bench to leave out."""
        pick = seen.get("pick") or {}
        odds = pick.get("probabilities") or {pick.get("answer"): pick.get("confidence", 0)}
        top = measured if measured is not None else max((h for h in habits if h.name in odds),
                                                        key=lambda h: odds[h.name], default=None)
        free = deciders.Chain([b for b in chain.backends if b.name != "llm" and b.name not in chain.down])
        left = deadline - time.monotonic()
        if top is None or not free.backends or left < 3:
            return
        began, answers = time.monotonic(), {}
        try:
            self.confirm(text, top, free.within(left - 2), answers)
        except deciders.DecisionError as exc:
            logger.info("habits: no recorded confirmation of %s: %s", top.name, exc)
            return
        seen.update({stage: {**answer, "recorded": True, "habit": top.name} for stage, answer in answers.items()})
        seen["recorded_ms"] = int((time.monotonic() - began) * 1000)

    def confirm(self, text: str, habit: catalog.Habit, chain: deciders.Chain, seen: dict | None = None) -> bool:
        """One question before a picked habit runs: a message can name the task and still not ask for it
        as the habit does it (an exception, a negation, another folder or way, a second request). A habit that
        takes its item from the message runs only when a second question finds, as sure, nothing else to answer."""
        named = takes_message(habit.graph)
        cases = tuple((case, NAMED_CASES.get(case, text) if named else text) for case, text in CONFIRM_CASES)
        questions = [deciders.Question(CONFIRM.format(task=task(habit, named)), "choice",
                                       tuple(case for case, _ in cases), cases)]
        if named:  # the same call: no time added
            questions.append(deciders.Question(ALSO, "choice", tuple(case for case, _ in ALSO_CASES), ALSO_CASES))
        answers = chain.ask(f"User message:\n{text}", questions)
        answer, gate = answers[0], self.setting("min_confidence")
        passed = answer.value == "run" and answer.confidence >= gate
        alone = not named or (answers[1].value == "nothing" and answers[1].confidence >= gate)
        logger.info("habits: confirm %s: %s at %.2f (%s)%s%s", habit.name, answer.value, answer.confidence,
                    answer.backend, _odds(answer),
                    f"; also: {answers[1].value} at {answers[1].confidence:.2f}" if named else "")
        if seen is not None:
            seen["confirm"] = _seen(answer, gate, passed)
            if named:
                seen["also"] = _seen(answers[1], gate, alone)  # "nothing" at the gate, or Hermes answers
        return passed and alone

    def on_post_tool_call(self, tool_name="", args=None, session_id="", result=None, status="ok", tool_call_id="",
                          **_):
        try:
            pending = self.pending.get(str(session_id or ""))
            if pending and pending["mode"] != "active" and status in ("ok", None, ""):
                noted = dict(args) if isinstance(args, dict) else {}
                if tool_name == "terminal" and isinstance(result, str):
                    cwd = _json(result).get("cwd")
                    if isinstance(cwd, str):
                        noted["_cwd"] = cwd
                self.ledger.note_call(str(session_id), str(tool_name), noted)
        except Exception as exc:
            logger.warning("habits: could not note a tool call: %s", exc)
        try:  # a draft of the dream practices beside this call; cheap when nothing practices
            self.practise(str(session_id or ""), str(tool_name), args if isinstance(args, dict) else {}, result,
                          status)
        except Exception as exc:
            logger.warning("habits: could not practice beside a tool call: %s", exc)
        return None

    def answering(self) -> bool:
        """The model answered a request of any session in the last MODEL_FRESH seconds."""
        return self.model_seen is not None and time.monotonic() - self.model_seen < MODEL_FRESH

    def on_post_api_request(self, session_id="", usage=None, turn_id="", **_):
        """Count the model tokens and calls of a turn a habit is in (not a /btw or review fork of its session).
        Any session's: the model answers."""
        self.model_seen = time.monotonic()
        try:
            pending = self.pending.get(str(session_id or ""))
            if pending is not None and pending.get("turn", "") in ("", str(turn_id or "")):
                pending["calls"] += 1
                if isinstance(usage, dict):
                    total = usage.get("total_tokens")
                    if not isinstance(total, (int, float)):
                        total = sum(v for k, v in usage.items() if isinstance(v, (int, float)) and
                                    k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))
                    pending["tokens"] += int(total)
        except Exception as exc:
            logger.warning("habits: could not count tokens: %s", exc)
        return None

    def on_post_llm_call(self, session_id="", assistant_response=None, conversation_history=None, platform="", **_):
        try:
            return self._post_llm_call(session_id, assistant_response)
        finally:  # whatever the reply did, the practice of a grown draft ends with the turn and the dream may start
            self.practise_turn(str(session_id or ""))  # neither raises
            self.maybe_dream(str(platform or ""))

    def _post_llm_call(self, session_id, assistant_response):
        said = _text(assistant_response).strip()
        try:
            telling = self.telling.pop(str(session_id or ""), None)
            if telling and not silent(said):  # the reply that tells it went out
                self.ledger.told(telling)
        except Exception as exc:  # untaken, it is told again: better twice than never
            logger.warning("habits: could not mark the news told: %s", exc)
        try:
            pending = self.pending.pop(str(session_id or ""), None)
            if not pending:
                return None
            pending["reply"] = said
            self.ledger.set_turn(pending["run_id"], int((time.monotonic() - pending["started"]) * 1000),
                                 pending.get("tokens"), pending.get("calls"))
            if pending["mode"] == "active":
                current = catalog.find(pending["habit"])
                if current is not None and current.version == pending["version"]:  # not saved again in the turn
                    self.ledger.evaluate(pending["habit"], pending["version"])
                    if pending.get("platform") != "cron":
                        self.tell([pending["habit"]])  # a retirement, a demotion: said at once
                return None
            calls = self.ledger.take_calls(str(session_id))
            # The turn's context (its profile, its secrets) goes with the thread, or the judge cannot reach the model.
            self.background(self.compare, pending, calls)
        except Exception as exc:
            logger.warning("habits: comparison skipped: %s", exc)
        return None

    def compare(self, pending: dict, calls: list):
        try:
            if "answer" in pending:
                if not self.judged(pending):
                    return
            else:
                judge = replay.judge_with(self.complete) if self.setting("judge") else None
                outcome = replay.compare(pending["planned"], calls, judge, sent=replay.media(pending.get("reply")))
                if outcome.get("judge_error") and outcome["missing"]:
                    logger.warning("habits: judge failed (%s); run %s left uncounted", outcome["judge_error"],
                                   pending["run_id"])
                    return
                self.ledger.compared(pending["run_id"], outcome["matched"], outcome["by_judge"],
                                     planned=outcome["planned"])
                logger.info("habits: compared %s: %d/%d planned writes matched (%d by the judge), %d extra, "
                            "%d calls seen%s", pending["habit"], outcome["matched"], outcome["planned"],
                            outcome["by_judge"], outcome["extra"], len(calls),
                            f", judge failed: {outcome['judge_error']}" if outcome.get("judge_error") else "")
            current = catalog.find(pending["habit"])
            if current is None or current.version != pending["version"]:
                return  # the graph changed during the turn: its new version starts its own scores
            before = self.ledger.status(pending["habit"], pending["version"])
            after = self.ledger.evaluate(pending["habit"], pending["version"])
            if after != before:
                logger.info("habits: %s is now %s", pending["habit"], after)
            if pending.get("platform") != "cron":  # ready, retired: said at once, from the turn's own context
                self.tell([pending["habit"]])
        except Exception as exc:
            logger.warning("habits: comparison failed: %s", exc)

    def trusted(self, pending: dict) -> bool:
        """A spot run is judged only for being wrong when the habit is trusted: the user said yes to it, or it already
        agreed CLOSE_CHECKS times. Before that its checks are its only measurement, against the model's answer."""
        if pending.get("mode") != "spot":
            return False
        habit, version = pending["habit"], pending["version"]
        return self.yes_on_record(habit, version) or self.ledger.agreed(habit, version) >= CLOSE_CHECKS

    def yes_on_record(self, habit: str, version: str) -> bool:
        """The user's own yes to this version is its last move on record, said in a chat or in their terminal (the
        guard refuses the model's own `hermes habits promote`). Wider than ledger.vouched, which opens a habit's
        text to the model and counts a chat's yes only: here a yes only softens a check of a habit that reads."""
        row, last = self.ledger.info(habit), self.ledger.history(habit, 1)
        return bool(row and row["status"] == "active" and row["version"] == version and last
                    and (last[0]["version"], last[0]["to"]) == (version, "active") and last[0]["by"] in ("user", "cli"))

    def judged(self, pending: dict) -> bool:
        """A read-only habit's answer against the model's reply: one judged comparison, counted as one planned write
        (as a watch check), whatever the judge setting. No reply, or a judge that fails, leaves the run uncounted."""
        if not pending.get("reply"):
            logger.info("habits: no reply to judge %s against; run %s left uncounted", pending["habit"],
                        pending["run_id"])
            return False
        answer = pending["answer"]
        try:
            agrees, why = replay.reply_judge_with(self.complete, spot=self.trusted(pending))(
                answer["request"], answer["message"], pending["reply"])
        except Exception as exc:
            logger.warning("habits: reply judge failed (%s); run %s left uncounted", exc, pending["run_id"])
            return False
        self.ledger.compared(pending["run_id"], 1 if agrees else 0, 1, planned=1, verdict=why)
        logger.info("habits: judged %s against the reply: %s (%s)", pending["habit"],
                    "agrees" if agrees else "disagrees", why)
        return True

    def asked_for(self, session: str) -> bool:
        """The user's own last message in this session asks to make a habit, or to remember how to do the task:
        one yes/no to the decision model at the host's min_confidence. Unknown, or no decision model answering: no."""
        text = self.said.get(session or "", "")
        if not text:
            return False
        try:
            answer = self.decider().within(5).ask(f"User message:\n{text}", [
                deciders.Question(ASKED, "yes_no", ("yes", "no"))])[0]
        except deciders.DecisionError as exc:
            logger.info("habits: could not ask whether the user asked for a habit: %s", exc)
            return False
        logger.info("habits: the user asked for a habit: %s at %.2f (%s)", answer.value, answer.confidence,
                    answer.backend)
        return answer.value == "yes" and answer.confidence >= self.setting("min_confidence")
