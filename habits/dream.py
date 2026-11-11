"""The dream's core: what comes back in Hermes's history, spotted, and the small reflex that could take it over.

Pure functions over plain turns. The only contact with Hermes is `open_store`, which imports it on call; everything
else reads the dicts a store hands back, so it is tested with a fake store and never needs Hermes. The dream calls no
model, keeps nothing of a result but its status, and counts again from zero each time (nothing accumulates).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from . import graph, runner, shell

HISTORY_DAYS = 60          # the window a dream reads
MIN_TURNS, MIN_DAYS = 3, 2  # what comes back: at least 3 turns on 2 days
NEAR = 3                   # gestures shown when nothing is spotted (each seen in >= 2 turns)
TOGETHER = 0.8             # grow: share of each other's turns
CHANGE_QUIET_DAYS = 14     # change: the reflex's own form unseen this long
REST_QUIET_DAYS = 30       # rest: neither the reflex nor its action seen this long
FRESH_DAYS = 30            # an action last seen longer ago than this stopped being a habit: not proposed
MAX_SESSIONS = 3000        # sessions read at most per dream; past it the read is "partial"
READ_SECONDS = 20.0        # time budget of read_turns; past it "partial"
PAGE = 200                 # sessions per list_sessions_rich page
KEY_CHARS = 2000           # an action whose canonical JSON is longer is never a candidate
COMPARE_CHARS = 20000      # what a comparison looks at, in memory (nothing is stored)
GROW_STEPS = 4             # steps of a grown reflex, at most
GROW = True                # cut line: False and `group` gives singles only
LOCAL_TOOLS = frozenset({"read_file", "search_files"})   # local reads besides terminal readers
WRAPPER = "tool_call"

OWN = frozenset({"habit_run", "habit_save"})
BOOKKEEPING = frozenset({"tool_search", "tool_describe", "todo", "memory", "clarify"})
MOMENT = frozenset({"skill_view"})
CODE = frozenset({"execute_code", "delegate_task"})


# ---- shapes -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Call:
    tool: str               # the real tool name, the wrapper peeled
    args: dict              # parsed arguments ({} when they could not be read)
    order: int              # position in its turn, 0-based, after unwrapping
    failed: bool = False    # its result row says blocked, pending, denied, error or not runnable


@dataclass(frozen=True)
class Turn:
    session: str            # the session row id
    index: int              # 0 = the row's first turn
    at: float               # epoch seconds of the user message
    day: str                # local date, "YYYY-MM-DD"
    first: bool             # index 0 of a row that is not a continuation
    scheduled: bool         # the session's source is "cron"
    cwd: str                # the session row's cwd, "" when unknown
    calls: tuple


@dataclass(frozen=True)
class Read:
    turns: list
    sessions: int           # session rows read (sub-agents not counted)
    skipped: int            # rows or messages of a shape not understood
    status: str             # "ok" | "empty" | "partial" | "unread"
    why: str = ""           # for unread/partial: one short reason, never a path or a value
    span: int = 0           # days from the oldest to the newest turn read, counted inclusively; 0 when none


@dataclass(frozen=True)
class Action:
    key: str                # key_of(tool, args)
    tool: str
    args: dict              # the most common raw form
    raw: str                # canonical JSON of {tool, args}
    label: str = ""         # what `brief` shows when two spotted actions would show the same (set by `spot`)


@dataclass
class Spotted:
    action: Action
    turns: int
    days: int
    first_seen: float
    last_seen: float
    moments: dict           # {"first": share, "last": share, "skill": [name, share] | None, "scheduled": share}
    forms: dict             # raw form -> number of turns it appeared in
    cwd: str                # shared by >= 80 % of its turns, else ""
    where: frozenset        # {(session, index)} of its turns, for grow and siblings
    stamps: tuple           # ((at, day), ...) of its turns, oldest first: evidence after a no


@dataclass(frozen=True)
class Near:                 # seen in >= 2 turns, not spotted: shown when nothing is
    action: Action
    turns: int
    days: int


@dataclass(frozen=True)
class Proposal:
    kind: str               # "change" | "rest"
    subject: str            # the habit name
    detail: dict            # evidence, JSON-safe


# ---- the store (the only contact with Hermes) ---------------------------------------------------------------------

class EmptyStore:
    """A history that does not exist yet: a fresh home reads as empty, not as an error."""

    def list_sessions_rich(self, **kw):
        return []

    def get_messages(self, session_id, **kw):
        return []

    def close(self):
        pass


def open_store(home=None):
    """Hermes's session store, read only; EmptyStore when it has no state.db yet; None when Hermes cannot be reached
    (older Hermes, a test): the dream then says it could not read. `home`: the folder of ANOTHER Hermes whose
    state.db is read (read only, nothing is written there); None when it has no state.db, because a folder the user
    named that holds no history is not an empty history."""
    try:
        from hermes_constants import get_hermes_home  # lazy: this module imports nothing of Hermes at load time
        from hermes_state import SessionDB
        if home is not None:
            db = Path(home).expanduser() / "state.db"
            return _opened(lambda: SessionDB(db_path=db, read_only=True)) if db.is_file() else None
        if not (get_hermes_home() / "state.db").exists():
            return EmptyStore()
        return _opened(lambda: SessionDB(read_only=True))
    except Exception:
        return None


BUSY_TRIES = 3          # a store that Hermes is writing to answers "locked" or "busy" for a moment: ask again, briefly
BUSY_PAUSE = 0.25       # seconds between two tries


def _opened(make, pause: float = BUSY_PAUSE):
    """make(), asked again a few times when the store says it is locked or busy; any other error is raised."""
    for attempt in range(BUSY_TRIES):
        try:
            return make()
        except Exception as exc:
            if attempt + 1 == BUSY_TRIES or not _busy(exc):
                raise
            time.sleep(pause)


# ---- reading history ----------------------------------------------------------------------------------------------

def _json(value):
    """A JSON text decoded; anything else, or a text that is not JSON, comes back as is."""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def calls_of(message: dict) -> list:
    """(tool, args, call_id) of an assistant message, the `tool_call` wrapper peeled, in order. Inner calls share the
    wrapper's id; a wrapper of another shape gives nothing."""
    items = _json(message.get("tool_calls")) if isinstance(message, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else item
        name, args = fn.get("name"), _args(fn.get("arguments"))
        if not isinstance(name, str) or not name:
            continue
        cid = str(item.get("id") or "")
        if name == WRAPPER:
            inner = args.get("calls")
            if isinstance(inner, str):
                inner = _json(inner)
            if isinstance(inner, list):
                for each in inner:
                    if isinstance(each, dict) and isinstance(each.get("name"), str) and each["name"] \
                            and each["name"] != WRAPPER:   # a wrapper inside a wrapper is no call
                        out.append((each["name"], _args(each.get("arguments")), cid))
            continue
        out.append((name, args, cid))
    return out


def _args(value) -> dict:
    value = _json(value) if isinstance(value, (str, bytes)) else value
    return value if isinstance(value, dict) else {}


def failed(tool_message: dict) -> bool:
    """A result that says the call did not happen: blocked, pending approval, denied, an error, success false, or a
    program that could not run (exit -1, 126, 127). Only these keys are read; a text that is not JSON is not a failure."""
    content = tool_message.get("content") if isinstance(tool_message, dict) else None
    data = _json(content) if isinstance(content, (str, bytes)) else content
    if not isinstance(data, dict):
        return False
    if data.get("status") in ("blocked", "pending_approval", "denied"):
        return True
    error = data.get("error")
    if isinstance(error, str) and error.strip():
        return True
    if data.get("success") is False:
        return True
    code = data.get("exit_code")
    return isinstance(code, int) and not isinstance(code, bool) and code in (-1, 126, 127)


def _marker(model_config, name: str) -> bool:
    data = _json(model_config) if isinstance(model_config, (str, bytes)) else model_config
    return isinstance(data, dict) and name in data


def _local_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _window_start(now: float, days: int) -> float:
    """The start of the window: the first moment of the day `days - 1` days before today, so that N days read are the N
    calendar days the summary counts (today included)."""
    try:
        today = time.localtime(now)
        start = time.mktime((today.tm_year, today.tm_mon, today.tm_mday - (int(days) - 1), 0, 0, 0, 0, 0, -1))
        return min(start, now)
    except (OverflowError, ValueError, OSError):
        return now - days * 86400


def read_turns(store, now: float, days: int = HISTORY_DAYS, *, clock=time.monotonic,
               max_seconds: float = READ_SECONDS, pause: float = BUSY_PAUSE) -> Read:
    """The turns of the last `days` days, with the calls of each. Fails soft: whatever cannot be read is counted. A
    message dated after tomorrow (a foreign or damaged row: milliseconds, inf) is not understood, counted and left
    out; a store that answers locked or busy is asked again a few times before the read is given up."""
    if store is None:
        return Read([], 0, 0, "unread", "no history store")
    if not callable(getattr(store, "list_sessions_rich", None)) or not callable(getattr(store, "get_messages", None)):
        return Read([], 0, 0, "unread", "unknown history shape")
    started = clock()
    since = _window_start(now, days)
    try:
        for attempt in range(BUSY_TRIES):
            got = _read(store, since, started, clock, max_seconds, now + 86400)
            if got.status != "unread" or got.why != "OperationalError" or attempt + 1 == BUSY_TRIES \
                    or clock() - started > max_seconds:
                return got
            time.sleep(pause)  # an OperationalError is mostly a lock Hermes holds for a moment
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


_FULL = dict(include_children=True, include_archived=True, include_hidden=True, compact_rows=True,
             order_by_last_active=True)
# Listings to try in turn on the first page: the planned one, then whole rows, then the order of
# `started_at` (a row is then kept until it is a month older than the window), then Hermes's own defaults.
LADDER = (_FULL, {**_FULL, "compact_rows": False}, {**_FULL, "compact_rows": False, "order_by_last_active": False}, {})
STARTED_SLACK = 30 * 86400


def _busy(exc) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _list(store, since, started, clock, max_seconds):
    """Every session row, newest chain first: (rows, partial reason or "", unread reason or "")."""
    rows, partial = [], ""
    level, offset, first_error = 0, 0, ""
    while True:
        try:
            page = store.list_sessions_rich(limit=PAGE, offset=offset, **LADDER[level])
        except Exception as exc:
            if offset == 0:
                first_error = first_error or type(exc).__name__
                if level + 1 < len(LADDER) and not _busy(exc):   # a locked store would only wait again
                    level += 1
                    continue
                return rows, "", first_error
            return rows, "a page of sessions could not be read", ""
        page = list(page) if isinstance(page, (list, tuple)) else []
        rows.extend(page)
        floor = since - (STARTED_SLACK if "order_by_last_active" not in LADDER[level]
                         or not LADDER[level]["order_by_last_active"] else 0)
        old = all(isinstance(r, dict) and (_number(r.get("last_active")) or _number(r.get("started_at")) or 0) < floor
                  and r.get("end_reason") != "compression" for r in page)
        if len(page) < PAGE or (page and old):
            return rows, partial, ""
        if len(rows) >= MAX_SESSIONS:
            return rows, "too many sessions", ""
        if clock() - started > max_seconds:
            return rows, "out of time", ""
        offset += PAGE


def _read(store, since, started, clock, max_seconds, ceiling: float = float("inf")) -> Read:
    rows, partial, unread = _list(store, since, started, clock, max_seconds)
    if unread:
        return Read([], 0, 0, "unread", unread)
    skipped, byid = 0, {}
    usable = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("id"):
            skipped += 1
            continue
        if r["id"] in byid:  # a row listed twice (the paging moved under us): read once
            continue
        byid[r["id"]] = r
        usable.append(r)
    turns, sessions = [], 0
    for r in usable:
        activity = _number(r.get("last_active")) or _number(r.get("started_at")) or 0
        if activity < since:
            continue
        if r.get("source") == "subagent" or _marker(r.get("model_config"), "_delegate_from"):
            continue
        if clock() - started > max_seconds:
            partial = partial or "out of time"
            break
        parent = byid.get(r.get("parent_session_id"))
        continuation = bool(r.get("parent_session_id")) and parent is not None and parent.get("end_reason") == "compression"
        floor = since
        if _marker(r.get("model_config"), "_branched_from"):
            floor = max(since, _number(r.get("started_at")) or 0)
        try:
            messages = store.get_messages(r["id"])
        except Exception:
            skipped += 1
            continue
        sessions += 1
        got, bad = _turns_of(r, messages, floor, continuation, ceiling)
        turns.extend(got)
        skipped += bad
    turns.sort(key=lambda t: t.at)
    if partial:
        status = "partial"
    else:
        status = "ok" if turns else "empty"
    span = 0
    if turns:
        first, last = time.strptime(turns[0].day, "%Y-%m-%d"), time.strptime(turns[-1].day, "%Y-%m-%d")
        span = int((time.mktime(last) - time.mktime(first)) / 86400 + 0.5) + 1
    return Read(turns, sessions, skipped, status, partial, span)


def _turns_of(r, messages, floor, continuation, ceiling: float = float("inf")):
    """The turns of one session row: (turns, messages not understood)."""
    out, bad, n = [], 0, 0
    current = None
    scheduled = r.get("source") == "cron"
    cwd = r.get("cwd") if isinstance(r.get("cwd"), str) else ""

    def close():
        if current is not None:
            out.append(Turn(r["id"], current["index"], current["at"], _local_day(current["at"]),
                            current["index"] == 0 and not continuation, scheduled, cwd,
                            tuple(Call(t, a, i, f) for i, (t, a, _id, f) in enumerate(current["calls"]))))

    if not isinstance(messages, (list, tuple)):
        return out, 1
    for m in messages:
        if not isinstance(m, dict) or not isinstance(m.get("role"), str):
            bad += 1
            continue
        role = m["role"]
        if role == "user":
            close()
            index, n = n, n + 1
            at = _number(m.get("timestamp"))
            current = {"index": index, "at": at, "calls": []} if at is not None and floor <= at <= ceiling else None
            if at is None or at > ceiling or at != at:  # none, in the future, inf or nan: not a time Hermes wrote
                bad += 1
        elif current is None:
            continue
        elif role == "assistant":
            for tool_name, args, cid in calls_of(m):
                current["calls"].append((tool_name, args, cid, False))
        elif role == "tool":
            cid = m.get("tool_call_id")
            if cid and failed(m):
                current["calls"] = [(t, a, i, True) if i == cid else (t, a, i, f) for t, a, i, f in current["calls"]]
    close()
    return out, bad


# ---- the key of an action and the exclusions ----------------------------------------------------------------------

_TRIMMERS = (re.compile(r"\s*;\s*echo\b[^;|&]*\$\?[^;|&]*$"),
             re.compile(r"\s*\|\s*(?:head|tail)(?:\s+-n)?(?:\s+-?\d+)?$"),
             re.compile(r"\s*2>&1$"),
             re.compile(r"\s*2>\s*/dev/null$"),
             re.compile(r"\s*\|\s*cat$"))


def command_key(command: str) -> str:
    """The command with its white space collapsed and the trimmers at its end removed (they change the output, not
    the gesture)."""
    collapsed = " ".join(command.split())
    text = collapsed
    while True:
        before = text
        for trimmer in _TRIMMERS:
            text = trimmer.sub("", text)
        if text == before:
            break
    return text.strip() or collapsed


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def key_of(tool: str, args: dict) -> str | None:
    """What makes two calls the same gesture; None when the shape has no key or is too long."""
    args = args if isinstance(args, dict) else {}
    if tool == "terminal":
        command = args.get("command")
        key = "terminal:" + command_key(command) if isinstance(command, str) and command.strip() else None
    elif tool == "read_file":
        path = args.get("path") or args.get("file_path")
        key = "read_file:" + path.rstrip("/") if isinstance(path, str) else tool + ":" + _canonical(args)
    else:
        key = tool + ":" + _canonical(args)
    return key if key is not None and len(key) <= KEY_CHARS else None


def reflex_call(tool: str, args: dict) -> str | None:
    """The key of a call of a saved reflex (`habit_run` and its name): `reflex:NAME`, else None. The one rule the
    dream's grouping and the practice of a grown draft both use, so a reflex in a draft is seen the same way."""
    name = args.get("name") if tool == "habit_run" and isinstance(args, dict) else None
    return f"reflex:{name}" if isinstance(name, str) and name else None


def raw_of(tool: str, args: dict) -> str:
    return _canonical({"tool": tool, "args": args})


KEYLIKE = (re.compile(r"(?:sk-|sk_live_|pk_live_|rk_live_|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|xox[abprs]-)"
                      r"[A-Za-z0-9_.-]{8,}|(?:AKIA|ASIA)[A-Z0-9]{12,}|AIza[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9_.-]{8,}|"
                      r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY|"
                      r"hooks\.slack\.com/services/\S+|discord(?:app)?\.com/api/webhooks/\S+"),
           # NAME=value, or "Name: value" whose value has the shape of a secret (a digit in it), or a header
           re.compile(r"(?<![A-Za-z0-9])(?:api[_-]?key|token|secret|passw(?:or)?d|bearer|authorization)(?![A-Za-z0-9])"
                      r"(?:\s*=\s*(?:(?:basic|bearer|token|digest)\s+)?\S{8,}|"
                      r"\s*:\s*(?:(?:basic|bearer|token|digest)\s+\S{8,}|(?-i:(?=\S*[\dA-Z=+/]))\S{8,}))", re.I),
           re.compile(r"(?<![A-Za-z0-9])bearer\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{12,}", re.I),
           re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}\b"))
_DOLLAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
_QUIET = r"(?:(?!\$\()[^|>;&<`])*"  # what an echo may print: no pipe, redirect, second command or substitution
NOISE = re.compile(r"(?:sleep\s+\S+(?:\s*(?:;|&&)\s*echo\b" + _QUIET + r")?|echo\b" + _QUIET +
                   r"|cd(?:\s+\S+)?|true|false|:|pwd|clear)")
PRIVATE_NAMES = frozenset({".zshrc", ".zshenv", ".zprofile", ".zlogin", ".bashrc", ".bash_profile", ".profile",
                           ".zsh_history", ".bash_history", ".netrc", ".npmrc", ".pypirc", ".pgpass",
                           ".git-credentials", "rclone.conf", ".htpasswd", ".boto", ".s3cfg", ".my.cnf",
                           ".mylogin.cnf", ".psql_history", ".mysql_history", ".python_history", ".dockercfg",
                           ".node_repl_history", ".irb_history", ".claude.json", ".dbshell",
                           ".envrc", ".gitconfig", ".zhistory", ".sh_history", ".lesshst", ".viminfo",
                           ".sqlite_history", ".rediscli_history", ".authinfo", ".vault-token", ".terraformrc",
                           ".wgetrc", ".curlrc", ".yarnrc", ".msmtprc", ".secrets", ".zsh_sessions"})
# A name is private by its start too (.zshrc.local, .zshrc.pre-oh-my-zsh, .bash_history.bak).
_PRIVATE_PREFIXES = tuple(sorted(PRIVATE_NAMES))
# Names a pattern such as id_* or cred* could match (a pattern that starts with a literal is tried against them).
_PRIVATE_SAMPLES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials", "credentials.json", "secrets.yml",
                    "secrets.json", "token.json", "auth.json", "rclone.conf", "service-account.json", "server.key",
                    "private.pem", "keystore.jks")
