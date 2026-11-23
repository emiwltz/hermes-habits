"""Notes what a Hermes session does, one JSON line per event, into the file named by REFLEX_BENCH_LOG.

It only watches: every hook here is an observer, and a failure to write never reaches the agent. The lines hold the
commands the agent ran and the sizes of what came back, not the outputs themselves: they stay on the machine.
"""

import json
import os
import time

KEPT = 600  # characters of one argument kept in the log


def _note(event: str, **fields):
    path = os.environ.get("REFLEX_BENCH_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as log:
            log.write(json.dumps({"t": round(time.time(), 3), "event": event, **fields}, default=str) + "\n")
    except OSError:
        pass


def _short(value):
    if isinstance(value, dict):
        return {str(k): _short(v) for k, v in list(value.items())[:20]}
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= KEPT else text[:KEPT] + f"… (+{len(text) - KEPT})"


def _tool(tool_name="", args=None, result="", task_id="", duration_ms=0, **more):
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    _note("tool", name=tool_name, args=_short(args or {}), ms=duration_ms, chars=len(text))


def _api(model="", provider="", api_duration=None, finish_reason=None, message_count=None, usage=None,
         assistant_message=None, assistant_content_chars=None, api_call_count=None, **more):
    calls = []
    for call in getattr(assistant_message, "tool_calls", None) or []:
        function = getattr(call, "function", None)
        calls.append(getattr(function, "name", None) or (call.get("function", {}).get("name") if isinstance(call, dict)
                                                         else None))
    _note("api", model=model, provider=provider, seconds=api_duration, finish=finish_reason, messages=message_count,
          usage=usage or {}, said=assistant_content_chars, calls=calls, nth=api_call_count)


def _start(session_id="", model="", platform="", **more):
    _note("start", session=session_id, model=model, platform=platform)


def _end(session_id="", completed=False, interrupted=False, **more):
    _note("end", session=session_id, completed=completed, interrupted=interrupted)


def register(ctx):
    ctx.register_hook("post_tool_call", _tool)
    ctx.register_hook("post_api_request", _api)
    ctx.register_hook("on_session_start", _start)
    ctx.register_hook("on_session_end", _end)
