"""The small model behind rx.ask: a closed question in, one option and a confidence out.

A decider is any callable (question, options, items) -> [(answer, confidence), ...], one pair per item, where an item
is (text, context). The engine keeps the answers at or above its confidence bar and sends the rest up to the big
model, so a reflex also works with no decider at all: every question then goes up.

Typed is the one built in: the decision models that share TypeSafe Jev's request format (Jev itself, Cloudflare Clef).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

JEV = "https://api.typesafe.ai/v1/systemone"
PER_CALL = 50  # questions in one request
SECONDS = 8.0
STATE = 32_000  # characters of context sent with one question


class NoAnswer(Exception):
    """The small model could not answer: the engine sends the question up."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # a redirect would carry the key to another host
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


_OPEN = urllib.request.build_opener(_NoRedirect).open


class Typed:
    """A decision model that speaks Jev's format: state + typed questions -> probabilities."""

    def __init__(self, key: str, url: str = JEV, model: str = "jev-latest", seconds: float = SECONDS, opener=_OPEN):
        self.key, self.url, self.model, self.seconds, self.opener = key, url, model, seconds, opener
        self.name, self.until = model, None

    def __call__(self, question: str, options: list, items: list, until: float | None = None) -> list:
        """until: the moment (time.monotonic) after which nobody waits for the answers: no request is sent then."""
        self.until = until
        yes_no = list(options) == ["yes", "no"]
        with_context = [at for at, (_, context) in enumerate(items) if context]
        answers = [None] * len(items)
        shared = [at for at in range(len(items)) if at not in with_context]
        for start in range(0, len(shared), PER_CALL):  # items without a context of their own share one request
            chunk = shared[start:start + PER_CALL]
            texts = [f"{question}\n{items[at][0]}".strip() for at in chunk]
            for at, answer in zip(chunk, self._request("(none)", texts, options, yes_no)):
                answers[at] = answer
        if with_context:  # an item with its own context is asked alone, about that context
            def one(at):
                text, context = items[at]
                return self._request(context[:STATE], [f"{question}\n{text}".strip()], options, yes_no)[0]
            with ThreadPoolExecutor(max_workers=min(8, len(with_context))) as pool:
                for at, answer in zip(with_context, pool.map(one, with_context)):
                    answers[at] = answer
        return answers

    def _request(self, state: str, questions: list, options: list, yes_no: bool) -> list:
        if yes_no:
            specs = {f"q{i}": {"type": "noul", "instructions": q} for i, q in enumerate(questions)}
        else:
            specs = {f"q{i}": {"type": "choice", "instructions": q, "criteria": {o: o for o in options}}
                     for i, q in enumerate(questions)}
        reply = self._post({"model": self.model, "state": state or "(none)", "questions": specs}).get("answers")
        if not isinstance(reply, dict):
            raise NoAnswer("no answers in the reply")
        found = []
        for i in range(len(questions)):
            try:
                one = reply[f"q{i}"]
                if yes_no:
                    p = float(one["noul"])
                    found.append(("yes", p) if p >= 0.5 else ("no", 1 - p))
                else:
                    value = str(one["choice"])
                    odds = one.get("probabilities") or {}
                    found.append((value, float(odds.get(value, one.get("confidence", 0)))))
            except (KeyError, TypeError, ValueError):
                raise NoAnswer("an answer in an unexpected shape") from None
        return found

    def _post(self, body: dict) -> dict:
        deadline = min(time.monotonic() + self.seconds, self.until or float("inf"))
        if deadline <= time.monotonic():
            raise NoAnswer("out of time")
        data = json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        for attempt in (1, 2):
            request = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
            try:
                with self.opener(request, timeout=max(0.1, deadline - time.monotonic())) as response:
                    reply = json.loads(response.read())
                if not isinstance(reply, dict):
                    raise NoAnswer("the reply is not a JSON object")
                return reply
            except urllib.error.HTTPError as exc:
                if attempt == 1 and (exc.code == 429 or exc.code >= 500) and deadline - time.monotonic() > 1:
                    time.sleep(0.3)
                    continue
                raise NoAnswer(f"HTTP {exc.code}") from None
            except (OSError, ValueError) as exc:  # timeouts, DNS, cut or bad JSON
                raise NoAnswer(type(exc).__name__) from None
        raise NoAnswer("no answer")


def from_env(env=os.environ):
    """The decider this machine is set up for, or None: TYPESAFE_API_KEY (Jev), or REFLEX_DECIDER_URL with
    REFLEX_DECIDER_KEY and REFLEX_DECIDER_MODEL for another model of the same format."""
    url, key = env.get("REFLEX_DECIDER_URL"), env.get("REFLEX_DECIDER_KEY") or env.get("TYPESAFE_API_KEY")
    if not key:
        return None
    return Typed(key, url or JEV, env.get("REFLEX_DECIDER_MODEL") or "jev-latest")
