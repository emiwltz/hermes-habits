"""Which commands only read.

A reflex runs a command with `rx.sh` (it only reads) or `rx.do` (it changes something). A dry run executes the first
and only plans the second, so the promise "a dry run changes nothing" rests on this table.

It is written to be safe by construction rather than complete. A command passes as a read only when its program is
listed AND every one of its words is something the entry allows: for a program with options that write or run
something, the entry lists the options that are fine, and any other option is refused, whatever it is (an option
this file never heard of, a long option cut short, a bundle of short ones). An option whose value is optional never
takes the next word as its value: the program would read that word as another option. A read that is refused costs
little: it goes through `rx.do`, which a dry run plans instead of running. A host that needs more teaches its own
readers.

Commands arrive as a list of words and run without a shell, so there is no pipe, redirection or substitution to read.
"""

from __future__ import annotations

import os
import re

# Folders a program may be named from by its full path; anywhere else, ./ls is not ls.
SYSTEM = ("/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin", "/usr/local/sbin", "/opt/homebrew/bin",
          "/opt/homebrew/sbin", "/opt/local/bin", "/snap/bin")

# Programs none of whose options writes, runs another program or sets anything.
ANY = frozenset("""
    ls cat head wc stat cut tr nl rev fold fmt expand basename dirname realpath readlink pwd whoami uname id du df
    echo printf true false test [ ps pgrep pidof uptime free which nproc column jq grep egrep fgrep strings
    md5 md5sum shasum sha1sum sha256sum diff cmp comm lscpu lsblk lsusb sw_vers vm_stat ioreg mdls mdfind
    system_profiler dig nslookup host printenv netstat
""".split())
OUTSIDE = frozenset({"curl", "dig", "nslookup", "host", "ping", "kubectl"})  # reads that ask another machine


class Options:
    """The words a program may take and still only read."""

    def __init__(self, flags="", valued="", attached="", long=(), long_valued=(), long_optional=(), digits=False,
                 plus=False, positional=None, bundles=True):
        self.flags = set(flags)  # short options with no value: -n
        self.valued = set(valued)  # short options that always take a value, attached or in the next word: -n 5, -n5
        self.attached = set(attached)  # short options whose value, if any, is the rest of the word: -Iseconds
        self.long = set(long)  # --quiet
        self.long_valued = set(long_valued)  # long options that always take a value: --lines 5, --lines=5
        self.long_optional = set(long_optional)  # a value only ever after =: --color, --color=always
        self.digits = digits  # -5
        self.plus = plus  # +5, +%F
        self.positional = positional  # how many other words at most; None: any number
        self.bundles = bundles  # False: the program reads -Lo 2 f as -L 2 -o f, so a valued option ends its word

    def read(self, name: str, args: list) -> tuple:
        """(why these words do not only read, or "", the words that are neither an option nor an option's value)."""
        others, i = [], 0
        while i < len(args):
            word = args[i]
            if word == "--":
                others += args[i + 1:]
                break
            if word.startswith("--"):
                option, equals, _ = word.partition("=")
                if option in self.long_valued:
                    i += 0 if equals else 1
                elif option not in self.long_optional and (option not in self.long or equals):
                    return f"{name} {option} is not among the options known to only read", others
            elif len(word) > 1 and word[0] == "-":
                if not (self.digits and word[1:].isdigit()):
                    for at, letter in enumerate(word[1:], 1):
                        last = at == len(word) - 1
                        if letter in self.valued:
                            if not last and not self.bundles:
                                return f"{name} -{letter} takes its value in the next word", others
                            i += 1 if last else 0
                            break
                        if letter in self.attached:
                            break
                        if letter not in self.flags:
                            return f"{name} -{letter} is not among the options known to only read", others
            elif not (self.plus and word.startswith("+")):
                others.append(word)
            i += 1
        if self.positional is not None and len(others) > self.positional:
            return (f"{name} takes {self.positional} name{'' if self.positional == 1 else 's'} here, "
                    f"not {len(others)}"), others
        return "", others

    def why_not(self, name: str, args: list) -> str:
        return self.read(name, args)[0]


TAIL = Options("qvz", "nc", long=["--quiet", "--silent", "--verbose", "--zero-terminated"],
               long_valued=["--lines", "--bytes"], digits=True)
