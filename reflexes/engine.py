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
import builtins
import datetime
import inspect
import itertools
import json
import os
import re
import shlex
import string
import sys
import threading
import time
import types
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
NOTED = 2000  # characters of a command or of an error kept in the journal
LENT_CHARS = 100_000  # characters a lent helper takes: it runs outside the sandbox and its limits
CONTEXT = 2000  # characters of an item's context shown to whoever answers
DOUBTS = 200  # things one question may send up: more than that, no caller answers in one go
ASKED = 2000  # things one question may be about
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
    "operator": "write the function out: key=lambda x: x[1]",
    "statistics": "write it out: sum(xs) / len(xs), sorted(xs)[len(xs) // 2]",
    "heapq": "use sorted(xs)[:n]",
    "enum": "use plain constants",
    "pprint": "use json.dumps(value, indent=2)",
    "uuid": "use rx.sh(['uuidgen'])",
    "tempfile": "use rx.do(['mktemp'])",
    "difflib": "use rx.sh(['diff', a, b], ok=(0, 1))",
    "textwrap": "use str methods: ' '.join(text.split()), text[:80], '\\n'.join('  ' + line for line in lines)",
}
HINTS = (  # what the sandbox says in its own words, and what the author should do about it
    ("cannot suspend in this synchronous context",
     "rx (or shlex) cannot be called from inside sorted, min, max, map or filter: get the values first, in a list "
     "comprehension, then sort or pick"),
    ("does not match format",
     "the sandbox's strptime wants a whole date (%Y, %m and %d) and, for a time, %H with %M: cut the text yourself "
     "for anything else"),
    ("is not an iterator",
     "in the sandbox a generator expression, map, filter and zip are lists: wrap it in iter(...), or take its "
     "items by index"),
    ("exceptions can only be called with zero or one string argument",
     "an error takes one text here: raise ValueError(f'...')"),
    ("callable replacement is not yet supported", "build the text from re.finditer(...) instead"),
    ("'float' object cannot be interpreted as an integer",
     "timedelta and range take whole numbers here: timedelta(seconds=int(hours * 3600))"),
    ("'datetime.datetime' has no attribute 'today'", "use datetime.now()"),
    ("has no attribute 'utcnow'", "use datetime.now(datetime.timezone.utc)"),
    ("cannot import name 'UTC'", "use datetime.timezone.utc"),
    ("cannot import name", "the sandbox has part of the standard library only: write that step in plain Python"),
    ("No module named", "the sandbox has part of the standard library only: write that step in plain Python, or "
                        "get it from a command with rx.sh"),
    ("does not yet support method decorators",
     "write a plain method or a function instead of @property, @staticmethod or @classmethod"),
    ("not supported between instances of",
     "the sandbox does not call __lt__ and its kin: sort or compare with key=lambda x: x.field"),
)
NOT_CALLED = ("__bool__", "__len__", "__format__")  # the sandbox ignores them in a class, without a word
NAMES = {kind.__name__: sorted(dir(kind)) for kind in (str, bytes, list, dict, set, frozenset, tuple, int, float, bool)}
NO_VALUE = re.compile(r"'(\w+)'(?: object)? has no attribute '(\w+)'")  # a method taken without being called
HIT = "_rx_hit"  # the name that any, all and next use to stop at the first item that settles them
SETTLED = {"any": f") and not {HIT}.append(1))", "all": f") or {HIT}.append(1))", "next": f"), {HIT}.append(1))[0]"}
LISTS_HERE = {"map", "filter", "zip", "enumerate", "reversed"}  # iterators in Python, lists in the sandbox
TYPES = {"str", "bytes", "dict", "list", "set", "tuple", "int", "float"}
KEYED = {"sorted", "min", "max"}  # their key= is a function, whatever else is called key elsewhere
# What the rewritten source calls, put before the author's code only when it is needed.
OURS = {
    # any, all and next stop where Python stops: an iterator that ends once the hit is made, so that an iterator
    # the author shares is left just after the item found, and an endless one ends
    "until": """_rx_iter, _rx_next = iter, next
class _rx_until:
    def __init__(self, source, hit): self.source, self.hit = _rx_iter(source), hit
    def __iter__(self): return self
    def __next__(self):
        if self.hit: raise StopIteration
        return _rx_next(self.source)
""",
    # str.lower handed a number: the error Python gives (the method is in a lambda, which would say another)
    "unfit": """def _rx_unfit(kind, method, given):
    raise TypeError("descriptor '" + method + "' for '" + kind + "' objects doesn't apply to a '" + given + "' object")
""",
    # two builtins that answer wrongly without a word in the sandbox, defined again when the reflex uses them (its
    # own function of that name, coming later, still wins). hasattr is always False on a built-in value there
    "hasattr": """_rx_hasattr = hasattr
def hasattr(thing, name):
    for kind in (bool, int, float, str, bytes, list, tuple, dict, set, frozenset):
        if isinstance(thing, kind):  # a Counter is a dict here: it has what a dict has, at least
            return name in _rx_names[kind.__name__] or _rx_hasattr(thing, name)
    return _rx_hasattr(thing, name)
""",
    # and round(x, n) differs from Python's on halves: round(2.675, 2) gives 2.68 there
    "round": """_rx_round = round
def round(number, ndigits=None):
    if ndigits is None:
        return _rx_round(number)
    if isinstance(number, float) and isinstance(ndigits, int) and not isinstance(ndigits, bool) and 0 <= ndigits < 300:
        return float(format(number, "." + str(ndigits) + "f"))
    return _rx_round(number, ndigits)
""",
}


