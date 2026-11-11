"""The guard on habits, as a pre_tool_call hook: the wall, a new habit through habit_save only, and no self-approval.

- The wall: a habit's SKILL.md, assets/reflex.yml (or assets/HABIT.yaml, the name v0.1 wrote) and assets/LEARNED.md
  (a file older versions wrote) change through habit_save only, whoever writes (the model, the review, the curator).
- A new habit: an assets/reflex.yml (or HABIT.yaml) written into a skill that is no habit yet is refused, whoever
  writes it; a habit is saved with habit_save.
- No self-approval: only the user changes a state, so the model's `hermes habits promote|undo|resume|reset|retire`,
  its writes into the plugin's own folder (the ledger, the paused and off files), however the path is
  spelled (plugin-data and habits, or habits.db next to a Hermes folder), its writes into the plugin's own code
  (plugins/habits, this guard included), its `hermes config set` of the plugin's settings, its setting of HABITS_OFF,
  and its call of the plugin's /habits command through Hermes's own code are blocked. Code can still reach the ledger
  in ways no text shows, so the ledger also trusts an active state only when its own history records the move.
- What a call can reach: a call is read for what it can do (a program named by a variable, a script it runs, code
  folded as Python folds it, a path as a glob, a variable or a link resolves it), and refused when it touches the
  plugin's folder, the settings or a habit's files in a form the reader cannot resolve.
- Reading is never an edit: a call that only looks at a habit's files, the plugin's folders or Hermes's own files
  passes (reads, near the top of this file), also inside an if or a loop, from code that only reads, and through a
  variable the line itself surely sets to a plain path, which is read as that path (_resolved). A refusal names the
  habit the call really touches, or says that the guard cannot tell where the call writes.
- First of all, whether the call concerns that world at all (concerns, at the end of this file): one that shows
  nothing of it (no word of it in its text, no path that lands in it, no script that names it) passes unread. The
  careful reading above, which refuses what it cannot read, is for the calls that do. A file the model writes is
  judged by where it goes and by whether it is a script that acts on that world, never by a word it mentions.
"""

from __future__ import annotations

import ast
import base64
import binascii
import codecs
import contextvars
import fnmatch
import functools
import itertools
import logging
import os
import posixpath
import re
import shlex
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

from . import catalog, graph, shell

logger = logging.getLogger(__name__)

HAND_EDIT = ("{name} is a habit: the Habits plugin checks and versions its files, and an edit by hand can break it. "
             "To change it, call habit_save with the whole new reflex, and only when the user asks to change the habit "
             "itself (a new version of a request habit or a watch starts learning again). For a variation just this "
             "once, do the task by hand and leave the habit as it is. A helper script in its assets/ folder is written "
             "with the write_file tool, on its full path.")
DOOR = ("Habits refused assets/reflex.yml for {name}: a habit is saved with habit_save only, which checks the reflex "
        "and writes the skill's files. Draft it outside the skills folders, as draft.yaml in a scratch folder named "
        "by its full path, try it with habit_run {{file: <its full path>}}, then call habit_save with that file.")
STOP = (" Stop: {n} refusals in 10 minutes. Do not write this habit again; tell the user what you wanted to make, or "
        "save it with habit_save if they ask for it.")
OWN = ("Only the user changes a habit's state, and Habits records who did: point the user to /habits (yes, undo, "
       "resume) instead.")
UNREADABLE = ("Habits could not read which `hermes habits` command this runs, or it is one that changes a habit's "
              "state. Only the user changes a state, and Habits records who did: point the user to /habits. The model "
              "may call `hermes habits` list, show, view, check, tick and help, written plainly (no variable, "
              "substitution or quoting trick in the verb); to run a habit, call habit_run {name, inputs}; to write "
              "text that merely mentions it, use write_file.")
LIVE = ("A live run of a habit is the user's: `hermes habits run` from the model may only try a reflex without "
        "moving anything (--input, --message, --within, --json). To run a habit, call habit_run {name, inputs}; to "
        "run it for real by hand, ask the user to do it in their own terminal or in /habits.")
CHILD = ("Habits could not read the message this `hermes` starts a turn with (chat, gateway, -z…): it comes from a "
         "file, a pipe, a variable or a substitution, or it carries /habits. A turn started by a process of yours "
         "would take /habits yes as the user's. Write the message in the command itself, as plain text, without "
         "/habits; only the user answers /habits.")
PLUGIN = ("The Habits plugin is the user's to install, update, turn off or remove: the model may read `hermes plugins` "
          "(list, show, info, validate…) and change another plugin. To change this one, point the user to their own "
          "terminal.")
OWN_FILES = ("plugin-data/habits holds the Habits ledger and its switches (paused, off): only the plugin writes there. "
             "To change a habit's state, point the user to /habits.")
OWN_HISTORY = ("Hermes's history (state.db) is written by Hermes only: the dream reads it to see what really happened, so "
               "a model cannot add to it or change it. Reading it is fine (select).")
OWN_SETTINGS = ("Hermes's settings file (config.yaml) holds the Habits settings (learning, caution, permissions…), and those "
                "are the user's: the model does not edit that file, run `hermes config edit`, or set a Habits setting. "
                "For any other setting, run `hermes config set <key> <value>`; to change a habit's state, point the user "
                "to /habits.")
OWN_CODE = ("plugins/habits holds the Habits plugin's own code, the guard included: only the user changes it, in their "
            "own terminal (hermes plugins install, update, remove). Reading it is fine. To work on the plugin's source, "
            "work in a copy outside Hermes's folder.")
OFF_SWITCH = ("HABITS_OFF is the user's switch: a call of the model's may read it, never set it, for itself or for a "
              "`hermes` it starts. The guard stays on either way. If Habits is in the way, tell the user what you "
              "wanted to do.")
UNNAMED = ("This line names Hermes, Habits or one of their files, and Habits cannot read which program it runs: a "
           "variable, a substitution, eval, or a pipe into a shell or an interpreter. The plugin's ledger, settings, "
           "code and each habit's state are the user's, so such a line is refused rather than guessed. Write the "
           "program's name plainly, one command per line, or ask the user to run it.")
WHERE = ("Habits cannot tell where this writes a habit's file (SKILL.md, assets/reflex.yml, assets/LEARNED.md): a "
         "relative path from a folder the line does not name, a variable or a glob, or code that writes by itself. "
         "A habit's files change through habit_save only. For another file, name it by its full path, or start the "
         "line with cd <its folder's full path>; from code, write it with write_file.")
MERGE = ("{name} is a habit: it is never deleted or merged into another skill, by a review, the curator or you. Leave "
         "it as it is; the user puts it to rest with /habits rest {name}.")
EDITS = {"edit", "patch", "write_file", "remove_file"}  # skill_manage actions that rewrite a skill's files
# `hermes habits promote x` from a shell, any path to hermes, or its words in a script's argument list; quotes and
# backslashes taken out first, as the shell does (pro''mote is promote).
STATE = re.compile(r"\bhabits\b[\s'\",]+(?:-{1,2}(?=\w)[\w-]*[\s'\",]+)*(?:promote|undo|resume|reset|retire|learn)\b")
QUOTES = re.compile(r"[\"'\\]")
LEDGER = re.compile(r"habits\.db\b", re.I)
# The plugin's settings, which Hermes reads on every call: promote: auto or a promote_runs of 1 would let a habit
# Hermes made act with no yes. `hermes config get|show` only read them.
CONFIG = re.compile(r"hermes.*?\bconfig\b[\s'\",]+(?:-{1,2}(?=\w)[\w-]*[\s'\",]+)*(?:set|unset|edit|migrate)\b",
                    re.S | re.I)
# Hermes's way to a plugin's slash command, which records the yes as the user's: a process the model starts can
# take it (a script the model wrote once did), as can /habits and a state word given to hermes as a query.
COMMAND = re.compile(r"\b(?:bind_asker|invoke_plugin_command|get_plugin_command_handler|plugin_command_dispatch"
                     r"|get_plugin_commands|plugin_choices)\b")
SLASH = re.compile(r"/habits\b[\s'\",]+(?:oui|yes|promote|essai|try|trial|repos|rest|faux|wrong|reprends|resume"
                   r"|annule|undo|no|non|dream|rêve|reve)\b", re.I)
REFUSALS = 2  # refusals in REFUSALS_SECONDS before the next one says stop
REFUSALS_SECONDS = 600
HABIT_FILES = re.compile(r"(?:^|[\s'\"=/])skills/(?:[^\s'\"]+/)?([a-z0-9][a-z0-9_-]{0,39})/"
                         r"(?:SKILL\.md|assets/(?:reflex\.yml|HABIT\.yaml)|assets/LEARNED\.md)",
                         re.I)  # the Mac's disk ignores case
# The files of a habit only habit_save writes
OWN_PARTS = ("SKILL.md", "assets/reflex.yml", "assets/HABIT.yaml", "assets/LEARNED.md")
OWN_NAMES = ("skill.md", "reflex.yml", "habit.yaml", "learned.md")
GRAPH_NAMES = ("reflex.yml", "habit.yaml")  # the graph file, by its new and its v0.1 name
HABIT_YAML = re.compile(r"(?:^|[\s'\"=/])skills/(?:[^\s'\"]+/)?([a-z0-9][a-z0-9_-]{0,39})/"
                        r"assets/(?:reflex\.yml|HABIT\.yaml)", re.I)
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
# The tools whose calls the guard examines for a state change or a habit's file: when the examination itself fails, the
# call is refused. Other tools go on (a broken guard must not stop Hermes from reading a file).
FAIL_CLOSED = {"terminal", "write_file", "patch", "execute_code", "skill_manage"}
BROKEN_GUARD = ("Habits could not examine this call (its check failed), so it is refused rather than let through. "
                "Try it again in a simpler form: a plain command, or write_file for a file.")
SAVING = contextvars.ContextVar("habits_saving", default=False)


# `hermes habits run` options that take a value; with --json, the only ones of a dry run, which records nothing
# (cli_run). --live does its writes and --memory writes its file. Exact names: argparse would read --li as --live.
DRY_RUN = {"--input", "--message", "--within"}
RUN_OPTIONS = ("--input", "--live", "--json", "--within", "--message", "--memory", "--help")  # argparse reads a prefix


@functools.lru_cache(maxsize=32)
def reads(line: str) -> bool:
    """Every command of the line only reads, as shell.py reads them, and a few more the guard sees as reads, though
    no habit step may hold them: a cd, which moves no file, a dry `hermes habits run`, a variable set for the line's
    next commands (S=<a path>, V=$(head -1 f)), a find whose -exec runs a reader, and such commands inside the
    shell's own if, for, while and { } (for f in a b; do head "$f"; done). Without them a look at a habit's files
    was refused as an edit by hand, and `cd <root> && hermes habits check draft/reflex.yml` as a write, again and
    again while a habit was being written. Reading is never an edit. The line is a terminal call's, read as the
    shell reads it: a # comment is left out, and {name} is plain text there (a template only in a reflex's step)."""
    if shell.EXPANSION in line or shell.MARK in line:
        return False
    try:
        commands, fed = shell._commands(_strip_comments(line), with_input=True)
        return all(_only_reads(words, writes, subs, given) for (words, writes, subs), given in zip(commands, fed))
    except shell.Unknown:
        return False