SORT = Options("bdfgiMhnRrVuzscCm", "kt",
               long=["--ignore-leading-blanks", "--dictionary-order", "--ignore-case", "--general-numeric-sort",
                     "--ignore-nonprinting", "--month-sort", "--human-numeric-sort", "--numeric-sort", "--random-sort",
                     "--reverse", "--version-sort", "--unique", "--zero-terminated", "--stable", "--check", "--merge"],
               long_valued=["--key", "--field-separator", "--sort"])
UNIQ = Options("cduizD", "fsw", long=["--count", "--repeated", "--unique", "--ignore-case", "--zero-terminated"],
               long_valued=["--skip-fields", "--skip-chars", "--check-chars"], positional=1)
DATE = Options("uRj", "drfv", attached="I", long=["--utc", "--universal", "--rfc-email", "--rfc-2822"],
               long_valued=["--date", "--reference", "--rfc-3339"], long_optional=["--iso-8601"], plus=True)
HOSTNAME = Options("sfiIdAy", long=["--short", "--fqdn", "--long", "--ip-address", "--all-ip-addresses", "--domain",
                                    "--all-fqdns"], positional=0)
SYSCTL = Options("naeNbq", long=["--values", "--all", "--names", "--binary", "--quiet", "--ignore"])
PING = Options("nq46", "cWwit", positional=1)
FILE = Options("bihLzkrsNn0", "eF", long=["--brief", "--mime", "--mime-type", "--mime-encoding", "--dereference",
                                          "--no-dereference", "--uncompress", "--keep-going", "--raw",
                                          "--special-files", "--no-pad", "--print0"])
TREE = Options("adlfxCnNqpugshDFvtcUriASJ", "LPI", long=["--dirsfirst", "--noreport", "--prune", "--du", "--si",
                                                        "--gitignore"], long_valued=["--filelimit", "--charset"],
               bundles=False)
JOURNALCTL = Options("rexqalkbm", "upoSUgt", attached="n", digits=True,
                     long=["--reverse", "--pager-end", "--catalog", "--quiet", "--all", "--full", "--dmesg",
                           "--user", "--system", "--no-pager", "--utc", "--no-hostname", "--no-tail", "--disk-usage",
                           "--list-boots", "--merge"],
                     long_valued=["--unit", "--user-unit", "--priority", "--output", "--since", "--until",
                                  "--grep", "--identifier", "--facility"],
                     long_optional=["--boot", "--lines"])
SYSTEMCTL = Options("alq", "nopt",
                    long=["--all", "--full", "--no-pager", "--no-legend", "--plain", "--failed", "--value", "--quiet",
                          "--user", "--system", "--recursive", "--reverse", "--show-types"],
                    long_valued=["--lines", "--output", "--type", "--state", "--property"])
LSOF = Options("nPlRatUN", "upcd", attached="isgF")
SS = Options("HOnraloempTis460tudwxSZz", "fA",
             long=["--no-header", "--oneline", "--numeric", "--resolve", "--all", "--listening", "--options",
                   "--extended", "--memory", "--processes", "--threads", "--info", "--summary", "--ipv4", "--ipv6",
                   "--tcp", "--udp", "--raw", "--unix"], long_valued=["--family", "--query"])
OPTIONS = {"tail": TAIL, "sort": SORT, "uniq": UNIQ, "date": DATE, "hostname": HOSTNAME, "sysctl": SYSCTL,
           "ping": PING, "file": FILE, "tree": TREE, "journalctl": JOURNALCTL, "lsof": LSOF, "ss": SS}

# Programs that read only through these subcommands, which must be the first word (an option before it could hide
# another one), and then take what is listed: an Options, or None for any words.
SUBCOMMANDS = {
    "systemctl": dict.fromkeys(["is-active", "is-enabled", "is-failed", "status", "show", "cat", "list-units",
                                "list-timers", "list-unit-files", "list-sockets", "list-dependencies"], SYSTEMCTL),
    "docker": dict.fromkeys(["ps", "images", "version", "info", "inspect", "logs", "port", "top", "history", "diff"]),
    "kubectl": dict.fromkeys(["get", "describe", "logs", "version", "top", "api-resources", "explain"]),
    "launchctl": dict.fromkeys(["list", "print"]),
    "diskutil": dict.fromkeys(["list", "info"]),
    "defaults": dict.fromkeys(["read", "read-type", "domains", "find"]),
    "hermes": dict.fromkeys(["logs", "status", "version"]),
    "vcgencmd": dict.fromkeys(["measure_temp", "measure_volts", "measure_clock", "get_throttled", "get_mem",
                               "version"], Options(positional=1)),
}
LEADING = {"systemctl": {"--user", "--system", "--no-pager"}}  # flags that may come before the subcommand
ENDLESS = {"logs": ("f", {"--follow"}), "get": ("w", {"--watch", "--watch-only"})}  # they never end

