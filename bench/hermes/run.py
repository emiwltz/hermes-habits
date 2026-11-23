#!/usr/bin/env python3
"""The Hermes bench: give a real Hermes agent the plugin of this repository, and note what it does with it.

    python bench/hermes/run.py run <out> [--model mock] [--provider custom] [--base-url URL] [--login DIR]
                                         [--arms hand,plugin] [--scenarios health,...] [--reps 1]
                                         [--profile DIR] [--crowd N] [--seen N] [--writer MODEL]
                                         [--keys NAME,NAME] [--hermes /path/to/hermes] [--unconfined]
    python bench/hermes/run.py report <out>

One run is one world: a throwaway home folder with its files, a Hermes home of its own, and one of two arms.

    hand     no reflex: the agent does the task by hand. The line everything is compared with.
    plugin   the plugin (plugin.yaml and __init__.py at the root), the scenario's reflexes in ~/.reflexes, and
             the `reflex` command in the agent's terminal

The agent gets the user's request and nothing else (`hermes -z`). Afterwards the world is read: what is on disk, the
calls to `reflex` (its own journal), every tool call and model request (the observer plugin), and Hermes's own
usage report.

--model mock    a scripted fake model plays the agent: no key, no cost, and the whole chain is proved
--login DIR     a Hermes home that holds a sign-in made for the bench (HERMES_HOME=DIR hermes auth add nous)
--profile DIR   a Hermes home whose skills, SOUL.md and memory are copied into each world, to crowd the agent's
                prompt (loaded.py makes one up); with --model as-is its model settings are taken too
--crowd N       N more reflexes in each world (crowd.py); --seen K: the one that fits was run K times before
--writer MODEL  in a scenario of several sessions, another model for the one that makes the reflex

Every session is fenced by the operating system (confine.py): it writes only in its world, and cannot read the home
folder of whoever runs the bench. Everything a run leaves stays under <out>; `report` prints counts only.
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
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import confine  # noqa: E402
import crowd  # noqa: E402
from scenarios import SCENARIOS, script  # noqa: E402

ARMS = ("hand", "plugin")
ROOM = "3800"  # what the plugin asks `reflex note` to fit in
PASSED = ("LANG", "LC_ALL", "TZ", "TERM", "USER", "LOGNAME", "SHELL", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
          "https_proxy", "http_proxy", "no_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
          "NODE_EXTRA_CA_CERTS")  # of the bench's own environment, what a session gets: no key unless --keys names it


def reflex_command() -> str:
    found = Path(sys.executable).parent / "reflex"
    return str(found) if found.exists() else shutil.which("reflex") or sys.exit("no reflex command: pip install -e .")


def build(world: Path, name: str, arm: str, options) -> None:
    """One world: the home, the reflexes and the plugin if this arm has them, and a Hermes home that watches."""
    scenario = SCENARIOS[name]
    home, bin_, hermes = world / "home", world / "bin", world / "hermes"
    home.mkdir(parents=True)
    bin_.mkdir()
    scenario["setup"](home)
    quiet = bin_ / "reflex-quiet"  # for the bench's own checks: not the name the agent calls, not in its journal
    quiet.write_text(f'#!/bin/sh\nHOME="{home}" exec "{reflex_command()}" "$@"\n')
    quiet.chmod(quiet.stat().st_mode | stat.S_IEXEC)
    (hermes / "plugins").mkdir(parents=True)
    shutil.copytree(HERE / "observer", hermes / "plugins" / "reflex_bench_observer")
    enabled = ["reflex_bench_observer"]
    if arm == "plugin":
        (home / ".reflexes").mkdir()
        for reflex, source in {**crowd.reflexes(options.crowd), **scenario["reflexes"]}.items():
            (home / ".reflexes" / f"{reflex}.py").write_text(source)
        if options.crowd:  # a year of use: the crowd was written over the last months, the scenario's own long ago
            for at, reflex in enumerate(crowd.reflexes(options.crowd)):
                os.utime(home / ".reflexes" / f"{reflex}.py", (time.time() - (at + 5) * 86400,) * 2)
            for reflex in scenario["reflexes"]:
                os.utime(home / ".reflexes" / f"{reflex}.py", (time.time() - 400 * 86400,) * 2)
            if options.seen and scenario["fits"] in scenario["reflexes"]:  # ...and run now and then
                (home / ".reflexes" / "journal.jsonl").write_text("".join(
                    json.dumps({"t": time.time() - (k + 1) * 7 * 86400, "argv": ["run", scenario["fits"]], "exit": 0,
                                "ms": 20, "status": "done"}) + "\n" for k in range(options.seen)))
        wrapper = bin_ / "reflex"  # the agent's `reflex`, first on its PATH (its calls are in ~/.reflexes/journal.jsonl)
        wrapper.write_text(f'#!/bin/sh\nexec "{reflex_command()}" "$@"\n')
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
        (hermes / "plugins" / "habits").mkdir()
        for part in ("plugin.yaml", "__init__.py"):  # the plugin itself, as it is at the root of the repository
            shutil.copy(ROOT / part, hermes / "plugins" / "habits" / part)
        enabled.append("habits")
    if options.login:
        fresh(options)
        for kept in ("auth.json", ".env"):  # a sign-in, never a configuration
            if (Path(options.login).expanduser() / kept).exists():
                shutil.copy(Path(options.login).expanduser() / kept, hermes / kept)
    config = {"plugins": {"enabled": sorted(enabled)}, "agent": {"max_turns": 40},
              "security": {"allow_lazy_installs": False},  # or Hermes installs a Python of its own in every home
              "model": {"provider": options.provider, "default": options.model,
                        **({"base_url": options.base_url} if options.base_url else {})}}
    if options.profile:
        profile = Path(options.profile).expanduser()
        for kept in ("SOUL.md", "memories/MEMORY.md", "memories/USER.md"):
            if (profile / kept).exists():
                (hermes / kept).parent.mkdir(exist_ok=True)
                shutil.copy(profile / kept, hermes / kept)
        if (profile / "skills").is_dir():
            shutil.copytree(profile / "skills", hermes / "skills", dirs_exist_ok=True)
    if options.model == "as-is":  # the profile's own model, and how hard it thinks
        told = _json(Path(options.profile).expanduser() / "config.yaml") if options.profile else {}
        if not told.get("model"):
            sys.exit("--model as-is needs --profile, a Hermes home whose config.yaml (written as JSON) names a model")
        config["model"] = told["model"]
        config["agent"].update({k: v for k, v in (told.get("agent") or {}).items() if k.startswith("reasoning")})
    (hermes / "config.yaml").write_text(json.dumps(config, indent=1))  # JSON is YAML


def shared(world: Path, options) -> Path:
    """Where Hermes keeps the sign-in that several homes share. A sign-in made in a browser is renewed every half
    hour, and each renewal uses up the one before: worlds that each renewed their own copy signed each other out.
    So the worlds of a run and the home their sign-in comes from share one store, which is how Hermes keeps several
    profiles on one sign-in."""
    return Path(options.login).expanduser() / "shared" if options.login else world / "hermes" / "shared"


def fresh(options) -> None:
    """Before a world takes its copy of the sign-in, Hermes brings it up to date in the home it comes from: it takes
    what another world renewed, or renews it itself when that is due."""
    home = Path(options.login).expanduser()
    state = (_json(home / "auth.json").get("providers") or {}).get("nous") or {}
    python = Path(options.hermes).resolve().parent / "python"
    if state.get("refresh_token") and python.exists():
        subprocess.run([str(python), "-c", "from hermes_cli.auth_nous import resolve_nous_runtime_credentials as go; go()"],
                       env={**os.environ, "HERMES_HOME": str(home), "HERMES_SHARED_AUTH_DIR": str(home / "shared")},
                       capture_output=True, timeout=90)


def within_reach(options) -> list:
    """The folders of the PATH the agent is given. Under the fence, one that lies in the hidden home is left out:
    a command found there could not even be read."""
    folders = [f for f in os.environ.get("PATH", "").split(os.pathsep) if f]
    if options.unconfined:
        return folders
    home = confine.real(Path.home())
    return [f for f in folders if not confine.inside(confine.real(f), [home]) or confine.inside(confine.real(f), options.reads)]


def needed(options, aside: Path) -> list:
    """What a fenced session must still be able to read if it lies in the home folder: the two Pythons (Hermes's
    and the one of `reflex`), their environments, the two checkouts, and the certificates the network asks for."""
    places = [sys.prefix, sys.base_prefix, ROOT, Path(reflex_command()).parent.parent, Path(options.hermes).parent.parent,
              *(os.environ.get(name, "") for name in PASSED if "CERT" in name or "CA_" in name)]
    python = Path(options.hermes).resolve().parent / "python"
    if python.exists():
        told = subprocess.run([str(python), "-c", "import os, sys, hermes_cli; print(sys.base_prefix); "
                               "print(os.path.dirname(os.path.dirname(os.path.abspath(hermes_cli.__file__))))"],
                              capture_output=True, text=True, env={**os.environ, "HERMES_HOME": str(aside)})
        places += told.stdout.split("\n")[:2]
    return sorted({confine.real(p) for p in places if p and confine.real(p).exists() and confine.real(p) != Path("/")})


def _port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def session(world: Path, name: str, arm: str, at: int, request: str, options) -> dict:
    """One session of the agent on one request, and what it left."""
    env = {**{k: v for k, v in os.environ.items() if k in PASSED or k in options.keys.split(",")},
           "PATH": os.pathsep.join([str(world / "bin"), *within_reach(options)]), "HOME": str(world / "home"),
           "HERMES_HOME": str(world / "hermes"), "REFLEX_BENCH_LOG": str(world / "events.jsonl"),
           "HERMES_SHARED_AUTH_DIR": str(shared(world, options)), "HERMES_DISABLE_LAZY_INSTALLS": "1",
           "HERMES_YOLO_MODE": "1", "NO_COLOR": "1"}
    config, model = json.loads((world / "hermes" / "config.yaml").read_text()), None
    if options.model == "mock":
        port = _port()
        (world / f"script-{at}.json").write_text(json.dumps(script(name, arm, at)))
        model = subprocess.Popen([sys.executable, str(HERE / "mock.py"), str(port), str(world / f"script-{at}.json"),
                                  str(world / "model.jsonl")])
        config["model"] = {"provider": "custom", "default": "mock-1", "base_url": f"http://127.0.0.1:{port}/v1"}
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), 0.1).close()
                break
            except OSError:
                time.sleep(0.05)
    elif options.writer and len(SCENARIOS[name]["requests"]) > 1:
        config["model"]["default"] = options.writer if at == 0 else options.model  # the big one writes, the small runs
    (world / "hermes" / "config.yaml").write_text(json.dumps(config, indent=1))
    usage_file = world / f"usage-{at}.json"
    command = [options.hermes, "-z", request, "--usage-file", str(usage_file)]
    if not options.unconfined:  # the agent writes in its world and in the sign-in's store, and cannot read the real home
        shared(world, options).mkdir(parents=True, exist_ok=True)
        command = confine.confined(command, [world, shared(world, options)], [Path.home()], options.reads)
    began = time.time()
    try:
        done = subprocess.run(command, cwd=world / "home", env=env, capture_output=True, text=True,
                              timeout=options.seconds)
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
    counts = {}
    for tool in tools:
        counts[tool["name"]] = counts.get(tool["name"], 0) + 1
    usage, first = _json(usage_file), (requests[0].get("usage") or {}) if requests else {}
    return {"request": request, "exit": code, "seconds": round(ended - began, 1), "answer": answer[:4000],
            "requests": len(requests), "tools": counts, "reflex_calls": [" ".join(e["argv"]) for e in journal],
            "reflex_ends": [e.get("status") for e in journal if e.get("status")],
            "commands": [str(t["args"].get("command", ""))[:300] for t in tools if t["name"] == "terminal"],
            "first_input_tokens": first.get("prompt_tokens") or first.get("input_tokens"),
            "usage": {k: usage.get(k) for k in ("input_tokens", "output_tokens", "cache_read_tokens", "total_tokens",
                                                "api_calls", "estimated_cost_usd", "completed", "turn_exit_reason")}}


def stop(world: Path) -> None:
    """Hermes leaves a process behind each session (its local runtime, which idles out by itself): one per world is
    280 MB, so it is stopped at once. Only a process that is a Hermes gateway is, by the number it wrote itself."""
    try:
        pid = int(json.loads(_text(world / "hermes" / "gateway.pid")).get("pid", 0))
    except (ValueError, AttributeError):
        return
    named = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    if pid <= 1 or "gateway" not in named:  # the number of a process that ended may now be another's
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
    # by hand the user has to spell a long routine out; with its reflex a few words do
    requests = scenario.get("by_hand", scenario["requests"]) if arm == "hand" else scenario["requests"]
    noted, sessions = "", []
    try:
        for at, request in enumerate(requests):
            if arm == "plugin" and at == len(requests) - 1:  # what the last session will be told
                noted = subprocess.run([str(world / "bin" / "reflex-quiet"), "note", "--chars", ROOM],
                                       capture_output=True, text=True).stdout
            sessions.append(session(world, name, arm, at, request, options))
    finally:
        stop(world)  # whatever happened, nothing of this world goes on running
    try:
        wrong = scenario["achieved"](world, [s["answer"] for s in sessions])
    except Exception as exc:  # a check that cannot even run is a failure of the world, said as such
        wrong = [f"the check failed: {type(exc).__name__}: {exc}"]
    fits, last = scenario["fits"], [call.split() for call in sessions[-1]["reflex_calls"]]
    ran = {call[1] for call in last if call[0] in ("run", "test") and len(call) > 1}
    return {"scenario": name, "arm": arm, "model": options.model, "provider": options.provider, "rep": rep,
            "achieved": not wrong, "wrong": wrong, "fits": fits, "took_the_reflex": bool(fits) and fits in ran,
            "others_run": sorted(ran - {fits, scenario.get("makes")}),  # reflexes it ran that were not the one for this
            "shown": bool(fits) and f"  {fits}(" in noted,  # was the reflex that fits in the note, or cut from it
            "note_chars": len(noted), "crowd": options.crowd, "seen": options.seen,
            **({"writer": options.writer} if options.writer else {}),
            "sessions": sessions, "world": str(world), "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "versions": options.versions}


def run(options) -> None:
    out = Path(options.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    real = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser().resolve()
    given = Path(options.login).expanduser().resolve() if options.login else None
    if given and (given == real or real in given.parents):
        sys.exit(f"--login {options.login} is the Hermes home in use. The bench renews the sign-in it is given, and the "
                 "Hermes in use would be signed out: make a home that only holds a sign-in "
                 "(HERMES_HOME=~/bench-login hermes auth add nous).")
    names = [n for n in options.scenarios.split(",") if n]
    arms = [a for a in options.arms.split(",") if a]
    unknown = [n for n in names if n not in SCENARIOS] + [a for a in arms if a not in ARMS]
    if unknown:
        sys.exit(f"unknown: {', '.join(unknown)} (scenarios: {', '.join(SCENARIOS)}; arms: {', '.join(ARMS)})")
    aside = out / "hermes-aside"  # even to say its version, Hermes is given a home that is not the one in use
    hermes = subprocess.run([options.hermes, "--version"], capture_output=True, text=True,
                            env={**os.environ, "HERMES_HOME": str(aside)}).stdout.splitlines()
    options.versions = {"hermes": hermes[0] if hermes else "?", "reflexes": metadata.version("agent-reflexes")}
    options.reads = needed(options, aside)
    shutil.rmtree(aside, ignore_errors=True)
    if not options.unconfined:
        try:
            confine.confined(["true"], [out], [Path.home()], options.reads)
        except confine.Unavailable as exc:
            sys.exit(f"cannot fence the agent on this machine: {exc}. With --unconfined it runs with your own rights "
                     "and may write anywhere you can.")
    with open(out / "records.jsonl", "a", encoding="utf-8") as records:
        for name in names:
            for arm in arms:
                if arm == "hand" and "makes" in SCENARIOS[name]:
                    continue  # by hand there is no reflex to make
                for rep in range(1, options.reps + 1):
                    record = one(out, name, arm, rep, options)
                    records.write(json.dumps(record) + "\n")
                    records.flush()
                    last = record["sessions"][-1]
                    print(f"{'ok  ' if record['achieved'] else 'FAIL'} {name:8} {arm:6} {options.model:12} "
                          f"{last['requests']:2} requests, {sum(last['tools'].values()):2} tool calls, "
                          f"{last['seconds']:5.1f} s  reflex: {' | '.join(last['reflex_calls'])[:70] or '-'}"
                          + ("" if record["achieved"] else f"\n     {'; '.join(record['wrong'])[:200]}"), flush=True)
                    if not last["requests"]:  # a sign-in that ran out, no credit, no network, or a session that
                        # another Hermes than the world's own served (its requests are not seen here): never go on
                        sys.exit("stopped: no request reached the model through this world's own Hermes. "
                                 f"Hermes said: {last['answer'][:300]!r}")


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
        fitting = [r for r in rows if r["fits"] and arm != "hand"]
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
            took = f" took {sum(r['took_the_reflex'] for r in rows)}" if rows and rows[0]["fits"] and arm != "hand" else ""
            cells.append(f"{sum(r['achieved'] for r in rows)}/{len(rows)}{took}" if rows else "-")
        if any(c != "-" for c in cells):
            print(f"{name:10}" + "".join(f"{cell:>16}" for cell in cells))


def main() -> None:
    parser = argparse.ArgumentParser(description="The Hermes bench for reflexes.")
    parser.add_argument("command", choices=["run", "report"])
    parser.add_argument("out", help="where the worlds and the records go (keep it out of the repository)")
    parser.add_argument("--model", default="mock", help="mock, a model id, or as-is for the model of --profile")
    parser.add_argument("--provider", default="custom")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--login", default="", help="a Hermes home that holds a sign-in made for the bench")
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--seconds", type=int, default=600, help="what one session may take")
    parser.add_argument("--profile", default="", help="a Hermes home whose skills, SOUL.md and memory crowd each world")
    parser.add_argument("--crowd", type=int, default=0, help="this many more reflexes in each world (crowd.py)")
    parser.add_argument("--seen", type=int, default=0, help="with --crowd: the reflex that fits was run this many times "
                        "before, so the note keeps it; 0: never, and written long ago")
    parser.add_argument("--writer", default="", help="another model for the session that makes a reflex")
    parser.add_argument("--keys", default="", help="environment variables handed on to the agent, by name (none: a key "
                        "in your shell is never passed on unasked)")
    parser.add_argument("--hermes", default=os.environ.get("HERMES_BIN") or shutil.which("hermes") or "hermes")
    parser.add_argument("--unconfined", action="store_true", help="no fence: the agent runs with your own rights")
    options = parser.parse_args()
    (run if options.command == "run" else report)(options)


if __name__ == "__main__":
    main()
