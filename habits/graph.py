"""Read a habit graph and check it against the habits/0.1 format.

A graph is refused when it breaks one of the seven rules or one of the four
safety checks. Every issue names the rule, the node and the fix, so the LLM
that wrote the graph can correct it.
"""

from __future__ import annotations

import copy
import difflib
import fnmatch
import json
import re
from dataclasses import dataclass

from . import deciders, shell
from .yaml12 import load as load_yaml

SPEC = "habits/0.1"
VERBS = ("ask", "check", "do", "llm", "use", "end", "wake")
TERMINALS = ("end", "wake")
BUILTINS = {"item", "doubts", "trigger"}
MAX_NODES = 20

LIMIT_DEFAULTS = {"seconds": 120, "steps": 200, "tools": 100, "questions": 300, "llm": 5}
LIMIT_CAPS = {"seconds": 600, "steps": 1000, "tools": 500, "questions": 2000, "llm": 20}
NONE_ALLOWED = {"questions", "llm"}  # limits that may be 0: a habit that asks nothing, or never calls an llm
WATCH_CAPS = {"seconds": 60, "tools": 20}

READ_ARGS = {"command", "workdir", "timeout"}  # all a terminal read passes: no background, notify, pty…
READ_TOOLS = {"web_extract", "web_search", "read_file", "search_files", "kanban_list", "kanban_show", "skill_view"}
# Read tools Hermes runs side by side (its own parallel-safe list): an each over them runs at the same time.
PARALLEL_TOOLS = {"web_extract", "web_search", "read_file", "search_files"}
DELETE_COMMANDS = {"rm", "rmdir", "shred", "unlink", "dd"}
CODE_TOOLS = {"execute_code", "delegate_task"}
# Hermes's main tools, for a model that guessed a name. execute_code and delegate_task are main tools too, but a
# habit never runs free code: they are never suggested.
MAIN_TOOLS = ("terminal", "read_file", "write_file", "patch", "search_files", "web_search", "web_extract",
              "browser_navigate", "browser_snapshot", "browser_click", "browser_type", "send_message", "vision_analyze",
              "image_generate", "cronjob_manage", "memory", "skill_view", "clarify")
# Tool actions that remove data: as forbidden as rm.
REMOVING = {"skill_manage": {"delete", "remove_file"}, "cronjob_manage": {"remove"}, "memory": {"remove", "delete"}}
CHECK_OPS = {"empty", "equals", "contains", "greater", "changed"}
SCHEMA_TYPES = {"string", "number", "boolean", "list"}

NODE_NAME = re.compile(r"^[a-z0-9_-]{1,40}$")
VAR_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VAR_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*$")
EVERY = re.compile(r"^[1-9]\d*[mhd]$")  # Hermes cron counts in minutes
AT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")  # a time of day for a watch in days: its cron job gets "M H * * *"
TEMPLATE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?:\.[A-Za-z0-9_]+)*\}")
# Quoted spans as a shell reads them, left to right: an escape, a double-quoted span, a single-quoted span.
QUOTED = re.compile(r"""\\.|"(?:\\.|[^"\\])*"|'[^']*'""")
BRACES = re.compile(r"\{([^{}]*(?:,|\.\.)[^{}]*)\}")  # {a,b} or {a..c}, innermost first; a {template} has neither
GLOB = re.compile(r"[*?\[]")

_MODIFIERS = {"as", "each", "where", "with"}
ALLOWED = {
    "ask": {"ask", "yes_no", "choice", "score", "pick", "margin", "unsure", "min_confidence", "then", "next",
            "doubts"} | _MODIFIERS,
    "check": {"check", "then"},
    "do": {"do", "args", "read", "next", "error", "as", "each", "where"},
    "llm": {"llm", "schema", "next", "error"} | _MODIFIERS,
    "use": {"use", "next", "error", "as", "with"},
    "end": {"end", "with"},
    "wake": {"wake", "with"},
}
RULES = {
    1: "no isolated node",
    2: "no dead end",
    3: "no loop",
    4: "a fallback",
    5: "one thing per node",
    6: "no orphan answer",
    7: "small",
}


@dataclass(frozen=True)
class Issue:
    """One reason a graph is refused."""

    rule: str  # "1" to "7", "format" or "safety"
    node: str  # the node in cause, or "" for the whole graph
    message: str

    def __str__(self) -> str:
        label = f"rule {self.rule} ({RULES[int(self.rule)]})" if self.rule.isdigit() else self.rule
        return f"{label}: {self.message}"


def load(source) -> dict:
    """Return a habit mapping from a dict, YAML text or JSON text."""
    if isinstance(source, dict):
        return source
    if not isinstance(source, str):
        raise ValueError("a habit is a mapping, or YAML or JSON text")
    try:
        data = load_yaml(source)
    except Exception as exc:  # any YAML syntax error: its problem and where, never the line itself (a file's text)
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else ""
        problem = getattr(exc, "problem", None) or getattr(exc, "context", None) or type(exc).__name__
        raise ValueError(f"not valid YAML or JSON: {problem}{where}") from None
    if not isinstance(data, dict):
        raise ValueError("a habit is a mapping with spec, start, fallback and nodes")
    return data


# The names an LLM gives the shell: plainly a shell, so plainly terminal. Any other name stays as written.
SHELLS = {"bash", "sh", "shell", "zsh", "execute", "exec", "run", "run_command", "run_terminal_cmd", "run_shell",
          "cmd", "command", "subprocess", "run_shell_command", "shell_command", "exec_command", "terminal_exec"}