# An interpreter or a build tool reads only when it says its version. Not go, cargo or rustc: in a folder that
# asks for another version of them, even that downloads and runs a whole toolchain.
VERSIONS = {name: {"--version", "-V"} for name in ("python", "python3", "node", "ruby", "perl", "uv", "java",
                                                    "gcc", "make", "ffmpeg", "npm", "pip", "pip3")}
for _name in ("node", "perl", "ruby", "gcc", "make"):
    VERSIONS[_name] = VERSIONS[_name] | {"-v"}
VERSIONS["java"] = VERSIONS["java"] | {"-version"}
VERSIONS["ffmpeg"] = VERSIONS["ffmpeg"] | {"-version"}

FIND_NEVER = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
RG_NEVER = {"--pre", "--pre-glob", "--hostname-bin"}
# git: subcommands that only read, whatever their options, except the ones below. git takes a long option cut
# short, so a word is refused as soon as it could be the start of one of them.
GIT = {"status", "log", "diff", "show", "blame", "describe", "ls-files", "ls-tree", "shortlog", "rev-parse",
       "rev-list", "cat-file", "merge-base", "name-rev", "for-each-ref", "count-objects", "whatchanged"}
GIT_NEVER = ("--output", "--ext-diff", "--textconv", "--filters", "--open-files-in-pager", "--exec", "--upload-pack",
             "--receive-pack", "--show-signature")
GIT_FINE = {"--text", "--ex", "--exclude", "--exclude-standard", "--exclude-from", "--exclude-per-directory"}
GIT_BRANCH = Options("arlvi", long=["--all", "--remotes", "--list", "--show-current", "--verbose", "--ignore-case",
                                    "--column", "--no-column", "--no-color", "--merged", "--no-merged"],
                     long_valued=["--contains", "--no-contains", "--sort", "--format", "--points-at"],
                     long_optional=["--color"])
GIT_TAG = Options("li", attached="n", long=["--list", "--ignore-case", "--column", "--no-column", "--merged",
                                            "--no-merged"],
                  long_valued=["--contains", "--no-contains", "--sort", "--format", "--points-at"])
CURL_PLAIN = {"-s", "-S", "-L", "-f", "-I", "-i", "-k", "-4", "-6", "-g", "-N", "--silent", "--show-error",
              "--location", "--fail", "--head", "--include", "--compressed", "--http1.1", "--http2", "--globoff",
              "--insecure", "--no-buffer", "--no-progress-meter", "--fail-with-body", "--fail-early", "--ipv4",
              "--ipv6", "--tlsv1.2", "--tlsv1.3", "--path-as-is", "--no-keepalive"}
CURL_VALUED = {"-m", "--max-time", "-A", "--user-agent", "-H", "--header", "-r", "--range", "--retry",
               "--retry-delay", "--retry-max-time", "--connect-timeout", "-e", "--referer", "--max-redirs",
               "--max-filesize"}
