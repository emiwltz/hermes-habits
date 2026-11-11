#!/usr/bin/env bash
# The dream on a real Hermes, from nothing: install this checkout in a throwaway home, let Hermes create its state.db,
# write a synthetic history into it, then run `hermes habits dream`. One PASS / FAIL line per check; exit 1 on a FAIL.
# Never touches the everyday home. Not collected by pytest. Usage: tests/e2e/dream_e2e.sh [checkout]  (default: this one)
# Needs the checkout committed (the install takes HEAD) and `hermes` on the PATH.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
CHECKOUT="$(cd "${1:-$HERE/../..}" && pwd)"
STAMP="$(date +%H%M%S)"
BASE="${E2E_BASE:-$HOME/goinfre/habits-nuit}"
A="$BASE/e2e-$STAMP"          # the home with a history
B="$BASE/e2e-$STAMP-empty"    # a fresh home with none
W="$(mktemp -d "${TMPDIR:-/tmp}/dream-e2e.XXXXXX")"
FAILS=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; FAILS=$((FAILS + 1)); }
note() { printf 'note  %s\n' "$1"; }
check() { if "${@:2}"; then pass "$1"; else fail "$1"; fi; }   # check "name" command args...
has() { python3 "$HERE/check.py" json-has "$1" "$2"; }          # has file 'expression on d'

command -v hermes >/dev/null || { echo "FAIL  hermes is not on the PATH"; exit 1; }
if [ -n "$(git -C "$CHECKOUT" status --porcelain 2>/dev/null)" ]; then
  note "the checkout has uncommitted changes: the install takes the committed HEAD only"
fi
INSTALL_DIR="$(hermes --version 2>/dev/null | sed -n 's/^Install directory: //p')"
HPY="$INSTALL_DIR/venv/bin/python"
[ -x "$HPY" ] || { echo "FAIL  cannot find Hermes's own python ($HPY)"; exit 1; }

install_in() {   # install_in HOME
  mkdir -p "$1"
  HERMES_HOME="$1" hermes plugins install "file://$CHECKOUT" --enable --yes-deps >"$W/install.log" 2>&1 \
    && grep -q "Plugin habits enabled" "$W/install.log"
}
create_db() {    # Hermes's own SessionDB creates the file and its schema
  HERMES_HOME="$1" PYTHONPATH="$INSTALL_DIR" "$HPY" -c 'from hermes_state import SessionDB; SessionDB().close()' 2>"$W/db.log" \
    && [ -f "$1/state.db" ]
}
# The data of a store: the hash of state.db and the size of its write-ahead log (absent counts as empty). Not
# state.db-shm (the readers' shared index, which any reader touches) and not the empty lock files Hermes leaves.
hash_db() { shasum -a 256 "$1/state.db" | sed 's/ .*//'; { wc -c <"$1/state.db-wal" || echo 0; } 2>/dev/null | tr -d " "; }
probe() {        # probe HOME [args]: the dream's reader on that home's history
  local h="$1"; shift
  HERMES_HOME="$h" PYTHONPATH="$INSTALL_DIR:$h/plugins/habits" "$HPY" "$HERE/probe.py" "$@"
}

echo "== home with a history: $A"
check "install from the committed HEAD of $CHECKOUT" install_in "$A"
check "hermes habits list names the dream (E5)" bash -c "HERMES_HOME='$A' hermes habits list 2>&1 | grep -q 'hermes habits dream'"
check "Hermes creates state.db" create_db "$A"
python3 "$HERE/make_history.py" "$A/state.db" >"$W/history.log" 2>&1 && pass "synthetic history written ($(cat "$W/history.log"))" || fail "synthetic history written: $(cat "$W/history.log")"
BEFORE="$(hash_db "$A")"

START=$SECONDS
HERMES_HOME="$A" hermes habits dream --json >"$W/dream.json" 2>"$W/dream.err"
TOOK=$((SECONDS - START))
check "dream --json is one JSON object with status ok" has "$W/dream.json" 'd["status"] == "ok" and d["turns"] > 40 and d["sessions"] > 20'
check "dream took under 20 s (took ${TOOK} s)" test "$TOOK" -lt 20
check "no state in --json speaks the old words" bash -c "! grep -Eiq 'shadow|retired|promote|graph|scenario' '$W/dream.json'"