# Where keys and logins live in a home folder: Hermes's own write-denied list, the coding agents' logins and the
# command-line tools that keep a token. The one list of them: the dream stays out of these folders (PRIVATE_DIRS,
# _homes, a pattern that could be one of them), and a file there is never read as a reflex (tools.graph_file), since a
# refusal or a page would copy it out. Hermes's own folder is not in it: each side has its own rule for it.
PRIVATE_FOLDERS = (".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".password-store", ".1password",
                   ".anthropic", ".codex", ".claude", ".netrc", ".config/gh", ".config/glab-cli", ".config/gcloud",
                   ".config/rclone", ".config/op", ".config/git", ".config/hub", ".config/openai", "Library/Keychains")
PRIVATE_DIRS = re.compile("|".join(rf"(?:^|/){re.escape(folder)}(?:/|$)" for folder in (".hermes", *PRIVATE_FOLDERS)),
                          re.I)
# Text that carries a secret whatever the program: NAME=value or "Name: value" where the name says it is one,
# a password or user:password given to a program, a password flag followed by its value. The name is the word that
# says secret, not a word that merely holds those letters (passthru, tokenizer, credit); a bare `key` is not one.
_SECRET_NAME = re.compile(r"pass(?!thru|through|es\b|enger|port|ive|ed\b)|pwd|secret|token(?!iz)|auth(?!or(?!iz))|"
                          r"cred(?!it)|cookie|bearer|signature|api[_-]?key|apikey|private[_-]?key|access[_-]?key|"
                          r"session[_-]?id|(?<=[_.-])keys?(?:$|[_.-])|(?:^|[_.-])(?:totp|otp|pin)(?:$|[_.-])", re.I)
_COUNT_NAME = re.compile(r"(?:^|[_.-])(?:max|min|num|count|limit|len|length|size|total|per)(?:$|[_.-])", re.I)
_QUERY_NAME = re.compile(r"^(?:key|sig|sign|sid)$", re.I)
_PAIR = re.compile(r"""(?<![A-Za-z0-9])-{0,2}([A-Za-z][A-Za-z0-9_.-]*)["']?[ \t]*([:=])[ \t]*["']?([^\s"'&,}\]]+)""")
_PLAIN_VALUES = frozenset({"no", "yes", "true", "false", "on", "off", "none", "null", "0", "1", "never", "always"})
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# A user:password given to a program (curl -u me:pw; not docker's -u 1000:1000), and a secret inside a URL.
_USERPASS = re.compile(r"(?:^|\s)(?:-u|-U|--user|--proxy-user)[\s=]*(?!\d+:\d+(?:\s|$))\S*[:%]\S+")
_URL_USER = re.compile(r"://[^/\s:@]+:([^/\s@]+)@|://(?=[^/\s@]*\d)[A-Za-z0-9_.~%-]{20,}@|"
                       r"/bot\d{5,}:[A-Za-z0-9_-]{6,}")
_URL_QUERY = re.compile(r"[?&;]([A-Za-z][A-Za-z0-9_.-]*)=([^&#\s\"']+)")
_ENVIRONMENT = re.compile(r"/environ\b|\bENVIRON\b|\bos\.environ|\bgetenv\b|\$ENV\b|%ENV\b|\bENV\[|\bENV\.|\benviron\b|"
                          r"process\.e" r"nv|\$_EN" r"V\b|\bDeno\.e" r"nv")
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,10}$")
_PIECES = re.compile(r"[\s;|&()<>`'\"=,:]+")
_SYNTAX = re.compile(r"[\s;|&()<>]|\$\(")
_GLOB = re.compile(r"[*?\[{]")
_BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")
SECRET_PROGRAMS = frozenset({"sshpass", "security", "pass", "gopass", "op", "bw", "lpass", "doppler", "sops", "keyctl",
                             "secret-tool", "gpg", "gpg2", "chamber", "aws-vault", "keepassxc-cli", "kwallet-query",
                             "age", "pbpaste", "xclip", "xsel", "wl-paste", "history", "fc", "printenv", "ssh-add",
                             "htpasswd", "chpasswd", "passwd", "wpa_passphrase", "mkpasswd"})
# A short list, not a complete one: the programs that take a password after a short option, and which option. A
# secret typed in a command cannot be caught by a list; the report shows an acting command shortened (see `brief`).
_PASSWORD_AFTER = {"redis-cli": ("-a",), "mongosh": ("-p",), "mongo": ("-p",), "mongodump": ("-p",),
                   "mongorestore": ("-p",), "sqlcmd": ("-P",), "unzip": ("-P",), "zip": ("-P",),
                   "ldapsearch": ("-w",), "ldapmodify": ("-w",), "ldapadd": ("-w",), "ldapdelete": ("-w",),
                   "ldappasswd": ("-w",), "rabbitmqadmin": ("-p",), "mosquitto_pub": ("-P",),
                   "mosquitto_sub": ("-P",), "openssl": ("-k", "-pass", "-passin", "-passout", "-passwd"),
                   "ssh-keygen": ("-P", "-N"), "sshpass": ("-p",), "lftp": ("-u",)}
# And the ones that glue it: mysql -pSECRET (a bare -p only asks for it).
_SUDO = "su" "do"
_PASSWORD_GLUED = frozenset({"mysql", "mysqldump", "mysqladmin", "mysqlimport", "mariadb", "mariadb-dump", "7z", "7za",
                             "7zr", "rar", "unrar"})
_CLOUD = frozenset({"gh", "glab", "aws", "gcloud", "az", "docker", "podman", "skopeo", "crane", "helm", "oras",
                    "buildah", "npm", "pnpm", "yarn", "twine", "cargo", "pip", "kubectl", "oc", "heroku", "flyctl",
                    "fly", "vercel", "netlify", "wrangler", "firebase", "railway", "stripe", "terraform", "tofu",
                    "vault", "hf", "huggingface-cli", "hermes", "rclone", "ssh-keygen", "launchctl"})