def _lent(helper):
    """A helper of the host, with a bound on what it is given: it runs here, outside the sandbox and its limits."""
    def size(value) -> int:
        if isinstance(value, (str, bytes)):
            return len(value)
        if isinstance(value, dict):
            return sum(size(item) for item in value.values())
        return sum(size(item) for item in value) if isinstance(value, (list, tuple, set, frozenset)) else 0

    def bounded(*args, **kwargs):
        if size([args, kwargs]) > LENT_CHARS:
            raise ValueError(f"{helper.__name__} takes {LENT_CHARS} characters at most here")
        return helper(*args, **kwargs)
    return bounded


# Pure helpers Python has and the sandbox lacks: lent from the host, they touch nothing. Few on purpose: each one
# runs outside the sandbox's limits, and of 176 reflexes written by models not one called any of them.
LENT = {
    "shlex": {"quote": _lent(shlex.quote), "split": _lent(shlex.split), "join": _lent(shlex.join)},
    "string": {name: getattr(string, name) for name in ("ascii_letters", "ascii_lowercase", "ascii_uppercase",
                                                        "digits", "hexdigits", "punctuation", "whitespace")},
}
TAIL = "\n\nimport rx\n__reflex_inputs__ = dict(__reflex_inputs__)\n{call}\n"
CUT = threading.Event()  # set by whoever wants every run of this process to stop where it is (the command line does)
INTERRUPTED = "interrupted: what the journal lists was done, and a change may be half made"
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
    tree = _parsed(source)
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
            except Exception:  # not a literal (a name, 24 * 7, a call): shown as it is written
                entry["default"], entry["written"] = ast.unparse(default), True
        inputs.append(entry)
    doc = ast.get_docstring(fn) or ast.get_docstring(tree) or ""
    return {"doc": doc.strip(), "inputs": inputs, "rx": takes_rx, "open": args.kwarg is not None}


def _parsed(source: str):
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise Refused(f"not valid Python: {exc.msg} (line {exc.lineno})") from None
    except (ValueError, RecursionError, MemoryError) as exc:  # a null byte, an expression nested too deep
        raise Refused(f"not valid Python: {type(exc).__name__}: {str(exc)[:100]}") from None