SPREAD = ("command", "path", "url", "query")  # arguments an LLM puts on the node itself
INNER = {"read", "as", "next", "error", "each", "where"}  # node keys an LLM puts inside do
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")
SHELL_CHARS = re.compile(r"""[\s|&;<>()$`'"*?~/=]""")
COMMAND_ONLY = {"workdir", "timeout", "background"}  # terminal arguments that may sit next to a command
ARGS_KEYS = ("args", "arguments", "input", "parameters")  # where the usual tool-call shapes put the arguments
COMMAND_KEYS = ("cmd", "script")  # what an LLM calls the command
WHOLE = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\}$")  # {notes} where a name is expected
# The arguments of the main tools, said in a refusal (Hermes's own schemas; the plugin checks them when it can).
ARG_HINT = {"terminal": "command: …", "read_file": "path: …", "write_file": "path: …, content: …",
            "patch": "path: …, old_string: …, new_string: …", "search_files": "pattern: …, path: …",
            "web_search": "query: …", "web_extract": "urls: [https://…]", "send_message": "target: …, message: …",
            "browser_navigate": "url: …", "skill_view": "name: …", "image_generate": "prompt: …",
            "vision_analyze": "image_url: …, question: …", "memory": "target: …, operations: […]"}


def normalize(habit) -> tuple:
    """The graph with the usual LLM slips fixed, and one short line per fix so the model learns the form: do as an
    object, a command in do, a shell's name for terminal, arguments on the node, then next to next, a variable in
    braces where its name goes. Anything it cannot fix for sure stays as written, for validate to refuse."""
    if not isinstance(habit, dict) or not isinstance(habit.get("nodes"), dict):
        return habit, []
    habit, notes = copy.deepcopy(habit), []
    for name, node in habit["nodes"].items():
        if isinstance(node, dict) and "tool" in node and not any(verb in node for verb in VERBS):
            node["do"] = node.pop("tool")
            notes.append(f'"{name}": tool is written do')
        kind = kind_of(node)
        if kind == "do":
            notes += [f'"{name}": {note}' for note in _normalize_do(node)]
        elif (kind in ("ask", "check") and isinstance(node.get("then"), dict) and "next" in node and "each" not in node
              and "pick" not in node):  # a pick's next is its item's way on: the refusal says where unsure goes
            answers = options(node) if kind == "ask" else ["yes", "no"]
            missing = [a for a in answers if a not in {str(k) for k in node["then"]}]
            if missing and isinstance(node["next"], str):  # next is every other answer
                node["then"].update({answer: node["next"] for answer in missing})
                notes.append(f'"{name}": then and next together; next used for {", ".join(missing)}')
            else:
                notes.append(f'"{name}": then and next together; kept then, dropped next')
            del node["next"]
        if kind == "ask" and _unsure_in_then(node):
            notes.append(f'"{name}": unsure goes in then; now then: {{…, unsure: {node["then"]["unsure"]}}}')
        if kind:
            notes += [f'"{name}": {note}' for note in _unbrace(kind, node)]
    return habit, notes


def _unsure_in_then(node: dict) -> bool:
    """unsure: <node> beside a question that is no pick: its then's unsure (with next, every answer goes on to
    next). Not with each, whose unsure items are doubts, nor when then already holds another unsure."""
    target, then = node.get("unsure"), node.get("then")
    if not isinstance(target, str) or "pick" in node or "each" in node:
        return False
    if then is None and isinstance(node.get("next"), str) and options(node):
        then = {answer: node["next"] for answer in options(node)}
    if not isinstance(then, dict) or then.get("unsure", target) != target:
        return False
    node.pop("next", None)  # beside a then, the fix above has already dropped it
    node["then"] = {**then, "unsure": node.pop("unsure")}
    return True


def _unbrace(kind, node) -> list[str]:
    """{notes} written where a variable's name goes (each, with, where, a check's variable): the name itself."""
    found = []

    def bare(value):
        match = WHOLE.match(value.strip()) if isinstance(value, str) else None
        if match:
            found.append(match.group(1))
        return match.group(1) if match else value
    for key in ("each", "pick"):
        if isinstance(node.get(key), str):
            node[key] = bare(node[key])
    if isinstance(node.get("with"), list):
        node["with"] = [bare(v) for v in node["with"]]
    if isinstance(node.get("where"), dict) and all(isinstance(k, str) for k in node["where"]):
        node["where"] = {bare(k): v for k, v in node["where"].items()}
    condition = node.get("check") if kind == "check" else None
    if isinstance(condition, dict) and len(condition) == 1:
        op, value = next(iter(condition.items()))
        if isinstance(value, list) and value:
            condition[op] = [bare(value[0])] + value[1:]
        else:
            condition[op] = bare(value)
    return [f"{{{v}}} is the variable {v}: write its name without braces where a name goes" for v in found]


def _normalize_do(node: dict) -> list[str]:
    notes = []
    if isinstance(node.get("arguments"), dict) and "args" not in node:
        node["args"] = node.pop("arguments")
        notes.append("arguments renamed args")
    if isinstance(node["do"], dict):
        found = _do_object(node["do"])
        if isinstance(found, tuple):
            tool, args, inner = found
            outside = node.get("args") if isinstance(node.get("args"), dict) else {}
            if (args is None or "args" not in node or not set(args) & set(outside)) and all(
                    node.get(key, value) == value for key, value in inner.items()):
                node.update(inner, do=tool)
                if args is not None:
                    node["args"] = {**outside, **args}
                notes.append(f"do takes a tool name; now do: {tool}"
                             + (f", args: {{{', '.join(node['args'])}}}" if args else ""))
    do, args = node["do"], node.get("args")
    prefixed = re.match(r"terminal\s*:\s*(\S.*)$|terminal\s+(\S.*)$", do.strip(), re.S) if isinstance(do, str) else None
    if prefixed and (args is None or isinstance(args, dict) and set(args) <= COMMAND_ONLY):
        command = (prefixed.group(1) or prefixed.group(2)).strip()
        node.update(do="terminal", args={"command": command, **(args or {})})
        notes.append(f"do takes the tool name alone; now do: terminal, args: {{command: {_brief(command)}}}")
    elif (isinstance(do, str) and do.strip() and not TOOL_NAME.match(do.strip()) and SHELL_CHARS.search(do)
            and not _tool_first(do) and (args is None or isinstance(args, dict) and set(args) <= COMMAND_ONLY)):
        node.update(do="terminal", args={"command": do.strip(), **(args or {})})
        notes.append(f"do held a shell command; now do: terminal, args: {{command: {_brief(do.strip())}}}")
    args = node.get("args")
    if (isinstance(node["do"], str) and node["do"].strip().lower() in SHELLS
            and not (isinstance(args, dict) and "code" in args)):  # code to run is free code, not a command
        notes.append(f'{node["do"]} is not a Hermes tool; now do: terminal')
        node["do"] = "terminal"
    spread = [key for key in SPREAD + (tuple(sorted(COMMAND_ONLY)) if node["do"] == "terminal" else ()) if key in node]
    if spread and (isinstance(args, dict) or "args" not in node) and not set(spread) & set(args or {}):
        node["args"] = {**(args or {}), **{key: node.pop(key) for key in spread}}
        notes.append(f'{", ".join(spread)} moved into args')
    args = node.get("args")
    if node["do"] == "terminal" and isinstance(args, dict) and "command" not in args:
        given = [key for key in COMMAND_KEYS if key in args]
        if len(given) == 1 and isinstance(args[given[0]], str):
            args["command"] = args.pop(given[0])
            notes.append(f"{given[0]} renamed command")
    if node["do"] == "terminal" and isinstance(args, dict) and isinstance(args.get("command"), list):
        line = args["command"]
        if len(line) == 3 and line[0] in ("sh", "bash") and line[1] in ("-c", "-lc") and isinstance(line[2], str):
            args["command"] = line[2]  # terminal already runs the line in a shell
            notes.append(f"command is one line; now command: {_brief(line[2])}")
    if node["do"] == "web_extract" and isinstance(args, dict) and isinstance(args.get("url"), str) and "urls" not in args:
        args["urls"] = [args.pop("url")]
        notes.append("web_extract takes urls, a list; now urls: [url]")
    return notes


