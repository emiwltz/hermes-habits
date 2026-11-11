"""Practice and the user's decisions: a reflex practices beside Hermes, the user says yes, no or try.

The dream (nursery.py) spots what comes back and drafts a small reflex. This module is the other half:

- practice: when Hermes does again an action a draft is made of, the draft is practiced beside it. A one-step
  read that only reads the local machine really runs, in a background thread, and its output is compared with
  Hermes's in memory (only the verdict is kept, never an output). Anything else (an act, the web, an unknown
  program) is only planned: the match then says "Hermes did this exact action again", nothing more.
- the cards in /habits: yes, no, try.
- learn: the user's yes saves the draft through the same path as habit_save (origin learned) and makes it
  automatic, bound to the exact form the card showed.
- change and rest of a saved learned reflex, on the user's yes.

Every Hermes hook that reaches this module swallows its errors: the worst case is a draft that does not practice.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

from . import catalog, dream, graph as graphs, runner, tools
from .texts import say

logger = logging.getLogger(__name__)

NO_DAYS = 30  # a no holds this long
MAX_PRACTISING = 3
STAMP = "practising-since"  # the file whose time says a draft began to practice (in the plugin's data folder)
REFRESH = 60.0  # seconds: how stale the practicing drafts held in memory may be
RUN_EVERY = 600.0  # seconds: one real run of a draft per this
DRAFT_STATES = ("spotted", "practising", "ready")  # where a no can still catch a draft
LEARNED = "learned"
RANK = {"learn": 0, "change": 1, "rest": 1, "try": 2}  # what waits, in the order that matters: a ready reflex's yes first


def _keylike(text: str) -> bool:
    """An output that looks like it holds a key: nothing of it is compared or kept, and the draft goes."""
    return any(pattern.search(text) for pattern in dream.KEYLIKE)


# Programs whose command does not depend on the folder it starts in, when every other word is an option or a full path.
CWD_FREE = frozenset({"df", "uptime", "date", "whoami", "hostname", "uname", "sw_vers", "free", "vm_stat", "who", "id",
                      "w", "ps", "top", "lscpu", "nproc", "arch", "locale", "cal"})
STAGED_HOURS = 24  # a yes whose skill files were never written goes back to ready after this


def _places(scenario) -> list[tuple[str, str]]:
    """(command, folder) of each terminal step of a reflex, in order; folder is "" when the step names none."""
    nodes = scenario.get("nodes") if isinstance(scenario, dict) else None
    out = []
    for node in (nodes or {}).values():
        if isinstance(node, dict) and node.get("do") == "terminal" and isinstance(node.get("args"), dict):
            command, folder = node["args"].get("command"), node["args"].get("workdir")
            if isinstance(command, str):
                out.append((command, folder if isinstance(folder, str) else ""))
    return out


def _full(word: str) -> bool:
    """A full path, or one that starts at the home folder: it names the same place from any folder."""
    return word.strip("'\"").startswith(("/", "~", "$HOME"))


def _git_folder(words: list[str]) -> bool:
    """`git -C <full path> ...`: git works in the folder it is given, whatever folder it starts in."""
    i = 1
    while i < len(words) and words[i].startswith("-"):
        if words[i] == "-C":
            return i + 1 < len(words) and _full(words[i + 1])
        i += 1
    return False


def _depends_on_folder(command: str) -> bool:
    """A command that may act on the folder it starts in. It does not when it names where it works (`git -C <full
    path> ...`, `cd <full path> && ...`), when every path it is given is a full one, or when it is a known
    folder-free program whose other words are options or full paths. A relative path, or no path at all (`ls`,
    `git status`, `grep -r x .`), depends on it."""
    anchored = False
    for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command.strip()):
        words = part.split()
        if not words:
            return True
        program = words[0].rsplit("/", 1)[-1]
        if program == "cd":
            if len(words) == 2 and _full(words[1]):
                anchored = True  # what follows works in the folder it names
                continue
            return True
        if anchored or (program == "git" and _git_folder(words)):
            continue
        rest = words[1:]
        if program in CWD_FREE:
            if any(not (w.startswith(("-", "/", "~", "$HOME")) or w.isdigit()) for w in rest):
                return True
            continue
        paths = [w for w in rest if not (w.startswith("-") or w.isdigit())]
        if not paths or not all(_full(w) for w in paths):
            return True
    return False


def _named_folders(command: str) -> list[str]:
    """The full paths a command names (what it works in or on), the home folder spelled out."""
    found = []
    for word in command.split():
        word = word.strip("'\"")
        if word.startswith("$HOME"):
            word = "~" + word[len("$HOME"):]
        if word.startswith(("/", "~")):
            found.append(word)
    return found


def _dedup_answer(value) -> bool:
    """Hermes's own answer to a repeated read of an unchanged file: the stub {"status": "unchanged", "dedup": true,
    "content_returned": false}, or the block that follows two stubs (tools/file_tools.py). Not the file's text."""
    data = value
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except ValueError:  # the text a step prints for it (key: value lines), or the block's own words
            return bool(re.search(r"^dedup: true$", value, re.M) or
                        (re.search(r"^status: unchanged$", value, re.M) and
                         re.search(r"^content_returned: false$", value, re.M))
                        or value.startswith("BLOCKED: You have called read_file"))
    if not isinstance(data, dict):
        return False
    if data.get("dedup") is True or (data.get("status") == "unchanged" and data.get("content_returned") is False):
        return True
    return "already_read" in data and str(data.get("error") or "").startswith("BLOCKED")


