"""The guard on habits, as a pre_tool_call hook: the wall, a new habit through habit_save only, and no self-approval.

- The wall: a habit's SKILL.md, assets/HABIT.yaml and assets/LEARNED.md (a file older versions wrote) change through
  habit_save only, whoever writes (the model, the review, the curator).
- A new habit: an assets/HABIT.yaml written into a skill that is no habit yet is refused, whoever writes it; a habit
  is saved with habit_save.
- No self-approval: only the user changes a state, so the model's `hermes habits promote|undo|resume|reset|retire`,
  its writes into the plugin's own folder (the ledger, the paused and off files), however the path is
  spelled (any write that names habits.db, or plugin-data and habits), its `hermes config set` of the plugin's
  settings, and its call of the plugin's /habits command through Hermes's own code are blocked. Code can still reach
  the ledger in ways no text shows, so the ledger also trusts an active state only when its own history records the
  move.
"""

from __future__ import annotations

import ast
import contextvars
import logging
import os
import posixpath
import re
import time
from pathlib import Path

from . import catalog, graph, shell

logger = logging.getLogger(__name__)

HAND_EDIT = ("{name} is a habit: the Habits plugin checks and versions its files, and an edit by hand can break it. "
             "To change it, call habit_save with the whole new graph, and only when the user asks to change the habit "
             "itself (a new version of a request habit or a watch starts learning again). For a variation just this "
             "once, do the task by hand and leave the habit as it is. A helper script in its assets/ folder is written "
             "with the write_file tool, on its full path.")
DOOR = ("Habits refused assets/HABIT.yaml for {name}: a habit is saved with habit_save only, which checks the graph "
        "and writes the skill's files. Draft it outside the skills folders, as graph.yaml in a scratch folder named "
        "by its full path, try it with habit_run {{file: <its full path>}}, then call habit_save with that file.")
STOP = (" Stop: {n} refusals in 10 minutes. Do not write this habit again; tell the user what you wanted to make, or "
        "save it with habit_save if they ask for it.")
OWN = ("Only the user changes a habit's state, and Habits records who did: point the user to /habits (yes, undo, "
       "resume) instead.")
OWN_FILES = ("plugin-data/habits holds the Habits ledger and its switches (paused, off): only the plugin writes there. "
             "To change a habit's state, point the user to /habits.")
OWN_SETTINGS = ("The Habits settings (promote, promote_runs…) are the user's: they change them in their own "
                "terminal. To change a habit's state, point the user to /habits.")
WHERE = ("Habits cannot tell where this writes a habit's file (SKILL.md, assets/HABIT.yaml, assets/LEARNED.md): a "
         "relative path from a folder the line does not name, a variable or a glob, or code that writes by itself. "
         "A habit's files change through habit_save only. For another file, name it by its full path, or start the "
         "line with cd <its folder's full path>; from code, write it with write_file.")
MERGE = ("{name} is a habit: it is never deleted or merged into another skill, by a review, the curator or you. Leave "
         "it as it is; the user puts it to rest with /habits rest {name}.")
EDITS = {"edit", "patch", "write_file", "remove_file"}  # skill_manage actions that rewrite a skill's files
# `hermes habits promote x` from a shell, any path to hermes, or its words in a script's argument list; quotes and
# backslashes taken out first, as the shell does (pro''mote is promote).
STATE = re.compile(r"\bhabits\b[\s'\",]+(?:--?[\w-]+[\s'\",]+)*(?:promote|undo|resume|reset|retire|learn)\b")
QUOTES = re.compile(r"[\"'\\]")
LEDGER = re.compile(r"habits\.db\b", re.I)
# The plugin's settings, which Hermes reads on every call: promote: auto or a promote_runs of 1 would let a habit
# Hermes made act with no yes. `hermes config get|show` only read them.
CONFIG = re.compile(r"hermes.*?\bconfig\b[\s'\",]+(?:--?[\w-]+[\s'\",]+)*(?:set|unset|edit|migrate)\b", re.S | re.I)
# Hermes's way to a plugin's slash command, which records the yes as the user's: a process the model starts can
# take it (a script the model wrote once did), as can /habits and a state word given to hermes as a query.
COMMAND = re.compile(r"\b(?:bind_asker|invoke_plugin_command|get_plugin_command_handler|plugin_command_dispatch"
                     r"|get_plugin_commands|plugin_choices)\b")
