"""The command line: reflex list | show | run | test | answer | guide.

What it prints is written for the model that called it: the answer first, then only what it needs to trust it or to
carry on. Exit code: 0 done, 3 asks (a question waits), 1 stopped, 2 the call itself is wrong (no such reflex, no
such run, a word out of place).
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import sys
from pathlib import Path

from . import decide, engine, store
from .host import Local

USAGE = """reflex list                           the reflexes here, one per line
reflex show <name>                    one reflex's source
reflex run <name|file.py> [k=v ...]   run it
reflex test <name|file.py> [k=v ...]  dry run: reads and questions happen, changes are only listed
reflex answer <id> <answer>           carry on a run that asked
reflex guide                          how to write a reflex
Add --json to run, test and answer for the whole result as JSON."""
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
GONE = "the folder you are in no longer exists: a reflex needs a folder to run in (cd somewhere first)"


def main(argv=None) -> int:
    try:
        return _main(list(sys.argv[1:] if argv is None else argv))
    except BrokenPipeError:  # the reader closed early (| head): nothing more to say
        try:
            sys.stdout.close()
        except OSError:
            pass
        return 0


def _main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if args else 2
    command, rest = args[0], args[1:]
    if command == "list":
        return _list()
    if command == "guide":
        print((Path(__file__).parent / "GUIDE.md").read_text(encoding="utf-8").rstrip())
        print("\nA reflex is a file <name>.py in " + " or ".join(str(f) for f in store.folders())
              + f". Try it with `{_me()} test <name>` (a dry run), then `{_me()} run <name>`.")
        return 0
    if command == "answer":
        as_json = rest[-1:] == ["--json"]
        rest = rest[:-1] if as_json else rest
        if not rest:
            return _fail(f"give the run and the answer: {_me()} answer <id> '<answer>'")
        words = rest[2:] if rest[1:2] == ["--"] else rest[1:]  # after --, an answer may start with a dash
        return _answer(rest[0], " ".join(words), as_json)
    if command not in ("show", "run", "test"):
        return _fail(f"reflex has no {command}.\n{USAGE}")
    as_json = "--json" in rest
    rest = [word for word in rest if word != "--json"]
    if not rest:
        return _fail(f"reflex {command} needs the name of a reflex")
    path = store.find(rest[0])
    if path is None:
        names = ", ".join(store.everything()) or "none yet"
        return _fail(f"no reflex named {rest[0]} (here: {names})")
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _fail(f"cannot read {path}: {exc.strerror or exc}")
    if command == "show":
        print(source.rstrip())
        return 0
    try:
        inputs = _inputs(rest[1:], source)
    except ValueError as exc:
        return _fail(str(exc))
    host = _here()
    if host is None:
        return _fail(GONE)
    outcome = engine.run(source, inputs, host, dry=command == "test", decider=decide.from_env())
    return _report(outcome, as_json)


def _list() -> int:
    found, notes = store.survey()
    if not found:
        print("no reflex yet (looked in: " + ", ".join(str(f) for f in store.folders()) + ")")
    for name, path in found.items():
        try:
            about = engine.describe(path.read_text(encoding="utf-8", errors="replace"))
            inputs = ", ".join(i["name"] if i["required"] else
                               f"{i['name']}={i['default'] if i.get('written') else _default(i['default'])}"
                               for i in about["inputs"])
            print(f"{name}({inputs}) · {_line(about['doc'].splitlines()[0]) if about['doc'] else 'no description'}")
        except (engine.Refused, OSError) as exc:
            print(f"{name} · cannot run: {exc}")
    for note in notes:
        print(f"note: {note}")
    return 0


def _default(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def _inputs(pairs: list, source: str) -> dict:
    """name=value pairs. A value is read as JSON when it is JSON (a number, true, a list), else as the text it is;
    an input whose default is a text always stays a text."""
    try:
        texts = {i["name"] for i in engine.describe(source)["inputs"]
                 if isinstance(i.get("default"), str) and not i.get("written")}
    except engine.Refused:
        texts = set()  # the run itself will say what is wrong with the reflex
    inputs = {}
    for pair in pairs:
        name, equals, value = pair.partition("=")
        if not equals or not name.isidentifier():
            raise ValueError(f"inputs are written name=value, not {pair!r}")
        inputs[name] = value if name in texts else _value(value)
    return inputs


def _value(text: str):
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return text


def _answer_value(text: str):
    """An answer as it was typed: a JSON object, list or quoted text is read as such, anything else stays the text it
    is (so the options true, 1.10 or null can be answered by typing them)."""
    text = text.strip()
    if text[:1] in ("{", "[", '"'):
        try:
            return json.loads(text)
        except (ValueError, RecursionError):
            pass
    return text


def _answer(run_id: str, text: str, as_json: bool) -> int:
    if not text.strip():
        return _fail(f"give the answer: {_me()} answer {run_id} '<answer>'")
    answer, decider = _answer_value(text), decide.from_env()
    state = store.take(run_id)  # taken out of the store: an answer is given once, by one caller
    if state is None:
        return _fail(f"no run {run_id} waits for an answer (a question is kept one day, and answered once)")
    try:
        folder = engine.started_in(state)  # the run carries on where it started, wherever the answer is typed
        host = Local(cwd=folder) if folder and os.path.isdir(folder) else None
        if host is None:
            store.put_back(run_id, state)
            return _fail(f"the folder this run was started in is gone ({folder}): it cannot carry on"
                         if folder else GONE)
        outcome = engine.resume(state, answer, host, decider=decider)
    except BaseException:  # whatever happens here, the question is not lost
        store.put_back(run_id, state)
        raise
    if outcome.status == "asks" and outcome.state == state and store.put_back(run_id, state):
        return _report(outcome, as_json, run_id)  # the answer did not fit: the same question, under the same id
    return _report(outcome, as_json)


def _here():
    """The local host in the folder we are in; None when that folder is gone."""
    try:
        return Local()
    except OSError:
        return None


def _me() -> str:
    """How this command is called here, for the hints it prints (a host that wraps it says so in REFLEX_COMMAND)."""
    return os.environ.get("REFLEX_COMMAND") or "reflex"


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _report(outcome, as_json: bool, run_id: str | None = None) -> int:
    lost = ""
    if outcome.status == "asks" and run_id is None:
        try:
            run_id = store.keep(outcome.state)
        except OSError as exc:  # the run happened, and cannot wait: say so, and what it already did
            lost = f"its question cannot be answered: the run could not be kept ({exc.strerror or exc})"
    if as_json:
        data = _plain(outcome.to_dict())
        if run_id:
            data["answer_with"] = f"{_me()} answer {run_id} '<answer>'"
        if lost:
            data["lost"] = lost
        print(json.dumps(data, ensure_ascii=False, default=str))
    else:
        print(_tilde(render(outcome, run_id)) + (f"\n{lost}" if lost else ""))
    return 1 if lost else {"done": 0, "asks": 3}.get(outcome.status, 1)


def _plain(value):
    """A value JSON can carry: a set as a list, bytes and odd numbers as text."""
    try:
        if isinstance(value, dict):
            return {str(k): _plain(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_plain(v) for v in value]
        if isinstance(value, (set, frozenset)):
            return sorted((_plain(v) for v in value), key=str)
    except RecursionError:  # nested deeper than JSON can say
        return "(a value nested too deep to show)"
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _tilde(text: str) -> str:
    """The home folder written ~ again where it starts a path: shorter to read, the same on every machine."""
    home = os.path.expanduser("~").rstrip("/")
    if len(home) < 2:
        return text
    return re.sub(r"(?<![\w./~-])" + re.escape(home) + r"(?=/|[\s'\":,)\]]|$)", "~", text)


def _line(text) -> str:
    """A text that stays on its line, whatever it holds (a file name can hold a line break)."""
    return CONTROL.sub(lambda found: repr(found.group())[1:-1], str(text))


def _changed(outcome) -> tuple:
    """(what the run changed, what may have partly changed: a change that failed or timed out)."""
    acts = [e for e in outcome.effects if e["verb"] in ("do", "write") and not e.get("planned")]

    def why(entry) -> str:  # the error without the command, which is already shown
        return _line(entry["error"].removeprefix(f"rx.{entry['verb']} {entry['what']}: "))[:200]
    return ([_line(e["what"]) for e in acts if not e.get("error")],
            [f"{_line(e['what'])}  ({why(e)})" for e in acts if e.get("error")])


def render(outcome, run_id: str | None = None) -> str:
    lines = []
    changed, partly = _changed(outcome)
    if outcome.status == "done":
        if outcome.dry and outcome.planned:
            lines.append("dry run, nothing was changed. It would:")
            lines += [f"  {_line(step)}" for step in outcome.planned]
            lines.append(f"then end with: {_shown(outcome.result)}")
        elif outcome.dry:
            lines.append(f"dry run (it changes nothing): {_shown(outcome.result)}")
        else:
            lines.append(f"done: {_shown(outcome.result)}")
        if partly:  # the reflex went on after a change that failed: the caller must know it may be half made
            lines += [f"  may have partly run: {what}" for what in partly]
        lines.append(_counts(outcome))
    elif outcome.status == "asks":
        asks = dict(outcome.asks or {})
        lines.append(f"asks: {_line(asks.pop('question', ''))}")
        error, options, about = asks.pop("error", None), asks.pop("options", None), asks.pop("about", None)
        if error:
            lines.append(f"(the last answer did not fit: {_line(error)})")
        if options:
            lines.append(f"options: {', '.join(_line(o) for o in options)}")
        for entry in about or []:
            context = " ".join(_line(entry.get("context", "")).split())
            lines.append(f"- {_line(entry['item'])}" + (f"  [{context[:300]}]" if context else ""))
        if asks:
            lines.append(_line(json.dumps(_plain(asks), ensure_ascii=False, default=str)))
        if about and len(about) > 1:
            shown = json.dumps({str(e["item"]): "…" for e in about[:2]}, ensure_ascii=False)
            form = shlex.quote(shown) + ("  (one answer for each item)" if len(about) > 2 else "")
        else:
            form = "<one option>" if options else "'<your answer>'"
        lines.append(f"answer with: {_me()} answer {run_id} {form}" if run_id else "(this question cannot be answered)")
        if outcome.dry:
            lines.append("(dry run: nothing was changed so far)")
        elif changed or partly:
            lines.append("already changed, before this question:")
            lines += [f"  {what}" for what in changed] + [f"  may have partly run: {what}" for what in partly]
    else:
        lines.append(f"stopped: {outcome.reason}")
        if changed or partly:
            lines.append("already changed before it stopped:")
            lines += [f"  {what}" for what in changed] + [f"  may have partly run: {what}" for what in partly]
        elif outcome.effects:
            lines.append("nothing was changed before it stopped.")
    return "\n".join(lines)


def _shown(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and isinstance(result.get("message"), str):
        rest = {k: v for k, v in result.items() if k != "message"}
        return result["message"] + ("\n" + json.dumps(_plain(rest), ensure_ascii=False, default=str) if rest else "")
    return json.dumps(_plain(result), ensure_ascii=False, default=str)


def _counts(outcome) -> str:
    verbs = [e["verb"] for e in outcome.effects]
    reads = verbs.count("sh") + verbs.count("read")
    acts = [e for e in outcome.effects if e["verb"] in ("do", "write")]
    changes, failed = sum(1 for e in acts if not e.get("error")), sum(1 for e in acts if e.get("error"))
    questions = verbs.count("ask") + verbs.count("up")
    parts = [f"{reads} read{'s' if reads != 1 else ''}",
             f"{changes} change{'s' if changes != 1 else ''}{' planned' if outcome.dry and changes else ''}"]
    if failed:
        parts.append(f"{failed} failed")
    if questions:
        parts.append(f"{questions} question{'s' if questions != 1 else ''}")
    return f"({', '.join(parts)} · {outcome.ms / 1000:.1f} s)"


if __name__ == "__main__":
    sys.exit(main())