# The shell's own words before a command (if x; then y; else z; fi), and those that close a block
LEADERS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{"}
CLOSERS = {"fi", "done", "}"}
VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Variables the shell or a program reads by itself: setting one changes which program a later command runs, how its
# words are cut or what it does (PATH, IFS, GIT_EXTERNAL_DIFF, HABITS_OFF), so such a line is no plain read.
OWN_VARIABLES = {"PATH", "CDPATH", "IFS", "HOME", "PWD", "OLDPWD", "ENV", "SHELL", "ZDOTDIR", "FPATH", "TMPDIR", "PAGER",
                 "EDITOR", "VISUAL", "LESS", "LESSOPEN", "LESSCLOSE", "MANPAGER", "BROWSER", "PS4", "PROMPT_COMMAND",
                 "HISTFILE", "SHELLOPTS", "BASHOPTS", "GLOBIGNORE", "NULLCMD", "READNULLCMD", "TERM", "LANG", "path",
                 "cdpath", "fpath", "manpath", "module_path"}
OWN_VARIABLE_STARTS = ("LD_", "DYLD_", "GIT_", "HABITS_", "HERMES_", "PYTHON", "BASH", "ZSH", "LC_", "NODE_", "RUBY",
                       "PERL", "XDG_", "SSH_", "GPG", "npm_", "NPM_", "DOCKER_", "KUBE", "AWS_", "CURL_", "SQLITE_")
PLAIN_VALUE = re.compile(r"[A-Za-z0-9_./@+:,%-]+")  # a path with nothing the shell expands or cuts


def _keyword(word, names) -> bool:
    return not word.names and not word.subs and not any(word.quoted) and word.text in names


def _unwrapped(words) -> list | None:
    """The command left once the shell's own words are taken off its start (if, then, else, elif, while, until, do,
    !, {); nothing for a closing fi, done or }; for `for NAME in words`, an echo of those words (they run nothing but
    their substitutions). None for a form that is no read: a for over a variable the shell reads by itself."""
    k = 0
    while k < len(words) and _keyword(words[k], LEADERS):
        k += 1
    rest = list(words[k:])
    if rest and _keyword(rest[0], {"for"}):
        named = len(rest) >= 3 and _keyword(rest[1], {rest[1].text}) and VARIABLE.fullmatch(rest[1].text) and \
            not _own_variable(rest[1].text) and _keyword(rest[2], {"in"})
        return [shell.Word("echo", (False,) * 4), *rest[3:]] if named else None
    if len(rest) == 1 and _keyword(rest[0], CLOSERS):
        return []
    return rest


def _only_reads(words, writes, subs, fed=False) -> bool:
    """fed: the command reads its input from a pipe or a file (what sqlite3 is given there, it runs)."""
    words = _unwrapped(words)
    if words is None:
        return False
    return _moves_nothing(words, writes, subs) or _find_reads(words, writes, subs) or \
        _python_reads(words, writes, subs) or _printf_reads(words, writes, subs) or _read_reads(words, writes, subs) or \
        _xargs_reads(words, writes, subs) or (not fed and _query_reads(words, writes, subs)) or \
        not shell._why(words, writes, subs, 0)


def _own_variable(name: str) -> bool:
    return name in OWN_VARIABLES or name.startswith(OWN_VARIABLE_STARTS)


def _assigned(word) -> tuple | None:
    """(name, its plain value or None) for a word that sets a variable of the line's own (S=/x, V=$(…)); None for any
    other word and for a variable the shell or a program reads by itself. The plain value is the path the variable
    surely holds: nothing in it that the shell expands, cuts or matches, ~ and $HOME written out."""
    found = ASSIGN.match(word.text)
    if not found or any(word.quoted[:found.end()]):
        return None
    name = word.text[:found.end() - 1]
    if _own_variable(name):
        return None
    value, quoted = word.text[found.end():], word.quoted[found.end():]
    if word.subs:
        return name, None
    home = str(Path.home())
    if word.names:
        if word.names != ("HOME",) or not value.startswith(shell.EXPANSION) or not PLAIN_VALUE.fullmatch(home):
            return name, None
        value = home + value[1:]
    elif value[:1] == "~" and not quoted[0]:
        if (value != "~" and not value.startswith("~/")) or not PLAIN_VALUE.fullmatch(home):
            return name, None
        value = home + value[1:]
    return name, value if PLAIN_VALUE.fullmatch(value) else None


def _find_reads(words, writes, subs) -> bool:
    """A find that writes nothing itself and whose every -exec or -execdir runs a reader (cat {}, grep -n x {} +)."""
    if writes or subs or not words or shell._name(words[0]) != "find" or not all(w.fixed for w in words):
        return False
    texts = [w.text for w in words]
    if any(t in FIND_WRITES or t.startswith("-fprint") or t in ("-ok", "-okdir") for t in texts):
        return False
    runs = _find_runs(words)
    return bool(runs) and all(run and not shell._why(run, False, (), 0) for run in runs)


def _python_reads(words, writes, subs) -> bool:
    """python3 -c '<code>' whose code writes nothing by itself and runs no program: open() to read, json.load, a
    print (_code_writes, the reading of execute_code)."""
    if writes or subs or len(words) != 3 or not all(w.fixed for w in words) or words[0].names or words[1].names:
        return False
    code = words[2].text.replace(shell.EXPANSION, str(Path.home()))  # fixed: the only variable it may hold is $HOME
    return bool(PYTHON.fullmatch(shell._name(words[0]) or "")) and words[1].text == "-c" and \
        "hermes_tools" not in code and not _code_writes(code)


# ---- a look around the Hermes home ---------------------------------------------------------------------------------
# "What takes space in ~/.hermes" is the follow-up the shipped health reflex suggests, and a model answers it with ls,
# du, a loop and a count. On the owner's real Hermes two such lines were refused in one turn: one held a
# printf "%s  " "$d", the other an empty=$((empty+1)). These are the reads such a line is made of, beside shell.py's.

XARGS_FLAGS = {"-0", "-r", "-t", "-x"}
XARGS_VALUES = {"-n", "-L", "-P", "-I", "-J", "-R", "-S", "-s", "-E", "-d"}  # options that take a value


def _subs_read(words, subs) -> bool:
    return all(not shell._why_line(sub, 1) for sub in [*subs, *(s for w in words for s in w.subs)])


def _printf_reads(words, writes, subs) -> bool:
    """printf with its format written in the line (printf "%s  " "$d"): only an option before the format sets a
    variable (-v), so what comes after it is text to print, a variable's value too."""
    if writes or len(words) < 2 or shell._name(words[0]) != "printf" or not words[1].fixed or \
            words[1].text.startswith("-"):
        return False
    return _subs_read(words, subs)


def _read_reads(words, writes, subs) -> bool:
    """read into variables of the line's own (ls | while read -r d; do du -sh "$d"; done; IFS= read -r line): it
    sets them from its input and writes no file. Never one the shell or a program reads by itself (read PATH)."""
    if writes or subs or not words:
        return False
    if len(words) > 1 and words[0].text == "IFS=" and not words[0].names and not words[0].subs:
        words = words[1:]  # for this read only: it keeps the line whole
    if shell._name(words[0]) != "read" or not all(w.fixed for w in words):
        return False
    names = [w.text for w in words[1:] if w.text != "-r"]
    return bool(names) and all(VARIABLE.fullmatch(name) and not _own_variable(name) for name in names)


def _xargs_reads(words, writes, subs) -> bool:
    """xargs that hands what it is fed to a reader no argument turns into a writer (find … | xargs du -sh,
    ls | xargs -I{} wc -c {})."""
    if writes or subs or not words or shell._name(words[0]) != "xargs" or not all(w.fixed for w in words):
        return False
    texts, i = [w.text for w in words[1:]], 0
    while i < len(texts) and texts[i].startswith("-"):
        if texts[i] in XARGS_FLAGS:
            i += 1
        elif texts[i] in XARGS_VALUES:
            i += 2
        elif texts[i][:2] in XARGS_VALUES:
            i += 1  # -n1, -I{}
        else:
            return False
    return i < len(texts) and shell._name(words[1 + i]) in shell.PURE


def _query_reads(words, writes, subs) -> bool:
    """sqlite3 asked only to select, or opened read-only (_history_read): a read, of any database."""
    return not writes and not subs and len(words) > 1 and all(w.fixed for w in words) and \
        shell._name(words[0]) in ("sqlite3", "sqlite") and _history_read(SimpleNamespace(words=words))


# $(( )) that only counts. The plugin's shell reader refuses every $(( )) (a reflex computes in jq or awk), so a line
# with n=$((n+1)) could not be read at all: a look was refused when it also held a word like `du -sh`, and a write
# passed gates that need the line's words. The guard writes such an arithmetic out as a number (0 stands for it) when
# it surely is one: nothing in it but names, numbers and + - * / %, and each name set by the line itself to a number,
# in a command of its own at the top of the line, before any use, and never set another way (bash and zsh evaluate
# a name's text again inside $(( )): v='a[$(program)]'; echo $((v)) runs the program). Any other $(( )) stays as it
# is, and the line is read as before.

COUNTS = re.compile(r"\$\(\(([A-Za-z0-9_$ \t+*/%-]*)\)\)")
SETS_BY_NAME = {"read", "mapfile", "readarray", "getopts", "declare", "typeset", "local", "readonly", "export", "let",
                "integer", "float", "unset", "nameref"}  # they set a variable whose name a word of theirs gives


def _counted(line: str) -> str:
    """The line with each $(( )) that only counts written as 0; the line itself when one of them may do more."""
    if "$((" not in line or "<<" in line or shell.EXPANSION in line or shell.MARK in line:
        return line
    expanded = _expanded_dollars(line)
    spans = [m for m in COUNTS.finditer(line) if m.start() in expanded]
    if len(spans) != sum(1 for i in expanded if line.startswith("$((", i)):
        return line
    names = set()
    for m in spans:
        if re.search(r"\$(?![A-Za-z_])", m.group(1)):
            return line  # $1, $$, ${…}: a text put in before the count
        names |= set(VARIABLE.findall(re.sub(r"\b\d+\b", " ", m.group(1))))
    out, last = [], 0
    for m in spans:
        out += [line[last:m.start()], "0"]
        last = m.end()
    out = "".join(out) + line[last:]

    def inside(i):
        return any(m.start() <= i < m.end() for m in spans)

    try:
        tokens, _ = shell._lex(_strip_comments(out))
        _surely_set(tokens)  # eval, source, alias, a case: a variable may be set unseen
        for name in names:
            if _own_variable(name):
                return line
            found = list(re.finditer(r"(?<![A-Za-z0-9_\\])%s(?![A-Za-z0-9_])" % name, line))  # not the n of '%s\n'
            sets = [m for m in found if not inside(m.start()) and line[max(m.start() - 1, 0):m.start()] != "$" and not
                    (line[max(m.start() - 2, 0):m.start()] == "${" and line[m.end():m.end() + 1] == "}")]
            if not sets or sets[0] is not found[0] or not re.match(r"=\d+(?=[;\n]|$)", line[sets[0].end():]):
                return line  # used before it is set, or first set to something else
            if any(not re.match(r"=\d+(?=[\s;&|)]|$)", line[m.end():]) and
                   not (line.startswith("=$((", m.end()) and inside(m.end() + 1)) for m in sets):
                return line  # set another way somewhere: read n, for n in, n=text, n+=, n[0]=
            cut = sets[0].end() + re.match(r"=\d+", line[sets[0].end():]).end()
            cut -= sum(m.end() - m.start() - 1 for m in spans if m.end() <= cut)  # the same place in `out`
            first, _ = shell._lex(_strip_comments(out[:cut]))
            if not (_surely_set(first).get(name) or "").isdigit():
                return line  # inside an if, a loop, ( ) or after a && that may not run
        named = re.compile(r"(?:%s)(?![A-Za-z0-9_])" % "|".join(sorted(names))) if names else None
        k = 0
        while k < len(tokens):  # a variable set through a name the line does not show (read $v, printf -v "$v", read \n)
            token, k, rest = tokens[k], k + 1, []
            if not isinstance(token, shell.Word) or shell._name(token) not in SETS_BY_NAME | {"printf", "for"}:
                continue
            while k < len(tokens) and isinstance(tokens[k], shell.Word):
                rest.append(tokens[k])
                k += 1
            if shell._name(token) == "printf":
                if not rest or not rest[0].fixed or rest[0].text.startswith("-"):
                    return line
            elif any(not w.fixed or (named and named.match(w.text))
                     for w in (rest[:1] if shell._name(token) == "for" else rest)):
                return line
    except (shell.Unknown, RecursionError):
        return line
    return out


# ---- a variable set to a plain path on the same line ---------------------------------------------------------------
# `S=~/.hermes/skills/x; find $S -type f; cat $S/SKILL.md` is how a model looks at a folder. The guard reads such a
# line with the variable written out, but only when the line surely sets it: once, to a plain path, in a command of
# its own at the top of the line (no if, loop, ( ), pipe, & or && after another command), before every use, with
# nothing in the line that could set it another way (eval, source, read S, export S, a function, ${S:-x}, IFS).
# Any other variable stays unknown, and what a line does through an unknown variable is refused as before.

SETS_FIRST = re.compile(r"(?:^|[;&\n])[ \t]*[A-Za-z_][A-Za-z0-9_]*=")
UNSEEN = {"eval", "source", ".", "trap", "alias", "case", "select", "function", "coproc", "exec", "set", "shopt",
          "setopt", "emulate"}  # what can set a variable, or change how one is read, in a way the line does not show
OPENERS = {"if", "while", "until", "for", "{"}
BEFORE_A_COMMAND = {"then", "do", "else", "elif", "!", "time"}
RESOLVED_MAX = 20_000  # characters
MOVES_IFS = re.compile(r"(?<![A-Za-z0-9_])(?:IFS|HOME)(?![A-Za-z0-9_])\+?=|(?<![A-Za-z0-9_$])IFS(?![A-Za-z0-9_])")


def _resolved(line: str) -> str:
    """The line with each variable it surely sets to a plain path written out where it is used; the line itself when
    it sets none. A variable set from another one (D=$S/assets) is written out on the next pass. A line too long to
    be a look at a folder is left as it is."""
    return line if len(line) > RESOLVED_MAX else _resolved_at(line, str(Path.home()))


@functools.lru_cache(maxsize=32)
def _resolved_at(line: str, _home: str) -> str:  # kept per home folder: ~ and $HOME are written out
    line = _counted(line)
    for _ in range(4):
        found = _resolved_once(line)
        if found == line:
            break
        line = found
    return _unbraced(line)


BRACED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}(?![A-Za-z0-9_\[:])")


def _unbraced(line: str) -> str:
    """The line with ${NAME} written $NAME where both read the same (nothing after it that $NAME would take in):
    the plugin's shell reader keeps {NAME} for a reflex's templates, and a terminal call holds none."""
    if "${" not in line:
        return line
    expanded = _expanded_dollars(line)
    return BRACED.sub(lambda m: "$" + m.group(1) if m.start() in expanded else m.group(), line)


def _resolved_once(line: str) -> str:
    if "$" not in line or "<<" in line or shell.EXPANSION in line or shell.MARK in line or \
            not SETS_FIRST.search(line) or MOVES_IFS.search(line):
        return line
    try:
        tokens, _ = shell._lex(_strip_comments(line))  # a terminal call's line: {name} is plain text, ${NAME} a variable
        values = _surely_set(tokens)
    except (shell.Unknown, RecursionError):
        return line
    for name, value in values.items():
        line = _written_out(line, name, value)
    return line


def _surely_set(tokens: list) -> dict:
    """{name: plain value} for the variables a line sets in a command of its own at its top level, which nothing can
    skip. shell.Unknown for a line that holds what could set a variable unseen."""
    found, twice = {}, set()
    depth, words, redirected, target, before, last = 0, [], False, False, None, False

    def close(after):
        nonlocal depth, last
        k = 0
        while k < len(words) and _keyword(words[k], BEFORE_A_COMMAND):
            k += 1
        head = words[k] if k < len(words) else None
        name = shell._name(head) if head is not None else None
        sure = False
        if name in UNSEEN:
            raise shell.Unknown("a command that can set a variable unseen")
        if head is not None and _keyword(head, OPENERS):
            depth += 1
        elif head is not None and _keyword(head, CLOSERS):
            depth -= 1
            if depth < 0:
                raise shell.Unknown("a block closed before it opens")
        elif k == 0 and words and depth == 0 and not redirected:
            sets = [_assigned(w) for w in words]
            sure = all(sets) and (before in (None, ";", "\n") or (before == "&&" and last)) and \
                after in (None, ";", "\n", "&&")
            for name, value in sets if sure else ():
                if name in found or name in twice or value is None:
                    twice.add(name)
                    found.pop(name, None)
                else:
                    found[name] = value
        last = sure

    for token in tokens:
        if isinstance(token, shell.Word):
            if target:
                target = False
            else:
                words.append(token)
        elif token in shell.SEPARATORS or token in ("(", ")"):
            if words or redirected:
                close(token)
                before, words, redirected = token, [], False
            elif token != "\n":
                before = token  # a line break after a | or a && leaves the next command joined to the last
            if token in ("(", ")"):
                depth += 1 if token == "(" else -1
                before = token
                if depth < 0:
                    raise shell.Unknown("a ) with no (")
        elif token == ";;":
            raise shell.Unknown("a case")
        else:
            redirected = target = True
    if words or redirected:
        close(None)
    return found


def _expanded_dollars(line: str) -> set:
    """Where the shell expands a $ of the line: outside single quotes, not after a backslash."""
    spots, i, quote = set(), 0, None
    while i < len(line):
        c = line[i]
        if quote == "'":
            quote = None if c == "'" else quote
        elif c == "\\":
            i += 1
        elif c == "'" and quote is None:
            quote = "'"
        elif c == '"':
            quote = None if quote == '"' else '"'
        elif c == "$":
            spots.add(i)
        i += 1
    return spots


def _written_out(line: str, name: str, value: str) -> str:
    """The line with $NAME and ${NAME} replaced by the value, when the name stands nowhere else in it than where it
    is set, once, and then used: no second NAME=, no `read NAME`, no ${NAME:-x}, no $NAME[1] or $NAME:h (zsh)."""
    uses = list(re.finditer(r"\$(?:\{%s\}|%s(?![A-Za-z0-9_]))" % (name, name), line))
    named = list(re.finditer(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % name, line))
    sets = [m for m in named if line.startswith("=", m.end()) and line[max(m.start() - 1, 0):m.start()] not in ("$", "{")]
    if not uses or len(sets) != 1 or len(named) != 1 + len(uses):
        return line
    if any(m.start() < sets[0].start() or line[m.end():m.end() + 1] in ("[", ":") for m in uses):
        return line
    expanded, out, last = _expanded_dollars(line), [], 0
    for m in uses:
        if m.start() in expanded:
            out += [line[last:m.start()], value]
            last = m.end()
    return "".join(out) + line[last:]


def _moves_nothing(words, writes, subs=()) -> bool:
    if writes or not words:
        return False
    if all(_assigned(w) for w in words):  # S=/x, V=$(head -1 f): a variable for the line's next commands
        return all(not shell._why_line(sub, 1) for sub in [*subs, *(s for w in words for s in w.subs)])
    if not all(w.fixed for w in words):
        return False
    head, rest = shell._name(words[0]), [w.text for w in words[1:]]
    if head == "cd":
        return len(rest) <= 1
    if head != "hermes" or rest[:2] != ["habits", "run"]:
        return False
    return _dry_arguments(rest[2:])


def _dry_arguments(rest: list) -> bool:
    """The words after `hermes habits run` ask for a dry run of one target: only the options of DRY_RUN and --json,
    spelled in full (argparse accepts a prefix: --li is --live), one target."""
    return len(_run_targets(rest) or []) == 1


def _option(word: str) -> str:
    """An option of `hermes habits run` spelled as argparse reads it: --js is --json while it is the only option that
    starts so (--me is ambiguous, --li is --live); an exact name always wins."""
    name, eq, value = word.partition("=")
    if not name.startswith("--") or len(name) < 3 or name in RUN_OPTIONS:
        return word
    found = [option for option in RUN_OPTIONS if option.startswith(name)]
    return found[0] + eq + value if len(found) == 1 else word


def _run_targets(rest: list) -> list | None:
    """The targets of those words, or None when one of them is an option a dry run does not take."""
    targets, i = [], 0
    while i < len(rest):
        word, i = rest[i], i + 1
        word = _option(word)
        if word.partition("=")[0] in DRY_RUN and "=" not in word:
            if i >= len(rest) or rest[i].startswith("-"):
                return None
            i += 1
        elif word.startswith("-") and word.partition("=")[0] not in DRY_RUN and word != "--json":
            return None
        elif not word.startswith("-"):
            targets.append(word)
    return targets


# ---- `hermes habits <verb>`: the verb is read the way the shell (or Python) reads it, never matched as text ----------
# The verbs that change nothing; any other word after `habits` (promote, undo, resume, reset, retire, pause, cleanup,
# a verb that does not exist yet, a word the reader cannot fix) is the user's. `run` only in the form _dry_arguments
# accepts. A call that names hermes and habits and the reader cannot read is refused too.
SAFE_VERBS = {"list", "show", "view", "check", "tick", "help", "run", "-h", "--help"}
VALUE_FLAGS = {"--profile", "-p", "--config", "-c", "--home"}  # hermes's own options that take a value
STATE_WORDS = {"promote", "undo", "resume", "reset", "retire", "learn", "pause", "cleanup"}
SHELL_DEPTH = 4  # lines inside lines (bash -c '…')


def _hermes_word(word) -> bool:
    return word.fixed and posixpath.basename(word.text) == "hermes"


def _live_draft(rest: list) -> bool:
    """A live run of a graph file by its path, in the other options of a dry run: it is no habit's, records nothing in
    the ledger (cli_run), and a draft's trial in a scratch folder is part of writing one. A live run of a habit by
    its name is the user's."""
    targets = _run_targets([w for w in rest if w != "--live"])
    return "--live" in rest and targets is not None and len(targets) == 1 and \
        ("/" in targets[0] or targets[0].endswith((".yml", ".yaml")))


def _verb_refusal(words: list, code: bool = False) -> str | None:
    """Why `hermes habits …` as these words (fixed strings or shell words with .fixed/.text) is refused, or None. The
    words are a whole command line's, starting anywhere before `habits`."""
    texts = [w.text for w in words]
    fixed = [w.fixed for w in words]
    if "habits" in [t for t, f in zip(texts, fixed) if f]:
        k = next(i for i, (t, f) in enumerate(zip(texts, fixed)) if f and t == "habits")
        # something that is no hermes before it: grep habits file, echo habits, cat habits.md
        before = words[:k]
        if not any(not w.fixed or "hermes" in w.text.lower() for w in before):
            # a program of another name run as `X habits promote` (a copy or a link of hermes): unless X only reads
            nxt = words[k + 1] if k + 1 < len(words) else None
            if not code and nxt is not None and nxt.fixed and nxt.text in STATE_WORDS and shell._why(words, False, (), 0):
                return UNREADABLE
            return None
        if k + 1 >= len(words):
            return UNREADABLE if code else None  # code may still add the verb to its list or string
        verb = words[k + 1]
        if not verb.fixed or verb.text not in SAFE_VERBS:
            return UNREADABLE
        if verb.text == "run":
            after = words[k + 2:]
            if not all(w.fixed for w in after) or not _dry_arguments([w.text for w in after]) and \
                    not _live_draft([w.text for w in after]):
                return LIVE
        return None
    # no `habits` to be read, yet a hermes whose subcommand is a word the reader cannot fix (hermes $X promote)
    for h, word in enumerate(words):
        if not _hermes_word(word):
            continue
        rest, i = words[h + 1:], 0
        while i < len(rest) and rest[i].fixed and rest[i].text.startswith("-"):
            i += 2 if rest[i].text in VALUE_FLAGS else 1
        if i < len(rest) and not rest[i].fixed:
            return UNREADABLE
    if any(not w.fixed for w in words) and STATE_WORDS & set(texts):
        return UNREADABLE
    return None


class _Word:
    """A string of Python code as a word of the same shape as the shell reader's."""

    def __init__(self, text, fixed=True):
        self.text, self.fixed = text, fixed


class _Fold(ast.NodeTransformer):
    """Joins what Python would join before it runs: 'pro' + 'mote', ['hermes', 'habits'] + ['x']."""

    def visit_BinOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Add):
            a, b = node.left, node.right
            if all(isinstance(n, ast.Constant) and isinstance(n.value, str) for n in (a, b)):
                return ast.copy_location(ast.Constant(a.value + b.value), node)
            if isinstance(a, type(b)) and isinstance(a, (ast.List, ast.Tuple)):
                return ast.copy_location(type(a)(elts=a.elts + b.elts, ctx=ast.Load()), node)
        return node


def _code_refusal(code: str) -> str | None:
    """Why code that may run `hermes habits …` is refused: each string is read as a shell line, each list as the
    words of a command; a string built at run time (f-string, format, a variable in a list, a word kept in a
    variable) is refused. Code that does not parse is refused: its text cannot be read."""
    try:
        tree = _Fold().visit(ast.parse(code))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return UNREADABLE
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text, parent = node.value, parents.get(node)
            if not re.search(r"\bhabits\b", text):
                continue
            if isinstance(parent, (ast.BinOp, ast.JoinedStr, ast.AugAssign, ast.FormattedValue)) or \
                    isinstance(parent, ast.Attribute):  # 'hermes habits {}'.format(verb), an f-string, a +
                return UNREADABLE
            if text.strip() == "habits" and not isinstance(parent, (ast.List, ast.Tuple)):
                return UNREADABLE  # the word kept in a variable, to be joined with a verb later
            if not isinstance(parent, (ast.List, ast.Tuple)) or " " in text.strip():
                why = _line_refusal(text)
                if why and why is not UNNAMED:  # a sentence or a query is no line: only what it would do as one
                    return why
        elif isinstance(node, (ast.List, ast.Tuple)):
            words = [_Word(e.value) if isinstance(e, ast.Constant) and isinstance(e.value, str)
                     else _Word("\x00", False) for e in node.elts]
            if any(w.fixed and w.text in ("habits",) or _hermes_word(w) for w in words):
                why = _verb_refusal(words, code=True)
                if why:
                    return why
    return None


def hermes_habits_refusal(tool: str, args: dict) -> str | None:
    """The refusal for a terminal or execute_code call that may run `hermes habits …`, or None. A shell line is read
    by the plugin's shell reader; code by its syntax tree; what neither can read is refused."""
    if tool not in ("terminal", "execute_code"):
        return None
    text = str(args.get("code" if tool == "execute_code" else "command") or "")
    plain = QUOTES.sub("", text).lower()
    if tool == "execute_code":
        if not any(key in plain for key in ("habits", "hermes", "\\x", "\\u", "chr(", "bytes(")):
            return None
        return _code_refusal(text)
    return shell_refusal(text, 0, True, _Ctx(str(args.get("workdir") or "")))


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


_FOLDERS: dict = {}
FOLDERS_SECONDS = 1.0


def forget_folders():
    """Drop the list of habit folders: a habit was saved, removed or may have been added by a call."""
    _FOLDERS.clear()


def _folders() -> dict:
    """catalog.folders(), listed once for the calls of one moment (two recursive globs and a read of each habit's SKILL.md
    per listing: 25 ms with 300 skills). The guard drops it after every call that is no read, and the plugin's own saves
    drop it, so a call that could touch a habit always sees the habits as they are."""
    now = time.monotonic()
    if _FOLDERS and _FOLDERS["fn"] is catalog.folders and now - _FOLDERS["at"] < FOLDERS_SECONDS:
        return dict(_FOLDERS["value"])
    value = catalog.folders()
    _FOLDERS.update(fn=catalog.folders, at=now, value=dict(value))
    return value


ANYWHERE = "?"  # edited_habit: a habit's file may be written where the guard cannot tell


def edited_habit(tool: str, args: dict) -> str | None:
    """The saved habit whose SKILL.md, reflex.yml, HABIT.yaml or LEARNED.md this call would rewrite, if any
    (habit_save's own writes go through dispatch_tool, which Hermes does not guard; SAVING covers a Hermes that would). A shell, code or
    file write that names one of those files is read as the shell and the disk read it: its words with quotes and
    escapes undone, in any case (a cd into the folder, a relative path, a doubled slash), and where its writes land,
    links resolved (_landing). While a habit exists, one whose writes land where the guard cannot tell is ANYWHERE:
    a relative path from the session's own folder (Hermes keeps its last cd, in another call), a variable or a glob,
    code that writes by itself (an active habit can be replaced by a cd, then a cp)."""
    if tool == "skill_manage":
        names = _skill_names(args, EDITS)
        folders = _folders() if names else {}
        return _habit_of(names, folders)
    if tool not in ("write_file", "patch", "terminal", "execute_code"):
        return None
    if tool == "execute_code" and not _code_writes(str(args.get("code") or "")):
        # Code that only reads (open() to read, json.load, read_file): reading is never an edit. What it hands to
        # Hermes's own tools is read as those calls; when that cannot be told, the code is read by its words.
        handed = _handed_on(str(args.get("code") or ""))
        if handed is not None:
            return next((hit for hit in (edited_habit(t, a) for t, a in handed) if hit), None)
    if tool == "terminal":
        command = str(args.get("command") or "")
        hit = habit_reach(command, _folders(), str(args.get("workdir") or "")) \
            if not SAVING.get() and not reads(command) else None  # a read reaches no habit: no folder to list
        if hit:
            return hit
    text = " ".join(str(args.get(key) or "") for key in ("path", "patch", "command", "code"))
    if not _names(text, OWN_NAMES, tool == "execute_code") or tool == "terminal" and reads(text):
        return None
    folders = _folders()
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


