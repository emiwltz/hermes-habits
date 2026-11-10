"""Wire Habits into Hermes: two tools, six hooks, /habits, `hermes habits`, two skills.

Every Hermes import is lazy, inside a function, so the module also loads in
tests and in `hermes plugins validate`. Every entry point catches its own
errors: the worst case is Hermes without the plugin.

The Habits class keeps the plumbing (settings, ledger, locks, a habit's tool calls, the S1) and mixes in
one module per part: tools (habit_run, habit_save), watch (cron checks), trigger (request habits), guard (a
habit's own files, a new habit through habit_save only, no self-approval), display (`hermes habits`, the pages)
and cockpit (/habits, its decisions and verbs, the prompt section).
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: one process at a time is all we can promise
    fcntl = None

from . import catalog, deciders, ledger as ledgers, runner
from .cockpit import PROMPT_CHARS, Cockpit
from .display import Display
from .guard import Guard
from .tools import RUN_SCHEMA, SAVE_SCHEMA, Tools, enabled_tools, outside
from .trigger import Trigger
from .texts import say
from .watch import Watch

logger = logging.getLogger(__name__)

TOOLSET = "habits"
ROOT = Path(__file__).resolve().parent.parent
# The shipped skills, as habits:habits and habits:writing-habits: when to pass the baton, and how to write a reflex.
SKILLS = (("habits", "When to call a reflex (habit_run) and read its reply"),
          ("writing-habits", "Write Habits graphs: format, rules, examples"))

HOOKS = ("pre_llm_call", "pre_tool_call", "post_tool_call", "post_llm_call", "post_api_request", "on_session_finalize")
OFF = "HABITS_OFF"
OFF_SECONDS = 600  # the off file counts this long after its last touch: a bench that died never leaves habits off
# A process that ends waits this long at most for its turns' comparisons: one judge call at its cap, then the
# ledger (whose writes wait 10 s at most for another process's lock).
LINGER = deciders.LLM_TIMEOUT + 15

DEFAULTS = {
    "backend": "auto", "min_confidence": 0.8, "trigger_confidence": 0.7, "promote_runs": 12,
    "promote_agreement": 0.9, "retire_window": 20, "retire_agreement": 0.75, "spot_check": 0.1, "judge": True,
    "direct": True, "promote": "ask", "language": "en",
}
# A value outside these falls back on the default: a typo never turns the yes off.
CHOICES = {"backend": ("auto", "jev", "llm"), "promote": ("ask", "auto"), "language": ("en", "fr")}


def strict(schema):
    """A JSON schema that providers' strict structured output accepts: every object closed."""
    if isinstance(schema, dict):
        out = {k: strict(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
            out.setdefault("required", list(out.get("properties", {})))
        return out
    if isinstance(schema, list):
        return [strict(v) for v in schema]
    return schema


_warned: set[str] = set()


def _alive(thread) -> bool:
    is_alive = getattr(thread, "is_alive", None)
    return bool(is_alive()) if callable(is_alive) else False


def _warn_once(key: str, message: str, *args):
    if key not in _warned:
        _warned.add(key)
        logger.warning(message, *args)


def refused(name: str, args: dict) -> str | None:
    """Why a habit may not make this call: a habit never does what the model could not do itself."""
    enabled = enabled_tools()
    if enabled and name not in enabled:
        return f"{name} is not enabled in this Hermes session"
    if name == "terminal":
        reason = dangerous(args.get("command"))
        if reason:
            return f"Hermes would ask approval for this command ({reason})"
    try:  # other plugins' pre_tool_call guards see the call as if the model made it
        from hermes_cli.plugins import get_pre_tool_call_directive as ask
    except (ImportError, AttributeError) as exc:  # a Hermes without that API: there is nothing to ask
        ask = None
        _warn_once("guards", "habits: other plugins' guards not asked: %s", exc)
    directive, message = None, None
    if ask is not None:
        try:  # fails closed: a veto may hide behind the error, so the step goes back to the model
            directive, message = _directive(ask, name, args)
        except Exception as exc:
            _warn_once("guards-failed", "habits: other plugins' guards failed, steps refused: %s", exc)
            return f"other plugins' guards could not be asked ({type(exc).__name__})"
    if directive in ("block", "approve"):
        verb = "blocks" if directive == "block" else "asks approval for"
        return f"a Hermes guard {verb} {name}" + (f": {message}" if message else "")
    return None


def _directive(ask, name: str, args: dict) -> tuple:
    """(directive, message) from Hermes's guards. Its own call id: Hermes gates a guard per call, so parallel reads
    would block each other without one; a helper of an older signature takes none. An answer that is not a pair
    raises."""
    try:
        answer = ask(name, args, tool_call_id=f"habits-{uuid.uuid4().hex}")
    except TypeError as exc:
        if "tool_call_id" not in str(exc):
            raise
        answer = ask(name, args)
    directive, message = answer
    return directive, message


def dangerous(command) -> str | None:
    """Why Hermes would ask approval for this command, or None.

    A plugin's own tool calls skip Hermes's approval prompts outside a chat turn,
    so a habit refuses anything Hermes would ask about and hands it to the model.
    """
    if not isinstance(command, str):
        return None
    detect = None
    try:
        from tools.approval_detection import detect_dangerous_command as detect
    except ImportError:
        try:
            from tools.approval import detect_dangerous_command as detect
        except ImportError:
            detect = None
    if detect is not None:
        try:
            found = detect(command)
            if found and found[0]:
                return str(found[2] if len(found) > 2 else found[1])
            return None
        except Exception:
            pass
    if re.search(r"\b(rm|rmdir|shred|unlink|dd|chmod|chown|kill|pkill|sudo)\b|\|\s*(ba)?sh\b", command):
        return "a command that may destroy or change the system"
    return None


class Habits(Tools, Watch, Trigger, Guard, Display, Cockpit):
    """The plugin's state between calls: settings, ledger, pending shadow turns."""

    def __init__(self, ctx, home: Path | None = None):
        self.ctx = ctx
        self._home = home  # tests pass a folder; Hermes gives plugin-data/habits
        self._ledger = None
        self.pending: dict[str, dict] = {}
        self.said: dict[str, str] = {}  # session -> the user's last message, for habit_save
        self.telling: dict[str, list] = {}  # session -> the news rows its turn tells, taken once its reply went out
        self.model_seen: float | None = None  # when the model last answered a request (monotonic)
        self.locks: dict[str, threading.Lock] = {}
        self.guard = threading.Lock()
        self.refusals: dict[str, list[float]] = {}  # session -> when the guard refused a new habit: stop after 2 in 10 min
        self.workers: list = []  # the threads a turn left working: a comparison

    # ---- plumbing -------------------------------------------------------

    def off(self) -> bool:
        """Habits steps aside, its tools apart: HABITS_OFF is set, to measure Hermes without habits. Hermes 0.23
        runs a `hermes -z` turn in its gateway and the plugin in its plugin host, which the variable does not
        reach: a file named off in the plugin's folder, touched in the last 10 minutes, does the same for every
        session (the bench uses it)."""
        if os.environ.get(OFF):
            return True
        try:
            return abs(time.time() - (self.home() / "off").stat().st_mtime) < OFF_SECONDS
        except Exception:  # no file, or no plugin folder
            return False

    def paused(self) -> bool:
        """/habits pause: no habit runs and the watches stay silent until /habits reprends."""
        try:
            return (self.home() / "paused").exists()
        except Exception:  # no plugin folder
            return False

    def hook(self, callback):
        """The hook, doing nothing while Habits is off."""
        @functools.wraps(callback)
        def hooked(*args, **kwargs):
            return None if self.off() else callback(*args, **kwargs)
        return hooked

    def background(self, work, *args):
        """Work after a turn, on a daemon thread that carries the turn's context (its profile, its secrets, its
        conversation for post_message); kept so that a process that ends waits for it."""
        thread = threading.Thread(target=contextvars.copy_context().run, args=(work, *args), daemon=True)
        with self.guard:
            self.workers = [t for t in self.workers if _alive(t)] + [thread]
        thread.start()

    def on_session_finalize(self, reason="", **_):
        """hermes -z, chat -q and the CLI's exit end the process with os._exit right after this hook, past every
        thread: a turn being compared would be lost midway. A process that ends waits for
        them here (LINGER at most); a new session or a reset goes on, and so do they."""
        if reason != "shutdown":
            return None
        with self.guard:
            workers = list(self.workers)
        deadline = time.monotonic() + LINGER
        for thread in workers:
            if _alive(thread):
                logger.info("habits: waiting for a habit's work before Hermes exits")
                thread.join(max(0.0, deadline - time.monotonic()))
        return None

    def setting(self, key):
        default = DEFAULTS[key]
        try:
            value = self.ctx.get_config(key, default)
        except Exception:
            return default
        try:
            if isinstance(default, bool):  # a word it does not know keeps the default: never a feature off by a typo
                said = str(value).strip().lower() if not isinstance(value, bool) else value
                return value if isinstance(value, bool) else True if said in ("1", "true", "yes", "on") else \
                    False if said in ("0", "false", "no", "off") else default
            if key in CHOICES:
                value = "off" if value is False else str(value).strip().lower()  # YAML 1.1 reads a bare off as false
                return value if value in CHOICES[key] else default
            return type(default)(value)
        except (TypeError, ValueError):
            return default

    def home(self) -> Path:
        if self._home is not None:
            return self._home
        from plugins.plugin_storage import plugin_data_dir
        return Path(plugin_data_dir("habits"))

    @property
    def ledger(self) -> ledgers.Ledger:
        if self._ledger is None:  # its thresholds read where they apply: a change in Settings needs no restart
            self._ledger = ledgers.Ledger(self.home() / "habits.db", self.rules)
        return self._ledger

    def rules(self) -> ledgers.Rules:
        return ledgers.Rules(
            promote_runs=self.setting("promote_runs"), promote_agreement=self.setting("promote_agreement"),
            retire_window=self.setting("retire_window"), retire_agreement=self.setting("retire_agreement"),
            promote=self.setting("promote"))  # anything but auto waits for the yes

    def untold(self, names=None, take=True) -> list[tuple[str, str]]:
        """The retirements to tell now; a ledger that cannot say keeps them for a later reply or wake."""
        try:
            return self.ledger.untold(names, take)
        except Exception as exc:
            logger.warning("habits: could not read the retirements to tell: %s", exc)
            return []

    def lock(self, name: str) -> threading.Lock:
        with self.guard:
            return self.locks.setdefault(name, threading.Lock())

    @contextlib.contextmanager
    def held(self, name: str, wait: float = 0.0):
        """One run at a time per habit, across threads and processes (gateway, CLI, cron tick).
        Yields False when the habit is already running."""
        lock = self.lock(name)
        if not (lock.acquire(timeout=wait) if wait else lock.acquire(blocking=False)):
            yield False
            return
        try:
            if fcntl is None:
                yield True
                return
            folder = self.home() / "locks"
            folder.mkdir(parents=True, exist_ok=True)
            safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
            with open(folder / f"{safe}.lock", "w") as handle:
                deadline = time.monotonic() + wait
                while True:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            yield False
                            return
                        time.sleep(0.05)
                try:
                    yield True
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            lock.release()

    @staticmethod
    def secret(name: str):
        try:
            from agent.secret_scope import get_secret
            return get_secret(name)
        except Exception:
            return None

    def complete(self, instructions: str, text: str, schema: dict, timeout: float = deciders.LLM_TIMEOUT) -> dict:
        """One structured call on the user's own model."""
        result = self.ctx.llm.complete_structured(instructions=instructions, input=[{"type": "text", "text": text}],
                                                  json_schema=strict(schema), timeout=timeout)
        parsed = getattr(result, "parsed", None)
        if parsed is None:
            parsed = json.loads(getattr(result, "text", "") or "null")
        if not isinstance(parsed, dict):
            raise ValueError("the model did not answer with a JSON object")
        return parsed

    def decider(self) -> deciders.Chain:
        return deciders.build(self.setting("backend"), self.secret("TYPESAFE_API_KEY"), self.complete)

    def tool(self, name: str, args: dict) -> str:
        refusal = refused(name, args or {})
        if refusal:
            return json.dumps({"error": f"{refusal}; the habit leaves it to the model"})
        return self.ctx.dispatch_tool(name, args)

    def subhabit(self, name: str) -> dict:
        habit = catalog.find(name)
        if habit is None or not habit.valid:
            raise KeyError(name)
        return habit.graph

    def run(self, habit: catalog.Habit | None, scenario: dict, dry: bool, inputs=None, trigger="",
            max_seconds=None, keep=True, within: Path | None = None, memory: dict | None = None,
            own: Path | None = None) -> runner.Result:
        """keep: False for a measurement, which reads the memory of check: changed and never moves it. within: a
        measurement's folder (resolved): terminal commands start there and no write leaves it, but to run a script
        of own (the habit's assets/). memory: a
        measurement's own references for check: changed, moved in place, instead of the ledger's."""
        if "min_confidence" not in scenario:
            scenario = {**scenario, "min_confidence": self.setting("min_confidence")}
        if memory is None:
            memory = self.ledger.memory(habit.name) if habit else {}
        env = runner.Env(tool=self.tool, decider=self.decider(), llm=self.complete, habits=self.subhabit,
                         memory=memory, dry=dry, max_seconds=max_seconds,
                         workdir=str(within) if within else "~",  # else each backend's own home
                         writes=functools.partial(outside, within, own=own) if within else None)
        result = runner.run(scenario, env, inputs, trigger)
        if habit and keep and env.memory:
            try:
                self.ledger.remember(habit.name, env.memory)
            except Exception as exc:  # the run happened: its bookkeeping never hides that
                logger.warning("habits: could not keep the memory of %s: %s", habit.name, exc)
        return result

    def used(self, habit: catalog.Habit, background: bool = False):
        """Tell Hermes's curator a saved habit ran: it archives a skill nobody loaded for 30 days, and the plugin
        runs a habit without loading its skill. A private Hermes API, best-effort: it never fails a run."""
        if habit.origin != "skill":
            return  # an example is not one of the user's skills

        def bump():
            try:
                from tools.skill_usage import bump_use
                bump_use(habit.name)
            except Exception as exc:  # an older Hermes, a locked or broken usage file
                logger.debug("habits: the curator did not count the use of %s: %s", habit.name, exc)
        if background:  # the hook's time is the user's: the usage file's lock and Hermes's own hooks wait elsewhere
            threading.Thread(target=contextvars.copy_context().run, args=(bump,), daemon=True).start()
        else:
            bump()

    def summary(self, habit: catalog.Habit) -> dict:
        """The ledger's numbers for a habit. An invalid graph on disk never runs, so it never starts a new
        version: it shows the last valid version's."""
        if habit.valid:
            return self.ledger.summary(habit.name, habit.version)
        row = self.ledger.info(habit.name)
        return self.ledger.summary(habit.name, row["version"] if row else habit.version, start_new=False)

    def version(self, habit: catalog.Habit) -> str:
        """The version a status change applies to: the last valid one while the graph on disk is invalid."""
        row = None if habit.valid else self.ledger.info(habit.name)
        return row["version"] if row else habit.version


def register(ctx) -> None:
    habits = Habits(ctx)
    ctx.register_tool(name="habit_run", toolset=TOOLSET, schema=RUN_SCHEMA, handler=habits.tool_run, emoji="🔁")
    ctx.register_tool(name="habit_save", toolset=TOOLSET, schema=SAVE_SCHEMA, handler=habits.tool_save, emoji="🔁")
    for name in HOOKS:  # HABITS_OFF turns them all off, the tools stay
        ctx.register_hook(name, habits.hook(getattr(habits, name if name.startswith("on_") else f"on_{name}")))
    ctx.register_command("habits", habits.command, description=say("menu", habits.lang()))
    ctx.register_cli_command("habits", "Habits: list, show, view, check, run, promote, pause, resume, undo, "
                             "tick, retire, reset, cleanup", habits.cli_setup, habits.cli_handle)
    if hasattr(ctx, "register_system_prompt_section"):  # an older Hermes has none
        ctx.register_system_prompt_section("habits", habits.prompt, max_chars=PROMPT_CHARS)
    else:
        _warn_once("prompt", "habits: this Hermes has no system prompt sections; the model is not told of habits")
    for name, description in SKILLS:
        path = ROOT / "skills" / name / "SKILL.md"
        if not path.exists():
            continue
        try:
            ctx.register_skill(name, path, description=description,
                               frontmatter={"name": name, "description": description})
        except ValueError as exc:  # a clash with another plugin's skill: Habits still works without it
            logger.warning("habits: could not register the skill %s: %s", name, exc)
