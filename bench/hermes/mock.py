"""A fake model for the Hermes bench: it speaks the chat-completions format and plays a script.

    python bench/hermes/mock.py <port> <script.json> <log.jsonl>

The script is a list of steps, one per answer of the model. A step is {"say": text} (the final answer) or
{"calls": [{"name": tool, "arguments": {...}}, ...]}. A call may give several forms under "any": the first whose tool
Hermes offers is made (a reflex is reached through its own tool, through `tool_call` when Hermes keeps that tool
behind its search, or through the terminal). Past the end of the script the model says "done".

It proves the bench end to end without a key, and it notes what each request carried: how many messages, which
tools, how long the system prompt and the tool list are. That last number is what a way of giving reflexes costs
before the first word.
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, SCRIPT, LOG = int(sys.argv[1]), json.load(open(sys.argv[2])), sys.argv[3]


def step_for(request: dict) -> dict:
    offered = {t["function"]["name"] for t in request.get("tools") or []}
    if not offered:  # a side request (a title for the session): no tools, no script
        return {"say": "ok"}
    done = sum(1 for m in request.get("messages", []) if m.get("role") == "assistant")
    step = SCRIPT[done] if done < len(SCRIPT) else {"say": "done"}
    if "calls" not in step:
        return step
    calls = []
    for call in step["calls"]:
        forms = call.get("any") or [call]
        chosen = next((f for f in forms if f["name"] in offered), None)
        if chosen is None:
            return {"say": f"none of {[f['name'] for f in forms]} is offered"}
        calls.append(chosen)
    said = " ".join(str(m.get("content")) for m in request.get("messages", []) if m.get("role") == "tool")
    found = re.findall(r"answer ([0-9a-f]{6})", said)  # $ID in a step is the id of the run that last asked
    return json.loads(json.dumps({"calls": calls}).replace("$ID", found[-1] if found else "$ID"))


def _around(text: str) -> str:
    """Where the model first reads of reflexes, if it does: the 400 characters around the word."""
    at = text.lower().find("reflex")
    return "" if at < 0 else text[max(0, at - 150):at + 250]


class Model(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body: bytes, kind: str):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(json.dumps({"object": "list", "data": [{"id": "mock-1", "object": "model"}]}).encode(),
                   "application/json")

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        messages, tools = request.get("messages", []), request.get("tools") or []
        system = " ".join(str(m.get("content")) for m in messages if m.get("role") == "system")
        step = step_for(request)
        with open(LOG, "a", encoding="utf-8") as log:
            log.write(json.dumps({
                "messages": len(messages), "tools": sorted(t["function"]["name"] for t in tools),
                "system_chars": len(system), "tools_chars": len(json.dumps(tools)),
                "reflex_in_system": _around(system), "reflex_in_tools": _around(json.dumps(tools)),
                "listed": re.findall(r"(?m)^  ([a-z0-9][a-z0-9_-]*)\(.*\) · ", system),  # the reflexes the prompt names
                "step": step}) + "\n")
        if "calls" in step:
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"call_{len(messages)}_{at}", "type": "function", "index": at,
                 "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments", {}))}}
                for at, call in enumerate(step["calls"])]}
            finish = "tool_calls"
        else:
            message, finish = {"role": "assistant", "content": step["say"]}, "stop"
        usage = {"prompt_tokens": (len(system) + len(json.dumps(tools))) // 4, "completion_tokens": 20,
                 "total_tokens": (len(system) + len(json.dumps(tools))) // 4 + 20}
        if request.get("stream"):
            first = {"id": "mock", "object": "chat.completion.chunk", "model": "mock-1",
                     "choices": [{"index": 0, "delta": message, "finish_reason": None}]}
            last = {"id": "mock", "object": "chat.completion.chunk", "model": "mock-1",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage}
            self.reply(f"data: {json.dumps(first)}\n\ndata: {json.dumps(last)}\n\ndata: [DONE]\n\n".encode(),
                       "text/event-stream")
        else:
            self.reply(json.dumps({"id": "mock", "object": "chat.completion", "model": "mock-1", "usage": usage,
                                   "choices": [{"index": 0, "message": message, "finish_reason": finish}]}).encode(),
                       "application/json")


ThreadingHTTPServer(("127.0.0.1", PORT), Model).serve_forever()