HANDED_ON = {"terminal": "command", "write_file": "path", "patch": "path"}  # the tool: what says where it acts
HANDS_ON_MORE = {"skill_manage", "execute_code", "delegate_task", "cronjob_manage", "process_manage"}


def _handed_on(code: str) -> list | None:
    """The calls code hands to Hermes's own tools (from hermes_tools import terminal, write_file, patch), each as
    that tool's call when its command or path is plain text; None when one is not, or for a tool that carries more
    than a line or a path. Such a call meets this guard again when it runs: this reading only gives the refusal at
    once, with its reason."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name in HANDS_ON_MORE:
            return None
        key = HANDED_ON.get(name)
        if key:
            value = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == key), None)
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                return None
            found.append((name, {key: _resolved(value.value) if name == "terminal" else value.value}))
    return found


def _skill_names(args: dict, actions: set) -> list:
    """The skill each operation of a skill_manage call acts on, for these actions. Hermes copies the call's own name
    (and action) into each operation that has none, and resolves category/name to the name."""
    ops = args.get("operations") if isinstance(args.get("operations"), list) else [args]
    found = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        action = op.get("action") or args.get("action")
        name = op.get("name") if op.get("name") not in (None, "") else args.get("name")
        if action in actions:
            found.append(name if isinstance(name, str) else ANYWHERE)
    return found


def _habit_of(names: list, folders: dict) -> str | None:
    """The saved habit one of these names is (case and category ignored), ANYWHERE for a name the guard cannot read
    while a habit exists."""
    if not folders:
        return None
    keys = {k.casefold(): k for k in folders}
    for name in names:
        if name == ANYWHERE:
            return ANYWHERE
        found = keys.get(name.strip().split("/")[-1].strip().casefold())
        if found:
            return found
    return None


def unplaced(tool: str, args: dict) -> bool:
    """A file tool's write of assets/reflex.yml or HABIT.yaml on a relative path: the guard cannot tell which skill
    it lands in."""
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
    return [(c.words, c.targets, c.join) for c in _parse(line, check=True)]


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
    full with their links resolved, in lower case; _folders() asks Hermes the same way."""
    try:
        from tools.skills_tool import skill_search_dirs
        roots, _active = skill_search_dirs()
    except Exception:  # outside Hermes (tests) or an older Hermes
        roots = []
    found = [str(root) for _tier, root in roots if str(root).strip("/")]
    return [os.path.realpath(os.path.expanduser(r)).casefold()
            for r in found + [str(folder.parent) for folder in _folders().values()]]


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


def _code_writes(code: str, sql: bool = False) -> bool:
    """Whether code may write a file by itself, or run what may: a module but CODE_MODULES, a name of CODE_WRITES
    (exec… and spawn… too), open() but to read, or code that is no Python. A str's replace() of two plain texts and
    a dict's copy() are no such call. sql: sqlite3 asked only to select (each execute() given a plain select) is a
    read too."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return True
    modules_read = CODE_MODULES | ({"sqlite3"} if sql else set())
    fine = set()  # ast.walk sees a call before its function's name
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            plain = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
                     and "/" not in a.value]
            if not node.keywords and (name == "replace" and len(node.args) == len(plain) == 2
                                      or name == "copy" and not node.args):
                fine.add(id(node.func))
            first = node.args[0] if node.args else None
            if sql and name == "execute" and isinstance(node.func, ast.Attribute) and isinstance(first, ast.Constant) \
                    and isinstance(first.value, str) and first.value.strip() and all(
                        SELECTS.match(piece) for piece in first.value.split(";") if piece.strip()):
                fine.add(id(node.func))
            if name == "open" and _opens_to_write(node):
                return True
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            taken = [a.name for a in node.names] if isinstance(node, ast.ImportFrom) else []
            if any(m.split(".")[0] not in modules_read for m in modules) or \
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
    """The skill a call would turn into a habit, writing its assets/reflex.yml (or HABIT.yaml) outside habit_save. A
    shell or code write that names the file is one unless it surely lands outside every skill (_landing, _skilled); a file tool's
    write is read by its path, links resolved."""
    if tool in ("terminal", "execute_code"):  # a shell write: cp, tee, a redirection; or code
        command = str(args.get("command") or args.get("code") or "")
        if not _names(command, GRAPH_NAMES, tool == "execute_code") or tool == "terminal" and reads(command):
            return None
        if tool == "execute_code" and not _code_writes(command):
            handed = _handed_on(command)  # code that only reads a reflex.yml makes no habit; a call it hands on may
            if handed is not None:
                return next((hit for hit in (made_habit(t, a) for t, a in handed) if hit), None)
        found = HABIT_YAML.search(_paths(QUOTES.sub("", command) if tool == "terminal" else command))
        if found:
            return found.group(1)
        places = _landing(tool, args, GRAPH_NAMES, door=True)
        return "this skill" if places is None or any(_skilled(p) for p in places) else None
    if tool in ("write_file", "patch"):  # Hermes does not protect the skills folder from its file tools
        given = posixpath.expanduser(str(args.get("path") or ""))
        path = posixpath.normpath(os.path.realpath(given) if posixpath.isabs(given) else given).lower()
        found = re.search(r"(?:^|/)(?:skills/(?:.+/)?)?([^/]+)/assets/(?:reflex\.yml|habit\.yaml)$", path) \
            if unplaced(tool, args) else re.search(r"/skills/(?:.+/)?([^/]+)/assets/(?:reflex\.yml|habit\.yaml)$", path)
        if found:
            return found.group(1)
        if posixpath.isabs(given) and path.endswith(("/assets/reflex.yml", "/assets/habit.yaml")) and \
                any(_inside(path, root) for root in _roots()):  # a skills folder of another name
            return path.split("/")[-3]
        unplaced_graph = unplaced(tool, args) and path.endswith(("assets/reflex.yml", "assets/habit.yaml"))
        return "this skill" if unplaced_graph else None
    if tool != "skill_manage":
        return None
    ops = args.get("operations") if isinstance(args.get("operations"), list) else [args]
    for op in ops:
        if not isinstance(op, dict) or op.get("action") not in ("write_file", "patch"):
            continue
        if posixpath.normpath(str(op.get("file_path") or "")).lower() in (
                str(catalog.HABIT_FILE).lower(), str(catalog.LEGACY_FILE).lower()):
            return str(op.get("name") or "this skill")
    return None


def changes_state(tool: str, args: dict, home=None) -> str | None:
    """Why this call would change a habit's state outside the user's hands: a `hermes habits promote|undo|resume|
    reset|retire`, a write into the plugin's own folder or its code, which a cd, a variable, a glob or a path built in
    code can spell (a write that names both plugin-data and habits, or habits.db next to a Hermes folder), a `hermes
    config set` of the plugin's settings, a script written to do one of these, or Hermes's own code for its /habits
    command, run by the model (a script it wrote once did), and /habits with a state word given to hermes as a query.
    None for anything else: a read of that folder (a select, a copy out), a file that only mentions it."""
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
        if tool == "execute_code" and "habits_off" in plain.lower() and _code_writes(str(args.get("code") or "")):
            return OFF_SWITCH
    if tool == "terminal":
        why = _workdir_reach(str(args.get("workdir") or ""), str(args.get("command") or ""), home) or \
            terminal_reach(str(args.get("command") or ""), home) or _diff_state(str(args.get("command") or "")) or \
            _script_words_state(str(args.get("command") or ""), home)
        if why:
            return why
    elif tool in ("write_file", "patch"):
        why = file_reach(str(args.get("path") or ""), home) or written_script(tool, args, home)
        if why:
            return why
        path = str(args.get("path") or "")
        if _full(path) and not args.get("patch"):
            # a full path was judged by where it lands; one in another Hermes folder (a profile's plugin-data/habits)
            # is refused by its words; what a document says is not an act
            return OWN_FILES if OWN_FOLDERS.search(path) and _hints_hermes(path) and "plugin-data" in path.lower() \
                else None
    if not _names_ledger(text):
        return None
    if tool == "terminal" and (reads(text) or _only_copied_out(text, LEDGER_WORDS)):
        return None  # a read as every other gate of the guard reads one (a cd, a variable for the next command)
    if tool == "execute_code" and not _code_writes(str(args.get("code") or ""), sql=True):
        return None  # code that only reads (a select) changes nothing
    return OWN_FILES


LEDGER_WORDS = re.compile(r"habits\.db|plugin-data", re.I)


def _names_ledger(text: str) -> bool:
    """A text that names the plugin's ledger or its folder: plugin-data together with habits, or habits.db next to a
    Hermes folder. The bare name habits.db is anyone's (a habit tracker of the user's keeps one)."""
    plain = QUOTES.sub("", text)
    lowered = plain.lower() + " " + text.lower()
    if "plugin-data" in lowered and "habits" in lowered:
        return True
    return bool(LEDGER.search(text) or LEDGER.search(plain)) and _hints_hermes(plain)


COPIES = {"cp", "scp", "ditto", "install", "rsync"}


def _only_read_word(w, cmd) -> bool:
    """A plain path a command only reads though the line is no plain read: what cp, scp, ditto, install or rsync
    copy from (every operand but the last), what tar puts into a new archive, what a [ ] test looks at and what
    sqlite3 only selects from. A backup of Hermes's files, or a look at them, is not a change of them."""
    words = cmd.words
    if not words or not w.fixed or _regex(w)[0] is not None or any(w is t for t in cmd.targets):
        return False
    k = _head(cmd)
    if k < len(words) and words[k].text in ("[", "[[", "test"):
        return True  # a test of the file: [ -f <it> ]
    if _history_read(cmd):
        return True  # sqlite3 <it> 'select …', sqlite3 -readonly <it>
    name, options = shell._name(words[0]), [x.text for x in words[1:] if x.text.startswith("-")]
    if name in COPIES:
        operands = [x for x in words[1:] if not x.text.startswith("-")]
        if any(o.startswith(("-t", "--target", "--remove-source", "--backup", "--suffix", "-S", "-b")) for o in options):
            return False
        return len(operands) >= 2 and operands[-1] is not w and any(x is w for x in operands)
    if name in ("tar", "bsdtar") and len(words) > 2:
        first = words[1].text
        mode = first.lstrip("-")
        if first.startswith("--") or not mode.isalpha() or "c" not in mode or set(mode) & set("xrutAd") or \
                any(o.startswith(("--remove-files", "--delete", "-C", "--directory")) for o in options):
            return False
        archive = next((words[k + 1] for k, x in enumerate(words[1:-1], 1) if (k == 1 or x.text.startswith("-"))
                        and not x.text.startswith("--") and "f" in x.text), None)
        return archive is not None and archive is not w and any(x is w for x in words[2:])
    return False


def _only_copied_out(text: str, names) -> bool:
    """Every word of the line that names these files is one its command only reads."""
    pairs = _line_words(text)
    if not pairs:
        return False
    named = [(w, c) for w, c in pairs if names.search(w.text)]
    return bool(named) and all(_only_read_word(w, c) for w, c in named)


ACTS = {"open", "system", "popen", "run", "call", "check_call", "check_output", "Popen", "connect", "write_text",
        "write_bytes", "unlink", "remove", "rename", "replace", "rmtree", "copy", "copy2", "copyfile", "copytree", "move",
        "mkdir", "makedirs", "touch", "exec", "eval", "__import__", "import_module", "execute", "executescript", "main",
        "approve", "set_status", "putenv", "symlink", "symlink_to", "chmod", "truncate"}


def _only_defines(tree) -> bool:
    """A Python file that only defines: imports, functions, classes and plain assignments, nothing run when it is
    read. A module or a test file is source: it acts when something runs it, and what runs it is read then."""
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Pass)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
            if not any((getattr(c.func, "attr", None) or getattr(c.func, "id", None)) in ACTS for c in calls):
                continue
        return False
    return True


def _runs_refused(tree, home) -> str | None:
    """A program the code runs by a command that folds (subprocess.run([...]), os.system('…')), read like a terminal
    line: its `hermes habits` verb, its write to the ledger or the settings."""
    ev = _Eval(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not RUNNER.match(ev.dotted(node.func) or ""):
            continue
        given = [ev.value(a) for a in node.args] or [ev.value(k.value) for k in node.keywords
                                                     if k.arg in ("args", "command", "cmd")]
        if SPAWNERS.match(ev.dotted(node.func) or ""):
            given = [[x for p in given if p is not UNKNOWN for x in (p if isinstance(p, (list, tuple)) else [p])]]
        line = _argv(given[0]) if given and given[0] is not UNKNOWN else None
        if line:
            why = shell_refusal(line, 1, True, _Ctx()) or changes_state("terminal", {"command": line}, home)
            if why:
                return why
    return None


def written_script(tool: str, args: dict, home=None) -> str | None:
    """Why a file the model writes is a script that acts on the plugin's world, or None. Only a script is read (a
    shell or Python suffix, a #! first line): prose, data, a test or source code that mentions habits.db or
    config.yaml is ordinary work. A shell script is read like the lines it holds. A Python file is read for the
    programs it runs; one that also acts when run (anything but definitions at its top) is read like code run now.
    A patch is judged on the file it leaves, when that file can be read."""
    path = str(args.get("path") or "")
    content = str(args.get("content") or args.get("new_string") or "")
    if not content or not _is_script(path, content):
        return None
    python = path.lower().endswith(".py") or (content.startswith("#!") and "python" in content.split("\n", 1)[0])
    whole = content
    if tool == "patch" and args.get("new_string") is not None:
        full = _full_path(path, None)
        try:
            if full and os.path.isfile(full) and os.path.getsize(full) <= SCRIPT_BYTES:
                before = Path(full).read_text(errors="replace")
                old = str(args.get("old_string") or "")
                whole = before.replace(old, content) if old and old in before else before + "\n" + content
        except OSError:
            pass
    if not python:
        lines = "\n".join(line for line in whole.split("\n") if not line.lstrip().startswith("#"))
        return shell_refusal(lines, 1, False, _Ctx()) or script_state(whole, False, home)
    try:
        tree = ast.parse(textwrap.dedent(whole))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return script_state(whole, True, home)
    return _runs_refused(tree, home) or (None if _only_defines(tree) else script_state(whole, True, home))


HERMES_HINT = re.compile(r"\.hermes|hermes_home|get_hermes_home", re.I)
SETTINGS_NAME = re.compile(r"conf(?:ig)?(?:\.y|\*|\?)", re.I)  # config.yaml, config.yml, conf*, config*, conf?


def _hints_hermes(plain: str) -> bool:
    return bool(HERMES_HINT.search(plain)) or str(_hermes_home()) in plain


def _names_settings(text: str, python: bool = False) -> bool:
    """Whether a script's text, if it ran, would name Hermes's settings file: Hermes's folder and config.y…/conf* in it,
    and something in it that can write (a plain read of the settings is no change)."""
    plain = QUOTES.sub("", text)
    if not (_hints_hermes(plain) and SETTINGS_NAME.search(plain)):
        return False
    if python:
        return _code_writes(text)
    lines = [line for line in text.split("\n") if SETTINGS_NAME.search(QUOTES.sub("", line))
             and not line.lstrip().startswith("#")]
    return any(not reads(line) and not _only_copied_out(line, SETTINGS_NAME) for line in lines)


def script_state(text: str, python: bool, home=None) -> str | None:
    """Why a script's text, run later by `bash file` or `python3 file`, would change a state or the settings: the
    ledger or the settings file named in it, a `hermes habits` state verb, a write into the plugin's folder. The same
    reading as a typed line, applied to the file at the moment it runs (and, in changes_state, when it is written):
    a variable the script surely sets to a plain path is read as that path."""
    text = text if python else _resolved(text)
    why = changes_state("execute_code" if python else "terminal", {"code" if python else "command": text}, home)
    if why:
        return why
    return OWN_SETTINGS if _names_settings(text, python) else None


def _script_words_state(command: str, home=None) -> str | None:
    """Any program that is handed a script file by name (at, crontab, screen, busybox sh, ssh host bash f, xargs -a f…)
    runs it, whatever its own name: a script file named in a line that is not a read is read like one run by bash."""
    if reads(command):
        return None
    pairs = _line_words(command)
    if not pairs:
        return None
    done = 0
    for w, cmd in pairs:
        if not w.fixed or w.text.startswith("-") or w is (cmd.words[0] if cmd.words else None):
            continue
        suffix = Path(w.text).suffix.lower()
        if suffix not in SCRIPT_SUFFIXES:
            continue
        place = _plain_path(w)
        try:
            path = Path(place) if place else None
            if path is None or not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(errors="replace")
        except OSError:
            continue
        done += 1
        if done > 8:
            break
        why = script_state(text, suffix == ".py", home)
        if why:
            return why
    return None


def _diff_state(command: str) -> str | None:
    """A patch, git apply or ed that reads a diff naming the ledger or the settings file: those files are the user's."""
    pairs = _line_words(command)
    if not pairs:
        return None
    cwd = ""
    for cmd in dict.fromkeys(c for _w, c in pairs):
        if len(cmd.words) == 2 and shell._name(cmd.words[0]) == "cd":
            place = _plain_path(cmd.words[1])
            cwd = os.path.realpath(place) if place else LOST
            continue
        operands = _writes_by_content(cmd) if cmd.words else None
        if operands is None:
            continue
        name = shell._name(cmd.words[0])
        if name not in ("patch", "ed", "ex") and not (name == "git" and any(w.fixed and w.text in ("apply", "am")
                                                                          for w in cmd.words)):
            continue
        for text in _diff_texts(operands, cmd, _folder_of(cmd, cwd)) or []:
            plain = QUOTES.sub("", text)
            if _names_ledger(text):  # plugin-data with habits, or habits.db next to a Hermes folder: a bare habits.db
                return OWN_FILES     # is anyone's (the user's own habit tracker keeps one)
            here = _folder_of(cmd, cwd)
            lost = here is LOST or (isinstance(here, str) and here and _inside(here.casefold(), os.path.realpath(
                _hermes_home()).casefold()))  # a cd to a variable, or into Hermes's own folder
            if lost and (SETTINGS_NAME.search(plain) or re.search(r"(?m)^[-+]{3} \S*habits\.db", plain)):
                return OWN_SETTINGS if SETTINGS_NAME.search(plain) else OWN_FILES
            if SETTINGS_NAME.search(plain) and _hints_hermes(plain) or re.search(r"(?m)^[-+]{3} \S*\.hermes/config",
                                                                                      plain):
                return OWN_SETTINGS
    return None


# ---- what a call can reach: the shell line, the script it runs, the code, the paths ------------------------------------
# The three gates above key on a literal token (a file name, plugin-data, an unquoted state verb). This part reads what
# a call can DO: a program run by name or by a path, a script file read at call time, code folded as Python folds it,
# and a path as the disk resolves it (globs, variables, links). What it cannot read on the sensitive surface is refused.

CODE_REACH = ("Habits could not tell what this code runs or reaches: a program built at run time, a module or attribute "
              "picked by a computed name, the plugin's own code or settings, or a write where the plugin's folder, the "
              "settings file or a habit's files may be. Run programs with the terminal tool, where each line is read; "
              "write files with write_file; the Habits ledger and settings are the user's (/habits).")
TOO_LONG = "Habits cannot read a call this long ({n} characters): split it, or write the text to a file with write_file."
MAX_TEXT = 200_000
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "ash"}
SOURCES = {"source", "."}
OTHER_LANGS = {"perl", "ruby", "node", "nodejs", "php", "lua", "osascript", "tclsh", "rscript", "deno", "bun"}
WRAPPERS = {"env", "command", "builtin", "nohup", "time", "nice", "ionice", "timeout", "sudo", "doas", "stdbuf",
            "setsid", "exec", "watch", "xargs", "parallel", "caffeinate", "unbuffer", "chronic", "find", "arch",
            "flock", "strace", "busybox", "toybox"}
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "time", "coproc"}
PYTHON = re.compile(r"python[\d.]*|pypy[\d.]*|ipython[\d.]*")
ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
SHELLISH = re.compile(r"\b(?:sh|bash|zsh|dash|ksh|eval|source|xargs)\b")
INTERPRETERS = re.compile(r"\b(?:python[\d.]*|pypy[\d.]*|perl|ruby|node|php|lua|osascript|deno|bun)\b|(?:^|[\s;&|])\.\s")
SCRIPT_SUFFIXES = {".sh", ".bash", ".zsh", ".ksh", ".command", ".py"}
HEREDOC = re.compile(r"(?<!<)<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([A-Za-z_]\w*))")
HEREDOCS = itertools.count()
HOME_FILES = ("off", "paused", "habits.db", "habits.db-wal", "habits.db-shm", "habits.db-journal")
DISTINCT = re.compile(r"plug|habits|paused|config\.y", re.I)
BAD_IMPORTS = {"ctypes", "cffi", "posix", "_posixsubprocess", "nt", "runpy", "imp"}
BAD_NAMES = {"save_plugin_setting", "save_plugin_settings", "set_config_value", "save_config", "globals",
             "__builtins__", "__loader__", "__spec__"}
