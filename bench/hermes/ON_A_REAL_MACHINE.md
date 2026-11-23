# Reflexes on a machine where Hermes is in real use

This is a handover. It is written for a coding agent (a Claude Code session) that runs on the machine itself, with
the machine's owner at hand. It says what the project is, what is already known, what this machine can tell that no
other can, and how to find out **without touching the Hermes that is in use there**.

Read it whole before the first command. Talk to the owner in French.

## The project, in a few lines

A **reflex** is a small Python function an agent runs instead of redoing a routine step by step:

```python
def reflex(rx, folder="~/Downloads"):
    """Move the PDFs of a folder into ~/Documents/pdf."""
    names = [n for n in rx.sh(["ls", folder]).splitlines() if n.endswith(".pdf")]
    for name in names:
        rx.do(["mv", f"{folder}/{name}", "~/Documents/pdf/"])
    return f"{len(names)} moved."
```

It runs in a sandbox, below the big model: `reflex run <name>` does the whole task in one call and ends `done`,
`asks` (it needs one answer) or `stopped` (it says why and what it already changed). `reflex test <name>` is a dry
run. The kernel is in `reflexes/`, its reasons in `docs/DESIGN.md`, the measures in `README.md`. Habits (a reflex, a
moment, and trust) and the dream (learning them) come later, on top of this.

The question of this phase is not whether reflexes work. It is: **given to a real Hermes agent with a cheap model,
are they taken, and what do they change?** `bench/hermes/` asks it: each run is a throwaway world (a home folder
with files, a Hermes home of its own), one request typed as a user would, and a reading of what the agent did.

## What is known (cloud, Linux x86-64, Hermes 2026.9.24)

- The kernel: 533 tests pass. Nothing has ever run on macOS or on ARM.
- 164 real sessions (DeepSeek V4 Flash and Claude Haiku 5.5 through Nous Portal, $0.32 in all) compared ways of
  giving reflexes to the agent:

  | How the agent learns it has reflexes | Took the one that fits |
  | --- | --- |
  | `note`: a note in `AGENTS.md`, always in the prompt | 17 of 24 |
  | `skill`: a Hermes skill, one line in the skills index | 0 of 24 |
  | `plugin`: one Hermes tool, behind Hermes's tool search | 0 of 24 |
  | `section`: a plugin writes the same note into the prompt at each session start | 24 of 24 |

  A cheap model takes a reflex when its name and what it does are in front of it. It never goes looking for one.
  The note in a file goes stale: a reflex made in one session was never found by the next (0 of 4). The section is
  written from the reflexes there are, and the next session took it 4 times of 4.
- By hand, no agent could file notes the owner's way (0 of 12): it cannot guess a convention. With the reflexes in
  its prompt, 8 of 8.
- When a reflex stopped halfway because a file was in the way, Haiku left the file alone 8 times of 8. Flash forced
  its way through 7 times of 8, with or without a reflex. That is the model, and worth watching here.
- Every cell is two runs for each model: enough to see 0 against 24, not to tell 80 % from 90 %.
- **A world is a convention, not a wall.** In about 170 sessions, one agent that could not find where reflexes are
  saved wrote a file into the repository the bench ran from, and several searched folders outside their world.
  Since then every session runs fenced by the operating system (`confine.py`): it writes only in its world, cannot
  read the home folder around it, and cannot signal a process that is not its own. On Linux this was tried with an
  agent scripted to escape, and held. **On macOS it has never run**: step 2 tries it before anything else.

## What only this machine can tell

1. Does the kernel work on this system (macOS arm64, or Linux aarch64)? The unit tests answer.
2. With the model settings really in use here, and a prompt as crowded as the real one (the owner's skills and
   `SOUL.md`), is the reflex still taken?
3. What does the real history hold that reflexes could take over? `history.py` counts it, without printing any text.

## The rules

They are not negotiable. If one cannot be kept, stop and tell the owner.