SLASH = re.compile(r"/habits\b[\s'\",]+(?:oui|yes|promote|essai|try|trial|repos|rest|faux|wrong|reprends|resume"
                   r"|annule|undo)\b", re.I)
REFUSALS = 2  # refusals in REFUSALS_SECONDS before the next one says stop
REFUSALS_SECONDS = 600
HABIT_FILES = re.compile(r"(?:^|[\s'\"=/])skills/(?:[^\s'\"]+/)?([a-z0-9][a-z0-9_-]{0,39})/"
                         r"(?:SKILL\.md|assets/HABIT\.yaml|assets/LEARNED\.md)", re.I)  # the Mac's disk ignores case
OWN_PARTS = ("SKILL.md", "assets/HABIT.yaml", "assets/LEARNED.md")  # the files of a habit only habit_save writes
OWN_NAMES = ("skill.md", "habit.yaml", "learned.md")
HABIT_YAML = re.compile(r"(?:^|[\s'\"=/])skills/(?:[^\s'\"]+/)?([a-z0-9][a-z0-9_-]{0,39})/assets/HABIT\.yaml", re.I)
# Commands each word of which is a path (Hermes's skill lock reads the same verbs): the first ones write their last,
# the others each of theirs. Which skill a line writes into is where they land.
WRITE_LAST = {"cp", "install", "ln", "rsync", "ditto", "scp"}
WRITE_EACH = {"mv", "tee", "mkdir", "touch", "rm", "rmdir", "unlink", "chmod", "truncate"}
TARGETS = (">", ">>", ">|", "&>", "&>>", "<>", ">&")  # redirections that write their target
# Code that names a habit's file may read it, as a draft's check does, and writes nothing by itself: its write_file
# and terminal calls meet this guard one by one. So no module but these, no call that writes, runs a program or
# reaches a name no text shows, and open() to read only.
CODE_MODULES = {"yaml", "json", "re", "os", "pathlib", "sys", "textwrap", "pprint", "collections", "itertools",
                "functools", "datetime", "time", "math", "difflib", "string", "typing", "statistics", "glob",
                "fnmatch", "hashlib", "hermes_tools"}
CODE_WRITES = {"system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp", "rename", "renames", "replace",
               "link", "symlink", "symlink_to", "hardlink_to", "link_to", "makedirs", "mkdir", "mkfifo", "mknod",
               "remove", "unlink", "rmdir", "removedirs", "rmtree", "write_text", "write_bytes", "touch", "chmod",
               "lchmod", "chown", "lchown", "chflags", "truncate", "ftruncate", "fdopen", "dup2", "utime", "copy",
               "copy2", "copyfile", "copytree", "copymode", "copystat", "move", "kill", "killpg", "putenv", "exec",
               "eval", "compile", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars",
               "__builtins__", "__dict__", "__class__", "__subclasses__", "__globals__", "import_module", "modules",
               "breakpoint"}
SAVING = contextvars.ContextVar("habits_saving", default=False)


# `hermes habits run` options that take a value; with --json, the only ones of a dry run, which records nothing
# (cli_run). --live does its writes and --memory writes its file. Exact names: argparse would read --li as --live.
DRY_RUN = {"--input", "--message", "--within"}


def reads(line: str) -> bool:
    """Every command of the line only reads, as shell.py reads them, and two more the guard sees as reads, though no
    habit step may hold them: a cd, which moves no file, and a dry `hermes habits run`. Without them, `cd
    <root> && hermes habits check draft/HABIT.yaml` and `hermes habits run … | tail` were refused as writes, again
    and again while a habit was being written."""
    if shell.EXPANSION in line or shell.MARK in line:
        return False
    try:
        return all(_moves_nothing(words, writes) or not shell._why(words, writes, subs, 0)
                   for words, writes, subs in shell._commands(shell._marked(line)))
    except shell.Unknown:
        return False


