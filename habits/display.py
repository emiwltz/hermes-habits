"""What people see: `hermes habits` in a terminal, /habits <name> and the diagram pages (the cockpit has /habits
itself and its decisions).

A habit's own text reaches a chat without MEDIA: directives, so a page it read cannot make the gateway
attach a file.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import re
import shutil
import textwrap
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import catalog, graph, view
from .texts import TEXTS, period, say

if TYPE_CHECKING:  # the class these methods are mixed into
    from .plugin import Habits

logger = logging.getLogger(__name__)


# The --json boundary (decision 0.34): the words of the contract leave here, by one mapping. Internal names stay in
# the ledger, view.py and the pages. What a person or a habit wrote (values, inputs, arguments) is never touched.
STATE_WORDS = {"shadow": "learning", "active": "automatic", "retired": "resting", "live": "called"}
KEY_WORDS = {"node": "step", "practising": "practicing"}  # the ledger keeps its own spelling of that state
KEY_PREFIXES = (("shadow_", "learning_"), ("active_", "automatic_"))
STATE_KEYS = ("state", "status_of", "habit_state")  # keys that hold a habit's state (a run's status is end or wake)
OPAQUE_KEYS = {"values", "inputs", "args", "memory", "llm_ms", "untrusted"}


def public(obj, key=None):
    """A copy of what leaves as JSON, in the contract's words: states learning, automatic, resting, called; run mode
    test or live; keys learning_* and automatic_* for shadow_* and active_*; step for node; practicing for practising."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            name = KEY_WORDS.get(k, k) if isinstance(k, str) else k
            for old, new in KEY_PREFIXES:
                if isinstance(k, str) and k.startswith(old):
                    name = new + k[len(old):]
            out[name] = v if k in OPAQUE_KEYS else public(v, k)
        return out
    if isinstance(obj, (list, tuple)):
        return [public(v, key) for v in obj]
    if key == "mode" and obj == "dry":
        return "test"
    if key in STATE_KEYS and isinstance(obj, str):
        return STATE_WORDS.get(obj, obj)
    return obj