_CLOUD_WORDS = frozenset({"login", "auth", "credential", "credentials", "credential-helper", "configure", "token",
                          "tokens", "print-access-token", "get-secret-value", "get-login-password",
                          "get-session-token", "secrets", "secret", "get-parameter", "get-parameters",
                          "export-secret-keys", "keychain", "getenv", "dumpstate", "--get-regexp", "--show-origin"})
# Programs whose own words name a script of the user's (npm run auth:test): the cloud words are not read in them.
_SCRIPT_RUNNERS = frozenset({"npm", "pnpm", "yarn"})
_SCRIPT_VERBS = frozenset({"run", "run-script", "exec", "test", "start", "build"})
# Programs that print the environment or what holds it, besides env: program -> the word that makes them do it.
_PRINTS = {"tmux": ("show-environment", "showenv", "show-env"), "systemctl": ("show-environment",),
           "launchctl": ("export", "print"), "heroku": ("config",), "vercel": ("env",), "netlify": ("env",),
           "docker": ("inspect",), "podman": ("inspect",), "compgen": ("-v", "-e"), "direnv": ("export",)}
_LAUNCHERS = frozenset({"sudo", "doas", "env", "command", "builtin", "nohup", "exec", "time", "nice", "ionice",
                        "stdbuf", "timeout", "eval", "setsid", "caffeinate", "xargs", "watch", "nocorrect",
                        "busybox", "toybox"})
_KEYWORDS = frozenset({"if", "then", "else", "elif", "fi", "while", "until", "do", "done", "for", "in", "case", "esac",
                       "function", "!", "{", "}"})
_REMOTE = frozenset({"ssh", "docker", "podman", "kubectl", "xargs", "watch", "su", "sudo", "doas", "nsenter", "chroot",
                     "sh", "bash", "zsh", "dash", "ksh", "fish", "eval", "exec", "timeout", "nohup", "script"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish"})
_RECURSIVE = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "ack", "find", "fd", "fdfind", "tar", "zip", "rsync",
                        "cp", "scp", "tree", "du", "locate", "mdfind"})
_SEARCHERS = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "ack", "fd", "fdfind"})   # their first word is a pattern
_INDEXED = frozenset({"locate", "mdfind"})   # they look in an index, not in the folder they run in
_CODE_PROGRAMS = frozenset({"jq", "yq", "awk", "gawk", "sed", "perl", "python", "python3", "node", "ruby", "php", "lua",
                            "osascript", "bc", "expr"})   # their words are code, not paths


# ---- what may become a draft: by construction -------------------------------------------------------------------------
# A list of secret shapes is never complete, so the first rule is not about shapes: only a tool whose arguments cannot
# carry what someone typed gives drafts. terminal, read_file and search_files are looked at by the rules below; any
# other tool (an MCP tool, the web tools) only when every argument is plain; a tool that carries free text or only
# moves a screen never does.
DRAFT_TOOLS = frozenset({"terminal", "read_file", "search_files"})
FREE_TEXT_TOOLS = frozenset({"send_message", "write_file", "patch", "text_to_speech", "image_generate", "vision_analyze",
                             "skill_manage", "cronjob_manage", "session_search", "browser_type", "browser_click",
                             "browser_press", "browser_snapshot", "browser_scroll", "browser_back", "browser_vision",
                             "browser_console", "browser_get_images"})
# A plain value is a number, a boolean, nothing, or a short string without a space that is ONE of: a word, an
# option-like name or identifier (get_state, sensor.office_co2, --all), a path, or a URL without user, password,
# query or fragment. Not plain: a sentence, a quoted phrase, anything with a symbol a password is made of, and a piece
# of 8 characters or more that mixes letters and digits (Passw0rdZ, a1b2c3d4e5).
PLAIN_CHARS = 200
_CALL_WORD = re.compile(r"-{0,2}[A-Za-z_][A-Za-z0-9_.:-]{0,63}")
_CALL_FILE = re.compile(r"(?:~|\.{1,2})?/?[\w.+@~-]+(?:/[\w.+@~-]+)*/?")
_CALL_URL = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[\w.+~%-]*)*")
_MIXED = re.compile(r"(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{8,}")
_STAMP = re.compile(r"[\dTtZz:.+-]+")   # a date, a time, a version: digits with a T or a Z, no password


def _mixed(text: str) -> bool:
    """A run of 8 or more letters and digits that has both: the shape of a password or a key."""
    return any(_MIXED.fullmatch(piece) and not _STAMP.fullmatch(piece) for piece in re.split(r"[^A-Za-z0-9]+", text))


def _call_value(value, depth: int = 0) -> bool:
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, float):
        return value == value and abs(value) != float("inf")
    if isinstance(value, str):
        if len(value) > PLAIN_CHARS or any(c.isspace() for c in value):
            return False
        if not (value == "" or _CALL_WORD.fullmatch(value) or _CALL_FILE.fullmatch(value)
                or _CALL_URL.fullmatch(value)):
            return False
        return not _mixed(value)
    if depth >= 3:
        return False
    if isinstance(value, (list, tuple)):
        return len(value) <= 20 and all(_call_value(each, depth + 1) for each in value)
    if isinstance(value, dict):
        return len(value) <= 20 and all(isinstance(k, str) and len(k) <= 64 and re.fullmatch(r"[\w.-]*", k)
                                        and _call_value(each, depth + 1) for k, each in value.items())
    return False


def plain_call(args) -> bool:
    """Every argument of the call is plain (see above): an MCP or other tool call with such arguments may become a
    draft; one that carries anything else is free text and never does."""
    return isinstance(args, dict) and _call_value(args)


def free_text(tool: str, args) -> bool:
    """The call carries text someone typed, or only moves a screen: never a draft."""
    if tool in DRAFT_TOOLS:
        return False
    if tool in FREE_TEXT_TOOLS or (tool.startswith("browser_") and tool != "browser_navigate"):
        return True
    return not plain_call(args)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for each in value.values():
            yield from _strings(each)
    elif isinstance(value, (list, tuple)):
        for each in value:
            yield from _strings(each)


def _plain_value(name: str, value: str, colon: bool = False) -> bool:
    """A value that is no secret whatever the name says: a variable or placeholder, a plain word like yes or off, a
    path, a number counted by a count's name (max_tokens=200), the shell's own PWD. After a colon (prose or YAML) the
    value needs a digit, a capital or a symbol to count as one."""
    if not value or value.startswith(("$", "{", "(", "<", "%")) or value.lower() in _PLAIN_VALUES:
        return True
    if name in ("PWD", "OLDPWD") or value.startswith(("/", "./", "../", "~/")):
        return True
    if _NUMBER.fullmatch(value) and _COUNT_NAME.search(name):
        return True
    return colon and not re.search(r"[\dA-Z=+/_@!#%^*-]", value)


def _named_secret(value) -> bool:
    """A dict (or a list of them) that holds a value under a name that says secret: {"password": "x"}."""
    if isinstance(value, dict):
        for name, each in value.items():
            if isinstance(each, str) and isinstance(name, str) and each.strip() and _SECRET_NAME.search(name) \
                    and not _plain_value(name, each.strip()):
                return True
            if _named_secret(each):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_named_secret(each) for each in value)
    return False


def _secret_text(text: str) -> bool:
    """A secret written in the text: NAME=value or "Name: value" whose name says secret and whose value has the shape
    of one (not a variable, a path, a number a count's name holds, a plain word), however the name is glued
    (GITHUB_TOKEN, PGPASSWORD, --api-key=, X-Api-Key:, Authorization:), also inside a URL's query."""
    for m in _PAIR.finditer(text.replace("://", " ")):
        name, sep, value = m.groups()
        if _SECRET_NAME.search(name) and not _plain_value(name, value, sep == ":" and name.isalpha()):
            return True
    return False


def _url_secret(text: str) -> bool:
    """A password or token inside a URL (user:password@, token@, a Telegram bot's token) or a key in its query."""
    for m in _URL_USER.finditer(text):
        password = m.group(1)
        if password is None or not _plain_value("password", password):
            return True
    for name, value in _URL_QUERY.findall(text):
        if _SECRET_NAME.search(name) and not _plain_value(name, value):
            return True
        if _QUERY_NAME.match(name) and (len(value) >= 16 or (len(value) >= 6 and re.search(r"\d", value))):
            return True
    return False


def _secret_flag(words: list) -> bool:
    """A long option whose name says secret, followed by a value that has the shape of one: --password hunter2,
    --api-key=abc; not --max-tokens 200, --tokenizer gpt2, --password-file ~/pw, --passthru."""
    for i, word in enumerate(words):
        if not word.startswith("--"):
            continue
        name, equals, glued = word[2:].partition("=")
        if not _SECRET_NAME.search(name) or re.search(r"(?:file|path|dir|stdin|prompt)$", name, re.I):
            continue
        value = glued if equals else (words[i + 1] if i + 1 < len(words) else "")
        if value and not value.startswith("-") and not _plain_value(name, value):
            return True
    return False


def _words(command: str) -> list:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _program(command: str) -> list:
    """The words of a command from its program on: `sudo`, `env` and VAR=x assignments before it are skipped."""
    words = _words(command)
    while words and (words[0] in ("sudo", "env", "command", "nohup", "exec") or _ASSIGN.match(words[0])):
        words = words[1:]
    return words


def _segments(command: str, depth: int = 0) -> list:
    """The simple commands of a line, each as its words. A pipe, `;`, `&&`, `||`, `&`, a parenthesis, a new line or a
    backtick ends one; what sits inside `$( )`, backticks, a `bash -c` string or any quoted word that holds shell
    syntax is a command of its own, found too."""
    text = command.replace("`", " ; ").replace("\n", " ; ")
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:  # an open quote: read it as loosely as possible rather than not at all
        tokens = [t for t in re.split(r"[\s;|&()<>\"']+", text) if t]
    out, current = [], []
    for token in tokens:
        if all(c in ";|&()<>" for c in token):
            if any(c in token for c in ";|()") or token in ("&", "&&"):
                if current:
                    out.append(current)
                    current = []
            continue
        current.append(token)
    if current:
        out.append(current)
    if depth < 4:
        for words in list(out):
            i, _ = _program_index(words)
            if i < len(words) and words[i].rsplit("/", 1)[-1] in ("jq", "yq"):
                continue  # a filter is no command: .[] | .name is not a pipeline of two
            for word in words:
                if _SYNTAX.search(word):
                    out.extend(_segments(word, depth + 1))
            if i < len(words) and words[i].rsplit("/", 1)[-1] in _SHELLS:
                for j, word in enumerate(words[i + 1:-1], i + 1):
                    if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", word):
                        out.extend(_segments(words[j + 1], depth + 1))
    return out


def _program_index(words: list) -> tuple:
    """(index of the real program, whether `env` was on the way): assignments, keywords, `sudo`, `env -i`, `nice -n 5`
    and the like are passed. The index is len(words) when nothing is left to run."""
    i, saw_env = 0, False
    while i < len(words):
        word = words[i]
        base = word.rsplit("/", 1)[-1]
        if _ASSIGN.match(word) or word in _KEYWORDS:
            i += 1
        elif base in _LAUNCHERS:
            saw_env = saw_env or base == "env"
            i += 1
            while i < len(words) and (words[i].startswith("-") or (
                    base in ("timeout", "nice", "ionice", "watch") and re.fullmatch(r"[\d.]+[smhd]?", words[i]))):
                takes = words[i] in ("-u", "-C", "-S", "-g", "-h", "-D", "--unset", "--chdir", "--user") \
                    and base in ("env", "sudo", "doas")
                i += 2 if takes else 1
        else:
            return i, saw_env
    return len(words), saw_env


def _dumps(words: list) -> bool:
    """The command prints the environment, the shell's variables or its history (env, set, export -p, declare -p,
    printenv, history, ps with the environment, jq over env, tmux show-environment...)."""
    i, saw_env = _program_index(words)
    if i >= len(words):
        return saw_env
    prog, args = words[i].rsplit("/", 1)[-1], words[i + 1:]
    if prog in ("printenv", "history", "fc"):
        return True
    if prog == "set":
        return not args
    if prog in ("export", "readonly"):
        return not args or args == ["-p"]
    if prog in ("declare", "typeset"):
        return all(a.startswith(("-", "+")) for a in args)
    if prog == "ps":
        return any(re.fullmatch(r"-[A-Za-z]*E[A-Za-z]*", a) or (
            not a.startswith("-") and re.fullmatch(r"[A-Za-z]+", a) and "e" in a) for a in args[:2])
    if prog == "jq":
        return any(re.search(r"\benv\b|\$ENV", a) for a in args)
    if prog in ("docker", "podman") and "compose" in args and "config" in args:
        # `config` prints the resolved file, environment values included; with --quiet (or a list of names) it prints none
        return not set(args) & {"--quiet", "-q", "--services", "--hash", "--images", "--profiles", "--volumes",
                                "--networks"}
    if prog in _PRINTS:
        return any(a in _PRINTS[prog] for a in args)
    if prog in ("kubectl", "oc") and "config" in args and "view" in args:
        return True
    return False