def _moves_nothing(words, writes) -> bool:
    if writes or not words or not all(w.fixed for w in words):
        return False
    head, rest = shell._name(words[0]), [w.text for w in words[1:]]
    if head == "cd":
        return len(rest) <= 1
    if head != "hermes" or rest[:2] != ["habits", "run"]:
        return False
    targets, i, rest = 0, 0, rest[2:]
    while i < len(rest):
        word, i = rest[i], i + 1
        if word.partition("=")[0] in DRY_RUN and "=" not in word:
            if i >= len(rest) or rest[i].startswith("-"):
                return False
            i += 1
        elif word.startswith("-") and word.partition("=")[0] not in DRY_RUN and word != "--json":
            return False
        elif not word.startswith("-"):
            targets += 1
    return targets == 1


def _paths(text: str) -> str:
    """A call's text with its paths written one way: the home folder spelled out, // and /./ collapsed."""
    for home in ("~/", "$HOME/", "${HOME}/"):
        text = text.replace(home, str(Path.home()) + "/")
    text = re.sub(r"/+", "/", text)
    while "/./" in text:
        text = text.replace("/./", "/")
    return text


def _named(name: str, text: str) -> bool:
    """A habit's folder named as a word of a call: a cd into it, a relative path through it, in any case."""
    return bool(re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", text, re.I))


def _names(text: str, names: tuple, code: bool = False) -> bool:
    """Whether a call names one of these files, as the shell or Python reads it: quotes and escapes taken out,
    braces expanded, a text Python joins ('HAB' 'IT.yaml'), in any case."""
    if any(name in QUOTES.sub("", text).casefold() for name in names):
        return True
    try:
        if code:
            texts = [n.value for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Constant)
                     and isinstance(n.value, str)]
        else:
            texts = [w.text for t in shell._lex(shell._marked(text))[0] if isinstance(t, shell.Word)
                     for w in shell._braces(t)]
    except (SyntaxError, ValueError, shell.Unknown):
        return False
    return any(name in t.casefold() for t in texts for name in names)


ANYWHERE = "?"  # edited_habit: a habit's file may be written where the guard cannot tell


def edited_habit(tool: str, args: dict) -> str | None:
    """The saved habit whose SKILL.md, HABIT.yaml or LEARNED.md this call would rewrite, if any (habit_save's own
    writes go through dispatch_tool, which Hermes does not guard; SAVING covers a Hermes that would). A shell, code or
    file write that names one of those files is read as the shell and the disk read it: its words with quotes and
    escapes undone, in any case (a cd into the folder, a relative path, a doubled slash), and where its writes land,
    links resolved (_landing). While a habit exists, one whose writes land where the guard cannot tell is ANYWHERE:
    a relative path from the session's own folder (Hermes keeps its last cd, in another call), a variable or a glob,
    code that writes by itself (an active habit can be replaced by a cd, then a cp)."""
    if tool == "skill_manage":
        ops = args.get("operations") if isinstance(args.get("operations"), list) else [args]
        names = [op.get("name") for op in ops if isinstance(op, dict) and op.get("action") in EDITS]
        folders = catalog.folders() if names else {}
        return next((name for name in names if name in folders), None)
    if tool not in ("write_file", "patch", "terminal", "execute_code"):
        return None
    text = " ".join(str(args.get(key) or "") for key in ("path", "patch", "command", "code"))
    if not _names(text, OWN_NAMES, tool == "execute_code") or tool == "terminal" and reads(text):
        return None
    folders = catalog.folders()
    if not folders:
        return None
    texts = [_paths(text)] + ([_paths(QUOTES.sub("", text))] if tool == "terminal" else [])
    keys = {name.casefold(): name for name in folders}
    found = next((keys[n.casefold()] for t in texts for n in HABIT_FILES.findall(t) if n.casefold() in keys), None)
    if found:
        return found
    for name, folder in folders.items():  # a skills folder outside a "skills/" path (external_dirs), or a cd into it
        if any(f"{_paths(str(folder))}/{part}".casefold() in t.casefold() for t in texts for part in OWN_PARTS) or \
                any(_named(folder.name, t) or _named(name, t) for t in texts):
            return name
    places = _landing(tool, args, OWN_NAMES, door=False)
    if places is None:
        return ANYWHERE
    for name, folder in folders.items():  # a write in it, or of a folder that holds it (mv, rm -r)
        held = os.path.realpath(folder).casefold()
        if any(_inside(p.casefold(), held) or _inside(held, p.casefold()) for p in places):
            return name
    return None