def _days(text: str) -> int:
    """--days N: 1 to 60."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of days") from None
    if not 1 <= value <= 60:
        raise argparse.ArgumentTypeError("days must be from 1 to 60")
    return value

CLI_VERBS = {"list", "show", "check", "run", "dream", "tick", "retire", "rest", "reset", "cleanup"}  # retire: the old name of rest
# A chat line printed in a terminal: its /habits commands said as `hermes habits` ones (faux is no terminal verb: its
# nearest is undo, which takes the yes back).
TERMINAL = ((r"/habits (?:reprends|resume)(?![\w-])", "hermes habits resume"), (r"/habits (?:faux|wrong)(?![\w-])", "hermes habits undo"),
            (r"/habits (?:rêve|reve|dream)(?![\w-])", "hermes habits dream"), (r"/habits (?:essai|try)(?![\w-])", "hermes habits reset"), (r"/habits (?:oui|yes)(?![\w-])", "hermes habits yes"),
            (r"/habits (?:repos|rest)(?![\w-])", "hermes habits rest"), (r"/habits (?:annule|undo)(?![\w-])", "hermes habits undo"),
            (r"/habits (pause|view)(?![\w-])", r"hermes habits \1"), (r"/habits voir(?![\w-])", "hermes habits view"),
            (r"/habits (?:help|aide)(?![\w-])", "hermes habits --help"), (r"/habits (?=lists them|les liste)", "hermes habits list "),
            (r"/habits (?=[<a-z0-9])", "hermes habits show "),
            (r"/habits\b", "hermes habits list"))


# Notes that say nothing new (approved, on trial).
NOTED = ("approved", "on trial", "undone", "saved active")


# Only the command itself: /habits starting a word, not a folder of a path (plugin-data/habits/pages, ~/habits/x).
COMMAND = r"(?<![\w.~/-])(?!/habits/)"


def terminal(text: str) -> str:
    """The chat's /habits words as a terminal's commands; a span in backticks is a chat's own, kept as it is. Any
    character that is not printable, a line break apart, becomes a space: a description or a tool output holds ESC."""
    parts = text.split("`")
    for at in range(0, len(parts), 2):
        for pattern, said in TERMINAL:
            parts[at] = re.sub(COMMAND + pattern, said, parts[at])
    return "".join(c if c == "\n" or c.isprintable() else " " for c in "`".join(parts))  # no ESC reaches a terminal

# The gateway sends MEDIA: then a path (quoted, ~/, / or C:\), anywhere in a reply, as a file; the path may be
# on the next line. "Social media: 2" is only text.
MEDIA = re.compile(r"MEDIA:\s*(?:[`\"']|~/|/|[A-Za-z]:[/\\]|$)", re.I)


# A bare path the gateway would send as a file too (extract_local_files: absolute or ~/, an extension, a file on
# disk, outside code): Hermes puts the paths of its own footers in backticks for that (agent/turn_explainers.py).
BARE_PATH = re.compile(r"(?<![/:\w.`])(?:~/|/|[A-Za-z]:[/\\])(?:[\w.\-]+[/\\])*[\w.\-]+\.\w+")


def _no_media(text) -> str:
    """A habit's text as a reply may quote it: without its MEDIA: lines, and each bare file path in code, the
    text's own backticks made quotes first (an odd one would end the code early). A page, a note or a terminal's
    label could otherwise make the gateway attach a local file. Only the plugin's own replies (/habits view) carry
    a MEDIA: line."""
    text = "\n".join(line for line in str(text).splitlines() if not MEDIA.search(line))
    if not BARE_PATH.search(text):
        return text
    return BARE_PATH.sub(lambda m: f"`{m.group(0)}`", text.replace("`", "'"))


def messaging(platform) -> bool:
    """A chat platform (telegram, discord…), where a reply can carry a file, as opposed to a terminal or an app."""
    return bool(platform) and str(platform).lower() not in (
        "cli", "tui", "desktop", "acp", "api", "local", "api_server", "webhook", "msgraph_webhook", "kanban",
        "gateway", "tool", "codex", "cron")


class Display:
    """The pages of Habits, /habits and `hermes habits`."""

    # ---- pages --------------------------------------------------------------

    def page_data(self, name, scenario, result=None, habit=None, status=None, issues=None) -> dict:
        summary = self.summary(habit) if habit else {}
        summary.update(promote_runs=self.setting("promote_runs"), promote_agreement=self.setting("promote_agreement"))
        return {
            "name": name, "description": habit.description if habit else "",
            "kind": habit.kind if habit else catalog.kind_of(scenario),
            "status": status or (_status(habit, summary) if habit else "one-off"),
            "habit": scenario, "issues": issues or [], "summary": summary,
            "origin": habit.origin if habit else "one-off",
            "run": result.to_dict() if result else (self.ledger.last_trace(habit.name) if habit else None),
        }

    def page(self, name, scenario, result=None, habit=None, status=None, issues=None) -> Path | None:
        try:
            return view.write(self.home() / "pages", name, self.page_data(name, scenario, result, habit, status, issues))
        except Exception as exc:
            logger.warning("habits: could not write the page of %s: %s", name, exc)
            return None

    def gallery(self) -> Path:
        """Every habit, examples last, on one page: _gallery.html next to the habit pages."""
        items = [self.page_data(h.name, h.graph, habit=h, issues=h.issues) for h in catalog.all_habits()]
        items.sort(key=lambda d: d["origin"] == "example")
        return view.write_gallery(self.home() / "pages", items)

    def view(self, name: str = "", lister: str = "/habits") -> tuple[Path | None, str]:
        """Write the page of one habit, or the gallery: (path, what to say)."""
        lang = self.lang()
        if not name:
            return self.gallery(), say("view_gallery", lang)
        habit = catalog.find(name)
        if habit is None:
            return None, say("unknown" if lister == "/habits" else "unknown_cli", lang, name=name)
        path = self.page(habit.name, habit.graph, habit=habit, issues=habit.issues)
        return path, say("view_one" if path else "view_failed", lang, name=habit.name)

    def prune_pages(self, keep: int = 30):
        """One-off pages pile up: keep the newest ones."""
        try:
            pages = sorted((self.home() / "pages").glob("one-off-*.html"), key=lambda p: p.stat().st_mtime)
            for old in pages[:-keep]:
                old.unlink()
        except OSError:
            pass

    # ---- /habits and hermes habits -------------------------------------------

    def listing(self) -> str:
        """`hermes habits list`: the list /habits shows, in the language setting, with the terminal's commands."""
        return terminal(self.grouped(self.rows(), cli=True))

    def asks(self, name: str) -> str:
        """Under a habit's detail, the decision it waits for, if any: what it will write, then the commands."""
        from .cockpit import state_of
        habit = catalog.find(name)
        if habit is None or habit.origin == "example":
            return ""
        state = state_of(habit, self.summary(habit))
        if state != "ready":
            return ""
        writes = self.access(habit)[1]
        return ("\n" + writes if writes else "") + "\n" + say("ask_ready", self.lang(), name=habit.name)

    def detail(self, name: str, platform: str | None = None) -> str:
        """/habits <name> (and `hermes habits show`, platform None): its line in the list, its description, how it
        starts, its gains, its last 5 runs, and where its diagram is (in a chat, the command that sends it: a bare
        path there would be sent as a file)."""
        from .cockpit import state_of
        from .texts import count, seconds, why
        lang, habit = self.lang(), catalog.find(name)
        if habit is None:
            return say("unknown" if platform is not None else "unknown_cli", lang, name=name)
        summary = self.summary(habit)
        note = str(summary.get("note") or "")
        moved = f" · {why(note, lang)}" if _why(summary) and not note.startswith(NOTED) else ""
        state = "byname" if habit.origin == "example" else state_of(habit, summary)  # an example runs by name only
        lines = [self.line(habit, summary, state) + moved
                 + (say("example", lang) if habit.origin == "example" else ""),
                 habit.description, say("info", lang, version=habit.version, trigger=_trigger(
                     None if habit.origin == "example" else habit.graph.get("trigger"), lang))]  # an example never starts alone
        if habit.kind == "request" and state == "active" and self.setting("spot_check") > 0:
            # a message habit after the yes: the model checks its first runs behind it, which is why they stay slow
            from .trigger import CLOSE_CHECKS
            agreed = min(CLOSE_CHECKS, self.ledger.agreed(habit.name, habit.version))
            lines.append(say("detail_checking", lang, agreed=agreed, needed=CLOSE_CHECKS) if agreed < CLOSE_CHECKS
                         else say("detail_alone", lang, every=max(1, round(1 / self.setting("spot_check")))))
        if habit.kind == "watch":
            lines.append(say("detail_checks", lang, checks=count("checks", summary["runs"], lang),
                             wakes=count("wakes", summary["woke"], lang)))
        extra = []
        if summary.get("shadow_ms") and summary.get("active_ms"):
            extra.append(say("detail_gain", lang, fast=seconds(summary["active_ms"], lang),
                             slow=seconds(summary["shadow_ms"], lang)))
        if summary.get("shadow_tokens") and summary.get("active_tokens") is not None:
            extra.append(say("detail_tokens", lang, active=view._tokens(summary["active_tokens"]),
                             shadow=view._tokens(summary["shadow_tokens"])))
        lines += [" · ".join(extra)] if extra else []
        if habit.issues:
            lines += [say("detail_invalid", lang)] + [f"  {i}" for i in habit.issues]
        for run in self.ledger.runs(habit.name, 5):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(run["at"]))
            agree = say("run_matched", lang, matched=run["matched"], planned=run["planned"]) if run["compared"] else ""
            agree += say("run_judge", lang, why=_no_media(run["verdict"])[:100]) if run.get("verdict") else ""
            turn = say("run_turn", lang, took=seconds(run["turn_ms"], lang)) if run["turn_ms"] else ""
            mode = say(f"run_{run['mode']}", lang) if f"run_{run['mode']}" in TEXTS["en"] else run["mode"]
            if run.get("kind") == "skill" and run["mode"] == "adhoc":  # no one called it by name: its skill was loaded
                mode = say("run_skill", lang)
            status = say(f"status_{run['status']}", lang) if f"status_{run['status']}" in TEXTS["en"] else run["status"]
            lines.append(f"  {when} · {mode} · {status} · {seconds(run['ms'], lang)}{turn}{agree}"
                         + (f" · {_no_media(run['reason'])}" if run["reason"] else ""))  # it holds tool output
        page = self.home() / "pages" / f"{habit.name}.html"
        if messaging(platform) or not page.exists():  # a path that holds no file is no help: say how to write it
            lines.append(say("page_chat" if messaging(platform) else "page_none", lang, name=habit.name))
        else:
            lines.append(say("page", lang, path=str(page) if platform is None else f"`{page}`"))
        if platform is None:  # a terminal reader can open the file; in a chat the page is a command
            lines += [say("file", lang, path=str(habit.path)), say("steps", lang)] + steps(habit.graph, True, lang)
        return "\n".join(line for line in lines if line)

    def command(self, raw_args: str = "", platform: str = "", session_id: str = "", **_) -> str:
        """/habits: the cockpit, a verb (yes, try, rest, wrong, pause, resume, undo, or their French twins), a habit,
        or a view."""
        return self._command(raw_args, platform, session_id)

    def _command(self, raw_args: str, platform: str, session_id: str) -> str:
        from .cockpit import VERBS
        try:
            words = (raw_args or "").split()
            if words and words[0].lower() in VERBS:
                return self.verb(VERBS[words[0].lower()], words[1] if len(words) > 1 else "", str(session_id or ""),
                                 words[0])
            if words[:1] in (["help"], ["aide"]) or (words and words[0] in CLI_VERBS and catalog.find(words[0]) is None):
                return say("help", self.lang())
            if words[:1] in (["view"], ["voir"]):
                path, said = self.view(words[1] if len(words) > 1 else "")
                if path is None:
                    return said
                if messaging(platform):  # the gateway sends a MEDIA: path as a file
                    return f"{say('view_open', self.lang(), said=said)}\nMEDIA:{path}"
                return say("view_at", self.lang(), said=said, uri=path.as_uri())
            if not words:
                return self.cockpit(str(session_id or ""))
            return self.detail(words[0], str(platform or "")) + self.asks(words[0])
        except Exception as exc:
            logger.warning("habits: /habits failed: %s", exc)
            return say("error", self.lang(), kind=type(exc).__name__, exc=exc)

    def cli_setup(self, parser):
        sub = parser.add_subparsers(dest="habits_command", metavar="<command>")  # no brace list: the old words stay out of sight
        sub.add_parser("list", help="List your habits: learning, automatic or resting, with their numbers")
        sub.add_parser("view", help="Open the gallery of your habits, or the diagram of one reflex").add_argument(
            "name", nargs="?", default="")
        show = sub.add_parser("show", help="One habit: its numbers and recent runs")
        show.add_argument("name")
        show.add_argument("--json", action="store_true", help="where its file is, as a run finds it, and what it uses")
        sub.add_parser("yes", help="Say yes: let a habit act on its own (/habits yes)").add_argument("name")
        sub.add_parser("promote").add_argument("name")  # the old word for yes: accepted, not listed
        sub.add_parser("pause", help="Pause the habits: none starts from a message or a schedule (a call by name "
                       "still runs)")
        sub.add_parser("resume", help="Start the habits again after a pause")
        sub.add_parser("undo", help="Undo your last change of the last 24 h").add_argument(
            "name", nargs="?", default="")
        sub.add_parser("rest", help="Put a habit to rest (a scheduled one loses its cron job)").add_argument("name")
        sub.add_parser("retire").add_argument("name")  # the old word for rest: accepted, not listed
        for verb, text in (("tick", "Run a scheduled habit once, as its schedule does (used by cron)"),
                           ("reset", "Send a habit back to learning with fresh numbers (one called by name: back "
                                     "to called by name)")):
            sub.add_parser(verb, help=text).add_argument("name")
        dream = sub.add_parser("dream", help="The dream, now: re-read Hermes's history and show what comes back; it never says "
                               "yes to anything")
        dream.add_argument("--json", action="store_true", help="print one JSON object")
        dream.add_argument("--days", type=_days, default=None, metavar="N",
                           help="how many days of history to read, 1 to 60 (default 60)")
        dream.add_argument("--home", metavar="FOLDER", default=None,
                           help="read the history of another Hermes home (its state.db, read only); drafts and "
                                "proposals stay in this one")
        sub.add_parser("check", help="Check a reflex.yml file").add_argument("file")
        run = sub.add_parser("run", help="Run a reflex here without the model, as a test unless --live; nothing is "
                             "recorded; prints the path of the replay page of that run")
        run.add_argument("target", help="a habit's name, or a path to a reflex.yml file")
        run.add_argument("--input", action="append", default=[], metavar="NAME=VALUE",
                         help="a value for one of its inputs (JSON when it reads as JSON); repeat for each")
        run.add_argument("--live", action="store_true", help="do its writes for real (never under ~/Downloads)")
        run.add_argument("--json", action="store_true", help="print one JSON object")
        run.add_argument("--within", metavar="DIR", default=None,
                         help="start its commands in DIR; with --live, refuse any write outside DIR")
        run.add_argument("--message", metavar="TEXT", default=None,
                         help="a user message: the reflex runs only if a message like it would start it")
        run.add_argument("--memory", metavar="FILE", default=None,
                         help="a JSON file that keeps what check: changed compares with, from one run to the next")
        sub.add_parser("cleanup", help="Remove the cron job and file of every scheduled habit (run it before removing the plugin)")

    def cli_handle(self, args) -> int:
        verb = getattr(args, "habits_command", None) or "list"
        return self._cli_handle(args, verb)

    def _cli_handle(self, args, verb: str) -> int:
        try:
            if verb == "list":
                print(self.listing())
            elif verb == "show":
                if getattr(args, "json", False):
                    return self.cli_show(args.name)
                if catalog.find(args.name) is None:
                    # a draft is no habit yet: show what the dream saw, the reflex it would become and the words to type
                    shown = self.draft_detail(args.name)
                    if shown:
                        print(terminal(shown))
                        return 0
                    print(say("unknown_cli", self.lang(), name=args.name))
                    return 1
                print(terminal(self.detail(args.name) + self.asks(args.name)))
            elif verb == "dream":
                return self.cli_dream(getattr(args, "days", None), getattr(args, "json", False), getattr(args, "home", None))
            elif verb == "check":
                return _check_file(args.file, self.allowances(), self.lang())
            elif verb == "view":
                return self.cli_view(args.name)
            elif verb == "run":
                why = self.strict_refusal(args.target)
                if why:
                    print(json.dumps(public({"status": "refused", "message": why}), ensure_ascii=False) if args.json
                          else say("run_refused", self.lang(), why=why))
                    return 1
                if self.cli_run_draft(args):
                    return self._draft_run_code
                return self.cli_run(args.target, args.input, args.live, args.json, getattr(args, "within", None),
                                    getattr(args, "message", None), getattr(args, "memory", None))
            elif verb == "tick":
                return self.cli_tick(args.name)
            elif verb in ("yes", "promote"):  # the user's yes in a terminal: on record as the terminal's
                moved, said = self.yes(args.name, by="cli")
                print(terminal(said))
                return 0 if moved else 1
            elif verb in ("pause", "resume"):
                print(terminal(self.verb(verb)))
            elif verb == "undo":  # the terminal's last change, or the chat's
                row = self.ledger.undo(getattr(args, "name", "") or None, by="cli", of=("cli", "user"))
                print(terminal(self.undone(row)))
                return 0 if row else 1
            elif verb == "cleanup":
                _load_tools()
                print(self.removed(self.unschedule_all()))
            elif verb in ("rest", "retire", "reset"):
                verb = "rest" if verb == "retire" else verb
                habit = catalog.find(args.name)
                if habit is None or habit.origin == "example":
                    print(terminal(self.draft_note(args.name) or say(
                        "unknown_cli" if habit is None else "example_byname", self.lang(), name=args.name)))
                    return 1
                status = "retired" if verb == "rest" else "shadow"
                self.ledger.set_status(habit.name, self.version(habit), status, f"by hand ({verb})", by="cli")
                if verb == "rest" and habit.kind == "watch":
                    _load_tools()
                    print(self.removed(self.unschedule(habit.name)))
                shown = _status(habit, {"status": status})
                print(f"{habit.name}: {say('state_byname' if shown == 'live' else f'state_{shown}', self.lang())}")
            return 0
        except Exception as exc:
            print(say("error", self.lang(), kind=type(exc).__name__, exc=exc))
            return 1

    def removed(self, what) -> str:
        """The line after a cron job or a file was removed: what it was, or nothing."""
        lang = self.lang()
        return say("removed", lang, what=what or say("removed_nothing", lang))

    def cli_dream(self, days: int | None = None, as_json: bool = False, home: str | None = None) -> int:
        """`hermes habits dream`: a dream now, whatever the daily rule says. It reads, spots and sets drafts
        practicing; it never says yes to anything. Nothing wrong with an empty or unreadable history: exit 0."""
        if home and not Path(home).expanduser().is_dir():
            print(say("dream_home_missing", self.lang(), home=home))
            return 1
        summary = self.dream_now(manual=True, days=days, home=home)
        if as_json:
            print(json.dumps(public(summary), ensure_ascii=False))
        else:
            print(terminal(self.dream_text(summary, days)))
        return 0

    def strict_refusal(self, target: str) -> str:
        """With permissions strict, a test run says what `check` says of a deleting reflex: refused, before the run (a
        test run plans what a real one would do). "" when nothing is refused or the switch is not strict."""
        if self.setting("permissions") != "strict":
            return ""
        try:
            if "/" in str(target) or str(target).endswith((".yaml", ".yml")):
                from .tools import graph_source, loaded
                scenario = loaded(graph_source({"file": target}))[0]
            else:
                habit = catalog.find(target)
                scenario = habit.graph if habit is not None else None
            if not isinstance(scenario, dict):
                return ""
            return "; ".join(str(i) for i in graph.validate(scenario, **self.allowances())[:2])
        except Exception:  # the run itself says what is wrong with such a file
            return ""

    def cli_run_draft(self, args) -> bool:
        """`hermes habits run <draft>`: the draft's reflex as a test run, as a saved reflex's would be (a read runs, the
        rest is planned, nothing is written). True when the target is a draft: the exit code is then in
        self._draft_run_code."""
        target = str(args.target)
        if "/" in target or target.endswith((".yaml", ".yml")) or catalog.find(target) is not None:
            return False
        row = self.draft_row(target)
        if row is None or not isinstance(row.get("graph"), (dict, str)):
            return False
        import tempfile
        from . import yaml12
        scenario = row["graph"] if isinstance(row["graph"], dict) else json.loads(row["graph"])
        for node in (scenario.get("nodes") or {}).values():  # a draft is not proven: a call to the web is only planned
            if isinstance(node, dict) and node.get("do") in ("web_search", "web_extract"):
                node["read"] = False
        with tempfile.TemporaryDirectory(prefix="habits-draft-") as folder:
            path = Path(folder) / f"{target}.yml"
            path.write_text(yaml12.dump(scenario), encoding="utf-8")
            self._draft_run_code = self.cli_run(str(path), args.input, False, args.json, getattr(args, "within", None),
                                               getattr(args, "message", None), getattr(args, "memory", None))
        return True

    def cli_show(self, name: str) -> int:
        """`hermes habits show <name> --json`: the file a run of that name reads, and the habits it uses (the bench
        checks them before it runs anything). Unknown: whether a skill that is no habit has the name."""
        habit = catalog.find(name)
        if habit is None:
            names = catalog.skill_names()
            print(json.dumps({"status": "unknown", "habit": name, "skill": None if names is None else name in names}))
            return 1
        nodes = habit.graph.get("nodes") if isinstance(habit.graph.get("nodes"), dict) else {}
        uses = sorted({n["use"] for n in nodes.values() if isinstance(n, dict) and isinstance(n.get("use"), str)})
        print(json.dumps(public({"habit": habit.name, "path": str(habit.path), "kind": habit.kind, "origin": habit.origin,
                                 "version": habit.version, "valid": habit.valid, "issues": habit.issues, "uses": uses}),
                         ensure_ascii=False))
        return 0

    def cli_view(self, name: str = "") -> int:
        path, said = self.view(name or "", lister="hermes habits list")
        if path is None:
            print(said)
            return 1
        print(path)
        import subprocess
        import sys
        if sys.platform == "darwin" and shutil.which("open"):
            subprocess.run(["open", str(path)], check=False)
        return 0


