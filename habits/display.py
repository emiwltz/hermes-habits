"""What people see: `hermes habits` in a terminal, /habits <name> and the diagram pages (the cockpit has /habits
itself and its decisions).

A habit's own text reaches a chat without MEDIA: directives, so a page it read cannot make the gateway
attach a file.
"""

from __future__ import annotations

import importlib
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import catalog, graph, view
from .texts import TEXTS, say

if TYPE_CHECKING:  # the class these methods are mixed into
    from .plugin import Habits

logger = logging.getLogger(__name__)

HELP = say("help", "en")
CLI_VERBS = {"list", "show", "check", "run", "tick", "retire", "reset", "cleanup"}
# A chat line printed in a terminal: its /habits commands said as `hermes habits` ones (faux is no terminal verb: its
# nearest is undo, which takes the yes back).
TERMINAL = ((r"/habits (?:reprends|resume)\b", "hermes habits resume"), (r"/habits (?:faux|wrong)\b", "hermes habits undo"),
            (r"/habits (?:essai|try)\b", "hermes habits reset"), (r"/habits (?:oui|yes)\b", "hermes habits promote"),
            (r"/habits (?:repos|rest)\b", "hermes habits retire"), (r"/habits (?:annule|undo)\b", "hermes habits undo"),
            (r"/habits (pause|view)\b", r"hermes habits \1"), (r"/habits (?=lists them|les liste)", "hermes habits list "),
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
                 habit.description, say("info", lang, trigger=_trigger(habit.graph.get("trigger"), lang),
                                        version=habit.version)]
        if habit.kind == "watch":
            lines.append(say("detail_checks", lang, checks=count("checks", summary["runs"], lang),
                             wakes=count("wakes", summary["woke"], lang)))
        extra = []
        if summary.get("shadow_ms") and summary.get("active_ms"):
            extra.append(say("detail_gain", lang, fast=seconds(summary["active_ms"], lang),
                             slow=seconds(summary["shadow_ms"], lang)))
        if summary.get("shadow_tokens") and summary.get("active_tokens") is not None:
            extra.append(say("detail_tokens", lang, active=tokens(summary["active_tokens"]),
                             shadow=tokens(summary["shadow_tokens"])))
        lines += [" · ".join(extra)] if extra else []
        if habit.issues:
            lines += [say("detail_invalid", lang)] + [f"  {i}" for i in habit.issues]
        for run in self.ledger.runs(habit.name, 5):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(run["at"]))
            agree = say("run_matched", lang, matched=run["matched"], planned=run["planned"]) if run["compared"] else ""
            agree += say("run_judge", lang, why=_no_media(run["verdict"])[:100]) if run.get("verdict") else ""
            turn = say("run_turn", lang, took=seconds(run["turn_ms"], lang)) if run["turn_ms"] else ""
            mode = say(f"run_{run['mode']}", lang) if f"run_{run['mode']}" in TEXTS["en"] else run["mode"]
            status = say(f"status_{run['status']}", lang) if f"status_{run['status']}" in TEXTS["en"] else run["status"]
            lines.append(f"  {when} · {mode} · {status} · {seconds(run['ms'], lang)}{turn}{agree}"
                         + (f" · {_no_media(run['reason'])}" if run["reason"] else ""))  # it holds tool output
        page = self.home() / "pages" / f"{habit.name}.html"
        if messaging(platform):
            lines.append(say("page_chat", lang, name=habit.name))
        else:
            lines.append(say("page", lang, path=str(page) if platform is None else f"`{page}`"))
        if platform is None:  # a terminal reader can open the file; in a chat the page is a command
            lines += [say("file", lang, path=str(habit.path)), say("steps", lang)] + steps(habit.graph)
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
            if words[:1] == ["help"] or (words and words[0] in CLI_VERBS and catalog.find(words[0]) is None):
                return say("help", self.lang())
            if words[:1] == ["view"]:
                path, said = self.view(words[1] if len(words) > 1 else "")
                if path is None:
                    return said
                if messaging(platform):  # the gateway sends a MEDIA: path as a file
                    return f"{say('view_open', self.lang(), said=said)}\nMEDIA:{path}"
                return say("view_at", self.lang(), said=said, uri=path.as_uri())
            if not words:
                return self.cockpit()
            return self.detail(words[0], str(platform or "")) + self.asks(words[0])
        except Exception as exc:
            logger.warning("habits: /habits failed: %s", exc)
            return say("error", self.lang(), kind=type(exc).__name__, exc=exc)

    def cli_setup(self, parser):
        sub = parser.add_subparsers(dest="habits_command")
        sub.add_parser("list", help="List habits with their status and scores")
        sub.add_parser("view", help="Open the gallery of your habits, or the diagram of one").add_argument(
            "name", nargs="?", default="")
        show = sub.add_parser("show", help="Details and recent runs")
        show.add_argument("name")
        show.add_argument("--json", action="store_true", help="where its file is, as a run finds it, and what it uses")
        sub.add_parser("promote", help="Let a habit act on its own: your yes, as /habits yes").add_argument("name")
        sub.add_parser("pause", help="Pause the habits: none starts from a message or a schedule (a call by name "
                       "still runs)")
        sub.add_parser("resume", help="Start the habits again after a pause")
        sub.add_parser("undo", help="Undo your last status change of the last 24 h").add_argument(
            "name", nargs="?", default="")
        for verb, text in (("tick", "Run a scheduled habit once, as its schedule does (used by cron)"),
                           ("retire", "Put a habit to rest by hand (a scheduled one loses its cron job)"),
                           ("reset", "Send a habit back to learning with fresh scores (one called by name: back "
                                     "to called by name)")):
            sub.add_parser(verb, help=text).add_argument("name")
        sub.add_parser("check", help="Validate a HABIT.yaml file").add_argument("file")
        run = sub.add_parser("run", help="Run a habit here without the model, dry unless --live; nothing is "
                             "recorded; prints the path of the replay page of that run")
        run.add_argument("target", help="a habit's name, or a path to a HABIT.yaml file")
        run.add_argument("--input", action="append", default=[], metavar="NAME=VALUE",
                         help="a value for one of its inputs (JSON when it reads as JSON); repeat for each")
        run.add_argument("--live", action="store_true", help="do its writes for real (never under ~/Downloads)")
        run.add_argument("--json", action="store_true", help="print one JSON object")
        run.add_argument("--within", metavar="DIR", default=None,
                         help="start its commands in DIR; with --live, refuse any write outside DIR")
        run.add_argument("--message", metavar="TEXT", default=None,
                         help="a user message: a request habit runs only if its trigger would take it")
        run.add_argument("--memory", metavar="FILE", default=None,
                         help="a JSON file that keeps what check: changed compares with, from one run to the next")
        sub.add_parser("cleanup", help="Remove the cron job and script of every scheduled habit (run it before removing the plugin)")

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
                    print(say("unknown_cli", self.lang(), name=args.name))
                    return 1
                print(terminal(self.detail(args.name) + self.asks(args.name)))
            elif verb == "check":
                return _check_file(args.file)
            elif verb == "view":
                return self.cli_view(args.name)
            elif verb == "run":
                return self.cli_run(args.target, args.input, args.live, args.json, getattr(args, "within", None),
                                    getattr(args, "message", None), getattr(args, "memory", None))
            elif verb == "tick":
                return self.cli_tick(args.name)
            elif verb == "promote":  # the user's yes in a terminal: on record as the terminal's
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
                print(f"removed: {self.unschedule_all() or 'nothing'}")
            elif verb in ("retire", "reset"):
                habit = catalog.find(args.name)
                if habit is None or habit.origin == "example":
                    print(say("unknown_cli" if habit is None else "example_byname", self.lang(), name=args.name))
                    return 1
                status = "retired" if verb == "retire" else "shadow"
                self.ledger.set_status(habit.name, self.version(habit), status, f"by hand ({verb})", by="cli")
                if verb == "retire" and habit.kind == "watch":
                    _load_tools()
                    removed = self.unschedule(habit.name)
                    print(f"removed: {removed or 'nothing'}")
                shown = _status(habit, {"status": status})
                print(f"{habit.name}: {say('state_byname' if shown == 'live' else f'state_{shown}', self.lang())}")
            return 0
        except Exception as exc:
            print(say("error", self.lang(), kind=type(exc).__name__, exc=exc))
            return 1

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
        print(json.dumps({"habit": habit.name, "path": str(habit.path), "kind": habit.kind, "origin": habit.origin,
                          "version": habit.version, "valid": habit.valid, "issues": habit.issues, "uses": uses},
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


def _short(text) -> str:
    return " ".join(_plain(text).split())[:STEP_TEXT]


def _step_words(node: dict) -> str:
    """One step in a few words: its verb, then what it does, then where it leads."""
    from .cockpit import _step
    kind = graph.kind_of(node)
    if kind == "do":
        command = _step(node) if node.get("do") == "terminal" else ""
        words = f"do {node['do']}" + (f" {command[:STEP_TEXT].rstrip()}" if command else "")
    elif kind == "use":
        words = f"use {node['use']}"
    elif kind == "ask":
        words = f'ask "{_short(node["ask"])}"'
    elif kind == "check":
        test = node["check"]
        if isinstance(test, dict) and test:
            op, value = next(iter(test.items()))
            words = "check " + (f"{value[0]} {op} {value[1]}" if isinstance(value, list) and len(value) == 2
                                else f"{value} {op}")
        else:
            words = "check"
    elif kind in ("end", "wake"):
        words = f'{kind} "{_short(node[kind])}"'
    else:
        words = kind or "?"  # an llm step is told by its verb alone
    then = node.get("then")
    if isinstance(then, dict) and then:
        return f"{words} → " + ", ".join(f"{answer} {target}" for answer, target in then.items())
    return f"{words} → {node['next']}" if isinstance(node.get("next"), str) else words


def steps(graph_: dict) -> list[str]:
    """One line per node, in the order a reader follows from the start (breadth first), then the nodes nothing leads
    to (a fallback): "  <name>: <what it does> → <where it goes>"."""
    nodes = graph_.get("nodes") if isinstance(graph_.get("nodes"), dict) else {}
    order, queue = [], [graph_.get("start")]
    while queue:
        name = queue.pop(0)
        if isinstance(name, str) and name in nodes and name not in order:
            order.append(name)
            queue += graph.exits(nodes[name]) if isinstance(nodes[name], dict) else []
    order += [name for name in nodes if name not in order]
    return [_plain(f"  {name}: {_step_words(nodes[name])}" if isinstance(nodes[name], dict) else f"  {name}: ?")
            for name in order]


def _trigger(trigger, lang: str = "en") -> str:
    """How a habit starts, in words."""
    if trigger is None:
        return say("trigger_name", lang)
    if trigger == "message":
        return say("trigger_request", lang)
    if isinstance(trigger, dict) and isinstance(trigger.get("every"), str):
        return say("trigger_every", lang, every=trigger["every"]) + (
            say("trigger_at", lang, at=trigger["at"]) if isinstance(trigger.get("at"), str) else "")
    return json.dumps(trigger, ensure_ascii=False, default=str)  # an invalid trigger, as written


def _status(habit: catalog.Habit, summary: dict) -> str:
    """What a habit is now: a one-off has no shadow phase, it runs live by name until it is retired."""
    if habit.kind == "one-off" and summary.get("status") != "retired":
        return "live"
    return summary.get("status", "shadow")


def _why(summary: dict) -> str:
    """Why a habit is in shadow or retired when a threshold or a person moved it there, as " · <note>"
    ("saved" and "new version" go without saying)."""
    note = summary.get("note") or ""
    if summary.get("status") in ("shadow", "retired") and note not in ("", "saved", "new version"):
        return f" · {note}"
    return ""


def tokens(n) -> str:
    """12345 -> 12.3k, 0 -> 0."""
    n = int(n or 0)
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _check_file(path: str) -> int:
    """`hermes habits check <file>`: what habit_run and habit_save would say of the graph, without running it: the
    same file checks, the same fixes of the usual slips (said, so the file can be written so), the same rules,
    Hermes's tool names and their arguments."""
    from .tools import graph_source, loaded, problems_of
    try:
        habit, notes = loaded(graph_source({"file": path}))
    except ValueError as exc:
        print(f"format: {exc}")
        return 1
    _load_tools()
    for note in notes:
        print(f"normalized: {note}")
    issues = problems_of(habit)
    for issue in issues:
        print(issue)
    if not issues:
        print("valid: the graph follows every rule.")
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