def _program_secret(prog: str, args: list) -> bool:
    if prog in SECRET_PROGRAMS:
        return True
    low = [a.lower() for a in args]
    if prog == "git":  # a credential helper, or the whole config (its urls can hold a password)
        return any(a.startswith("credential") for a in low) or (
            "config" in low and bool(set(low) & {"-l", "--list", "--get-regexp", "--show-origin"}))
    if prog == "rclone":  # its config file holds the keys of every remote
        return "config" in low or "authorize" in low
    if prog not in _CLOUD:
        return False
    if prog in _SCRIPT_RUNNERS and low and low[0] in _SCRIPT_VERBS:
        return any("credential" in a for a in low[:1])   # a script of the user's: its name is not a cloud word
    tokens = set(re.split(r"[:=/\s]+", " ".join(low))) | set(low)
    return bool(tokens & _CLOUD_WORDS) or any("credential" in a for a in low)


def _password_option(prog: str, args: list) -> bool:
    """A password typed after a short option, for the programs that take one that way (a short list, see
    _PASSWORD_AFTER): redis-cli -a PW, unzip -P PW, mysql -pPW, sshpass -pPW."""
    options = _PASSWORD_AFTER.get(prog, ())
    if prog in ("curl", "http", "wget") and any(
            a in ("-b", "--cookie") and "=" in args[n + 1] for n, a in enumerate(args[:-1])):
        return True    # a cookie given as name=value
    for i, word in enumerate(args):
        if not word.startswith("-") or word.startswith("--"):
            continue
        value = None
        for opt in options:
            if word == opt:
                value = args[i + 1] if i + 1 < len(args) else ""
            elif word.startswith(opt) and len(word) > len(opt) and len(opt) == 2:
                value = word[len(opt):]    # glued: -Ppw
        if value is None and prog in _PASSWORD_GLUED and word.startswith("-p") and len(word) > 2:
            value = word[2:]
        if value and not value.startswith(("-", "$", "{", "<", "%")):
            return True
    return False


# What the shape of a terminal command gives away, whatever the program (the second rule after the tool's own):
# a quoted word that holds a token of 8 or more letters and digits, an option that is made to carry a secret, a
# header that carries one, a secret assigned with -v, a SQL string with a literal next to a secret's name, and the
# files that hold keys and logins, by their name or their folder.
_QUOTED = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'([^']*)'")
_SECRET_OPTION = re.compile(r"(?:[a-z0-9]+[-_])*(?:pass|password|passwd|passphrase|token|secret|cookie|auth|keyfile|"
                            r"passfile|pwfile)|(?:[a-z0-9]+[-_])+(?:file|key|header)")
_HEADER = re.compile(r"(?:^|=)\s*(?:cookie|set-cookie|authorization|proxy-authorization|x-[\w-]*"
                     r"(?:key|token|auth|secret|session|cookie)[\w-]*)\s*:", re.I)
_VALUE_FLAGS = frozenset({"-v", "-e", "--assign", "--env", "--var", "--set", "--define"})
_BARE_SECRETS = frozenset({"key", "keys", "pw", "pin", "pass", "apikey"})
_SQL_WORD = re.compile(r"\b(?:select|insert|update|delete|replace|create|alter|set|grant|where|values)\b", re.I)
_SQL_SECRET = re.compile(r"pass|pwd|secret|token|key", re.I)
_SQL_LITERAL = re.compile(r"'[^']+'|\"[^\"]+\"")
_CREDENTIAL_NAME = re.compile(r"credential|identifiant|mots?[\s_.-]*de[\s_.-]*passe|passw(?:or)?d", re.I)
_KEY_SUFFIX = re.compile(r"(?:[-_.]pass|\.key|\.pem|\.p12|\.kdbx|\.ovpn)$", re.I)
_PRIVATE_ABSOLUTE = ("/etc/wireguard", "/etc/ssl/private", "/etc/letsencrypt")
_READERS = frozenset({"cat", "bat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "ag", "ack", "sed",
                      "awk", "gawk", "nl", "tac", "strings", "xxd", "od", "hexdump", "view", "vi", "vim", "nano", "zcat",
                      "source", "cp", "scp", "open", "base64", "sort", "uniq", "cut", "wc", "diff", "cmp", "file"})
_INTERPRETERS = frozenset({"python", "python3", "node", "ruby", "perl", "php", "bash", "sh", "zsh", "deno", "bun"})
_SCRIPT = re.compile(r".+\.(?:py|sh|js|mjs|rb|pl|php|ts)$", re.I)
_USERPASS_EXEMPT = frozenset({"date", "sort", "uniq", "ls", "ps", "cp", "mv", "rsync", "du", "top", "id", "who", "last",
                              "tar", "zip", "docker", "podman", "sudo", "su", "doas", "crontab", "touch", "stat", "df"})


def _secret_option(word: str) -> bool:
    """An option made to carry a secret, or a file that holds one: a name that ends in -file, -key, -pass, -password,
    -token, -secret, -cookie, -header or -auth (--password-file, --ssh-key, --proxy-header), glued with = or not.
    Its value is private whatever it is."""
    if not word.startswith("-") or word.startswith("---"):
        return False
    name = word.lstrip("-").split("=", 1)[0].lower()
    return bool(name) and name[0].isalpha() and _SECRET_OPTION.fullmatch(name) is not None


def _quoted_token(command: str) -> bool:
    """A quoted word that holds a token of 8 or more characters, mixing letters and digits, with no space (and no
    slash: a path or a URL is not a password): awk -v key="XyZ9-apikey-4455aabbcc"."""
    for m in _QUOTED.finditer(command):
        for token in (m.group(1) or m.group(2) or "").split():
            for part in {token, token.split("=", 1)[-1].strip("'\"")}:
                if "/" not in part and len(part) >= 8 and re.search(r"[A-Za-z]", part) and re.search(r"\d", part) \
                        and not _STAMP.fullmatch(part):
                    return True
    return False


def _shape_secret(words: list) -> bool:
    """The words of one simple command carry a secret by their shape (see above)."""
    for i, word in enumerate(words):
        if _secret_option(word) or _HEADER.search(word):
            return True
        if word in _VALUE_FLAGS and i + 1 < len(words):
            name, equals, value = words[i + 1].partition("=")
            if equals and (_SECRET_NAME.search(name) or name.lower() in _BARE_SECRETS) \
                    and not _plain_value(name, value):
                return True
        if (" " in word or "'" in word or '"' in word) and _SQL_WORD.search(word) and _SQL_SECRET.search(word) \
                and _SQL_LITERAL.search(word):
            return True
    return False


def _named_credential(word: str) -> bool:
    """A file name that says login or password (credentials, identifiants, mots de passe, password)."""
    last = word.rstrip("/").rsplit("/", 1)[-1]
    return bool(last) and (_pathlike(word) or " " in word) and _CREDENTIAL_NAME.search(last) is not None


def _homes() -> list:
    """The folders the dream stays out of, lower case, as written and with their links resolved: Hermes's own, and the
    private ones of the home folder (~/.ssh, ~/.aws, ~/.config/gh...). A folder that holds one of them holds a secret."""
    home = os.path.expanduser("~")
    key = (os.environ.get("HERMES_HOME"), home)
    if _HOMES.get("key") != key:
        out = []
        paths = [os.path.expanduser(e) for e in (key[0], "~/.hermes") if e] + [os.path.join(home, f) for f in PRIVATE_FOLDERS]
        for each in paths:
            path = os.path.normpath(each)
            for form in (path, os.path.realpath(path)):
                if form.lower() not in out:
                    out.append(form.lower())
        _HOMES.update(key=key, value=out)
    return _HOMES["value"]


_HOMES: dict = {}


def _expand(word: str) -> str:
    """The word with ~, $HOME and $HERMES_HOME written out."""
    text = word.strip("'\"")
    home = os.path.expanduser("~")
    hermes = os.environ.get("HERMES_HOME") or "~/.hermes"
    text = re.sub(r"\$\{HERMES_HOME(?::?[-=][^}]*)?\}|\$HERMES_HOME(?![A-Za-z0-9_])",
                  lambda _: os.path.expanduser(hermes), text)
    text = re.sub(r"\$\{HOME\}|\$HOME(?![A-Za-z0-9_])", lambda _: home, text)
    if text == "~" or text.startswith("~/"):
        text = home + text[1:]
    return text


def _private(word: str) -> bool:
    text = word.replace("$HOME", "~").rstrip("/")  # any case: the Mac's disk does not tell .Zshrc from .zshrc
    last = text.rsplit("/", 1)[-1].lower()
    low = os.path.normpath(text).lower() if text.startswith("/") else text.lower()
    return last in PRIVATE_NAMES or last.startswith(_PRIVATE_PREFIXES) or bool(
        PRIVATE_DIRS.search(text if "/" in text else "/" + text)) or bool(_KEY_SUFFIX.search(last)) or any(
        low == each or low.startswith(each + "/") for each in _PRIVATE_ABSOLUTE)


def _expansions(word: str, limit: int = 64) -> list:
    """The word with its {a,b} braces written out (at most `limit` forms)."""
    forms = [word]
    for _ in range(6):
        again, changed = [], False
        for form in forms:
            m = _BRACES.search(form)
            if m is None:
                again.append(form)
                continue
            changed = True
            again += [form[:m.start()] + alt + form[m.end():] for alt in m.group(1).split(",")]
        forms = again[:limit]
        if not changed:
            break
    return forms


def _pattern_private(word: str) -> bool:
    """A word that holds a glob, braces or a substitution, and could name a private file or folder: a last part that
    starts with a dot (.zsh*, .e*, .*rc), a folder that starts with a dot and is itself a pattern (~/.h*/x, ~/.$(x)/y),
    or a start that a private name fits (id_*, cred*). Judged by what the pattern could match, not by what it does
    match today: a doubt is a no."""
    substitution = bool(re.search(r"\$(?![A-Za-z_])|`|\$\{", word))   # $( ) cut by the reader leaves a lone $
    if (re.search(r"\s", word) and not substitution) or not (_GLOB.search(word) or substitution):
        return False
    for form in _expansions(word.replace("$HOME", "~")):
        parts = [p for p in form.split("/") if p]
        if not parts:
            continue
        *folders, last = parts
        open_part = lambda p: bool(_GLOB.search(p) or "$(" in p or "`" in p or "${" in p)  # noqa: E731
        if last.startswith(".") and last not in (".", ".."):
            return True
        if any(p.startswith(".") and p not in (".", "..") and open_part(p) for p in folders):
            return True
        if open_part(last) and last[0] not in "*?[{$`" and any(
                fnmatch.fnmatchcase(sample, last.lower()) for sample in _PRIVATE_SAMPLES):
            return True
        for folder in PRIVATE_FOLDERS:  # ~/.config/g*/x could be ~/.config/gh/x, ~/Library/Key*/x the keychains
            names = folder.lower().split("/")
            for k in range(len(parts) - len(names) + 1):
                held = parts[k:k + len(names)]
                if any(open_part(p) and p[0] not in "*?[{$`" for p in held) and all(
                        fnmatch.fnmatchcase(name, p.lower()) for name, p in zip(names, held)):
                    return True
    return False


def _pathlike(word: str) -> bool:
    return "/" in word or word.startswith(("~", ".")) or bool(_EXTENSION.search(word))


def _is_home(path: str, homes: list) -> bool:
    """Path is Hermes's home (or a private folder) or inside it: the dream has no business in the folder of Hermes's
    config and keys."""
    low = path.lower().rstrip("/")
    return any(low == h or low.startswith(h + "/") for h in homes)


def _holds_home(path: str, homes: list) -> bool:
    """Path is Hermes's home or a private folder, or a folder that holds one (a recursive search from there reaches its
    files)."""
    low = path.lower().rstrip("/") or "/"
    return any(h == low or h.startswith(low.rstrip("/") + "/") for h in homes)


def _bad_dir(path: str, homes: list) -> bool:
    return _private(path) or _is_home(os.path.normpath(path), homes)


