"""Run a habit graph: the decision model decides, Hermes tools act, every step is traced.

The same graph with the same decision-model answers always takes the same path. Anything
that goes wrong (an error without an exit, an unsure answer, a budget) ends on
the fallback, a wake node, with the reason: a habit never fails silently.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import shlex
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from . import deciders, graph, shell

STEP_OUT = 400  # characters of output kept per step in the trace
MESSAGE_LIMIT = 8000  # characters of an end or wake message: it can carry a whole page
TRACE_LIMIT = 64_000
MAX_DEPTH = 3
# A pick: its options are the items, cut to this many characters in the question (80 in the trace), and
# "none of them" last; the top one is taken only this far over the second, unless the node says another margin.
PICK_TEXT = 200
PICK_MARGIN = 0.15
NONE_OF_THEM = "None of them: no item is the one asked for, or the question cannot tell"
OUTSIDE = ("curl", "ssh", "hc")  # readers that reach another machine
SCREENS = [("hc", "read"), ("hc", "get")]  # reads that print an agent's screen (get: .screen, .title), which can hold
# any page it read; and the same verbs after hc anywhere in a line (nice hc read, /x/bin/hc get, through ssh)
SCREEN = re.compile(r"(?<![\w.-])hc\b.*?(?<![\w.-])(?:read|get)(?![\w.-])", re.S)
# Programs that print what a server sent, written as a word anywhere in a line (behind nice or timeout too).
FETCHERS = re.compile(r"(?<![\w.-])(?:curl|wget)(?![\w.-])")
# -w variables a server cannot fill with its own text: numbers, and the URL as the graph gave it (no -L).
NUMBERS = re.compile(r"(?:http_code|response_code|http_connect|exitcode|num_\w+|size_\w+|speed_\w+|time_\w+|"
                     r"ssl_verify_result|remote_ip|remote_port|local_ip|local_port|http_version|scheme|url|url_effective)")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
PLAIN = re.compile(r"[A-Za-z0-9/_.-]+")  # a value an ssh remote command may take: one word to any shell
MEDIA_LINE = re.compile(r"^\s*MEDIA:\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\}\s*$")  # a whole line, as written
EXIT_ONE = {"grep", "egrep", "fgrep", "diff", "cmp", "test", "["}  # exit 1: no match, differs, false
# Hermes's terminal gives one status, the last command's, and joins stderr to the output: after a read that is one
# pipe, the runner prints bash's own statuses on a last line of its own, then takes that line off the value.
PIPE = "habits-pipe"
PIPE_LINE = f"\nprintf '\\n%s %s %s\\n' {PIPE} \"$?\" \"${{PIPESTATUS[*]}}\""
SIGPIPE = 141  # a writer whose reader stopped early (head, grep -m): not a failure
# A file whose name looks like a secret, in any case: no habit attaches or sends one, whoever wrote the graph (one
# habit refused PASSWD.md by itself). A whole name: .env and its variants, ssh keys (_sk ones too), a key,
# a certificate or a key store, auth.json; a word of the name (no letter right before or after it, so Tokenomics.md
# and passwordless.md are notes): credentials, passwd, password, secret, token, api key, mdp, mot de passe. A backup
# keeps its secret's name: id_rsa.bak, .env~, cert.pem.old, auth.json.1. A .key that is a package or a zip is a
# Keynote deck. (.+\.env last: written before a | it read as an env dump to Hermes's plugin security scan)
SECRET_FILE = re.compile(r"\.env(?:\..+)?|id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?(?:\..+)?|"
                         r".+\.(?:pem|key|p12|pfx|jks|keystore|kdbx|ppk)|auth\.json|.+\.env", re.I)
SECRET_WORD = re.compile(r"(?<![^\W\d_])(?:credentials?|passw(?:or)?ds?|secrets?|tokens?|api[\s_.-]?keys?|mdp|"
                         r"mots?[\s_.-]*de[\s_.-]*passe)(?![^\W\d_])", re.I)
BACKUP = re.compile(r"(?:\.(?:bak|old|orig|save|backup|copy|tmp|swp|\d+)|~)+$", re.I)
KEYNOTE = re.compile(r".+\.key", re.I)
QUOTES = "'\"`*_ "  # around a path in a text: quotes, and Markdown's code and emphasis
MEDIA_TAG = re.compile(r"MEDIA:", re.I)  # the gateway reads its tag in any case
QUOTED = re.compile(r"""`([^`\n]+)`|"([^"\n]+)"|'([^'\n]+)'""")
SENDER = re.compile(r"(?<![\w.-])hermes(?![\w.-]).*?(?<![\w.-])send(?![\w.-])", re.S)  # nice ~/x/hermes send too
# Programs that send what they are given to another machine, written as a word anywhere in a line.
SENDS = re.compile(r"(?<![\w.-])(?:curl|wget|scp|sftp|rsync|rclone|ssh|nc|ncat|netcat|socat|ftp|telnet|gh|mail|mailx|"
                   r"sendmail|mutt)(?![\w.-])")
FILEISH = re.compile(r"[/~]|\.[A-Za-z0-9]{1,10}$")  # a word that may be a file: a path, or a name with an extension
SHORT = re.compile(r"-[A-Za-z].")  # a short option with its value glued on: -fPATH, -T'PATH'


class Stop(Exception):
    """Ends the run on the fallback, with a reason."""


class BadValue(Exception):
    """A value that cannot go into a tool call."""


@dataclass
class Env:
    tool: Callable[[str, dict], str]  # dispatch a Hermes tool, return its result text
    decider: deciders.Chain
    llm: Callable[[str, str, dict], dict] | None = None  # (instructions, text, json_schema) -> object
    habits: Callable[[str], dict] | None = None  # name -> graph, for use
    memory: dict = field(default_factory=dict)  # node -> last value, for check: changed
    dry: bool = False  # shadow: only read steps run, writes are planned
    clock: Callable[[], float] = time.monotonic
    depth: int = 0
    max_seconds: float | None = None  # a tighter time cap, e.g. inside a Hermes hook
    scope: str = ""  # memory prefix of a sub-habit, so check: changed stays per habit
    caps: dict | None = None  # what a caller has left: a sub-habit spends the caller's budgets
    workers: int = 8  # reads of an each that run at the same time (graph.PARALLEL_TOOLS only); 1: one by one
    workdir: str | None = None  # where terminal commands start, whatever folder the session is in
    writes: Callable[[str, dict], str] | None = None  # (tool, args) -> why a write may not run, "" when it may
    llm_in_commands: bool = False  # model_text: quoted -> a value an llm wrote may fill one operand of a terminal command
    deleting: bool = False  # permissions: hermes, a live run with the user's yes inside a turn -> a deleting command may run


@dataclass
class Result:
    status: str  # "end" or "wake"
    message: str
    node: str
    reason: str = ""
    failed: bool = False  # ended on the fallback because something went wrong
    doubts: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)
    planned: list = field(default_factory=list)  # writes noted in a dry run
    ms: int = 0
    counts: dict = field(default_factory=dict)
    backends: list = field(default_factory=list)
    unsafe: list = field(default_factory=list)  # values written by an llm node
    tainted: bool = False  # the run held a value written by an llm node somewhere
    tools: list = field(default_factory=list)  # the tools that ran (planned calls left out), sub-habits included
    kept: bool = False  # a check: changed set an empty or partly failed read aside: neither news nor a failure
    failed_calls: list = field(default_factory=list)  # [node, item key] of each call of an each that ran and failed
    untrusted: bool = False  # a read that ran printed an agent's screen (hc read): anyone's text, as a web page
    outage: bool = False  # no decision model answered (a sub-habit's too): the habit is not at fault
    files: list = field(default_factory=list)  # (variable, value) of each MEDIA:{variable} line of its end template,
    # a line taken out of the message: a host that sends no file just leaves it out

    @property
    def step_failed(self) -> bool:
        """It woke because a tool step failed: its last step is a do that failed, then fell back or took its error
        exit straight to a wake. A wake its graph chose (a check, an answer, a step that worked) is not one."""
        last = self.steps[-1] if self.steps else {}
        return self.status == "wake" and last.get("kind") == "do" and "error" in last

    def report(self) -> str:
        """What happened, for the LLM and the user."""
        used = ", ".join(sorted(set(self.backends))) or "rules only"
        lines = [f"{self.status} at {self.node!r} after {self.ms / 1000:.1f} s ({used}, "
                 f"{self.counts.get('questions', 0)} questions, {self.counts.get('tools', 0)} tool calls)"]
        if self.reason:
            lines.append(f"reason: {self.reason}")
        lines += [f"- {s['node']}: {s['summary']}" for s in self.steps]
        if self.planned:
            n = len(self.planned)
            lines.append(f"planned, not run (dry run): {n} call{'' if n == 1 else 's'}")
        if self.doubts:
            lines.append(f"doubts ({len(self.doubts)}), left for you:")
            lines += [f"  - {_show(d['item'])[:120]} at {d['node']}: {d['reason']}" for d in self.doubts[:20]]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """The stored trace: under 64 KB, its long values, doubts and planned writes cut first, then the message,
        then old steps."""
        data = {k: getattr(self, k) for k in ("status", "message", "node", "reason", "failed", "doubts", "values",
                                              "steps", "planned", "ms", "counts", "backends")}
        data = json.loads(json.dumps(data, default=str))

        def size():
            return len(json.dumps(data, ensure_ascii=False))
        if size() <= TRACE_LIMIT:
            return data
        for key, field in (("doubts", "item"), ("planned", "args")):
            for entry in data[key]:
                if len(json.dumps(entry.get(field), ensure_ascii=False)) > 400:
                    entry[field] = _short(entry[field])
        values, lists = data["values"], {key: data[key] for key in ("doubts", "planned")}
        # Each part under its own cap can still add up: a second pass cuts harder, so steps are not all lost.
        for value_size, kept in ((2000, 50), (200, 20)):
            data["values"] = {k: v if len(json.dumps(v, ensure_ascii=False)) <= value_size else _short(v, value_size)
                              for k, v in values.items()}
            for key, entries in lists.items():
                if size() > TRACE_LIMIT and len(entries) > kept:
                    data[key], data[f"{key}_cut"] = entries[:kept], len(entries) - kept
            if size() <= TRACE_LIMIT:
                return data
        data["message"] = _short(data["message"], 2000)
        while size() > TRACE_LIMIT and data["steps"]:
            data["steps"].pop(0)
        return data


def run(habit: dict, env: Env, inputs: dict | None = None, trigger: str = "", unsafe=()) -> Result:
    """Run a validated habit once. unsafe: input names whose values came from an llm node."""
    return _Run(habit, env, inputs or {}, trigger, set(unsafe)).go()


def _key(item) -> str:
    return item if isinstance(item, str) else json.dumps(item, sort_keys=True, ensure_ascii=False)


def _show(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "\n".join(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def _one_line(value) -> bool:
    text = str(value)
    return text == "".join(text.splitlines())  # no character str.splitlines() splits on


def _lines(value) -> str:
    """A table of scalars, one "key: value" per line; anything else as _show writes it."""
    # a key with ": " or a line break (any that str.splitlines() splits on), or a value with one, would not read back
    # as one entry per line
    if (isinstance(value, dict) and value
            and all(isinstance(v, (str, int, float, bool)) and _one_line(v) and _one_line(k)
                    and ": " not in str(k) for k, v in value.items())):
        return "\n".join(f"{k}: {_show(v)}" for k, v in value.items())
    return _show(value)


def _short(value, size=STEP_OUT) -> str:
    text = _show(value)
    return text if len(text) <= size else text[:size] + f"… (+{len(text) - size})"


def _output(text):
    """The main output of a tool result: its "output" field when it has one."""
    if not isinstance(text, str):
        return text, None
    try:
        data = json.loads(text)
    except ValueError:
        return text, None
    if isinstance(data, dict):
        if data.get("error"):
            return None, str(data["error"])[:300]
        code = data.get("exit_code", data.get("returncode"))
        out = data.get("output", data)
        if isinstance(code, int) and code != 0:
            return out, f"exit {code}: {_short(out, 200)}"
        return out, None
    return data, None


class _Run:
    def __init__(self, habit, env, inputs, trigger, unsafe=frozenset()):
        self.habit, self.env, self.nodes = habit, env, habit["nodes"]
        self.vars = {**inputs, "trigger": trigger}
        self.tables: set[str] = set()  # variables that map each item to a value
        self.unsafe: set[str] = set(unsafe) & set(inputs)  # variables written by an llm node
        self.outside: set[str] = set()  # of those, the ones whose only source is text from outside (a page, a screen)
        self.each_outside = False  # the each being rendered runs over such a value: its {item} is text from outside
        self.partial: set[str] = set()  # tables of a do whose calls partly failed: never a check: changed reference
        self.kept = False  # a check: changed set a read aside
        self.failed_calls = []  # [node, item key]: a call that ran and failed, not a value refused before it
        self.untrusted = False  # an agent's screen was read
        self.outage = False  # no decision model answered
        self.doubts: list[dict] = []
        self.doubted: set[str] = set()
        self.steps: list[dict] = []
        self.planned: list[dict] = []
        self.backends: list[str] = []
        self.called: set[str] = set()
        self.counts = {"steps": 0, "tools": 0, "questions": 0, "llm": 0}
        self.limits = graph.limits(habit)
        if env.max_seconds:
            self.limits["seconds"] = min(self.limits["seconds"], env.max_seconds)
        for what, left in (env.caps or {}).items():
            self.limits[what] = min(self.limits[what], left)
        self.threshold = habit.get("min_confidence", 0.8)
        self.start = env.clock()

    # ---- the loop -------------------------------------------------------

    def go(self) -> Result:
        name = self.habit["start"]
        try:
            for _ in range(len(self.nodes) + 1):  # a valid graph has no loop
                node = self.nodes[name]
                kind = graph.kind_of(node)
                if kind in graph.TERMINALS:
                    return self.finish(name, node, kind)
                self.spend("steps")
                began = self.env.clock()
                step = {"node": name, "kind": kind, "t": self.ms(began)}
                try:
                    name = getattr(self, "_" + kind)(name, node, step)
                except (Stop, deciders.DecisionError):
                    step["stopped"] = True  # cut short: unlike a step that finished, it is still to do
                    raise
                finally:
                    step["ms"] = self.ms(self.env.clock(), began)
                    step.setdefault("summary", "")
                    self.steps.append(step)
            raise Stop("the graph did not end")
        except Stop as stop:
            return self.fall(str(stop))
        except deciders.DecisionError as exc:
            self.outage = True
            return self.fall(str(exc))
        except Exception as exc:  # an engine bug still ends on the fallback, recorded
            return self.fall(f"engine error: {type(exc).__name__}: {exc}")

    def ms(self, now, since=None) -> int:
        return int(round((now - (self.start if since is None else since)) * 1000))

    def spend(self, what: str, amount: int = 1):
        self.counts[what] += amount
        if self.counts[what] > self.limits[what]:
            raise Stop(f"budget: more than {self.limits[what]} {what}")
        if self.env.clock() - self.start > self.limits["seconds"]:
            raise Stop(f"budget: more than {self.limits['seconds']} s")

    def exit_error(self, name, node, step, reason):
        step["error"] = reason
        step["summary"] = f"error: {reason}"
        if isinstance(node.get("error"), str):
            return node["error"]
        raise Stop(f'"{name}": {reason}')

    def fall(self, reason: str) -> Result:
        fallback = self.habit.get("fallback")
        node = self.nodes.get(fallback) if fallback else None
        if not isinstance(node, dict) or "wake" not in node:
            result = self.result("wake", "The habit stopped.", fallback or "", reason, {})
        else:
            result = self.result("wake", self.text(node["wake"], table=True), fallback, reason, self.values(node))
        result.failed = True
        return result

    def finish(self, name, node, kind) -> Result:
        template, files = str(node[kind]), []
        if kind == "end":  # a file the end attaches goes apart: the host checks where its value came from first
            lines = template.splitlines()
            template = "\n".join(line for line in lines if not MEDIA_LINE.match(line))
            for found in filter(None, map(MEDIA_LINE.match, lines)):
                try:
                    files.append((found.group(1), self.get(found.group(1), missing=BadValue)))
                except BadValue:
                    pass
        message = self.text(template, table=True)
        reason = ""
        # the file a host would send: its name as given, and the name of what a link points to
        secret = next((p for _, value in files for p in _paths(value) for p in (p, real_path(p)) if secret_name(p)),
                      None)
        if kind == "end" and secret is not None:
            kind, files = "wake", []
            reason = (f"it would attach {_name(secret)}, whose name looks like a secret (a key, a password, a token, "
                      ".env): a habit never sends such a file")
        elif kind == "end" and self.doubts:
            kind, reason, files = "wake", f"{len(self.doubts)} doubts left", []
        result = self.result(kind, message, name, reason, self.values(node))
        result.files = files
        return result

    def values(self, node, item=None) -> dict:
        found = {}
        for name in node.get("with", []) or []:
            if isinstance(name, str):
                try:
                    found[name] = self.get(name, item, missing=BadValue)
                except BadValue:
                    found[name] = None
        return found

    def result(self, status, message, node, reason, values) -> Result:
        steps = _capped(self.steps)
        return Result(status=status, message=_short(message, MESSAGE_LIMIT), node=node, reason=reason,
                      doubts=self.doubts, values=values, steps=steps, planned=self.planned, ms=self.ms(self.env.clock()),
                      counts=dict(self.counts), backends=self.backends,
                      unsafe=[v for v in values if v in self.unsafe], tainted=bool(self.unsafe),
                      tools=sorted(self.called), kept=self.kept, failed_calls=self.failed_calls,
                      untrusted=self.untrusted, outage=self.outage)

    # ---- values ---------------------------------------------------------

    def get(self, path: str, item=None, missing=Stop):
        head, *rest = path.split(".")
        if head == "item":
            if item is None:
                raise missing('"item" only exists inside each')
            value = item
        elif head == "doubts":
            value = [dict(d) for d in self.doubts]
        elif head in self.vars:
            value = self.vars[head]
            if head in self.tables and item is not None:
                if not isinstance(value, dict) or _key(item) not in value:
                    raise missing(f'"{head}" has no value for this item')
                value = value[_key(item)]
        else:
            raise missing(f'"{head}" has no value')
        for part in rest:
            if isinstance(value, dict) and part in value:
                value = value[part]
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                raise missing(f'"{path}" has no value')
        return value

    def text(self, template: str, item=None, table=False) -> str:
        """A message or a question: templates replaced by plain text. table: a flat table reads as lines (end, wake)."""
        show = _lines if table else _show

        def sub(match):
            try:
                return show(self.get(match.group(0)[1:-1], item, missing=BadValue))
            except BadValue:
                return match.group(0)
        return graph.TEMPLATE.sub(sub, template)

    def render(self, tool: str, args, item, tainted=False):
        """Tool arguments with templates filled; terminal values are shell-quoted.

        tainted: the each list came from an llm node, so {item} is unsafe too.
        """
        if isinstance(args, dict):
            return {k: self.render_value(tool, k, v, item, tainted) for k, v in args.items()}
        return {}

    def render_value(self, tool, key, value, item, tainted=False):
        if isinstance(value, dict):
            return {k: self.render_value(tool, key, v, item, tainted) for k, v in value.items()}
        if isinstance(value, list):
            return [self.render_value(tool, key, v, item, tainted) for v in value]
        if not isinstance(value, str):
            return value
        if tool == "terminal":
            risky = set()
            for path in (m.group(0)[1:-1] for m in graph.TEMPLATE.finditer(value)):
                head = path.split(".")[0]
                if head in self.unsafe or (head == "item" and tainted):
                    risky.add(head)
                    # model_text: never keeps a model's text out of every command; text from outside is held to the
                    # code positions and the deleting rule (checked below), and is still one quoted operand.
                    if not self.env.llm_in_commands and not (head in self.outside or head == "item" and self.each_outside):
                        raise BadValue(f'"{path}" was written by an llm step and cannot enter a terminal call')
            # Whatever the switch says, such a value (a model's, or text a page, a server or a screen sent) is never code:
            # the program, the script of sed/awk/jq, an option that takes code, a command that deletes.
            where = graph._code_position(value, risky) if risky and key == "command" else ""
            if where:
                raise BadValue(f"a value written by an llm step or read from a tool's output cannot go {where}")
        line = tool == "terminal" and key == "command"
        whole = graph.TEMPLATE.fullmatch(value)
        if whole and not line:
            return self.get(value[1:-1], item, missing=BadValue)
        # An ssh sends these to the remote shell, which reads them again: the quotes put here are used up there.
        remote = shell.remote(value) if line and "ssh" in value else set()
        if remote is None:
            remote = set(range(len(value)))  # a line the reader cannot read: every value, for all it knows
        count = iter(range(len(value)))

        def sub(match):
            path, at = match.group(0)[1:-1], next(count)
            text = _show(self.get(path, item, missing=BadValue))
            if not line:
                return text
            # A backtick too: inside backticks, the shell ends them at the value's own before reading its quotes.
            if ".." in text or text.startswith("-") or "\n" in text or "\r" in text or "`" in text:
                raise BadValue(f"refused value {text[:60]!r}: contains .. or a backtick, starts with - or spans lines")
            if at in remote and not PLAIN.fullmatch(text):
                raise BadValue(f"refused value {text[:60]!r} in an ssh remote command: the remote shell reads it "
                               "again, so only letters, digits, / _ . and - pass")
            return shlex.quote(text)
        filled = graph.TEMPLATE.sub(sub, value)
        if line and graph._deletes(filled):
            # A deleting command with a value an llm wrote in it never runs, whatever the switches: only one the reflex
            # itself holds may pass, and only when the switch lets it.
            written = any(m.group(0)[1:-1].split(".")[0] in self.unsafe or (m.group(0)[1:-1].split(".")[0] == "item"
                                                                          and tainted)
                          for m in graph.TEMPLATE.finditer(value))
            if written:
                raise BadValue("the filled command would delete files with a value a model or a tool wrote: that "
                               "never runs")
            if not self.env.deleting and not self.env.dry:  # a test run only plans, so it can show the delete
                raise BadValue("the filled command would delete files: a habit may do that only with your yes and "
                               "permissions set to hermes")
        return filled

    @staticmethod
    def still_read(node, args):
        """A terminal step marked read: true was checked as a template at validation; a value filled in can turn it into
        something else (curl -s {url} with file:// or gopher://). Check the filled command with the same reader."""
        command = args.get("command") if isinstance(args, dict) else None
        if node.get("read") is True and node.get("do") == "terminal" and isinstance(command, str):
            cause = shell.why(command)
            if cause:
                raise BadValue(f"the filled command is no longer a read ({cause}): {command[:80]!r}")

    def items(self, node, key="each") -> list:
        """The items of an each (or of a pick, which also takes a table's keys), filtered by where, without the
        doubts."""
        value = self.get(node[key])
        if isinstance(value, str):
            items = [line.strip() for line in value.splitlines() if line.strip()]
        elif isinstance(value, list):
            items = value
        elif isinstance(value, dict) and key == "pick":
            items = list(value)
        else:
            raise Stop(f'"{node[key]}" is not a list or text')
        where = node.get("where") or {}
        kept = []
        for item in items:
            if node[key].split(".")[0] != "doubts" and _key(item) in self.doubted:
                continue
            try:
                if all(_show(self.get(k, item, missing=BadValue)) == _show(v) for k, v in where.items()):
                    kept.append(item)
            except BadValue:
                continue
        return kept

    def doubt(self, item, node, reason, confidence=None, unsafe=False):
        """unsafe: the item comes from a list an llm wrote, so doubts holds that llm's text from now on."""
        if unsafe:
            self.unsafe.add("doubts")
        self.doubted.add(_key(item))
        self.doubts.append({"item": item, "node": node, "reason": reason, "confidence": confidence})

    def origin(self, node) -> dict:
        """store() keywords for a do step's output: unsafe when the step ran over, or its arguments hold, a value a model
        wrote or a tool's output carried (jq, grep, echo over a page print text derived from it: one step of laundering
        does not clean it), or when the step itself reads from outside; outside when every such source is text from outside."""
        each = node["each"].split(".")[0] if isinstance(node.get("each"), str) else None
        heads = (graph._references(node.get("args")) | ({each} if each else set())) & self.unsafe
        unsafe = bool(heads) or graph.from_outside(node)
        return {"unsafe": unsafe, "outside": unsafe and all(h in self.outside for h in heads)}

    def store(self, node, value, table=False, unsafe=False, partial=False, outside=None):
        """unsafe: written by an llm node, or a table an each built over such a value, whose keys are its items (a
        pick takes one of them)."""
        name = node.get("as")
        if not isinstance(name, str):
            return
        self.vars[name] = value
        (self.tables.add if table else self.tables.discard)(name)
        (self.unsafe.add if unsafe else self.unsafe.discard)(name)
        if outside is None:  # taken from a value by an each or a pick: as outside as that value
            sources = {node[key].split(".")[0] for key in ("each", "pick") if isinstance(node.get(key), str)}
            outside = bool(sources) and all(h in self.outside for h in sources)
        (self.outside.add if unsafe and outside else self.outside.discard)(name)
        (self.partial.add if partial else self.partial.discard)(name)

    def state(self, names, item=None) -> str:
        """The state an ask gives the decision model; with item, a table's entry for that item."""
        parts = []
        for name in names or []:
            try:
                parts.append(f"{name}:\n{_show(self.get(name, item))}")
            except Stop:
                parts.append(f"{name}: (no value)")
        return "\n\n".join(parts)[: deciders.STATE_LIMIT]

    def decide(self, state, questions, step):
        self.spend("questions", len(questions))
        chain = self.env.decider
        before = len(chain.used)
        left = self.limits["seconds"] - (self.env.clock() - self.start)  # one deadline for the whole run
        try:
            answers = chain.ask(state, questions, left)
        except deciders.OutOfTime:  # its time was spent waiting for an answer: a budget, not an outage
            raise Stop(f"budget: more than {self.limits['seconds']} s") from None
        used = chain.used[before:]
        self.backends += used
        step["backend"] = ",".join(dict.fromkeys([b for b in step.get("backend", "").split(",") if b] + used))
        return answers

    # ---- the nodes ------------------------------------------------------

    def _ask(self, name, node, step):
        threshold = node.get("min_confidence", self.threshold)
        state = self.state(node.get("with"))
        if "pick" in node:
            return self._pick(name, node, step, threshold, state)
        if "each" in node:
            items = self.items(node)
            if items:
                self.spend("steps", len(items) - 1)
            questions = [deciders.question(f"{self.text(node['ask'], item)}\n{_show(item)}", node) for item in items]
            # A table in with gives each question its item's own entry (an agent's screen): Jev, unsure with every
            # screen in one state or in the question, was sure with its own. One call per distinct state.
            own = any(isinstance(n, str) and n.split(".")[0] in self.tables for n in node.get("with") or [])
            states = [self.state(node.get("with"), item) if own else state for item in items]
            answers = [None] * len(items)
            for shared in dict.fromkeys(states):
                at = [k for k, one in enumerate(states) if one == shared]
                for k, answer in zip(at, self.decide(shared, [questions[k] for k in at], step)):
                    answers[k] = answer
            table, tally, dropped = {}, {}, []
            for item, answer in zip(items, answers):
                if answer.confidence < threshold and node.get("doubts") == "drop":
                    dropped.append([_short(item, 80), round(answer.confidence, 3)])  # left out, never acted on
                    continue
                if answer.confidence < threshold:
                    self.doubt(item, name, f"unsure ({answer.confidence:.2f} under {threshold})", answer.confidence,
                               unsafe=node["each"].split(".")[0] in self.unsafe)
                    continue
                table[_key(item)] = answer.value
                tally[answer.value] = tally.get(answer.value, 0) + 1
            self.store(node, table, table=True, unsafe=node["each"].split(".")[0] in self.unsafe)  # keys: the items
            step["answers"] = [[_short(i, 80), a.value, round(a.confidence, 3)] for i, a in zip(items, answers)]
            if any(a.probabilities for a in answers):  # each option's, in the answers' order (Jev gives them)
                step["probabilities"] = [a.probabilities for a in answers]
            counted = ", ".join(f"{v} {n}" for v, n in tally.items())
            doubted = len(items) - len(table) - len(dropped)
            step["summary"] = f"{len(items)} answers ({counted or 'none'})" + (
                f", {doubted} doubts" if doubted else "") + (f", {len(dropped)} dropped" if dropped else "")
            if dropped:
                step["dropped"] = dropped
            return node["next"]
        q = deciders.question(self.text(node["ask"]), node)
        answer = self.decide(state, [q], step)[0]
        step["answers"] = [[None, answer.value, round(answer.confidence, 3)]]
        if answer.probabilities:
            step["probabilities"] = [answer.probabilities]
        then = {str(k): v for k, v in (node.get("then") or {}).items()}
        if answer.confidence < threshold:
            step["summary"] = f"unsure: {answer.value} at {answer.confidence:.2f}"
            if isinstance(node.get("as"), str):
                self.vars.pop(node["as"], None)
            if "unsure" in then:
                return then["unsure"]
            raise Stop(f'"{name}" was unsure ({answer.confidence:.2f} under {threshold})')
        self.store(node, answer.value)
        step["summary"] = f"{answer.value} ({answer.confidence:.2f})"
        return then[answer.value] if then else node["next"]

    def _pick(self, name, node, step, threshold, state):
        """One choice question whose options are the items ("1", "2"… with their text, "none" last): the top one is
        taken when it is at the threshold and the margin over the second; else unsure, or the fallback."""
        source = self.get(node["pick"])
        items = list({_key(item): item for item in self.items(node, "pick")}.values())  # each distinct item once
        texts = [f"{item}: {_show(source[item])}" if isinstance(source, dict) else _show(item) for item in items]

        def unsure(why):
            step["summary"] = f"unsure: {why}"
            if isinstance(node.get("as"), str):
                self.vars.pop(node["as"], None)
            if isinstance(node.get("unsure"), str):
                return node["unsure"]
            raise Stop(f'"{name}" was unsure ({why})')
        if not items:
            return unsure("no item to pick")
        if len(items) >= deciders.MAX_OPTIONS:
            return unsure(f"{len(items)} items, more than the {deciders.MAX_OPTIONS - 1} one choice question can offer")
        labels = [str(k) for k in range(1, len(items) + 1)]
        q = deciders.Question(self.text(node["ask"]), "choice", (*labels, "none"),
                              (*((label, _short(text, PICK_TEXT)) for label, text in zip(labels, texts)),
                               ("none", NONE_OF_THEM)))
        answer = self.decide(state, [q], step)[0]
        # Without each option's probability (the LLM fallback), the second has what the top one leaves at most.
        others = [p for option, p in answer.probabilities.items() if option != answer.value]
        second = max(others, default=0.0) if answer.probabilities else 1 - answer.confidence
        gap, margin = round(answer.confidence - second, 3), node.get("margin", PICK_MARGIN)
        chosen = items[labels.index(answer.value)] if answer.value in labels else None
        step["answers"] = [[None if chosen is None else _short(chosen, 80), answer.value, round(answer.confidence, 3)]]
        step["options"] = {label: _short(text, 80) for label, text in zip(labels, texts)}
        if answer.probabilities:
            step["probabilities"] = [answer.probabilities]
        step["margin"] = gap
        if chosen is None:
            return unsure(f"none of them at {answer.confidence:.2f}")
        if answer.confidence < threshold:
            return unsure(f"{answer.value} at {answer.confidence:.2f} under {threshold}")
        if gap < margin:
            return unsure(f"margin {gap:.2f} under {margin}")
        self.store(node, chosen, unsafe=node["pick"].split(".")[0] in self.unsafe)
        step["summary"] = f"picked {_short(chosen, 80)} ({answer.confidence:.2f}, margin {gap:.2f})"
        return node["next"]

    def _check(self, name, node, step):
        op, arg = next(iter(node["check"].items()))
        var, ref = (arg, None) if op in ("empty", "changed") else (arg[0], arg[1])
        value = self.get(str(var))
        kept = op == "changed" and str(var).split(".")[0] in self.partial  # some of its calls failed: never a reference
        if op == "changed" and self.condition(name, "empty", value, None):
            value = ""  # every empty read is one value
            # After a filled value it tells no change: no, and the reference stays. With no reference yet, or an
            # empty one, it is the reference: a folder empty at first wakes on its first file.
            kept = kept or self.env.memory.get(self.env.scope + name, "") != ""
        self.kept |= kept
        try:
            yes = not kept and self.condition(name, op, value, ref)
        except (TypeError, ValueError) as exc:
            raise Stop(f'"{name}" cannot compute {op}: {exc}') from None
        answer = "yes" if yes else "no"
        step["summary"] = f"{op} {var}: {answer}" + (" (an empty or failed read: the last value stays)" if kept else "")
        return node["then"][answer]

    def condition(self, name, op, value, ref) -> bool:
        if op == "empty":
            return value in (None, "", [], {}) or (isinstance(value, str) and not value.strip())
        if op == "equals":
            return _show(value).strip() == _show(ref).strip()
        if op == "contains":
            if isinstance(value, dict):  # a table from each: look at the answers, not the items
                return any(_show(v) == _show(ref) for v in value.values())
            if isinstance(value, list):
                return any(_show(v) == _show(ref) for v in value)
            return _show(ref) in _show(value)
        if op == "greater":
            return _number(value) > _number(ref)
        current = _key(value)  # changed: compared with the last run of this node of this habit
        key = self.env.scope + name
        changed = key in self.env.memory and self.env.memory[key] != current  # the first run sets the reference
        self.env.memory[key] = current
        return changed

    def _do(self, name, node, step):
        tool, read = node["do"], _reads(node)
        self.each_outside = False
        if "each" not in node:
            try:
                args = self.render(tool, node.get("args"), None)
                self.still_read(node, args)
            except BadValue as exc:
                return self.exit_error(name, node, step, str(exc))
            out, error = self.call(name, tool, args, read)
            if error:
                return self.exit_error(name, node, step, error)
            self.store(node, out, **self.origin(node))
            step["out"] = _short(out)
            step["summary"] = "planned (dry run)" if self.env.dry and not read else f"{tool} ok"
            return node["next"]
        items = self.items(node)
        if items:
            self.spend("steps", len(items) - 1)
        outputs, failures, calls = {}, [], []
        for item in items:
            try:
                self.each_outside = node["each"].split(".")[0] in self.outside
                args = self.render(tool, node.get("args"), item, tainted=node["each"].split(".")[0] in self.unsafe)
                self.still_read(node, args)
            except BadValue as exc:
                failures.append(str(exc))
                self.doubt(item, name, str(exc), unsafe=node["each"].split(".")[0] in self.unsafe)
                continue
            calls.append((item, args))
        if read and tool in graph.PARALLEL_TOOLS and self.env.workers > 1 and len(calls) > 1:
            for _ in calls:  # read tools Hermes itself runs side by side: the slowest call sets the time
                self.spend("tools")
            step["parallel"] = min(self.env.workers, len(calls))
            self.called.add(tool)
            done = self.call_all(tool, [args for _, args in calls], step["parallel"], step)
            self.spend("tools", 0)  # the batch may have used up the time: end on the fallback, as one by one would
        else:
            done = (self.call(name, tool, args, read) for _, args in calls)
        for (item, _), (out, error) in zip(calls, done):
            if error:
                failures.append(error)
                self.doubt(item, name, error, unsafe=node["each"].split(".")[0] in self.unsafe)
                self.failed_calls.append([name, _key(item)])
            else:
                outputs[_key(item)] = out
        self.store(node, outputs, table=True, partial=bool(failures), **self.origin(node))
        verb = "planned (dry run)" if self.env.dry and not read else "done"
        step["summary"] = f"{len(items)} {tool} call{'' if len(items) == 1 else 's'} {verb}" + (f", {len(failures)} failed" if failures else "")
        if "one_by_one_ms" in step:
            step["summary"] += f", side by side ({step['one_by_one_ms'] / 1000:.1f} s one by one)"
        if items and len(failures) == len(items):
            return self.exit_error(name, node, step, f"every call failed: {failures[-1]}")
        return node["next"]

    def call_all(self, tool, calls, workers, step):
        """Read calls at the same time, results in call order; each thread sees the caller's context.
        step["one_by_one_ms"] keeps what the calls would have taken one after the other."""
        took = []

        def one(args):
            if self.env.clock() - self.start > self.limits["seconds"]:
                return None, "budget: out of time before this call"  # calls not started yet are skipped
            began = time.monotonic()
            try:
                return _output(self.env.tool(tool, args))
            except Exception as exc:  # a tool must never crash the run
                return None, f"{tool} failed: {type(exc).__name__}: {str(exc)[:200]}"
            finally:
                took.append(time.monotonic() - began)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(contextvars.copy_context().run, one, args) for args in calls]
            results = [f.result() for f in futures]
        step["one_by_one_ms"] = int(round(sum(took) * 1000))
        return results

    def call(self, name, tool, args, read):
        self.spend("tools")
        if tool == "terminal" and self.env.workdir and not args.get("workdir"):
            args = {**args, "workdir": self.env.workdir}  # the same start folder from a chat, a cron job or a test
        secret = "" if read else sends_secret(tool, args)
        if secret:  # refused in shadow too, so a dry run plans what a live one would do
            return None, (f"{tool} refused: it would send {_name(secret)}, whose name looks like a secret (a key, a "
                          "password, a token, .env); a habit never sends such a file")
        if self.env.dry and not read:
            self.planned.append({"node": name, "tool": tool, "args": args})
            return None, None
        refused = self.env.writes(tool, args) if self.env.writes and not read else ""
        if refused:  # a measurement's writes stay in its folder: this one would have left it
            return None, f"{tool} refused: {refused}"
        self.called.add(tool)
        command = args.get("command") if tool == "terminal" and isinstance(args.get("command"), str) else ""
        pipe = shell.pipeline(command) if command else None  # a step that acts too: "No such file" then tail is no success
        # a line the reader cannot read counts, for all it knows, when it names hc at all
        self.untrusted |= "hc" in command and (bool(SCREEN.search(command))
                                               or any(shell.uses(command, *screen) for screen in SCREENS))
        source = (self.nodes.get(name) or {}).get("args", {}).get("command") if tool == "terminal" else None
        self.untrusted |= _server_text(source if isinstance(source, str) else command)
        if tool == "terminal":  # a call never outlives the run's time budget
            if read:  # nor goes on in the background: a read ends with its call (a graph on disk may predate the rule)
                args = {k: v for k, v in args.items() if k in graph.READ_ARGS}
            left = max(1, int(self.limits["seconds"] - (self.env.clock() - self.start)))
            asked = args.get("timeout")
            args = {**args, "timeout": min(asked, left) if isinstance(asked, int) and asked > 0 else left}
            if pipe:
                args["command"] = command + PIPE_LINE
        try:
            text = self.env.tool(tool, args)
        except Exception as exc:  # a tool must never crash the run
            return None, f"{tool} failed: {type(exc).__name__}: {str(exc)[:200]}"
        out, error = _output(text)
        if pipe and isinstance(out, str):
            out, error = _piped(out, error, pipe)
        # grep and friends exit 1 when nothing matches; grep -c prints 0 then, and diff, cmp and test exit 1 for
        # "differs" or "no": a value, when they set the line's status
        if error and read and tool == "terminal" and error.startswith("exit 1:") and (
                not _show(out).strip() or shell.last(command) in EXIT_ONE):
            return ("" if out is None else out), None
        return out, error

    def _llm(self, name, node, step):
        if self.env.llm is None:
            return self.exit_error(name, node, step, "no LLM available")
        fields = node.get("schema") or {"answer": "string"}
        kinds = {"string": {"type": "string"}, "number": {"type": "number"}, "boolean": {"type": "boolean"},
                 "list": {"type": "array", "items": {"type": "string"}}}
        schema = {"type": "object", "properties": {f: kinds[t] for f, t in fields.items()}, "required": list(fields)}
        targets = self.items(node) if "each" in node else [None]
        results = {}
        for item in targets:
            self.spend("llm")
            text = self.state(node.get("with"))
            if item is not None:
                text += f"\n\nitem:\n{_show(item)}"
            try:
                value = self.env.llm(self.text(node["llm"], item), text, schema)
                if not isinstance(value, dict) or not all(_typed(value.get(f), t) for f, t in fields.items()):
                    raise ValueError("fields missing or of the wrong type")
            except Exception as exc:
                if item is None:
                    return self.exit_error(name, node, step, f"invalid llm answer: {type(exc).__name__}")
                self.doubt(item, name, "invalid llm answer", unsafe=node["each"].split(".")[0] in self.unsafe)
                continue
            value = value["answer"] if "schema" not in node else value
            if item is None:
                results = value
            else:
                results[_key(item)] = value
        if "each" in node and targets and not results:
            return self.exit_error(name, node, step, "every llm answer was invalid")
        self.store(node, results, table="each" in node, unsafe=True)
        step["out"] = _short(results)
        step["summary"] = "llm answered (unsafe value)"
        return node["next"]

    def _use(self, name, node, step):
        if self.env.habits is None or self.env.depth + 1 >= MAX_DEPTH:
            return self.exit_error(name, node, step, f"use goes {MAX_DEPTH} levels deep at most")
        try:
            sub = self.env.habits(node["use"])
        except Exception:
            return self.exit_error(name, node, step, f'no habit named "{node["use"]}"')
        inputs = self.values(node)
        missing = [v for v in sub.get("inputs") or [] if v not in inputs]  # a null value is still given
        if missing:
            return self.exit_error(name, node, step, f'{node["use"]} needs {", ".join(missing)} in with')
        left = {what: self.limits[what] - self.counts[what] for what in self.counts}
        seconds = self.limits["seconds"] - (self.env.clock() - self.start)
        sub_env = replace(self.env, depth=self.env.depth + 1, scope=f'{self.env.scope}{name}:{node["use"]}/', caps=left,
                          max_seconds=max(0.001, min(seconds, self.env.max_seconds or seconds)))
        result = run(sub, sub_env, inputs, self.vars.get("trigger", ""), unsafe=self.unsafe & set(inputs))
        for what, amount in result.counts.items():
            self.counts[what] += amount
        self.planned += result.planned
        self.backends += result.backends
        self.called |= set(result.tools)
        self.untrusted |= result.untrusted
        self.outage |= result.outage
        # A sub-habit's doubts stay doubts of the caller, whatever exit it takes: they reach its report.
        self.doubts += result.doubts
        self.doubted |= {_key(d["item"]) for d in result.doubts}
        if result.doubts and result.tainted:  # its doubts may hold an llm's text: never in a command
            self.unsafe.add("doubts")
        for what in ("steps", "tools", "questions", "llm"):
            if self.counts[what] > self.limits[what]:
                raise Stop(f"budget: more than {self.limits[what]} {what} (with {node['use']})")
        if result.status != "end":
            return self.exit_error(name, node, step, f'{node["use"]} woke: {result.reason or result.message}')
        self.kept |= result.kept
        self.store(node, result.values or result.message, unsafe=bool(result.unsafe) or result.tainted)
        step["summary"] = f'{node["use"]}: {result.message}'
        return node["next"]