CURL_BUNDLE = set("sSLfIik46gN")
CURL_GUESSED = ("ftp.", "dict.", "ldap.", "imap.", "smtp.", "pop3.")  # with no scheme, curl guesses one from these
CURL_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]+)?([/?#][\x21-\x7e]*)?$")
IP = {"addr": {"show", "list", "ls"}, "link": {"show", "list", "ls"}, "route": {"show", "list", "ls", "get"},
      "neigh": {"show", "list", "ls"}, "rule": {"show", "list", "ls"}}
IP_NAMES = {"a": "addr", "address": "addr", "l": "link", "r": "route", "n": "neigh", "neighbor": "neigh",
            "neighbour": "neigh"}
IP_FLAGS = {"-4", "-6", "-br", "-brief", "-o", "-oneline", "-j", "-json", "-p", "-pretty", "-c", "-color", "-s",
            "-stats", "-statistics", "-d", "-details", "-h", "-human", "-r", "-resolve"}


def program(word: str) -> str:
    return os.path.basename(word)


def outside(argv: list) -> bool:
    """A read that asks another machine (the answer is anyone's text, and the question itself leaves this one)."""
    return bool(argv) and program(str(argv[0])) in OUTSIDE


def as_read(argv: list) -> list:
    """The command as it is run once it passed as a read. curl is told to speak HTTP and HTTPS only, also when it
    follows a redirect: what a server answers is not ours to read in advance, and -L would follow it into FTP."""
    if argv and program(str(argv[0])) == "curl":
        return [argv[0], "--proto", "=http,https", "--proto-redir", "=http,https", *argv[1:]]
    return list(argv)


def why_not(argv: list, extra: dict | None = None) -> str:
    """"" when the command only reads; else why it does not pass as a read."""
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(word, str) for word in argv):
        return "a command is a list of words"
    name, args = program(argv[0]), list(argv[1:])
    if "/" in argv[0] and os.path.dirname(argv[0]) not in SYSTEM:
        return f"{argv[0]} is a program of its own, not the system's {name}"
    allowed = (extra or {}).get(name)
    if allowed is True:
        return ""
    if allowed:
        first = args[0] if args else "(no subcommand)"
        return "" if first in set(allowed) else f"{name} {first} is not listed as a read"
    if name in ANY:
        return ""
    if name in OPTIONS:
        why, others = OPTIONS[name].read(name, args)
        return why or _also(name, args, others)
    if name in RULES:
        return RULES[name](args)
    if name in SUBCOMMANDS:
        return _subcommand(name, args)
    if name in VERSIONS:
        return "" if len(args) == 1 and args[0] in VERSIONS[name] else f"{name} runs code: only `{name} --version` is a read"
    return f"{name} is not known as a command that only reads"


def _also(name: str, args: list, others: list) -> str:
    """What the list of options cannot say. others: the words that are neither an option nor an option's value."""
    if name == "sysctl" and any("=" in w for w in args):
        return "sysctl name=value sets a value"
    if name == "date" and others and "-j" not in args:  # with -j (BSD) a date is only parsed, never set
        return "date <value> sets the clock"
    if name == "tail" and any(w.startswith("+") and not w[1:].isdigit() for w in args):
        return "tail +N takes a number only"
    if name == "ping":
        count = next((w[2:] or "".join(args[i + 1:i + 2]) for i, w in enumerate(args) if w.startswith("-c")), "")
        if not (count.isdigit() and int(count) <= 5):
            return "ping needs -c with a count of 5 at most, or it never ends"
    return ""


def _subcommand(name: str, args: list) -> str:
    rest = list(args)
    while rest and rest[0] in LEADING.get(name, ()):
        rest = rest[1:]
    if rest == ["--version"]:
        return ""
    if not rest or rest[0] not in SUBCOMMANDS[name]:
        return f"{name} {rest[0] if rest else '(no subcommand)'} is not listed as a read: the subcommand comes first"
    sub, tail = rest[0], rest[1:]
    letter, words = ENDLESS.get(sub, ("", set()))
    if any(w.split("=", 1)[0] in words or (letter and len(w) > 1 and w[0] == "-" and w[1] != "-" and letter in w)
           for w in tail):
        return f"{name} {sub} that follows or watches never ends"
    options = SUBCOMMANDS[name][sub]
    return options.why_not(f"{name} {sub}", tail) if options else ""


def _find(args: list) -> str:
    found = sorted(w for w in args if w in FIND_NEVER)
    return f"find {found[0]} writes or runs something" if found else ""


def _rg(args: list) -> str:
    found = sorted(w for w in args if w.split("=", 1)[0] in RG_NEVER)
    return f"rg {found[0]} runs a program" if found else ""


def _git(args: list) -> str:
    rest = list(args)
    while rest and rest[0] in ("-C", "--no-pager"):
        rest = rest[2:] if rest[0] == "-C" else rest[1:]
    if not rest:
        return "git with no subcommand"
    if rest in (["--version"], ["version"]):
        return ""
    sub, tail = rest[0], rest[1:]
    for word in tail:
        option = word.split("=", 1)[0]
        if option.startswith("--") and len(option) > 2 and option not in GIT_FINE and any(
                never.startswith(option) for never in GIT_NEVER):
            return f"git {option} can write a file or run a program"
    if any("%G" in word for word in tail):
        return "git %G checks a signature, which runs gpg"
    names = [w for w in tail if not w.startswith("-")]
    if sub in GIT:
        return ""
    if sub == "branch":
        listing = {"-l", "--list", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at"}
        if names and not any(w.split("=", 1)[0] in listing for w in tail):
            return "git branch <name> creates a branch"
        return GIT_BRANCH.why_not("git branch", tail)
    if sub == "tag":
        listing = {"-l", "--list", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at"}
        if names and not any(w.split("=", 1)[0] in listing or w.startswith("-n") for w in tail):
            return "git tag <name> creates a tag"
        return GIT_TAG.why_not("git tag", tail)
    if sub == "remote":
        return "" if tail in ([], ["-v"], ["--verbose"]) or (names[:1] == ["get-url"] and len(names) == 2) else \
            "git remote reads only as `git remote -v` or `git remote get-url <name>`"
    if sub == "config":
        reading = {"--get", "--get-all", "--get-regexp", "-l", "--list"}
        plain = {"--global", "--local", "--system", "--worktree", "--null", "-z", "--show-origin", "--show-scope",
                 "--name-only"}
        if names[:1] in (["get"], ["list"]) and all(w in plain for w in tail if w.startswith("-")):
            return ""
        if set(tail) & reading and all(w in reading | plain for w in tail if w.startswith("-")):
            return ""
        if len(names) == 1 and "." in names[0] and all(w in plain for w in tail if w.startswith("-")):
            return ""  # git config user.email: one name and no value
        return "git config reads only as `git config <name>`, or with --get, --get-all, --get-regexp or --list"
    if sub in ("stash", "worktree"):
        return "" if names[:1] in (["list"], ["show"]) and tail[:1] == names[:1] else \
            f"git {sub} reads only as `git {sub} list`" + (" or `git stash show`" if sub == "stash" else "")
    if sub == "reflog":
        return "" if not names or tail[:1] == ["show"] else f"git reflog {names[0]} is not a read"
    return f"git {sub} is not listed as a read"


def _curl(args: list) -> str:
    urls, i = [], 0
    while i < len(args):
        word = args[i]
        if word in ("-w", "--write-out"):
            form = "".join(args[i + 1:i + 2])
            if "%output" in form or "%{output" in form:
                return "curl -w with %output writes a file"
            if form.startswith("@"):  # the format is read from a file or from the input: it may say %output
                return "curl -w @file takes its format from somewhere this table cannot read"
            i += 2
        elif word == "--url":
            urls += args[i + 1:i + 2]
            i += 2
        elif word in CURL_VALUED:
            i += 2
        elif word in ("-o", "--output"):
            if args[i + 1:i + 2] != ["/dev/null"]:
                return "curl -o writes a file (only -o /dev/null is a read)"
            i += 2
        elif word in ("-X", "--request"):
            if [w.upper() for w in args[i + 1:i + 2]] not in (["GET"], ["HEAD"]):
                return f"curl -X {' '.join(args[i + 1:i + 2])} is not a GET or a HEAD"
            i += 2
        elif word in CURL_PLAIN or (len(word) > 1 and word[0] == "-" and word[1] != "-" and set(word[1:]) <= CURL_BUNDLE):
            i += 1
        elif word.startswith("-"):
            return f"curl {word} is not among the options of a plain GET or HEAD"
        else:
            urls.append(word)
            i += 1
    if not urls:
        return "curl with no URL"
    for url in urls:
        low = url.lower()
        if low.startswith(("http://", "https://")):
            continue
        # no scheme: a plain host, with a port and a path if any. Anything else could be read by curl as another
        # scheme (gopher:/host with one slash, a@dict.host with a user before a host it guesses the scheme from)
        if not CURL_HOST.match(low) or low.startswith(CURL_GUESSED):
            return f"curl reads http and https only, not {url[:40]}"
    return ""


def _ip(args: list) -> str:
    rest = list(args)
    while rest and rest[0] in IP_FLAGS:
        rest = rest[1:]
    thing = IP_NAMES.get(rest[0], rest[0]) if rest else ""
    if thing in IP and (len(rest) == 1 or rest[1] in IP[thing]):
        return ""
    return "ip reads only as `ip addr`, `ip link`, `ip route`, `ip neigh` or `ip rule`, alone or with show"


def _sleep(args: list) -> str:
    try:
        return "" if len(args) == 1 and 0 <= float(args[0]) <= 10 else "sleep takes one number of seconds, 10 at most"
    except ValueError:
        return "sleep takes one number of seconds, 10 at most"


def _exactly(name: str, *forms):
    def rule(args: list) -> str:
        return "" if args in [list(form) for form in forms] else "only " + " or ".join(
            f"`{name} {' '.join(form)}`" for form in forms) + " is a read"
    return rule


RULES = {"git": _git, "curl": _curl, "find": _find, "rg": _rg, "sleep": _sleep, "ip": _ip,
         "crontab": _exactly("crontab", ["-l"]), "pmset": _exactly("pmset", ["-g"], ["-g", "batt"], ["-g", "ps"])}