def unplaced(tool: str, args: dict) -> bool:
    """A file tool's write of assets/HABIT.yaml on a relative path: the guard cannot tell which skill it lands in."""
    path = posixpath.expanduser(str(args.get("path") or ""))
    return tool in ("write_file", "patch") and not posixpath.isabs(path)


def _landing(tool: str, args: dict, names: tuple, door: bool) -> list[str] | None:
    """Where a call writes, as full paths with their links resolved, or None when the guard cannot tell: a shell
    line's writes (_writes); a file tool's path (a relative one starts from the session's folder, unknown here);
    code, nothing when it writes nothing by itself (its write_file and terminal calls meet this guard one by one)."""
    if tool == "terminal":
        return _writes(str(args.get("command") or ""), str(args.get("workdir") or ""), names, door)
    if tool == "execute_code":
        return None if _code_writes(str(args.get("code") or "")) else []
    path = posixpath.expanduser(str(args.get("path") or ""))
    return [os.path.realpath(path)] if posixpath.isabs(path) else None


def _simple(line: str) -> list[tuple]:
    """The simple commands of a line as shell._commands cuts them, each as (words, the targets its redirections write,
    the separator after it); shell.Unknown for a form it does not read (a heredoc, ( ), a <( ))."""
    marked = shell._marked(line)
    shell._commands(marked)  # its refusals
    tokens, _ = shell._lex(marked)
    found, words, targets, used, i = [], [], [], False, 0
    while i < len(tokens):
        token, i = tokens[i], i + 1
        if isinstance(token, shell.Word):
            words, used = words + shell._braces(token), True
        elif token in shell.SEPARATORS:
            if used:
                found.append((words, targets, token))
            words, targets, used = [], [], False
        else:  # a redirection and its target, a pair shell._commands checked
            target, i, used = tokens[i], i + 1, True
            op = shell.REDIRECT.match(token).group(2)
            joined = op == ">&" and (target.text.isdigit() or target.text == "-")  # 2>&1
            if op in TARGETS and not joined and target.text != "/dev/null":
                targets.append(target)
    if used:
        found.append((words, targets, ""))
    return found


def _writes(line: str, workdir: str, names: tuple, door: bool) -> list[str] | None:
    """The full paths a shell line writes, links resolved (door: every path its writes name, sources too, so a link
    moved or copied in place is seen): each path of cp, mv, tee and the like, each redirection's target, each word of
    another command that names one of names (sed -i SKILL.md), a live run's --within folder. A relative path starts
    from the folders the command may run in: the call's workdir, a cd to a full path, then cds from there; after a ;
    or a || the line may still be where it began, since that cd may have failed. None when the guard cannot tell: a
    line the reader does not read, a variable, a substitution or a glob in such a path, a relative one from the
    session's own folder (Hermes keeps its last cd, which the guard never sees), a cd in a pipe, a write after
    another command (it could make a link the write goes through), or (door) another command that names one."""
    if shell.EXPANSION in line or shell.MARK in line:
        return None
    try:
        return _placed(_simple(line), workdir, names, door)
    except shell.Unknown:  # a substitution nested too deep, read below
        return None