def _tool_first(do: str) -> str | None:
    """The Hermes tool a do string starts with (read_file ~/x, web search …): a tool and its argument, not a command."""
    words = [w.rstrip(":") for w in do.split()[:2]]
    for name in (words[0] if words else "", "_".join(words).lower()):
        if name in MAIN_TOOLS or name in CODE_TOOLS:
            return name
    return None


def _do_object(do: dict):
    """(tool, args, node keys) from do written as an object, or why it cannot be read: {tool|name: X, args|
    arguments|input|parameters: {…}, …} (the tool_use and function-call shapes too), {command: …} (terminal),
    {X: {…}}, or {bash|terminal: "<command>"}."""
    rest = dict(do)
    if rest.get("type") in ("tool_use", "function"):
        rest.pop("type")
        rest.pop("id", None)  # the call's own id, no argument
    tool = rest.pop("tool", None) or rest.pop("name", None)
    if tool is None and "command" in rest:
        tool = "terminal"
    elif tool is None and len(rest) == 1:
        key, value = next(iter(rest.items()))
        if isinstance(value, str) and (str(key).lower() in SHELLS or key == "terminal"):
            tool, rest = "terminal", {"args": {"command": value}}
        elif isinstance(value, dict) and key not in ARGS_KEYS and TOOL_NAME.match(str(key)):
            tool, rest = key, {"args": value}
    if not isinstance(tool, str) or not tool.strip():
        return "no tool or name in it"
    if isinstance(rest.get("arguments"), str):  # the OpenAI shape: arguments as JSON text
        try:
            rest["arguments"] = json.loads(rest["arguments"])
        except ValueError:
            return "its arguments are not JSON"
    given = [rest.pop(key) for key in ARGS_KEYS if key in rest]
    if len(given) > 1 or (given and not isinstance(given[0], dict)):
        return "its arguments are given twice, or not as a mapping"
    inner = {key: rest.pop(key) for key in list(rest) if key in INNER}
    if given and set(rest) & set(given[0]):  # an argument both inside args and beside it
        return f"{', '.join(sorted(set(rest) & set(given[0])))} both inside its args and beside them"
    args = {**given[0], **rest} if given else rest or None
    return tool.strip(), args, inner


def do_problem(node: dict) -> str:
    """Why a do written as an object stays refused, for the message."""
    found = _do_object(node["do"])
    if not isinstance(found, tuple):
        return found
    tool, args, inner = found
    clash = sorted(set(args or {}) & set(node.get("args") or {})) if isinstance(node.get("args"), dict) else []
    if clash:
        return f"{', '.join(clash)} both inside do and in args"
    if args is not None and "args" in node and not isinstance(node["args"], dict):
        return "args both inside do and beside it"
    differ = [key for key, value in inner.items() if node.get(key, value) != value]
    return f"{', '.join(differ)} both inside do and beside it, with different values" if differ else ""


def _brief(text: str, size: int = 60) -> str:
    return json.dumps(text if len(text) <= size else text[:size] + "…", ensure_ascii=False)


# What a model means by a name no spelling brings close (fetch is not patch).
SYNONYMS = {"fetch": "web_extract", "http_get": "web_extract", "get_url": "web_extract", "notify": "send_message",
            "telegram": "send_message", "send": "send_message", "cron": "cronjob_manage", "schedule": "cronjob_manage",
            "remember": "memory", "ask_user": "clarify"}


def unknown_tools(habit, known, near=None) -> list[Issue]:
    """Each do whose tool is not among known, with the closest names of near (known by default), so a model that
    guessed a name learns the real one. known is the session's tools when near is None, else every tool Hermes
    registered. Nothing is checked without known: an MCP tool is not a guess."""
    if not known or not isinstance(habit, dict) or not isinstance(habit.get("nodes"), dict):
        return []
    pool = sorted(set(known if near is None else near) - CODE_TOOLS)
    found = []
    for name, node in habit["nodes"].items():
        tool = node.get("do") if isinstance(node, dict) else None
        if not isinstance(tool, str) or tool in known:
            continue
        # Its meaning, else a close spelling, else the tools that share a word with it (filesystem: read_file…).
        others = [t for t in pool if t != tool]  # a main tool this Hermes lacks is no suggestion for itself
        meant = [SYNONYMS[tool.lower()]] if SYNONYMS.get(tool.lower()) in others else []
        close = meant or difflib.get_close_matches(tool, others, n=3, cutoff=0.6) or [
            t for t in others if any(len(part) > 2 and part in tool.lower() for part in t.split("_"))][:3]
        found.append(Issue("format", name, f'"{name}": {tool} is not a Hermes tool'
                                           + (" of this session" if near is None else "")
                                           + (f"; closest: {', '.join(close)}" if close else "")
                                           + ". A shell command goes in do: terminal, args: {command: …}"))
    return found