def _same_folder(a: str, b: str) -> bool:
    norm = lambda p: os.path.normpath(os.path.expanduser(p)).rstrip("/") or "/"
    return norm(a) == norm(b)


def _not_done(status, result) -> bool:
    """Hermes did not do the action: it was blocked, denied, pending approval, or could not run (exit 126, 127). A plain
    non-zero exit that ran and printed something is a result like any other: Hermes tells the hook "error" for it, and
    a command that normally exits non-zero (grep with no match, diff) must stay learnable."""
    if status in ("ok", None, ""):
        return dream.failed({"content": result})
    if status != "error":
        return True
    data = result
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except ValueError:
            return True
    code = data.get("exit_code") if isinstance(data, dict) else None
    if not isinstance(code, int) or isinstance(code, bool):
        return True  # an error that is no exit code: the tool itself failed
    return dream.failed({"content": result})


def _hermes_folder(args: dict, result) -> str | None:
    """The folder Hermes ran the call in: its own workdir, else the one its result reports; None when unknown."""
    here = args.get("workdir") if isinstance(args.get("workdir"), str) else None
    if not (here or "").strip():
        data = result
        if isinstance(data, (str, bytes)):
            try:
                data = json.loads(data)
            except ValueError:
                data = None
        cwd = data.get("cwd") if isinstance(data, dict) else None
        here = cwd if isinstance(cwd, str) else None
    return (here or "").strip() or None


def _broad(folder: str) -> bool:
    """The home folder or the root: where a recursive command would walk everything, secrets included."""
    norm = os.path.normpath(os.path.expanduser(folder)).rstrip("/") or "/"
    return norm == "/" or norm == os.path.normpath(os.path.expanduser("~")).rstrip("/")


ALWAYS_RECURSIVE = frozenset({"find", "du", "tree", "rg", "ag", "fd", "ack"})


def _recursive(command: str) -> bool:
    """A command that walks the folders under the one it starts in: find, du, tree, rg, grep -r, ls -R."""
    for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command.strip()):
        words = part.split()
        if not words:
            continue
        program = words[0].rsplit("/", 1)[-1]
        if program in ALWAYS_RECURSIVE:
            return True
        if program in ("grep", "egrep", "fgrep", "ls", "cp", "chmod", "chown"):
            for w in words[1:]:
                if w in ("--recursive", "--dereference-recursive"):
                    return True
                if w.startswith("-") and not w.startswith("--") and ("R" in w or ("r" in w and program != "ls")):
                    return True
    return False


