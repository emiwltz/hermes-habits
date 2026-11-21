"""Run a reflex.

A reflex is one Python function, `def reflex(rx, ...)`. It runs in a sandbox that has no file, no network and no
process of its own: it touches the world only through `rx`, and every call to `rx` stops the sandbox and comes here.

    rx.sh(cmd)                  a command that only reads         runs in a dry run too
    rx.do(cmd)                  a command that changes something  only planned in a dry run
    rx.read(path)               the text of a file
    rx.write(path, text)        write a file                      only planned in a dry run
    rx.ask(question, options)   a closed question for the small model; what it is unsure of goes up
    rx.up(question, options)    one precise question for the big model: the run freezes, then carries on

A run ends in one of three ways: done (the reflex returned), asks (it is frozen on a question; `resume` carries on
with the answer) or stopped (something went wrong: the reason, and what was done before).
"""

from __future__ import annotations

import ast
import atexit
import inspect
import json
import shlex
import string
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import pydantic_monty as monty

VERBS = ("sh", "do", "read", "write", "ask", "up")
LIMITS = {"seconds": 120, "effects": 200, "cpu": 10}  # wall time, calls to rx, seconds of the reflex's own computing
COMMAND_SECONDS = 30  # one command, unless the call says timeout=
MIN_CONFIDENCE = 0.8  # under this, an answer of the small model is a doubt
SHELL_WORDS = {"|", "||", "&&", ";", "&", ">", ">>", "<", "<<", "2>", "2>&1", "&>"}
OUTPUT = 2_000_000  # characters one read may hand to the reflex
SHOWN = 300  # characters of an output kept in the journal
CONTEXT = 2000  # characters of an item's context shown to whoever answers
MARK = b"reflex/1\n"
NOT_STARTED = "not started: the run was out of time"
NO_FILES = ("the sandbox has no files of its own: read with rx.read(path), write with rx.write(path, text), list with "
            "rx.sh(['ls', folder]), move or delete with rx.do([...])")
NO_MODULE = {  # what a reflex reaches for by habit, and what does the job here
    "subprocess": "run a command with rx.sh (it only reads) or rx.do (it changes something)",
    "shutil": "move, copy or delete with rx.do(['mv', ...]), rx.do(['cp', ...]), rx.do(['rm', ...])",
    "glob": "list files with rx.sh(['ls', folder]) or rx.sh(['find', folder, '-name', '*.txt'])",
    "socket": "fetch with rx.sh(['curl', '-s', url])",
    "requests": "fetch with rx.sh(['curl', '-s', url])",
    "urllib": "fetch with rx.sh(['curl', '-s', url]); take a URL apart with re or str methods",
    "http": "fetch with rx.sh(['curl', '-s', url])",
}
# Pure helpers Python has and the sandbox lacks: lent from the host, they touch nothing.
LENT = {
    "shlex": {"quote": shlex.quote, "split": shlex.split, "join": shlex.join},
    "textwrap": {"dedent": textwrap.dedent, "indent": textwrap.indent, "shorten": textwrap.shorten,
                 "fill": textwrap.fill, "wrap": textwrap.wrap},
    "string": {name: getattr(string, name) for name in ("ascii_letters", "ascii_lowercase", "ascii_uppercase",
                                                        "digits", "hexdigits", "punctuation", "whitespace")},
}
TAIL = "\n\nimport rx\n__reflex_inputs__ = dict(__reflex_inputs__)\n{call}\n"
READY = ("re", "json", "math", "datetime", "collections", "itertools", "functools", "os")  # there without an import


@dataclass
class Outcome:
    status: str  # "done", "asks" or "stopped"
    result: object = None  # done: what the reflex returned
    asks: dict | None = None  # asks: the question
    reason: str = ""  # stopped: why
    effects: list = field(default_factory=list)  # every call to rx, in order
    planned: list = field(default_factory=list)  # dry run: what it would change
    state: bytes | None = None  # asks: the frozen run, for resume()
    output: str = ""  # what the reflex printed
    dry: bool = False
    ms: int = 0

    def to_dict(self) -> dict:
        data = {"status": self.status, "dry": self.dry, "ms": self.ms, "effects": self.effects}
        if self.status == "done":
            data["result"] = self.result
        if self.status == "asks":
            data["asks"] = self.asks
        if self.status == "stopped":
            data["reason"] = self.reason
        if self.planned:
            data["planned"] = self.planned
        if self.output:
            data["output"] = self.output
        return data