def missing_args(habit, schemas) -> list[Issue]:
    """Each do without an argument its tool requires, and the arguments it takes. schemas maps a tool to (required,
    properties), from Hermes's own schemas; a tool without one is not checked. terminal has its own check."""
    if not isinstance(habit, dict) or not isinstance(habit.get("nodes"), dict):
        return []
    found = []
    for name, node in habit["nodes"].items():
        tool = node.get("do") if isinstance(node, dict) else None
        args = node.get("args", {}) if isinstance(node, dict) else None
        if not isinstance(tool, str) or tool == "terminal" or tool not in schemas or not isinstance(args, dict):
            continue
        required, properties = schemas[tool]
        missing = [key for key in required if key not in args]
        if missing:
            found.append(Issue("format", name, f'"{name}": {tool} needs {", ".join(missing)}; it takes '
                                               f'{", ".join(properties)}.'))
    return found


def drafting(habit) -> list[Issue]:
    """Checks for a graph the model writes now, not for a file already saved (catalog.read does not run them): a
    saved habit stays valid while these learn. An ssh fed by the terminal waits for a person."""
    if not isinstance(habit, dict) or not isinstance(habit.get("nodes"), dict):
        return []
    found = []
    for name, node in habit["nodes"].items():
        if not isinstance(node, dict):
            continue  # validate says so
        args = node.get("args") if isinstance(node.get("args"), dict) else {}
        command = args.get("command") if node.get("do") == "terminal" else None
        if isinstance(command, str) and shell.interactive(command):
            found.append(Issue("format", name, f'"{name}" opens an ssh session that waits for a person: give ssh '
                                               "the command to run, as in ssh -o BatchMode=yes host 'uptime'."))
    return found


def kind_of(node) -> str | None:
    """The node's verb, or None when it has zero or several."""
    if not isinstance(node, dict):
        return None
    verbs = [verb for verb in VERBS if verb in node]
    return verbs[0] if len(verbs) == 1 else None


def exits(node: dict) -> list[str]:
    """Targets a node can lead to, fallback excluded."""
    targets = [node[key] for key in ("next", "error", "unsure") if isinstance(node.get(key), str)]
    then = node.get("then")
    if isinstance(then, dict):
        targets += [target for target in then.values() if isinstance(target, str)]
    return targets


def options(node: dict) -> list[str]:
    """The closed answers of an ask node."""
    if node.get("yes_no") is True:
        return ["yes", "no"]
    for key in ("choice", "score"):
        value = node.get(key)
        if isinstance(value, dict):
            return [str(option) for option in value]
        if isinstance(value, list):
            return [str(option) for option in value]
    return []


def limits(habit: dict) -> dict:
    """Effective budgets: defaults, watch defaults, then the habit's own values."""
    effective = dict(LIMIT_DEFAULTS)
    if is_watch(habit):
        effective.update(WATCH_CAPS)
    own = habit.get("limits")
    if isinstance(own, dict):
        effective.update({key: value for key, value in own.items() if key in LIMIT_DEFAULTS})
    return effective


def is_watch(habit: dict) -> bool:
    trigger = habit.get("trigger")
    return isinstance(trigger, dict) and "every" in trigger


def validate(habit: dict) -> list[Issue]:
    """Every reason the graph is refused; an empty list means it may run."""
    issues: list[Issue] = []

    def add(rule, node, message):
        issues.append(Issue(str(rule), node, message if message.endswith(".") else message + "."))

    if habit.get("spec") != SPEC:
        add("format", "", f'spec must be "{SPEC}"')
    nodes = habit.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        add("format", "", "nodes must be a non-empty mapping of named nodes")
        return issues

    _check_header(habit, add)
    kinds = {}
    for name, node in nodes.items():
        if not isinstance(name, str) or not NODE_NAME.match(name):
            add("format", str(name), f'node name "{name}" must use a-z, 0-9, _ or -, 40 characters at most')
            continue
        if not isinstance(node, dict):
            add("format", name, f'"{name}" must be a mapping')
            continue
        verbs = [verb for verb in VERBS if verb in node]
        if len(verbs) != 1:
            found = " and ".join(verbs) or "nothing"
            add(5, name, f'"{name}" must do exactly one thing, found {found}.')
            continue
        kind = verbs[0]
        kinds[name] = kind
        extra = set(node) - ALLOWED[kind]
        if kind == "llm" and extra & {"args", "tools", "read"}:
            add(5, name, f'"{name}" is an llm node: it thinks but uses no tool. Put the tool in a do node.')
            extra -= {"args", "tools", "read"}
        if kind in ("do", "llm", "use") and "then" in extra:  # a do read as a question: yes is next, no is error
            add("format", name, f'"{name}" ({kind}) has no then: it goes to next when it works, to error when it fails')
            extra.discard("then")
        if extra:
            add("format", name, f'"{name}" ({kind}) does not take {", ".join(sorted(extra))}')
        _check_node(name, kind, node, add)
    own = habit.get("limits") if isinstance(habit.get("limits"), dict) else {}
    for key, verb, does in (("llm", "llm", "is an llm node"), ("questions", "ask", "asks one")):
        if type(own.get(key)) is int and own[key] == 0:  # a run would always end on its fallback there
            for name in [n for n, k in kinds.items() if k == verb]:
                add("format", name, f'limit {key} is 0, but "{name}" {does}: raise the limit, or leave the node out')

    _check_graph(habit, nodes, kinds, add)
    if len(nodes) > MAX_NODES:
        add(7, "", f"{len(nodes)} nodes, {MAX_NODES} at most: split the graph with use")
    _check_variables(habit, nodes, kinds, add)
    _check_safety(habit, nodes, kinds, add)
    order = {"format": 0, "safety": 9}
    return sorted(issues, key=lambda issue: order.get(issue.rule, int(issue.rule) if issue.rule.isdigit() else 8))


