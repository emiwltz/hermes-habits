"""Which shell lines only read: a careful reader, never a guess.

A terminal step is a read (it runs in shadow, and a habit's reads are not measured as writes) when every command
of its pipes and sequences is a known reader used without what makes it write. The line is read the way a POSIX
shell reads it: quotes, escapes, braces, redirections, $( ) and backticks. Whatever the reader cannot classify (an
unknown command, a heredoc, a variable as a command, an open quote) is a write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TEMPLATE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*\}")
EXPANSION = "\x00"  # where a $variable or a substitution stood in a word: its value is unknown
MARK = "\x01"  # where a {template} stands: a value filled at run time, one shell-quoted word, never an option
DEPTH = 4  # substitutions and ssh hops inside one another
WORDS = 256  # words one word may become by brace expansion

# Readers that no option or argument turns into a writer.
PURE = {"ls", "cat", "head", "grep", "egrep", "fgrep", "wc", "stat", "cut", "tr", "jq", "column", "nl", "rev",
        "fold", "fmt", "expand", "basename", "dirname", "realpath", "readlink", "pwd", "whoami", "uname", "id", "du",
        "df", "echo", "true", "false", "ps", "pgrep", "pidof", "uptime", "free", "vm_stat",
        "sw_vers", "md5", "md5sum", "shasum", "sha256sum", "diff", "cmp", "comm", "ioreg"}
FIND_WRITES = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
# curl only reads with these options: a plain GET or HEAD, nothing written, nothing sent.
CURL_READ = {"-s", "-S", "-L", "-f", "-I", "-sS", "-sL", "-fsS", "-fsSL", "-sSL", "-fsL", "-sf", "-fs",
             "--silent", "--show-error", "--location", "--fail", "--head", "--compressed",
             "-m", "--max-time", "--connect-timeout", "--retry", "-A", "--user-agent", "-H", "--header",
             "-w", "--write-out", "-r", "--range", "-X", "--request", "-o", "--output"}
CURL_VALUE = {"-m", "--max-time", "--connect-timeout", "--retry", "-A", "--user-agent", "-H", "--header", "-w",
              "--write-out", "-r", "--range", "-X", "--request", "-o", "--output"}
# git sub-commands that print and change nothing, each with rules of its own (a listing form, a --get).
GIT_READS = {"rev-parse", "branch", "remote", "tag", "config"}
GIT_KEYS = re.compile(r"^(?:color\.[\w.-]+|core\.quotepath|safe\.directory)=", re.I)  # -c keys that change a display
# git sub-commands that only look, a developer's most common gestures: each takes only the options listed for it
# (an option written --name=value, a cluster of its one-letter ones, a number). Any other option is a write: it
# could write a file (--output), run a program the line names (--ext-diff, --textconv, a pager, gpg for
# --show-signature or a %G in a format) or be one only a later git knows. With them git takes no -c at all (an
# alias, a pager and an external diff are settings). A repository's own .git/config can still name a program these
# run (core.fsmonitor for status, diff.external or a textconv driver for diff, gpg for log.showSignature): it runs
# the same when Hermes types the line itself, which is where a reflex or the dream takes such a line from.
_GIT_REVS = {"--all", "--branches", "--tags", "--remotes", "--merges", "--no-merges", "--first-parent", "--reverse",
             "--date-order", "--topo-order", "--author-date-order", "--left-right", "--cherry-pick", "--cherry-mark",
             "--boundary", "--no-walk", "--full-history", "--simplify-by-decoration", "--ancestry-path", "--not", "-i",
             "--regexp-ignore-case", "-E", "--extended-regexp", "-F", "--fixed-strings", "--all-match", "--invert-grep",
             "-g", "--walk-reflogs", "-n"}
_GIT_REV_VALUES = {"--max-count", "--skip", "--since", "--after", "--until", "--before", "--author", "--committer",
                   "--grep", "--min-parents", "--max-parents", "--branches", "--tags", "--remotes", "--glob", "--exclude"}
_GIT_SHOWN = {"--oneline", "--graph", "--decorate", "--no-decorate", "--abbrev-commit", "--no-abbrev-commit",
              "--relative-date", "--parents", "--children", "--source", "--no-notes", "--no-mailmap", "--use-mailmap",
              "--no-show-signature", "--follow", "--pretty", "--color", "--no-color", "--abbrev"}
_GIT_SHOWN_VALUES = {"--format", "--pretty", "--date", "--decorate", "--decorate-refs", "--decorate-refs-exclude",
                     "--abbrev", "--color", "--encoding", "--diff-merges"}
_GIT_DIFF = {"-p", "-u", "--patch", "-s", "--no-patch", "--stat", "--numstat", "--shortstat", "--summary", "--name-only",
             "--name-status", "--raw", "--patch-with-stat", "--patch-with-raw", "--compact-summary", "--dirstat",
             "--cumulative", "--check", "--cached", "--staged", "--merge-base", "-z", "-R", "-w", "-b", "-a", "--text",
             "--ignore-all-space", "--ignore-space-change", "--ignore-space-at-eol", "--ignore-blank-lines",
             "--ignore-cr-at-eol", "--minimal", "--patience", "--histogram", "--word-diff", "--color-words", "--color",
             "--no-color", "--color-moved", "--no-renames", "-M", "-C", "-B", "-D", "--find-renames", "--find-copies",
             "--find-copies-harder", "--irreversible-delete", "--full-index", "--binary", "--abbrev", "--no-ext-diff",
             "--no-textconv", "--exit-code", "--quiet", "-q", "--function-context", "-W", "--no-prefix", "--relative",
             "--no-relative", "--pickaxe-all", "--pickaxe-regex", "--ignore-submodules", "--submodule", "-S", "-G", "-U"}
_GIT_DIFF_VALUES = {"--stat", "--diff-filter", "--unified", "--word-diff", "--word-diff-regex", "--color", "--abbrev",
                    "--diff-algorithm", "--inter-hunk-context", "--relative", "--src-prefix", "--dst-prefix",
                    "--line-prefix", "--ignore-submodules", "--submodule", "--find-renames", "--find-copies",
                    "--find-object", "--dirstat", "--color-moved", "--color-moved-ws", "--ws-error-highlight", "--anchored"}
_GIT_DIFF_SHORT = r"-U\d+|-[MCB]\d+%?|-[SG].+|-\d+|-n\d+|-L.*"
GIT_LOOKS = {  # sub-command: (its options, its options that take =value, its other short forms)
    "status": ({"-s", "--short", "-b", "--branch", "--porcelain", "--long", "-v", "-vv", "--verbose", "-u",
                "--untracked-files", "--ignored", "-z", "--show-stash", "--ahead-behind", "--no-ahead-behind",
                "--renames", "--no-renames", "--find-renames", "--column", "--no-column", "--ignore-submodules"},
               {"--porcelain", "--untracked-files", "--ignored", "--find-renames", "--column", "--ignore-submodules"},
               r"-u(?:no|all|normal)|-M\d+%?"),
    "log": (_GIT_REVS | _GIT_SHOWN | _GIT_DIFF | {"-L"}, _GIT_REV_VALUES | _GIT_SHOWN_VALUES | _GIT_DIFF_VALUES,
            _GIT_DIFF_SHORT),
    "show": (_GIT_REVS | _GIT_SHOWN | _GIT_DIFF, _GIT_REV_VALUES | _GIT_SHOWN_VALUES | _GIT_DIFF_VALUES, _GIT_DIFF_SHORT),
    "diff": (_GIT_DIFF, _GIT_DIFF_VALUES, r"-U\d+|-[MCB]\d+%?|-[SG].+"),
    "describe": ({"--tags", "--all", "--long", "--always", "--contains", "--exact-match", "--first-parent", "--debug",
                  "--dirty", "--broken"}, {"--dirty", "--broken", "--abbrev", "--candidates", "--match", "--exclude"}, ""),
    "ls-files": ({"-c", "--cached", "-d", "--deleted", "-m", "--modified", "-o", "--others", "-i", "--ignored", "-s",
                  "--stage", "-u", "--unmerged", "-k", "--killed", "-z", "-t", "-v", "-f", "--full-name", "--directory",
                  "--no-empty-directory", "--exclude-standard", "--error-unmatch", "--recurse-submodules", "--abbrev",
                  "--debug", "--eol", "--deduplicate", "--sparse", "-x", "--exclude"},
                 {"--abbrev", "--format", "--exclude", "--exclude-per-directory", "--with-tree"}, ""),
    "blame": ({"-b", "--root", "--show-stats", "-l", "-t", "-s", "-n", "--show-number", "-f", "--show-name", "-p",
               "--porcelain", "--line-porcelain", "--incremental", "-c", "-e", "--show-email", "-w", "-M", "-C",
               "--first-parent", "--color-lines", "--color-by-age", "--minimal", "--progress", "--no-progress", "-L",
               "--reverse"}, {"--abbrev", "--date", "--since", "--encoding", "--ignore-rev"}, r"-[MC]\d+|-L.+"),
    "shortlog": (_GIT_REVS | {"-s", "--summary", "--numbered", "-e", "--email", "-c", "--committer", "-w"},
                 _GIT_REV_VALUES | {"--format", "--group"}, r"-w[\d,]+|-\d+|-n\d+"),
}
# hc 0.1.18: the verbs that only print, with the options their --help lists (--json goes anywhere). Every other
# verb types, sends, runs, kills, labels or waits. read --lines N may scroll an idle full-screen app, then restore it.
HC_READS = {"ls": {"--all", "--label"}, "get": set(), "read": {"--lines"}, "templates": set(),
            "server status": set(), "machine ls": set()}
HC_VALUE = {"--label", "--lines"}  # options that take a value
HC_TARGET = {"get", "read"}  # verbs that take one terminal
SSH_KEYS = {"batchmode", "connecttimeout", "serveraliveinterval", "serveralivecountmax", "connectionattempts",
            "loglevel", "identitiesonly", "user", "port"}
SSH_VALUE = "bceilmopBDEFIJLOPQRSwW"  # OpenSSH options that take a value
# A reader written as its path in a standard bin folder is that reader (on the Mac, ssh's PATH lacks ~/.local/bin).
BINS = re.compile(r"^(?:/bin|/usr/bin|/usr/local/bin|/opt/homebrew/bin|(?:~|\x00)/\.local/bin)/([\w.+-]+)$")
SYSTEMCTL_READS = {"is-active", "is-enabled", "is-failed", "status", "show", "list-units", "list-timers"}
# journalctl options that only choose what to print (no --vacuum…, --rotate, --flush, --follow…)
JOURNAL = {"--user", "--system", "-u", "--unit", "--user-unit", "-n", "--lines", "-S", "--since", "-U", "--until",
           "-p", "--priority", "-g", "--grep", "--case-sensitive", "-o", "--output", "-b", "--boot", "-r", "--reverse",
           "-q", "--quiet", "--no-pager", "-x", "--catalog", "-k", "--dmesg", "-t", "--identifier", "--no-hostname",
           "-a", "--all", "--utc"}
HOSTNAME = {"-s", "-f", "-d", "-i", "-I", "-a", "-A", "--short", "--fqdn", "--long", "--domain", "--ip-address",
            "--all-ip-addresses", "--alias", "--all-fqdns"}
# hermes logs [name] prints the end of a log and stops; -f follows it and never ends. Exact names: argparse would
# read --fol as --follow.
HERMES_LOGS = {"-n", "--lines", "--level", "--session", "--since", "--component"}

OPERATORS = ("<<<", "<<-", "&>>", ">>", "<<", ">&", "<&", "<>", ">|", "&>", "&&", "||", "|&", ";;", "|", "&", ";",
             "<", ">", "(", ")", "\n")
SEPARATORS = {"|", "|&", "||", "&&", ";", "&", "\n"}
REDIRECT = re.compile(r"^(\d*)(<<<|<<-|&>>|>>|<<|>&|<&|<>|>\||&>|<|>)$")


class Unknown(Exception):
    """A form the reader does not classify: a write."""


@dataclass
class Word:
    text: str  # quotes removed; EXPANSION where a variable or a substitution stood
    quoted: tuple  # per character of text: True when it was quoted
    names: tuple = ()  # the variables it expands
    subs: tuple = ()  # the command lines of its substitutions

    @property
    def fixed(self) -> bool:
        """Nothing in it can become an option: no variable but $HOME (a path), no substitution, no leading glob
        (*.txt could match a file named -o), no glob in an option (-ex* could match a file named -exec)."""
        globbed = any(c in "*?[" and not q for c, q in zip(self.text, self.quoted))
        lead = self.text[:1] in ("*", "?", "[", "-") and globbed
        return not self.subs and set(self.names) <= {"HOME"} and not lead


def reads_only(line: str, depth: int = 0) -> bool:
    """Every command of the line only reads."""
    return not why(line, depth)


def why(line: str, depth: int = 0) -> str:
    """Why the line may write: the first command or form that makes it so, said so a model can fix it; "" when every
    command of the line only reads."""
    if EXPANSION in line or MARK in line:
        return "control characters in the line"  # the reader's own marks, written in the line: it would misread them
    return _why_line(line, depth)


def _why_line(line: str, depth: int) -> str:
    try:
        for words, writes, subs in _commands(_marked(line)):
            found = _why(words, writes, subs, depth)
            if found:
                return found
        return ""
    except Unknown as exc:
        return str(exc) or "a form the reader cannot read"


def uses(line: str, name: str, verb: str | None = None, depth: int = 0) -> bool:
    """Whether a command of the line is name (with verb, its first word that is no option: hc read): its
    substitutions and an ssh's remote command included. A line it cannot read uses it, for all it knows."""
    try:
        commands = _commands(_marked(line))
    except Unknown:
        return True
    for words, _, subs in commands:
        inner = list(subs) + [s for w in words for s in w.subs]
        head = _name(words[0]) if words else None
        if head == "ssh":
            found = _ssh([w.text for w in words[1:]], strict=False)
            inner += [" ".join(found[1])] if found and found[1] else []
        said = next((w.text for w in words[1:] if not w.text.startswith("-")), None)
        if head == name and verb in (None, said):
            return True
        if depth < DEPTH and any(uses(s, name, verb, depth + 1) for s in inner):
            return True
    return False


