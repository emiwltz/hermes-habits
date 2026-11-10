"""Write a habit's diagram page, and the gallery of all habits: self-contained HTML files, no network.

Each file also carries a plain HTML summary, shown where JavaScript does not run
(a phone's file preview, for instance); the interactive view replaces it.
"""

from __future__ import annotations

import html
import json
import time
from pathlib import Path

from . import graph

VIEW = Path(__file__).resolve().parent.parent / "view"
TEMPLATE = VIEW / "template.html"
GALLERY = VIEW / "gallery.html"
TOKEN = "/*HABITS_DATA*/null"
STATIC = "<!--HABITS_STATIC-->"
VERBS = ("ask", "check", "do", "llm", "use", "end", "wake")
# The ledger's states and a run's outcomes in the README's words (the pages' scripts keep the same two maps).
STATES = {"active": "automatic", "shadow": "learning", "retired": "resting", "live": "called by name",
          "one-off": "run once"}
OUTCOMES = {"end": "done", "wake": "handed back"}
KINDS = {"request": "request", "watch": "scheduled"}  # a habit with no trigger: its state says "called by name"


def shown(status) -> str:
    return STATES.get(str(status or ""), str(status or ""))


def head(data: dict) -> str:
    """Its state and its kind in the README's words: "learning · request", "called by name"."""
    if data.get("origin") == "example":  # an example never learns: it runs when called by name
        return "example · called by name"
    kind = KINDS.get(str(data.get("kind") or ""), "")
    return " · ".join(x for x in (shown(data.get("status")), kind) if x)


def blob(data) -> str:
    """JSON for a script: every "<" escaped, so no value can close the script or open a comment."""
    return json.dumps(data, ensure_ascii=False, default=str).replace("<", "\\u003c")


def _seconds(ms) -> str:
    return "—" if ms is None else f"{ms / 1000:.1f} s"


def _tokens(n) -> str:
    n = int(n or 0)
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def plural(n, word: str) -> str:
    """1 wake, 2 wakes, 0 wakes."""
    return f"{n} {word}" + ("" if n == 1 else "s")


def facts(data: dict) -> list[str]:
    """The numbers of a habit in a few short lines: agreement, gain in time and tokens, runs."""
    s, lines = data.get("summary") or {}, []
    if data.get("kind") == "watch":
        lines.append(f"{plural(s.get('runs', 0), 'check')} · {plural(s.get('woke', 0), 'wake')}")
    elif data.get("kind") == "one-off":  # run by name, always live: nothing to compare
        if data.get("origin") != "one-off" and "runs" in s:  # a saved habit, not a scenario run once nor a replay
            lines.append(f"called by name, never compared · {plural(s.get('runs', 0), 'run')}")
    elif s.get("agreement") is not None:
        lines.append(f"{s['agreement']:.0%} agreement over {plural(s.get('compared', 0), 'run')}")
    elif s:
        lines.append(f"{s.get('compared', 0)}/{s.get('promote_runs', 12)} compared runs")
    if s.get("shadow_ms") and s.get("active_ms"):
        lines.append(f"{_seconds(s['active_ms'])} instead of {_seconds(s['shadow_ms'])} per request")
    if s.get("shadow_tokens") and s.get("active_tokens") is not None:
        lines.append(f"{_tokens(s['active_tokens'])} model tokens instead of {_tokens(s['shadow_tokens'])}")
    return lines


def static(data: dict) -> str:
    """The page as plain HTML: what it says without its graph."""
    e = html.escape
    run = data.get("run") or {}
    nodes = (data.get("habit") or {}).get("nodes") or {}
    parts = [f"<h1>{e(str(data.get('name') or 'Habit'))}</h1>",
             f"<p class='desc'>{e(str(data.get('description') or ''))}</p>",
             f"<p><b>{e(head(data))}</b></p>"]
    parts += [f"<p>{e(line)}</p>" for line in (facts(data) if data.get("origin") != "example" else [])]
    if run:
        outcome = OUTCOMES.get(run.get("status"), str(run.get("status", "")))
        parts.append(f"<h2>Last run: {e(outcome)} in {_seconds(run.get('ms'))}</h2>")
        parts.append(f"<p>{e(str(run.get('message') or ''))}</p>")
        steps = "".join(f"<li><b>{e(str(s.get('node', '')))}</b> · {e(str(s.get('summary', '')))}</li>"
                        for s in run.get("steps") or [])
        parts.append(f"<ol>{steps}</ol>" if steps else "")
        if run.get("doubts"):
            parts.append(f"<p>{len(run['doubts'])} doubts left for the model.</p>")
    order, queue = [], [(data.get("habit") or {}).get("start")]
    while queue:  # reading order: from start along the exits, then whatever is left
        name = queue.pop(0)
        if name in nodes and name not in order:
            order.append(name)
            queue += graph.exits(nodes[name]) if isinstance(nodes[name], dict) else []
    rows = []
    for name in order + [n for n in nodes if n not in order]:
        node = nodes[name]
        if isinstance(node, dict):
            verb = next((v for v in VERBS if v in node), "")
            rows.append(f"<li><b>{e(str(name))}</b> · {e(verb)} · {e(str(node.get(verb, ''))[:160])}</li>")
    if rows:
        parts.append(f"<h2>Graph: {len(rows)} nodes</h2><ul>{''.join(rows)}</ul>")
    parts.append("<p class='dim'>Open this file in a browser for the interactive diagram and its replay.</p>")
    return "".join(parts)


def page(data: dict) -> str:
    """The template with the data inlined, and its plain summary."""
    data = {**data, "generated": data.get("generated") or time.strftime("%Y-%m-%d %H:%M")}
    text = TEMPLATE.read_text(encoding="utf-8")
    return text.replace(STATIC, static(data), 1).replace(TOKEN, blob(data), 1)


def gallery(items: list[dict]) -> str:
    """Every habit on one page: a card each, and its full page on a click, from the same file."""
    e = html.escape

    def card(d):  # one short block per habit: a phone screen has no room for a wide table
        lines = "".join(f"<br>{e(line)}" for line in (facts(d) if d.get("origin") != "example" else []))
        return (f"<p><b>{e(str(d.get('name')))}</b> · {e(head(d))}"
                f"<br><span class='dim'>{e(str(d.get('description') or ''))}</span>{lines}</p>")
    mine = [d for d in items if d.get("origin") != "example"]
    examples = [d for d in items if d.get("origin") == "example"]
    plain = (f"<h1>Habits</h1><p>{len(mine)} habits of your own</p>" + "".join(card(d) for d in mine)
             + (f"<h2>Examples shipped with the plugin</h2>" + "".join(card(d) for d in examples) if examples else "")
             + "<p class='dim'>Open this file in a browser for the cards and each habit's diagram.</p>")
    data = {"generated": time.strftime("%Y-%m-%d %H:%M"), "habits": items,
            "template": TEMPLATE.read_text(encoding="utf-8").replace(STATIC, "", 1)}
    text = GALLERY.read_text(encoding="utf-8")
    return text.replace(STATIC, plain, 1).replace(TOKEN, blob(data), 1)


def _write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:80] or "habit"
    path = folder / f"{safe}.html"
    path.write_text(text, encoding="utf-8")
    return path


def write(folder: Path, name: str, data: dict) -> Path:
    return _write(folder, name, page(data))


def write_gallery(folder: Path, items: list[dict]) -> Path:
    return _write(folder, "_gallery", gallery(items))  # a habit name never starts with "_"
