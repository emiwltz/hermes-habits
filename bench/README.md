# Bench

Measures a habit against Hermes doing the same task alone, and against Hermes writing the habit itself.
It is a development tool: Hermes does not load it. Standard library only (`python3`); a YAML scenario needs PyYAML,
which Hermes ships.

```bash
python3 bench/run.py bench/scenarios/demo.yaml --no-baseline --no-create      # habit runs only: no model turn
python3 bench/run.py bench/scenarios/demo.yaml --no-create --budget 0.02      # plus one Hermes turn per case
python3 bench/run.py bench/scenarios/ --dry-check                             # read and check every file, run nothing
```

**It spends model turns.** A baseline or a create is a real `hermes -z` turn on your machine, paid like any other.
`--budget` (0.02 $ unless given) stops before the turn that would pass it. A habit's own model calls (an `llm`
node, a question no decision model took) are counted at 0.002 $ each, so even `--no-baseline --no-create` is not
free for a habit with `llm` nodes.

## Options

| Option | |
| --- | --- |
| `--only id,…` | these cases only |
| `--dry-check` | check every case as a run would, and run nothing. Exit 0 when no file has a problem, else 2 |
| `--no-baseline`, `--no-create` | skip the Hermes turns of that kind |
| `--budget DOLLARS` | stop before the model turn or habit run that would pass it |
| `--night N,K` | a watch's night: N checks of which K woke (40,2 unless given) |
| `--out FILE`, `--log FILE` | also append the JSON records, and the notes and table, to these files |
| `--hermes CMD`, `--home DIR` | another `hermes`, another Hermes folder (default `$HERMES_HOME` or `~/.hermes`) |

## A case

```yaml
- id: tidy
  task: tidy-folder                                    # the family of tasks it belongs to
  habit: "{repo}/examples/tidy-downloads/assets/HABIT.yaml"   # or a saved habit's name
  inputs: {folder: "{scratch}/inbox"}
  message: "tidy my downloads"                         # a request habit: start from this message
  scratch: /tmp/habits-bench/tidy                      # must sit in the temp folder
  setup: 'mkdir -p "$SCRATCH/inbox" && touch "$SCRATCH/inbox/a.pdf"'
  rewrite: {"~/Downloads": "{scratch}/inbox"}         # run a scratch copy of the habit with these texts replaced
  mode: dry                                            # dry: writes planned; live: done, inside scratch only
  repeat: 3
  expect:
    end: {message_contains: "sorted", planned_tools: [terminal]}
    wake: {}                                           # a clean hand-back is right too
    max_ms: 20000
  verify: 'test -f "$SCRATCH/inbox/document/a.pdf"'   # after a live run or a baseline: exit 0 or it is wrong
  baseline: {prompt: "the same task, asked to Hermes", repeat: 1}
  create: {prompt: "ask Hermes to make this habit", name: bench-tidy, repeat: 1}
  forbid: [rsync, ssh]                                 # a model turn that runs one of these is wrong
```

`{scratch}` and `{repo}` work in every text; `setup` and `verify` also get `$SCRATCH` and `$REPO`.
`bench/scenarios/demo.yaml` has four working cases on the shipped examples.

## What it measures

- **run**: `hermes habits run <habit> --json`, with no model turn and nothing recorded in the ledger. `ms` is the
  run alone; `wall_ms` the whole `hermes` process, startup and trigger included.
- **baseline**: the same task asked to Hermes with habits off, in a fresh, empty folder. Seconds, tokens, API calls
  and cost come from Hermes's `state.db`.
- **create**: Hermes asked to write the habit, with habits paused so no watch or request habit acts meanwhile. It
  records whether the first graph was valid, how many saves it took, and whether the saved habit works. Every habit
  the turn saved is copied next to `--out`, retired and moved to the Trash; a cron job the turn made is removed.

The table per case (on stderr) gives runs right and done, median times, the speedup over Hermes alone and the share
of model calls avoided. Exit code 0 when every run was right, 1 otherwise, 2 when a scenario is refused.

## Safety

- A case that names a Downloads folder anywhere is refused, even dry: run a copy with `rewrite`.
- `mode: live` needs a scratch folder, and every write of the run must stay inside it.
- Each model turn runs in a fresh, empty folder under the temp folder (`habits-bench-turns/`), removed once its
  record is out. The bench refuses a turn while a copy of a case's habit or a scenario file sits within reach of it.
  Keep `--out` outside the temp folder: the habits a create made are copied next to it.
- Baseline and create turns act for real, and `hermes -z` skips approval prompts: write prompts that name only
  `{scratch}`.
- During a baseline the bench touches the plugin's off file, so habits are off for every Hermes session meanwhile.
- `hermes plugins install` clones the whole repository, so the installed plugin also holds `bench/` and `tests/`,
  which a model turn can read. To keep only what Hermes loads, once after the install:
  `git -C ~/.hermes/plugins/habits sparse-checkout set --no-cone '/*' '!/bench/' '!/tests/'`.