STATE_MOVERS = ("habits.ledger", "habits.cockpit")  # the modules that move a state
RUNNER = re.compile(r"^(?:subprocess|pty|pexpect|commands|plumbum|sh)\.\w+$|^os\.(?:system|popen|exec\w*|spawn\w*|"
                    r"posix_spawn\w*|startfile)$|^asyncio\.create_subprocess_\w+$")
SPAWNERS = re.compile(r"^os\.(?:exec\w*|spawn\w*|posix_spawn\w*)$")
IMPORTERS = {"importlib.import_module", "import_module", "__import__", "importlib.__import__",
             "importlib.util.spec_from_file_location", "importlib.machinery.SourceFileLoader",
             "importlib.util.find_spec", "pkgutil.get_loader", "pkgutil.find_loader"}
DANGEROUS_ATTRS = {"system", "popen", "run", "call", "Popen", "check_output", "check_call", "import_module",
                   "getoutput", "getstatusoutput", "spawn", "startfile"}
DISCOVERY = {"glob", "iglob", "listdir", "scandir", "walk", "iterdir", "rglob", "fnmatch", "find"}
MUTATORS = {"append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse", "update", "add", "discard",
            "setdefault", "popitem"}
CODE_SENSITIVE = re.compile(r"plug|habit|paused|config|skill|reflex|learned|\.hermes|hermes_home|state\.db", re.I)
STRING_BUILDERS = {"chr", "join", "joinpath", "decode", "bytes", "bytearray", "reversed", "format", "b64decode",
                   "fromhex", "replace", "translate", "getattr", "eval", "exec", "compile", "expanduser", "Path",
                   "glob", "iglob", "listdir", "walk", "scandir", "iterdir", "rglob", "home", "environ", "getenv",
                   "vars", "globals", "locals", "__import__", "import_module"}
GLUERS = {"join", "joinpath", "format", "replace", "glob", "iglob", "rglob", "listdir", "scandir", "iterdir", "walk",
          "with_name", "with_suffix", "relative_to"}
UNKNOWN = object()


class _Ctx:
    """What one reading of a call carries: where it runs, the heredoc bodies cut out of it, whether it writes."""

    def __init__(self, workdir: str = ""):
        self.workdir = str(Path(workdir).expanduser()) if workdir and _full(workdir) else None
        self.bodies: dict = {}
        self.writes = False
        self.off = False  # the line names HABITS_OFF


STDIN_PATH = re.compile(r"-|/dev/stdin|/dev/fd/\d+|/proc/(?:self|\d+)/fd/\d+|/dev/tty")
# Programs a here-string or a heredoc is only data to; any other program may run it (ssh, at, an interpreter)
DATA_SINKS = {"cat", "tee", "grep", "egrep", "fgrep", "rg", "wc", "sed", "awk", "head", "tail", "sort", "uniq", "tr",
              "cut", "jq", "yq", "less", "more", "diff", "cmp", "patch", "git", "pbcopy", "base64", "md5", "md5sum",
              "shasum", "sha256sum", "read", "echo", "printf", "true", "curl", "wget", "mail", "gh", "hc", "tar", "xxd",
              "od", "strings", "column", "nl", "rev", "fold", "fmt", "paste", "comm", "iconv", "sponge", "dd"}


class _Cmd:
    def __init__(self):
        self.words, self.targets, self.here, self.stdin, self.fds = [], [], [], [], []
        self.join, self.piped, self.used = "", False, False


def _strip_comments(line: str) -> str:
    """The line without its # comments (a # that starts a word, outside quotes), as the shell reads it."""
    out, quote, start, i, n = [], None, True, 0, len(line)
    while i < n:
        c = line[i]
        if quote == "'":
            out.append(c)
            quote = None if c == "'" else quote
        elif quote == '"':
            out.append(c)
            if c == "\\" and i + 1 < n:
                i += 1
                out.append(line[i])
            elif c == '"':
                quote = None
        elif c == "\\" and i + 1 < n:
            out += [c, line[i + 1]]
            i, start = i + 1, False
        elif c in "'\"":
            quote, start = c, False
            out.append(c)
        elif c == "#" and start:
            while i < n and line[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
            start = c in " \t\n;|&("
        i += 1
    return "".join(out)


def _heredocs(line: str) -> tuple:
    """The line with each heredoc turned into a here-string of a marker, and the bodies by marker. A heredoc with no
    closing line is left as it is (the reader meets the <<)."""
    if "<<" not in line:
        return line, {}
    lines, out, bodies, i = line.split("\n"), [], {}, 0
    while i < len(lines):
        text, i = lines[i], i + 1
        found = list(HEREDOC.finditer(text))
        if not found:
            out.append(text)
            continue
        take, here, ok = 0, [], True
        for m in found:
            delim = next(g for g in m.groups()[1:] if g is not None)
            body = []
            while i + take < len(lines) and (lines[i + take].strip() if m.group(1) else lines[i + take]) != delim:
                body.append(lines[i + take])
                take += 1
            if i + take >= len(lines):
                ok = False
                break
            take += 1
            here.append("\n".join(body))
        if not ok:
            out.append(text)
            continue
        names = []

        def mark(m):
            names.append(f"HEREDOC{next(HEREDOCS)}")
            return f"<<< {names[-1]}"
        out.append(HEREDOC.sub(mark, text))
        bodies.update(zip(names, here))
        i += take
    return "\n".join(out), bodies


def _parse(line: str, check: bool = False) -> list:
    """The simple commands of a line as _Cmd. With check, a form the plugin's shell reader refuses (a heredoc, ( ), a
    <( )) raises shell.Unknown; without, ( ) and ;; only separate and a stray operator is skipped."""
    marked = shell._marked(line)
    if check:
        shell._commands(marked)  # its refusals
    tokens, _ = shell._lex(marked)
    found, cur, i = [], _Cmd(), 0
    while i < len(tokens):
        token, i = tokens[i], i + 1
        if isinstance(token, shell.Word):
            cur.words += shell._braces(token)
            cur.used = True
        elif token in shell.SEPARATORS or token in (";;", "(", ")"):
            if cur.used:
                cur.join = token
                found.append(cur)
            piped = token in ("|", "|&")
            cur = _Cmd()
            cur.piped = piped
        else:
            m = shell.REDIRECT.match(token)
            if not m or i >= len(tokens) or not isinstance(tokens[i], shell.Word):
                if check:
                    raise shell.Unknown("a redirection without its target")
                continue
            target, i, cur.used = tokens[i], i + 1, True
            fd, op = m.groups()
            if op == "<<<":
                cur.here.append(target)
            elif op == "<":
                (cur.stdin if fd in ("", "0") else cur.fds).append(target)
            joined = op == ">&" and (target.text.isdigit() or target.text == "-")
            if op in TARGETS and not joined and target.text != "/dev/null":
                cur.targets.append(target)
    if cur.used:
        found.append(cur)
    return found


def _head(cmd: _Cmd) -> int:
    k = 0
    while k < len(cmd.words) and (ASSIGN.match(cmd.words[k].text) or cmd.words[k].text in KEYWORDS):
        k += 1
    return k


def shell_refusal(line: str, depth: int = 0, strict: bool = True, ctx: _Ctx | None = None) -> str | None:
    """Why this shell line is refused, or None. Each command is read for the `hermes habits` verb it runs, for a
    program it cannot name, for a shell or interpreter and the program that shell reads (a -c string, a here-string,
    a heredoc, a script file read now, a pipe: refused), for a wrapper (xargs, env, find -exec) and what it runs,
    and for a change of the plugin's settings. strict: a command named by a variable or a substitution is refused too
    (a script file is read leniently, until it names hermes or habits)."""
    if depth > SHELL_DEPTH:
        return UNREADABLE
    ctx = ctx or _Ctx()
    plain = QUOTES.sub("", line).lower()
    mentions = "hermes" in plain or "habits" in plain
    strict = strict or mentions
    ctx.off = ctx.off or "habits_off" in plain
    try:
        body, bodies = _heredocs(line)
        ctx.bodies.update(bodies)
        cmds = _parse(_strip_comments(body))
    except (shell.Unknown, RecursionError):
        if "hermes" in plain and "habits" in plain:
            return UNREADABLE
        return UNNAMED if strict and (SHELLISH.search(plain) or (mentions and INTERPRETERS.search(plain))) else None
    for text in bodies.values():  # an unquoted heredoc runs the substitutions in its body
        for m in re.finditer(r"\$\(([^()\n]*)\)|`([^`\n]*)`", text):
            why = shell_refusal(m.group(1) or m.group(2), depth + 1, strict, ctx)
            if why:
                return why
    ctx.writes = ctx.writes or any(c.targets or any(shell._name(w) in WRITE_LAST | WRITE_EACH | {"sed", "dd"}
                                                    for w in c.words[:1]) for c in cmds)
    for cmd in cmds:
        why = _command_refusal(cmd, depth, strict, ctx)
        if why:
            return why
        if len(cmd.words) == 2 and shell._name(cmd.words[0]) == "cd":  # a later script path starts from there
            place = _plain_path(cmd.words[1])
            ctx.workdir = place or ctx.workdir
    return None


_line_refusal = shell_refusal  # the name the code reader and the older checks call it by


OFF_SET = re.compile(r"(?:-\w|--\w[\w-]*=)?HABITS_OFF\+?=\S*", re.I)
OFF_SETTERS = {"export", "declare", "typeset", "readonly", "setenv", "read", "printf", "launchctl", "local"}


def _sets_off(cmd: _Cmd, ctx: _Ctx) -> bool:
    """A command that sets HABITS_OFF, for itself, for the shell or for the program it starts: an assignment before a
    command, an argument of env, export or docker -e, a read into it, or (in a line that names it) an assignment whose
    name is a variable."""
    words, k = cmd.words, _head(cmd)
    head = posixpath.basename(words[k].text) if k < len(words) else ""
    for w in words:
        if OFF_SET.fullmatch(w.text) or (w.text.upper() == "HABITS_OFF" and head in OFF_SETTERS):
            return True
        if ctx.off and not w.fixed and "=" in w.text and w.text.index("=") > w.text.find(shell.EXPANSION) >= 0:
            return True
    return False


def _command_refusal(cmd: _Cmd, depth: int, strict: bool, ctx: _Ctx) -> str | None:
    words = cmd.words
    if _sets_off(cmd, ctx):
        return OFF_SWITCH
    k = _head(cmd)
    setter = k < len(words) and posixpath.basename(words[k].text) in OFF_SETTERS | {"env", "alias"}
    for w in [*words[:k], *(words[k + 1:] if setter else [])]:
        # H=hermes, HB='hermes habits', H=/usr/local/bin/hermes, export HB=…: the command kept in a variable. A
        # folder that happens to be named habits (D=~/.hermes/plugin-data/habits) is a path, not the sub-command.
        if w.fixed and ASSIGN.match(w.text) and any(posixpath.basename(piece) == "hermes" or piece == "habits"
                                                    for piece in w.text.split("=", 1)[1].split()):
            return UNREADABLE
    for w in [*words, *cmd.targets, *cmd.here, *cmd.stdin]:
        for sub in w.subs:
            why = shell_refusal(sub, depth + 1, strict, ctx)
            if why:
                return why
    why = _verb_refusal(words) or _config_refusal(words) or _hermes_refusal(words, cmd)
    if why:
        return why
    for word in words:  # bash -c 'hermes habits promote x', ssh host '…': a word that is a line by itself
        if word.fixed and " " in word.text.strip() and re.search(r"habits|hermes", word.text, re.I):
            why = shell_refusal(word.text, depth + 1, strict, ctx)
            if why and why is not UNNAMED:  # a sentence or a query is no line: only what it would do as one counts
                return why
    k = _head(cmd)
    return _program_refusal(cmd, k, depth, strict, ctx) if k < len(words) else None


def _config_refusal(words: list) -> str | None:
    """`hermes config edit|migrate`, and `hermes config set|unset` of a key the reader cannot fix or that holds
    plugin or habit: the settings (learning, promote, permissions…) are the user's."""
    for h, w in enumerate(words):
        if not (w.fixed and (posixpath.basename(w.text) == "hermes" or w.text.startswith("hermes_cli"))):
            continue
        rest, i = words[h + 1:], 0
        while i < len(rest) and rest[i].fixed and rest[i].text.startswith("-"):
            i += 2 if rest[i].text in VALUE_FLAGS else 1
        if i >= len(rest) or not rest[i].fixed or rest[i].text != "config":
            continue
        sub, j = rest[i + 1:], 0
        while j < len(sub) and sub[j].fixed and sub[j].text.startswith("-"):
            j += 1
        if j >= len(sub):
            continue
        verb = sub[j]
        if not verb.fixed or verb.text in ("edit", "migrate"):
            return OWN_SETTINGS
        if verb.text in ("set", "unset"):
            key = sub[j + 1] if j + 1 < len(sub) else None
            if key is None or not key.fixed or re.search(r"habit|plugin", key.text, re.I):
                return OWN_SETTINGS
    return None


TURN_SUBS = {"chat", "gateway", "acp", "serve", "webapp", "desktop", "gui", "tui", "dashboard"}  # they run a turn
PLUGIN_VERBS = {"disable", "uninstall", "remove", "rm", "update", "install", "adopt", "auto-update", "autoupdate",
                "trust-update-url"}  # the ones of `hermes plugins` that change a plugin
UPDATES = {"update", "auto-update", "autoupdate"}  # they reach every plugin when none is named


def _turn_flag(text: str) -> bool:
    """-z / --oneshot / -q / --query, in any spelling argparse reads (a prefix, an attached value, a cluster)."""
    name = text.partition("=")[0]
    if name.startswith("--"):
        return len(name) >= 4 and ("--oneshot".startswith(name) or "--query".startswith(name))
    return name.startswith("-") and len(name) > 1 and bool(set(name[1:]) & set("zq"))


def _hermes_refusal(words: list, cmd: _Cmd | None = None) -> str | None:
    """Two things a model must not do with `hermes`: start a turn whose message the guard cannot read whole (`hermes
    chat -q "$(cat file)"`, a pipe, a variable): the process takes /habits yes as the user's, and text cannot
    show what a file or a transformation holds; and install, update, turn off or remove the Habits plugin itself,
    which takes the guard with it."""
    for h, word in enumerate(words):
        if not _hermes_word(word):
            continue
        rest, i = words[h + 1:], 0
        while i < len(rest) and rest[i].fixed and rest[i].text.startswith("-") and not _turn_flag(rest[i].text):
            i += 2 if rest[i].text in VALUE_FLAGS else 1
        sub = rest[i] if i < len(rest) else None
        turn = any(w.fixed and _turn_flag(w.text) for w in rest) or \
            (sub is not None and sub.fixed and sub.text in TURN_SUBS)
        if turn:
            if cmd is not None and (cmd.piped or cmd.stdin or cmd.here):
                return CHILD
            for w in rest:
                if not w.fixed or "{}" in w.text or "/habits" in w.text.casefold():
                    return CHILD
        elif sub is not None and sub.fixed and sub.text == "plugins":
            tail = rest[i + 1:]
            j = 0
            while j < len(tail) and tail[j].fixed and tail[j].text.startswith("-"):
                j += 1
            verb = tail[j] if j < len(tail) else None
            if verb is None:
                continue
            if not verb.fixed:
                return PLUGIN
            if verb.text in PLUGIN_VERBS:
                names = tail[j + 1:]
                if any(not w.fixed or "habit" in w.text.casefold() for w in names) or verb.text in UPDATES and \
                        not any(w.fixed and not w.text.startswith("-") for w in names) or \
                        verb.text in ("auto-update", "autoupdate"):
                    return PLUGIN
    return None


def _program_refusal(cmd: _Cmd, k: int, depth: int, strict: bool, ctx: _Ctx) -> str | None:
    words = cmd.words
    head, rest = words[k], words[k + 1:]
    if head.text in ("[", "[[") and not head.subs and not head.names:
        return None  # the test command: `[ -f x ] && …`, `if [[ -n $CI ]]; then …` (its substitutions are read above)
    if not head.fixed:
        return UNNAMED if strict else None  # a command named by a variable or a substitution: unreadable
    low = posixpath.basename(head.text.replace(shell.EXPANSION, "$")).lower()
    if low in SHELLS or low in SOURCES or low == "eval":
        return _shell_program(cmd, rest, low, depth, ctx)
    if PYTHON.fullmatch(low):
        return _python_program(cmd, rest, depth, ctx)
    if low in OTHER_LANGS:
        fed = [ctx.bodies.get(w.text, w.text) for w in cmd.here]
        return UNREADABLE if any(re.search(r"hermes|habits|plugin-data", t, re.I) for t in fed) or any(
            w.fixed and re.search(r"hermes|habits|plugin-data", w.text, re.I) for w in rest) else None
    if low == "alias":
        return UNREADABLE if any(not w.fixed or re.search(r"hermes|habits", w.text, re.I) for w in rest) else None
    if low in WRAPPERS:
        return _wrapper(cmd, k, low, depth, strict, ctx)
    if low not in DATA_SINKS:  # ssh host <<< '…', at now <<< '…': a program that may run what it is fed
        for w in cmd.here:
            text = ctx.bodies.get(w.text, w.text)
            if w.fixed and re.search(r"hermes|habits", text, re.I):
                why = shell_refusal(text, depth + 1, True, ctx)
                if why:
                    return why
    if "/" in head.text and not shell.BINS.match(head.text):  # a script run by its path
        return _file_refusal(head, None, depth, ctx, only_scripts=True)
    return None


def _shell_program(cmd: _Cmd, rest: list, low: str, depth: int, ctx: _Ctx) -> str | None:
    if low == "eval":
        if not all(w.fixed for w in rest):
            return UNNAMED
        return shell_refusal(" ".join(w.text for w in rest), depth + 1, True, ctx) if rest else None
    if low in SOURCES:
        if rest and rest[0].fixed and STDIN_PATH.fullmatch(rest[0].text):
            return _stdin_refusal(cmd, False, depth, ctx, device=True)
        return _file_refusal(rest[0], False, depth, ctx) if rest else None
    i = 0
    while i < len(rest):
        word, i = rest[i], i + 1
        t = word.text
        if word.fixed and t.startswith("-") and not t.startswith("--") and "c" in t[1:]:
            if i < len(rest):
                return shell_refusal(rest[i].text, depth + 1, True, ctx) if rest[i].fixed else UNNAMED
            return None
        if t in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 1
        elif not t.startswith(("-", "+")) or not word.fixed:
            if word.fixed and STDIN_PATH.fullmatch(t):  # bash /dev/stdin, zsh /dev/fd/3: the input is the program
                return _stdin_refusal(cmd, False, depth, ctx, device=t != "-")
            return _file_refusal(word, False, depth, ctx)
    return _stdin_refusal(cmd, False, depth, ctx)


def _python_program(cmd: _Cmd, rest: list, depth: int, ctx: _Ctx) -> str | None:
    i = 0
    while i < len(rest):
        word, i = rest[i], i + 1
        t = word.text
        if word.fixed and t.startswith("-") and not t.startswith("--") and "c" in t[1:]:
            if i < len(rest):
                # fixed: the only variable the code may hold is $HOME, read as the home folder (Python cannot parse
                # the reader's own mark, and code it cannot parse was let through)
                code = rest[i].text.replace(shell.EXPANSION, str(Path.home()))
                return code_refusal(code, True, depth + 1, ctx) if rest[i].fixed else UNNAMED
            return None
        if t == "-m":
            return CODE_REACH if i < len(rest) and not rest[i].fixed else None
        if t in ("-W", "-X", "-Q"):
            i += 1
        elif not t.startswith("-") or not word.fixed:
            if word.fixed and STDIN_PATH.fullmatch(t):
                return _stdin_refusal(cmd, True, depth, ctx, device=t != "-")
            return _file_refusal(word, True, depth, ctx)
    return _stdin_refusal(cmd, True, depth, ctx)


def _stdin_refusal(cmd: _Cmd, python: bool, depth: int, ctx: _Ctx, device: bool = False) -> str | None:
    """A shell or interpreter reading its program from its input: a here-string or heredoc is read, a file is read
    now, a pipe is refused. device: the program is named as /dev/stdin, /dev/fd/N or /proc/self/fd/N, so the input may
    come on any descriptor (3<<< '…'), and one the line does not show is refused."""
    for w in cmd.here:
        if not w.fixed:
            return UNNAMED
        text = ctx.bodies.get(w.text, w.text)
        why = code_refusal(text, True, depth + 1, ctx) if python else shell_refusal(text, depth + 1, True, ctx)
        if why:
            return why
    for w in [*cmd.stdin, *(cmd.fds if device else [])]:
        why = _file_refusal(w, python, depth, ctx)
        if why:
            return why
    shown = cmd.here or cmd.stdin or (device and cmd.fds)
    return UNNAMED if not shown and (cmd.piped or device) else None


def _wrapper(cmd: _Cmd, k: int, low: str, depth: int, strict: bool, ctx: _Ctx) -> str | None:
    """env, nohup, timeout, sudo, xargs, find -exec…: the command they run is read as if it stood alone; xargs adds
    arguments from its input to hermes, a shell or an interpreter, so those are refused."""
    words = cmd.words
    for j in range(k + 1, len(words)):
        w = words[j]
        if not w.fixed:
            continue
        b = posixpath.basename(w.text).lower()
        is_hermes = b == "hermes" or w.text.startswith("hermes_cli")
        if not (is_hermes or b in SHELLS or b == "eval" or PYTHON.fullmatch(b) or b in OTHER_LANGS):
            continue
        if low in ("xargs", "parallel") and is_hermes:
            return UNREADABLE  # its input adds arguments to hermes: a verb no text shows
        inner = _Cmd()
        inner.words, inner.piped, inner.here, inner.stdin = words[j:], cmd.piped, cmd.here, cmd.stdin
        why = _verb_refusal(inner.words) or _config_refusal(inner.words) or _hermes_refusal(inner.words, inner) or \
            _program_refusal(inner, 0, depth + 1, strict, ctx)
        if why:
            return why
        break
    if low != "find":
        for w in words[k + 1:]:
            if w.fixed and (w.text.startswith("-") or ASSIGN.match(w.text) or w.text.isdigit()):
                continue
            return UNNAMED if strict and not w.fixed else None
    return None


def _file_refusal(word, python, depth: int, ctx: _Ctx, only_scripts: bool = False) -> str | None:
    """The script a shell or python runs (or a file run by its path), read now. A script that is not there yet while
    the line writes is refused; one the guard cannot place is let go (a script the model wrote is read when it is
    written into $HERMES_HOME/scripts, and again when it runs)."""
    if not word.fixed:
        return None
    place = _place(word, {ctx.workdir})
    if not place:
        return UNNAMED if ctx.writes and not only_scripts else None
    path = Path(place[0])
    try:
        if not path.exists():
            return UNNAMED if ctx.writes else None
        if not path.is_file() or path.stat().st_size > 1_000_000:
            return None
        with open(path, "rb") as f:
            head = f.read(2)
        if only_scripts and head != b"#!" and path.suffix not in SCRIPT_SUFFIXES:
            return None
        text = path.read_text(errors="replace")
    except OSError:
        return None
    if python is None:
        python = path.suffix == ".py" or (text.startswith("#!") and "python" in text.split("\n", 1)[0])
    return _script_refusal(text, python, depth, ctx)


def _script_refusal(text: str, python: bool, depth: int, ctx: _Ctx) -> str | None:
    """A script's text: read as strictly as a terminal line once it names hermes or habits, leniently before."""
    if depth > SHELL_DEPTH:
        return UNREADABLE
    mentions = bool(re.search(r"hermes|habits", QUOTES.sub("", text), re.I))
    if python:
        return code_refusal(text, mentions, depth + 1, ctx, files=True) or script_state(text, True)
    lines = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("#"))
    return shell_refusal(lines, depth + 1, False, ctx) or script_state(text, False)