STEP_TEXT = 60  # characters of a question, a command or a message in a step line


def _plain(text) -> str:
    """A text for a terminal: every character that is not printable (ESC, BEL, a line break) becomes a space."""
    return "".join(c if c.isprintable() else " " for c in str(text))


def _cut(text: str, size: int = STEP_TEXT) -> str:
    """A text of at most `size` characters, or a little more when its first word is longer: it is cut after a whole word
    (a path is one word) and a cut always ends with …; a text that fits is given as it is."""
    text = text.strip()
    if len(text) <= size:
        return text
    space = text.rfind(" ", 1, size)
    if space < 1:  # the first word is longer than the room: it stays whole
        space = text.find(" ")
        if space < 0:
            return text
    return text[:space].rstrip() + "…"


def _short(text) -> str:
    return _cut(" ".join(_plain(text).split()))


WIDTH = 100  # a full step wraps at this width, between words


def _wrapped(line: str) -> str:
    """A long line broken between words (a path or a long option stays whole), the next lines indented under it."""
    return textwrap.fill(line, width=WIDTH, subsequent_indent="        ", break_long_words=False,
                         break_on_hyphens=False)


def _full(text) -> str:
    return " ".join(_plain(text).split())


def _step_words(node: dict, full: bool = False, lang: str = "en") -> str:
    """One step in a few words: its verb, then what it does, then where it leads. `full`: nothing is cut (the whole
    command, the whole call with its arguments, the whole question and message) and the folder is said."""
    from .cockpit import _step
    kind = graph.kind_of(node)
    if kind == "do":
        args = node.get("args") if isinstance(node.get("args"), dict) else {}
        if full and node["do"] == "terminal":
            words = f"do terminal {_full(args.get('command', ''))}"
            notes = ([say("shown_in", lang, where=_full(args["workdir"]))]
                     if isinstance(args.get("workdir"), str) and args["workdir"].strip() else [])
            notes += [say("shown_timeout", lang, n=args["timeout"])] if isinstance(args.get("timeout"), (int, float)) \
                and not isinstance(args.get("timeout"), bool) else []
            notes += [say("shown_background", lang)] if args.get("background") is True else []
            words += f"   ({', '.join(notes)})" if notes else ""
        elif full:
            words = f"do {node['do']}" + (" " + _full(json.dumps(args, ensure_ascii=False, default=str)) if args else "")
        else:
            command = _step(node) if node.get("do") == "terminal" else ""
            words = f"do {node['do']}" + (f" {_cut(command.strip())}" if command else "")
    elif kind == "use":
        words = f"use {node['use']}"
    elif kind == "ask":
        words = f'ask "{_full(node["ask"]) if full else _short(node["ask"])}"'
    elif kind == "check":
        test = node["check"]
        if isinstance(test, dict) and test:
            op, value = next(iter(test.items()))
            words = "check " + (f"{value[0]} {op} {value[1]}" if isinstance(value, list) and len(value) == 2
                                else f"{value} {op}")
        else:
            words = "check"
    elif kind in ("end", "wake"):
        words = f'{kind} "{_full(node[kind]) if full else _short(node[kind])}"'
    else:
        words = kind or "?"  # an llm step is told by its verb alone
    then = node.get("then")
    if isinstance(then, dict) and then:
        return f"{words} → " + ", ".join(f"{answer} {target}" for answer, target in then.items())
    return f"{words} → {node['next']}" if isinstance(node.get("next"), str) else words


