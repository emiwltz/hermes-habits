#!/usr/bin/env python3
"""The Hermes bench: give reflexes to a real Hermes agent, and note what it does with them.

    python bench/hermes/run.py run <out> [--model mock] [--provider custom] [--base-url URL]
                                         [--arms none,note,skill,plugin] [--scenarios health,triage,...]
                                         [--reps 1] [--hermes /path/to/hermes] [--from-home ~/.hermes]
    python bench/hermes/run.py report <out>

One run is one world: a throwaway home folder with its files, a Hermes home of its own, and one way of giving the
reflexes to the agent (an "arm"):

    none     no reflex at all: the agent does the task by hand. The line everything is compared with.
    note     the reflexes, the `reflex` command on the PATH, and a note in AGENTS.md
    skill    the same command, and a Hermes skill that says when to reach for it
    plugin   the reflexes as one Hermes tool

The agent gets the user's request and nothing else (`hermes -z`). Afterwards the world is read: what is on disk, the
calls to `reflex` (its own journal), every tool call and model request (the observer plugin), and Hermes's own
usage report.

With --model mock a scripted fake model plays the agent: no key, no cost, and the whole chain is proved.
Everything a run leaves stays under <out>, which may hold private text on a real machine: `report` prints only counts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import use  # noqa: E402
from scenarios import SCENARIOS, script  # noqa: E402

ARMS = ("none", "note", "skill", "plugin")
KEYS = ("NOUS_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "TYPESAFE_API_KEY",
        "REFLEX_DECIDER_URL", "REFLEX_DECIDER_KEY", "REFLEX_DECIDER_MODEL")  # handed on to the agent when set
CARRIED = ("config.yaml", ".env", "auth.json")  # what --from-home takes from a real Hermes home: its model and login


def reflex_command() -> str:
    found = Path(sys.executable).parent / "reflex"
    return str(found) if found.exists() else shutil.which("reflex") or sys.exit("no reflex command: pip install -e .")


def build(world: Path, name: str, arm: str, options) -> None:
    """One world: the home, the reflexes in the way this arm gives them, and a Hermes home that watches."""
    scenario = SCENARIOS[name]
    home, bin_, hermes = world / "home", world / "bin", world / "hermes"
    home.mkdir(parents=True)
    bin_.mkdir()
    scenario["setup"](home)
    quiet = bin_ / "reflex-quiet"  # for the bench's own checks: not on the agent's PATH name, not in the log
    quiet.write_text(f'#!/bin/sh\nHOME="{home}" exec "{reflex_command()}" "$@"\n')
    quiet.chmod(quiet.stat().st_mode | stat.S_IEXEC)
    if arm != "none":
        (home / ".reflexes").mkdir()
        for reflex, source in scenario["reflexes"].items():
            (home / ".reflexes" / f"{reflex}.py").write_text(source)
        wrapper = bin_ / "reflex"  # the agent's reflex command, on its PATH (its calls are in ~/.reflexes/journal.jsonl)
        wrapper.write_text(f'#!/bin/sh\nexec "{reflex_command()}" "$@"\n')
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    (hermes / "plugins").mkdir(parents=True)
    if options.from_home:
        fresh(options)
        for kept in CARRIED:
            source = Path(options.from_home).expanduser() / kept
            if source.exists():
                shutil.copy(source, hermes / kept)
    shutil.copytree(HERE / "observer", hermes / "plugins" / "reflex_bench_observer")
    enabled = ["reflex_bench_observer"]
    if arm == "plugin":
        shutil.copytree(HERE / "plugin" / "reflexes", hermes / "plugins" / "reflexes")
        enabled.append("reflexes")
    if arm == "skill":
        shutil.copytree(HERE / "skill" / "reflexes", hermes / "skills" / "automation" / "reflexes")
    if arm == "note":
        listing = subprocess.run([str(quiet), "list"], capture_output=True, text=True).stdout.rstrip()
        (home / "AGENTS.md").write_text(use.INDEX.format(listing="\n".join("  " + l for l in listing.splitlines())) + "\n")
    config = _config(hermes / "config.yaml")
    plugins = config.setdefault("plugins", {})
    plugins["enabled"] = sorted(set(plugins.get("enabled") or []) | set(enabled))
    if options.model != "as-is":
        config["model"] = {"provider": options.provider, "default": options.model,
                           **({"base_url": options.base_url} if options.base_url else {})}
    (hermes / "config.yaml").write_text(json.dumps(config, indent=1))  # JSON is YAML


def fresh(options, within: int = 20 * 60) -> None:
    """A login made in a browser lasts an hour. When the one of --from-home is about to run out, Hermes renews it
    there, in the home it was made in, before a world takes its copy: no world then has to renew its own, which
    would leave the others with a login that no longer counts."""
    home = Path(options.from_home).expanduser()
    state = (_json(home / "auth.json").get("providers") or {}).get("nous") or {}
    python = Path(options.hermes).resolve().parent / "python"
    if not state.get("refresh_token") or not python.exists():
        return
    try:
        left = datetime.fromisoformat(state["expires_at"]).timestamp() - time.time()
    except (KeyError, ValueError, TypeError):
        left = 0
    if left < within:
        subprocess.run([str(python), "-c", "from hermes_cli.auth_nous import resolve_nous_runtime_credentials as go; "
                        "go(force_refresh=True)"], env={**os.environ, "HERMES_HOME": str(home)}, capture_output=True,
                       timeout=90)


def _config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        sys.exit(f"cannot read {path}: {exc}")


def _port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def session(world: Path, name: str, arm: str, at: int, request: str, options) -> dict:
    """One session of the agent on one request, and what it left."""
    env = {**{k: v for k, v in os.environ.items() if k in ("LANG", "LC_ALL", "TZ", "TERM", "USER", "LOGNAME", "SHELL",
                                                            "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy",
                                                            "http_proxy", "no_proxy", "SSL_CERT_FILE",
                                                            "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")
                  or k in KEYS},
           "PATH": f"{world / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(world / "home"),
           "HERMES_HOME": str(world / "hermes"), "REFLEX_BENCH_LOG": str(world / "events.jsonl"),
           "HERMES_YOLO_MODE": "1", "NO_COLOR": "1"}
    model = None
    if options.model == "mock":
        port = _port()
        (world / f"script-{at}.json").write_text(json.dumps(script(name, arm, at)))
        model = subprocess.Popen([sys.executable, str(HERE / "mock.py"), str(port), str(world / f"script-{at}.json"),
                                  str(world / "model.jsonl")])
        config = json.loads((world / "hermes" / "config.yaml").read_text())
        config["model"] = {"provider": "custom", "default": "mock-1", "base_url": f"http://127.0.0.1:{port}/v1"}
        (world / "hermes" / "config.yaml").write_text(json.dumps(config, indent=1))
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.1).close()
                break
            except OSError:
                time.sleep(0.05)
    usage_file = world / f"usage-{at}.json"
    began = time.time()
    try:
        done = subprocess.run([options.hermes, "-z", request, "--usage-file", str(usage_file)], cwd=world / "home",
                              env=env, capture_output=True, text=True, timeout=options.seconds)
        code, answer, errors = done.returncode, done.stdout.strip(), done.stderr.strip()
    except subprocess.TimeoutExpired as late:
        code, answer, errors = 124, (late.stdout or b"").decode("utf-8", "replace").strip(), "timed out"
    finally:
        if model:
            model.kill()
    ended = time.time()
    stop(world)
    (world / f"answer-{at}.txt").write_text(answer + "\n")
    if errors:
        (world / f"errors-{at}.txt").write_text(errors + "\n")
    events = [e for e in _lines(world / "events.jsonl") if began <= e.get("t", 0) <= ended + 1]
    tools = [e for e in events if e["event"] == "tool"]
    requests = [e for e in events if e["event"] == "api"]
    journal = [e for e in _lines(world / "home" / ".reflexes" / "journal.jsonl") if began <= e.get("t", 0) <= ended + 1]
    calls = [" ".join(e["argv"]) for e in journal]
    counts = {}
    for tool in tools:
        counts[tool["name"]] = counts.get(tool["name"], 0) + 1
    usage = _json(usage_file)
    return {"request": request, "exit": code, "seconds": round(ended - began, 1), "answer": answer[:4000],
            "requests": len(requests), "tools": counts, "reflex_calls": calls,
            "reflex_ends": [e.get("status") for e in journal if e.get("status")],
            "commands": [str(t["args"].get("command", ""))[:300] for t in tools if t["name"] == "terminal"],
            "first_input_tokens": (requests[0].get("usage") or {}).get("prompt_tokens") or
            (requests[0].get("usage") or {}).get("input_tokens") if requests else None,
            "usage": {k: usage.get(k) for k in ("input_tokens", "output_tokens", "cache_read_tokens", "total_tokens",
                                                "api_calls", "estimated_cost_usd", "completed", "turn_exit_reason")}}


def stop(world: Path) -> None:
    """Hermes leaves a process behind each session (its local runtime, which idles out by itself): one per world is
    280 MB, so it is stopped at once."""
    try:
        pid = int(json.loads(_text(world / "hermes" / "gateway.pid")).get("pid", 0))
    except (ValueError, AttributeError):
        return
    for sign in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sign)
        except (OSError, ValueError):
            return
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.1)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _lines(path: Path) -> list:
    found = []
    for line in _text(path).splitlines():
        try:
            found.append(json.loads(line))
        except ValueError:
            pass
    return found


def _json(path: Path) -> dict:
    try:
        return json.loads(_text(path) or "{}")
    except ValueError:
        return {}


def one(out: Path, name: str, arm: str, rep: int, options) -> dict:
    scenario = SCENARIOS[name]
    world = out / "worlds" / f"{name}-{arm}-{options.model.replace('/', '_')}-{rep}"
    if world.exists():
        shutil.rmtree(world)
    build(world, name, arm, options)
    sessions = [session(world, name, arm, at, request, options) for at, request in enumerate(scenario["requests"])]
    try:
        wrong = scenario["achieved"](world, [s["answer"] for s in sessions])
    except Exception as exc:  # a check that cannot even run is a failure of the world, said as such
        wrong = [f"the check failed: {type(exc).__name__}: {exc}"]
    fits, last = scenario["fits"], sessions[-1]["reflex_calls"]
    took = bool(fits) and any(c.split()[:2] in (["run", fits], ["test", fits]) for c in last)
    stray = not fits and any(c.split()[:1] == ["run"] for c in last) and "makes" not in scenario
    return {"scenario": name, "arm": arm, "model": options.model, "provider": options.provider, "rep": rep,
            "achieved": not wrong, "wrong": wrong, "fits": fits, "took_the_reflex": took, "ran_one_that_does_not_fit": stray,
            "sessions": sessions, "world": str(world), "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "versions": options.versions}


def run(options) -> None:
    out = Path(options.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    hermes = subprocess.run([options.hermes, "--version"], capture_output=True, text=True).stdout.splitlines()
    options.versions = {"hermes": hermes[0] if hermes else "?", "reflexes": metadata.version("agent-reflexes")}
    names = [n for n in options.scenarios.split(",") if n]
    arms = [a for a in options.arms.split(",") if a]
    unknown = [n for n in names if n not in SCENARIOS] + [a for a in arms if a not in ARMS]
    if unknown:
        sys.exit(f"unknown: {', '.join(unknown)} (scenarios: {', '.join(SCENARIOS)}; arms: {', '.join(ARMS)})")
    with open(out / "records.jsonl", "a", encoding="utf-8") as records:
        for name in names:
            for arm in arms:
                for rep in range(1, options.reps + 1):
                    record = one(out, name, arm, rep, options)
                    records.write(json.dumps(record) + "\n")
                    records.flush()
                    last = record["sessions"][-1]
                    print(f"{'ok  ' if record['achieved'] else 'FAIL'} {name:8} {arm:6} {options.model:12} "
                          f"{last['requests']:2} requests, {sum(last['tools'].values()):2} tool calls, "
                          f"{last['seconds']:5.1f} s  reflex: {' | '.join(last['reflex_calls'])[:70] or '-'}"
                          + ("" if record["achieved"] else f"\n     {'; '.join(record['wrong'])[:200]}"), flush=True)


def report(options) -> None:
    records = _lines(Path(options.out) / "records.jsonl")
    if not records:
        sys.exit("no record yet")
    groups = {}
    for record in records:
        groups.setdefault((record["model"], record["arm"]), []).append(record)

    def mean(values):
        values = [v for v in values if isinstance(v, (int, float))]
        return sum(values) / len(values) if values else float("nan")
    print(f"{'model':16} {'arm':7} {'runs':>4} {'done':>5} {'took it':>8} {'requests':>9} {'tools':>6} {'in tok':>8} "
          f"{'out tok':>8} {'first in':>9} {'seconds':>8} {'cost $':>8}")
    for (model, arm), rows in sorted(groups.items()):
        fitting = [r for r in rows if r["fits"] and arm != "none"]
        sessions = [s for r in rows for s in r["sessions"]]
        took = f"{sum(r['took_the_reflex'] for r in fitting)}/{len(fitting)}" if fitting else "-"
        print(f"{model[:16]:16} {arm:7} {len(rows):4} {sum(r['achieved'] for r in rows):3}/{len(rows):<2}{took:>8} "
              f"{mean(s['requests'] for s in sessions):9.1f} {mean(sum(s['tools'].values()) for s in sessions):6.1f} "
              f"{mean(s['usage'].get('input_tokens') for s in sessions):8.0f} "
              f"{mean(s['usage'].get('output_tokens') for s in sessions):8.0f} "
              f"{mean(s['first_input_tokens'] for s in sessions):9.0f} {mean(s['seconds'] for s in sessions):8.1f} "
              f"{sum(s['usage'].get('estimated_cost_usd') or 0 for s in sessions):8.4f}")
    print("\nby scenario (done, and whether the reflex that fits was taken):")
    arms = [a for a in ARMS if any(r["arm"] == a for r in records)]
    print(f"{'':10}" + "".join(f"{arm:>16}" for arm in arms))
    for name in SCENARIOS:
        cells = []
        for arm in arms:
            rows = [r for r in records if r["scenario"] == name and r["arm"] == arm]
            took = f" took {sum(r['took_the_reflex'] for r in rows)}" if rows and rows[0]["fits"] and arm != "none" else ""
            cells.append(f"{sum(r['achieved'] for r in rows)}/{len(rows)}{took}" if rows else "-")
        if any(c != "-" for c in cells):
            print(f"{name:10}" + "".join(f"{cell:>16}" for cell in cells))


def main() -> None:
    parser = argparse.ArgumentParser(description="The Hermes bench for reflexes.")
    parser.add_argument("command", choices=["run", "report"])
    parser.add_argument("out", help="where the worlds and the records go (keep it out of the repository)")
    parser.add_argument("--model", default="mock", help="mock, a model id, or as-is to keep the model of --from-home")
    parser.add_argument("--provider", default="custom")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--seconds", type=int, default=600, help="what one session may take")
    parser.add_argument("--hermes", default=os.environ.get("HERMES_BIN") or shutil.which("hermes") or "hermes")
    parser.add_argument("--from-home", default="", help="a real Hermes home to take the model and the login from")
    options = parser.parse_args()
    (run if options.command == "run" else report)(options)


if __name__ == "__main__":
    main()