def _reads(node) -> bool:
    """A step that only reads: marked read: true, or a read tool, or a read-only command."""
    if node.get("read") is False:  # said so, even of a tool that reads (a draft's web call is only planned in a test)
        return False
    if node.get("read") is True or node.get("do") in graph.READ_TOOLS:
        return True
    command = (node.get("args") or {}).get("command") if node.get("do") == "terminal" else None
    # curl, ssh and hc reach another machine (hc read pi/t3 goes over ssh): such a read runs in shadow only when the
    # graph says read: true (after a ;, in a $( ) or through ssh too)
    return (isinstance(command, str) and graph._reads_only(command)
            and not any(shell.uses(command, name) for name in OUTSIDE))


def _server_text(command: str) -> bool:
    """The line may print what a server sent: a curl whose body or headers do not all go to /dev/null, or whose -w
    prints more than numbers and the URL it was given, unless it asks this machine; wget; or one of them the reader
    cannot see (behind a wrapper, in a line it cannot read). The graph's own command: {''x} is text there."""
    words = FETCHERS.findall(command)
    if not words:
        return False
    found = shell.invocations(command, "curl")
    if found is None or len(found) < len(words):
        return True
    return any(_curl_text(args) for args in found)


def _curl_text(args: list) -> bool:
    urls = [a for a in args if a.startswith(("http://", "https://", shell.MARK))]
    try:
        if urls and all(urllib.parse.urlsplit(u).hostname in LOCAL_HOSTS for u in urls):
            return False
    except ValueError:
        return True
    pairs = list(zip(args, args[1:] + [""]))
    discarded = sum(1 for a, b in pairs if (a in ("-o", "--output") and b == "/dev/null")
                    or a == "--output=/dev/null")
    formats = [b for a, b in pairs if a in ("-w", "--write-out")]
    formats += [a.partition("=")[2] for a in args if a.startswith("--write-out=")]
    follows = any(a in ("-L", "--location") or (a[:1] == "-" and a[1:2] != "-" and "L" in a) for a in args)
    for text in formats:
        if "%header" in text or "%output" in text:
            return True
        for variable in re.findall(r"%\{(\w+)\}", text):
            if not NUMBERS.fullmatch(variable) or (variable == "url_effective" and follows):
                return True
    return discarded < len(urls)