class Refused(ValueError):
    """The source is not a reflex, or the inputs are not its inputs."""


def describe(source: str) -> dict:
    """What a reflex says of itself, read without running it: its description and its inputs."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise Refused(f"not valid Python: {exc.msg} (line {exc.lineno})") from None
    found = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "reflex"]
    if not found:
        raise Refused("no reflex here: a reflex is one function, def reflex(rx, ...)")
    fn = found[-1]
    if isinstance(fn, ast.AsyncFunctionDef):
        raise Refused("reflex is a plain function (def), not async def")
    args = fn.args
    names = [a.arg for a in args.posonlyargs + args.args]
    defaults = [None] * (len(names) - len(args.defaults)) + list(args.defaults)
    takes_rx = bool(names) and names[0] == "rx"
    inputs = []
    for name, default in list(zip(names, defaults))[1 if takes_rx else 0:] + list(zip(
            [a.arg for a in args.kwonlyargs], args.kw_defaults)):
        entry = {"name": name, "required": default is None}
        if default is not None:
            try:
                entry["default"] = ast.literal_eval(default)
            except Exception:  # not a literal (a name, a call, a set of lists): shown as it is written
                entry["default"] = ast.unparse(default)
        inputs.append(entry)
    doc = ast.get_docstring(fn) or ast.get_docstring(tree) or ""
    return {"doc": doc.strip(), "inputs": inputs, "rx": takes_rx, "open": args.kwarg is not None}


def _prepared(source: str) -> tuple:
    """(the code the sandbox runs, how many lines it adds before the author's first line).

    The everyday modules the reflex did not bind itself are imported for it: a forgotten `import re` is not worth a
    failed run. The import is one line put first; after a `from __future__` import, which must stay first, it goes
    on that same line instead and adds none."""
    tree = ast.parse(source)
    bound = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bound |= {n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)}
    names = [("os.path" if name == "os" else name) for name in READY if name not in bound]
    ready = "import " + ", ".join(names) if names else "pass"
    code = _first_of(source)
    future = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "__future__"]
    if not future:
        return ready + "\n" + code, 1
    lines = code.split("\n")
    at, col = future[-1].end_lineno - 1, future[-1].end_col_offset
    row = lines[at].encode()
    lines[at] = (row[:col] + b"; " + ready.encode() + row[col:]).decode()
    return "\n".join(lines), 0


def _first_of(source: str) -> str:
    """next(x for x in ...) as Python means it. The sandbox builds a generator expression as a list, which next()
    refuses: the expression is wrapped in iter(), in place, so every line keeps its number."""
    edits = []  # (line, byte column, text to insert there)
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "next" and node.args
                and isinstance(node.args[0], ast.GeneratorExp)):
            arg = node.args[0]
            # next(x for x in xs): the expression borrows the call's own brackets, and its span holds them
            shared = (arg.lineno, arg.col_offset) == (node.func.end_lineno, node.func.end_col_offset)
            edits.append((arg.lineno, arg.col_offset, b"(iter" if shared else b"iter("))
            edits.append((arg.end_lineno, arg.end_col_offset, b")"))
    if not edits:
        return source
    lines = [line.encode() for line in source.split("\n")]
    for line, col, text in sorted(edits, reverse=True):  # from the end: what is still to place keeps its offset
        lines[line - 1] = lines[line - 1][:col] + text + lines[line - 1][col:]
    return "\n".join(line.decode() for line in lines)


def run(source: str, inputs: dict | None = None, host=None, *, dry: bool = False, decider=None,
        limits: dict | None = None, min_confidence: float = MIN_CONFIDENCE) -> Outcome:
    """Run a reflex from its source. dry: reads and questions happen, changes are only planned."""
    job = _Run(host, decider, dry, {**LIMITS, **(limits or {})}, min_confidence)
    try:
        about = describe(source)
        given = dict(inputs or {})
        known = [i["name"] for i in about["inputs"]]
        unknown = [name for name in given if name not in known] if not about["open"] else []
        missing = [i["name"] for i in about["inputs"] if i["required"] and i["name"] not in given]
        if unknown or missing:
            takes = ", ".join(known) or "no input"
            raise Refused("; ".join(filter(None, [
                f"unknown input: {', '.join(unknown)}" if unknown else "",
                f"missing input: {', '.join(missing)}" if missing else ""])) + f" (it takes: {takes})")
    except Refused as exc:
        return job.stopped(str(exc))
    call = "reflex(rx, **__reflex_inputs__)" if about["rx"] else "reflex(**__reflex_inputs__)"
    code, job.shift = _prepared(source)
    return job.start(code + TAIL.format(call=call), given)


def resume(state: bytes, answer, host=None, *, decider=None) -> Outcome:
    """Carry on a run that asked, with the answer."""
    try:
        if not isinstance(state, bytes) or not state.startswith(MARK):
            raise ValueError("not a frozen run")
        head, _, snapshot = state[len(MARK):].partition(b"\n")
        meta = json.loads(head)
        job = _Run(host, decider, meta["dry"], meta["limits"], meta["min_confidence"])
        job.effects, job.planned, job.printed, job.spent = meta["effects"], meta["planned"], meta["output"], meta["ms"]
        job.shift, pending = meta.get("shift", 0), meta["pending"]
        pending["verb"], pending["asks"]
    except (ValueError, KeyError, TypeError) as exc:
        return Outcome("stopped", reason=f"this run cannot be carried on: its saved state is damaged ({exc})")
    return job.carry_on(snapshot, pending, answer, state)


# ---- the sandbox ---------------------------------------------------------------------------------------------

_pool = None
_pool_lock = threading.Lock()


def _sandbox():
    """One pool of sandbox workers per process, started at the first run."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = monty.Monty()
            _pool.__enter__()
            atexit.register(_close)
        return _pool


def _close():
    global _pool
    if _pool is not None:
        try:
            _pool.__exit__(None, None, None)
        finally:
            _pool = None


def _never(*args, **kwargs):
    """Stands for each verb inside the sandbox: every call stops the sandbox before it gets here."""
    raise RuntimeError("rx is answered by the engine")


class _Pause(Exception):
    def __init__(self, pending: dict, asks: dict):
        self.pending, self.asks = pending, asks


class _Stop(Exception):
    """The run ends here, whatever the reflex would do next."""


class _Raise(Exception):
    """An error the reflex sees, and may catch."""

    def __init__(self, error: BaseException):
        self.error = error


class _Run:
    def __init__(self, host, decider, dry, limits, min_confidence):
        if host is None:
            from .host import Local
            host = Local()
        self.host, self.decider, self.dry, self.limits = host, decider, bool(dry), limits
        self.min_confidence = min_confidence
        self.effects, self.planned, self.printed, self.spent = [], [], "", 0
        self.began = time.monotonic()
        self.last_error = ""
        self.shift = 0  # lines the engine put before the author's first one

    # ---- the three ends ---------------------------------------------------------------------------------------

    def ms(self) -> int:
        return self.spent + int((time.monotonic() - self.began) * 1000)

    def outcome(self, status, **more) -> Outcome:
        return Outcome(status, effects=self.effects, planned=self.planned, output=self.printed, dry=self.dry,
                       ms=self.ms(), **more)

    def stopped(self, reason: str) -> Outcome:
        return self.outcome("stopped", reason=reason)

    # ---- driving the sandbox ----------------------------------------------------------------------------------

    def session(self):
        modules = {"rx": monty.ExternalModule({**{verb: _never for verb in VERBS}, "home": self.host.home}),
                   **{name: monty.ExternalModule(attrs) for name, attrs in LENT.items()}}
        limits = monty.ResourceLimits(max_feed_duration_secs=self.limits["cpu"], max_memory=256_000_000)
        return _sandbox().checkout(external_modules=modules, limits=limits, script_name="reflex.py")

    def start(self, code: str, inputs: dict) -> Outcome:
        try:
            with self.session() as session:
                snap = session.feed_start(code, inputs={"__reflex_inputs__": inputs}, print_callback=self.heard)
                return self.drive(snap)
        except monty.MontyError as exc:
            return self.stopped(self.said(exc))

    def carry_on(self, snapshot: bytes, pending: dict, answer, state: bytes) -> Outcome:
        try:
            value = self.answered(pending, answer)
        except Refused as exc:  # an answer that does not fit: the same question again, and why
            return self.outcome("asks", asks={**pending["asks"], "error": str(exc)}, state=state)
        try:
            with self.session() as session:
                snap = session.load_snapshot(snapshot, print_callback=self.heard)
                return self.drive(snap.resume({"return_value": value}))
        except monty.MontyError as exc:
            return self.stopped(self.said(exc))

    def drive(self, snap) -> Outcome:
        try:
            return self.driven(snap)
        except monty.MontyError:
            raise
        except Exception as exc:  # a fault of the engine or of a host: the run stops, and its journal is kept
            return self.stopped(f"the engine failed ({type(exc).__name__}: {exc}): what the journal lists was done")

    def driven(self, snap) -> Outcome:
        while not isinstance(snap, monty.MontyComplete):
            if self.ms() > self.limits["seconds"] * 1000:
                return self.stopped(f"budget: more than {self.limits['seconds']} s")
            name = getattr(snap, "function_name", None) or ""
            if getattr(snap, "is_os_function", False):
                answer = self.system(name, snap.args)
                snap = snap.resume(answer) if answer else snap.resume_auto()  # else the sandbox's own rules
                continue
            if not name.startswith("rx."):
                snap = snap.resume_auto()  # a lent helper (shlex.quote), an unknown name
                continue
            try:
                value = self.effect(name[3:], snap.args, snap.kwargs)
            except _Raise as raised:
                self.last_error = str(raised.error)
                snap = snap.resume({"exception": raised.error})
                continue
            except _Pause as pause:
                head = json.dumps({"dry": self.dry, "limits": self.limits, "min_confidence": self.min_confidence,
                                   "shift": self.shift,
                                   "effects": self.effects, "planned": self.planned, "output": self.printed,
                                   "ms": self.ms(), "pending": {**pause.pending, "asks": pause.asks}}, default=str)
                return self.outcome("asks", asks=pause.asks, state=MARK + head.encode() + b"\n" + snap.dump())
            except _Stop as stop:
                return self.stopped(str(stop))
            snap = snap.resume({"return_value": value})
        result = snap.output
        if result is None and self.printed.strip():
            result = self.printed.strip()  # a reflex that prints its answer instead of returning it
        return self.outcome("done", result=result)

    def system(self, name: str, args) -> dict | None:
        """What the sandbox asks of the system: the home folder and a short sleep are given, files and the modules
        that reach outside are refused with what to use instead. None: the sandbox's own answer stands."""
        if name == "os.getenv":
            return {"return_value": self.host.home if args[:1] == ("HOME",) else (args[1] if len(args) > 1 else None)}
        if name == "system.sleep":
            left = self.limits["seconds"] - self.ms() / 1000  # a sleep spends the run's time like anything else
            time.sleep(max(0.0, min(float(args[0]), 10.0, left + 0.05)))
            return {"return_value": None}
        if name == "import" and str(args[0]).split(".")[0] in NO_MODULE:
            root = str(args[0]).split(".")[0]
            return {"exception": ModuleNotFoundError(f"no {root} in the sandbox: {NO_MODULE[root]}")}
        if name == "open" or name.startswith("Path."):
            return {"exception": PermissionError(NO_FILES)}
        return None

    def heard(self, stream, text):
        self.printed = (self.printed + text)[-20_000:]

    def said(self, exc) -> str:
        """Why the reflex stopped, with its line."""
        text = str(exc)
        if isinstance(exc, monty.MontySyntaxError):
            return f"not valid Python: {text}"
        try:
            frames = [f for f in exc.traceback() if f.function_name != "<module>"]
        except Exception:  # an error with no frames to show
            frames = []
        line = f" (line {frames[-1].line - self.shift} of the reflex)" if frames else ""
        return f"{text}{line}"

    # ---- the verbs --------------------------------------------------------------------------------------------

    def effect(self, verb: str, args, kwargs):
        if len(self.effects) >= self.limits["effects"]:
            raise _Stop(f"budget: more than {self.limits['effects']} calls to rx")
        if self.ms() > self.limits["seconds"] * 1000:
            raise _Stop(f"budget: more than {self.limits['seconds']} s")
        handler = getattr(self, f"rx_{verb}", None)
        if handler is None:
            raise _Raise(AttributeError(f"rx has no {verb}: it has {', '.join(VERBS)}"))
        try:
            bound = inspect.signature(handler).bind(*args, **kwargs)
        except TypeError as exc:
            raise _Raise(TypeError(f"rx.{verb}{inspect.signature(handler)}: {exc}")) from None
        return handler(*bound.args, **bound.kwargs)

    def note(self, verb: str, what: str, began: float, **more) -> dict:
        entry = {"verb": verb, "what": what, "ms": int((time.monotonic() - began) * 1000), **more}
        self.effects.append(entry)
        return entry

    def command(self, verb: str, cmd) -> list:
        """A command as a list of words, ~ written out. No shell reads it."""
        if isinstance(cmd, str):
            try:
                words = shlex.split(cmd)
            except ValueError as exc:
                raise _Raise(ValueError(f"rx.{verb}: cannot split {cmd!r} into words ({exc}): pass a list of words"))
        elif isinstance(cmd, (list, tuple)) and all(isinstance(w, (str, int, float)) for w in cmd):
            words = [str(w) for w in cmd]
        else:
            raise _Raise(TypeError(f"rx.{verb} takes a command as a list of words, such as ['ls', '-la', folder]"))
        if not words:
            raise _Raise(ValueError(f"rx.{verb}: an empty command"))
        shell = sorted(set(words) & SHELL_WORDS) or [w for w in words if w.startswith("$(") or "`" in w][:1]
        if shell:
            raise _Raise(ValueError(
                f"rx.{verb} runs one program, without a shell: {shell[0]} is not read. Call rx.{verb} once per "
                "program and do the piping, filtering and counting in Python"))
        return [self.host.path(w) if w == "~" or w.startswith("~/") else w for w in words]

    def seconds(self, timeout) -> float:
        left = self.limits["seconds"] - self.ms() / 1000
        asked = timeout if isinstance(timeout, (int, float)) and timeout > 0 else COMMAND_SECONDS
        return max(1.0, min(float(asked), left))

    def ran(self, argv, seconds, input) -> tuple:
        """The host's (output, errors, exit code); a host that raises counts as a command that failed."""
        try:
            out, err, code = self.host.run(argv, seconds, input)
            return str(out or ""), str(err or ""), int(code)
        except Exception as exc:
            return "", f"{type(exc).__name__}: {exc}", 126

    def failed(self, verb, argv, out, err, code, ok) -> str:
        if ok is None or code in ok:
            return ""
        said = (err or out or "").strip()
        return f"rx.{verb} {shlex.join(argv)}: exit {code}" + (f": {said[:300]}" if said else "")

    @staticmethod
    def codes(ok):
        if ok is None:
            return None
        if isinstance(ok, int) and not isinstance(ok, bool):
            return (ok,)
        if isinstance(ok, (list, tuple)) and all(isinstance(c, int) for c in ok):
            return tuple(ok)
        raise _Raise(TypeError("ok is the exit codes that count as success, such as (0, 1); None accepts any"))

    def rx_sh(self, cmd, ok=(0,), timeout=None, input=None):
        """A command that only reads; its output. A list of commands runs side by side: one output each."""
        ok = self.codes(ok)
        many = isinstance(cmd, (list, tuple)) and bool(cmd) and all(isinstance(c, (list, tuple)) for c in cmd)
        commands = [self.command("sh", c) for c in (cmd if many else [cmd])]
        for argv in commands:
            why = self.host.reads_only(argv)
            if why:
                raise _Raise(ValueError(f"rx.sh only reads, and {why}. If the command changes something, it is "
                                        "rx.do; if it only computes, do it in Python"))
        if len(self.effects) + len(commands) > self.limits["effects"]:
            raise _Stop(f"budget: more than {self.limits['effects']} calls to rx")
        seconds = self.seconds(timeout)
        deadline = time.monotonic() + (self.limits["seconds"] - self.ms() / 1000)  # for the whole list

        def one(argv):
            started = time.monotonic()
            left = deadline - started
            if left <= 0:
                return argv, "", NOT_STARTED, 124, started
            out, err, code = self.ran(argv, min(seconds, max(1.0, left)), input)
            return argv, out, err, code, started
        workers = min(int(getattr(self.host, "workers", 8)), len(commands))  # a host may take one command at a time
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(one, commands))
        else:
            results = [one(argv) for argv in commands]
        if any(err == NOT_STARTED for _, _, err, _, _ in results):  # some of the list never ran: no result to give
            for argv, out, err, code, started in results:
                self.note("sh", shlex.join(argv), started, code=code, **({"error": err} if code else {}))
            raise _Stop(f"budget: more than {self.limits['seconds']} s")
        outputs, failure = [], ""
        for argv, out, err, code, started in results:
            problem = self.failed("sh", argv, out, err, code, ok)
            self.note("sh", shlex.join(argv), started, code=code, out=_short(out),
                      **({"outside": True} if self.host.reads_outside(argv) else {}),
                      **({"error": problem} if problem else {}))
            failure = failure or problem
            outputs.append(_cut(out).rstrip("\n"))
        if failure:
            raise _Raise(RuntimeError(failure))
        return outputs if many else outputs[0]

    def rx_do(self, cmd, ok=(0,), timeout=None, input=None):
        """A command that changes something; its output. In a dry run it is only planned, and gives ""."""
        ok, argv = self.codes(ok), self.command("do", cmd)
        began = time.monotonic()
        if self.dry:
            self.planned.append(shlex.join(argv))
            self.note("do", shlex.join(argv), began, planned=True)
            return ""
        out, err, code = self.ran(argv, self.seconds(timeout), input)
        problem = self.failed("do", argv, out, err, code, ok)
        self.note("do", shlex.join(argv), began, code=code, out=_short(out), **({"error": problem} if problem else {}))
        if problem:
            raise _Raise(RuntimeError(problem))
        return _cut(out).rstrip("\n")

    def rx_read(self, path):
        """The text of a file."""
        began, full = time.monotonic(), self.host.path(_text("read", "path", path))
        try:
            text = self.host.read(full)
        except Exception as exc:
            problem = f"rx.read {full}: {getattr(exc, 'strerror', None) or exc}"
            self.note("read", full, began, error=problem)
            raise _Raise(FileNotFoundError(problem) if isinstance(exc, FileNotFoundError) else OSError(problem))
        self.note("read", full, began, chars=len(text))
        return _cut(text)

    def rx_write(self, path, text, append=False):
        """Write a file (its folders are made). In a dry run it is only planned."""
        began, full = time.monotonic(), self.host.path(_text("write", "path", path))
        text = _text("write", "text", text)
        what = f"{'append to' if append else 'write'} {full} ({len(text)} characters)"
        if self.dry:
            self.planned.append(what)
            self.note("write", full, began, planned=True, chars=len(text))
            return None
        try:
            self.host.write(full, text, bool(append))
        except Exception as exc:
            problem = f"rx.write {full}: {getattr(exc, 'strerror', None) or exc}"
            self.note("write", full, began, error=problem)
            raise _Raise(OSError(problem))
        self.note("write", full, began, chars=len(text))
        return None

    def rx_ask(self, question, options=None, about=None, context=None):
        """A closed question for the small model. Without options it is yes or no, and gives True or False.
        about: one thing, or a list (one question each, {item: answer} back). What it is unsure of goes up."""
        question = _text("ask", "question", question)
        yes_no = options is None
        choices = ["yes", "no"] if yes_no else _options("ask", options)
        many = isinstance(about, (list, tuple))
        items = list(about) if many else [about]
        if many and not items:
            return {}
        if any(not isinstance(i, (str, int, float, type(None))) for i in items):
            raise _Raise(TypeError("rx.ask: each thing in about is a text (a name, a line, a path)"))
        if many and len({_key(i) for i in items}) != len(items):
            raise _Raise(ValueError("rx.ask: the same thing is twice in about"))

        def context_of(item):
            # {item: text} gives each item of a list its own; anything else is the material of every question
            found = context.get(item, "") if many and isinstance(context, dict) else (context or "")
            return found if isinstance(found, str) else json.dumps(found, default=str, ensure_ascii=False)
        began, answers, by = time.monotonic(), None, "nobody"
        if self.decider is not None:
            try:
                answers = list(self.decider(question, choices, [(_key(i), context_of(i)) for i in items]))
                by = getattr(self.decider, "name", "small model")
            except Exception as exc:  # no small model answered: every item is a doubt
                answers, by = None, f"nobody ({type(exc).__name__}: {str(exc)[:80]})"
        sure, unsure = {}, []
        for at, item in enumerate(items):
            try:  # an answer in any other shape than (option, confidence) is a doubt too
                answer, confidence = answers[at]
                confidence = float(confidence)
            except Exception:
                answer, confidence = None, 0.0
            if answer in choices and confidence >= self.min_confidence:
                sure[_key(item)] = answer
            else:
                unsure.append(item)
        self.note("ask", question, began, items=len(items), unsure=len(unsure), by=by)
        if unsure:
            asks = {"question": question, "options": choices}
            if many or about is not None:
                asks["about"] = [{"item": i, **({"context": context_of(i)[:CONTEXT]} if context_of(i) else {})}
                                 for i in unsure]
            elif context_of(None):
                asks["context"] = context_of(None)[:CONTEXT]  # what the one question is about
            raise _Pause({"verb": "ask", "many": many, "yes_no": yes_no, "options": choices, "sure": sure,
                          "items": items, "unsure": [_key(i) for i in unsure]}, asks)
        return _answers(sure, items, many, yes_no)

    def rx_up(self, question, options=None, **context):
        """One precise question for the big model. The run freezes; it carries on with the answer.
        With options the answer is one of them; without, it is whatever the big model gives."""
        question = _text("up", "question", question)
        choices = None if options is None else _options("up", options)
        taken = sorted({"about", "error", "question"} & set(context))
        if taken:
            raise _Raise(ValueError(f"rx.up: {taken[0]} is a name of the question itself; give that value another name"))
        self.note("up", question, time.monotonic())
        raise _Pause({"verb": "up", "options": choices},
                     {"question": question, **({"options": choices} if choices else {}), **context})

    def answered(self, pending: dict, answer):
        """The value the frozen call gives back, from the answer; Refused when it does not fit the question."""
        if pending["verb"] == "up":
            choices = pending.get("options")
            if choices and str(answer).strip() not in choices:
                raise Refused(f"{answer!r} is not one of {', '.join(choices)}")
            return str(answer).strip() if choices else answer
        options, unsure = pending["options"], pending["unsure"]
        if not isinstance(answer, dict):
            if len(unsure) != 1:
                raise Refused(f"answer with one option for each of the {len(unsure)} items, as {{item: option}}")
            answer = {unsure[0]: answer}
        given = {}
        for item in unsure:
            if item not in answer:
                raise Refused(f"no answer for {item!r}")
            value = answer[item]
            text = str(value).strip()
            if pending["yes_no"]:
                text = {"true": "yes", "y": "yes", "false": "no", "n": "no"}.get(text.lower(), text.lower())
            if text not in options:
                raise Refused(f"{value!r} for {item!r} is not one of {', '.join(options)}")
            given[item] = text
        return _answers({**pending["sure"], **given}, pending["items"], pending["many"], pending["yes_no"])


def _answers(found: dict, items: list, many: bool, yes_no: bool):
    """What rx.ask gives back: the caller's own items as keys, True or False for a yes-or-no question."""
    values = {item: (found[_key(item)] == "yes" if yes_no else found[_key(item)]) for item in items}
    return values if many else values[items[0]]


def _options(verb: str, options) -> list:
    if not isinstance(options, (list, tuple)):
        raise _Raise(TypeError(f"rx.{verb}: options is a list of answers, such as ['task', 'idea']"))
    choices = [str(o) for o in options]
    if len(choices) < 2 or len(set(choices)) != len(choices):
        raise _Raise(ValueError(f"rx.{verb}: options are at least two different answers"))
    return choices


def _key(item) -> str:
    return "" if item is None else str(item)


def _text(verb: str, name: str, value) -> str:
    if not isinstance(value, str):
        raise _Raise(TypeError(f"rx.{verb}: {name} is a text, not {type(value).__name__}"))
    return value


def _cut(text: str) -> str:
    if len(text) > OUTPUT:
        raise _Raise(RuntimeError(f"an output of {len(text)} characters is too much to hand over: narrow the read"))
    return text


def _short(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= SHOWN else text[:SHOWN] + f"… (+{len(text) - SHOWN})"