probe "$A" >"$W/probe.json" 2>"$W/probe.err" || { fail "probe ran: $(tail -2 "$W/probe.err")"; }
KEYS='[s["key"] for s in d["spotted"]]'
check "spotted: ~/bin/notes wake, two spellings, 5 turns on 5 days" has "$W/probe.json" 'any(s["key"] == "terminal:~/bin/notes wake" and s["turns"] == 5 and s["days"] == 5 for s in d["spotted"])'
check "spotted: a read that comes back (df -h ~)" has "$W/probe.json" '"terminal:df -h ~" in '"$KEYS"
check "spotted: a file read that comes back" has "$W/probe.json" '"read_file:/home/someone/proj/TODO.md" in '"$KEYS"
check "spotted: the three grouped calls behind the wrapper, separately" has "$W/probe.json" 'all(k in '"$KEYS"' for k in ("terminal:git status --short", "terminal:git log --oneline -n 3", "terminal:uptime")) and not any("tool_call" in k for k in '"$KEYS"')'
check "spotted: the scheduled job, as scheduled" has "$W/probe.json" 'any(s["key"] == "terminal:git pull" and s["scheduled"] == 1 for s in d["spotted"])'
check "chained session: 8 turns in the window, the 70-day-old one not counted" has "$W/probe.json" 'd["turns_by_session"].get("chain-1") == 8 and not any("nowhere-old" in k for k in '"$KEYS"')'
check "compression child: its first turn is read as a continuation (make lint on 3 turns)" has "$W/probe.json" 'any(s["key"] == "terminal:make lint" and s["turns"] == 3 for s in d["spotted"])'
check "sub-agents: none of their 40 calls counted" has "$W/probe.json" 'not any("echo-agent-job" in k for k in '"$KEYS"') and "sub-1" not in d["turns_by_session"] and "sub-2" not in d["turns_by_session"]'
check "private files, keys and passwords: left out as secrets, never spotted" has "$W/probe.json" 'd["left"].get("secrets") == 12 and not any(w in k for k in '"$KEYS"' for w in (".env", "hunter2", "curl"))'
check "noise left out (cd, echo)" has "$W/probe.json" 'd["left"].get("noise") == 8 and not any(k.startswith(("terminal:cd", "terminal:echo")) for k in '"$KEYS"')'
check "the blocked call that repeats is not counted" has "$W/probe.json" 'd["left"].get("failed") == 3 and not any("rm -rf" in k for k in '"$KEYS"')'
check "the dream reader read the history in under 20 s" has "$W/probe.json" 'd["seconds"] < 20 and d["skipped"] == 0'
check "the history was only read (state.db files unchanged by the dream)" test "$(hash_db "$A")" = "$BEFORE"

HERMES_HOME="$A" hermes habits dream >"$W/text-en.txt" 2>&1
check "text (English): reads, spots, names the next step" bash -c "grep -q '^Read ' '$W/text-en.txt' && grep -q 'df -h' '$W/text-en.txt' && grep -q 'Next:' '$W/text-en.txt'"
check "text (English): with the default (suggest) it gives the words to try a proposal" bash -c "grep -q 'habits try' '$W/text-en.txt'"
check "text (English) uses no word of the old vocabulary" bash -c "! grep -Eiq 'shadow|retired|promote|graph|scenario|node|gate|trigger' '$W/text-en.txt'"
HERMES_HOME="$A" hermes habits list >"$W/list-before.txt" 2>&1
HERMES_HOME="$A" hermes habits dream --days 7 >"$W/look.txt" 2>&1
HERMES_HOME="$A" hermes habits list >"$W/list-after.txt" 2>&1
check "list names every draft with its state, one per line" bash -c "grep -q '^- .* · waits for your try · ' '$W/list-before.txt' && ! grep -q 'No habits of your own yet' '$W/list-before.txt'"
check "a look (--days 7) says it is a look and what the plain command does" bash -c "grep -q 'This was a look: nothing was saved' '$W/look.txt' && grep -q 'keeps what it finds as drafts' '$W/look.txt'"
check "a look writes nothing: the list is the same before and after" cmp -s "$W/list-before.txt" "$W/list-after.txt"
printf 'plugins:\n  enabled:\n    - habits\n  entries:\n    habits:\n      settings:\n        language: fr\n' >"$A/config.yaml"
HERMES_HOME="$A" hermes habits dream >"$W/text-fr.txt" 2>&1
check "text (French): the same dream in French" bash -c "grep -q '^Lu ' '$W/text-fr.txt' && grep -q 'Ensuite' '$W/text-fr.txt'"

echo "== a fresh home with no history: $B"
check "install in a second home" install_in "$B"
HERMES_HOME="$B" hermes habits dream >"$W/empty.txt" 2>&1; EMPTY_EXIT=$?
check "no history: says so, exit 0" bash -c "test $EMPTY_EXIT -eq 0 && grep -q 'Nothing to learn from yet' '$W/empty.txt'"

echo "== the other home read from this one (--home), read only"
HERMES_HOME="$B" hermes habits dream --home "$A" --json >"$W/home.json" 2>"$W/home.err"
check "--home: the history of the first home is read from the second" has "$W/home.json" 'd["status"] == "ok" and d["turns"] > 40 and len(d["spotted"]) >= 3'
check "--home: with the default (suggest) nothing practices and the drafts wait for a try" has "$W/home.json" 'len(d["practicing"]) == 0 and len(d["spotted"]) >= 1'
check "--home: nothing was written in the other home (its state.db files are unchanged)" test "$(hash_db "$A")" = "$BEFORE"
HERMES_HOME="$B" hermes habits list >"$W/list-b.txt" 2>&1
check "--home is a look: this home got no draft from it" bash -c "! grep -q \"dream's drafts\" '$W/list-b.txt'"
HERMES_HOME="$B" hermes habits dream --home "$B/nowhere" >"$W/nowhere.txt" 2>&1; NOWHERE_EXIT=$?
check "--home on a folder that does not exist: a clear line, exit 1" bash -c "test $NOWHERE_EXIT -eq 1 && grep -q 'nowhere' '$W/nowhere.txt'"

echo "== the install scanner"
hermes plugins validate "$CHECKOUT" >"$W/validate.log" 2>&1
check "scanner: security scan safe" grep -q "security scan — safe" "$W/validate.log"
check "scanner: Validation passed." grep -q "Validation passed." "$W/validate.log"

echo
note "homes kept for a look (purged in 48 h): $A  $B"
note "logs: $W"
if [ "$FAILS" -eq 0 ]; then echo "ALL PASS"; else echo "$FAILS FAIL"; exit 1; fi