def _what_runs(raw: str) -> str:
    """What a call runs, to tell whether a draft's action came back. A terminal call is its command, and whether it
    runs in the background: its folder is compared on its own, and an argument that changes nothing of what runs (a
    timeout) is no difference. Any other tool is its whole arguments."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    args = data.get("args") if isinstance(data, dict) else None
    if isinstance(args, dict) and data.get("tool") == "terminal" and isinstance(args.get("command"), str):
        return dream.raw_of("terminal", {"command": args["command"], "background": bool(args.get("background"))})
    return raw


def _in_folder(scenario: dict, folder: str | None) -> dict:
    """A copy of the reflex whose terminal steps start in `folder` (in no folder at all when it is None)."""
    copy = json.loads(json.dumps(scenario))
    for node in (copy.get("nodes") or {}).values():
        if isinstance(node, dict) and node.get("do") == "terminal" and isinstance(node.get("args"), dict):
            if folder:
                node["args"]["workdir"] = folder
            else:
                node["args"].pop("workdir", None)
    return copy


def _subsequence(parts, keys) -> bool:
    it = iter(keys)
    return all(part in it for part in parts)


class Practice:
    """Mixed into Habits (plugin.py): practice, learn, the cards and their answers."""

    # ---- small helpers -----------------------------------------------------------------------------------

    def _rule(self, name: str) -> int:
        return int(getattr(self.rules(), name))

    def _say(self, key: str, **fields) -> str:
        return say(key, self.lang(), **fields)

    @staticmethod
    def _steps(d: dict) -> list:
        """A draft's actions, as dream.shown, describe and procedure take them."""
        args = d.get("args") if isinstance(d.get("args"), dict) else {}
        if not d.get("parts"):
            return [dream.Action(d["key"], d["tool"], args, d["raw"])]
        steps = []  # a grown draft: its `do` steps and the reflexes it calls (`use`), in order
        nodes = d["graph"].get("nodes") if isinstance(d.get("graph"), dict) else {}
        for node in (nodes or {}).values():
            if isinstance(node, dict) and isinstance(node.get("use"), str):
                called = {"name": node["use"]}
                steps.append(dream.Action(f"reflex:{node['use']}", "habit_run", called, dream.raw_of("habit_run", called)))
            elif isinstance(node, dict) and "do" in node:
                step_args = node.get("args") if isinstance(node.get("args"), dict) else {}
                steps.append(dream.Action(dream.key_of(node["do"], step_args) or "", node["do"], step_args,
                                          dream.raw_of(node["do"], step_args)))
        return steps

    def _holds(self, habit, scenario: dict, description: str) -> bool:
        """The saved habit is exactly what was shown: this reflex and this description, nothing the model slipped in."""
        try:
            normal = graphs.load(tools.loaded(scenario)[0])
        except Exception:
            normal = scenario
        return bool(habit is not None and habit.valid and habit.description == description and habit.graph == normal)

    def _problems(self, scenario: dict) -> list[str]:
        return tools.problems_of(scenario)

    # ---- practicing -----------------------------------------------------------------------------------------

    def _practising_stamp(self):
        """When a draft last began to practice, as a file the dream (this process or a terminal's) touches: one stat, no
        ledger read. None when no draft has begun to practice here."""
        try:
            return (self.home() / STAMP).stat().st_mtime_ns
        except Exception:
            return None

    def practising_changed(self) -> None:
        """A draft begins to practice: the hooks of every process that shares this ledger read the drafts again on their
        next call, not up to REFRESH seconds later (a dream typed in a terminal counts at once)."""
        try:
            path = self.home() / STAMP
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(time.time_ns()), encoding="utf-8")
        except Exception as exc:
            logger.warning("habits: could not note that a draft practices: %s", exc)

    def refresh_practising(self):
        """Re-read the drafts that practice (at most every REFRESH seconds on the hook, at once when a draft began to
        practice, and after a decision)."""
        self.practising_stamp = self._practising_stamp()
        try:
            self.practising = {row["key"]: row for row in self.ledger.drafts(("practising",))}
        except Exception as exc:
            logger.warning("habits: could not read the drafts that practice: %s", exc)
        self.practising_at = time.monotonic()

    def practise(self, session: str, tool: str, args: dict, result, status="ok") -> None:
        """post_tool_call, for every call. Cheap when nothing practices: no ledger read, one dictionary look-up."""
        try:
            self._practise(str(session or ""), str(tool or ""), args if isinstance(args, dict) else {}, result, status)
        except Exception as exc:
            logger.warning("habits: practice skipped a call: %s", exc)
        return None

    def _practise(self, session: str, tool: str, args: dict, result, status="ok"):
        if self._learning() == "off" or self.paused():
            return
        if (self.practising_at is None or time.monotonic() - self.practising_at > REFRESH
                or self._practising_stamp() != getattr(self, "practising_stamp", None)):
            self.refresh_practising()
        if not self.practising:
            return
        if _not_done(status, result):
            return  # Hermes did not do it (blocked, denied, pending approval, could not run): it did not come back
        key = dream.key_of(tool, args)
        if key is None:
            return
        if any(d.get("parts") for d in self.practising.values()):  # a grown draft is judged at the end of the turn
            keys = self.turn_keys.setdefault(session, [])
            if len(keys) < 500:
                keys.append(dream.reflex_call(tool, args) or key)
        d = self.practising.get(key)
        if d is None or d.get("parts"):
            return
        once = self.practised_turn.setdefault(session, set())  # an action counts once a turn
        if key in once:
            return
        if len(self.practised_turn) > 200:
            self.practised_turn.clear()
        once.add(key)
        self.background(self._practised, session, key, d, dream.raw_of(tool, args), result, args)

    def _practised(self, session: str, key: str, d: dict, raw: str, result, args: dict | None = None):
        """The slow half, on a background thread: the verdict of one call, the ledger's count, ready or rest."""
        try:
            kind = "read" if d.get("reads") else "plan"
            args = args or {}
            scenario = d["graph"] if isinstance(d.get("graph"), dict) else json.loads(d["graph"])
            here = _hermes_folder(args, result)
            commands = [c for c, _ in _places(scenario)]
            depends = any(_depends_on_folder(c) for c in commands)
            fixed = [folder for _, folder in _places(scenario) if folder]
            if _what_runs(raw) != _what_runs(d["raw"]) or (depends and here is not None and fixed and
                                   not all(_same_folder(here, f) for f in fixed)):
                # another form of the same action, or the same command in another folder than the reflex's: counted,
                # not compared, and never a match
                self.ledger.practised(key, kind, "seen", session)
                return
            if not d.get("reads"):  # an act, the web, an MCP tool, an unknown program: planned, never run
                self._came(session, key, d)
                return
            # A read really runs, and only where Hermes ran it: never in a folder guessed or taken from the history.
            # A read that cannot be run safely beside Hermes is not a dead end: it is counted, like an act.
            if (depends and here is None) or _dedup_answer(result):
                self._came(session, key, d)  # came back, folder unknown (or Hermes's own answer was a stub): not run
                return
            walks = _broad(here) if depends else any(_broad(p) for c in commands for p in _named_folders(c))
            if (here is not None or not depends) and walks and any(_recursive(c) for c in commands):
                self._came(session, key, d)  # a walk of the whole home folder or root: not run
                return
            verdict = self._run_beside(key, d, result, _in_folder(scenario, here if depends else None), here or "")
            if verdict is None:
                return
            if verdict == "gone":
                self.ledger.move_draft(key, "gone", ("practising",))
                self.practising.pop(key, None)
                return
            if verdict == "came":  # the read ran but nothing could be compared (a stub in place of the file's text)
                self._came(session, key, d)
                return
            self._after(key, d, self.ledger.practised(key, "read", verdict, session))
        except Exception as exc:
            logger.warning("habits: could not practice %s: %s", d.get("name"), exc)

    def _came(self, session: str, key: str, d: dict) -> None:
        """The action came back and nothing was compared: counted like an act (kind plan), so a draft never waits for a
        comparison that cannot happen. Its line and its card then say "came back", never "the same"."""
        self._after(key, d, self.ledger.practised(key, "plan", "same", session))

    def _came_back(self, row: dict) -> bool:
        """A draft of a read that was counted without a comparison at least once: its numbers say "came back"."""
        try:
            return any(r.get("kind") == "plan" for r in self.ledger.practice(row["key"], 50))
        except Exception:
            return False

    def _run_beside(self, key: str, d: dict, result, scenario: dict, folder: str = "") -> str | None:
        """Run the draft dry (its read really runs) and compare with what Hermes got. None: not run now (too soon,
        busy). "gone": an output looks like a key. Both texts are dropped here; only the verdict leaves.

        It dispatches like any plugin call (Hermes's terminal tool gets no task id from ctx.dispatch_tool): a command
        that only reads, so sharing Hermes's shell does it no harm."""
        last = self.last_practice.get(key)
        if last is not None and time.monotonic() - last < RUN_EVERY:
            return None
        for command, _ in _places(scenario):  # the exclusions are asked again for the folder it will really run in
            if dream.excluded("terminal", {"command": command}, self.blocked, cwd=folder):
                return "gone"
        with self.held(d["name"]) as free:
            if not free:
                return None
            self.last_practice[key] = time.monotonic()
            run = self.run(None, scenario, dry=True, max_seconds=10, keep=False)
        shown, hermes_error = runner._output(result)
        if run.status != "end" or run.failed:
            # "error" is the reflex failing where Hermes succeeded. When Hermes's own call failed too, the two
            # failures compare like any two results (a command that exits non-zero for both is not at fault).
            failure = next((s.get("error") for s in reversed(run.steps) if s.get("error")), None)
            if _dedup_answer(failure):
                return "came"  # Hermes's per-task dedup answered the reflex's read with its stub or its block
            if hermes_error is None or not failure:
                return "error"
            ours, theirs = str(failure), str(hermes_error)
        else:
            # The draft ends on its output (`end: "{out}"`), so its message is that output, cut at the message's
            # cap: Hermes's output is cut the same way, and a long output compares on what both show.
            ours = run.message
            theirs = runner._short(shown, runner.MESSAGE_LIMIT)
        if _dedup_answer(ours) or _dedup_answer(theirs):
            return "came"  # a stub stands in for the file's text on one side: nothing to compare
        if _keylike(ours) or _keylike(theirs):
            return "gone"
        return dream.compare(theirs, ours)

    def _compares(self, d: dict) -> bool:
        """The outputs of this draft are run and compared: a read draft of one step whose comparisons were all made. A
        grown draft never is (only its sequence is seen), and a read counted without a comparison is not either: they
        never say "the same"."""
        return bool(d.get("reads")) and not d.get("parts") and not self._came_back(d)

    def _asks_the_user(self, d: dict) -> bool:
        """A reflex that holds a deleting command or one Hermes would ask approval for always waits for the user's yes,
        whatever the setting (what the yes card lists as flagged). When in doubt: it waits."""
        try:
            scenario = d["graph"] if isinstance(d["graph"], dict) else json.loads(d["graph"])
            return bool(self.flagged(scenario))
        except Exception:
            return True

    def _after(self, key: str, d: dict, row: dict | None):
        """Ready when enough matches, rest when it keeps differing (3.6 step 7)."""
        if not row:
            return
        if row["same"] + row["shape"] >= self._rule("ready_matches"):
            if not self.ledger.move_draft(key, "ready", ("practising",)):
                return
            self.practising.pop(key, None)
            if self._learning() == "auto" and not self._asks_the_user(d):  # every ready draft, but a risky one
                done, _ = self.learn(key, d["raw"], by="rules")
                if done:
                    habit = catalog.find(d["name"])
                    self.ledger.tell(d["name"], "dream_learned", version=habit.version if habit else "")
                    return
            now = self.ledger.draft(key) or d  # learn may have given the draft another name
            self.ledger.tell(now["name"], "dream_learn")
        elif row.get("streak", 0) >= self._rule("rest_differs"):
            if self.ledger.move_draft(key, "rest", ("practising",)):
                self.practising.pop(key, None)

    def practise_turn(self, session: str) -> None:
        """post_llm_call: the verdict of the grown drafts, by the keys seen in the turn that just ended."""
        try:
            self.practised_turn.pop(session, None)
            keys = self.turn_keys.pop(session, [])
            for key, d in list(self.practising.items()):
                parts = d.get("parts") or []
                if not parts or parts[0] not in keys:
                    continue
                verdict = "same" if _subsequence(parts, keys) else "differs"
                self._after(key, d, self.ledger.practised(key, "turn", verdict, session))
        except Exception as exc:
            logger.warning("habits: could not judge the turn's drafts: %s", exc)
        return None

    # ---- the yes ------------------------------------------------------------------------------------------------

    def learn(self, key: str, raw: str, by: str = "user") -> tuple[bool, str]:
        """Save a ready draft as a learned reflex and make it automatic, bound to the form the card showed."""
        d = self.ledger.draft(key)
        if d is None or d.get("state") != "ready":
            return False, self._say("unknown", name=(d or {}).get("name") or key)
        name = d["name"]
        if raw != d["raw"]:
            return False, self._say("after_changed_since", name=name)
        scenario = d["graph"] if isinstance(d["graph"], dict) else json.loads(d["graph"])
        problems = self._problems(scenario)
        if problems:  # it can never be saved as it is: it rests, and the user is told why (no page knows a draft)
            self.ledger.move_draft(key, "rest", ("ready",))
            return False, self._say("draft_invalid", name=name, why="; ".join(problems[:2]))
        steps = self._steps(d)
        description = dream.describe(steps, d.get("moments"))
        if self._name_taken(name, scenario, description):  # the yes never overwrites another habit or skill
            return False, self._rename(d, steps)
        if not self.ledger.move_draft(key, "asked", ("ready",)):
            return False, self._say("after_changed_since", name=name)
        answer = self.save(name, description, self._procedure(steps, scenario), scenario, origin=LEARNED)
        status = answer.get("status")
        if status == "staged":  # the skill's writes wait for the user's approval: finish_staged ends it
            if by != "user":  # the rules never wait on files: the user's yes comes first
                self.ledger.move_draft(key, "ready", ("asked",))
                return False, self._say("after_learned_staged", name=name)
            return True, self._say("after_learned_staged", name=name)
        if status != "saved":
            self.ledger.move_draft(key, "ready", ("asked",))
            return False, str(answer.get("error") or "; ".join(map(str, answer.get("issues") or [])) or status)
        habit = catalog.find(name)
        if not self._holds(habit, scenario, description):
            self.ledger.move_draft(key, "ready", ("asked",))
            return False, self._say("after_changed_since", name=name)
        self.ledger.status(habit.name, habit.version)  # the version the catalog reads, on record
        if not self.ledger.approve(habit.name, habit.version, by=by) and not self._active(habit):
            self.ledger.move_draft(key, "ready", ("asked",))
            return False, self._say("after_changed_since", name=name)
        self.ledger.move_draft(key, "saved", ("asked",), habit=habit.name)
        return True, self._say("after_learned", name=habit.name)

    def _active(self, habit) -> bool:
        """This exact version is already automatic (a reflex deleted by hand and made again): nothing left to approve."""
        info = self.ledger.info(habit.name)
        return bool(info and info.get("status") == "active" and info.get("version") == habit.version)

    def _name_taken(self, name: str, scenario: dict, description: str) -> bool:
        """The name belongs to another habit or to a Hermes skill. The same reflex saved already is no clash."""
        habit = catalog.find(name)
        if habit is not None and habit.origin != "example":  # a shipped example is shadowed by a saved habit (save)
            return not self._holds(habit, scenario, description)
        folder, ours = catalog.skill_folder(name)
        return folder is not None and not ours

    def _rename(self, d: dict, steps: list) -> str:
        """Give a draft whose name is taken another one, free among the drafts, the habits and the Hermes skills. It
        waits for a new yes under the new name, which its card shows."""
        name = d["name"]
        taken = {r["name"] for r in self.ledger.drafts() if r.get("name")} | {h.name for h in catalog.all_habits()}
        for _ in range(50):
            new = dream.draft_name(steps, taken)
            folder, ours = catalog.skill_folder(new)
            if folder is None or ours:
                if self.ledger.rename_draft(d["key"], new, ("ready",)):
                    return self._say("after_renamed", name=name, new=new)
                break
            taken.add(new)
        return self._say("after_changed_since", name=name)

    def _folder_lines(self, scenario, fixed_key: str | None, here_key: str, lang: str) -> list[str]:
        """Where the reflex runs, when its commands depend on it: the folder each step names, and "the folder Hermes
        is in" for a step that names none. A card passes no fixed_key: it shows each step's folder beside its command
        (dream.shown), so only the sentence about a step that names none is left to say."""
        fixed, here = [], False
        for command, folder in _places(scenario):
            if folder:
                if folder not in fixed:
                    fixed.append(folder)
            elif _depends_on_folder(command):
                here = True
        lines = [say(fixed_key, lang, folder=", ".join(fixed))] if fixed and fixed_key else []
        return lines + ([say(here_key, lang)] if here else [])

    def _procedure(self, steps: list, scenario: dict) -> str:
        """The skill's procedure text, with where the reflex runs (the model reads it to know)."""
        lines = self._folder_lines(scenario, "procedure_folder", "procedure_folder_here", "en")
        return dream.procedure(steps) + ("\n\n" + " ".join(lines) if lines else "")

    def finish_staged(self) -> None:
        """A yes whose skill writes were staged: once they are approved, make the reflex automatic, but only if the
        habit on disk is exactly the draft's. Anything else (the model saved its own under the name) goes back to
        ready, and so does a yes whose files were never written (a day after)."""
        for d in self.ledger.drafts(("asked",)):
            try:
                habit = catalog.find(d["name"])
                if habit is None or habit.origin != "skill":
                    if (habit is None or habit.origin == "example") and self.ledger.clock() - (d.get("since") or 0) > STAGED_HOURS * 3600:
                        self.ledger.move_draft(d["key"], "ready", ("asked",))
                    continue  # not written yet (a shipped example of that name is not it)
                scenario = d["graph"] if isinstance(d["graph"], dict) else json.loads(d["graph"])
                if not self._holds(habit, scenario, dream.describe(self._steps(d), d.get("moments"))):
                    self.ledger.move_draft(d["key"], "ready", ("asked",))
                    continue
                info = self.ledger.info(habit.name)
                if info is None or info.get("origin") != LEARNED:  # a look at the habit may have made its row first
                    if not (info and info.get("status") == "active" and info.get("version") == habit.version):
                        self.ledger.adopt(habit.name, habit.version, LEARNED)
                self.ledger.status(habit.name, habit.version)
                if self.ledger.approve(habit.name, habit.version, by="user") or self._active(habit):
                    self.ledger.move_draft(d["key"], "saved", ("asked",), habit=habit.name)
            except Exception as exc:
                logger.warning("habits: could not finish the staged yes of %s: %s", d.get("name"), exc)

    # ---- the cards --------------------------------------------------------------------------------------------------

    def _shown_raw(self, raw: str) -> str:
        """The exact command or call of a raw form, uncut."""
        try:
            data = json.loads(raw)
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            return dream.shown([dream.Action(dream.key_of(data["tool"], args) or "", data["tool"], args, raw)])
        except Exception:
            return str(raw)

    def _yes_no(self):
        return [{"value": "yes", "label": self._say("yes")}, {"value": "no", "label": self._say("no")}]

    @staticmethod
    def _acts(steps) -> bool:
        """Some part of the draft acts: a step that is no local read, or a reflex that does more than read."""
        from .trigger import read_only
        for a in steps:
            if a.key.startswith("reflex:"):
                habit = catalog.find(a.key[len("reflex:"):])
                if habit is None or not habit.valid or not read_only(habit.graph):
                    return True
            elif not dream.local_read(a.tool, a.args):
                return True
        return False

    def _draft_card(self, d: dict, try_card: bool) -> dict:
        steps = self._steps(d)
        # each command with the folder it will run in, in the card's language, one after the other on one line
        action = dream.shown(steps, str(d.get("cwd") or ""), self.lang(), " then ")
        fields = dict(name=d["name"], action=action, turns=d.get("turns") or 0, days=d.get("days") or 0,
                      moments=self.moments_text(self._moments(d.get("moments")), self.lang()))
        if try_card:
            question = self._say("card_try", **{k: fields[k] for k in ("action", "turns", "days", "moments")})
            choices = [{"value": "try", "label": self._say("try")}, {"value": "no", "label": self._say("no")}]
        else:
            matches = (d.get("same") or 0) + (d.get("shape") or 0)
            if self._compares(d):
                compared = matches + (d.get("differs") or 0) + (d.get("errors") or 0)
                question = self._say("card_learn", matches=matches, compared=compared, **fields)
            elif d.get("reads") and not d.get("parts"):  # a read that came back, not run beside Hermes: nothing compared
                question = self._say("card_learn_came", matches=matches, **fields)
            elif d.get("reads") or not self._acts(steps):  # a grown draft that only reads: only its sequence came back
                question = self._say("card_learn_grown", matches=matches, **fields)
            else:
                question = self._say("card_learn_plan", matches=matches, **fields)
            choices = self._yes_no()
        scenario = d["graph"] if isinstance(d["graph"], dict) else json.loads(d["graph"])
        if not try_card:  # where it will run, before the yes
            question += "".join("\n" + line for line in
                                self._folder_lines(scenario, None, "card_folder_here", self.lang()))
        flagged = self.flagged(scenario)
        if flagged:
            question += "\n" + self._say("card_flagged", commands=", ".join(f"`{c}`" for c in flagged))
        return {"id": f"d:{d['key']}", "kind": "try" if try_card else "learn", "name": d["name"],
                "question": question, "choices": choices, "raw": d["raw"], "since": d.get("since") or 0}

    def _proposal_card(self, p: dict) -> dict | None:
        detail, name = p.get("detail") or {}, p["subject"]
        if p["kind"] == "rest":
            question = self._say("card_rest_quiet", name=name, quiet=detail.get("quiet", 0))
            raw = ""
        elif p["kind"] == "change":
            old = next((d for d in self.ledger.drafts(("saved",)) if d.get("habit") == name), None)
            action = dream.shown(self._steps(old), str(old.get("cwd") or ""), self.lang(), " then ") if old else name
            raw = detail.get("raw") or ""
            question = self._say("card_change", name=name, sibling=self._shown_raw(raw), turns=detail.get("turns", 0),
                                 days=detail.get("days", 0), action=action, quiet=detail.get("quiet", 0))
            if old:  # the new form runs where the old one did
                question += "".join("\n" + line for line in self._folder_lines(
                    old["graph"] if isinstance(old.get("graph"), dict) else {}, None, "card_folder_here", self.lang()))
        else:
            return None
        return {"id": f"p:{p['id']}", "kind": p["kind"], "name": name, "question": question,
                "choices": self._yes_no(), "raw": raw, "since": p.get("at") or 0}

    def dream_cards(self) -> list[dict]:
        """The decisions the dream waits for, a ready draft before a change or a rest before a try, each oldest first: ready drafts (and, when learning is suggest, spotted ones
        as try cards) and open change and rest proposals."""
        try:
            self.finish_staged()  # a yes whose files were approved takes effect now, not at the next dream
        except Exception as exc:
            logger.warning("habits: could not finish a staged yes: %s", exc)
        cards = [self._draft_card(d, False) for d in self.ledger.drafts(("ready",))]
        if self._learning() == "suggest":
            cards += [self._draft_card(d, True) for d in self.ledger.drafts(("spotted",))]
        for p in self.ledger.proposals():
            if not self._still_true(p):  # a card that stopped being true is withdrawn before it is shown
                self.ledger.decide(p["id"], "gone", "rules")
                self.ledger.withdraw_news(p["subject"], ("dream_proposal",))
                continue
            card = self._proposal_card(p)
            if card:
                cards.append(card)
        return sorted(cards, key=lambda c: (RANK.get(c["kind"], 1), c["since"]))

    def _still_true(self, p: dict) -> bool:
        """A proposal about a reflex that is gone, or (a rest) that ran since it was made, no longer holds."""
        habit = catalog.find(p["subject"])
        if habit is None:
            return False
        if p["kind"] == "rest":
            runs = self.ledger.runs(habit.name, 1)
            return not (runs and (runs[0].get("at") or 0) > (p.get("at") or 0))
        return True

    def excluded_step(self, steps) -> str:
        """Why one of these actions is never a reflex (a secret, noise, a deleting command, the plugin's own
        settings...), or "". The dream never drafts such an action; this is the second look before a try or a change."""
        for step in steps:
            if str(step.key).startswith("reflex:"):  # a step that calls a reflex of its own
                continue
            try:
                why = dream.excluded(step.tool, step.args, self.blocked)
            except Exception:
                why = "unreadable"
            if why:
                return why
        return ""

    def draft_still_open(self, name: str) -> dict | None:
        """The draft of that name a no can still catch (spotted, practicing or ready), else None."""
        try:
            return next((d for d in self.ledger.drafts(DRAFT_STATES) if d.get("name") == name), None)
        except Exception as exc:
            logger.warning("habits: could not look for the draft %s: %s", name, exc)
            return None

    def dream_card_named(self, name: str) -> dict | None:
        return next((c for c in self.dream_cards() if c["name"] == name), None)

    # ---- the answers --------------------------------------------------------------------------------------------

    def dream_decide(self, card_id: str, picked: str, by: str = "user", raw: str | None = None) -> str:
        """yes, no or try on a card. raw: the form the card showed (a typed answer shows none: the draft's own,
        which is frozen once it is past spotted)."""
        kind, _, ref = str(card_id).partition(":")
        if kind == "d":
            return self._decide_draft(ref, picked, by, raw)
        if kind == "p":
            return self._decide_proposal(ref, picked, by, raw)
        return self._say("nothing_waits")

    def _decide_draft(self, key: str, picked: str, by: str, raw: str | None) -> str:
        d = self.ledger.draft(key)
        if d is None:
            return self._say("nothing_waits")
        name = d["name"]
        if picked == "yes":
            return self.learn(key, d["raw"] if raw is None else raw, by=by)[1]
        if picked == "no":
            until = self.ledger.clock() + NO_DAYS * 86400
            self.ledger.move_draft(key, "no", DRAFT_STATES, no_until=until)
            self.ledger.withdraw_news(name, ("dream_learn",))  # "practiced well" is not true to tell after a no
            self.practising.pop(key, None)
            return self._say("after_no", name=name, days=NO_DAYS)
        if picked == "try":
            if self.excluded_step(self._steps(d)):  # never practice what the dream must not draft
                self.ledger.move_draft(key, "gone", ("spotted",))
                return self._say("nothing_waits")
            if len(self.ledger.drafts(("practising",))) >= MAX_PRACTISING:
                return self._say("try_full", name=name)
            if self.ledger.move_draft(key, "practising", ("spotted",)):
                self.practising_changed()
                self.refresh_practising()
                return self._say("after_try", name=name)
        return self._say("nothing_waits")

    def _decide_proposal(self, ref: str, picked: str, by: str, raw: str | None) -> str:
        proposal = next((p for p in self.ledger.proposals() if str(p["id"]) == str(ref)), None)
        if proposal is None:
            return self._say("nothing_waits")
        name, detail = proposal["subject"], proposal.get("detail") or {}
        if picked == "no":
            self.ledger.decide(proposal["id"], "no", by)
            self.ledger.withdraw_news(name, ("dream_proposal",))
            return self._say("after_no", name=name, days=NO_DAYS)
        if picked != "yes":
            return self._say("nothing_waits")
        if proposal["kind"] == "rest":
            self.ledger.decide(proposal["id"], "yes", by)
            self.ledger.withdraw_news(name, ("dream_proposal",))
            return self.rest(name)
        return self._change(proposal, name, detail, by, raw)

    def _change(self, proposal: dict, name: str, detail: dict, by: str, raw: str | None) -> str:
        """Change: the reflex is saved again with the sibling's action, a new version the user's yes makes automatic."""
        shown = detail.get("raw") or ""
        if raw is not None and raw != shown:
            return self._say("after_changed_since", name=name)
        try:
            data = json.loads(shown)
            args = data.get("args") if isinstance(data.get("args"), dict) else {}
            action = dream.Action(detail.get("sibling") or dream.key_of(data["tool"], args) or "", data["tool"], args, shown)
        except Exception:
            return self._say("after_changed_since", name=name)
        if self.excluded_step([action]):  # a change never brings in an action the dream must not draft
            return self._say("after_changed_since", name=name)
        old = next((d for d in self.ledger.drafts(("saved",)) if d.get("habit") == name), None)
        scenario = dream.draft_reflex([action], (old or {}).get("cwd") or "")
        problems = self._problems(scenario)
        if problems:
            return self._say("draft_invalid", name=name, why="; ".join(problems[:2]))
        description = dream.describe([action])
        answer = self.save(name, description, self._procedure([action], scenario), scenario, origin=LEARNED)
        if answer.get("status") == "staged":  # the files wait for the user's approval: ask again once they are written
            return self._say("after_learned_staged", name=name)
        if answer.get("status") != "saved":
            return str(answer.get("error") or "; ".join(map(str, answer.get("issues") or [])) or answer.get("status"))
        habit = catalog.find(name)
        if not self._holds(habit, scenario, description):
            return self._say("after_changed_since", name=name)
        self.ledger.status(habit.name, habit.version)
        if not self.ledger.approve(habit.name, habit.version, by=by):
            return self._say("after_changed_since", name=name)
        self.ledger.decide(proposal["id"], "yes", by)
        if old:
            self.ledger.move_draft(old["key"], "gone", ("saved",))
        new = self.ledger.draft(action.key)
        if new and new.get("state") in DRAFT_STATES:
            self.ledger.move_draft(action.key, "saved", DRAFT_STATES, habit=name)
        return self._say("after_changed", name=name, action=self._shown_raw(shown))