def _prepared(source: str) -> dict:
    """The code the sandbox runs, and what the engine keeps about it: where its own lines sit among the author's
    (at, shift), which of its helpers they hold (needs), the methods the author hands over without calling them
    (loose).

    The everyday modules the reflex did not bind itself are imported for it: a forgotten `import re` is not worth a
    failed run. The engine's lines go first, or right after a `from __future__` import, which must stay first."""
    tree = _parsed(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            ignored = [n.name for n in node.body if isinstance(n, ast.FunctionDef) and n.name in NOT_CALLED]
            if ignored:
                raise Refused(f"class {node.name} defines {ignored[0]}, which the sandbox never calls: give it a "
                              f"plain method instead (is_empty(), size()) and call that (line {node.lineno})")
    bound, modules = set(), set()  # what the source binds at its top level, and which of those are modules
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {(alias.asname or alias.name).split(".")[0] for alias in node.names}
            bound |= names
            modules |= names
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bound |= {n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)}
    ready = [name for name in READY if name not in bound]
    future = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "__future__"]
    at = max((n.end_lineno for n in future), default=0)
    code, loose, needs = _as_meant(source, tree, modules | set(ready) | {"rx"}, _taken(tree))
    if any(n.lineno <= at for n in tree.body if n not in future
           and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))):  # a docstring may come before
        code, needs = source, set()  # code on the very line of the __future__ import would run before our own
    needs |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} & {"hasattr", "round"}
    ours = ["import " + ", ".join("os.path" if name == "os" else name for name in ready) if ready else "pass"]
    ours += [line for name in OURS if name in needs for line in OURS[name].splitlines()]
    lines = code.split("\n")
    lines[at:at] = ours
    return {"code": "\n".join(lines), "at": at, "shift": len(ours), "loose": loose, "needs": needs}