def invocations(line: str, name: str, depth: int = 0) -> list | None:
    """The words after name of each command of the line that is name, found as uses() finds them (substitutions
    and an ssh's remote command included); None when the line cannot be read."""
    try:
        commands = _commands(_marked(line))
    except Unknown:
        return None
    found = []
    for words, _, subs in commands:
        inner = list(subs) + [s for w in words for s in w.subs]
        head = _name(words[0]) if words else None
        if head == "ssh":
            remote = _ssh([w.text for w in words[1:]], strict=False)
            inner += [" ".join(remote[1])] if remote and remote[1] else []
        if head == name:
            found.append([w.text for w in words[1:]])
        for sub in inner if depth < DEPTH else ():
            more = invocations(sub, name, depth + 1)
            if more is None:
                return None
            found += more
    return found


# Their first word that is no option (or the one after -e) is a program or a pattern, never a path it acts on.
PROGRAMS = {"sed", "awk", "gawk", "jq", "grep", "egrep", "fgrep", "rg"}


def operands(line: str, depth: int = 0) -> list | None:
    """The words a line acts on, as written (a template is MARK, a variable EXPANSION): each command's words after
    its name but the program of sed, awk, jq or grep, and what its redirections name; its substitutions and an
    ssh's remote command included. None when the line cannot be read."""
    try:
        tokens, _ = _lex(_marked(line))
    except Unknown:
        return None
    found, words, named, inner, i = [], [], [], [], 0
    while i <= len(tokens):
        token = tokens[i] if i < len(tokens) else "\n"
        i += 1
        if isinstance(token, Word):
            words.append(token)
            continue
        if REDIRECT.match(token) and i < len(tokens) and isinstance(tokens[i], Word):
            named.append(tokens[i].text)
            inner += tokens[i].subs
            i += 1
            continue
        inner += [s for w in words for s in w.subs]
        head = _name(words[0]) if words else None
        rest = [w.text for w in words[1:]]
        if head == "ssh":
            remote = _ssh(rest, strict=False)
            inner += [" ".join(remote[1])] if remote and remote[1] else []
            rest = []
        program, given, skip = head in PROGRAMS, False, False
        for text in rest:
            if skip:
                skip = False
            elif program and text in ("-e", "--expression", "--regexp"):
                skip = given = True
            elif program and not given and not text.startswith("-"):
                given = True
            else:
                found.append(text)
        found += named
        words, named = [], []
    for sub in inner if depth < DEPTH else ():
        found += operands(sub, depth + 1) or []
    return found