def _hermes_home() -> Path:
    found = os.environ.get("HERMES_HOME")
    if found:
        return Path(found).expanduser()
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def _own_paths(home) -> tuple:
    """The plugin's own folder (trees), what holds it (exact), its files, and the settings files, each also with its
    links resolved."""
    homes = {Path(home)} if home else set()
    homes |= {_hermes_home() / "plugin-data" / "habits", Path.home() / ".hermes" / "plugin-data" / "habits"}
    trees = {str(h) for h in homes}
    exact = {str(h.parent) for h in homes}
    files = {f"{h}/{n}" for h in homes for n in HOME_FILES}
    configs = {f"{h}/config.yaml" for h in {_hermes_home(), Path.home() / ".hermes"}}
    out = []
    for group in (trees, exact, files | trees, configs):
        out.append(sorted(group | {os.path.realpath(p) for p in group}))
    return tuple(out)


def _history_files() -> list:
    """Hermes's own history database, with its journal files, in each Hermes folder, links resolved."""
    homes = {_hermes_home(), Path.home() / ".hermes"}
    files = {f"{h}/state.db{suffix}" for h in homes for suffix in ("", "-wal", "-shm", "-journal")}
    return sorted(files | {os.path.realpath(p) for p in files})


SELECTS = re.compile(r"^\s*(?:select|with|explain|\.tables|\.schema|\.headers|\.mode|pragma\s+table_info)\b", re.I)


RUNS_OR_WRITES = re.compile(r"(?:^|[\s;])\.(?:shell|system|import|output|once|read|save|backup|restore|load|cd|excel|www|open"
                            r"|log|clone)\b|\battach\b|load_extension|\bvacuum\b|\b(?:writefile|edit)\s*\(", re.I)
# The dot-commands that only show: sqlite3 reads a shorter spelling too (.sh is .shell), so in a file opened read-only
# any other one is no read.
DOT_SHOWS = re.compile(r"\.(?:tables|schema|fullschema|headers?|mode|indexes|indices|databases|dbinfo|dump|show|stats|"
                       r"timer|timeout|width|nullvalue|separator|print|echo|bail|changes|eqp|explain|help|quit|exit)"
                       r"(?:\s|$)", re.I)


def _history_read(cmd) -> bool:
    """sqlite3 asked only to select from Hermes's history (or opened read-only): a read, as for any other file."""
    words = [w.text for w in cmd.words]
    if shell._name(cmd.words[0]) not in ("sqlite3", "sqlite") or any(not w.fixed for w in cmd.words[1:]):
        return False
    if any(t in ("-init", "-cmd") or RUNS_OR_WRITES.search(t) for t in words[1:]):
        return False  # a dot-command that runs a shell or writes a file, an attached or loaded file: no read
    if any(re.match(r"\.[A-Za-z_]+(?:\s|$)", piece.strip()) and not DOT_SHOWS.match(piece.strip())
           for t in words[1:] for piece in re.split(r"[;\n]", t)):
        return False  # .sh, .sys, .o, .trace, .ar: a dot-command by its short name, or one that writes a file
    if any(t in ("-readonly", "-ro") or (t.startswith("file:") and re.search(r"[?&](?:mode=ro|immutable=1)\b", t))
           for t in words):
        return True  # opened read-only, as a file name or as a URI (immutable: how the ledger opens with no writer)
    sql = [t for t in words[1:] if not t.startswith("-") and not t.endswith((".db", ".sqlite", ".sqlite3"))]
    return bool(sql) and all(SELECTS.match(piece) for t in sql for piece in t.split(";") if piece.strip())


def _regex(word) -> tuple:
    """(a regex of the paths a word may become, its count of literal characters) or (None, 0) for a plain word. A
    variable or a substitution may be anything; * ? [ ] unquoted are the shell's; ~ is the home folder."""
    text, quoted = word.text, list(word.quoted)
    if text[:1] == "~" and quoted and not quoted[0] and (text == "~" or text.startswith("~/")):
        home = str(Path.home())
        text, quoted = home + text[1:], [True] * len(home) + quoted[1:]
    if word.names == ("HOME",) and text.startswith(shell.EXPANSION):
        home = str(Path.home())
        text, quoted = home + text[1:], [True] * len(home) + quoted[1:]
    text = re.sub(r"/+", "/", text)
    if len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    quoted = quoted[:len(text)] + [True] * max(0, len(text) - len(quoted))
    parts, lit, wild, i = [], 0, False, 0
    while i < len(text):
        c, q = text[i], quoted[i]
        if c in (shell.EXPANSION, shell.MARK):
            parts.append(".*")
            wild = True
        elif not q and c == "*":
            parts.append("[^/]*")
            wild = True
        elif not q and c == "?":
            parts.append("[^/]")
            wild = True
        elif not q and c == "[" and text.find("]", i + 2) > 0:
            j = text.find("]", i + 2)
            body = text[i + 1:j]
            neg = body[:1] in "!^"
            body = body[1:] if neg else body
            parts.append("[" + ("^/" if neg else "") + "".join("-" if ch == "-" else re.escape(ch) for ch in body) + "]")
            wild, i = True, j
        else:
            parts.append(re.escape(c))
            lit += c != "/"
        i += 1
    if not wild:
        return None, 0
    return re.compile("".join(parts), re.I | re.S), lit


def _plain_path(word) -> str | None:
    """The full path a plain word names (links resolved), or None."""
    text = word.text
    if text[:1] == "~" and word.quoted and not word.quoted[0] and (text == "~" or text.startswith("~/")):
        text = str(Path.home()) + text[1:]
    elif word.names == ("HOME",) and text.startswith(shell.EXPANSION):
        text = str(Path.home()) + text[1:]
    if shell.EXPANSION in text or shell.MARK in text or not text.startswith("/"):
        return None
    text = re.sub(r"/+", "/", text)
    while "/./" in text:
        text = text.replace("/./", "/")
    return text.rstrip("/") or "/"


def _reaches(word, trees: list, exact: list, texts: list) -> bool:
    """Whether a word names, or as a glob or with a variable may name, a path inside one of trees, one of exact, or
    (as a pattern) one of texts. A word with an = (dd of=…, --out=…) is also read after it."""
    variants = [word]
    if "=" in word.text:
        cut = word.text.index("=") + 1
        variants.append(shell.Word(word.text[cut:], word.quoted[cut:], word.names, word.subs))
    for v in variants:
        pattern, lit = _regex(v)
        if pattern is None:
            path = _plain_path(v)
            if path is None:
                continue
            for p in {path, os.path.realpath(path)}:
                low = p.casefold()
                if any(_inside(low, t.casefold()) for t in trees) or low in {e.casefold() for e in exact}:
                    return True
        elif lit >= 4 and (pattern.pattern.startswith(("/", "\\/", ".*"))):
            if any(pattern.fullmatch(c) for c in texts) or any(pattern.fullmatch(c) for c in exact):
                return True
    return False


CARRIES_A_VARIABLE = re.compile(r"(?<![\w./-])(?:eval|source|set|export|declare|typeset|readonly|local|setopt|shopt|trap|"
                                r"alias|exec|env|printenv|compgen)(?![\w./-])|(?:^|[;&|\n(])\s*\.\s|\$\{!")


def _unused_sets(pairs: list, text: str) -> set:
    """The ids of the words that set a variable the line uses nowhere (S=/path in a command of its own, its name
    standing nowhere else): after the variables are written out, such a word acts on nothing, and it is not exported
    to the next call. One whose name still stands elsewhere ($S left unread, read S) acts as before."""
    found = set()
    if CARRIES_A_VARIABLE.search(text):
        return found  # export, set -a, eval, source, ${!x}: a variable may reach a program or be read by another name
    for cmd in dict.fromkeys(c for _w, c in pairs):
        if not cmd.words or cmd.targets or cmd.here or cmd.stdin:
            continue
        sets = [_assigned(w) for w in cmd.words]
        if all(sets) and not any(w.subs for w in cmd.words):
            found |= {id(w) for w, (name, _value) in zip(cmd.words, sets)
                      if len(re.findall(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % name, text)) == 1}
    return found


def _plainly_reaches(word, exact: list) -> bool:
    """The word, or what stands after its = (S=/path, of=/path), is a plain path that is one of these: no glob and
    no variable had to be read as "anything"."""
    cut = word.text.index("=") + 1 if "=" in word.text else 0
    for v in (word, shell.Word(word.text[cut:], word.quoted[cut:], word.names, word.subs)):
        if _regex(v)[0] is None and _reaches(v, [], exact, []):
            return True
    return False


def _fragment_reaches(words: list, candidates: list) -> bool:
    """A path put together across a line (T=~/.hermes/plug; T=${T}in-data/habits/off; touch $T): a literal piece of a
    word that is part of a plugin path, on a line where a word is unresolved."""
    unresolved = any(not w.fixed or _regex(w)[0] is not None for w in words)
    if not unresolved:
        return False
    low = [c.casefold() for c in candidates]
    for w in words:
        text = w.text.split("=", 1)[1] if ASSIGN.match(w.text) else w.text
        if text[:1] == "~":
            text = str(Path.home()) + text[1:]
        for piece in re.split(r"[\x00\x01*?\[\]]", text):
            if len(piece) >= 6 and "/" in piece and DISTINCT.search(piece) and any(piece.casefold() in c for c in low):
                return True
    return False


def _line_words(text: str, depth: int = 0) -> list | None:
    """Every word of a terminal line (commands, redirect targets, here-strings, and those of its substitutions) with
    the command it stands in, or None when it cannot be read. The same line is read by a dozen gates of one call: it is
    parsed once (a call's text is the key, a small cache)."""
    found = _line_words_once(text, depth)
    return None if found is None else list(found)


@functools.lru_cache(maxsize=16)
def _line_words_once(text: str, depth: int = 0):
    try:
        body, _ = _heredocs(text)
        cmds = _parse(_strip_comments(body))
    except (shell.Unknown, RecursionError):
        return None
    found = [(w, c) for c in cmds for w in [*c.words, *c.targets, *c.here, *c.stdin]]
    if depth < 3:
        for w, _c in list(found):
            for sub in w.subs:
                more = _line_words(sub, depth + 1)
                found += more or []
    return tuple(found)


EXEC_OPENERS = {"-exec", "-execdir", "-ok", "-okdir"}
FIND_WRITES = ("-delete", "-fprint", "-fprint0", "-fprintf", "-fls")  # find's own writes
RUN_READS = {"cat", "ls", "grep", "egrep", "fgrep", "rg", "head", "tail", "wc", "stat", "file", "test", "[", "readlink",
             "realpath", "echo", "printf", "du", "diff", "cmp", "md5sum", "shasum", "sha256sum", "basename", "dirname",
             "less", "more", "strings", "xxd", "od", "true"}
PLACEHOLDER = re.compile(r"\{\d*\}/+(?:off|paused|habits(?:\.db[-\w]*)?)(?![\w.-])", re.I)
SWITCHES = ("habits", "off", "paused", "habits.db", "habits.db-wal", "habits.db-shm", "habits.db-journal", "plugin-data")
HISTORY_NAMES = ("state.db", "state.db-wal", "state.db-shm", "state.db-journal")
PATH_TESTS = {"-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex"}
NAME_TESTS = {"-name", "-iname", "-lname", "-ilname"}


def _find_runs(words: list) -> list[list]:
    """The commands of a find's -exec / -execdir / -ok / -okdir, each as its words (up to ; or +)."""
    runs, i = [], 0
    while i < len(words):
        if words[i].fixed and words[i].text in EXEC_OPENERS:
            j = i + 1
            while j < len(words) and not (words[j].fixed and words[j].text in (";", "+")):
                j += 1
            runs.append(words[i + 1:j])
            i = j
        i += 1
    return runs


def _runs_a_writer(words: list) -> bool:
    """Whether a find, xargs or parallel command runs something that is no plain read."""
    head = next((shell._name(w) for w in words[1:] if w.fixed and not w.text.startswith("-")), None) \
        if shell._name(words[0]) != "find" else None
    runs = _find_runs(words) if shell._name(words[0]) == "find" else []
    if shell._name(words[0]) == "find":
        return any(w.fixed and w.text in FIND_WRITES or w.fixed and w.text.startswith("-fprint") for w in words) or \
            any(not (r and r[0].fixed and shell._name(r[0]) in RUN_READS) for r in runs)
    return head not in RUN_READS


def _find_reach(cmd: _Cmd, trees: list, cwd: str | None, names: tuple = SWITCHES) -> bool:
    """A find rooted at the plugin's folder, at a folder that holds it (~/.hermes, ~, /), or at a root the guard cannot
    resolve, that writes (-delete, -exec of a writer) and whose tests may match the plugin's folder or its switches
    (off, paused, habits.db). With Hermes's history files as trees and their names: the same question for state.db."""
    words = cmd.words
    if not words or shell._name(words[0]) != "find" or not _runs_a_writer(words):
        return False
    roots, i = [], 1
    while i < len(words) and not (words[i].fixed and (words[i].text.startswith(("-", "(", "!")))):
        roots.append(words[i])
        i += 1
    reached = not roots
    for w in roots:
        path = _plain_path(w)
        if path is None:
            if w.fixed and not w.text.startswith("/") and not w.text.startswith("~"):
                path = os.path.normpath(os.path.join(cwd, w.text)) if cwd else None
                if path is None:
                    continue  # a relative root from the session's folder: the guard cannot tell
            else:
                reached = True  # a variable, a glob, a substitution
                continue
        low = os.path.realpath(path).casefold()
        reached = reached or any(_inside(t.casefold(), low) or _inside(low, t.casefold()) for t in trees)
    if not reached:
        return False
    tests = [(words[k].text, words[k + 1]) for k in range(i, len(words) - 1) if words[k].fixed]
    patterns = [(t, v) for t, v in tests if t in NAME_TESTS | PATH_TESTS]
    if not patterns:
        return True
    candidates = list(names) + [t.casefold() for t in trees] + [f"{t}/{n}".casefold() for t in trees for n in names]
    for test, value in patterns:
        if not value.fixed:
            return True
        pattern = value.text.casefold()
        if test in PATH_TESTS and test.endswith("regex"):
            try:
                if any(re.search(pattern, c) for c in candidates):
                    return True
            except re.error:
                return True
        elif any(fnmatch.fnmatchcase(c, pattern) for c in candidates):
            return True
    return False


STANDING = ("This line would leave the shell standing in the Habits plugin's own folder ({folder}), where a later "
            "relative path could not be told from the plugin's files. Read it by its full path instead: ls <path>, "
            "cat <path>/file.")
OWN_FOLDERS = re.compile(r"(?:^|/)(?:plugin-data|plugins)/habits(?:/|$)", re.I)