def _piped(out: str, error, pipe: list) -> tuple:
    """A read pipe's value and error from its status line: a command before the last that failed fails the step,
    as bash's pipefail would. Without the line (a call cut short, a terminal that is not bash), the tool's answer
    stands."""
    rest, _, line = out.rpartition("\n")
    words = line.split()
    if len(words) < 2 or words[0] != PIPE or not all(w.isdigit() for w in words[1:]):
        return out, error
    out, status, codes = rest.strip(), int(words[1]), [int(w) for w in words[2:]]
    for (name, args), code in zip(pipe[:-1], codes[:-1] if len(codes) == len(pipe) else ()):
        if not _passes(name, args, code):
            return out, f"{name} failed in the pipe, exit {code}: {_short(out, 200)}"
    return out, f"exit {status}: {_short(out, 200)}" if status else None


def _passes(name, args, code) -> bool:
    """A status of a command inside a pipe that is no failure: grep's no match, a writer its reader stopped (head),
    and any status of curl -w, which prints each transfer's result, a failed one included (000)."""
    if code in (0, SIGPIPE) or (code == 1 and name in EXIT_ONE):
        return True
    return name == "curl" and any(a in ("-w", "--write-out") or a.startswith("--write-out=") for a in args)


def secret_name(path) -> bool:
    """The file name at the end of path looks like a secret's (SECRET_FILE, SECRET_WORD), a backup suffix aside."""
    whole = str(path).strip().strip(QUOTES).rstrip("/")
    name = BACKUP.sub("", whole.rsplit("/", 1)[-1]) or whole.rsplit("/", 1)[-1]
    if not name or (KEYNOTE.fullmatch(name) and _keynote(whole)):
        return False
    return bool(SECRET_FILE.fullmatch(name) or SECRET_WORD.search(name))