def steps(graph_: dict, full: bool = False, lang: str = "en") -> list[str]:
    """One line per node, in the order a reader follows from the start (breadth first), then the nodes nothing leads
    to (a fallback): "  <name>: <what it does> → <where it goes>". `full` (what `show` prints): every step whole, a
    long one wrapped between words; otherwise a step is cut at 60 characters, always ending with …."""
    nodes = graph_.get("nodes") if isinstance(graph_.get("nodes"), dict) else {}
    order, queue = [], [graph_.get("start")]
    while queue:
        name = queue.pop(0)
        if isinstance(name, str) and name in nodes and name not in order:
            order.append(name)
            queue += graph.exits(nodes[name]) if isinstance(nodes[name], dict) else []
    order += [name for name in nodes if name not in order]
    out = [_plain(f"  {name}: {_step_words(nodes[name], full, lang)}" if isinstance(nodes[name], dict) else f"  {name}: ?")
           for name in order]
    return [_wrapped(line) for line in out] if full else out


def _trigger(trigger, lang: str = "en") -> str:
    """How a habit starts, in words."""
    if trigger is None:
        return say("trigger_name", lang)
    if trigger == "message":
        return say("trigger_request", lang)
    if trigger == "skill":
        return say("trigger_skill", lang)
    if isinstance(trigger, dict) and isinstance(trigger.get("every"), str):
        return say("trigger_every", lang, period=period(trigger["every"], lang)) + (
            say("trigger_at", lang, at=trigger["at"]) if isinstance(trigger.get("at"), str) else "")
    return json.dumps(trigger, ensure_ascii=False, default=str)  # an invalid trigger, as written