def _left_in(text: str, trees: list) -> str | None:
    """The plugin's folder a line leaves the session's shell standing in: a cd (or pushd) outside parentheses into the
    data folder or the code folder, links resolved, not followed by a cd to a full path elsewhere. A relative cd is
    read by its words (cd plugin-data/habits)."""
    try:
        tokens, _ = shell._lex(shell._marked(_strip_comments(_heredocs(text)[0])))
    except (shell.Unknown, RecursionError):
        return None
    low = [t.casefold() for t in trees]
    depth, found, start = 0, None, True
    for i, token in enumerate(tokens):
        if not isinstance(token, shell.Word):
            depth += (token == "(") - (token == ")")
            start = token in shell.SEPARATORS or token in ("(", ")", ";;")
            continue
        if start and depth <= 0 and token.text in ("cd", "pushd") and i + 1 < len(tokens) and \
                isinstance(tokens[i + 1], shell.Word):
            target = tokens[i + 1]
            place = _plain_path(target)
            if place is not None:
                real = os.path.realpath(place)
                found = real if any(_inside(real.casefold(), t) for t in low) else None
            elif target.fixed and OWN_FOLDERS.search(target.text):
                found = target.text
        start = start and (bool(ASSIGN.match(token.text)) or token.text in KEYWORDS)
    return found


# ---- reading the plugin's own code (the owner loosened the guard on 11 November) ---------------------------------
# The model may read the plugin's code folder like any other (cd into it, git status, log, diff, ls, cat, grep) and run
# the test suite of a working copy the plugin is linked to. What writes there, moves it, removes it or changes its git
# state stays refused. These are the commands of such a line; anything else in it makes the old rules apply.

CODE_GIT = {"status", "log", "diff", "show", "blame", "annotate", "ls-files", "ls-tree", "describe", "shortlog", "cat-file",
            "grep", "for-each-ref", "rev-list", "diff-tree", "diff-files", "diff-index", "show-ref", "name-rev",
            "count-objects", "check-ignore", "merge-base", "whatchanged"}
GIT_NOT = ("--output", "--ext-diff", "--textconv", "--open-files-in-pager")  # what runs a program or writes a file
TEST_FLAGS = {"-q", "-qq", "-v", "-vv", "-vvv", "-x", "-s", "-l", "-b", "-f", "--lf", "--ff", "--nf", "--sw", "-ra", "-rA",
              "-rs", "-rx", "-rf", "-rE", "-rN", "--co", "--collect-only", "--no-header", "--disable-warnings",
              "--strict-markers", "--strict-config", "--showlocals", "--full-trace", "--no-summary", "--version", "-V",
              "-h", "--help", "--quiet", "--verbose", "--exitfirst"}
TEST_VALUES = {"-c", "-k", "-m", "-n", "-W", "-p", "-s", "-t", "-r", "--deselect", "--ignore", "--ignore-glob", "--durations",
               "--maxfail", "--tb", "--color", "--rootdir", "--confcutdir"}


def _git_read(words) -> bool:
    """git asked only to look: status, log, diff, show, blame, ls-files…, or what shell.py reads (branch, remote -v, tag
    -l, config --get, rev-parse). Never a -c, an output file, an external diff program or a pager of its own."""
    args = [w.text for w in words[1:]]
    if shell._git(args, 0):
        return True
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "--no-pager":
            i += 1
        elif args[i] == "-C" and i + 1 < len(args):
            i += 2
        else:
            return False
    if i >= len(args):
        return False
    sub, rest = args[i], args[i + 1:]
    if any(w.startswith(("-O",)) or shell._abbrev(w, *GIT_NOT) for w in rest):
        return False
    if sub == "stash":
        return rest[:1] in (["list"], ["show"])
    return sub in CODE_GIT


def _test_args(rest: list) -> bool:
    """The options of a test run that read and report: no output file, no folder to empty, no plugin to load but the
    switching off of one (-p no:cacheprovider)."""
    i = 0
    while i < len(rest):
        word = rest[i]
        i += 1
        if not word.startswith("-"):
            continue  # a test file, a folder or a test's name
        name, eq, value = word.partition("=")
        if name in TEST_VALUES:
            if not eq:
                if i >= len(rest):
                    return False
                value, i = rest[i], i + 1
            if name == "-p" and not value.startswith("no:"):
                return False
        elif word not in TEST_FLAGS and not re.fullmatch(r"-r[a-zA-Z]+|-n\d+|-v+|-q+", word):
            return False
    return True


def _test_run(words) -> bool:
    """pytest, python -m pytest, python -m unittest or uv run of one of them, with options that only read."""
    texts = [w.text for w in words]
    if posixpath.basename(texts[0]) == "uv" and texts[1:2] == ["run"] and len(texts) > 2:
        texts = texts[2:]
    base = posixpath.basename(texts[0])
    if base in ("pytest", "py.test"):
        return _test_args(texts[1:])
    if re.fullmatch(r"python[\d.]*", base) and texts[1:2] == ["-m"] and texts[2:3] in (["pytest"], ["unittest"]):
        return _test_args(texts[3:])
    return False


def _code_read(line: str, acting: bool = False) -> bool:
    """Every command of the line only reads or runs a test suite (a git look, a cd, what `reads` calls a read); with
    `acting`, at least one is not a cd. A redirection that writes, a substitution or a variable makes it no such line."""
    if shell.EXPANSION in line or shell.MARK in line:
        return False
    try:
        found = False
        for words, writes, subs in shell._commands(shell._marked(line)):
            if _moves_nothing(words, writes) or not shell._why(words, writes, subs, 0):
                found |= not (words and shell._name(words[0]) == "cd")
                continue
            if writes or subs or not words or not all(w.fixed for w in words):
                return False
            sqlite = shell._name(words[0]) in ("sqlite3", "sqlite") and _history_read(SimpleNamespace(words=words))
            if not (shell._name(words[0]) == "git" and _git_read(words) or _test_run(words) or sqlite):
                return False
            found = True
        return found or not acting
    except (shell.Unknown, IndexError):
        return False


def _cd_walk(text: str, cwd: str | None, first: bool = False) -> str | None:
    """The folder (links resolved) a line leaves the session's shell in, from where it starts (cwd, or None when not
    known): a cd or pushd outside parentheses, a relative one from where the shell stands. A cd to a place that
    cannot be read (a variable) leaves the shell where it was (the careful reading). With `first`, only up to the
    first cd: where the line's other commands run."""
    try:
        tokens, _ = shell._lex(shell._marked(_strip_comments(_heredocs(text)[0])))
    except (shell.Unknown, RecursionError):
        return cwd
    depth, start = 0, True
    for i, token in enumerate(tokens):
        if not isinstance(token, shell.Word):
            depth += (token == "(") - (token == ")")
            start = token in shell.SEPARATORS or token in ("(", ")", ";;")
            continue
        if start and depth <= 0 and token.text in ("cd", "pushd"):
            target = tokens[i + 1] if i + 1 < len(tokens) else None
            if not isinstance(target, shell.Word):
                cwd = None  # a bare cd: the home folder
            else:
                place = _plain_path(target)
                if place is None and target.fixed and not target.text.startswith("-") and cwd and \
                        shell.EXPANSION not in target.text and shell.MARK not in target.text:
                    place = posixpath.normpath(posixpath.join(cwd, target.text))
                if place is not None:
                    cwd = os.path.realpath(place)
            if first:
                return cwd
        start = start and (bool(ASSIGN.match(token.text)) or token.text in KEYWORDS)
    return cwd


STANDING_NOW = ("The shell is still standing in the Habits plugin's own folder ({folder}) from an earlier line, and this "
                "line writes or runs something. Reading is fine there; to do more, start the line with cd <the full path "
                "of where you work> (the plugin's code is the user's to change).")
STANDING_CODE = ("This line would leave the shell standing in the Habits plugin's own folder ({folder}) with nothing to "
                 "run there. Put a command on the same line (cd <path> && git status), or read it by its full path "
                 "(ls <path>, cat <path>/file).")


def terminal_reach(text: str, home) -> str | None:
    """OWN_FILES or OWN_SETTINGS when a line that is no mere read names, or by a glob, a variable or a link may name,
    the plugin's folder or the settings file."""
    trees, exact, texts, configs = _own_paths(home)
    code_trees, code_exact = _code_paths()
    if "cd" in text or "pushd" in text:
        folder = _left_in(text, trees)
        if folder:
            return STANDING.format(folder=folder)
        folder = _left_in(text, code_trees)
        if folder and not _code_read(text, acting=True):  # cd <it> && git status leaves a shell the guard follows
            return STANDING_CODE.format(folder=folder)
    if reads(text):
        return None
    code_read = _code_read(text)
    pairs = _line_words(text)
    if pairs is None:
        return None
    words = [w for w, _ in pairs]
    copied = {id(w) for w, c in pairs if _only_read_word(w, c)}  # cp <it> elsewhere, tar czf x.tgz <it>: read only
    copied |= _unused_sets(pairs, text)  # S=<it>, written out where the line used it: the uses are read, not the name
    for w in words:
        if id(w) in copied:
            continue
        if _reaches(w, trees, exact, texts):
            return OWN_FILES
        if not code_read and _reaches(w, code_trees, code_exact, code_trees):
            return OWN_CODE
        if _reaches(w, [], configs, configs):
            return OWN_SETTINGS
    acting = [w for w in words if id(w) not in copied]
    if _fragment_reaches(acting, texts + exact + configs):
        return OWN_FILES
    loose = [w for w in acting if (not w.fixed or _regex(w)[0] is not None) and re.search(r"plug|habits", w.text, re.I)]
    if loose and not code_read:  # a glob or a variable that names plugins or habits and may be one of the code's files
        files = _code_files(code_trees)
        if any(_reaches(w, [], [], files) for w in loose) or _fragment_reaches(acting, code_trees):
            return OWN_CODE
    history = _history_files()
    entered, last_of = False, {}
    for w, cmd in pairs:
        if cmd.words and id(cmd) not in last_of:
            last_of[id(cmd)] = [x for x in cmd.words[1:] if not x.text.startswith("-")][-1:]
        if cmd.words and shell._name(cmd.words[0]) == "cd" and w is not cmd.words[0]:
            place = _plain_path(w)
            entered = place is None or _inside(os.path.realpath(place).casefold(), str(_hermes_home()).casefold())
            continue
        if not cmd.words or _history_read(cmd):
            continue
        source = shell._name(cmd.words[0]) in WRITE_LAST and w not in last_of[id(cmd)]  # cp from it only reads it
        named = _reaches(w, [], history, history) or (entered and posixpath.basename(w.text).startswith("state.db"))
        if named and not source and id(w) not in copied:
            return OWN_HISTORY
    cwd = None
    for cmd in dict.fromkeys(c for _w, c in pairs):  # the folder a later command runs in: the cd before it
        if len(cmd.words) == 2 and shell._name(cmd.words[0]) == "cd":
            place = _plain_path(cmd.words[1])
            cwd = place if place else cwd
        if _find_reach(cmd, trees, cwd):
            return OWN_FILES
        if not code_read and _find_reach(cmd, code_trees, cwd):
            return OWN_CODE
        if _find_reach(cmd, history, cwd, HISTORY_NAMES):  # find ~/.hermes -name 'state.db*' -delete
            return OWN_HISTORY
        if cmd.words and shell._name(cmd.words[0]) in ("xargs", "parallel") and _runs_a_writer(cmd.words) and \
                any(w.fixed and PLACEHOLDER.search(w.text) for w in cmd.words):
            return OWN_FILES
    if any(w.fixed and PLACEHOLDER.search(w.text) for c in dict.fromkeys(c for _w, c in pairs)
           if c.words and shell._name(c.words[0]) == "find" and _runs_a_writer(c.words) for w in c.words):
        return OWN_FILES
    homes = {str(_hermes_home()), str(Path.home() / ".hermes")}
    if any(w is c.words[0] or True for w, c in pairs if c.words and shell._name(c.words[0]) == "cd" and
           w is not c.words[0] and _plain_path(w) in homes) and \
            any(posixpath.basename(w.text) == "config.yaml" for w in words):
        return OWN_SETTINGS
    return None


def _workdir_reach(workdir: str, command: str, home) -> str | None:
    """A line that is no read, run with its workdir in the plugin's folder (or the folder that holds it): its relative
    paths (touch off, a redirection to guard.py) are the plugin's files."""
    if not workdir or not _full(workdir) or reads(command):
        return None
    real = os.path.realpath(os.path.expanduser(workdir)).casefold()
    trees, exact, _texts, _configs = _own_paths(home)
    if any(_inside(real, t.casefold()) for t in trees) or real in {e.casefold() for e in exact}:
        return OWN_FILES
    code_trees, code_exact = _code_paths()
    if (any(_inside(real, t.casefold()) for t in code_trees) or real in {e.casefold() for e in code_exact}) and \
            not _code_read(command):
        return OWN_CODE
    return None


def file_reach(path: str, home) -> str | None:
    """OWN_FILES / OWN_SETTINGS for a file tool's path inside the plugin's folder or the settings file, links
    resolved."""
    given = posixpath.expanduser(str(path or ""))
    if not posixpath.isabs(given):
        return None
    trees, exact, _texts, configs = _own_paths(home)
    real = os.path.realpath(given).casefold()
    if any(_inside(real, t.casefold()) for t in trees) or real in {e.casefold() for e in exact}:
        return OWN_FILES
    code_trees, code_exact = _code_paths()
    if any(_inside(real, t.casefold()) for t in code_trees) or real in {e.casefold() for e in code_exact}:
        return OWN_CODE
    if real in {c.casefold() for c in configs}:
        return OWN_SETTINGS
    if real in {h.casefold() for h in _history_files()}:
        return OWN_HISTORY
    return None


GIT_WRITES = {"apply", "am", "checkout", "restore", "reset", "stash", "merge", "pull", "cherry-pick", "rebase", "revert",
              "clean", "rm", "mv", "switch", "read-tree", "update-index"}
INPUT_FLAGS = {"-i", "--input"}  # patch's way to name its diff
DIR_FLAGS = {"-d", "--directory", "-C"}  # patch -d, tar -C, git -C: the folder it works in
LOST = object()  # a folder the guard lost track of (a cd to a variable)


def _writes_by_content(cmd: _Cmd) -> list | None:
    """The words a command takes its changes from when it writes files its own words do not name (patch, git apply,
    git checkout, tar -x, unzip…): its operands. None for any other command."""
    words, name = cmd.words, shell._name(cmd.words[0]) if cmd.words else ""
    rest = [w for w in words[1:]]
    texts = [w.text for w in rest]
    if name in ("patch", "ed", "ex", "unzip", "cpio", "pax"):
        if name == "unzip" and any(t in ("-l", "-t", "-p", "-Z", "-v") for t in texts):
            return None
        return rest
    if name in ("tar", "bsdtar"):
        first = next((t for t in texts), "")
        extracting = bool(re.match(r"^-?[A-Za-z]*x", first)) or any(t in ("--extract", "--get") or
                                                                    re.match(r"^-[A-Za-z]*x[A-Za-z]*$", t) for t in texts)
        return rest if extracting else None
    if name == "git":
        i = 0
        while i < len(rest) and rest[i].fixed and rest[i].text.startswith("-"):
            i += 2 if rest[i].text in ("-C", "-c", "--git-dir", "--work-tree") else 1
        if i < len(rest) and (not rest[i].fixed or rest[i].text in GIT_WRITES):
            return rest[i + 1:]
    return None


def _folder_of(cmd: _Cmd, cwd):
    """The folder a command works in: -d / -C / --directory=, else the folder of the cd before it."""
    words = cmd.words
    for k, w in enumerate(words):
        value = w.text.split("=", 1)[1] if w.text.startswith("--directory=") else \
            words[k + 1] if w.fixed and w.text in DIR_FLAGS and k + 1 < len(words) else None
        if value is not None:
            place = _plain_path(value) if not isinstance(value, str) else value
            return os.path.realpath(place) if place else LOST
    return cwd


def _near(folder, folders: dict) -> str | None:
    """The habit whose folder this one is, is inside, or holds (the skills folder, the home, /); ANYWHERE when the
    folder is lost."""
    if folder is LOST:
        return ANYWHERE
    if not folder:
        return None  # the session's own folder: Hermes keeps its last cd from another call, which the guard never sees
    low = os.path.realpath(folder).casefold()
    for name, habit in folders.items():
        held = os.path.realpath(habit).casefold()
        if _inside(low, held) or _inside(held, low):
            return name
    return None


def _diff_texts(operands: list, cmd: _Cmd, cwd) -> list | None:
    """The text of every file a patch-like command reads its changes from (its operands, -i FILE, its redirected
    input), or None when one of them cannot be read now: absent, a pipe, a variable, a here-document."""
    words = [w for w in operands if not w.text.startswith("-") or w.text in INPUT_FLAGS]
    names, k = [], 0
    while k < len(words):
        w = words[k]
        if w.text in INPUT_FLAGS and k + 1 < len(words):
            names.append(words[k + 1])
            k += 1
        elif w.text.startswith("--input="):
            names.append(shell.Word(w.text.split("=", 1)[1], w.quoted[len("--input="):], w.names, w.subs))
        elif not w.text.startswith("-"):
            names.append(w)
        k += 1
    names += cmd.stdin
    if cmd.piped or cmd.here or not names:
        return None
    texts = []
    for w in names:
        if not w.fixed:
            return None
        place = _place(w, {cwd if isinstance(cwd, str) and cwd else None}) if not w.text.startswith("/") else _place(w, set())
        found = None
        for candidate in place or []:
            try:
                path = Path(candidate)
                if path.is_file() and path.stat().st_size <= 1_000_000:
                    found = path.read_text(errors="replace")
                    break
            except OSError:
                pass
        if found is None:
            if w.text.isdigit() or re.fullmatch(r"-?\d+", w.text):
                continue  # patch -p 1: a number, no file
            return None
        texts.append(found)
    return texts


def _patch_reach(pairs: list, folders: dict, workdir: str) -> str | None:
    """A patch, an archive or a checkout that writes files its words do not name, run in or above a habit's folder
    (cd ~/.hermes/skills/check-links && git apply p.diff): the diff is read now, and a habit's file in it is a
    refusal; a change the guard cannot read is a refusal next to a habit. From the session's own folder (a cd made
    in another call) it cannot tell: known limit."""
    cwd = os.path.realpath(os.path.expanduser(workdir)) if workdir and _full(workdir) else ""
    for cmd in dict.fromkeys(c for _w, c in pairs):
        words = cmd.words
        if len(words) == 2 and shell._name(words[0]) == "cd":
            place = _plain_path(words[1]) or (os.path.normpath(os.path.join(cwd, words[1].text))
                                              if cwd and words[1].fixed and not words[1].text.startswith(("/", "~")) else None)
            cwd = os.path.realpath(place) if place else LOST
            continue
        operands = _writes_by_content(cmd) if words else None
        if operands is None:
            continue
        here = _folder_of(cmd, cwd)
        texts = _diff_texts(operands, cmd, here) if shell._name(words[0]) in ("patch", "ed", "ex") or \
            (shell._name(words[0]) == "git" and any(w.fixed and w.text in ("apply", "am") for w in words)) else None
        if texts is not None:
            for text in texts:
                low = text.casefold()
                found = next((n for n, f in folders.items() if _named(n, text) or _named(f.name, text)), None)
                mentions = any(n in low for n in OWN_NAMES) or "plugin-data" in low
                if found and mentions:
                    return found
                if mentions and _near(here, folders):
                    return _near(here, folders)
            continue
        near = _near(here, folders)
        if near:
            return near
    return None


def habit_reach(text: str, folders: dict, workdir: str = "") -> str | None:
    """The habit whose folder, assets folder or files a non-read terminal line names, or by a glob or a variable may
    name: rm -rf, mv, cp -R, rsync, tar -C, a glob over SKILL.m?, a cd into it and a loop. A path deeper in it (a
    helper script) is no such name. The habit is the one the line names (its path, or its name in a word that holds
    a glob or a variable); a word that could be any habit's file ($D/SKILL.md, skills/*/SKILL.md) gives ANYWHERE,
    never the first habit of the list."""
    if not folders or reads(text):
        return None
    pairs = _line_words(text)
    if pairs is None:
        return None
    anyones, unused = False, _unused_sets(pairs, text)
    for name, folder in folders.items():
        base = str(folder)
        places = [base, f"{base}/assets"] + [f"{base}/{part}" for part in OWN_PARTS]
        places = sorted(set(places) | {os.path.realpath(p) for p in places})
        entered = False
        for w, cmd in pairs:
            cd = cmd.words and shell._name(cmd.words[0]) == "cd" and w is not cmd.words[0]
            if id(w) not in unused and _reaches(w, [], places, places):
                if cd:
                    entered = True
                elif _plainly_reaches(w, places) or _named(folder.name, w.text) or _named(name, w.text):
                    return name
                else:
                    anyones = True
        if entered and any(not w.fixed or _regex(w)[0] is not None for w, cmd in pairs
                           if not (cmd.words and shell._name(cmd.words[0]) == "cd")):
            return name
    if anyones:
        return ANYWHERE
    hit = _patch_reach(pairs, folders, workdir)
    if hit:
        return hit
    for w, cmd in pairs:  # a relative glob that may be one of the files (the session's folder may be a habit's)
        pattern, lit = _regex(w)
        if pattern is not None and lit >= 4 and not w.text.startswith(("/", "~", shell.EXPANSION)) and \
                not (cmd.words and w is cmd.words[0]) and any(pattern.fullmatch(p) for p in OWN_PARTS + ("assets",)):
            return ANYWHERE
    return None