def _keynote(path: str) -> bool:
    """A .key file that is a Keynote deck: a package (a folder), or its one-file form, a zip. Anything else, a file
    that cannot be read included, is a key."""
    full = os.path.expanduser(path)
    try:
        if os.path.isdir(full):
            return True
        with open(full, "rb") as handle:
            return handle.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def real_path(path) -> str:
    """An absolute or ~/ path with its links resolved, as a host would send it; any other text as it is."""
    text = str(path).strip().strip(QUOTES)
    if not text.startswith(("/", "~/")):
        return str(path)
    try:
        return str(Path(os.path.expanduser(text)).resolve())
    except (OSError, RuntimeError, ValueError):
        return str(path)


def _name(path) -> str:
    return str(path).strip().strip(QUOTES).rstrip("/").rsplit("/", 1)[-1]


def _paths(value) -> list:
    """The texts a file value holds: one, or those of a list or a table."""
    if isinstance(value, dict):
        value = list(value.values())
    return [v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str)]


def sends_secret(tool: str, args) -> str:
    """The secret-like file a call would send, or "": a MEDIA:<path> in any of its texts (send_message, hermes send),
    and in a terminal command that sends (hermes send, curl, wget, scp, rsync, ssh, gh, mail…), any word of it that
    may name a file: a path, a name with an extension or a secret's name, the part after = or @, a short option's
    glued value (-fPATH). However the file reaches the sender (-f, -T, -d @-, < x, cat x |), the command names it."""
    command = args.get("command") if tool == "terminal" and isinstance(args, dict) else None
    found = []
    for text in _strings(args):
        if text is command:
            continue  # read word by word below
        found += media_paths(text)
    if isinstance(command, str):
        try:  # punctuation apart: a | or a ; glued to a path still leaves the path a word
            lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            words = list(lexer)
        except ValueError:  # an unclosed quote: the words as they come
            words = command.split()
        sends = bool(SENDER.search(command) or SENDS.search(command))
        for word in words:
            found += media_paths(word)
            if not sends or "://" in word:
                continue  # a URL: its path is a page, not a file of this machine
            found += [word.rpartition("@")[2]] if "@" in word else []  # curl's -d @x, -F name=@x: a file, any name
            parts = [word, word.partition("=")[2]] + ([word[2:]] if SHORT.match(word) else [])
            found += [part for part in parts if FILEISH.search(part) or SECRET_FILE.fullmatch(_name(part))]
    return next((path for path in found if path and "://" not in path and secret_name(path)), "")  # a URL is a page