def _placed(commands: list, workdir: str, names: tuple, door: bool) -> list[str] | None:
    start = os.path.realpath(os.path.expanduser(workdir)) if _full(workdir) else None
    now, seen, places, stranger, after = {start}, {start}, [], False, ""
    for words, targets, join in commands:
        here = now if after in ("&&", "|", "|&") else set(seen)
        head = shell._name(words[0]) if words else None
        texts = [w.text for w in words]
        found, other = list(targets), False
        if head == "cd":
            if after in ("|", "|&") or join in ("|", "|&", "&"):
                return None  # a cd in a pipe or in the background moves no later command in bash; zsh's last does
            given = words[1] if len(words) == 2 and texts[1] != "-" else None
            now = {str(Path.home())} if len(words) == 1 else set(_place(given, here) or [None]) if given else {None}
            seen = seen | now
        elif head in WRITE_LAST or head in WRITE_EACH:
            now = here
            if any(t.startswith("-") and "/" in t for t in texts[1:]):
                return None  # an option that holds a path (--target-directory=…)
            operands = [w for w in words[1:] if not w.text.startswith("-")]
            each = door or head in WRITE_EACH or "-t" in texts  # GNU's -t DIR: the folder they all go to
            found += operands if each else operands[-1:]
        else:
            now = here
            within = _within(words)
            if within is not None:
                found.append(within)
            elif head not in ("exit", ":") and shell._why(words, False, (), 0) and not _moves_nothing(words, False):
                other = True
                named = [w for w in words if any(name in w.text.casefold() for name in names)]
                if named and door:
                    return None
                found += named
        if found and stranger:
            return None
        for word in found:
            place = _place(word, here)
            if place is None:
                return None
            places += place
        stranger |= other
        after = join
    return places


def _within(words):
    """The folder of a live `hermes habits run <file> --live --within <folder>`, whose writes stay there (cli_run
    checks the dry plan, then each write): the one path it writes. None for any other command."""
    texts = [w.text for w in words]
    if not words or shell._name(words[0]) != "hermes" or texts[1:3] != ["habits", "run"] or "--live" not in texts:
        return None
    within, targets, i = None, 0, 3
    while i < len(texts):
        word, i = texts[i], i + 1
        if word in DRY_RUN:
            if i >= len(texts) or texts[i].startswith("-"):
                return None
            within, i = words[i] if word == "--within" else within, i + 1
        elif word.startswith("-"):
            if word not in ("--live", "--json"):
                return None  # --memory writes its file
        else:
            targets += 1
    return within if targets == 1 else None


def _place(word, here: set) -> list[str] | None:
    """Where a word names a path, in full with its links resolved, from each folder the command may run in; None
    when a variable but $HOME, a substitution or a glob leaves it unknown, or a relative path meets an unknown
    folder (None in here)."""
    if word.subs or set(word.names) - {"HOME"} or any(c in "*?[" and not q for c, q in zip(word.text, word.quoted)):
        return None
    text = word.text
    if word.names:  # $HOME/x
        if not text.startswith(shell.EXPANSION) or text.count(shell.EXPANSION) != 1:
            return None
        text = str(Path.home()) + text[1:]
    elif text[:1] == "~" and not word.quoted[0]:
        if text != "~" and not text.startswith("~/"):
            return None  # ~user
        text = str(Path.home()) + text[1:]
    if text.startswith("/"):
        return [os.path.realpath(text)]
    if None in here:
        return None
    return [os.path.realpath(os.path.join(folder, text)) for folder in here]


def _full(path: str) -> bool:
    return path.startswith("/") or path == "~" or path.startswith("~/")


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _roots() -> list[str]:
    """The skills folders Hermes reads (external_dirs need not be called skills) and those that hold a habit, in
    full with their links resolved, in lower case; catalog.folders() asks Hermes the same way."""
    try:
        from tools.skills_tool import skill_search_dirs
        roots, _active = skill_search_dirs()
    except Exception:  # outside Hermes (tests) or an older Hermes
        roots = []
    found = [str(root) for _tier, root in roots if str(root).strip("/")]
    return [os.path.realpath(os.path.expanduser(r)).casefold()
            for r in found + [str(folder.parent) for folder in catalog.folders().values()]]


