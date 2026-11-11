# End-to-end proof on a real Hermes

Not collected by pytest. Each script uses a throwaway `HERMES_HOME` (default under `~/goinfre/habits-nuit/`) and never
touches `~/.hermes`. They need `hermes` on the PATH and a committed checkout (the install takes the committed HEAD).

- `dream_e2e.sh [checkout]` installs the plugin in a fresh home, lets Hermes create its `state.db`, writes the
  synthetic history, then checks `hermes habits dream` (text, `--json`, French), a second home with no history, and
  `--home` (read only). One `PASS` or `FAIL` line per check, `ALL PASS` at the end, exit 1 on any `FAIL`. About 1 min.
- `jury.sh [checkout] [home]` builds the standing throwaway Hermes (default `~/goinfre/habits-nuit/jury`), with the
  synthetic history in it and the small project that history happened in (`<home>-proj`, made by `make_project.sh`, with a local
  remote beside it; beside the home and not in it, because Hermes treats every path inside its home as private), and prints the command to try. It stops if the home exists (it deletes nothing).
- `make_project.sh <folder>` makes a small real git project (a `TODO.md`, a `bin/notes` script, a Makefile, a local
  remote). `make_history.py <state.db> [--base <folder>]` writes the synthetic history (standard library only): a command in two spellings on
  5 days, a file read, grouped calls behind the wrapper, a chained session, a compression child, sub-agents, a
  scheduled job, private files, noise, a blocked call. Dates are relative to now.
- `probe.py` reads a home's history with the dream's own reader and prints counts and every spotted key as JSON
  (`--home FOLDER` for another home, `--shapes` to keep only the tool and the program name). Run it with Hermes's python.
- `check.py` is the small assertion helper of `dream_e2e.sh`.

Everything in the history is invented; no real command, path or file content. State is compared by `state.db` and its
log, never by `state.db-shm` (any reader touches that shared index).
