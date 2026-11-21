"""The command line: reflex list | show | run | test | answer.

What it prints is written for the model that called it: the answer first, then only what it needs to trust it or to
carry on. Exit code: 0 done, 3 asks (a question waits), 1 stopped, 2 a call that could not start.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import decide, engine, store
from .host import Local

USAGE = """reflex list                           the reflexes here, one per line
reflex show <name>                    one reflex's source
reflex run <name|file.py> [k=v ...]   run it
reflex test <name|file.py> [k=v ...]  dry run: reads and questions happen, changes are only listed
reflex answer <id> <answer>           carry on a run that asked
Add --json to run, test and answer for the whole result as JSON."""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="reflex", usage=USAGE, add_help=True)
    parser.add_argument("command", choices=["list", "show", "run", "test", "answer"])
    parser.add_argument("target", nargs="?")
    parser.add_argument("rest", nargs="*")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "list":
        return _list()
    if not args.target:
        return _fail(f"reflex {args.command} needs a {'run id' if args.command == 'answer' else 'reflex name'}")
    if args.command == "answer":
        return _answer(args.target, " ".join(args.rest), args.json)
    path = store.find(args.target)
    if path is None:
        names = ", ".join(store.everything()) or "none yet"
        return _fail(f"no reflex named {args.target} (here: {names})")
    source = path.read_text(encoding="utf-8")
    if args.command == "show":
        print(source.rstrip())
        return 0
    try:
        inputs = _inputs(args.rest)
    except ValueError as exc:
        return _fail(str(exc))
    outcome = engine.run(source, inputs, Local(), dry=args.command == "test", decider=decide.from_env())
    return _report(outcome, args.json)


def _list() -> int:
    found = store.everything()
    if not found:
        print("no reflex yet (looked in: " + ", ".join(str(f) for f in store.folders()) + ")")
        return 0
    for name, path in found.items():
        try:
            about = engine.describe(path.read_text(encoding="utf-8"))
            inputs = ", ".join(i["name"] if i["required"] else f"{i['name']}={json.dumps(i['default'])}"
                               for i in about["inputs"])
            print(f"{name}({inputs}) · {about['doc'].splitlines()[0] if about['doc'] else 'no description'}")
        except (engine.Refused, OSError) as exc:
            print(f"{name} · cannot run: {exc}")
    return 0


def _inputs(pairs: list) -> dict:
    inputs = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not name.isidentifier():
            raise ValueError(f"inputs are written name=value, not {pair!r}")
        inputs[name] = _value(value)
    return inputs


def _value(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text


def _answer(run_id: str, text: str, as_json: bool) -> int:
    state = store.kept(run_id)
    if state is None:
        return _fail(f"no run {run_id} waits for an answer (a question is kept one day, and answered once)")
    if not text.strip():
        return _fail("give the answer: reflex answer <id> '<answer>'")
    outcome = engine.resume(state, _value(text), Local(), decider=decide.from_env())
    if not (outcome.status == "asks" and outcome.state == state):  # anything but the same question again
        store.drop(run_id)
    return _report(outcome, as_json, run_id if outcome.state == state else None)


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def _report(outcome, as_json: bool, run_id: str | None = None) -> int:
    if outcome.status == "asks" and run_id is None:
        run_id = store.keep(outcome.state)
    if as_json:
        data = outcome.to_dict()
        if run_id:
            data["answer_with"] = f"reflex answer {run_id} '<answer>'"
        print(json.dumps(data, ensure_ascii=False, default=str))
    else:
        print(_tilde(render(outcome, run_id)))
    return {"done": 0, "asks": 3}.get(outcome.status, 1)


def _tilde(text: str) -> str:
    """The home folder written ~ again: shorter to read, and the same on every machine."""
    home = Local().home.rstrip("/")
    return text.replace(home + "/", "~/") if len(home) > 1 else text


def render(outcome, run_id: str | None = None) -> str:
    lines = []
    if outcome.status == "done":
        if outcome.dry and outcome.planned:
            lines.append("dry run, nothing was changed. It would:")
            lines += [f"  {step}" for step in outcome.planned]
            lines.append(f"then end with: {_shown(outcome.result)}")
        elif outcome.dry:
            lines.append(f"dry run (it changes nothing): {_shown(outcome.result)}")
        else:
            lines.append(f"done: {_shown(outcome.result)}")
        lines.append(_counts(outcome))
    elif outcome.status == "asks":
        asks = dict(outcome.asks or {})
        lines.append(f"asks: {asks.pop('question', '')}")
        error, options, about = asks.pop("error", None), asks.pop("options", None), asks.pop("about", None)
        if error:
            lines.append(f"(the last answer did not fit: {error})")
        if options:
            lines.append(f"options: {', '.join(options)}")
        for entry in about or []:
            context = " ".join(str(entry.get("context", "")).split())
            lines.append(f"- {entry['item']}" + (f"  [{context[:300]}]" if context else ""))
        if asks:
            lines.append(json.dumps(asks, ensure_ascii=False, default=str))
        if about and len(about) > 1:
            form = "{" + ", ".join(f'"{e["item"]}": "…"' for e in about[:2]) + (", …}" if len(about) > 2 else "}")
        else:
            form = "<one option>" if options else "<your answer>"
        lines.append(f"answer with: reflex answer {run_id} '{form}'")
        if outcome.dry:
            lines.append("(dry run: nothing was changed so far)")
    else:
        lines.append(f"stopped: {outcome.reason}")
        changed = [e["what"] for e in outcome.effects if e["verb"] in ("do", "write") and not e.get("planned")
                   and not e.get("error")]
        if changed:
            lines.append("already changed before it stopped:")
            lines += [f"  {what}" for what in changed]
        elif outcome.effects:
            lines.append("nothing was changed before it stopped.")
    return "\n".join(lines)


def _shown(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and isinstance(result.get("message"), str):
        rest = {k: v for k, v in result.items() if k != "message"}
        return result["message"] + ("\n" + json.dumps(rest, ensure_ascii=False, default=str) if rest else "")
    return json.dumps(result, ensure_ascii=False, default=str)


def _counts(outcome) -> str:
    verbs = [e["verb"] for e in outcome.effects]
    reads = verbs.count("sh") + verbs.count("read")
    changes = sum(1 for e in outcome.effects if e["verb"] in ("do", "write"))
    questions = verbs.count("ask") + verbs.count("up")
    parts = [f"{reads} read{'s' if reads != 1 else ''}",
             f"{changes} change{'s' if changes != 1 else ''}{' planned' if outcome.dry and changes else ''}"]
    if questions:
        parts.append(f"{questions} question{'s' if questions != 1 else ''}")
    return f"({', '.join(parts)} · {outcome.ms / 1000:.1f} s)"


if __name__ == "__main__":
    sys.exit(main())