def _starts(args: dict, cwd: str) -> tuple:
    """Where the call runs: (folders, loose). The folders are its own workdir, then the session's folder, written out
    (a relative workdir is read from the session's folder); `loose` holds a relative workdir that could not be placed,
    which is still judged by its name (.ssh, ../.ssh)."""
    base = _expand(cwd.strip()) if isinstance(cwd, str) and cwd.strip() else ""
    base = os.path.normpath(base) if base.startswith("/") else ""
    out, loose = [], []
    for each in (args.get("workdir"), args.get("cwd")):
        if isinstance(each, str) and each.strip():
            text = _expand(each.strip())
            if text.startswith("/"):
                found = os.path.normpath(text)
            else:
                loose.append(text)
                found = os.path.normpath(os.path.join(base, text)) if base else ""
            if found and found not in out:
                out.append(found)
    if base and base not in out:
        out.append(base)
    return out, loose


_DISK: dict | None = None   # what the disk answered during one spot(): one question per path; None outside it


def _disk(path: str, ask) -> bool:
    key = (path, ask)
    if _DISK is not None and key in _DISK:
        return _DISK[key]
    try:
        found = bool(ask(path))
    except OSError:
        found = True    # a path that cannot be looked at is a doubt
    if _DISK is not None:
        _DISK[key] = found
    return found


def _bad_path(word: str, dirs: list, homes: list, blocked, recursive: bool = False, glob: bool = False,
              script: bool = False) -> bool:
    """A word that is (or, from one of the folders, would be) a private file or folder: secrets files and folders,
    Hermes's home, and anything Hermes's own tools refuse. A word with no slash and no extension (token, password) is
    a name only where such a file exists; a doubt about a path is a no."""
    if not word or "://" in word:
        return False
    parts = {word, word.split("=", 1)[-1]}
    if glob and "{" in word and "," in word:
        parts |= {form for each in list(parts) for form in _expansions(each)}   # ~/.{hermes,x}/c: each way it can read
    for part in parts:
        if not part or part.startswith("-"):
            continue
        if glob and _pattern_private(part):
            return True
        text = _expand(part)
        pathy = _pathlike(part)
        found = [text]
        if text and not text.startswith(("/", "~")):
            found += [os.path.normpath(os.path.join(d, text)) for d in dirs]
        for each in found:
            if not each:
                continue
            absolute = each.startswith("/")
            if absolute:
                each = os.path.normpath(each)
            if _private(each) or _private(part):
                return True
            named = pathy or (absolute and each != text and _disk(each, os.path.lexists))
            # a script the user runs is named as it likes (count_tokens.py): the name rule is for files that are read
            if script and _SCRIPT.fullmatch(part):
                pass
            elif (runner.secret_name(each) if named else runner.SECRET_FILE.fullmatch(each)):
                return True
            if absolute and (_is_home(each, homes) or (recursive and _holds_home(each, homes))):
                return True
            if blocked is not None and named and (absolute or pathy):
                try:
                    if blocked(each):
                        return True
                except Exception:
                    return True
    return False


def _roots(prog: str, args: list, dirs: list) -> tuple:
    """(folders, named): the folders a recursive program looks in as the command writes them, resolved; `named` says
    whether it writes any. A word is a folder when it has a slash, starts with ~ or a dot, holds a glob (its folder
    part counts: src/*.py is src), or is a folder under the one the command runs in. A grep's first word is its
    pattern and a find's folders are the words before its first option."""
    words = [a for a in args]
    if prog == "find":
        end = next((n for n, a in enumerate(words) if a.startswith(("-", "(", "!"))), len(words))
        words = words[:end]
    else:
        words = [a for a in words if not a.startswith("-")]
        if prog in _SEARCHERS and words:
            words = words[1:]
    out, named = [], False
    for word in words:
        text = _expand(word)
        if not re.search(r"\s", text) and _GLOB.search(text):
            head = text.split("/")
            base = "/".join(head[:next((n for n, p in enumerate(head) if _GLOB.search(p)), 0)]) or (
                "/" if text.startswith("/") else ".")
        elif "/" in text or text.startswith(("~", ".")):
            base = text
        elif dirs and _disk(os.path.join(dirs[0], text), os.path.isdir):
            base = text
        else:
            continue
        named = True
        if base.startswith("/"):
            out.append(os.path.normpath(base))
        else:
            out += [os.path.normpath(os.path.join(d, base)) for d in dirs]
    return out, named


def _segment_secret(words: list, dirs: list, homes: list, blocked) -> bool:
    if _dumps(words):
        return True
    i, _ = _program_index(words)
    prog = words[i].rsplit("/", 1)[-1] if i < len(words) else ""
    args = words[i + 1:] if i < len(words) else []
    if _program_secret(prog, args) or _password_option(prog, args) or _secret_flag(args) or _shape_secret(words):
        return True
    if prog not in _USERPASS_EXEMPT and _USERPASS.search(" ".join(words)):
        return True
    if prog in _READERS and any(_named_credential(a) for a in args if not a.startswith("-")):
        return True
    if prog in ("cd", "pushd"):
        target = next((a for a in args if not a.startswith("-")), None)
        if target is not None:
            text = _expand(target)
            for each in ([text] if text.startswith("/") else [os.path.join(d, text) for d in dirs]):
                if each.startswith("/") and os.path.normpath(each) not in dirs:
                    dirs.append(os.path.normpath(each))
    if words[:i] and words[0].rsplit("/", 1)[-1] == _SUDO and any(w in ("-S", "--stdin") for w in words[1:i]):
        return True  # the -S option reads a password from what is piped in
    recursive = prog in _RECURSIVE
    skip = set()   # words that are code or a pattern, not a path: no glob reading for them
    if prog in _CODE_PROGRAMS:
        skip = set(range(len(words)))
    elif prog in _SEARCHERS:
        first = next((n for n in range(i + 1, len(words)) if not words[n].startswith("-")), None)
        skip = {first} if first is not None else set()
    runs = None   # the script an interpreter is given: python3 scripts/x.py
    if prog.rstrip("0123456789.") in _INTERPRETERS:
        runs = next((n for n in range(i + 1, len(words)) if not words[n].startswith("-")), None)
    if any(_bad_path(w, dirs, homes, blocked, recursive, glob=n not in skip, script=n == runs)
           for n, w in enumerate(words)):
        return True
    if recursive and prog not in _INDEXED:
        roots, named = _roots(prog, args, dirs)
        if any(_holds_home(r, homes) for r in roots):
            return True
        if not named and any(_holds_home(d, homes) for d in dirs):
            return True  # a search with no folder looks in the one it runs in
    if prog in _REMOTE and any(_dumps(words[j:]) for j in range(1, len(words))):
        return True
    return False


def _touches_secrets(tool: str, args: dict, command: str, blocked, cwd: str = "") -> bool:
    homes = _homes()
    dirs, loose = _starts(args, cwd)
    if any(_bad_dir(d, homes) for d in dirs) or any(_private(each) for each in loose):
        return True
    if any(_url_secret(t) for t in _strings(args)):   # a password or token in a URL, in any tool's arguments
        return True
    if tool == "terminal":
        if _ENVIRONMENT.search(command) or _quoted_token(command):
            return True
        scripts = set()
        for words in _segments(command):
            if _segment_secret(words, dirs, homes, blocked):
                return True
            i, _ = _program_index(words)
            if i < len(words) and words[i].rsplit("/", 1)[-1].rstrip("0123456789.") in _INTERPRETERS:
                scripts.update(w for w in words[i + 1:i + 3] if not w.startswith("-") and _SCRIPT.fullmatch(w))
        # the same line read the crudest way, so that a form the reader mishandles still shows its words
        return any(_bad_path(piece, dirs, homes, blocked, script=piece in scripts)
                   for piece in _PIECES.split(command))
    paths = [args.get(name) for name in ("path", "file_path", "file", "filename")]
    if any(isinstance(p, str) and (_named_credential(p) or _KEY_SUFFIX.search(p.rstrip("/").rsplit("/", 1)[-1]))
           for p in paths):   # a file that says login or password, read as a file
        return True
    for text in _strings(args):
        if len(text) <= 2000 and any(_bad_path(piece, dirs, homes, blocked, tool == "search_files", True)
                                     for piece in {text, text.strip()} if "\n" not in piece):
            return True
    if tool == "search_files":
        named = args.get("path") if isinstance(args.get("path"), str) and args.get("path").strip() else ""
        roots = _roots("find", [named], dirs)[0] if named else dirs
        if any(_holds_home(r, homes) for r in roots):
            return True
    return False


# ---- what is no habit: a command that never ends, a command that deletes --------------------------------------------
_WATCHERS = frozenset({"watch", "btop", "atop", "iotop", "iftop", "nethogs", "glances", "nload", "fswatch",
                       "inotifywait", "nodemon", "multitail", "uvicorn", "gunicorn", "hypercorn", "ng" "rok", "livereload",
                       "http-server", "live-server", "serve"})
_SERVERS = {"flask": ("run",), "rails": ("s", "server"), "jekyll": ("serve", "s"), "hugo": ("server",),
            "mkdocs": ("serve",), "next": ("dev", "start"), "vite": ("dev", "serve", "preview"), "nuxt": ("dev",),
            "ng": ("serve",), "caddy": ("run",), "jupyter": ("notebook", "lab"), "npx": ("serve", "vite", "http-server")}
_SERVING_MODULES = frozenset({"http.server", "SimpleHTTPServer", "uvicorn", "gunicorn", "flask", "streamlit", "jupyter"})
_SCRIPT_SERVERS = frozenset({"dev", "start", "serve", "watch", "develop", "server"})
_LOGS = frozenset({"logs", "events", "attach"})
_FOLLOWERS = frozenset({"docker", "podman", "kubectl", "oc", "docker-compose", "nerdctl", "stern", "heroku", "fly",
                        "flyctl", "gcloud", "az", "k3s"})


def _endless(words: list, background: bool) -> bool:
    """The command never ends by itself: it follows a log (-f, --follow), watches, or serves in the foreground. One
    sent to the background does end (its call returns at once)."""
    i, _ = _program_index(words)
    if background or i >= len(words):
        return False
    prog, args = words[i].rsplit("/", 1)[-1], words[i + 1:]
    base = prog.rstrip("0123456789.")
    flags = set(args)
    if any(w.rsplit("/", 1)[-1] == "watch" for w in words[:i]):    # watch -n 5 df: the launcher repeats it forever
        return True
    if any(a == "--follow" or a.startswith("--follow=") or a == "--watch" for a in args):
        return True
    if base == "python":
        pairs = list(zip(args, args[1:]))
        return any(a == "-m" and b in _SERVING_MODULES for a, b in pairs) or (
            "runserver" in args and any(a.endswith("manage.py") for a in args))
    if prog in ("top", "htop"):
        return not flags & {"-n", "-l", "-b"} or flags == {"-b"}
    if prog == "tcpdump":
        return "-c" not in flags
    if prog == "tail":
        return any(re.fullmatch(r"-[A-Za-z0-9]*[fF][A-Za-z0-9]*", a) for a in args)
    if prog in ("journalctl", "dmesg"):
        return bool(flags & {"-f", "-w", "-W"})
    if prog == "ping":
        return not any(a in ("-c", "-w", "-W") or a.startswith("-c") for a in args)
    if prog == "sleep":
        return "infinity" in flags
    if prog == "nc":
        return bool(flags & {"-l", "-lk", "-kl"})
    if prog == "ssh":
        return "-N" in flags
    if prog == "php":
        return "-S" in flags
    if prog in ("npm", "pnpm", "yarn", "bun"):
        script = [a for a in args if not a.startswith("-")]
        script = script[1:] if script and script[0] in ("run", "run-script", "exec") else script
        return bool(script) and script[0] in _SCRIPT_SERVERS
    if prog in _FOLLOWERS:
        if flags & _LOGS and flags & {"-f", "-F"}:
            return True
        if "get" in flags and flags & {"-w"}:
            return True
        if prog in ("docker", "podman", "docker-compose") and "events" in flags:
            return True
        return prog in ("docker", "podman", "docker-compose") and "up" in flags and not flags & {"-d", "--detach"}
    if prog in _SERVERS:
        return bool(args) and args[0] in _SERVERS[prog]
    return prog in _WATCHERS


def _empties(command: str) -> bool:
    """A redirection that empties a file: a line that starts with `>`, or that only prints nothing into one."""
    return bool(re.search(r"(?:^|[;&|({\n])\s*(?::|true|echo(?:\s+-n)?(?:\s+(?:\"\"|''))?|printf(?:\s+(?:\"\"|''))?|"
                          r"cat\s+/dev/null|<\s*/dev/null)?\s*>(?!>)\s*(?!/dev/null)[^\s>&]", command))