def media_paths(text: str) -> list:
    """What each MEDIA: tag of a text may send, read as widely as the gateway reads it: in any case, quoted with
    backticks or quotes, inside Markdown emphasis; a path may hold spaces, so its first words one, two… up to eight,
    the rest of its line, and a quoted span. Hermes's own reading too, when it is there."""
    found = _gateway_media(text) if "media:" in text.lower() else []
    for line in text.splitlines():
        for part in MEDIA_TAG.split(line)[1:]:
            part = part.strip()
            words = part.split()
            quoted = QUOTED.match(part.lstrip("*_"))
            found += [" ".join(words[:n]) for n in range(1, min(len(words), 8) + 1)] + [part]
            found += [next(g for g in quoted.groups() if g)] if quoted else []
    return found


_GATEWAY = []  # Hermes's MEDIA reader, once found; None when this Hermes has none


def _gateway_media(text: str) -> list:
    if not _GATEWAY:
        try:
            from gateway.platforms.base import BasePlatformAdapter
            _GATEWAY.append(BasePlatformAdapter.extract_media)
        except Exception:  # outside Hermes, or another version: the reading above stands alone
            _GATEWAY.append(None)
    try:
        return [path for path, _ in _GATEWAY[0](text)[0]] if _GATEWAY[0] else []
    except Exception:
        return []


def _strings(value) -> list:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


def _typed(value, kind) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    return isinstance(value, list)


def _capped(steps: list) -> list:
    """Keep a trace under 64 KB: drop outputs, then cut long answer lists, then old steps."""
    def size():
        return len(json.dumps(steps, default=str, ensure_ascii=False))
    if size() <= TRACE_LIMIT:
        return steps
    for s in steps:
        s.pop("out", None)
    for s in steps:
        if size() <= TRACE_LIMIT:
            break
        if len(s.get("answers") or []) > 50:
            s["answers_cut"] = len(s["answers"]) - 50
            s["answers"] = s["answers"][:50]
            if "probabilities" in s:
                s["probabilities"] = s["probabilities"][:50]
    while size() > TRACE_LIMIT and len(steps) > 1:
        steps.pop(0)
    return steps


def _number(value) -> float:
    if isinstance(value, (list, dict)):
        return float(len(value))
    if isinstance(value, bool):
        return float(value)
    try:
        return float(_show(value).strip())
    except ValueError:
        raise Stop(f"{_short(value, 40)!r} is not a number") from None