REMOTE_MARK = re.compile(MARK + r"(\d+)" + MARK)


def remote(line: str) -> set | None:
    """The templates of a line (0 for its first {…}) that an ssh sends to the remote shell, which reads their value
    again as code: those in an ssh command's words, a $( ) in them included, at any depth. None when the line cannot
    be read."""
    count = iter(range(len(line)))
    return _remote(TEMPLATE.sub(lambda m: f"{MARK}{next(count)}{MARK}", line), 0)


def _remote(line: str, depth: int) -> set | None:
    try:
        commands = _commands(line)
    except Unknown:
        return None
    found = set()
    for words, _, subs in commands:
        inner = list(subs) + [s for w in words for s in w.subs]
        if words and _name(words[0]) == "ssh":  # its host too: a plain word is all ssh needs
            found |= {int(n) for text in [w.text for w in words] + inner for n in REMOTE_MARK.findall(text)}
            continue
        for sub in inner:
            more = _remote(sub, depth + 1) if depth < DEPTH else None
            if more is None:
                return None
            found |= more
    return found


def interactive(line: str) -> bool:
    """An ssh with a host and no command to run, its input the terminal's: a session that waits for a person. Its
    input from a file or a pipe, a tunnel (-N, -f), a control command (-O) or -G does not wait."""
    try:
        commands, pipes = _commands(_marked(line), with_input=True)
    except Unknown:
        return False
    for (words, _, _), fed in zip(commands, pipes):
        if words and _name(words[0]) == "ssh" and not fed:
            args = [w.text for w in words[1:]]
            found = _ssh(args, strict=False)
            if found and not found[1] and not found[2] & set("NfOG"):
                return True
    return False


def _name(word: Word) -> str | None:
    """The command a word names: its text, or the reader a standard bin folder holds (/usr/bin/sort is sort,
    ~/.local/bin/hc or $HOME/.local/bin/hc is hc); None for a variable or a substitution."""
    if word.subs or (word.names and (word.names != ("HOME",) or not word.text.startswith(EXPANSION + "/"))):
        return None
    found = BINS.match(word.text) if "/" in word.text else None
    if found and (found.group(1) in PURE or found.group(1) in RULES):
        return found.group(1)
    return None if word.names else word.text


# ---- the lexer -------------------------------------------------------------

def _marked(line: str) -> str:
    """The line with each template as MARK: the runner fills them in the text as written, inside quotes or not
    ({''x} is no template, and stays text)."""
    return TEMPLATE.sub(MARK, line)


def _lex(line: str, i: int = 0, close: bool = False):
    """Words and operators ("2>" keeps its fd) of a line. With close, it reads a $( ) and stops after its ). A line
    nested so deep that Python gives up is a line the reader cannot read."""
    try:
        return _lex_line(line, i, close)
    except RecursionError:
        raise Unknown("a line nested too deep") from None


def _lex_line(line: str, i: int = 0, close: bool = False):
    tokens, chars, names, subs, started, depth = [], [], [], [], False, 0

    def end():
        nonlocal chars, names, subs, started
        if started:
            tokens.append(Word("".join(c for c, _ in chars), tuple(q for _, q in chars), tuple(names), tuple(subs)))
        chars, names, subs, started = [], [], [], False

    while i < len(line):
        c = line[i]
        if c in " \t":
            end()
            i += 1
        elif c == "\\":
            if i + 1 >= len(line):
                raise Unknown("a backslash at the end of the line")
            if line[i + 1] != "\n":  # a backslash and a newline join two lines
                chars.append((line[i + 1], True))
                started = True
            i += 2
        elif c == "'":
            j = line.find("'", i + 1)
            if j < 0:
                raise Unknown("a quote left open")
            chars += [(ch, True) for ch in line[i + 1:j]]
            started, i = True, j + 1
        elif c == '"':
            started, i = True, _double(line, i + 1, chars, names, subs)
        elif c == "$":
            started, i = True, _dollar(line, i, chars, names, subs, False)
        elif c == "`":
            started, i = True, _backtick(line, i, chars, subs, False)
        elif c == "#" and not started:
            raise Unknown("a comment (#): leave it out, the reader does not read past it")
        elif c in "|&;<>()\n":
            if close and c == ")" and depth == 0:
                end()
                return tokens, i + 1
            fd = ""
            if c in "<>" and chars and all(ch.isdigit() and not q for ch, q in chars) and not names:
                fd, chars, started = "".join(ch for ch, _ in chars), [], False  # 2>: the number names a stream
            end()
            op = next(o for o in OPERATORS if line.startswith(o, i))
            if op in ("<", ">") and line.startswith("(", i + 1):
                raise Unknown("a process substitution <( )")
            depth += {"(": 1, ")": -1}.get(op, 0)
            tokens.append(fd + op)
            i += len(op)
        else:
            chars.append((c, False))
            started, i = True, i + 1
    if close:
        raise Unknown("a $( ) left open")
    end()
    return tokens, i