_REMOVE_WORDS = frozenset({"remove", "uninstall", "purge", "autoremove", "delete", "del", "prune", "drop", "clean"})


def _deletes_more(command: str) -> bool:
    """A command that deletes in a way graph's list of words misses: docker/podman prune, rsync --delete, git clean,
    git branch -D, git stash drop, truncate, a package uninstall, kubectl delete, a redirection that empties a file."""
    if _empties(command):
        return True
    for words in _segments(command):
        i, _ = _program_index(words)
        if i >= len(words):
            continue
        prog, args = words[i].rsplit("/", 1)[-1], words[i + 1:]
        if prog in ("truncate", "srm", "trash", "rimraf", "mk" "fs", "wipe" "fs", "fdisk", "parted") or prog.startswith("mk" "fs."):
            return True
        if any(a.startswith("--delete") or a in ("--remove-source-files", "--remove-files", "--purge", "-delete")
               for a in args):
            return True
        names = [a for a in args if not a.startswith("-")]
        if prog == "git":
            # -C PATH and -c K=V are the program's, not a sub-command
            rest, skip = [], False
            for a in args:
                if skip:
                    skip = False
                elif a in ("-C", "-c", "--git-dir", "--work-tree"):
                    skip = True
                elif not a.startswith("-"):
                    rest.append(a)
            sub = rest[0] if rest else ""
            flags = set(args)
            if sub in ("clean", "gc", "prune", "reflog") and sub != "reflog":
                return True
            if sub in ("branch", "tag") and flags & {"-d", "-D", "--delete"}:
                return True
            if sub == "stash" and len(rest) > 1 and rest[1] in ("drop", "clear"):
                return True
            if sub == "reset" and "--hard" in flags:
                return True
            if sub in ("worktree", "remote", "submodule") and len(rest) > 1 and rest[1] in _REMOVE_WORDS | {"rm"}:
                return True
        elif prog in ("docker", "podman", "docker-compose", "nerdctl", "kubectl", "oc", "helm", "terraform", "tofu"):
            if set(names) & (_REMOVE_WORDS | {"rm", "rmi", "kill", "destroy", "down"}) and not (
                    prog in ("docker", "podman") and "logs" in names):
                return True
        elif prog in ("apt", "apt-get", "dpkg", "yum", "dnf", "brew", "pip", "pip3", "npm", "pnpm", "yarn", "cargo",
                      "gem", "snap", "flatpak", "pacman", "apk", "port", "pipx", "conda", "mamba", "uv"):
            if names and names[0] in _REMOVE_WORDS | {"rm", "-R", "-Rs"}:
                return True
    return False


def excluded(tool: str, args: dict, blocked: Callable[[str], str] | None = None, cwd: str = "") -> str:
    """Why an action is never a candidate; "" when it may be one. `blocked` says why a path is private ("" if not);
    `cwd` is the folder of the session it was seen in (relative names are read from there). When in doubt: excluded."""
    args = args if isinstance(args, dict) else {}
    if tool in OWN:
        return "own"
    if tool in BOOKKEEPING:
        return "bookkeeping"
    if tool in MOMENT:
        return "moment"
    if tool in CODE:
        return "code"
    if free_text(tool, args):   # carries what someone typed, or only moves a screen: no draft, whatever it says
        return "free text"
    command = args.get("command") if tool == "terminal" and isinstance(args.get("command"), str) else ""
    lines = [each for each in command.splitlines() if each.strip()]
    if lines and all(NOISE.fullmatch(command_key(each)) for each in lines):   # noise only when every line is
        return "noise"
    texts = list(_strings(args))
    if any(p.search(t) for t in texts for p in KEYLIKE) or any(
            runner.SECRET_WORD.search(m.group(1)) for t in texts for m in _DOLLAR.finditer(t)) or any(
            _secret_text(t) for t in texts) or _named_secret(args):
        return "key"
    if _touches_secrets(tool, args, command, blocked, cwd):
        return "secrets"
    if command and (graph._deletes(command) or _deletes_more(command)):
        return "deletes"
    if command:
        sent_away = args.get("background") is True or re.search(r"[^&]&\s*$", command) is not None
        if any(_endless(w, sent_away) for w in _segments(command)):
            return "endless"
    if command:
        words = _program(command)
        if len(words) >= 2 and words[0].rsplit("/", 1)[-1] == "hermes" and words[1] in ("habits", "config"):
            return "settings"
    if key_of(tool, args) is None:
        return "long"
    return ""


# Programs that reach the network or another machine, besides the runner's own list.
NETWORK = tuple(dict.fromkeys(tuple(runner.OUTSIDE) + (
    "curl", "ssh", "hc", "wget", "nc", "ncat", "netcat", "telnet", "scp", "sftp", "rsync", "rclone", "ftp", "mosh",
    "socat", "nmap", "ping", "ping6", "traceroute", "mtr", "dig", "nslookup", "host", "whois", "lynx", "w3m")))


def is_local(command) -> bool:
    """The command stays on this machine: it does not reach the network or another machine. A line that cannot be
    read, a substitution or an ssh hop that does, and a /dev/tcp path, are not local."""
    if not isinstance(command, str) or not command.strip():
        return False
    if re.search(r"/dev/(?:tcp|udp)/", command, re.I):
        return False
    for words in _segments(command):  # the program behind a wrapper such as nice or xargs
        i, _ = _program_index(words)
        if i < len(words) and words[i].rsplit("/", 1)[-1] in NETWORK:
            return False
    return not any(shell.uses(command, name) for name in NETWORK)


def local_read(tool: str, args: dict, folder: str | None = None) -> bool:
    """An action the dream may really run beside Hermes: a terminal reader that stays on this machine, read_file,
    search_files. Anything that reaches the network or another machine is counted like an act. `folder` is where the
    draft would run ("" when the history shows none: the call has no workdir and the session no folder): a command
    that depends on the folder it starts in is then no read, because it would run where nobody knows (it is only
    counted, and waits for a yes like any act). None: the caller does not know, as before."""
    if tool in LOCAL_TOOLS:
        return True
    command = args.get("command") if isinstance(args, dict) else None
    if not (tool == "terminal" and isinstance(command, str) and bool(command.strip())
            and shell.reads_only(command) and is_local(command)):
        return False
    if folder == "" and not (isinstance(args.get("workdir"), str) and args["workdir"].strip()):
        from .practice import _depends_on_folder   # lazy: practice imports this module
        return not _depends_on_folder(command)
    return True


# ---- spotting and the moments -------------------------------------------------------------------------------------

