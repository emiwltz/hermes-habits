"""Which commands only read.

A reflex runs a command with `rx.sh` (it only reads) or `rx.do` (it changes something). A dry run executes the first
and only plans the second, so the promise "a dry run changes nothing" rests on this table: a command passes as a read
only if its program is listed here and its words keep to what the entry allows. Anything else is refused with the
reason, and belongs in `rx.do`.

Commands arrive as a list of words and run without a shell, so there is no pipe, redirection or substitution to read.
"""

from __future__ import annotations

import os
import re

# Programs that only read whatever their arguments are.
ANY = frozenset("""
    ls cat head wc stat cut tr nl rev fold fmt expand basename dirname realpath readlink pwd whoami uname id du df
    echo printf true false test [ ps pgrep pidof uptime free which nproc arch column jq grep egrep fgrep strings
    md5 md5sum shasum sha1sum sha256sum diff cmp comm lscpu lsblk lsusb sw_vers vm_stat ioreg mdls mdfind
    system_profiler dig nslookup host lsof
""".split())

# Programs that read unless one of these options is among their words.
UNLESS = {
    "find": {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"},
    "tail": {"-f", "-F", "--follow", "--retry"},
    "sort": {"-o", "--output", "--compress-program"},
    "file": {"-C", "--compile"},
    "tree": {"-o"},
    "rg": {"--pre", "--pre-glob", "--hostname-bin"},
    "date": {"-s", "--set"},
    "journalctl": {"-f", "--follow", "--flush", "--rotate", "--sync", "--relinquish-var", "--smart-relinquish-var",
                   "--setup-keys", "--update-catalog", "--vacuum-size", "--vacuum-time", "--vacuum-files"},
    "sysctl": {"-w", "--write", "-p", "--load", "--system"},
}

# Of those, the letters that mean the same inside a bundle of short options (tail -fn 5, sort -uo out).
UNLESS_LETTERS = {"tail": "fF", "sort": "o", "file": "C", "tree": "o", "journalctl": "f"}

# Programs that read only through these subcommands (the first words that are not options).
SUBCOMMANDS = {
    "systemctl": {"is-active", "is-enabled", "is-failed", "status", "show", "cat", "list-units", "list-timers",
                  "list-unit-files", "list-sockets", "list-dependencies"},
    "docker": {"ps", "images", "version", "info", "inspect", "logs", "port", "top", "history", "diff"},
    "kubectl": {"get", "describe", "logs", "version", "top", "api-resources", "explain"},
    "npm": {"ls", "list", "view", "outdated", "root", "prefix"},
    "pip": {"list", "show", "freeze", "check"},
    "pip3": {"list", "show", "freeze", "check"},
    "brew": {"list", "info", "outdated", "deps"},
    "cargo": {"tree", "metadata"},
    "go": {"version", "env", "list"},
    "launchctl": {"list", "print"},
    "diskutil": {"list", "info"},
    "defaults": {"read", "domains", "find"},
    "hermes": {"logs", "status", "version"},
    "vcgencmd": {"measure_temp", "measure_volts", "measure_clock", "get_throttled", "get_mem", "version"},
}
FOLLOWS = {"-f", "--follow"}  # never ends: no reflex waits on a stream
VERSION = {"--version", "-V", "-v", "version"}  # `python3 --version`, `node -v`: the one read of an interpreter
INTERPRETERS = {"python", "python3", "node", "ruby", "perl", "uv", "java", "rustc", "gcc", "make", "ffmpeg"}

GIT = {"status", "log", "diff", "show", "blame", "describe", "ls-files", "ls-tree", "shortlog", "rev-parse",
       "rev-list", "cat-file", "merge-base", "name-rev", "for-each-ref", "count-objects", "grep", "whatchanged"}
GIT_NEVER = {"-c", "--output", "--ext-diff", "--textconv", "--filters", "--exec-path", "--upload-pack",
             "--open-files-in-pager"}
OUTSIDE = {"curl", "dig", "nslookup", "host", "ping"}  # reads that leave this machine
CURL_PLAIN = {"-s", "-S", "-L", "-f", "-I", "-i", "-k", "--silent", "--show-error", "--location", "--fail", "--head",
              "--include", "--compressed", "--http1.1", "--http2", "-4", "-6", "-g", "--globoff"}
CURL_VALUED = {"-m", "--max-time", "-A", "--user-agent", "-H", "--header", "-w", "--write-out", "-r", "--range",
               "--retry", "--retry-delay", "--connect-timeout", "-e", "--referer", "--url"}
SHORT_BUNDLE = re.compile(r"^-[sSLfIik46g]+$")


def program(word: str) -> str:
    return os.path.basename(word)


def outside(argv: list) -> bool:
    """A read that asks another machine (the answer is anyone's text, and the question itself leaves this one)."""
    return bool(argv) and program(str(argv[0])) in OUTSIDE


def why_not(argv: list, extra: dict | None = None) -> str:
    """"" when the command only reads; else why it does not pass as a read."""
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(word, str) for word in argv):
        return "a command is a list of words"
    name, args = program(argv[0]), list(argv[1:])
    allowed = (extra or {}).get(name)
    if allowed is True:
        return ""
    if allowed:
        return "" if _sub(args) in set(allowed) else f"{name} {_sub(args) or ''} is not listed as a read".strip()
    if name in ANY:
        return ""
    if args in (["--version"], ["-V"]) and (name in UNLESS or name in SUBCOMMANDS or name in RULES):
        return ""
    if name in UNLESS:
        letters = UNLESS_LETTERS.get(name, "")
        found = sorted(w for w in args if w in UNLESS[name] or w.split("=", 1)[0] in UNLESS[name]
                       or (letters and _bundle(w) and set(w) & set(letters)))
        if found:
            return f"{name} {found[0]} writes, runs something or never ends"
        return _extra_rule(name, args)
    if name in SUBCOMMANDS:
        sub = _sub(args)
        if sub not in SUBCOMMANDS[name]:
            return f"{name} {sub or '(no subcommand)'} is not listed as a read"
        if sub == "logs" and any(w in FOLLOWS or (_bundle(w) and "f" in w) for w in args):
            return f"{name} logs that follows never ends"
        return ""
    rule = RULES.get(name)
    if rule:
        return rule(args)
    if name in INTERPRETERS:
        return "" if len(args) == 1 and args[0] in VERSION else f"{name} runs code: only `{name} --version` is a read"
    return f"{name} is not known as a command that only reads"