def _double(line, i, chars, names, subs) -> int:
    """A double-quoted span from i: $ and backticks still expand."""
    while i < len(line):
        c = line[i]
        if c == '"':
            return i + 1
        if c == "\\" and line[i + 1:i + 2] in ("$", "`", '"', "\\", "\n"):
            if line[i + 1] != "\n":
                chars.append((line[i + 1], True))
            i += 2
        elif c == "$":
            i = _dollar(line, i, chars, names, subs, True)
        elif c == "`":
            i = _backtick(line, i, chars, subs, True)
        else:
            chars.append((c, True))
            i += 1
    raise Unknown("a double quote left open")


def _dollar(line, i, chars, names, subs, quoted) -> int:
    after = line[i + 1:i + 2]
    if line.startswith("$((", i):
        raise Unknown("$(( )): compute in jq or awk instead (jq -r '(now - .ms/1000) | floor' f is an age in seconds)")
    if after in ("'", '"') and not quoted:
        raise Unknown("$'…' or $\"…\"")
    if after == "(":
        _, j = _lex(line, i + 2, close=True)
        subs.append(line[i + 2:j - 1])
    elif after == "{":
        found = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}").match(line, i)
        if not found:
            raise Unknown("${…} with an operator: write $NAME")  # ${x:-…} can hold a substitution
        names.append(found.group(1))
        j = found.end()
    else:
        found = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*|[0-9?#@*$!-])").match(line, i)
        if not found:
            if after.strip() and after != '"':
                raise Unknown("a $ the shells read differently ($=x, $~x, $+x)")
            chars.append(("$", quoted))  # a lone $ is itself
            return i + 1
        names.append(found.group(1))
        j = found.end()
    chars.append((EXPANSION, quoted))
    return j


def _backtick(line, i, chars, subs, quoted) -> int:
    inner, j = [], i + 1
    escapes = '$`\\"' if quoted else "$`\\"
    while j < len(line):
        c = line[j]
        if c == "\\" and line[j + 1:j + 2] and line[j + 1] in escapes:
            inner.append(line[j + 1])
            j += 2
        elif c == "`":
            if MARK in inner:  # the shell finds this backtick before any quote: a value holding one gets out
                raise Unknown("a template inside backticks: write $( ) instead")
            subs.append("".join(inner))
            chars.append((EXPANSION, quoted))
            return j + 1
        else:
            inner.append(c)
            j += 1
    raise Unknown("a backtick left open")


def _braces(word: Word) -> list:
    """The words a shell makes of one word by brace expansion: {a,b} and {a..c}, unquoted only."""
    done, todo = [], [list(zip(word.text, word.quoted))]
    while todo:
        chars = todo.pop(0)
        found = _group(chars)
        if found is None:
            done.append(Word("".join(c for c, _ in chars), tuple(q for _, q in chars), word.names, word.subs))
            continue
        start, end, parts = found
        todo = [chars[:start] + part + chars[end + 1:] for part in parts] + todo
        if len(todo) + len(done) > WORDS:
            raise Unknown("a brace expansion too long to check")
    return done


def _group(chars):
    """The first {…} the shell expands, as (start, end, parts), or None."""
    for start, (c, quoted) in enumerate(chars):
        if c != "{" or quoted:
            continue
        depth, cuts = 0, []
        for j in range(start, len(chars)):
            ch, q = chars[j]
            if q:
                continue
            if ch == "{":
                depth += 1
            elif ch == "," and depth == 1:
                cuts.append(j)
            elif ch == "}":
                depth -= 1
                if depth:
                    continue
                if cuts:
                    bounds = [start] + cuts + [j]
                    return start, j, [chars[a + 1:b] for a, b in zip(bounds, bounds[1:])]
                body = chars[start + 1:j]
                steps = None if any(q for _, q in body) else _sequence("".join(c for c, _ in body))
                if steps is not None:
                    return start, j, [[(c, False) for c in step] for step in steps]
                break  # a literal {…}, such as a template: look further
    return None


def _sequence(body: str):
    found = re.fullmatch(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?|([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?", body)
    if not found:
        return None
    if found.group(1) is not None:
        low, high, step = int(found.group(1)), int(found.group(2)), abs(int(found.group(3) or 1)) or 1
        convert = str
    else:
        low, high, step = ord(found.group(4)), ord(found.group(5)), abs(int(found.group(6) or 1)) or 1
        convert = chr
    if abs(high - low) // step >= WORDS:
        raise Unknown("a brace expansion too long to check")
    return [convert(v) for v in range(low, high + (1 if high >= low else -1), step if high >= low else -step)]


def _commands(line: str, with_input: bool = False):
    """The simple commands of a line, as (words, writes, substitutions); writes: a redirection writes a file. With
    with_input, also whether each one reads its input from a pipe or a file rather than the terminal's."""
    tokens, _ = _lex(line)
    commands, words, writes, subs, used, last = [], [], False, [], False, None
    fed, inputs = False, []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if isinstance(token, Word):
            words += _braces(token)
            used = True
            continue
        if token in SEPARATORS:
            if used:
                commands.append((words, writes, subs))
                inputs.append(fed)
            elif token != "\n":
                raise Unknown(f"{token} with no command before it")
            words, writes, subs, used, last = [], False, [], False, token if used else last
            fed = token in ("|", "|&")
            continue
        redirect = REDIRECT.match(token)
        if not redirect or i >= len(tokens) or not isinstance(tokens[i], Word):
            raise Unknown("( ), ;; or a redirection without its target")
        target = tokens[i]
        i += 1
        op, used = redirect.group(2), True
        fed |= op in ("<", "<<<", "<>", "<&") and redirect.group(1) in ("", "0")
        subs += target.subs
        literal = not target.names and not target.subs
        if op in ("<<", "<<-"):
            raise Unknown("a heredoc (<<)")
        if op in ("<", "<>", "<<<") or op in (">", ">>", ">|", "&>", "&>>"):
            # bash opens a connection for /dev/tcp/host/port and /dev/udp/host/port; a target no one can read may be one
            if target.text.startswith(("/dev/tcp/", "/dev/udp/")) or target.text[:1] == EXPANSION:
                raise Unknown("a redirection to /dev/tcp or /dev/udp, or to a path built at run time, can open a "
                              "connection")
        if op in (">", ">>", ">|", "&>", "&>>"):
            writes |= not (literal and target.text == "/dev/null")
        elif op == ">&":
            writes |= not (literal and (target.text.isdigit() or target.text == "-"))  # 2>&1 joins two streams
        elif op == "<&" and not (literal and (target.text.isdigit() or target.text == "-")):
            raise Unknown("<& from a file")
        elif op == "<>":
            writes = True
    if used:
        commands.append((words, writes, subs))
        inputs.append(fed)
    elif last in ("|", "|&", "||", "&&"):
        raise Unknown("a line that ends on a pipe, && or ||")
    return (commands, inputs) if with_input else commands


def pipeline(line: str) -> list | None:
    """The commands of a line that is one pipe of two commands or more, as (name, words after it), in the order
    bash's PIPESTATUS gives their statuses; None for one command, a list (;, &&, ||, &, two lines) or a line the
    reader cannot read."""
    try:
        marked = _marked(line.strip())
        tokens, _ = _lex(marked)
        commands = _commands(marked)
    except Unknown:
        return None
    joined = False
    for token in tokens:
        if isinstance(token, Word) or token not in SEPARATORS:
            joined = False
        elif token in ("|", "|&"):
            joined = True
        elif not (token == "\n" and joined):  # a line break after | goes on with the pipe
            return None
    if len(commands) < 2:
        return None
    return [(_name(words[0]) if words else None, [w.text for w in words[1:]]) for words, _, _ in commands]


def last(line: str) -> str | None:
    """The command whose exit status is the line's (the last one, when only pipes and ; join them), or None."""
    try:
        tokens, _ = _lex(_marked(line))
        commands = _commands(_marked(line))
    except Unknown:
        return None
    if not commands or any(t in ("&&", "||", "&") for t in tokens if isinstance(t, str)):
        return None
    return _name(commands[-1][0][0]) if commands[-1][0] else None


# ---- the commands ----------------------------------------------------------

def _why(words, writes, subs, depth) -> str:
    """Why one simple command may write, or "" when it only reads."""
    if writes:
        return "a redirection to a file writes it: send output only to /dev/null, or join streams with 2>&1"
    if depth > DEPTH:
        raise Unknown("substitutions and ssh hops nested too deep")
    for inner in list(subs) + [s for w in words for s in w.subs]:
        # Its text is cut from the marked line: a MARK there is a template, one quoted word as outside it (inside a
        # $( ) only: _backtick refuses one). The line itself was checked for the reader's marks once, before marking.
        found = _why_line(inner, depth + 1)
        if found:
            return f"inside $( ): {found}"
    if not words:
        return ""  # only a redirection that writes nothing: < file
    head, args = _name(words[0]), words[1:]
    if head is None:
        return "a variable or a $( ) as the command"
    if head in PURE:
        return ""
    rule = RULES.get(head)
    if rule is None:
        return _stranger(head)
    if head in ("test", "["):  # its operands are compared, never run: a variable is fine unless it is read as arithmetic
        return "" if _test([w.text for w in args], depth) else WHY[head]
    # Every argument of a reader with options that write must be known now: a $X could hold -o.
    loose = next((w for w in args if not w.fixed), None)
    if loose is not None:
        return _loose(head, loose)
    texts = [w.text for w in args]
    if head == "ssh":
        return _ssh_why(texts, depth)
    if head == "find":
        found = sorted(FIND_WRITES & set(texts))
        return f"find {found[0]} acts on what it finds: list with find here, act in a next step" if found else ""
    return "" if rule(texts, depth) else WHY[head]


def _stranger(head: str) -> str:
    if MARK in head:
        return "a template as the command: write the command itself, a value only in its arguments"
    if head == "cd":
        return "cd is not a reader: give the folder to the command itself (ls ~/dir, find ~/dir, git -C ~/dir)"
    if "/" in head:
        return (f"{head} is a path used as the command: only a reader in /bin, /usr/bin, /usr/local/bin, "
                "/opt/homebrew/bin or ~/.local/bin counts")
    if "=" in head:
        return f"{head.partition('=')[0]}=… sets a variable for the command"
    return f"{head} is not a known reader"


def _loose(head: str, word: Word) -> str:
    """Why an argument is not known before the run."""
    if word.subs:
        return f"a $( ) in an argument of {head}: its output could be an option"
    names = sorted(set(word.names) - {"HOME"})
    if names and re.search(r"TOKEN|KEY|SECRET|PASSWORD", names[0], re.I):
        # Hermes strips its secrets from every terminal command: the value is empty there, and never belongs in a file
        return (f"${names[0]} is a secret: Hermes strips secrets from terminal commands, so it is empty there; never "
                "write it in a habit, check that service another way")
    if names:
        return f"${names[0]} in an argument of {head}: its value could be an option; write the value itself"
    shown = word.text.replace(MARK, "{…}").replace(EXPANSION, "$…")
    if MARK in word.text:
        return (f"an unquoted glob next to a template ({shown}) in {head}: give the whole pattern as an input "
                f"(-iname {{pattern}}), or filter after with | grep -i {{query}}")
    return (f"an unquoted glob at the start of an argument of {head} ({shown}): a file named like an option could "
            f"fill it; quote it, as -name '*.md'")


def _ssh_why(args, depth) -> str:
    found = _ssh(args, strict=True)
    if not found:
        return WHY["ssh"]
    if not found[1]:
        return "an ssh without a remote command waits for a person"
    words = list(found[1])
    if words[0].startswith(".local/bin/"):
        words[0] = "~/" + words[0]  # the remote shell starts in $HOME: ~/.local/bin/hc is hc
    line = " ".join(words)  # ssh joins its words and the remote shell reads them again
    if EXPANSION in line:
        return "a variable in the ssh remote command: the remote shell would read its value again, as code"
    # A template there is one plain word: the runner refuses any other value it would fill in (remote()).
    inner = _why_line(line, depth + 1)
    return f"in the ssh remote command: {inner}" if inner else ""


WHY = {  # what makes each reader with rules a write, when its rule refuses it
    "curl": "curl beyond a plain GET: only -s, -S, -L, -f, -I, -m, -A, -H, -r, -w, -o /dev/null, -X GET or HEAD, "
            "and URLs (no @file, no {value} after -H or -w)",
    "tail": "tail -f, -F or --follow never ends: read the end with tail -n", "sort": "sort -o writes a file", "uniq": "uniq with two files writes the second",
    "date": "date with an operand sets the clock: write date +%F", "hostname": "hostname with a name sets it",
    "sleep": "sleep longer than 10 s", "file": "file -C writes a magic file", "env": "env with a command runs it",
    "printf": "printf -v sets a variable",
    "test": "test or [ with -v, -R, or an operand of -eq, -lt… that is not a plain number: bash reads it as arithmetic "
            "and runs a substitution in it",
    "[": "test or [ with -v, -R, or an operand of -eq, -lt… that is not a plain number: bash reads it as arithmetic "
         "and runs a substitution in it", "sed": "sed beyond printing: -i, -f, and w, e, a, i, c, r or labels",
    "awk": "awk with -f, system(), a pipe, @load or a print > file",
    "git": "git beyond status, log, diff, show, blame, describe, ls-files, shortlog, rev-parse, listing branch and "
           "tag, remote -v and config --get, with the options that only choose what to print (written --name=value): "
           "no -c, --output, --ext-diff, --textconv, --show-signature, %G in a format, pager or option of another kind",
    "hc": "hc beyond ls (--all, --label), get <t>, read <t> (--lines), templates, server status and machine ls, "
          "each with --json",
    "ssh": "ssh with options other than -o Key=value (BatchMode, ConnectTimeout…), -p, -i, -q, -T, -n",
    "systemctl": "systemctl beyond is-active, is-enabled, is-failed, status, show, list-units and list-timers",
    "journalctl": "journalctl with an option that writes or never ends (--vacuum…, --rotate, --flush, -f)",
    "hermes": "hermes beyond logs [name] with -n, --level, --session, --since or --component (-f never ends)",
    "ob": "ob beyond sync-status (--path, --json): ob sync changes the vault, run it in a step without read: true",
}


def _program(text: str) -> bool:
    """A text the command runs as code (a sed script, an awk program, a remote command): fully known now."""
    return isinstance(text, str) and EXPANSION not in text and MARK not in text


def _abbrev(given: str, *longs: str) -> bool:
    """given is one of longs, or an abbreviation GNU getopt reads as one (--out for --output)."""
    name = given.partition("=")[0]
    return len(name) > 2 and name.startswith("--") and any(long.startswith(name) for long in longs)


def _options(args, value="", optional="", longs=()):
    """getopt's reading: ([(option, value)], operands). A cluster -ab is -a -b; an option of value takes the rest of
    its cluster or the next word, one of optional only the rest of its cluster. An option after an operand still
    counts, as GNU reads it (the other way round would only find fewer)."""
    found, operands, i = [], [], 0
    while i < len(args):
        word = args[i]
        i += 1
        if word == "--":
            operands += args[i:]
            break
        if word.startswith("--"):
            name, eq, text = word.partition("=")
            if not eq and name in longs:
                text, i = (args[i] if i < len(args) else ""), i + 1
            found.append((name, text if eq or name in longs else None))
        elif word.startswith("-") and len(word) > 1:
            for k, ch in enumerate(word[1:], 1):
                if ch in value:
                    rest = word[k + 1:]
                    if not rest:
                        rest, i = (args[i] if i < len(args) else ""), i + 1
                    found.append(("-" + ch, rest))
                    break
                if ch in optional:
                    found.append(("-" + ch, word[k + 1:] or None))
                    break
                found.append(("-" + ch, None))
        else:
            operands.append(word)
    return found, operands


def _find(args, depth):
    return not FIND_WRITES & set(args)


def _curl(args, depth):
    waiting = None  # the option whose value is the next word
    for word in args:
        if waiting:
            if not _curl_value(waiting, word):
                return False
            waiting = None
        elif word.startswith("-"):
            flag, equals, value = word.partition("=")
            combined = len(flag) > 2 and flag[1] != "-" and set(flag[1:]) <= set("sSLfI")
            if flag not in CURL_READ and not combined:
                return False
            if flag in CURL_VALUE and equals and not _curl_value(flag, value):
                return False
            waiting = flag if flag in CURL_VALUE and not equals else None
        elif not word.startswith(("http://", "https://", MARK)):
            return False  # one URL per word; anything else could be a file
    return not waiting


def _curl_value(flag: str, value: str) -> bool:
    """-H @file sends a local file, -w @file reads a format from one, -w '%output{…}' writes a file, -o writes one
    unless it is /dev/null, -X sends another method. A {template} there is filled at run time, maybe with @file."""
    if flag in ("-H", "--header", "-w", "--write-out") and value.startswith(("@", "{", MARK)):
        return False
    if flag in ("-o", "--output"):
        return value == "/dev/null"
    if flag in ("-X", "--request"):
        return value in ("GET", "HEAD")
    return not (flag in ("-w", "--write-out") and "%output" in value.lower())


def _tail(args, depth):
    found, _ = _options(args, value="ncsb", longs=("--lines", "--bytes", "--sleep-interval", "--pid",
                                                   "--max-unchanged-stats"))
    return not any(o in ("-f", "-F") or o == "--follow" or _abbrev(o, "--follow") for o, _ in found)


def _sort(args, depth):
    found, _ = _options(args, value="kotTS")
    return not any(o == "-o" or _abbrev(o, "--output", "--compress-program") for o, _ in found)


def _uniq(args, depth):
    _, operands = _options(args, value="fsw", longs=("--skip-fields", "--skip-chars", "--check-chars"))
    return len(operands) <= 1  # uniq in out writes out


def _date(args, depth):
    found, operands = _options(args, value="dfrvz", optional="I", longs=("--date", "--file", "--reference"))
    if any(o in ("-s", "-t") or _abbrev(o, "--set") for o, _ in found):
        return False
    parse = any(o == "-j" for o, _ in found)  # BSD: read the operand, never set the clock with it
    # date 0101 sets the clock: an operand is a +format, or with -j the date to read.
    return all(_program(op) and (op.startswith("+") or parse) for op in operands)


def _hostname(args, depth):
    return all(word in HOSTNAME for word in args)  # hostname name sets it


def _sleep(args, depth):
    total = 0.0
    for word in args:
        found = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", word)
        if not found:
            return False
        total += float(found.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[found.group(2)]
    return bool(args) and total <= 10


def _file(args, depth):
    found, _ = _options(args, value="efFmP", longs=("--exclude", "--files-from", "--separator", "--magic-file",
                                                    "--parameter"))
    return not any(o == "-C" or _abbrev(o, "--compile") for o, _ in found)  # file -C writes a magic file


def _printf(args, depth):
    return not any(word.startswith("-v") for word in args)  # printf -v PATH … sets a variable of the shell


def _env(args, depth):
    return not args  # env alone prints; env with a command runs it


SED_FLAGS = set("nErsuz")
SED_LONG = {"--quiet", "--silent", "--regexp-extended", "--separate", "--unbuffered", "--null-data", "--posix",
            "--sandbox", "--debug", "--expression"}


def _sed(args, depth):
    found, operands = _options(args, value="e", longs=("--expression",))
    for option, _ in found:
        if option not in SED_LONG and not (len(option) == 2 and option[1] in SED_FLAGS | {"e"}):
            return False  # -i and -I edit in place, -f reads a script we cannot see, and the rest is unknown
    scripts = [v for o, v in found if o in ("-e", "--expression")] or operands[:1]
    return bool(scripts) and all(_program(s) and _sed_reads(s) for s in scripts)


def _sed_reads(script: str) -> bool:
    """A sed script made only of commands that print or edit the stream: p, d, s without w or e, y, q, ranges and
    blocks. a, i, c, r, w, e, labels and comments are refused, so the text is never misread."""
    i, depth, n = 0, 0, len(script)
    while True:
        while i < n and script[i] in " \t\n;":
            i += 1
        if i >= n:
            return depth == 0
        i = _sed_address(script, i)
        while 0 <= i < n and script[i] in " \t!":
            i += 1
        if i < 0 or i >= n:
            return False
        command = script[i]
        i += 1
        if command == "{":
            depth += 1
            continue
        if command == "}":
            depth -= 1
            if depth < 0:
                return False
        elif command in "pPdDnNgGhHxz=":
            pass
        elif command in "qQl":
            while i < n and script[i].isdigit():
                i += 1
        elif command in "sy":
            delimiter = script[i:i + 1]
            if not delimiter or delimiter in "\\\n[]":
                return False
            i = _sed_regex(script, i + 1, delimiter) if command == "s" else _sed_text(script, i + 1, delimiter)
            i = _sed_text(script, i, delimiter) if i >= 0 else -1
            if i < 0:
                return False
            while command == "s" and i < n and script[i] in "gpiImM0123456789":
                i += 1  # w (write a file) and e (run a command) stop here, and are refused below
        else:
            return False
        while i < n and script[i] in " \t":
            i += 1
        if i < n and script[i] not in ";\n}":
            return False


def _sed_address(script: str, i: int) -> int:
    j = _sed_one(script, i)
    if j <= i or not script.startswith(",", j):
        return j
    step = re.compile(r"[+~]\d+").match(script, j + 1)
    if step:
        return step.end()
    k = _sed_one(script, j + 1)
    return k if k > j + 1 else -1


def _sed_one(script: str, i: int) -> int:
    """The end of one address at i (i itself when there is none), or -1."""
    found = re.compile(r"\d+(?:~\d+)?|\$").match(script, i)
    if found:
        return found.end()
    if script.startswith("/", i):
        delimiter, start = "/", i + 1
    elif script.startswith("\\", i) and script[i + 1:i + 2] and script[i + 1] not in "\\\n[]":
        delimiter, start = script[i + 1], i + 2
    else:
        return i
    j = _sed_regex(script, start, delimiter)
    while 0 <= j < len(script) and script[j] in "IM":
        j += 1
    return j


def _sed_regex(script: str, i: int, delimiter: str) -> int:
    """The index after the delimiter that ends a regex at i, or -1. A delimiter inside [ ] is refused: sed
    versions disagree on it."""
    while i < len(script):
        c = script[i]
        if c == "\\":
            i += 2
        elif c == "\n":
            return -1
        elif c == delimiter:
            return i + 1
        elif c == "[":
            i = _bracket(script, i, delimiter)
            if i < 0:
                return -1
        else:
            i += 1
    return -1


def _bracket(script: str, i: int, delimiter: str) -> int:
    j = i + 1
    if script[j:j + 1] == "^":
        j += 1
    if script[j:j + 1] == "]":
        j += 1
    while j < len(script):
        c = script[j]
        if c == "]":
            return j + 1
        if c == "[" and script[j + 1:j + 2] in (":", ".", "="):
            end = script.find(script[j + 1] + "]", j + 2)
            if end < 0 or delimiter in script[j:end]:
                return -1
            j = end + 2
            continue
        if c in (delimiter, "\n"):
            return -1
        j += 1
    return -1


def _sed_text(script: str, i: int, delimiter: str) -> int:
    """The index after the delimiter that ends a replacement at i, or -1."""
    while i < len(script):
        c = script[i]
        if c == "\\":
            i += 2
        elif c == "\n":
            return -1
        elif c == delimiter:
            return i + 1
        else:
            i += 1
    return -1


def _awk(args, depth):
    found, operands = _options(args, value="Fv")
    if any(o not in ("-F", "-v") for o, _ in found) or not operands:
        return False  # -f reads a program we cannot see; gawk's -e, -i, -l, -E load more
    program = operands[0]
    if not _program(program) or any(word in program for word in ("system", "|", "@")):
        return False  # system( ) and pipes run commands, @load and @include load code
    # print > file writes: a > after the first print may be one (compare with < instead, or leave print out).
    first = program.find("print")
    return first < 0 or ">" not in program[first:]


def _git(args, depth):
    i, set_on_the_line = 0, False
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("--no-pager", "--no-optional-locks"):
            i += 1
            continue
        if args[i] not in ("-C", "-c") or i + 1 >= len(args):
            return False  # --paginate, -p, --exec-path, --git-dir…: a pager or another git
        if args[i] == "-c":
            if not (GIT_KEYS.match(args[i + 1]) and _program(args[i + 1])):
                return False  # -c diff.external=…, core.pager=…, alias.x=… and the like run commands
            set_on_the_line = True
        i += 2
    if i >= len(args):
        return False
    sub, rest = args[i], args[i + 1:]
    if sub in GIT_LOOKS:
        return not set_on_the_line and _git_look(sub, rest)
    if sub not in GIT_READS:
        return False
    if any(word.startswith("--output") or _abbrev(word, "--output", "--ext-diff") for word in rest):
        return False
    rule = {"branch": _git_branch, "tag": _git_tag, "remote": _git_remote, "config": _git_config}.get(sub)
    return rule(rest) if rule else True


def _git_look(sub: str, rest: list) -> bool:
    """Every option of a git status, log, diff, show, describe, ls-files, blame or shortlog is one of GIT_LOOKS. A
    word that starts with no dash is a revision or a path, which only chooses what is shown; so is every word after
    --. An option's value written apart (-n 5, -S word) is such a word; one that starts with a dash is read as an
    option, and refused unless it is listed. A format is refused when it holds %G (git runs gpg for it) or a
    template (its value is not known now)."""
    flags, values, shorts = GIT_LOOKS[sub]
    for word in rest:
        if word == "--":
            return True
        if not word.startswith("-") or word == "-":
            continue
        name, equals, value = word.partition("=")
        if word in flags:
            continue
        if equals and name in values:
            if name in ("--format", "--pretty") and (not _program(value) or "%G" in value):
                return False
            continue
        if shorts and re.fullmatch(shorts, word):
            continue
        if not word.startswith("--") and len(word) > 2 and all("-" + letter in flags for letter in word[1:]):
            continue  # -sb, -co: a cluster of its one-letter options
        return False
    return True


def _listing(rest, flags, values, listing=("-l", "--list")) -> bool:
    """Options among flags or values (written --x=…), and operands only after a listing option: git branch x and
    git tag x create x."""
    listed = False
    for word in rest:
        if word in listing:
            listed = True
        if word.startswith("-"):
            name = word.partition("=")[0]
            if word not in flags and not (name in values and "=" in word):
                return False
        elif not listed:
            return False
    return True


def _git_branch(rest):
    return _listing(rest, {"-a", "--all", "-r", "--remotes", "-l", "--list", "-v", "-vv", "--verbose",
                           "--show-current", "--no-color", "-i", "--ignore-case", "--no-column", "--omit-empty"},
                    {"--color", "--sort", "--format", "--contains", "--no-contains", "--merged", "--no-merged",
                     "--points-at", "--column"})


def _git_tag(rest):
    flags = {"-l", "--list", "-i", "--ignore-case", "--no-column", "--omit-empty"}
    flags |= {w for w in rest if re.fullmatch(r"-n\d*", w)}  # -n3: lines of each annotation
    return _listing(rest, flags, {"--sort", "--format", "--contains", "--no-contains", "--points-at", "--merged",
                                  "--no-merged", "--column", "--color"})


def _git_remote(rest):
    return all(word in ("-v", "--verbose") for word in rest)


def _git_config(rest):
    modes = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l"}
    flags = modes | {"--global", "--local", "--system", "--worktree", "--show-origin", "--show-scope", "--name-only",
                     "--null", "-z", "--bool", "--int", "--path", "--includes", "--no-includes"}
    values = {"--file", "--type", "--default", "--blob"}
    operands = [w for w in rest if not w.startswith("-")]
    for word in rest:
        if word.startswith("-") and word not in flags and not (word.partition("=")[0] in values and "=" in word):
            return False
    return bool(modes & set(rest)) or operands[:1] in (["get"], ["list"])  # git 2.46: git config get <key>


def _hc(args, depth):
    rest = [word for word in args if word != "--json"]
    verb = " ".join(rest[:2]) if " ".join(rest[:2]) in HC_READS else " ".join(rest[:1])
    if verb not in HC_READS:
        return False
    rest, operands, i = rest[len(verb.split()):], [], 0
    while i < len(rest):
        name, equals, _ = rest[i].partition("=")
        if name in HC_READS[verb]:
            i += 2 if name in HC_VALUE and not equals else 1
            if i > len(rest):
                return False  # an option without its value
        elif rest[i].startswith("-"):
            return False
        else:
            operands.append(rest[i])
            i += 1
    return len(operands) <= (verb in HC_TARGET)


def _systemctl(args, depth):
    found, operands = _options(args, value="pnt", longs=("--property", "--lines", "--type", "--state"))
    flags = {"--user", "--system", "--no-pager", "-q", "--quiet", "-a", "--all", "--full", "-l", "--plain",
             "--no-legend", "--failed", "--value", "-p", "--property", "-n", "--lines", "-t", "--type", "--state"}
    return bool(operands) and operands[0] in SYSTEMCTL_READS and all(o in flags for o, _ in found)


def _journalctl(args, depth):
    found, _ = _options(args, value="uSUpgot", optional="nb",
                        longs=("--unit", "--user-unit", "--since", "--until", "--priority", "--grep", "--output",
                               "--identifier"))
    return all(o in JOURNAL for o, _ in found)  # its operands are matches (_PID=1), which only filter


HABITS_READS = {"check": (1, ()), "show": (1, ("--json",)), "list": (0, ())}  # verb: (operands, options)


def _hermes(args, depth):
    if args[:1] == ["habits"] and args[1:2] and args[1] in HABITS_READS:
        # hermes habits check <file>, show <name> [--json], list print and change nothing: the guard lets the model
        # check a habit's own file (run, view, tick, retire, reset act or open a page)
        count, options = HABITS_READS[args[1]]
        rest = args[2:]
        return all(not w.startswith("-") or w in options for w in rest) and len(
            [w for w in rest if not w.startswith("-")]) == count
    if args[:1] != ["logs"]:
        return False  # hermes gateway restart, hermes send, hermes habits run… act
    found, operands = _options(args[1:], value="n", longs=tuple(o for o in HERMES_LOGS if o.startswith("--")))
    return len(operands) <= 1 and all(o in HERMES_LOGS for o, _ in found)


def _ob(args, depth):
    # ob sync-status prints a vault's sync settings (no key, no network); ob sync and the rest change the vault
    found, operands = _options(args[1:], longs=("--path",))
    return args[:1] == ["sync-status"] and not operands and all(o in ("--path", "--json") for o, _ in found)


def _ssh_read(args, depth):
    return not _ssh_why(args, depth)


def _ssh(args, strict: bool):
    """(host, the remote command's words, the option letters) of an ssh, or None. strict: only -o (known keys), -p, -i, -q, -T and -n,
    the options a read needs; else any option, to tell whether a command follows the host."""
    i, seen = 0, set()
    while i < len(args) and args[i].startswith("-") and args[i] != "-":
        if args[i] == "--":
            i += 1
            break
        flags, i = args[i][1:], i + 1
        for k, ch in enumerate(flags):
            seen.add(ch)
            if ch in SSH_VALUE:
                value = flags[k + 1:]
                if not value:
                    if i >= len(args):
                        return None
                    value, i = args[i], i + 1
                if strict and not (ch == "i" or (ch == "p" and value.isdigit()) or (ch == "o" and _ssh_key(value))):
                    return None
                break
            if strict and ch not in "qTn":
                return None
    if i >= len(args):
        return None
    return args[i], args[i + 1:], seen


def _ssh_key(value: str) -> bool:
    key, eq, text = value.partition("=")
    if not eq:
        key, _, text = value.partition(" ")
    return key.strip().lower() in SSH_KEYS and _program(text)  # ProxyCommand, LocalCommand… run commands here


ARITHMETIC = {"-eq", "-ne", "-lt", "-le", "-gt", "-ge"}


def _test(texts: list, depth: int) -> bool:
    """test and [ read files and compare, but bash 4.3+ reads the operand of -v, -R and of -eq, -ne, -lt, -le, -gt,
    -ge as an arithmetic expression, and a subscript there (a[$(cmd)]) runs the command, however it is quoted."""
    if any(t in ("-v", "-R") or "$(" in t or "`" in t for t in texts):
        return False
    if ARITHMETIC & set(texts):
        number = re.compile(r"[+-]?\d+")
        for i, t in enumerate(texts):
            if t in ARITHMETIC and not all(0 <= j < len(texts) and number.fullmatch(texts[j]) for j in (i - 1, i + 1)):
                return False
    return True


RULES = {"test": _test, "[": _test, "find": _find, "tail": _tail, "curl": _curl, "sort": _sort, "uniq": _uniq, "date": _date, "hostname": _hostname,
         "sleep": _sleep, "file": _file, "env": _env, "printf": _printf, "sed": _sed, "awk": _awk, "git": _git, "hc": _hc,
         "ssh": _ssh_read, "systemctl": _systemctl, "journalctl": _journalctl, "hermes": _hermes, "ob": _ob}