def _status(habit: catalog.Habit, summary: dict) -> str:
    """What a habit is now: a one-off has no shadow phase, it runs live by name until it is retired."""
    if habit.kind == "one-off" and summary.get("status") != "retired" and not graph.starts_on_skill(habit.graph):
        return "live"
    return summary.get("status", "shadow")


def _why(summary: dict) -> str:
    """Why a habit is in shadow or retired when a threshold or a person moved it there, as " · <note>"
    ("saved" and "new version" go without saying)."""
    note = summary.get("note") or ""
    if summary.get("status") in ("shadow", "retired") and note not in ("", "saved", "new version"):
        return f" · {note}"
    return ""


def _check_file(path: str, allow: dict | None = None, lang: str = "en") -> int:
    """`hermes habits check <file>`: what habit_run and habit_save would say of the reflex, without running it: the
    same file checks, the same fixes of the usual slips (said, so the file can be written so), the same rules,
    Hermes's tool names and their arguments."""
    from .tools import graph_source, loaded, problems_of
    try:
        habit, notes = loaded(graph_source({"file": path}))
    except ValueError as exc:
        print(say("check_format", lang, why=exc))
        return 1
    _load_tools()
    for note in notes:
        print(say("check_normalized", lang, note=note))
    issues = problems_of(habit, allow=allow)
    for issue in issues:
        print(issue)
    if not issues:
        print(say("check_valid", lang))
    return 1 if issues else 0


# The module of each Hermes tool a habit calls most: a run imports those alone. Hermes's discovery of every built-in
# tool takes 200-225 ms, the terminal's module alone 100 ms; its first call imports part of the rest, so a run of a
# one-step habit saves about 45 ms (09/11, in Hermes's Python, median of 5).
TOOL_MODULES = {"terminal": "tools.terminal_tool",
                **dict.fromkeys(("read_file", "write_file", "patch", "search_files"), "tools.file_tools")}


def _load_tools(names=None):
    """Hermes loads its built-in tools only for agent runs; a CLI command must ask. names: the tools one run calls,
    whose modules alone are imported when Hermes then knows each of them; every tool otherwise."""
    try:
        from tools.registry import discover_builtin_tools, registry
        if names is not None and set(names) <= set(TOOL_MODULES):
            for module in sorted({TOOL_MODULES[name] for name in names}):
                importlib.import_module(module)
            if all(registry.get_entry(name) is not None for name in names):
                return
        discover_builtin_tools()
    except Exception as exc:
        logger.warning("habits: could not load Hermes tools: %s", exc)