1. **One folder.** Everything is written under `LAB` (`~/reflex-lab`). Deleting it removes everything.
2. **The real Hermes home is read, never written.** No file is created, changed or deleted under `~/.hermes`: not
   its plugins (the `habits` plugin is in production there), not `plugin-data`, skills, memory, config, `auth.json`,
   `.env`, `shared`.
3. **The machine's own `hermes` command is never run.** The lab gets its own Hermes, same version. It is run by
   `bench/hermes/run.py`, which gives every session a `HOME` and a `HERMES_HOME` inside its world: the session
   starts its own gateway and cannot find the real one. The two other uses (asking its version, signing in) are
   written below, with both variables inside the lab.
4. **The fence stays on.** `run.py` confines every session by default. `--unconfined` lets an agent with a cheap
   model run with the owner's own rights, on their real files: never without the owner's word, given after they
   have read this line.
5. **No gateway, no service, no signal.** Never start, stop, restart, enable or disable a gateway or a service.
   Never `pkill`, `killall`, or `kill`. The bench stops the gateways it started, by their number, after checking
   their name. Other processes are not ours: on the Mac `cua-driver` and three `node` processes of another project;
   on the Pi the live gateway (Telegram, cron jobs, a dashboard on port 9119).
6. **No production key.** Do not read the values in `~/.hermes/.env`. The bench passes no key of the shell on to
   the agent. The model is reached through a sign-in made for the bench (step 3). On the Pi, the Anthropic key is
   rate-limited and in use: nothing here goes through it.
7. **Nothing global.** No `sudo`, no `brew`, no `apt`, no `pip` outside the lab's environments, no change to shell
   profiles, `PATH`, crontab, launchd or systemd.
8. **One run at a time.** Never two `run.py` at once. On the Pi: nothing between 02:45 and 04:45 (the backup), and
   stop if less than 1 GB of memory is available or less than 5 GB of disk is free.
9. **What stays here.** `LAB/out` holds what the agents answered and, with `--noise`, copies of the owner's skills
   and `SOUL.md`. Nothing of it goes into the chat or anywhere else, except what step 6 names.
10. **When in doubt, stop.** In particular: a command that would write outside `LAB`; an isolation check of step 2
   that fails; a run that stops by itself; a cost above $3 in all.

## Step 0. Look (read only)

```bash
export LAB="$HOME/reflex-lab"
mkdir -p "$LAB" && touch "$LAB/.began"
uname -sm; df -h "$LAB" | tail -1
git -C ~/.hermes/hermes-agent rev-parse HEAD     # the version of the Hermes in use: the lab's will be the same
pgrep -fl gateway || echo "no gateway runs"      # on the Pi: one, the real one. Note its number.
```

## Step 1. Install in the lab (no model, about ten minutes)

`uv` builds the two environments. Its caches and the Python it may download are kept in the lab too.

```bash
export UV_CACHE_DIR="$LAB/uv-cache" UV_PYTHON_INSTALL_DIR="$LAB/uv-python"
UV="$(command -v uv || find ~/.hermes/tools -type f -name uv 2>/dev/null | head -1)"; echo "uv: $UV"
```

The Mac has `uv`. The Pi has none on its `PATH`: the one Hermes brought is only executed, which writes nothing
under `~/.hermes` with the two variables above. If neither exists, tell the owner before installing one into
`$LAB/bin` (`curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$LAB/bin" UV_NO_MODIFY_PATH=1 sh`).

The reflexes and the bench:

```bash
[ -d "$LAB/repo" ] || git clone --branch claude/cool-euler-74kvpy https://github.com/emiwltz/hermes-habits "$LAB/repo"
"$UV" venv --python 3.14 "$LAB/rx-venv"
"$UV" pip install --python "$LAB/rx-venv/bin/python" -e "$LAB/repo[dev]"
cd "$LAB/repo" && "$LAB/rx-venv/bin/python" -m pytest -q 2>&1 | tail -15
```

On Linux x86-64 this prints `533 passed`. Here it has never run: **the result is the first finding**. Do not fix a
failing test. Note its name and the first line of its error, then go on, unless this fails too:

```bash
REFLEX_HOME="$LAB/aside-reflexes" REFLEX_PATH="$LAB/repo/examples" "$LAB/rx-venv/bin/reflex" run disk-space
```

A Hermes for the lab, at the version in use on the machine (a local clone reads the real checkout and writes only
in the lab):

```bash
git clone --no-hardlinks ~/.hermes/hermes-agent "$LAB/hermes-agent"
"$UV" venv --python 3.14 "$LAB/hermes-venv"
"$UV" pip install --python "$LAB/hermes-venv/bin/python" -e "$LAB/hermes-agent"
mkdir -p "$LAB/aside" && HOME="$LAB/aside" HERMES_HOME="$LAB/aside/hermes" HERMES_DISABLE_LAZY_INSTALLS=1 \
    "$LAB/hermes-venv/bin/hermes" --version
```

If Python 3.14 cannot be had, 3.13 does (`--python 3.13`). If `~/.hermes/hermes-agent` is not a git checkout, clone
`https://github.com/NousResearch/hermes-agent` and check out the commit the real one reports. If the install fails
on a dependency, stop and report the error.

This lab Hermes is a twin, not the real one: same code, its own packages (resolved today, not the real one's
locked set), and none of the real plugins, MCP servers, memory, cron jobs or messaging. That is on purpose.

## Step 2. The fence, then the canary: the whole chain with a fake model (no key, no cost, five minutes)

First the fence alone. It makes a small folder in the lab, tries eight things there, and removes it:

```bash
"$LAB/rx-venv/bin/python" "$LAB/repo/bench/hermes/confine.py" --check "$LAB"
```

Eight lines must say `ok`: it writes where it may, reads what it is given, starts a Python; it does not write
elsewhere, does not read what is hidden, does not list the home, does not signal a process outside. On a Linux
kernel older than 6.12 the last one fails and the check says so: files are fenced, signals are not; go on and
report it. Anything else that fails, or `cannot confine`: stop and report the lines. On the Mac this is the first
time this fence runs anywhere, so a failure here is a finding, not an accident. Do not go on with `--unconfined`
unless the owner says so.

One case to know: if this session's own commands already run in a sandbox (Claude Code can do that), macOS refuses
to put a second one inside it (`sandbox_apply: Operation not permitted`). Tell the owner. They can let the bench's
commands run outside that sandbox, so that the fence can be put on; that is the better way.

Then the whole chain, fenced:

```bash
RUN() { "$LAB/rx-venv/bin/python" "$LAB/repo/bench/hermes/run.py" "$@"; }
RUN run "$LAB/out/canary" --hermes "$LAB/hermes-venv/bin/hermes" --scenarios health,triage,stopped --seconds 180
RUN report "$LAB/out/canary"
```

Fifteen lines must say `ok` (three scenarios, five ways). If the fence check passed and these fail, the fence is
in Hermes's way on this system: look at `errors-0.txt` and `hermes/logs/errors.log` in the first world, report the
path that was refused, and stop. Then the isolation checks. **All must hold before any
real model is called.**

```bash
# a. every session was served by its world's own Hermes: each world's store holds its session
"$LAB/rx-venv/bin/python" - "$LAB/out/canary" <<'EOF'
import pathlib, sqlite3, sys
for db in sorted(pathlib.Path(sys.argv[1]).glob("worlds/*/hermes/state.db")):
    print(sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute("select count(*) from sessions").fetchone()[0], db.parent.parent.name)
EOF
# b. nothing of the lab still runs (a gateway that lingers leaves by itself within ten minutes: wait, do not kill)
pgrep -fl "$LAB" || echo "nothing of the lab runs"
# c. the real home: nothing changed since step 0
find ~/.hermes -newer "$LAB/.began" -type f 2>/dev/null | head -20
# d. no reflex folder appeared in the real home
ls -d ~/.reflexes 2>/dev/null || echo "no ~/.reflexes"
# e. what a world weighs
du -sh "$LAB/out/canary"
```