# ---- code: folded as Python folds it, then each program it runs read like a terminal line ------------------------

class _Path(str):
    """A pathlib path in the folder: a str that joins with /."""


class _Eval:
    """Constant folding of the expressions code builds a string or a list from: + * % , slices and indexes, f-strings,
    join, format, split, replace, encode and decode, chr and bytes, base64 and hex, os.path and pathlib joins, and a
    name assigned once and never changed. Anything else is UNKNOWN."""

    def __init__(self, tree):
        self.alias, self.memo = {}, {}
        count, bad, assigned = {}, set(), {}

        def taint(target):
            bad.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.alias[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    self.alias[a.asname or a.name] = f"{node.module or ''}.{a.name}".lstrip(".")
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        count[t.id], assigned[t.id] = count.get(t.id, 0) + 1, node.value
                    else:
                        taint(t)
            elif isinstance(node, ast.AnnAssign) and node.value is not None and isinstance(node.target, ast.Name):
                count[node.target.id], assigned[node.target.id] = count.get(node.target.id, 0) + 1, node.value
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
                taint(node.target)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                taint(node.target)
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                taint(node.optional_vars)
            elif isinstance(node, ast.Delete):
                for t in node.targets:
                    taint(t)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                bad.update(node.names)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bad.add(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = node.args
                bad.update(a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs,
                                           *([args.vararg] if args.vararg else []), *([args.kwarg] if args.kwarg else [])])
                if not isinstance(node, ast.Lambda):
                    bad.add(node.name)
            elif isinstance(node, ast.ClassDef):
                bad.add(node.name)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS:
                taint(node.func.value)
        self.env = {n: v for n, v in assigned.items() if count[n] == 1 and n not in bad}

    def dotted(self, node) -> str | None:
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            return ".".join([self.alias.get(node.id, node.id)] + parts[::-1])
        return None

    def value(self, node, depth: int = 0):
        key = id(node)
        if key in self.memo:
            return self.memo[key]
        if depth > 40:
            return UNKNOWN
        self.memo[key] = UNKNOWN  # a name that refers to itself
        try:
            found = self._value(node, depth + 1)
            if isinstance(found, (str, bytes, list, tuple)) and len(found) > 100_000:
                found = UNKNOWN
        except Exception:
            found = UNKNOWN
        self.memo[key] = found
        return found

    def _all(self, nodes, depth):
        out = [self.value(n, depth) for n in nodes]
        return UNKNOWN if any(o is UNKNOWN for o in out) else out

    def _value(self, node, depth):
        v = self.value
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, (str, bytes, int)) else UNKNOWN
        if isinstance(node, (ast.List, ast.Tuple)):
            if any(isinstance(e, ast.Starred) for e in node.elts):
                return UNKNOWN
            out = self._all(node.elts, depth)
            return out if out is UNKNOWN or isinstance(node, ast.List) else tuple(out)
        if isinstance(node, ast.Name):
            return v(self.env[node.id], depth) if node.id in self.env else UNKNOWN
        if isinstance(node, ast.JoinedStr):
            parts = []
            for piece in node.values:
                if isinstance(piece, ast.Constant):
                    parts.append(piece.value)
                elif isinstance(piece, ast.FormattedValue) and piece.format_spec is None and piece.conversion == -1:
                    got = v(piece.value, depth)
                    if not isinstance(got, (str, int)):
                        return UNKNOWN
                    parts.append(str(got))
                else:
                    return UNKNOWN
            return "".join(parts)
        if isinstance(node, ast.BinOp):
            a, b = v(node.left, depth), v(node.right, depth)
            if a is UNKNOWN or b is UNKNOWN:
                return UNKNOWN
            if isinstance(node.op, ast.Div) and isinstance(a, str) and isinstance(b, str):
                return _Path(posixpath.join(a, b))
            if isinstance(node.op, ast.Add) and type(a) is type(b) and isinstance(a, (str, bytes, list, tuple)):
                return a + b
            if isinstance(node.op, ast.Add) and isinstance(a, str) and isinstance(b, str):
                return a + b
            if isinstance(node.op, ast.Mult) and isinstance(a, (str, bytes, list)) and isinstance(b, int) and \
                    0 <= b * len(a) <= 100_000:
                return a * b
            if isinstance(node.op, ast.Mod) and isinstance(a, (str, bytes)):
                return a % (tuple(b) if isinstance(b, list) else b)
            return UNKNOWN
        if isinstance(node, ast.Subscript):
            base = v(node.value, depth)
            if not isinstance(base, (str, bytes, list, tuple)):
                return UNKNOWN
            s = node.slice
            if isinstance(s, ast.Slice):
                got = [None if p is None else v(p, depth) for p in (s.lower, s.upper, s.step)]
                if any(g is UNKNOWN or not (g is None or isinstance(g, int)) for g in got):
                    return UNKNOWN
                return base[slice(*got)]
            index = v(s, depth)
            return base[index] if isinstance(index, int) else UNKNOWN
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            got = v(node.operand, depth)
            return -got if isinstance(got, int) else UNKNOWN
        if isinstance(node, ast.Attribute):
            if node.attr == "parent":
                base = v(node.value, depth)
                return _Path(posixpath.dirname(base)) if isinstance(base, str) else UNKNOWN
            return "/" if self.dotted(node) == "os.sep" else UNKNOWN
        if isinstance(node, ast.Call):
            return self._call(node, depth)
        return UNKNOWN

    def _call(self, node, depth):
        v = self.value
        name = self.dotted(node.func)
        if name == "map" and len(node.args) == 2 and isinstance(node.args[0], ast.Name) and node.args[0].id in ("chr", "str"):
            items = v(node.args[1], depth)
            return UNKNOWN if items is UNKNOWN else [chr(i) if node.args[0].id == "chr" else str(i) for i in items]
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            return UNKNOWN
        args = self._all(node.args, depth)
        kw = {k.arg: v(k.value, depth) for k in node.keywords}
        if args is UNKNOWN or UNKNOWN in kw.values():
            return UNKNOWN
        if name in ("os.path.expanduser", "os.path.expandvars", "posixpath.expanduser", "os.fspath"):
            return args[0]
        table = {
            "chr": chr, "ord": ord, "str": str, "int": int, "list": list, "tuple": tuple, "bytes": bytes,
            "bytearray": lambda *a: bytes(bytearray(*a)), "reversed": lambda x: list(reversed(x)),
            "bytes.fromhex": bytes.fromhex, "str.join": lambda s, x: s.join(x), "binascii.unhexlify": binascii.unhexlify,
            "binascii.a2b_hex": binascii.unhexlify, "base64.b64decode": base64.b64decode,
            "base64.urlsafe_b64decode": base64.urlsafe_b64decode, "base64.standard_b64decode": base64.b64decode,
            "base64.b32decode": base64.b32decode, "base64.b16decode": base64.b16decode,
            "base64.a85decode": base64.a85decode, "codecs.decode": lambda x, enc="utf-8", *r: codecs.decode(
                x.encode() if isinstance(x, str) and enc in ("hex", "base64", "hex_codec", "base64_codec") else x, enc, *r),
            "os.path.join": posixpath.join, "posixpath.join": posixpath.join, "os.path.normpath": posixpath.normpath,
            "os.path.abspath": posixpath.normpath, "os.path.realpath": posixpath.normpath,
            "pathlib.Path": lambda *a: _Path(posixpath.join(*a) if a else "."), "pathlib.PurePath": lambda *a: _Path(posixpath.join(*a)),
            "pathlib.PosixPath": lambda *a: _Path(posixpath.join(*a)), "pathlib.Path.home": lambda: _Path("~"),
            "shlex.split": shlex.split, "shlex.join": shlex.join,
        }
        if name in table:
            return table[name](*args, **kw)
        if isinstance(node.func, ast.Attribute):
            obj = v(node.func.value, depth)
            method = node.func.attr
            if obj is UNKNOWN:
                return UNKNOWN
            if isinstance(obj, _Path) and method in ("joinpath", "expanduser", "resolve", "absolute", "as_posix",
                                                     "__fspath__"):
                return _Path(posixpath.join(obj, *args)) if method == "joinpath" else obj
            if isinstance(obj, (str, bytes)) and method in (
                    "join", "format", "split", "rsplit", "splitlines", "strip", "lstrip", "rstrip", "lower", "upper",
                    "title", "capitalize", "swapcase", "replace", "removeprefix", "removesuffix", "encode", "decode",
                    "zfill", "partition", "rpartition", "hex"):
                return getattr(obj, method)(*args, **kw)
        return UNKNOWN


def _argv(value) -> str | None:
    """A folded command as one shell line, or None when it is neither a str nor a list of str."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and all(isinstance(x, str) for x in value):
        return " ".join(shlex.quote(x) for x in value)
    return None


def code_refusal(code: str, strict: bool = True, depth: int = 0, ctx: _Ctx | None = None, files: bool = False):
    """Why code is refused, or None. Code that does not parse is left to the older check. Every program it runs is
    folded (_Eval) and read like a terminal line; one it builds at run time is refused. strict also refuses the
    plugin's own code and settings, a module or attribute picked by a computed name, and a write where the plugin's
    folder, the settings file or a habit's files may be. files (a script run by python): only the programs whose
    command folds, and the modules that move a state or the settings."""
    if depth > SHELL_DEPTH:
        return CODE_REACH
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    ctx = ctx or _Ctx()
    ev = _Eval(tree)
    runner_funcs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            taken = [a.name for a in node.names] if isinstance(node, ast.ImportFrom) else []
            names = [f"{node.module}.{t}" if isinstance(node, ast.ImportFrom) else t for t in taken]
            for m in modules + names:
                parts = m.split(".")
                if m.startswith(STATE_MOVERS) or (m.startswith("hermes_cli.plugins_") and not files) or \
                        (strict and not files and ("habits" in parts or parts[0] in BAD_IMPORTS)):
                    return CODE_REACH
            if any(t in BAD_NAMES for t in taken):
                return CODE_REACH
        elif isinstance(node, ast.Call):
            why = _call_refusal(node, ev, strict and not files, depth, ctx, runner_funcs)
            if why:
                return why
        elif isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in runner_funcs:
            ident = node.id if isinstance(node, ast.Name) else node.attr
            if ident in BAD_NAMES and (strict or files) and not (files and ident in ("globals", "__builtins__",
                                                                                  "__loader__", "__spec__")):
                return CODE_REACH
            if strict and not files and RUNNER.match(ev.dotted(node) or ""):
                return CODE_REACH  # a program runner kept as a value, to be called later
    return _touch_refusal(code, tree, ev) if strict and not files else None


def _call_refusal(node, ev: _Eval, strict: bool, depth: int, ctx: _Ctx, runner_funcs: set) -> str | None:
    name = ev.dotted(node.func) or ""
    bare = name.rsplit(".", 1)[-1]
    if RUNNER.match(name):
        runner_funcs.add(id(node.func))
        positional = [ev.value(a) for a in node.args]
        if any(isinstance(a, ast.Starred) for a in node.args):
            return CODE_REACH if strict else None
        if UNKNOWN in positional:
            return _partial_argv(node, ev) if strict and not SPAWNERS.match(name) else None
        if SPAWNERS.match(name):
            flat = [x for p in positional for x in (p if isinstance(p, (list, tuple)) else [p])]
            line = _argv(flat)
        else:
            given = positional[0] if positional else next((ev.value(k.value) for k in node.keywords
                                                           if k.arg in ("args", "command", "cmd")), UNKNOWN)
            line = _argv(given)
        if line is None:
            return CODE_REACH if strict else None
        return shell_refusal(line, depth + 1, True, ctx)
    if bare in ("eval", "exec", "compile") and name == bare:
        text = ev.value(node.args[0]) if node.args else UNKNOWN
        if isinstance(text, str):
            return code_refusal(text, strict, depth + 1, ctx)
        return CODE_REACH if strict else None
    if name in IMPORTERS or (bare in ("import_module", "__import__") and strict):
        target = ev.value(node.args[0]) if node.args else UNKNOWN
        if "spec_from_file_location" in name or "SourceFileLoader" in name or not isinstance(target, str):
            return CODE_REACH if strict else None
        parts = target.split(".")
        if target.startswith(STATE_MOVERS) or (strict and "habits" in parts) or (strict and parts[0] in BAD_IMPORTS):
            return CODE_REACH
        return None
    if bare == "getattr" and name == "getattr":
        attr = ev.value(node.args[1]) if len(node.args) > 1 else UNKNOWN
        if strict and (not isinstance(attr, str) or attr in DANGEROUS_ATTRS | BAD_NAMES):
            return CODE_REACH
    if name in ("vars", "locals", "globals", "setattr", "delattr") and strict:
        return CODE_REACH
    return None


def _partial_argv(node, ev: _Eval) -> str | None:
    """A program run with a list whose first words fold and a later one does not (['hermes', 'chat', '-q', q]): fine
    when it is a program that runs no other (not a shell, an interpreter or a wrapper) and hermes has its subcommand
    fixed; anything else is a program built at run time."""
    first = node.args[0] if node.args else None
    if isinstance(first, ast.Name) and isinstance(ev.env.get(first.id), (ast.List, ast.Tuple)):
        first = ev.env[first.id]
    if not isinstance(first, (ast.List, ast.Tuple)) or any(isinstance(e, ast.Starred) for e in first.elts):
        return CODE_REACH
    words = []
    for e in first.elts:
        got = ev.value(e)
        words.append(_Word(got, True) if isinstance(got, str) else _Word("\x00", False))
    if not words or not words[0].fixed:
        return CODE_REACH
    b = posixpath.basename(words[0].text).lower()
    if b in SHELLS or b == "eval" or b in SOURCES or b in WRAPPERS or b in OTHER_LANGS or PYTHON.fullmatch(b):
        return CODE_REACH
    return _verb_refusal(words, code=True) or _config_refusal(words) or _hermes_refusal(words)


def _touch_refusal(code: str, tree, ev: _Eval) -> str | None:
    """Code that can write, and names in any form (folded) the plugin's folder, the settings file or a habit's files,
    or walks the Hermes folder to find them."""
    if not _code_writes(code, sql=True):
        return None
    texts, seen, overflow = [], 0, False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Constant, ast.BinOp, ast.JoinedStr, ast.Call, ast.Subscript, ast.Name)):
            if not isinstance(node, ast.Constant):  # a plain literal builds nothing at run time: it never counts
                seen += 1
                if seen > 4000:
                    overflow = True  # too much to fold: the rest is judged below, by what can build a name
            if overflow and not isinstance(node, ast.Constant):
                continue
            got = ev.value(node)
            for item in (got if isinstance(got, (list, tuple)) else [got]):
                if isinstance(item, str):
                    texts.append(item.lower())
    if overflow:  # fail closed on what was not folded: a sensitive word in the text, or anything that builds a string
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree)
                                                                           if isinstance(n, ast.Attribute)}
        if CODE_SENSITIVE.search(code) or used & STRING_BUILDERS:
            return CODE_REACH
    building = bool({"join", "joinpath", "expanduser", "Path", "glob", "iglob", "listdir", "walk", "scandir",
                     "iterdir", "rglob", "home"} & ({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} |
                                                    {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}) or
                 any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div) for n in ast.walk(tree)))
    # a path the code glues to something no text shows (home + '/state.db'): every piece is then judged by its words
    glued = overflow or any(isinstance(n, (ast.BinOp, ast.JoinedStr)) and ev.value(n) is UNKNOWN or
                            isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) in GLUERS
                            and ev.value(n) is UNKNOWN for n in ast.walk(tree))
    for s in set(texts):
        pathlike = "/" in s or (("*" in s or "?" in s) and not re.search(r"\s", s)) or \
            (building and s in ("habits", "plugin-data", "paused", "skills"))  # a query with a ? is no path
        if pathlike and not glued and s.startswith(("/", "~")) and not any(c in s for c in "*?[ \n") and not re.search(
                r"plugin-data|\.hermes|hermes_home", s):
            why = file_reach(s, None)  # a full plain path elsewhere (~/code/hermes-habits/notes.txt): where it lands
            if why:
                return why
            continue
        if pathlike and re.search(r"plugins/habits", s):
            return OWN_CODE
        if pathlike and re.search(r"plug|habits|paused", s):
            return OWN_FILES
        if pathlike and re.search(r"config\.ya?ml", s):
            return OWN_SETTINGS
        if pathlike and re.search(r"state\.db", s):
            return OWN_HISTORY
        if pathlike and re.search(r"skills|skill\.md|reflex|learned\.md|habit\.yaml", s) and _folders():
            return WHERE
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree)
                                                                          if isinstance(n, ast.Attribute)}
    hermes_ish = any(".hermes" in s or "hermes_home" in s for s in texts) or {"get_hermes_home", "HERMES_HOME"} & names
    return CODE_REACH if hermes_ish and DISCOVERY & names else None


# ---- the other tools that carry a program: process input, cron scripts, a script written for cron, any shell tool ----

def _strings(value, out: list, depth: int = 0) -> list:
    if depth > 6:
        return out
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, (list, tuple)):
        if value and all(isinstance(x, str) for x in value):
            out.append(" ".join(shlex.quote(x) for x in value))
        for x in value:
            _strings(x, out, depth + 1)
    elif isinstance(value, dict):
        for x in value.values():
            _strings(x, out, depth + 1)
    return out


BODY_SENSITIVE = re.compile(r"plug|habit|paused|config\.y|conf\*|\.hermes|hermes_home|state\.db|reflex|learned|skill\.m",
                            re.I)


def _long_refusal(text: str, home=None) -> str | None:
    """A terminal call too long to read line by line, that names the plugin's world (any other was let through
    before). A heredoc that only feeds cat or tee is data, not code: the line is read without it and judged by where
    it writes; its body is refused only when the shell could run a piece of it ($( ), a backtick). Anything else this
    long is refused."""
    skeleton, bodies = _heredocs(text)
    if not bodies or len(skeleton) > MAX_TEXT:
        return TOO_LONG.format(n=len(text))
    for line in skeleton.split("\n"):
        for marker in re.findall(r"<<< (HEREDOC\d+)", line):
            head = re.split(r"[;&|]", line.split("<<<")[0])[-1].split()
            if not head or head[0] not in ("cat", "tee") or any(w in SHELLS for w in head):
                return TOO_LONG.format(n=len(text))
    if any(("$(" in body or "`" in body) and BODY_SENSITIVE.search(body) for body in bodies.values()):
        return TOO_LONG.format(n=len(text))  # a body the shell may run pieces of, that names the plugin's world
    return terminal_reach(skeleton, home)


def reach_refusal(tool: str, args: dict, home=None) -> str | None:
    """The refusal for what a call carries to a shell or an interpreter that the terminal and execute_code checks do
    not see: the input of a process, a cron job's script, a script written into Hermes's scripts folder, the file
    tools' paths (links resolved), the program of a tool that runs one, and code."""
    if tool == "terminal":
        text = str(args.get("command") or "")
        if len(text) > MAX_TEXT:
            return _long_refusal(text, home)
        return terminal_reach(text, home)
    if tool == "execute_code":
        text = str(args.get("code") or "")
        if len(text) > 2 * MAX_TEXT:
            return TOO_LONG.format(n=len(text))
        return code_refusal(text)
    if tool in ("write_file", "patch"):
        path = str(args.get("path") or "")
        why = file_reach(path, home)
        if why:
            return why
        scripts = str(_hermes_home() / "scripts").casefold() + "/"
        if os.path.realpath(posixpath.expanduser(path)).casefold().startswith(scripts):
            content = str(args.get("content") or args.get("new_string") or "")
            return _script_refusal(content, path.endswith(".py"), 0, _Ctx())
        return None
    if tool == "process_manage" and str(args.get("action") or "") in ("write", "submit"):
        data = str(args.get("data") or "")
        return shell_refusal(data, 0, True) or (code_refusal(data) if re.search(r"hermes|habits|import", data) else None)
    if tool == "cronjob_manage" and str(args.get("action") or "") in ("create", "update"):
        for key in ("script", "monitor_script"):
            script = args.get(key)
            if isinstance(script, str) and script.strip():
                path = Path(posixpath.expanduser(script))
                path = path if path.is_absolute() else _hermes_home() / "scripts" / path
                try:
                    text = path.read_text(errors="replace") if path.is_file() and path.stat().st_size <= 1_000_000 else ""
                except OSError:
                    text = ""
                why = _script_refusal(text, path.suffix == ".py", 0, _Ctx()) if text else None
                if why:
                    return why
        return None
    if re.search(r"^mcp__|shell|exec|bash|command|run_", tool) and tool not in ("execute_code", "terminal"):
        for s in _strings(args, []):
            if re.search(r"hermes|habits", s, re.I):
                why = shell_refusal(s, 0, True)
                if why:
                    return why
    return None


