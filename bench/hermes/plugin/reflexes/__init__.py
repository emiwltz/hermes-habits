"""Reflexes as one Hermes tool.

The thinnest host there can be: the tool calls the `reflex` command (REFLEX_COMMAND, or `reflex` on the PATH), so a
reflex's commands run on this machine with the command's own local host. A host that runs them through Hermes's
terminal tool, and so through its backends and approvals, comes next; the tool the model sees stays the same.

The tool takes its inputs as values, not as one command line: an answer that is a JSON object needs no quoting.
"""

import json
import os
import subprocess

HEAD = """Reflexes: small programs for tasks done before. One call does the whole task, with no thinking in between.
do="run": run one. It ends "done" (use its result as it is), "asks" (it needs one answer from you: do="answer" with
the id it gives, and it carries on) or "stopped" (it says why and what it already changed; do the rest yourself).
do="test": dry run, shows what it would change and changes nothing. do="guide": how to write one.
When a request matches a reflex, run it instead of doing the steps by hand."""
SCHEMA = {
    "name": "reflex",
    "parameters": {
        "type": "object",
        "properties": {
            "do": {"type": "string", "enum": ["run", "test", "answer", "list", "show", "guide"]},
            "name": {"type": "string", "description": "the reflex (run, test, show), or the id of the run (answer)"},
            "inputs": {"type": "object", "description": "the reflex's inputs, by name (run, test)"},
            "answer": {"description": "the answer (answer): one option, or {item: option} when it asks about several"},
        },
        "required": ["do"],
    },
}


def _command() -> str:
    return os.environ.get("REFLEX_COMMAND") or "reflex"


def _reflex(words: list, said: str | None = None) -> dict:
    try:
        done = subprocess.run([_command(), *words], input=said, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"exit": 127, "output": f"the reflex command did not run: {exc}"}
    return {"exit": done.returncode, "output": (done.stdout + done.stderr).strip()[-20000:]}


def _handler(args, **more):
    do, name = str(args.get("do") or ""), str(args.get("name") or "")
    if do in ("list", "guide"):
        return json.dumps(_reflex([do]))
    if not name:
        return json.dumps({"exit": 2, "output": f"do={do} needs name"})
    if do == "show":
        return json.dumps(_reflex(["show", name]))
    if do == "answer":
        answer = args.get("answer")
        return json.dumps(_reflex(["answer", name, "-"], answer if isinstance(answer, str) else json.dumps(answer)))
    if do in ("run", "test"):
        inputs = args.get("inputs") if isinstance(args.get("inputs"), dict) else {}
        pairs = [f"{key}={value if isinstance(value, str) else json.dumps(value)}" for key, value in inputs.items()]
        return json.dumps(_reflex([do, name, *pairs]))
    return json.dumps({"exit": 2, "output": "do is one of run, test, answer, list, show, guide"})


def register(ctx):
    listing = _reflex(["list"])["output"]
    schema = {**SCHEMA, "description": HEAD + "\nYours:\n" + "\n".join("  " + line for line in listing.splitlines())}
    ctx.register_tool(name="reflex", toolset="reflexes", schema=schema, handler=_handler)