def _share(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


class Left(dict):
    """`spot`'s count by reason (an ordinary dict to compare and to read), plus `turns`: the distinct turns left out,
    which a turn left out for two reasons counts once."""

    def __init__(self, by_reason, turns=()):
        super().__init__(by_reason)
        self.turns = set(turns)


def _folder(call: Call, session_cwd: str) -> str:
    """The folder a call ran in, as far as the history says: its own workdir (a relative one read from the session's
    folder), else the session's; "" when neither is known."""
    own = call.args.get("workdir") if call.tool == "terminal" else None
    if isinstance(own, str) and own.strip():
        text = _expand(own.strip())
        if text.startswith("/") or not session_cwd:
            return os.path.normpath(text) if text.startswith("/") else text
        return os.path.normpath(os.path.join(_expand(session_cwd), text))
    return session_cwd


def spot(turns: list, *, blocked=None, min_turns: int = MIN_TURNS, min_days: int = MIN_DAYS,
         now: float | None = None) -> tuple:
    """(spotted, left, near). `left` counts, by reason, the turns that held a gesture left out on purpose. An action
    whose last sighting is older than FRESH_DAYS before `now` (default: now) stopped being a habit: it is neither
    spotted nor near."""
    global _DISK
    _DISK = {}
    try:
        return _spot(turns, blocked, min_turns, min_days, time.time() if now is None else now)
    finally:
        _DISK = None


def _without_folder(action: Action) -> Action:
    """The action with no workdir of its own: where its turns do not agree on a folder it runs in the folder Hermes is
    in when it is called (the card says so), never in the folder of one of the forms."""
    if action.tool != "terminal" or "workdir" not in action.args:
        return action
    args = {k: v for k, v in action.args.items() if k != "workdir"}
    return Action(action.key, action.tool, args, raw_of(action.tool, args))


def _spot(turns: list, blocked, min_turns: int, min_days: int, now: float) -> tuple:
    left: Counter = Counter()
    left_turns: set = set()  # the turns left out for any reason: a turn left out for two reasons is one turn
    notes: dict = {}
    whys: dict = {}  # (raw form, folder) -> why it is left out: one answer per distinct call, asked once
    if blocked is not None:
        asked: dict = {}  # and one answer per path: the disk is asked once for it in this dream
        ask = blocked

        def blocked(path, _ask=ask, _asked=asked):
            if path not in _asked:
                _asked[path] = _ask(path)
            return _asked[path]
    for turn in turns:
        gestures = [c for c in turn.calls if c.tool not in BOOKKEEPING]
        seen_here, left_here = set(), set()
        for i, c in enumerate(gestures):
            if c.failed:
                left_here.add("failed")
                continue
            raw = raw_of(c.tool, c.args)
            why = whys.get((raw, turn.cwd))
            if why is None:
                why = whys[(raw, turn.cwd)] = excluded(c.tool, c.args, blocked, turn.cwd)
            if why:
                if why != "moment":
                    left_here.add(why)
                continue
            key = key_of(c.tool, c.args)
            skill = None
            if i > 0 and gestures[i - 1].tool == "skill_view" and isinstance(gestures[i - 1].args.get("name"), str):
                skill = gestures[i - 1].args["name"]
            note = notes.setdefault(key, {"tool": c.tool, "turns": {}, "forms": Counter(), "recent": {}, "args": {},
                                          "form_turns": {}})
            if key not in seen_here:
                seen_here.add(key)
                note["turns"][(turn.session, turn.index)] = {
                    "at": turn.at, "day": turn.day, "cwd": _folder(c, turn.cwd), "scheduled": turn.scheduled,
                    "skill": skill, "first": turn.first and i == 0, "last": i == len(gestures) - 1,
                    # the turn holds this call and nothing else (a skill load aside): first and last say nothing
                    "alone": sum(1 for g in gestures if g.tool not in MOMENT) <= 1}
            if (key, raw) not in seen_here:
                seen_here.add((key, raw))
                note["form_turns"].setdefault(raw, {})[(turn.session, turn.index)] = (turn.at, turn.day)
                note["forms"][raw] += 1
                note["recent"][raw] = max(note["recent"].get(raw, 0), turn.at)
                note["args"][raw] = c.args
        left.update(left_here)
        if left_here:
            left_turns.add((turn.session, turn.index))
    spotted, near = [], []
    for key, note in notes.items():
        marks = list(note["turns"].values())
        n, d = len(marks), len({m["day"] for m in marks})
        best = max(note["forms"], key=lambda f: (note["forms"][f], note["recent"][f]))
        action = Action(key, note["tool"], note["args"][best], best)
        cwds = Counter(m["cwd"] for m in marks)
        folder, in_folder = cwds.most_common(1)[0]
        kept = folder if _share(in_folder, n) >= 0.8 and folder else ""
        if kept:   # the folder is kept: the draft counts what came back there
            used = [m for m in marks if m["cwd"] == kept]
        else:      # no folder holds 80 % of the turns: the draft has none, it runs where Hermes is when it is called
            used = marks
            action = _without_folder(action)
        n, d = len(used), len({m["day"] for m in used})
        if max(m["at"] for m in marks) < now - FRESH_DAYS * 86400:
            continue    # last seen long ago: it stopped being a habit
        if n >= min_turns and d >= min_days:
            skills = Counter(m["skill"] for m in used if m["skill"])
            skill = None
            if skills:
                name, count = skills.most_common(1)[0]
                skill = [name, _share(count, n)]
            order = sorted(used, key=lambda m: m["at"])
            # a share is of the turns counted (never above 100 %); first and last only count in a turn that holds
            # other calls: in a turn of one call they are true of every gesture and say nothing
            spotted.append(Spotted(
                action, n, d, order[0]["at"], order[-1]["at"],
                {"first": _share(sum(m["first"] and not m["alone"] for m in used), n),
                 "last": _share(sum(m["last"] and not m["alone"] for m in used), n),
                 "skill": skill, "scheduled": _share(sum(m["scheduled"] for m in used), n)},
                dict(note["forms"]), kept, frozenset(note["turns"]),
                tuple((m["at"], m["day"]) for m in order)))
        elif n >= 2:
            near.append((d, n, max(m["at"] for m in used), Near(action, n, d)))
    spotted.sort(key=lambda s: (s.days, s.turns, s.last_seen), reverse=True)
    _label_apart(spotted)
    near.sort(key=lambda t: t[:3], reverse=True)
    return spotted, Left(left, left_turns), [t[3] for t in near[:NEAR]]


# ---- growing ------------------------------------------------------------------------------------------------------

def group_key(steps: list) -> str:
    """The key of a draft: the action's own key, or `grow:` and the sorted keys of a group's parts."""
    if len(steps) == 1:
        return steps[0].action.key
    return "grow:" + "+".join(sorted(s.action.key for s in steps))


def group(spotted: list, turns: list, *, share: float = TOGETHER, reflexes: frozenset = frozenset(),
          frozen: frozenset = frozenset()) -> list:
    """Spotted actions that come together become one group, in their usual order; the others come back alone."""
    if not GROW:
        return [[s] for s in spotted]
    order = {id(s): i for i, s in enumerate(spotted)}
    days = {(t.session, t.index): t.day for t in turns}
    first_at: dict = {}   # key -> {turn id -> position of the key's first call}
    names = {n: f"reflex:{n}" for n in reflexes}
    wanted = {s.action.key for s in spotted} | set(names.values())
    for t in turns:
        for c in t.calls:
            if c.failed:
                continue
            key = reflex_call(c.tool, c.args) or key_of(c.tool, c.args)
            if key in wanted:
                first_at.setdefault(key, {}).setdefault((t.session, t.index), c.order)
    items = [s for s in spotted if s.action.key not in frozen]
    for name, key in names.items():
        where = frozenset(first_at.get(key, {}))
        if where:
            action = Action(key, "habit_run", {"name": name}, raw_of("habit_run", {"name": name}))
            items.append(Spotted(action, len(where), len({days[w] for w in where if w in days}), 0.0, 0.0, {}, {},
                                 "", where, ()))
    items.sort(key=lambda s: -s.turns)

    def edge(a, b):
        both = a.where & b.where
        return (len(both) >= 3 and len(both) >= share * len(a.where) - 1e-9
                and len(both) >= share * len(b.where) - 1e-9 and len({days.get(w) for w in both}) >= 2)

    used, groups = set(), []
    for seed in items:
        if id(seed) in used:
            continue
        members = [seed]
        used.add(id(seed))
        for other in items:
            if id(other) in used or len(members) >= GROW_STEPS:
                continue
            if all(edge(m, other) for m in members):
                members.append(other)
                used.add(id(other))
        groups.append(members)
    out = []
    for members in groups:
        if len(members) > 1:
            common = frozenset.intersection(*(m.where for m in members))
            usual = Counter(tuple(m.action.key for m in sorted(members, key=lambda m: first_at.get(
                m.action.key, {}).get(w, 0))) for w in common)
            ranked = usual.most_common(1)[0][0] if usual else tuple(m.action.key for m in members)
            members = sorted(members, key=lambda m: ranked.index(m.action.key))
        if len(members) == 1 and members[0].action.key.startswith("reflex:"):
            continue  # a saved reflex alone is no draft; two that come together are one
        out.append(members)
    out.extend([s] for s in spotted if s.action.key in frozen)
    out.sort(key=lambda g: min(order.get(id(m), len(order)) for m in g))
    return out


# ---- proposing ----------------------------------------------------------------------------------------------------

_GUARDS = frozenset({"--dry-run", "-n", "--check", "--simulate", "--force", "-f", "--hard", "--no-verify",
                     "--force-with-lease", "--delete", "--prune", "-rf", "-fr", "--yes", "-y"})


def _valued(words: list) -> frozenset:
    """The options of a command that carry their value (--name=value, -XDELETE, -p2222): two forms that differ in one
    are another gesture (another target, another verb), not another form of the same."""
    return frozenset(w for w in words[1:] if (w.startswith("--") and "=" in w)
                     or re.fullmatch(r"-[A-Z][A-Za-z0-9]+|-[a-z]\d+", w) is not None)


def _guarded(words: list) -> frozenset:
    """The rehearsing or forcing options of a command (--dry-run, -n, --force, --hard): `npm publish --dry-run` is not
    `npm publish`, nor `git push` `git push --force`."""
    return frozenset(w for w in words[1:] if w in _GUARDS)


def siblings(a: Action, b: Action) -> bool:
    """The same gesture in another form: same tool and, for a terminal, the same program and the same words apart from
    options and numbers (`ls` and `ls -la`, `git log -5` and `git log -20`); for other tools the same path or url.
    Never two equal keys."""
    if a.key == b.key or a.tool != b.tool:
        return False
    if a.tool == "terminal":
        wa, wb = _program(str(a.args.get("command", ""))), _program(str(b.args.get("command", "")))
        if not wa or not wb:
            return False
        # the same program and the same words once options and numbers are put aside: a new target, a new verb or
        # a new word makes another gesture, not another form of this one
        plain = lambda w: [x for x in w[1:] if not x.startswith("-") and not x.isdigit()]  # noqa: E731
        return (wa[0].rsplit("/", 1)[-1] == wb[0].rsplit("/", 1)[-1] and plain(wa) == plain(wb)
                and _valued(wa) == _valued(wb) and _guarded(wa) == _guarded(wb))
    for name in ("path", "file_path", "url"):
        if isinstance(a.args.get(name), str) and a.args.get(name) == b.args.get(name):
            return True
    return False


def may_draft(s: Spotted, row: dict | None, now: float) -> bool:
    """A draft may be made when there is no row, or when a no has run out and the action came back since it: at least
    3 turns on 2 days after the no."""
    if row is None:
        return True
    if row.get("state") != "no" or (row.get("no_until") or 0) > now:
        return False
    since = row.get("since") or 0
    after = [(at, day) for at, day in s.stamps if at > since]
    return len(after) >= MIN_TURNS and len({day for _, day in after}) >= MIN_DAYS


def propose(spotted: list, habits: list, now: float) -> list:
    """Change and rest for the saved learned reflexes. `habits` holds one dict per reflex: name, version, status, key,
    raw, last_seen (history or runs, epoch)."""
    out = []
    for h in habits:
        if h.get("status") == "retired":
            continue
        quiet = (now - (h.get("last_seen") or 0)) / 86400
        if h.get("status") == "active" and quiet >= CHANGE_QUIET_DAYS:
            mine = Action(h.get("key", ""), *_split_raw(h.get("raw")))
            near = [s for s in spotted if s.action.key != h.get("key") and siblings(mine, s.action)]
            if near:
                s = max(near, key=lambda s: (s.days, s.turns))
                out.append(Proposal("change", h["name"], {"sibling": s.action.key, "raw": s.action.raw,
                                                         "turns": s.turns, "days": s.days, "quiet": int(quiet)}))
                continue
        if quiet >= REST_QUIET_DAYS and not any(
                s.action.key == h.get("key") and (now - s.last_seen) / 86400 < REST_QUIET_DAYS for s in spotted):
            out.append(Proposal("rest", h["name"], {"why": "quiet", "quiet": int(quiet)}))
    return out


def _split_raw(raw):
    data = _json(raw) if isinstance(raw, str) else None
    if isinstance(data, dict) and isinstance(data.get("tool"), str):
        return data["tool"], data.get("args") if isinstance(data.get("args"), dict) else {}, raw or ""
    return "", {}, raw or ""


# ---- the reflex a draft becomes -----------------------------------------------------------------------------------

def _plain(text: str) -> str:
    """A text safe to put inside a step: no brace, no dollar, no control character."""
    return re.sub(r"[{}$\x00-\x1f]", "", str(text)).strip()


_NAME_WORD = re.compile(r"[A-Za-z][A-Za-z_-]{0,23}")
_NAME_FLAGS = ("-m",)   # python -m pytest: the word after these is a program's name, not a value


def _bit(word: str) -> str:
    """One word of a command, short enough for a name: the last part of a path without its extension; "" when it is
    an option, a url, a number, private, holds a digit or a capital in the middle, or is not a plain name. A name
    is made of a program and its sub-command or file words: nothing a user typed as a value gets in."""
    word = word.strip("'\"")
    if not word or word.startswith("-") or "://" in word or "=" in word:
        return ""
    word = word.split()[0] if word.split() else ""
    base = os.path.splitext(word.rstrip("/").rsplit("/", 1)[-1])[0] if ("/" in word or word.startswith("~")) else word
    if not _NAME_WORD.fullmatch(base) or base in ("null", "zero", "stdin", "stdout", "stderr", "tty") or re.search(r"[a-z][A-Z]", base) or _private(word) or \
            runner.secret_name(word) or _SECRET_NAME.search(base) or any(p.search(word) for p in KEYLIKE):
        return ""
    return base


def _folderish(word: str) -> bool:
    return "/" in word or word.startswith(("~", "."))


_NO_GIST = frozenset({"echo", "printf", "say", "print"})   # the words after these are data, not a sub-command
# Options whose value is a folder the command works in, not a sub-command or a name: git -C proj log is `git log`.
_FOLDER_OPTIONS = frozenset({"-C", "--git-dir", "--work-tree", "--directory", "--chdir", "--cwd", "--prefix"})


def _fit(words: list, limit: int) -> str:
    """The words, joined, as many whole words as fit in `limit` characters. The first word always stays whole, however
    long it is: a name never ends in the middle of a word."""
    out: list = []
    for word in words:
        if out and len(" ".join(out + [word])) > limit:
            break
        out.append(word)
    return " ".join(out)


def _gist(action: Action, more: int = 2) -> list:
    """The words that tell this action from its neighbours: for a terminal the program, then up to `more` of the
    words that follow (a sub-command, a folder or a script by its name; never an option, the value an option takes
    unless it is a path or the program's name after -m, a url, a number or a word a secret rule would flag), up to
    the first pipe or `;`; for read_file and search_files the verb and the name of the file or folder; else the
    tool's name."""
    if action.tool == "terminal":
        words = _program(str(action.args.get("command", "")))
        if not words:
            return []
        out = [words[0].rsplit("/", 1)[-1]]
        if out[0] in _NO_GIST:
            return out
        after = ""    # the option the word before this one was
        skip = False  # the word before this one was an option that takes a folder (-C proj)
        for word in words[1:]:
            if len(out) > more or word in ("|", "||", "&&", ";", "&", "|&") or word.endswith(";"):
                break
            if skip:
                skip, after = False, ""
                continue
            if word.startswith("-"):
                after = word
                skip = word in _FOLDER_OPTIONS
                continue
            was, after = after, ""
            if was and "=" not in was and not _folderish(word) and not (
                    (was in _NAME_FLAGS and out[0].startswith("python")) or (was == "-c" and out[0] in _SHELLS)):
                continue
            bit = _bit(word)
            if bit:
                out.append(bit)
        return out
    if action.tool.startswith("reflex:") or action.key.startswith("reflex:"):
        return [action.key[len("reflex:"):]]
    if action.tool in LOCAL_TOOLS:
        path = str(action.args.get("path") or action.args.get("file_path") or "")
        stem = _bit(path) if path else ""
        return [action.tool, stem] if stem else [action.tool]
    return [action.tool]


def _options(action: Action, limit: int = 2) -> list:
    """The first option names of a terminal command, without their values (--oneline, -la): what tells `git log` from
    `git log --oneline`. Only the name; one that a secret rule would flag, or that carries a value, is left."""
    if action.tool != "terminal":
        return []
    out = []
    for word in _program(str(action.args.get("command", "")))[1:]:
        if word in ("|", "||", "&&", ";", "&", "|&") or word.endswith(";"):
            break
        if re.fullmatch(r"--[a-z][a-z-]{1,23}|-[A-Za-z]{1,3}", word) and not _SECRET_NAME.search(word) \
                and word not in out and word not in _FOLDER_OPTIONS:
            out.append(word)
    return out[:limit]


def _short(action: Action, limit: int = 24, options: bool = False) -> str:
    """A few words that name the action: a program and what it works on, a tool's name (and, for a description, its
    first option names)."""
    if action.tool == "terminal" and not _gist(action):
        return "a command"
    words = [_plain(w) for w in _gist(action) + (_options(action) if options else [])]
    return _fit([w for w in words if w], limit) or "a call"


WAKE_CHARS = 60   # what a step line of `show` holds of a message (display.STEP_TEXT): the sentence must fit whole


def _hand_back(steps: list) -> str:
    """What the reflex says when it hands back: one whole sentence that fits in WAKE_CHARS, never cut. It names the
    steps when they fit, else it says "its command(s)"."""
    for names in (" then ".join(_short(a) for a in steps), "its command" if len(steps) == 1 else "its commands"):
        sentence = f"The reflex could not run {names}: do it the usual way."
        if len(sentence) <= WAKE_CHARS:
            return sentence
    return "The reflex could not run: do it the usual way."


def draft_reflex(steps: list, cwd: str = "", uses: dict | None = None) -> dict:
    """The reflex a draft becomes: one fixed action, or a fixed sequence of them (no inputs)."""
    uses = uses or {}
    many = len(steps) > 1
    nodes: dict = {}
    for i, action in enumerate(steps, 1):
        name = f"run{i}" if many else "run"
        out = f"out{i}" if many else "out"
        follow = f"run{i + 1}" if i < len(steps) else "done"
        reflex = uses.get(action.key) or (action.key[len("reflex:"):] if action.key.startswith("reflex:") else "")
        if reflex:
            node = {"use": reflex, "as": out, "next": follow}
        elif action.tool == "terminal":
            # what the history shows: the call's own folder wins over the session's, and its timeout and
            # background come with it
            args = {"command": action.args.get("command", "")}
            own = action.args.get("workdir")
            if isinstance(own, str) and own.strip():
                args["workdir"] = own
            elif cwd:
                args["workdir"] = cwd
            if isinstance(action.args.get("timeout"), (int, float)) and not isinstance(action.args.get("timeout"), bool):
                args["timeout"] = action.args["timeout"]
            if action.args.get("background") is True:
                args["background"] = True
            node = {"do": "terminal", "args": args}
        else:
            node = {"do": action.tool, "args": json.loads(json.dumps(action.args, default=str))}
        if not reflex:
            if local_read(action.tool, action.args, str(action.args.get("workdir") or cwd or "")
                          if action.tool == "terminal" else None):
                node["read"] = True
            node.update({"as": out, "next": follow})
        nodes[name] = node
    if many:
        text = "\n\n".join(f"{_short(a)}:\n{{out{i}}}" for i, a in enumerate(steps, 1))
    else:
        text = "{out}"
    nodes["done"] = {"end": text}
    nodes["back"] = {"wake": _hand_back(steps)}
    return {"spec": graph.SPEC, "start": "run1" if many else "run", "fallback": "back", "nodes": nodes}


def draft_name(steps: list, taken: set) -> str:
    """A habit name (lowercase, digits, dashes) of 40 characters at most, not in `taken`: built from the program and
    its sub-command words only (see _gist), with the first option's name added when the plain name is taken, and a
    number last; `reflex` when nothing is left."""
    def build(extra: bool) -> str:
        parts = []
        for action in steps[:3]:
            if action.tool == "terminal":
                bits = _gist(action)
            elif action.tool in LOCAL_TOOLS:
                bits = _gist(action)
                bits = ["read" if action.tool == "read_file" else "search"] + bits[1:]
            elif action.key.startswith("reflex:"):
                bits = [action.key[len("reflex:"):]]
            else:
                bits = [re.sub(r"^mcp__", "", action.tool)]
            parts.append("-".join(bits + (_options(action, 1) if extra else [])))
        parts = [p for p in (re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-") for part in parts) if p]
        kept: list = []
        for part in parts:    # a step's name goes in whole or not at all
            if kept and len("-".join(kept + [part])) > 34:
                break
            kept.append(part)
        text, words = "-".join(kept), []
        for word in text.split("-"):    # a first step that is too long alone: cut after a whole word, never inside one
            if len("-".join(words + [word])) > 34:
                break
            words.append(word)
        return "-".join(words) or "reflex"

    base = build(False)
    if base in taken:
        base = build(True)
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}-{n}"
    return name[:40]


_PATHLIKE = re.compile(r"(?<![\w/])(?:~|\$HOME|\.{1,2})?/[\w.~-]+(?:/\S*)?|\b[\w-]*[A-Za-z][\w-]*\d[\w-]{14,}\b|\b\d[\w-]*[A-Za-z][\w-]{14,}\b")
DESCRIPTION_CHARS = 60


_WHEN = (("first", " at session start"), ("last", " at the end of work"), ("scheduled", " on a schedule"))
_OPERATORS = frozenset({"|", "||", "&&", ";", "&"})


def _replaced(action: Action) -> str:
    """The exact command a one-step reflex replaces, fit for a description, under the rules of a name: a program by
    its name, a sub-command or a file by its name (`_bit`: no path, nothing private, no value), an option by its name
    (`_options`' rule), a small number, `~`. The word after an option is kept only when it is a path (its name) or a
    small number. "" as soon as one word is anything else: an option's value never enters a description."""
    command = str(action.args.get("command", "")) if action.tool == "terminal" else ""
    words = command.split()
    if not words or len(command) > 160 or re.search(r"[\"'`$=\\<>(){}*?]", command):
        return ""
    out, program, after, skip = [], True, False, False
    for word in words:
        if skip:    # the folder after -C: where the command works, not what it does
            skip = False
            continue
        if word in _OPERATORS:
            out.append(word)
            program, after = True, False
            continue
        if word in _FOLDER_OPTIONS and not program:
            skip = True
            continue
        if word.startswith("-") and not program:
            if not re.fullmatch(r"--[a-z][a-z-]{1,23}|-[A-Za-z]{1,3}|-\d{1,3}", word) or _SECRET_NAME.search(word):
                return ""
            out.append(word)
            after = True
            continue
        was, after = after, False
        if word in ("~", ".", "..") or (was and re.fullmatch(r"\d{1,3}", word)):
            out.append(word)
        else:
            bit = _bit(word) if (program or not was or _folderish(word)) else ""
            if not bit or (program and bit in _NO_GIST):
                return ""
            out.append(bit)
        program = False
    return " ".join(out)


def describe(steps: list, moments: dict | None = None) -> str:
    """One line for news and for the skill's description: 60 characters at most, no path, no token. A reflex of one
    command says the exact command it replaces and, when it fits, the moment it was seen at (the model reads this
    line to know that the reflex stands for typing that command)."""
    shares = moments if isinstance(moments, dict) else {}
    share, when = max(((float(shares.get(k) or 0), text) for k, text in _WHEN
                       if isinstance(shares.get(k), (int, float))), default=(0.0, ""))
    when = when if share >= 0.5 else ""
    exact = _replaced(steps[0]) if len(steps) == 1 else ""
    if exact:
        for text in (f"Runs `{exact}`{when}", f"Runs `{exact}`"):
            if len(text) <= DESCRIPTION_CHARS:
                return text
    text = "Runs " + ", then ".join(_short(a, 30, options=True) for a in steps)
    text = _PATHLIKE.sub("…", " ".join(text.split()))
    if len(text) + len(when) <= DESCRIPTION_CHARS:
        return text + when
    return text if len(text) <= DESCRIPTION_CHARS else _fit(text.split(), DESCRIPTION_CHARS - 1) + "…"


def shown(steps: list, cwd: str = "", lang: str = "en", sep: str = "\n") -> str:
    """The exact command(s) or call(s), uncut, with the folder they run in and their options: what a card shows
    before the yes. `cwd` is the folder a step without its own would run in."""
    out = []
    for a in steps:
        if a.tool == "terminal":
            line = str(a.args.get("command", ""))
            own = a.args.get("workdir")
            where = own if isinstance(own, str) and own.strip() else cwd
            from .texts import say  # the notes are the card's words: in the card's language
            notes = ([say("shown_in", lang, where=where)] if where else []) + (
                [say("shown_timeout", lang, n=a.args["timeout"])] if isinstance(a.args.get("timeout"), (int, float))
                and not isinstance(a.args.get("timeout"), bool) else []) + (
                [say("shown_background", lang)] if a.args.get("background") is True else [])
            out.append(line + (f"   ({', '.join(notes)})" if notes else ""))
        elif a.key.startswith("reflex:"):
            out.append("reflex " + a.key[len("reflex:"):])
        else:
            out.append(f"{a.tool} {_canonical(a.args)}")
    return sep.join(out)


_PLAIN_WORD = re.compile(r"-{1,2}[A-Za-z][A-Za-z0-9-]*|\d{1,3}|[A-Za-z][A-Za-z_.-]*|\||\|\||&&|;|&")
_PLAIN_PATH = re.compile(r"[~.]|\.\.|[~.]?[\w.+~-]*(?:/[\w.+~-]*)+")


def _plain_arg(word: str) -> bool:
    """A word that is an option name, a plain word, a small number, an operator or a path: nothing a secret hides in
    but a plain word (which only a human can tell from a password)."""
    return bool(_PLAIN_WORD.fullmatch(word) or _PLAIN_PATH.fullmatch(word))


def brief(action: Action) -> str:
    """The action as the dream's report and `--json` show it. A read that stays on this machine shows as written. An
    action that ACTS shows its program and sub-command, and its arguments only when every one is a plain word, an
    option name, a small number or a path; any other (a value, a url, a quoted string, an assignment) makes it
    `program sub-command …`. A secret typed in a command cannot be caught by a list, so what acts is shown short; the
    full command is on its card, which is where the user decides. Nothing stored changes."""
    if action.label:
        return action.label
    if action.key.startswith("reflex:") or local_read(action.tool, action.args):
        return shown([action])
    if action.tool == "terminal":
        command = str(action.args.get("command", ""))
        words = command.split()
        if words and len(command) <= 160 and not re.search(r"[\"'`$=\\]", command) and all(map(_plain_arg, words)):
            return shown([action])
        return " ".join(_gist(action, 1) or ["a command"]) + " …"
    leaves = list(_strings(action.args))
    if all(_plain_arg(t) for t in leaves) and len(_canonical(action.args)) <= 160:
        return shown([action])
    return action.tool + " …"


def distinct(texts: list) -> list:
    """The same texts, where a text that already stood earlier in the list gets " (2)", " (3)"... so that no two read
    alike (the short labels of two commands that differ only in a value)."""
    seen: Counter = Counter()
    out = []
    for text in texts:
        seen[text] += 1
        out.append(text if seen[text] == 1 else f"{text} ({seen[text]})")
    return out


def _label_apart(spotted: list) -> None:
    """Two spotted actions never show the same label. The first keeps the short one; another gets the names of its
    first options and one more word (`git commit -m …` for `git commit …`) when that tells it apart, else " (2)",
    " (3)"... (two commands that differ only in a value, which a short label does not show)."""
    groups: dict = {}
    for s in spotted:
        groups.setdefault(brief(s.action), []).append(s)
    used = set(groups)
    for label, group in groups.items():
        for s in group[1:]:
            a = s.action
            rich = " ".join(_gist(a, 2) + _options(a)) + " …" if a.tool == "terminal" else label
            new, n = rich, 1
            while new in used:
                n += 1
                new = f"{label} ({n})"
            used.add(new)
            s.action = replace(a, label=new)


def procedure(steps: list) -> str:
    """The procedure text of the reflex's skill."""
    lines = ["Hermes did this each time, so a reflex does it now. Call it with habit_run and use what it returns.", "",
             "It does, in order:"]
    lines += [f"{i}. {_plain(_short(a, 60))}" for i, a in enumerate(steps, 1)]
    lines += ["", "If it comes back handed back, it lists what is left: do that the usual way."]
    return "\n".join(lines)


# ---- comparing ----------------------------------------------------------------------------------------------------

_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_PIPE_LINE = re.compile(r"^" + re.escape(runner.PIPE) + r"(?:\s+\d+)+\s*$", re.M)
_MASKS = (re.compile(r"\d{4}-\d\d-\d\d(?:[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:?\d\d)?)?"),
          re.compile(r"\b\d{1,2}:\d\d(?::\d\d)?\b"),
          re.compile(r"\b(?=[0-9a-fA-F]*[a-fA-F])(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{7,}\b"),
          re.compile(r"\b1\d{9}(?:\d{3})?\b"))  # an epoch time
_COUNT = re.compile(r"\d+")
_LETTER = re.compile(r"[^\W\d_]")


def _squash(text) -> str:
    text = "" if text is None else str(text)
    text = _PIPE_LINE.sub("", _ESCAPES.sub("", text[:COMPARE_CHARS]))
    return " ".join(text.split())


def compare(hermes: str, reflex: str) -> str:
    """"same", "shape" or "differs". "shape" forgives what moves by itself: a date or clock, an id, an epoch time, and
    a count or a pid that sits among words ("3 files" and "5 files"). A number with nothing around it ("12" and "57")
    says everything the output says: two different ones are two different contents. In memory: nothing is stored."""
    a, b = _squash(hermes), _squash(reflex)
    if a == b:
        return "same"
    for mask in _MASKS:
        a, b = mask.sub("0", a), mask.sub("0", b)
    if a == b:
        return "shape"
    if _LETTER.search(a) and _LETTER.search(b):
        return "shape" if _COUNT.sub("0", a) == _COUNT.sub("0", b) else "differs"
    return "differs"