def _sub(args: list) -> str | None:
    return next((w for w in args if not w.startswith("-")), None)


def _bundle(word: str) -> bool:
    """A word of short options: -f, -fn, -uo."""
    return len(word) > 1 and word[0] == "-" and word[1] != "-"


def _extra_rule(name: str, args: list) -> str:
    if name == "sysctl" and any("=" in w for w in args):
        return "sysctl name=value sets a value"
    if name == "date":  # a bare date sets the clock, unless an option says the words are a date to print
        printing = {"-j", "-d", "--date", "-r", "--reference", "-f", "-v"}
        bare = [w for w in args if not w.startswith(("-", "+"))]
        if bare and not any(w in printing or w.split("=", 1)[0] in printing for w in args):
            return "date <value> sets the clock"
    return ""


def _git(args: list) -> str:
    rest = list(args)
    while rest and rest[0] in ("-C", "--no-pager", "--git-dir", "--work-tree"):
        rest = rest[2:] if rest[0] in ("-C", "--git-dir", "--work-tree") else rest[1:]
    bad = sorted(w for w in rest if w in GIT_NEVER or w.split("=", 1)[0] in GIT_NEVER)
    if bad:
        return f"git {bad[0]} can run a program or write a file"
    if not rest:
        return "git with no subcommand"
    if rest in (["--version"], ["version"]):
        return ""
    sub, tail = rest[0], rest[1:]
    if sub == "grep" and any(w.startswith("-O") for w in tail):
        return "git grep -O opens a program on the files it finds"
    if sub in GIT:
        return ""
    names = [w for w in tail if not w.startswith("-")]
    if sub == "branch":
        listing = {"-l", "--list", "-a", "--all", "-r", "--remotes", "--show-current", "-v", "-vv", "--contains",
                   "--merged", "--no-merged"}
        return "" if not names or set(tail) & listing else "git branch <name> creates a branch"
    if sub == "tag":
        return "" if not names or set(tail) & {"-l", "--list", "-n"} else "git tag <name> creates a tag"
    if sub == "remote":
        return "" if not names or names[:1] == ["get-url"] else f"git remote {names[0]} is not a read"
    if sub == "config":
        return "" if set(tail) & {"--get", "--get-all", "--get-regexp", "-l", "--list"} else "git config sets a value"
    if sub in ("stash", "worktree", "reflog"):
        ok = names[:1] in ([], ["list"], ["show"]) if sub != "reflog" else names[:1] in ([], ["show"])
        return "" if ok else f"git {sub} {names[0]} is not a read"
    return f"git {sub} is not listed as a read"


def _curl(args: list) -> str:
    urls, i = [], 0
    while i < len(args):
        word = args[i]
        if word in CURL_VALUED:
            i += 2
            continue
        if word in ("-o", "--output"):
            if args[i + 1:i + 2] != ["/dev/null"]:
                return "curl -o writes a file (only -o /dev/null is a read)"
            i += 2
            continue
        if word in ("-X", "--request"):
            if [w.upper() for w in args[i + 1:i + 2]] not in (["GET"], ["HEAD"]):
                return f"curl -X {' '.join(args[i + 1:i + 2])} is not a GET or a HEAD"
            i += 2
            continue
        if word in CURL_PLAIN or SHORT_BUNDLE.match(word):
            i += 1
            continue
        if word.startswith("-"):
            return f"curl {word} is not among the options of a plain GET or HEAD"
        urls.append(word)
        i += 1
    if not urls:
        return "curl with no URL"
    other = [u for u in urls if "://" in u and not u.lower().startswith(("http://", "https://"))]
    return f"curl reads http and https only, not {other[0][:40]}" if other else ""


def _uniq(args: list) -> str:
    return "" if len([w for w in args if not w.startswith("-")]) <= 1 else "uniq with a second file writes it"


def _sleep(args: list) -> str:
    try:
        return "" if len(args) == 1 and 0 <= float(args[0]) <= 10 else "sleep takes one number of seconds, 10 at most"
    except ValueError:
        return "sleep takes one number of seconds, 10 at most"


def _hostname(args: list) -> str:
    return "" if all(w.startswith("-") for w in args) else "hostname <name> sets the name"


def _ping(args: list) -> str:
    for flag in ("-c", "--count"):
        if flag in args:
            count = args[args.index(flag) + 1:args.index(flag) + 2]
            if count and count[0].isdigit() and int(count[0]) <= 5:
                return ""
    return "ping needs -c with a count of 5 at most, or it never ends"


def _crontab(args: list) -> str:
    return "" if args == ["-l"] else "only crontab -l is a read"


def _pmset(args: list) -> str:
    return "" if args[:1] == ["-g"] else "only pmset -g is a read"


RULES = {"git": _git, "curl": _curl, "uniq": _uniq, "sleep": _sleep, "hostname": _hostname, "ping": _ping,
         "crontab": _crontab, "pmset": _pmset}