def _check_header(habit, add):
    trigger = habit.get("trigger")
    if trigger is not None and trigger != "message":
        if isinstance(trigger, dict) and "every" in trigger and set(trigger) <= {"every", "at"}:
            every = trigger["every"]
            if not isinstance(every, str) or not EVERY.match(every):
                add("format", "", 'every takes a whole number of minutes, hours or days: 5m, 2h or 1d')
            elif "at" in trigger and every != "1d":
                # Hermes cron has no "every N days at": */N days of the month starts again on the 1st (7d: the 29th,
                # then the 1st).
                add("format", "", f'at goes with every: 1d ({{every: 1d, at: "21:00"}}), not every: {every}: a time '
                                  "of day every N days has no cron form that keeps the gap")
            elif "at" in trigger and not (isinstance(trigger["at"], str) and AT.match(trigger["at"])):
                # Unquoted, 21:00 is the number 1260 to a YAML 1.1 reader.
                add("format", "", 'at is a time of day, 24-hour and quoted: at: "21:00" (and "09:00", not 9:00)')
        elif isinstance(trigger, dict) and list(trigger) == ["event"]:
            # The event kind is gone, and no hook ever started one: say what replaces it.
            add("format", "", "trigger {event: …} is no longer supported: check on a schedule with {every: 30m}, "
                              "or leave trigger out to run the habit by name")
        else:
            add("format", "", 'trigger is "message", {every: 30m} or {every: 1d, at: "21:00"}')
    for key in ("start", "fallback"):
        if key in habit and not isinstance(habit[key], str):
            add("format", "", f"{key} must be the name of a node")
    inputs = habit.get("inputs", [])
    if isinstance(inputs, dict):
        given = ", ".join(f"{k}: {v}" for k, v in list(inputs.items())[:3])
        add("format", "", f"inputs lists names: inputs: [{', '.join(map(str, inputs))}]; give the values in "
                          f"habit_run's inputs argument: {{{given}}}")
    elif not isinstance(inputs, list) or not all(isinstance(i, str) and VAR_NAME.match(i) for i in inputs):
        add("format", "", "inputs must be a list of variable names")
    elif set(inputs) & BUILTINS:
        add("format", "", "inputs cannot use item, doubts or trigger: the engine sets them")
    confidence = habit.get("min_confidence", 0.8)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        add("format", "", "min_confidence must be a number between 0 and 1")
    own = habit.get("limits", {})
    if not isinstance(own, dict):
        add("format", "", "limits must be a mapping")
        return
    for key, value in list(own.items()):
        if isinstance(value, float) and value.is_integer():  # JSON cannot tell 60 from 60.0
            own[key] = value = int(value)
        if key not in LIMIT_DEFAULTS:
            add("format", "", f'unknown limit "{key}"; known: {", ".join(LIMIT_DEFAULTS)}')
        elif not isinstance(value, int) or isinstance(value, bool) or value < (key not in NONE_ALLOWED):
            add("format", "", f"limit {key} must be " + ("a whole number, 0 or more" if key in NONE_ALLOWED
                                                         else "a positive whole number"))
        elif value > LIMIT_CAPS[key]:
            add(7, "", f"limit {key} is {value}, {LIMIT_CAPS[key]} at most")


def _check_node(name, kind, node, add):
    text = node[kind]
    if kind in ("ask", "llm", "end", "wake") and not (isinstance(text, str) and text.strip()):
        add("format", name, f'"{name}" needs its {kind} text')
    if kind == "do" and isinstance(text, dict):
        problem = do_problem(node)
        add("format", name, f'"{name}": do takes a tool name: do: terminal, args: {{command: …}}'
                            + (f" ({problem})" if problem else ""))
    elif kind in ("do", "use") and not (isinstance(text, str) and text.strip()):
        add("format", name, f'"{name}" needs the name of a {"tool" if kind == "do" else "habit"}')
    elif kind == "do" and re.search(r"\s", text):
        tool = _tool_first(text)
        add("format", name, f'"{name}": do takes the tool name alone: do: {tool}, args: {{{ARG_HINT.get(tool, "…")}}}'
            if tool else f'"{name}": do takes a tool name, not a command. Write do: terminal, args: {{command: …}}')
    if "as" in node and not (isinstance(node["as"], str) and VAR_NAME.match(node["as"])):
        add("format", name, f'"{name}": as must be a variable name')
    elif node.get("as") in BUILTINS:
        add("format", name, f'"{name}": as cannot be {node["as"]}: item, doubts and trigger are set by the engine')
    if kind in ("do", "llm", "use") and not isinstance(node.get("next"), str):
        add(2, name, f'"{name}" needs next; error alone is not enough')
    if "each" in node and not (isinstance(node["each"], str) and VAR_PATH.match(node["each"])):
        add("format", name, f'"{name}": each must name a list variable, or a path into one (found.data.web)')
    if "where" in node and not isinstance(node["where"], dict):
        add("format", name, f'"{name}": where must be a mapping like {{kind: installeur}}')
    if "with" in node and not (isinstance(node["with"], list) and all(isinstance(v, str) for v in node["with"])):
        add("format", name, f'"{name}": with must be a list of variable names')
    if "then" in node and "next" in node and kind in ("ask", "check"):
        add("format", name, f'"{name}": use then or next, not both')
    for key in ("next", "error", "unsure"):
        if key in node and not isinstance(node[key], str):
            add(2, name, f'"{name}": {key} must be the name of a node')
    if isinstance(node.get("then"), dict):
        for answer, target in node["then"].items():
            if not isinstance(target, str):
                add(2, name, f'"{name}": the exit of "{answer}" must be the name of a node')

    if kind == "ask":
        _check_ask(name, node, add)
    elif kind == "check":
        _check_check(name, node, add)
    elif kind == "do":
        args = node.get("args")
        if "args" in node and not isinstance(args, dict):
            add("format", name, f'"{name}": args must be a mapping')
        elif text == "terminal" and not (isinstance((args or {}).get("command"), str) and args["command"].strip()):
            add("format", name, f'"{name}": terminal takes args: {{command: "…"}}, one shell line')
        if isinstance(text, str) and text.lower() in SHELLS and isinstance(args, dict) and "code" in args:
            add("format", name, f'"{name}": a habit runs no free code. Save the script in a file and run it with '
                                'do: terminal, args: {command: "python3 ~/scripts/name.py"}')
        if "read" in node and not isinstance(node["read"], bool):
            add("format", name, f'"{name}": read must be true or false')
    elif kind == "llm":
        schema = node.get("schema", {"answer": "string"})
        if not isinstance(schema, dict) or not schema or not all(isinstance(t, str) and t in SCHEMA_TYPES for t in schema.values()):
            add("format", name, f'"{name}": schema maps each field to string, number, boolean or list')