# ---- the first question: does this call visibly concern the plugin's world at all? ---------------------------------------
# Everything above reads a call carefully and refuses what it cannot read. That is right for a call that concerns the
# plugin's world, and wrong for the rest of a day's work: `if [ -f x ]`, `$PYTHON run.py`, `curl … | sh` say nothing about
# Habits. So the guard first asks, cheaply, whether the call's text names that world or one of its paths lands in it: the
# hermes program, the plugin's data folder and its own code, a habit's files, Hermes's settings and history, HABITS_OFF.
# A call that does not is let through unread. A name the text does not show (decoded or glued at run time) is not seen:
# the guard blocks what it can read.

WORLD = ("hermes", "plugin-data", "state.db", "reflex.y", "skill.md", "learned.md")  # and habit, as a word's start
HABIT_WORD = re.compile(r"(?<![a-z])habit")  # habits, habit_save, HABIT.yaml, HABITS_OFF, /habits; not inhabitants
WORLD_BACKWARDS = ("semreh", "stibah", "atad-nigulp")  # the same words through rev or [::-1]
ESCAPES = re.compile(r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|[0-7]{3})")
TOKEN = re.compile(r"[^\s;|&<>()`]+")
FULL_TOKEN = re.compile(r"(?:^|[\s;|&<>()`=])((?:~|/|\$HOME|\$\{HOME\})[^\s;|&<>()`]*|[^\s;|&<>()`]*[*?\[][^\s;|&<>()`]*)")
RUNS_A_FILE = re.compile(r"(?:^|[\s;|&(])(?:\.|source|\S*sh|python[\d.]*|pypy[\d.]*|at|batch|crontab|xargs|parallel|"
                         r"script|screen|tmux|ssh|eval|exec|env|nohup)(?=\s|$)")
FIND_ACTS = re.compile(r"(?:^|[\s;|&(])find\s[^;|&]*\s-(?:delete|exec|execdir|ok|okdir|fprint|fls)")
JOINED = re.compile(r"""["']\s*\+?\s*["']""")  # 'hab' + 'its', 'her' 'mes': one text to Python
PAUSED = re.compile(r"/paused(?![\w.-])")  # the pause switch, as the last name of a path
PLUG_PIECE = re.compile(r"[\w.~-]*/[\w./~-]{3,}")
READS_A_DIFF = re.compile(r"(?:^|[\s;|&(])(?:patch|ed|ex|git\s[^;|&\n]*\b(?:apply|am))(?=\s|$)")
CODE_PATHS = re.compile(r"""['"]((?:~|/)[^'"\n\s]{1,400})['"]""")
OWN_GLOBS = ("skill.md", "assets", "assets/reflex.yml", "assets/habit.yaml", "assets/learned.md", "reflex.yml",
             "habit.yaml", "learned.md")
MANY_PATHS = 200  # past this many paths in one call the careful reading takes over
SCRIPT_BYTES = 1_000_000
ROOT = Path(__file__).resolve().parent.parent  # the plugin's own code, where it really is


def _marked_low(low: str) -> bool:
    if "hermes_tools" in low:
        low = low.replace("hermes_tools", "")  # the module code calls Hermes's tools by: each such call meets the guard
    return any(word in low for word in WORLD) or ("habit" in low and bool(HABIT_WORD.search(low))) or \
        ("/paused" in low and bool(PAUSED.search(low)))


def _unquoted(text: str) -> str:
    """A text without its quotes and backslashes (plain passes over the text: a call may hold megabytes)."""
    if "'" in text or '"' in text or "\\" in text:
        return text.replace("'", "").replace('"', "").replace("\\", "")
    return text


def _names_world(text: str, code: bool = False) -> bool:
    """A marker word of the plugin's world in a text, as it stands, with its quotes and escapes undone, backwards, and
    (code: Python, or a line that may hold some) with its string pieces joined ('hab' + 'its')."""
    low = text.lower()
    plain = _unquoted(low)
    if _marked_low(low) or (plain is not low and _marked_low(plain)):  # as written ("$E"habits) and as the shell reads
        return True
    if code and plain is not low and _marked_low(_unquoted(JOINED.sub("", low))):
        return True  # read both ways: joining "$H" "habits" would hide the word it holds
    if any(word in plain for word in WORLD_BACKWARDS):
        return True
    if "\\" in text and ESCAPES.search(text):
        try:
            if _marked_low(codecs.decode(text.encode("latin-1", "backslashreplace"), "unicode_escape").lower()):
                return True
        except (UnicodeError, ValueError):
            return True
    return False


_PLACES: dict = {}
PLACES_SECONDS = 5.0


def _code_paths() -> tuple:
    """The plugin's own code folder (trees) and the folder that holds an installed one (exact), each also with its
    links resolved: the folder this file runs from, and plugins/habits in each Hermes folder."""
    trees = {str(ROOT)} | {str(h / "plugins" / "habits") for h in (_hermes_home(), Path.home() / ".hermes")}
    exact = {str(h / "plugins") for h in (_hermes_home(), Path.home() / ".hermes")}
    return (sorted(trees | {os.path.realpath(p) for p in trees}), sorted(exact | {os.path.realpath(p) for p in exact}))


CODE_FILES = 3000


def _code_files(trees: list) -> list:
    """The folders and files of the plugin's code as they are on the disk now (3,000 at most), for a word that is a
    glob or holds a variable: it can only become a path that exists."""
    found = list(trees)
    for tree in trees:
        for folder, dirs, names in os.walk(tree):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", "venv", ".venv")]
            found += [f"{folder}/{name}" for name in [*dirs, *names]]
            if len(found) > CODE_FILES:
                return found
    return found


def _places(home) -> tuple:
    """(folders, files) of the plugin's world on this disk, links resolved, in lower case: its data folder, its code,
    Hermes's scripts folder (a script written there is read); the folders that hold them, the settings and the
    history. Kept a few seconds: a dozen links to resolve, for every call that names a path."""
    key = (str(home), os.environ.get("HERMES_HOME"), os.environ.get("HOME"))
    now = time.monotonic()
    if _PLACES.get("key") == key and now - _PLACES["at"] < PLACES_SECONDS:
        return _PLACES["value"]
    trees, exact, _files, configs = _own_paths(home)
    code_trees, code_exact = _code_paths()
    scripts = {str(h / "scripts") for h in (_hermes_home(), Path.home() / ".hermes")}
    folders = {p.casefold() for p in [*trees, *code_trees, *scripts, *(os.path.realpath(s) for s in scripts)]}
    files = {p.casefold() for p in [*exact, *code_exact, *configs, *_history_files()]}
    named = [*_files, *exact, *code_trees, *code_exact, *configs, *_history_files()]  # what a glob may become
    value = (tuple(sorted(folders)), frozenset(files), named, code_trees)
    _PLACES.update(key=key, at=now, value=value)
    return value


def _in_world(path: str, places: tuple) -> bool:
    """A full path, as the disk resolves it, inside the plugin's world or a habit's folder."""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        return True
    low = real.casefold()
    folders, files = places[0], places[1]
    if low in files or any(_inside(low, folder) for folder in folders):
        return True
    return _in_habit(real)


def _in_habit(real: str) -> bool:
    """A path in a habit's folder: the folder or one above it (six levels) holds assets/reflex.yml or HABIT.yaml."""
    home = str(Path.home())
    try:
        folder = real if os.path.isdir(real) else os.path.dirname(real)
        if not os.path.isdir(folder):
            return False  # no folder there yet: not a habit's (and no stat after stat on a path that does not exist)
        for _ in range(6):
            if folder in ("/", home, "") or folder == os.path.dirname(folder):
                return False
            if os.path.isdir(folder + "/assets") and (os.path.isfile(folder + "/assets/reflex.yml") or
                                                      os.path.isfile(folder + "/assets/HABIT.yaml")):
                return True
            folder = os.path.dirname(folder)
    except (OSError, ValueError):
        return True
    return False


def _full_path(text: str, base: str | None) -> str | None:
    """A token as a full path: ~ and $HOME spelled out, a relative one from base; None when it cannot be placed."""
    for home in ("$HOME", "${HOME}"):
        if text.startswith(home):
            text = "~" + text[len(home):]
    if text == "~" or text.startswith("~/"):
        text = str(Path.home()) + text[1:]
    if "$" in text or "\x00" in text or text.startswith("~"):
        return None
    if text.startswith("/"):
        return text
    return os.path.join(base, text) if base else None


def _script_names_world(path: str, runner: bool) -> bool:
    """A file a line may run (a script by its suffix or its first line, or any file when the line runs a shell or an
    interpreter) whose text names the plugin's world."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > SCRIPT_BYTES:
            return False
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return False
    if not (runner or raw[:2] == b"#!" or os.path.splitext(path)[1].lower() in SCRIPT_SUFFIXES | {".diff", ".patch"}):
        return False
    return _names_world(raw.decode("utf-8", "replace"))


def _line_concerns(command: str, workdir: str, places: tuple) -> bool:
    """A terminal line that names the plugin's world, or one of whose paths lands in it, or that runs a script whose
    text names it. A cheap cut of the line into tokens: no shell reading."""
    if _names_world(command, code=True):
        return True
    base = os.path.realpath(os.path.expanduser(workdir)) if workdir and _full(workdir) else None
    if base and _in_world(base, places):
        return True
    plain = _unquoted(command)
    if "find" in plain and FIND_ACTS.search(plain):
        return True  # a find that deletes or runs a program: which files it reaches is read with care
    if "plug" in plain.lower():  # a piece of the plugin's own paths, the rest in a variable or a glob
        world = [p.casefold() for p in places[2]]
        for m in PLUG_PIECE.finditer(plain.lower()):
            piece = m.group()
            if piece[:1] == "~":
                piece = str(Path.home()).casefold() + piece[1:]
            if len(piece) >= 6 and "plug" in piece and any(piece in p for p in world):
                return True
    runner = bool(RUNS_A_FILE.search(plain) or READS_A_DIFF.search(plain))
    seen, after_cd = set(), False
    # with no folder to start from, only a full path or a glob can be placed: the other words are not even cut
    relative = base is not None or "cd " in plain or "pushd " in plain
    for m in (TOKEN if relative else FULL_TOKEN).finditer(plain):
        token = m.group() if relative else m.group(1)
        if token in ("cd", "pushd"):
            after_cd = True
            continue
        if "=" in token and not token.startswith(("/", "~", ".")):
            token = token[token.index("=") + 1:]  # VAR=/path, of=/path, --out=/path
        entering, after_cd = after_cd, False
        if not token or token.startswith("-"):
            base = None if entering else base  # cd - : where the line stands is unknown from here
            continue
        if any(c in token for c in "*?["):
            low = token.casefold()
            for pattern in {low, low.rsplit("/", 1)[-1], "/".join(low.rsplit("/", 2)[-2:])}:
                if sum(c not in "*?[]/" for c in pattern) >= 4 and any(fnmatch.fnmatchcase(name, pattern)
                                                                      for name in OWN_GLOBS):
                    return True  # SKILL.m?, assets/refle*: a glob that can only be a habit's own file
            full = _full_path(token, base)
            if full:  # a glob on a full path: may it be one of the plugin's files? (~/.her*/plug*/hab*/*.db)
                word = shell.Word(full, tuple(False for _ in full))
                if _reaches(word, [], [], places[2]) or (re.search(r"plug|hab", low) and _reaches(
                        word, [], [], _code_files(places[3]))):
                    return True
            base = None if entering else base
            continue
        path = _full_path(token, base)
        if entering:
            base = os.path.realpath(path) if path else None
        if path is None or path in seen:
            continue
        seen.add(path)
        if len(seen) > MANY_PATHS or _in_world(path, places) or _script_names_world(path, runner):
            return True
    return False


def _write_concerns(tool: str, args: dict, places: tuple) -> bool:
    """A file tool's call: by where it writes, and by its text when what it writes is a script (a suffix, a first
    line). Prose, data and source in another language are not read: they act on nothing by themselves."""
    path = str(args.get("path") or "")
    if _names_world(path) or args.get("patch") and _names_world(str(args.get("patch"))):
        return True
    full = _full_path(path, None)
    if full and _in_world(full, places):
        return True
    content = str(args.get("content") or args.get("new_string") or "")
    return _is_script(path, content) and _names_world(content, code=path.lower().endswith(".py"))


SHELL_FILES = (".env", ".envrc", ".zshenv", ".zshrc", ".zprofile", ".bashrc", ".bash_profile", ".profile")


def _is_script(path: str, content: str) -> bool:
    """A file that runs: a shell or Python suffix, a #! first line, or a file a shell or Hermes loads at its start."""
    name = posixpath.basename(path).lower()
    return os.path.splitext(name)[1] in SCRIPT_SUFFIXES or content.startswith("#!") or name in SHELL_FILES or \
        name.endswith(".env")


def _code_concerns(code: str, places: tuple) -> bool:
    """Code that names the plugin's world, or holds a full path that lands in it or is a script that names it."""
    if _names_world(code, code=True):
        return True
    seen = set()
    for m in CODE_PATHS.finditer(code):
        path = _full_path(m.group(1), None)
        if path is None or path in seen:
            continue
        seen.add(path)
        if len(seen) > MANY_PATHS or _in_world(path, places) or _script_names_world(path, True):
            return True
    return False


def concerns(tool: str, args: dict, home=None) -> bool:
    """Whether a call visibly concerns the plugin's world: only then is it read carefully (and refused when it cannot
    be read). Any other call passes untouched."""
    if tool == "skill_manage":
        return True  # which skill is a habit is asked of the folders, once the call is here
    if tool == "terminal":
        return _line_concerns(str(args.get("command") or ""), str(args.get("workdir") or ""), _places(home))
    if tool == "execute_code":
        return _code_concerns(str(args.get("code") or ""), _places(home))
    if tool in ("write_file", "patch"):
        return _write_concerns(tool, args, _places(home))
    if tool == "process_manage":
        return _names_world(str(args.get("data") or ""), code=True)
    if tool == "cronjob_manage":
        return str(args.get("action") or "") in ("create", "update")
    if re.search(r"^mcp__|shell|exec|bash|command|run_", tool):
        return any(_names_world(s) for s in _strings(args, []))
    return False


class Guard:
    """The pre_tool_call hook of Habits."""

    def on_pre_tool_call(self, tool_name="", args=None, session_id="", task_id="", **_):
        """A habit's files change through habit_save only; a new habit through habit_save; a
        state through the user only. Hermes's code sandbox calls its tools with no session, only its task's id.

        A shell that a passing line (cd <plugin's folder> && git status) left standing in the plugin's folder is
        remembered for that session or task: its next lines run there, so a relative write is the plugin's file."""
        args = args if isinstance(args, dict) else {}
        if str(tool_name or "") != "terminal" or not isinstance(args.get("command"), str):
            return self._guarded(tool_name, args, session_id, task_id)
        if "$" in args["command"] and ("=" in args["command"] or "${" in args["command"] or "$((" in args["command"]) \
                and not SAVING.get():
            try:  # a variable the line sets to a plain path is read as that path (S=<folder>; cat $S/SKILL.md)
                args = {**args, "command": _resolved(args["command"])}
            except Exception as exc:
                logger.warning("habits: could not write out the line's variables: %s", exc)
        key, command = str(task_id or session_id or ""), args["command"]
        standing = self.__dict__.setdefault("standing", {}).get(key)
        checked = args
        if standing and not args.get("workdir"):  # where the line's own commands run: the cd it starts with, else here
            checked = {**args, "workdir": _cd_walk(command, standing, first=True) or "/"}
        verdict = self._guarded(tool_name, checked, session_id, task_id)
        if verdict is not None and checked is not args and verdict.get("message") == OWN_CODE:
            verdict = {**verdict, "message": STANDING_NOW.format(folder=standing)}
        if verdict is None and (standing or "cd" in command or "pushd" in command) and not args.get("workdir"):
            try:
                after = _cd_walk(command, standing)
                code_trees = _code_paths()[0]
                if after and any(_inside(after.casefold(), t.casefold()) for t in code_trees):
                    if len(self.standing) > 500:
                        self.standing.clear()
                    self.standing[key] = after
                else:
                    self.standing.pop(key, None)
            except Exception as exc:
                logger.warning("habits: could not follow the shell's folder: %s", exc)
        return verdict

    def _guarded(self, tool_name="", args=None, session_id="", task_id=""):
        if SAVING.get():
            forget_folders()
            return None
        tool, args = str(tool_name or ""), args if isinstance(args, dict) else {}
        try:
            home = self._guard_home()
            if not concerns(tool, args, home):
                return None  # ordinary work: nothing of the plugin's world in sight
            why = changes_state(tool, args, home)
            if why:
                return {"action": "block", "message": why}
            name = edited_habit(tool, args)
            if name:
                return {"action": "block", "message": WHERE if name == ANYWHERE else HAND_EDIT.format(name=name)}
            name = self.merged(tool, args)
            if name:
                return {"action": "block", "message": MERGE.format(name=name)}
            made = made_habit(tool, args)
            if made:
                return self.door(made, str(session_id or task_id or ""))
            why = reach_refusal(tool, args, home)
            if why:
                return {"action": "block", "message": why}
            why = hermes_habits_refusal(tool, args)  # last: the more precise refusals above speak first
            return {"action": "block", "message": why} if why else None
        except Exception as exc:
            logger.warning("habits: pre_tool_call failed: %s", exc)
            if tool in FAIL_CLOSED:  # a broken guard must not let a state change or a habit edit through
                return {"action": "block", "message": BROKEN_GUARD}
            return None  # and must not stop Hermes from reading a file
        finally:
            forget_folders()  # the list lasts one call: the next one may come after a save, a removal or a mkdir

    def _guard_home(self):
        try:
            return self.home()
        except Exception:  # outside Hermes: the default folder is checked anyway
            return None

    def merged(self, tool: str, args: dict) -> str | None:
        """A habit skill_manage would delete or absorb into an umbrella skill. Whoever asks: in the plugin host, Hermes
        cannot say whether a review or the curator makes the call (its ContextVar stays in the agent's process), and
        the user puts a habit to rest in /habits."""
        if tool != "skill_manage":
            return None
        names = _skill_names(args, {"delete"})
        return _habit_of(names, _folders()) if names else None

    def door(self, name: str, session: str = ""):
        """A reflex.yml (or HABIT.yaml) written into a skill outside habit_save: refused toward habit_save. Each
        session (or task, when the call names none) counts its own refusals; past 2 in 10 minutes the refusal says stop."""
        now = time.monotonic()
        self.refusals = {key: kept for key, times in self.refusals.items()
                         if (kept := [at for at in times if now - at < REFUSALS_SECONDS])}
        times = self.refusals.setdefault(session, [])
        times.append(now)
        stop = STOP.format(n=len(times)) if len(times) > REFUSALS else ""
        return {"action": "block", "message": DOOR.format(name=name) + stop}