def _taken(tree) -> set:
    """Every name the source binds anywhere (a variable, a parameter, a function, an import): a builtin of that name
    may not be the builtin where it is used."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names |= set(node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def _as_meant(source: str, tree, modules: set, taken: set) -> tuple:
    """(the source with ordinary Python that the sandbox takes otherwise put right, the methods it still hands over
    without calling them, which of the engine's own helpers it now calls).

    - any(...), all(...) and next(...) of a generator expression stop at the first item that settles them, as in
      Python. The sandbox builds a generator expression as a whole list first: rx.do in it ran for every item.
    - next() of map, filter, zip, enumerate or reversed, which are lists in the sandbox.
    - a method handed over where a function is expected (sorted(key=str.lower), max(key=counts.get),
      map(str.strip, lines)): the sandbox has no such value.
    - a generator expression, a map, a zip kept for later (x = zip(a, b), return (...)) is read once, as in Python.

    One thing is refused instead, because nothing can put it right: a generator expression that makes a change
    (rx.do, rx.write, a call that is handed rx) and is read by something that may stop before its end. The sandbox
    would make the change for every item.

    Text is only inserted, around whole expressions, so every line keeps its number. Nothing is touched where the
    author gave one of these names another meaning, nor in f"{x=}", which shows its own text; and no function is
    made in the body of a class, where it would not see the class's names."""
    lines = [line.encode() for line in source.split("\n")]
    edits, order = [], itertools.count(1)  # (line, byte column, rank among those at the same place, text)

    def around(node, before: str, after: str):
        edits.append((node.lineno, node.col_offset, next(order), before))  # at one place, the outer comes first
        edits.append((node.end_lineno, node.end_col_offset, -next(order), after))  # and closes last

    def root(node):  # the name a dotted name starts with; "" for anything that is not names and dots only
        while isinstance(node, ast.Attribute):
            node = node.value
        return node.id if isinstance(node, ast.Name) else ""

    def builtin(node, names) -> str:  # the name called, when it is one of these and still Python's own
        return node.id if isinstance(node, ast.Name) and node.id in names and node.id not in taken else ""

    def shown(field) -> bool:  # f"{x=}": the expression is followed by = and its text is part of the result
        if sys.version_info < (3, 12):
            return True  # and before 3.12 the place of an expression inside an f-string is not reliable
        rest = lines[field.value.end_lineno - 1][field.value.end_col_offset:].lstrip(b" \t)")
        return rest.startswith(b"=") and not rest.startswith(b"==")

    apart = {id(n) for text in ast.walk(tree) if isinstance(text, ast.JoinedStr) for field in text.values
             if isinstance(field, ast.FormattedValue) and shown(field) for n in ast.walk(field)}
    classy = {id(n) for kind in ast.walk(tree) if isinstance(kind, ast.ClassDef) for line in kind.body
              if not isinstance(line, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(line)}
    still = (ast.Name, ast.Attribute, ast.Subscript, ast.Constant, ast.Slice, ast.Tuple, ast.List, ast.Set,
             ast.Dict, ast.UnaryOp, ast.BinOp, ast.expr_context, ast.operator, ast.unaryop)  # reads the same copied
    groupers = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                and n.module == "itertools" for a in n.names if a.name == "groupby"}
    called, wrapped, needs = set(), set(), set()
    parent = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def changes(node) -> bool:  # rx.do or rx.write in it, or a call that is handed rx and may do the same
        for call in ast.walk(node):
            if isinstance(call, ast.Call):
                func = call.func
                if isinstance(func, ast.Attribute) and func.attr in ("do", "write") and root(func) == "rx":
                    return True
                if any(isinstance(a, ast.Name) and a.id == "rx" for a in call.args + [k.value for k in call.keywords]):
                    return True
        return False

    for node in ast.walk(tree):
        if not isinstance(node, (ast.GeneratorExp, ast.Call)) or id(node) in apart:
            continue
        holder = parent.get(id(node))
        lazy = isinstance(node, ast.GeneratorExp) or bool(builtin(node.func, LISTS_HERE))
        if lazy and isinstance(holder, (ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.Return)) and holder.value is node:
            needs.add("until")
            around(node, "_rx_iter(", ")")  # kept for later: read once, and next() takes it
        if not isinstance(node, ast.GeneratorExp) or not any(changes(part) for part in [node.elt] + [
                c for g in node.generators for c in g.ifs] + [g.iter for g in node.generators[1:]]):
            continue
        whole = isinstance(holder, ast.Call) and holder.args[:1] == [node] and (
            builtin(holder.func, set(SETTLED) | {"list", "tuple", "set", "frozenset", "dict", "sorted", "sum", "min",
                                                 "max"}) or (isinstance(holder.func, ast.Attribute)
                                                             and holder.func.attr == "join"))
        if not whole:
            raise Refused(f"the generator expression of line {node.lineno} makes a change (rx.do, rx.write), and the "
                          "sandbox would make it for every item where Python may stop early: write a for loop, or a "
                          "list ([... for ...]) if every item is meant")
    for node in ast.walk(tree):  # outer expressions before the ones they hold
        if not isinstance(node, ast.Call):
            continue
        func, first = node.func, node.args[0] if node.args else None
        called.add(id(func))
        if id(node) in apart:
            continue
        name = builtin(func, SETTLED)
        plain = not node.keywords and len(node.args) == (2 if name == "next" and len(node.args) == 2 else 1)
        if name and plain and isinstance(first, ast.GeneratorExp) and not any(g.is_async for g in first.generators):
            opens, closes = (first.lineno, first.col_offset), (first.end_lineno, first.end_col_offset - 1)
            if lines[opens[0] - 1][opens[1]:opens[1] + 1] == b"(" and lines[closes[0] - 1][closes[1]:closes[1] + 1] == b")":
                needs.add("until")
                around(node, "[", f" for {HIT} in [[]]][0]")
                edits.append((opens[0], opens[1] + 1, next(order), "_rx_iter([" if name == "next" else "["))
                edits.append((*closes, -next(order), "])" if name == "next" else "]"))
                around(first.elt, "((", SETTLED[name])
                for part in first.generators:
                    around(part.iter, "_rx_until(", f", {HIT})")
        elif name == "next" and plain and isinstance(first, ast.Call) and builtin(first.func, LISTS_HERE):
            needs.add("until")
            around(first, "_rx_iter(", ")")
        handed = [first] if builtin(func, {"map", "filter"}) and first is not None else []
        if builtin(func, KEYED) or (isinstance(func, ast.Name) and func.id in groupers) or (
                isinstance(func, ast.Attribute) and (func.attr == "sort" or (
                    func.attr == "groupby" and root(func) in modules))):
            handed += [k.value for k in node.keywords if k.arg == "key"]
        for value in handed:
            if not isinstance(value, ast.Attribute) or id(value) in classy or root(value) in modules - {""}:
                continue  # a module's function (os.path.basename) is a value the sandbox has
            kind, owner = builtin(value.value, TYPES), value.value
            method = getattr(getattr(builtins, kind), value.attr, None) if kind else None
            if isinstance(method, (types.MethodDescriptorType, types.WrapperDescriptorType)):
                if {"isinstance", "type"} & taken:
                    continue
                wrapped.add(id(value))  # str.lower: called on each item, which must be a str as Python wants it
                needs.add("unfit")
                around(value, f"(lambda _s, *_a: _s.{value.attr}(*_a) if isinstance(_s, {kind}) else "
                              f"_rx_unfit('{kind}', '{value.attr}', type(_s).__name__) if True else ", ")")
            elif kind:
                wrapped.add(id(value))  # bytes.fromhex: a function of the type itself
                around(value, "(lambda *_a, **_k: ", "(*_a, **_k))")
            elif all(isinstance(n, still) for n in ast.walk(owner)):
                wrapped.add(id(value))  # counts.get: the owner is read once, here, as Python reads it
                around(value, f"([(lambda *_a, **_k: _o.{value.attr}(*_a, **_k)) for _o in [{ast.unparse(owner)}]][0]"
                              " if True else ", ")")
    loose = sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load)
                    and id(n) not in called | wrapped}
                   | {n.args[1].value for n in ast.walk(tree) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Name) and n.func.id == "getattr" and len(n.args) > 1
                      and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str)})
    if not edits:
        return source, loose, needs
    for line, col, _, text in sorted(edits, reverse=True):  # from the end: what is still to place keeps its offset
        lines[line - 1] = lines[line - 1][:col] + text.encode() + lines[line - 1][col:]
    code = "\n".join(line.decode() for line in lines)
    try:
        ast.parse(code)
    except SyntaxError:  # never hand over a rewrite that does not read: the author's own text then runs as it is
        return source, loose, set()
    return code, loose, needs