def _check_ask(name, node, add):
    types = [key for key in ("yes_no", "choice", "score", "pick") if key in node]
    if len(types) != 1:
        add(5, name, f'"{name}" needs exactly one answer type: yes_no, choice, score or pick')
        return
    if types[0] == "pick":
        _check_pick(name, node, add)
    else:
        for key, why in (("margin", "margin goes with pick only"),
                         ("unsure", "unsure: <node> goes with pick only; elsewhere write then: {unsure: <node>}")):
            if key in node:
                add("format", name, f'"{name}": {why}')
    answers = options(node)
    if types[0] == "yes_no" and node["yes_no"] is not True:
        add("format", name, f'"{name}": write yes_no: true')
    elif types[0] == "choice" and not 2 <= len(answers) <= deciders.MAX_OPTIONS:
        add("format", name, f'"{name}": a choice has 2 to {deciders.MAX_OPTIONS} options')
    elif types[0] == "score" and not 2 <= len(answers) <= 10:
        add("format", name, f'"{name}": a score has 2 to 10 levels, lowest first')
    confidence = node.get("min_confidence", 0.8)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        add("format", name, f'"{name}": min_confidence must be between 0 and 1')
    if "doubts" in node and (node["doubts"] != "drop" or "each" not in node):
        add("format", name, f'"{name}": doubts: drop goes on an ask with each only, to leave an unsure item out '
                            "instead of handing the run back")
    then = node.get("then")
    if "each" in node:
        if then is not None:
            first = next(iter(then.values()), "…") if isinstance(then, dict) else "…"
            var = node.get("as") if isinstance(node.get("as"), str) else "answer"
            add(6, name, f'"{name}" uses each: it can only have next, one answer per item. Write next: {first}, '
                         f'and filter in that node with where: {{{var}: {answers[0] if answers else "yes"}}}')
        return
    if then is None:
        return
    if not isinstance(then, dict):
        add("format", name, f'"{name}": then maps each answer to a node')
        return
    keys = {str(k) for k in then}
    for answer in answers:
        if answer not in keys:
            add(6, name, f'option "{answer}" of "{name}" leads nowhere. Add it to "then".')
    for key in then:
        if str(key) not in answers and key != "unsure":
            add("format", name, f'"{name}": "{key}" is not one of its answers')


def _check_pick(name, node, add):
    """A pick: one choice question among the items of a list, next with the item, unsure (or the fallback) without."""
    if not (isinstance(node["pick"], str) and VAR_PATH.match(node["pick"])):
        add("format", name, f'"{name}": pick names the list (or table) variable to choose one item from: pick: notes')
    if "each" in node:
        add("format", name, f'"{name}" picks one item of its list in one question: no each (each asks one per item)')
    if "then" in node:
        add("format", name, f'"{name}" picks one item: next goes on with it, unsure: <node> when it takes none '
                            "(no then)")
    if not isinstance(node.get("next"), str):
        add(2, name, f'"{name}" needs next: the node that goes on with the item picked')
    margin = node.get("margin", 0.15)
    if not isinstance(margin, (int, float)) or isinstance(margin, bool) or not 0 <= margin <= 1:
        add("format", name, f'"{name}": margin must be between 0 and 1 (how far the top item stands over the second)')


def _check_check(name, node, add):
    condition = node["check"]
    if not isinstance(condition, dict) or len(condition) != 1 or next(iter(condition)) not in CHECK_OPS:
        add("format", name, f'"{name}": check takes one of {", ".join(sorted(CHECK_OPS))}')
    else:
        op, value = next(iter(condition.items()))
        if op in ("empty", "changed") and not isinstance(value, str):
            add("format", name, f'"{name}": {op} takes a variable name')
        if op in ("equals", "contains", "greater") and not (isinstance(value, list) and len(value) == 2):
            add("format", name, f'"{name}": {op} takes [variable, value]')
    then = node.get("then")
    if not isinstance(then, dict):
        add(6, name, f'"{name}" needs then: {{yes: …, no: …}}')
        return
    for answer in ("yes", "no"):
        if answer not in then:
            add(6, name, f'answer "{answer}" of "{name}" leads nowhere. Add it to "then".')


def _check_graph(habit, nodes, kinds, add):
    start, fallback = habit.get("start"), habit.get("fallback")
    if fallback is None:
        add(4, "", 'add "fallback": the wake node that receives every error and every doubt')
    elif isinstance(fallback, str) and kinds.get(fallback) != "wake":
        add(4, str(fallback), f'fallback "{fallback}" must be a wake node')
    if not isinstance(fallback, str):
        fallback = None  # _check_header already said a fallback is the name of a node

    edges = {name: exits(nodes[name]) for name in kinds}
    for name, targets in edges.items():
        for target in targets:
            if target not in nodes:
                add(2, name, f'"{name}" leads to "{target}", which does not exist')
        if kinds[name] not in TERMINALS and not targets:
            add(2, name, f'"{name}" is a dead end: give it next or then')

    if start is None:
        add("format", "", 'add "start": the name of the first node')
        return
    if not isinstance(start, str):
        return  # _check_header already said start is the name of a node
    if start not in nodes:
        add(2, "", f'start "{start}" is not a node')
        return

    seen, stack = set(), [start]
    while stack:
        current = stack.pop()
        if current in seen or current not in kinds:
            continue
        seen.add(current)
        stack.extend(edges[current])
        if kinds[current] not in TERMINALS and fallback in nodes:
            stack.append(fallback)
    for name in kinds:
        if name not in seen and name != fallback:  # the fallback is required, even where nothing can fail
            add(1, name, f'"{name}" cannot be reached from start "{start}".')

    # Depth-first walk without recursion: 1 = on the current path, 2 = done.
    state: dict[str, int] = {}
    for root in kinds:
        if root in state:
            continue
        state[root] = 1
        path = [(root, iter(edges[root]))]
        while path:
            name, targets = path[-1]
            target = next(targets, None)
            if target is None:
                state[name] = 2
                path.pop()
            elif target in kinds and state.get(target) == 1:
                add(3, name, f'"{name}" goes back to "{target}". Repeat with "each" or with the trigger.')
            elif target in kinds and target not in state:
                state[target] = 1
                path.append((target, iter(edges[target])))