- a: fifteen lines, each starting with 1 or more.
- c, on the Mac (its Hermes is not running): nothing at all. On the Pi the live gateway writes its own files
  (`state.db`, logs, its plugins' data): that is expected. What must not appear there is anything the lab could
  have made: a `reflexes` plugin, a `reflexes` skill, a session whose request is one of the bench's. And the real
  gateway must still be the process noted in step 0 (`pgrep -fl gateway`).
- e: tens of megabytes. If a world weighs hundreds, Hermes installed something into it: stop and report.
- One more thing to read: `grep -c reflexes.yours "$LAB"/out/canary/worlds/health-section-mock-1/hermes/logs/agent.log`.
  Zero means this version of Hermes has no prompt sections for plugins: leave `section` out of the runs below and
  say so in the report.

## Step 3. A sign-in for the bench (the owner clicks once)

The bench does not borrow the real Hermes's sign-in: it would renew it, and a sign-in renewed in two places is
lost in both. It gets its own, kept in the lab.

```bash
mkdir -p "$LAB/login" "$LAB/login-home"
HOME="$LAB/login-home" HERMES_HOME="$LAB/login" HERMES_SHARED_AUTH_DIR="$LAB/login/shared" HERMES_DISABLE_LAZY_INSTALLS=1 \
    "$LAB/hermes-venv/bin/hermes" auth add nous --no-browser > "$LAB/login.log" 2>&1
```

Run it in the background and read `$LAB/login.log` a few seconds later: it holds an address and a code. Give both
to the owner, who opens the address and approves; the command then ends by itself (`Saved nous OAuth device-code
credentials`) and `$LAB/login/auth.json` exists. Never print that file.

As far as is known this second sign-in leaves the real Hermes's own alone (the cloud bench signed in the same way).
It is not proven: if the real Hermes asks the owner to sign in again later, that is worth telling.

What it costs is billed to the owner's Nous account. Measured in the cloud: about $0.002 a session with the two
cheap models. The Mac's real model thinks harder and costs more a token: expect up to $0.02 a session. The runs
below are about a hundred sessions: well under $3. `report` prints the dollars: look at them after each run.

## Step 4. The runs

First one real session:

```bash
RUN run "$LAB/out/smoke" --hermes "$LAB/hermes-venv/bin/hermes" --from-home "$LAB/login" \
    --provider nous --model deepseek/deepseek-v4-flash --arms section --scenarios health
```

One line, `ok`, with `reflex: list | run project-health`. Then, one after the other:

**Run A. The same as in the cloud: does the machine change anything?**

```bash
RUN run "$LAB/out/a" --hermes "$LAB/hermes-venv/bin/hermes" --from-home "$LAB/login" --provider nous \
    --model deepseek/deepseek-v4-flash --reps 2 --arms none --scenarios health,triage,preview,input,none,stopped
RUN run "$LAB/out/a" --hermes "$LAB/hermes-venv/bin/hermes" --from-home "$LAB/login" --provider nous \
    --model deepseek/deepseek-v4-flash --reps 2 --arms note,section
RUN report "$LAB/out/a"
```

**Run B. Like the real one: the real crowd in the prompt, and on the Mac the real model.**

`--like ~/.hermes` only reads the real home. `--noise` copies its skills (not the `habits` ones) and its `SOUL.md`
into each world. `--model as-is` takes the real model and how hard it thinks.

```bash
# on the Mac: the model in real use there (through Nous Portal, so the bench's sign-in reaches it)
RUN run "$LAB/out/b" --hermes "$LAB/hermes-venv/bin/hermes" --from-home "$LAB/login" --like ~/.hermes --noise \
    --model as-is --reps 2 --arms none --scenarios health,triage,preview,input,none,stopped
RUN run "$LAB/out/b" --hermes "$LAB/hermes-venv/bin/hermes" --from-home "$LAB/login" --like ~/.hermes --noise \
    --model as-is --reps 2 --arms note,section

# on the Pi: its real model goes through the Anthropic key, which is not to be used. Haiku, its cron model, through Nous:
RUN run "$LAB/out/b" --hermes "$LAB/hermes-venv/bin/hermes" --from-home "$LAB/login" --like ~/.hermes --noise \
    --provider nous --model anthropic/claude-haiku-5.5 --reps 2 --arms none --scenarios health,triage,preview,input,none,stopped
RUN run "$LAB/out/b" --hermes "$LAB/hermes-venv/bin/hermes" --from-home "$LAB/login" --like ~/.hermes --noise \
    --provider nous --model anthropic/claude-haiku-5.5 --reps 2 --arms note,section

RUN report "$LAB/out/b"
```

Each run is about fifty sessions, one after the other: fifteen to forty minutes. A run stops by itself, saying why,
when a session reaches no model (the sign-in ran out, no credit, no network): redo step 3 once; if it stops again,
report. A session that takes too long is cut and counted as failed; three in a row are a reason to stop.

If the model of `as-is` is not served through Nous Portal, do not go looking for another key: run B with
`--provider nous --model deepseek/deepseek-v4.1-flash-1017` and say so.

## Step 5. The real history (the owner decides)

```bash
"$LAB/rx-venv/bin/python" "$LAB/repo/bench/hermes/history.py" ~/.hermes > "$LAB/out/history.txt"; cat "$LAB/out/history.txt"
```

It opens the real `state.db` as a file that cannot change, and prints names of tools, names of programs
(`git status`, never an argument), counts, tokens and dollars: which requests come back, what each costs, and
whether the agent then takes the same steps each time. No text of a request, no path, no argument. The owner reads
it before it goes anywhere. With `--show` it adds the first words of each repeated request: for the owner's eyes
only, never to be sent on.

## Step 6. What to send back

Write `$LAB/out/REPORT.md`:

- the machine (system, processor, Python, the Hermes commit, where `uv` came from);
- the unit tests: how many passed, and for each that failed its name and the first line of its error;
- the canary: its lines and the result of each isolation check;
- for each run: the command, what `report` printed, how long it took, and anything odd (an `errors-*.txt` in a
  world, a session cut for time, a run that stopped by itself);
- where a reflex fitted and was not taken: what the agent did instead, in two lines, from the world's
  `answer-*.txt` and the `commands` of its record (no text of the owner's skills or `SOUL.md`);
- `history.txt`, if the owner agreed.

The owner then hands over `REPORT.md` and, if they agree, the `records.jsonl` of each run. A record holds what the
agent said and ran in a throwaway world; it names paths under `LAB`, so the user name of the machine is in it.

## Cleaning up

Nothing to undo outside the lab: no service, no `PATH`, no file elsewhere. Keep `LAB` for a later run, or delete it:
`rm -rf "$LAB"` removes the environments, the worlds, the caches and the bench's sign-in (which can also be revoked
from the Nous Portal account page).

## The two machines, as audited on 23 November 2026

| | Mac | Raspberry Pi 5 |
| --- | --- | --- |
| System | macOS 27.0, arm64, 10 cores, 16 GB | Debian 13, aarch64, 4 cores, 8 GB |
| Hermes in use | v0.23.0 (2026.9.24), commit `2bd378ed` | v0.22.5, commit `c627ec8` |
| Its gateway | stopped, no service | **live**: Telegram, 7 cron jobs, dashboard on 9119 |
| Its model | `deepseek/deepseek-v4.1-flash-1017` through Nous Portal, thinking set to xhigh | `anthropic/claude-opus-5-5` through a rate-limited key; cron jobs on Haiku |
| In production there | the `habits` plugin and its database | the `habits` plugin and its database, written to every day |
| `uv` | on the `PATH` | only Hermes's own |
| Mind | `timeout` does not exist on macOS; other people's `node` processes | 02:45 to 04:45 is the backup's; memory |

The Pi's Hermes is the older one: a hook or a setting the bench relies on may be missing there. The canary shows
it. If it fails for that reason, install the lab's Hermes at commit `7c88e98` (the one measured in the cloud)
instead, and say so in the report.