def run(source: str, inputs: dict | None = None, host=None, *, dry: bool = False, decider=None,
        limits: dict | None = None, min_confidence: float = MIN_CONFIDENCE) -> Outcome:
    """Run a reflex from its source. dry: reads and questions happen, changes are only planned."""
    job = _Run(host, decider, dry, {**LIMITS, **(limits or {})}, min_confidence)
    try:
        if not isinstance(source, str):
            raise Refused("a reflex is the text of one Python function")
        source = source.replace("\r\n", "\n").replace("\r", "\n")
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
        ready = _prepared(source)
    except Refused as exc:
        return job.stopped(str(exc))
    call = "reflex(rx, **__reflex_inputs__)" if about["rx"] else "reflex(**__reflex_inputs__)"
    job.at, job.shift, job.loose, job.length = ready["at"], ready["shift"], ready["loose"], source.count("\n") + 1
    more = {"_rx_names": NAMES} if "hasattr" in ready["needs"] else {}
    return job.start(ready["code"] + TAIL.format(call=call), given, more)


def resume(state: bytes, answer, host=None, *, decider=None) -> Outcome:
    """Carry on a run that asked, with the answer."""
    try:
        if not isinstance(state, bytes) or not state.startswith(MARK):
            raise ValueError("not a frozen run")
        head, _, snapshot = state[len(MARK):].partition(b"\n")
        meta = json.loads(head)
        limits = {name: meta["limits"][name] for name in LIMITS}
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in limits.values()):
            raise ValueError("limits")
        job = _Run(host, decider, bool(meta["dry"]), limits, float(meta["min_confidence"]))
        job.effects, job.planned = list(meta["effects"]), list(meta["planned"])
        job.printed, job.spent = str(meta["output"]), int(meta["ms"])
        job.shift, job.length, pending = int(meta.get("shift", 0)), int(meta.get("length", 0)), meta["pending"]
        job.at = int(meta.get("at", 0))
        job.loose = [str(name) for name in meta.get("loose", [])]
        if pending["verb"] not in ("ask", "up") or not isinstance(pending["asks"], dict):
            raise ValueError("no question")
        if pending["verb"] == "ask":
            pending["options"], pending["unsure"], pending["sure"], pending["items"]
            pending["many"], pending["yes_no"]
        return job.carry_on(snapshot, pending, answer, state)
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        return Outcome("stopped", reason=f"this run cannot be carried on: its saved state is damaged ({exc})")