def _references(value) -> set[str]:
    """Variable names used in templates inside a value."""
    if isinstance(value, str):
        return set(TEMPLATE.findall(value))
    if isinstance(value, dict):
        return set().union(*(_references(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_references(v) for v in value)) if value else set()
    return set()


def _used_variables(kind, node) -> set[str]:
    used = set()
    for key in ("each", "pick"):
        if isinstance(node.get(key), str):
            used.add(node[key].split(".")[0])
    if isinstance(node.get("where"), dict):
        used |= {str(key) for key in node["where"]}
    if isinstance(node.get("with"), list):
        used |= {v for v in node["with"] if isinstance(v, str)}
    if kind == "check" and isinstance(node.get("check"), dict) and node["check"]:
        value = next(iter(node["check"].values()))
        first = value[0] if isinstance(value, list) and value else value
        if isinstance(first, str):
            used.add(first.split(".")[0])
    used |= _references(node.get(kind)) | _references(node.get("args"))
    return used


def _defined(habit, nodes, kinds) -> set[str]:
    defined = set(BUILTINS)
    inputs = habit.get("inputs", [])
    if isinstance(inputs, (list, dict)):  # a mapping is refused on its own: its names still count here
        defined |= {i for i in inputs if isinstance(i, str)}
    return defined | {nodes[name]["as"] for name in kinds if isinstance(nodes[name].get("as"), str)}


def _literal(node, defined) -> set[str]:
    """Names of a terminal command's {word} in single quotes that no node defines: awk '{print}', curl -w
    '%{http_code}'. Text the model meant as it is, not a value."""
    command = (node.get("args") or {}).get("command") if node.get("do") == "terminal" and isinstance(
        node.get("args"), dict) else None
    if not isinstance(command, str):
        return set()
    return {t[1:-1].split(".")[0] for t, quote, _ in _quoted_templates(command) if quote == "'"} - defined


def _check_variables(habit, nodes, kinds, add):
    defined = _defined(habit, nodes, kinds)
    for name, kind in kinds.items():
        node = nodes[name]
        used = _used_variables(kind, node)
        literal = _literal(node, defined)
        for variable in sorted(used - defined):
            bare = WHOLE.match(variable)
            if variable in literal:
                add("format", name, f'"{name}": {{{variable}}} in quotes names no variable. For the text {{{variable}}}, '
                                    f"split it with '' so the engine leaves it: {{''{variable}}}, as in curl -w "
                                    "'%{''http_code}'")
            elif bare:
                add("format", name, f'"{name}" uses "{variable}": write the variable name without braces: {bare.group(1)}')
            else:
                add("format", name, f'"{name}" uses "{variable}", which no node defines. Add as: {variable}, or list it in inputs.')
        if "item" in used and "each" not in node:
            add("format", name, f'"{name}" uses item outside each: item is each element of a list, so add each: <list>')


def _braced(word: str, limit: int = 256) -> list[str]:
    """The words a shell makes of one word by brace expansion, in order: {r,}m is rm m, {q..s}m holds rm.
    A ValueError past the limit: {a,b}{a,b}… doubles at each pair, and the last one could hide a name."""
    words, done = [word], []
    while words:
        word = words.pop(0)
        match = BRACES.search(word)
        if not match:
            done.append(word)
            continue
        body, ends = match.group(1), match.group(1).split("..")
        if "," in body:
            parts = body.split(",")
        elif len(ends) in (2, 3) and len(ends[0]) == len(ends[1]) == 1:
            low, high = sorted(map(ord, ends[:2]))
            parts = [chr(c) for c in range(low, min(high, low + limit) + 1)]
        else:
            parts = [body]  # a number range, or a form the shell leaves as it is: no letters to hide a name
        words = [word[:match.start()] + part + word[match.end():] for part in parts] + words
        if len(words) + len(done) > limit:
            raise ValueError("too many words to check")
    return done


def _words(command: str) -> list[str]:
    """Every word of a command as a shell runs it, paths cut to their last part: quotes and backslashes
    removed (\\rm, r''m and "r"m are rm) and braces expanded. Quoted, a brace or a glob is only text."""
    literal = QUOTED.sub(lambda m: re.sub(r"[{},*?\[\]]", "_", m.group(0)), command)
    words = [re.sub(r"[\"'\\]", "", word) for word in re.split(r"[\s;&|()`$]+", literal) if word]
    return [each.rsplit("/", 1)[-1] for word in words for each in _braced(word)]


def _deletes(command: str) -> bool:
    # Any word counts, so /bin/rm, xargs rm and bash -c "rm …" are all caught, and a glob such as /bin/r[m]
    # or /bin/r? that the shell turns into rm (a lone * names no command).
    try:
        words = set(_words(command))
    except ValueError:
        return True  # a brace expansion too long to check
    globbed = any(fnmatch.fnmatchcase(name, word.replace("[^", "[!")) for word in words
                  if GLOB.search(word) and word.strip("*") for name in DELETE_COMMANDS)
    return bool(DELETE_COMMANDS & words) or globbed or {"find", "-delete"} <= words


def _reads_only(command: str) -> bool:
    """Every command of the line only reads, as shell.py reads it: a doubt is a write."""
    return shell.reads_only(command)


def _loose_quoting(command: str) -> bool:
    """A double quote, backslash, backtick or heredoc outside single quotes: values could escape their quoting (the
    shell finds a closing backtick before any quote)."""
    single = False
    for i, c in enumerate(command):
        if c == "'":
            single = not single
        elif not single and (c in '"\\`' or command.startswith("<<", i)):
            return True
    return False


def _quoted_templates(command: str) -> list[tuple]:
    """Templates written inside quotes, as (template, quote, whether its quoted span holds a glob): the engine
    already quotes each value, so they would break."""
    found, quote, i, span = [], None, 0, 0
    while i < len(command):
        c = command[i]
        if quote is None and c in "'\"":
            quote, span = c, i
        elif c == quote:
            quote = None
        elif c == "{" and quote:
            match = TEMPLATE.match(command, i)
            if match:
                close = command.find(quote, match.end())
                body = command[span + 1:close if close >= 0 else len(command)]
                found.append((match.group(0), quote, bool(GLOB.search(TEMPLATE.sub("", body)))))
                i = match.end()
                continue
        i += 1
    return found


def _check_safety(habit, nodes, kinds, add):
    defined = _defined(habit, nodes, kinds)
    llm_values = {nodes[name]["as"] for name, kind in kinds.items() if kind == "llm" and isinstance(nodes[name].get("as"), str)}
    # An item picked from a list an llm wrote is that llm's text too, and so is a table an each built over one (its
    # keys are the llm's items, which a pick then takes), and doubts once such an each may doubt one of its items:
    # however many steps deep.
    sources = {"ask": ("pick", "each"), "do": ("each",)}
    for _ in kinds:
        derived = {nodes[name]["as"] for name, kind in kinds.items() if isinstance(nodes[name].get("as"), str)
                   and any(isinstance(nodes[name].get(key), str) and nodes[name][key].split(".")[0] in llm_values
                           for key in sources.get(kind, ()))}
        if any(kind in ("ask", "do", "llm") and isinstance(nodes[name].get("each"), str)
               and nodes[name]["each"].split(".")[0] in llm_values for name, kind in kinds.items()):
            derived.add("doubts")
        if derived <= llm_values:
            break
        llm_values |= derived
    for name, kind in kinds.items():
        node = nodes[name]
        if kind != "do":
            continue
        tool = node.get("do")
        if not isinstance(tool, str):
            continue  # already refused as a format issue
        args = node.get("args") if isinstance(node.get("args"), dict) else {}
        command = args.get("command") if tool == "terminal" else None
        if tool in CODE_TOOLS:
            add("safety", name, f'"{name}" runs free code with {tool}, which could delete files. Use terminal or a dedicated tool.')
        removing = REMOVING.get(tool, set())
        action = args.get("action")
        if removing and (action in removing or (isinstance(action, str) and TEMPLATE.search(action))):
            add("safety", name, f'"{name}" removes data with {tool} ({action}). Habits never delete.')
        if isinstance(command, str) and TEMPLATE.search(command) and _loose_quoting(command):
            add("safety", name, f'"{name}" mixes values with double quotes, backslashes, backticks or a heredoc in its '
                                "command: the engine quotes each value itself; write {item} bare, $( ) instead of "
                                "backticks, and single quotes for text.")
        if isinstance(command, str):
            literal = _literal(node, defined)  # said once, by the variables check
            for template, quote, globbed in _quoted_templates(command):
                if quote == "'" and template[1:-1].split(".")[0] in literal:
                    continue
                if globbed:
                    add("format", name, f'"{name}" builds a pattern from {template} inside quotes, but the engine '
                                        "quotes each value: give the whole pattern as an input (-iname {pattern}, "
                                        f"pattern: '*word*.md'), or filter after with | grep -i {template}")
                else:
                    add("format", name, f'"{name}" quotes {template}: the engine already shell-quotes every value. '
                                        f'Write {template} without quotes around it.')
        if isinstance(command, str) and _deletes(command):
            add("safety", name, f'"{name}" deletes files. Habits never delete: move to the Trash instead (mv … ~/.Trash/).')
        # Read as it will be once its literal {word}s are split ({''word}): that one fix is said on its own.
        split = re.sub(r"\{(%s)([.}])" % "|".join(map(re.escape, sorted(_literal(node, defined)))), r"{''\1\2",
                       command) if isinstance(command, str) and _literal(node, defined) else command
        cause = shell.why(split) if isinstance(command, str) and node.get("read") is True else ""
        reading = node.get("read") is True or (node.get("read") is not False and isinstance(command, str)
                                                and _reads_only(command))
        extra = sorted(str(k) for k in args if k not in READ_ARGS) if tool == "terminal" and reading else []
        if extra:  # it runs in shadow too, and Hermes's background, notify… would keep it going past the run
            add("safety", name, f'"{name}": a read takes only command, workdir and timeout; {", ".join(extra)} '
                                "would keep it running or waking you past the run: leave them off")
        if node.get("read") is True and tool not in READ_TOOLS and tool != "terminal":
            add("safety", name, f'"{name}": read: true is only for a terminal read or a read tool '
                                f'({", ".join(sorted(READ_TOOLS))}): leave it off this {tool} step')
        elif node.get("read") is True and tool == "terminal" and cause:
            add("safety", name, f'"{name}" is marked read: true but may write: {cause}. Read commands: ls, cat, '
                                "grep, find, sort, uniq, jq, awk, sed -n, curl GET, git branch/rev-parse, hc ls/get/read, "
                                "systemctl is-active, ssh host '<read>', with no redirection but to /dev/null "
                                "(the full list: skill habits:writing-habits)")
        each = node.get("each").split(".")[0] if isinstance(node.get("each"), str) else None
        tainted = llm_values | ({"item"} if each in llm_values else set())
        if tool == "terminal" and tainted & _references(args):
            value = sorted(tainted & _references(args))[0]
            add("safety", name, f'"{name}" puts a value written by an llm node into a terminal command. Use it in a '
                                f'message, or write the file with do: write_file, args: {{path: …, content: "{{{value}…}}"}}')
    if is_watch(habit):
        own = habit.get("limits") if isinstance(habit.get("limits"), dict) else {}
        for key, cap in WATCH_CAPS.items():
            value = own.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > cap:
                add("safety", "", f"a watch runs unattended: limit {key} is {cap} at most")
