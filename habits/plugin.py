"""Wire Habits into Hermes: two tools, seven hooks, /habits, `hermes habits`, two skills.

Every Hermes import is lazy, inside a function, so the module also loads in
tests and in `hermes plugins validate`. Every entry point catches its own
errors: the worst case is Hermes without the plugin.

The Habits class keeps the plumbing (settings, ledger, locks, a habit's tool calls, the decision model) and mixes in
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

from . import catalog, deciders, graph, ledger as ledgers, runner
from .cockpit import PROMPT_CHARS, Cockpit
from .display import Display
from .guard import Guard
from .nursery import Dream
from .practice import Practice
from .tools import RUN_SCHEMA, SAVE_SCHEMA, Tools, enabled_tools, outside
from .trigger import Trigger
from .texts import say
from .watch import Watch

logger = logging.getLogger(__name__)

TOOLSET = "habits"
ROOT = Path(__file__).resolve().parent.parent
# The shipped skills, as habits:habits and habits:writing-habits: when to hand work to a reflex, and how to write one.
SKILLS = (("habits", "When to call a reflex (habit_run) and read its reply"),
          ("writing-habits", "Write Habits reflexes: format, rules, examples"))

HOOKS = ("pre_llm_call", "pre_tool_call", "post_tool_call", "post_llm_call", "post_api_request", "on_session_finalize",
         "transform_tool_result")
OFF = "HABITS_OFF"
OFF_SECONDS = 600  # the off file counts this long after its last touch: a bench that died never leaves habits off
# A process that ends waits this long at most for its turns' comparisons: one judge call at its cap, then the
# ledger (whose writes wait 10 s at most for another process's lock).
LINGER = deciders.LLM_TIMEOUT + 15

DEFAULTS = {
    "learning": "suggest", "caution": "normal", "dream": "daily", "decision_model": "auto",
    "permissions": "hermes", "model_text": "quoted", "language": "en",
    # Hidden overrides, not in config_schema; None = not set. A number the user set wins over the caution row.
    "promote": None, "backend": None, "min_confidence": None, "trigger_confidence": None,
    "promote_runs": None, "promote_agreement": None, "retire_window": None, "retire_agreement": None,
    "spot_check": None, "judge": True, "direct": True,
}
# A value outside these falls back on the default (a typo never turns the yes off), except the three in STRICT.
SPELLINGS = {("learning", "practise"): "practice"}  # the British spelling of a value, still accepted
CHOICES = {
    "learning": ("off", "suggest", "practice", "auto"), "caution": ("relaxed", "normal", "strict"),
    "dream": ("daily", "manual"), "decision_model": ("auto", "jev", "hermes"),
    "permissions": ("hermes", "strict"), "model_text": ("quoted", "never"), "language": ("en", "fr"),
    "promote": ("ask", "auto"), "backend": ("auto", "jev", "llm"),
}
# The three safety switches: a value they do not know (a typo, a YAML off / no / false) is the strict side, never the
# permissive default, and is said once in the log.
STRICT = {"permissions": "strict", "model_text": "never", "dream": "manual"}
NUMBERS = {"min_confidence": float, "trigger_confidence": float, "promote_runs": int, "promote_agreement": float,
           "retire_window": int, "retire_agreement": float, "spot_check": float}  # set by the user: wins; else the caution row
BACKEND_OF = {"auto": "auto", "jev": "jev", "hermes": "llm"}  # decision_model -> the name deciders.build knows


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


def reachable() -> bool:
    """Whether Hermes can put a flagged command to someone, or applies its own unattended rule, from here: a chat
    (CLI, gateway, ask) or a cron, `-q` or unattended-platform mode whose configured answer decides. Where it has
    none, Hermes's own check lets everything through, so the answer is no: fails closed, also when Hermes's
    private API cannot be read (risk 6.5: never a default that lets a flagged command run unseen)."""
    try:
        from tools.approval import _presence, _unattended_contexts
        _, is_cli, is_gateway, is_ask = _presence()
        return bool(is_cli or is_gateway or is_ask or _unattended_contexts())
    except Exception:
        return False


def refused(name: str, args: dict, *, approved: bool = False) -> str | None:
    """Why a habit may not make this call: a habit never does what the model could not do itself. approved: the
    user said yes to this habit and it runs inside a Hermes turn (permissions: hermes), so a command Hermes would
    ask about is left to Hermes's own approval, which asks, refuses or lets through as it does for the model; if
    nobody could be asked from here, the command is refused all the same."""
    enabled = enabled_tools()
    if enabled and name not in enabled:
        return f"{name} is not enabled in this Hermes session"
    if name == "terminal":
        command = args.get("command")
        reason = dangerous(command) or ("a command that deletes files" if approved and isinstance(command, str)
                                        and graph._deletes(command) else None)
        if reason and not (approved and reachable()):
            return f"Hermes would ask approval for this command ({reason})"
    try:  # other plugins' pre_tool_call guards see the call as if the model made it
        from hermes_cli.plugins import get_pre_tool_call_directive as ask
    except (ImportError, AttributeError) as exc:  # a Hermes without that API: there is nothing to ask
        ask = None
        _warn_once("guards", "habits: other plugins' guards not asked: %s", exc)
    directive, message = None, None
    if ask is not None:
        try:  # fails closed: a veto may hide behind the error, so the step goes back to the model
            directive, message = _directive(ask, name, guard_view(name, args))
        except Exception as exc:
            _warn_once("guards-failed", "habits: other plugins' guards failed, steps refused: %s", exc)
            return f"other plugins' guards could not be asked ({type(exc).__name__})"
    if directive in ("block", "approve"):
        verb = "blocks" if directive == "block" else "asks approval for"
        return f"a Hermes guard {verb} {name}" + (f": {message}" if message else "")
    return None


def guard_view(name: str, args: dict) -> dict:
    """What other plugins' guards are asked about: the command the reflex wrote, without the status line the runner
    adds to a one-pipe read (its words, "habits-pipe" among them, would make a guard refuse an innocent command)."""
    command = args.get("command")
    if name == "terminal" and isinstance(command, str) and command.endswith(runner.PIPE_LINE):
        return {**args, "command": command.removesuffix(runner.PIPE_LINE)}
    return args


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


class Habits(Tools, Watch, Trigger, Guard, Display, Cockpit, Dream, Practice):
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
        # the dream and its practice
        self.practising: dict[str, dict] = {}  # draft key -> its row, refreshed from the ledger at most every 60 s
        self.practising_at: float | None = None  # monotonic; None: not read yet
        self.turn_keys: dict[str, list[str]] = {}  # session -> the keys seen in the turn that is running
        self.last_practice: dict[str, float] = {}  # draft key -> monotonic time of its last comparison
        self.practised_turn: dict[str, set] = {}  # session -> the draft keys already counted in the turn that is running
        self.dream_checked: float | None = None  # monotonic: when this process last looked at the daily rule
        self.loaded: set = set()
        self.platforms: dict[str, str] = {}  # session -> platform, 500 at most

    # ---- plumbing -------------------------------------------------------

    def off(self, file: bool = True) -> bool:
        """Habits steps aside, its tools apart: HABITS_OFF is set, to measure Hermes without habits. Hermes 0.23
        runs a `hermes -z` turn in its gateway and the plugin in its plugin host, which the variable does not
        reach: a file named off in the plugin's folder, touched in the last 10 minutes, does the same for every
        session (the bench uses it)."""
        if os.environ.get(OFF):
            return True
        if not file:
            return False
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

    def hook(self, callback, file: bool = True):
        """The hook, doing nothing while Habits is off. The guard (file=False) never steps aside: neither HABITS_OFF
        nor the off file switches it off. A model can start a `hermes` with the variable set and can write the
        file, and a switch within its reach must not open the way to the ledger; with Habits off, no habit runs and
        nothing is counted, and a habit's state is still the user's alone."""
        @functools.wraps(callback)
        def hooked(*args, **kwargs):
            return None if file and self.off() else callback(*args, **kwargs)
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

    def asked(self, key):
        """What the user set for `key`, or None when nothing is set (the plugin's own default then applies)."""
        try:
            return self.ctx.get_config(key, None)
        except Exception:
            return None

    def setting(self, key):
        if key in NUMBERS:  # a number the user set wins; else the caution row says
            kind = NUMBERS[key]
            try:
                value = self.asked(key)
                if value is not None and not isinstance(value, bool):
                    return kind(value)
            except (TypeError, ValueError):
                pass
            return self.caution()[key]
        if key == "learning":  # unset: an old `promote: auto` still means learning = auto
            value = self.choice(key)
            if self.asked(key) is None and self.choice("promote", "ask") == "auto":
                _warn_once("promote", "habits: the old setting promote: auto is read as learning: auto")
                return "auto"
            return value
        if key == "promote":  # the old name, now derived: auto exactly when learning is auto
            return "auto" if self.setting("learning") == "auto" else "ask"
        if key == "decision_model":  # unset: an old `backend` still picks the model (llm is the Hermes model)
            if self.asked(key) is None:
                old = self.choice("backend", "auto")
                return {"jev": "jev", "llm": "hermes"}.get(old, "auto")
            return self.choice(key)
        if key == "backend":
            return BACKEND_OF[self.setting("decision_model")]
        default = DEFAULTS[key]
        if isinstance(default, bool):  # a word it does not know keeps the default: never a feature off by a typo
            value = self.asked(key)
            if value is None:
                return default
            said = str(value).strip().lower() if not isinstance(value, bool) else value
            return value if isinstance(value, bool) else True if said in ("1", "true", "yes", "on") else \
                False if said in ("0", "false", "no", "off") else default
        return self.choice(key)

    def choice(self, key, default=None):
        """A setting of a closed list: a value outside it (a typo) is the default."""
        default = DEFAULTS[key] if default is None else default
        value = self.asked(key)
        if value is None:
            return default
        value = "off" if value is False else str(value).strip().lower()  # YAML 1.1 reads a bare off as false
        value = SPELLINGS.get((key, value), value)
        if value in CHOICES[key]:
            return value
        if key in STRICT:
            _warn_once(f"strict-{key}-{value}", "habits: the setting %s does not know %r; it is read as %s", key, value, STRICT[key])
            return STRICT[key]
        return default

    def caution(self) -> dict:
        """The numbers of the caution row the user picked (2.9)."""
        return ledgers.CAUTION[self.setting("caution")]

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
        """The caution row, learning, and the numbers the user set by hand, which win over the row."""
        return ledgers.rules_for(self.setting("caution"), self.setting("learning"),
                                 {k: self.setting(k) for k in NUMBERS if self.asked(k) is not None})

    def yes_given(self, habit) -> bool:
        """The user's own yes: this version is automatic and its last move there was the user's, in a chat. The
        rules' promotion is none, and a terminal is the model's too (ledger.YES), so neither counts."""
        if habit is None:
            return False
        try:
            row = self.ledger.info(habit.name)
            if not row or row.get("status") != "active" or row.get("version") != habit.version:
                return False
            last = self.ledger.history(habit.name, 1)
            return bool(last) and (last[0]["version"], last[0]["to"]) == (habit.version, "active") \
                and last[0]["by"] in ledgers.YES
        except Exception as exc:  # a ledger that cannot say: no yes
            logger.warning("habits: could not read the yes of %s: %s", getattr(habit, "name", "?"), exc)
            return False

    def flagged(self, habit, _depth: int = 0, _seen: frozenset = frozenset()) -> list[str]:
        """Every command of the reflex that Hermes would ask about or that deletes, uncut, in step order: what the
        yes card shows so the yes is informed. habit: a saved habit, or the reflex of a draft. A reflex it
        calls (a `use` step) runs under the same yes: its commands come too, as `name: command` (as many levels as a
        run goes)."""
        found = []
        graph_ = habit if isinstance(habit, dict) else getattr(habit, "graph", None) or {}
        nodes = graph_.get("nodes") if isinstance(graph_, dict) else None
        for node in (nodes or {}).values():
            if not isinstance(node, dict):
                continue
            args = node.get("args") if isinstance(node.get("args"), dict) else {}
            command = args.get("command") if node.get("do") == "terminal" else None
            if isinstance(command, str) and command not in found and (dangerous(command) or graph._deletes(command)):
                found.append(command)
            called = node.get("use")
            if isinstance(called, str) and called not in _seen and _depth + 1 < runner.MAX_DEPTH:
                sub = catalog.find(called)
                if sub is not None and sub.valid:
                    for inner in self.flagged(sub, _depth + 1, _seen | {called}):
                        line = f"{called}: {inner}"
                        if line not in found:
                            found.append(line)
        return found

    def allowances(self) -> dict:
        """What the switches let a graph hold, for the early checks (habit_save, a one-off run, `check`)."""
        return {"allow_llm_in_command": self.setting("model_text") == "quoted",
                "allow_delete": self.setting("permissions") == "hermes"}

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
        # decision_model hermes asks Hermes's model alone: no question goes to Jev. The old backend: llm still keeps
        # Jev behind it.
        alone = self.setting("decision_model") == "hermes" and self.asked("decision_model") is not None
        return deciders.build(self.setting("backend"), None if alone else self.secret("TYPESAFE_API_KEY"), self.complete)

    def tool(self, name: str, args: dict, approved: bool = False) -> str:
        refusal = refused(name, args or {}, approved=approved)
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
            own: Path | None = None, turn: bool = False) -> runner.Result:
        """keep: False for a measurement, which reads the memory of check: changed and never moves it. within: a
        measurement's folder (resolved): terminal commands start there and no write leaves it, but to run a script
        of own (the habit's assets/). memory: a
        measurement's own references for check: changed, moved in place, instead of the ledger's. turn: started
        inside a Hermes turn (habit_run): only then, and only for a habit with the user's yes, may Hermes's own
        approval rules decide a flagged or deleting command (permissions: hermes, 0.30)."""
        if "min_confidence" not in scenario:
            scenario = {**scenario, "min_confidence": self.setting("min_confidence")}
        if memory is None:
            memory = self.ledger.memory(habit.name) if habit else {}
        approved = bool(not dry and turn and habit is not None and self.setting("permissions") == "hermes"
                        and self.yes_given(habit))
        # A reflex the dream learned, called in a turn, starts where Hermes is (it was made of what Hermes did there);
        # any other reflex starts in the backend's home, the same from a chat, a cron job or a test.
        here = bool(turn and habit is not None and not dry and (self.ledger.info(habit.name) or {}).get("origin") == "learned")
        env = runner.Env(tool=functools.partial(self.tool, approved=approved), decider=self.decider(),
                         llm=self.complete, habits=self.subhabit, memory=memory, dry=dry, max_seconds=max_seconds,
                         deleting=approved, llm_in_commands=self.setting("model_text") == "quoted",
                         workdir=str(within) if within else (None if here else "~"),  # else each backend's own home
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
    for name in HOOKS:  # HABITS_OFF turns every hook except the guard off, the tools stay
        try:
            ctx.register_hook(name, habits.hook(getattr(habits, name if name.startswith("on_") else f"on_{name}"),
                                                        file=name != "pre_tool_call"))
        except Exception as exc:
            if name != "transform_tool_result":  # a Hermes older than the hook: the start on a skill is simply off
                raise
            _warn_once("transform", f"habits: this Hermes has no transform_tool_result hook: {exc}")
    ctx.register_command("habits", habits.command, description=say("menu", habits.lang()))
    ctx.register_cli_command("habits", "Habits: list, show, view, check, run, dream, yes, pause, resume, undo, "
                             "tick, rest, reset, cleanup", habits.cli_setup, habits.cli_handle)
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