def started_in(state: bytes) -> str | None:
    """The folder of the host a frozen run was started with: where its answer must carry it on."""
    try:
        return json.loads(state[len(MARK):].partition(b"\n")[0]).get("cwd") or None
    except (ValueError, TypeError, AttributeError):
        return None


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


_zone_found = None


def _zone():
    """This machine's time zone, for the sandbox's clock: by name when the system gives one the sandbox knows
    (the daylight-saving rules come with it), else today's offset from UTC."""
    global _zone_found
    if _zone_found is None:
        now = datetime.datetime.now().astimezone()
        fixed = {"offset_seconds": int(now.utcoffset().total_seconds()), "name": now.tzname() or "local"}
        name = os.environ.get("TZ", "").lstrip(":")
        if not name:
            link = os.path.realpath("/etc/localtime")
            name = link.split("/zoneinfo/", 1)[1] if "/zoneinfo/" in link else ""
        _zone_found = fixed
        if name:
            try:
                with _sandbox().checkout(os_policy={"timezone": name}) as session:
                    session.feed_start("0")
                _zone_found = name
            except Exception:  # a name the sandbox does not know
                pass
    return _zone_found


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
        self.shift, self.at = 0, 0  # lines the engine put among the author's, and after which of them
        self.length = 0  # lines of the author's source
        self.loose = []  # methods the source hands over without calling them

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
        limits = monty.ResourceLimits(max_feed_duration_secs=self.limits["cpu"], max_memory=256_000_000,
                                      max_suspensions=100_000)  # the run's own budgets are the ones that count
        return _sandbox().checkout(external_modules=modules, limits=limits, script_name="reflex.py",
                                   os_policy={"timezone": _zone()})

    def start(self, code: str, inputs: dict, more: dict | None = None) -> Outcome:
        try:
            with self.session() as session:
                folder = getattr(self.host, "cwd", None)  # os.getcwd() and os.path.abspath() answer as the host
                where = {"cwd": folder} if isinstance(folder, str) and folder.startswith("/") else {}
                snap = session.feed_start(code, inputs={"__reflex_inputs__": inputs, **(more or {})},
                                          print_callback=self.heard, **where)
                return self.drive(snap)
        except monty.MontyError as exc:  # the sandbox itself dies of a Ctrl-C sent to the whole group
            return self.stopped(INTERRUPTED if CUT.is_set() else self.said(exc))
        except KeyboardInterrupt:
            self.halt()
            return self.stopped(INTERRUPTED)

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
            return self.stopped(INTERRUPTED if CUT.is_set() else self.said(exc))
        except KeyboardInterrupt:
            self.halt()
            return self.stopped(INTERRUPTED)

    def drive(self, snap) -> Outcome:
        try:
            return self.driven(snap)
        except monty.MontyError:
            raise
        except KeyboardInterrupt:  # cut by its caller: the run ends here, and says what it had done
            self.halt()
            return self.stopped(INTERRUPTED)
        except Exception as exc:  # a fault of the engine or of a host: the run stops, and its journal is kept
            return self.stopped(f"the engine failed ({type(exc).__name__}: {exc}): what the journal lists was done")

    def halt(self):
        """Stop what the host still runs for this run, if the host can."""
        stop = getattr(self.host, "stop", None)
        if stop:
            try:
                stop()
            except Exception:
                pass

    def driven(self, snap) -> Outcome:
        while not isinstance(snap, monty.MontyComplete):
            if CUT.is_set():  # asked to stop: here, between two steps, where nothing is half read
                self.halt()
                return self.stopped(INTERRUPTED)
            if self.ms() > self.limits["seconds"] * 1000:
                return self.stopped(f"budget: more than {self.limits['seconds']} s")
            name = getattr(snap, "function_name", None) or ""
            if getattr(snap, "is_os_function", False):
                try:
                    answer = self.system(name, snap.args)
                except _Stop as stop:
                    return self.stopped(str(stop))
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
                                   "shift": self.shift, "at": self.at, "length": self.length,
                                   "loose": self.loose,
                                   "cwd": getattr(self.host, "cwd", None),
                                   "effects": self.effects, "planned": self.planned, "output": self.printed,
                                   "ms": self.ms(), "pending": {**pause.pending, "asks": pause.asks}}, default=str)
                return self.outcome("asks", asks=pause.asks, state=MARK + head.encode() + b"\n" + snap.dump())
            except _Stop as stop:
                return self.stopped(str(stop))
            snap = snap.resume({"return_value": value})
        if CUT.is_set():  # the reflex may have gone on to its end past the cut: it is not "done"
            return self.stopped(INTERRUPTED)
        result = _fields(snap.output)
        if result is None and self.printed.strip():
            result = self.printed.strip()  # a reflex that prints its answer instead of returning it
        return self.outcome("done", result=result)

    def system(self, name: str, args) -> dict | None:
        """What the sandbox asks of the system: the environment its commands run in and a sleep are given, files and
        the modules that reach outside are refused with what to use instead. None: the sandbox's own answer stands."""
        if name in ("os.getenv", "os.environ"):
            given = getattr(self.host, "environment", None)
            env = dict(given()) if given else {"HOME": self.host.home}
            if name == "os.environ":
                return {"return_value": env}
            return {"return_value": env.get(str(args[0]), args[1] if len(args) > 1 else None)}
        if name == "system.sleep":
            asked, left = float(args[0]), self.limits["seconds"] - self.ms() / 1000
            time.sleep(max(0.0, min(asked, left)))  # a sleep spends the run's time like anything else
            if asked > left:
                raise _Stop(f"budget: more than {self.limits['seconds']} s")
            return {"return_value": None}
        if name == "import" and str(args[0]).split(".")[0] in NO_MODULE:
            root = str(args[0]).split(".")[0]
            return {"exception": ModuleNotFoundError(f"no {root} in the sandbox: {NO_MODULE[root]}")}
        if name == "open" or name.startswith("Path."):
            return {"exception": PermissionError(NO_FILES)}
        return None

    def heard(self, stream, text):
        self.printed = (self.printed + text)[-20_000:]

    def mine(self, line: int) -> int:
        """The author's line for a line of the code that ran; 0 for a line of the engine's."""
        if self.at < line <= self.at + self.shift:
            return 0
        own = line - self.shift if line > self.at else line
        return own if not self.length or own <= self.length else 0

    def said(self, exc) -> str:
        """Why the reflex stopped, with its line."""
        text = str(exc)
        if isinstance(exc, monty.MontySyntaxError):
            return f"not valid Python: {text}"
        try:
            every = list(exc.traceback())
        except Exception:  # an error with no frames to show
            every = []
        own = [f for f in every if self.mine(f.line)]  # the author's lines, not the engine's
        if every and not self.mine(every[-1].line) and own and own[-1].function_name == "<lambda>":
            own = own[:-1]  # raised by the engine's own helper, from the lambda it put there: the call is the place
        frames = [f for f in own if f.function_name != "<module>"] or own[-1:]  # in a function, else outside any
        line = f" (line {self.mine(frames[-1].line)} of the reflex)" if frames else ""
        hint = next((hint for said, hint in HINTS if said in text), "")
        taken = NO_VALUE.search(text)
        if taken and hasattr(getattr(builtins, taken[1], None), taken[2]):
            hint = (f"{taken[2]} is handed over without being called, and the sandbox has no such value: write "
                    f"lambda x: x.{taken[2]}(...)" if taken[2] in self.loose else
                    f"the sandbox has no {taken[1]}.{taken[2]}: write that step another way")
        return f"{text}{line}" + (f". {hint[0].upper()}{hint[1:]}" if hint else "")

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
        if isinstance(more.get("error"), str):
            more["error"] = _short(more["error"], NOTED)
        entry = {"verb": verb, "what": _short(what, NOTED), "ms": int((time.monotonic() - began) * 1000), **more}
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

        as_read = getattr(self.host, "as_read", None)  # a host may run a read in a narrower form (curl kept to HTTP)

        def one(argv):
            started = time.monotonic()
            left = deadline - started
            if left <= 0:
                return argv, "", NOT_STARTED, 124, started
            out, err, code = self.ran(as_read(argv) if as_read else argv, min(seconds, max(1.0, left)), input)
            return argv, out, err, code, started
        workers = min(int(getattr(self.host, "workers", 8)), len(commands))  # a host may take one command at a time
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                try:
                    results = list(pool.map(one, commands))
                except BaseException:  # interrupted while they run: none is left running behind the run
                    self.halt()
                    raise
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
            self.planned.append(_short(shlex.join(argv), NOTED))
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
        what = f"{'append to' if append else 'write'} {_short(full, NOTED)} ({len(text)} characters)"
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
        if len(items) > ASKED:
            raise _Raise(ValueError(f"rx.ask: {len(items)} things at once, and one question takes {ASKED}: ask in "
                                    "smaller lists"))

        def context_of(item):
            # {item: text} gives each item of a list its own; anything else is the material of every question
            found = context.get(item, "") if many and isinstance(context, dict) else (context or "")
            return found if isinstance(found, str) else json.dumps(found, default=str, ensure_ascii=False)
        began, answers, by = time.monotonic(), None, "nobody"
        if self.decider is not None:
            try:
                answers = self.decided(question, choices, [(_key(i), context_of(i)) for i in items])
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
        if len(unsure) > DOUBTS:
            raise _Stop(f"rx.ask: {len(unsure)} things are in doubt, more than one question can send up ({DOUBTS}): "
                        "ask about fewer things at a time" + ("" if self.decider else ", or set up a small model"))
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

    def decided(self, question, choices, items) -> list:
        """The small model's answers, waited for no longer than the run has left."""
        box = {}

        wait = max(0.0, (self.limits["seconds"] - self.ms() / 1000) / 2)  # the other half is for the rest
        until = time.monotonic() + wait
        try:  # a small model that takes a deadline stops asking once we stopped waiting
            more = {"until": until} if "until" in inspect.signature(self.decider).parameters else {}
        except (TypeError, ValueError):
            more = {}

        def work():
            try:
                box["answers"] = list(self.decider(question, choices, items, **more))
            except Exception as exc:
                box["error"] = exc
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive() and time.monotonic() < until and not CUT.is_set():
            worker.join(0.1)
        if "error" in box:
            raise box["error"]
        if "answers" not in box:
            raise TimeoutError("no answer in half the time the run had left")
        return box["answers"]

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


def _fields(value):
    """A result as plain data: an object of one of the reflex's own classes becomes the dict of its fields."""
    try:
        if isinstance(value, monty.MontyClassProxy):
            return {str(name): _fields(field) for name, field in value.attributes.items()}
        if isinstance(value, dict):
            return {key: _fields(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(_fields(item) for item in value)
    except (RecursionError, TypeError, AttributeError):  # nested too deep, or a shape of its own: as it is
        pass
    return value


def _cut(text: str) -> str:
    if len(text) > OUTPUT:
        raise _Raise(RuntimeError(f"an output of {len(text)} characters is too much to hand over: narrow the read"))
    return text


def _short(text: str, most: int = SHOWN) -> str:
    text = (text or "").strip()
    return text if len(text) <= most else text[:most] + f"… (+{len(text) - most})"