def _skilled(path: str) -> bool:
    """A full path in a skill or a skills folder: through a folder named skills (a profile's too; the Mac's disk
    ignores case), under one Hermes reads or one that holds a habit, or in a folder that holds a SKILL.md."""
    low = path.casefold()
    if "skills" in low.split("/") or any(_inside(low, root) for root in _roots()):
        return True
    for folder in [Path(path), *Path(path).parents][:-1]:
        try:
            if (folder / "SKILL.md").is_file():
                return True
        except OSError:
            continue
    return False


def _code_writes(code: str) -> bool:
    """Whether code may write a file by itself, or run what may: a module but CODE_MODULES, a name of CODE_WRITES
    (exec… and spawn… too), open() but to read, or code that is no Python. A str's replace() of two plain texts and
    a dict's copy() are no such call."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return True
    fine = set()  # ast.walk sees a call before its function's name
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            plain = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
                     and "/" not in a.value]
            if not node.keywords and (name == "replace" and len(node.args) == len(plain) == 2
                                      or name == "copy" and not node.args):
                fine.add(id(node.func))
            if name == "open" and _opens_to_write(node):
                return True
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            taken = [a.name for a in node.names] if isinstance(node, ast.ImportFrom) else []
            if any(m.split(".")[0] not in CODE_MODULES for m in modules) or \
                    any(t == "*" or t in CODE_WRITES or t.startswith(("exec", "spawn")) for t in taken):
                return True
        elif isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in fine:
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in CODE_WRITES or name.startswith(("exec", "spawn")):
                return True
    return False


def _opens_to_write(call) -> bool:
    """open() or .open() given a mode (or flags) that is not a plain text for reading."""
    method = isinstance(call.func, ast.Attribute)
    args = list(call.args if method else call.args[1:])
    if method and args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str) and "/" in args[0].value:
        args = args[1:]  # io.open('/x', …), os.open('/x', …): a path first
    args += [k.value for k in call.keywords if k.arg in ("mode", "flags")]
    return any(not (isinstance(a, ast.Constant) and isinstance(a.value, str) and set(a.value) <= set("rbtU"))
               for a in args)


def made_habit(tool: str, args: dict) -> str | None:
    """The skill a call would turn into a habit, writing its assets/HABIT.yaml outside habit_save. A shell or code
    write that names the file is one unless it surely lands outside every skill (_landing, _skilled); a file tool's
    write is read by its path, links resolved."""
    if tool in ("terminal", "execute_code"):  # a shell write: cp, tee, a redirection; or code
        command = str(args.get("command") or args.get("code") or "")
        if not _names(command, ("habit.yaml",), tool == "execute_code") or tool == "terminal" and reads(command):
            return None
        found = HABIT_YAML.search(_paths(QUOTES.sub("", command) if tool == "terminal" else command))
        if found:
            return found.group(1)
        places = _landing(tool, args, ("habit.yaml",), door=True)
        return "this skill" if places is None or any(_skilled(p) for p in places) else None
    if tool in ("write_file", "patch"):  # Hermes does not protect the skills folder from its file tools
        given = posixpath.expanduser(str(args.get("path") or ""))
        path = posixpath.normpath(os.path.realpath(given) if posixpath.isabs(given) else given).lower()
        found = re.search(r"(?:^|/)(?:skills/(?:.+/)?)?([^/]+)/assets/habit\.yaml$", path) if unplaced(tool, args) \
            else re.search(r"/skills/(?:.+/)?([^/]+)/assets/habit\.yaml$", path)
        if found:
            return found.group(1)
        if posixpath.isabs(given) and path.endswith("/assets/habit.yaml") and \
                any(_inside(path, root) for root in _roots()):  # a skills folder of another name
            return path.split("/")[-3]
        return "this skill" if unplaced(tool, args) and path.endswith("assets/habit.yaml") else None
    if tool != "skill_manage":
        return None
    ops = args.get("operations") if isinstance(args.get("operations"), list) else [args]
    for op in ops:
        if not isinstance(op, dict) or op.get("action") not in ("write_file", "patch"):
            continue
        if posixpath.normpath(str(op.get("file_path") or "")).lower() == str(catalog.HABIT_FILE).lower():
            return str(op.get("name") or "this skill")
    return None


def changes_state(tool: str, args: dict) -> str | None:
    """Why this call would change a habit's state outside the user's hands: a `hermes habits promote|undo|resume|
    reset|retire`, a write into the plugin's own folder, which a cd, a variable, a glob or a path built in
    code can spell (any write that names habits.db, or both plugin-data and habits), a `hermes config set` of the
    plugin's settings, or Hermes's own code for its /habits command, run by the model (a script it wrote once did), and
    /habits with a state word given to hermes as a query. Code can write anything, so it never names them. None for
    anything else, a shell's reads of that folder and of Hermes's code included."""
    if tool not in ("terminal", "execute_code", "write_file", "patch"):
        return None
    text = " ".join(str(args.get(key) or "") for key in ("command", "code", "path", "patch"))
    if tool in ("terminal", "execute_code"):
        plain = QUOTES.sub("", text)
        if STATE.search(text) or STATE.search(plain):
            return OWN
        runs = tool == "execute_code" or not reads(text)
        if runs and "habits" in plain.lower() and CONFIG.search(plain):
            return OWN_SETTINGS
        if runs and ("habits" in plain.lower() and COMMAND.search(plain) or SLASH.search(plain)):
            return OWN
    lowered = text.lower()
    if not LEDGER.search(text) and not ("plugin-data" in lowered and "habits" in lowered):
        return None
    if tool == "terminal" and graph._reads_only(text):
        return None
    return OWN_FILES


class Guard:
    """The pre_tool_call hook of Habits."""

    def on_pre_tool_call(self, tool_name="", args=None, session_id="", task_id="", **_):
        """A habit's files change through habit_save only; a new habit through habit_save; a
        state through the user only. Hermes's code sandbox calls its tools with no session, only its task's id."""
        if SAVING.get():
            return None
        tool, args = str(tool_name or ""), args if isinstance(args, dict) else {}
        try:
            why = changes_state(tool, args)
            if why:
                return {"action": "block", "message": why}
            name = edited_habit(tool, args)
            if name:
                return {"action": "block", "message": WHERE if name == ANYWHERE else HAND_EDIT.format(name=name)}
            name = self.merged(tool, args)
            if name:
                return {"action": "block", "message": MERGE.format(name=name)}
            made = made_habit(tool, args)
            return self.door(made, str(session_id or task_id or "")) if made else None
        except Exception as exc:
            logger.warning("habits: pre_tool_call failed: %s", exc)
            return None

    def merged(self, tool: str, args: dict) -> str | None:
        """A habit skill_manage would delete or absorb into an umbrella skill. Whoever asks: in the plugin host, Hermes
        cannot say whether a review or the curator makes the call (its ContextVar stays in the agent's process), and
        the user puts a habit to rest in /habits."""
        if tool != "skill_manage":
            return None
        ops = args.get("operations") if isinstance(args.get("operations"), list) else [args]
        folders = catalog.folders()
        return next((op["name"] for op in ops if isinstance(op, dict) and op.get("action") == "delete"
                     and op.get("name") in folders), None)

    def door(self, name: str, session: str = ""):
        """A HABIT.yaml written into a skill outside habit_save: refused toward habit_save. Each session (or task, when
        the call names none) counts its own refusals; past 2 in 10 minutes the refusal says stop."""
        now = time.monotonic()
        self.refusals = {key: kept for key, times in self.refusals.items()
                         if (kept := [at for at in times if now - at < REFUSALS_SECONDS])}
        times = self.refusals.setdefault(session, [])
        times.append(now)
        stop = STOP.format(n=len(times)) if len(times) > REFUSALS else ""
        return {"action": "block", "message": DOOR.format(name=name) + stop}
