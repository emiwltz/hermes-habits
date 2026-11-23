# The Hermes bench

The two benches next door ask whether a model can write a reflex and whether a model that is told about its
reflexes uses them. This one asks the question that decides the rest: give reflexes to a real Hermes agent, with its
own prompt, its own tools and a cheap model, and watch what it does.

## What it compares

One run is one world: a throwaway home folder with real files, a Hermes home of its own, and one way of giving the
reflexes to the agent.

| Arm | What the agent has |
| --- | --- |
| `none` | no reflex at all: it does the task by hand. Everything is compared with this |
| `note` | the reflexes, the `reflex` command, and a note in `AGENTS.md` |
| `skill` | the same command, and a Hermes skill that says when to reach for it |
| `plugin` | the reflexes as one Hermes tool (`plugin/reflexes`) |
| `section` | the same command, and a plugin that writes the note into the prompt, from the reflexes there are, at the start of each session: the plugin at the root of this repository |

The agent gets what a user would type and nothing else (`hermes -z`). Afterwards the world is read:

- **done**: is the task achieved, by whatever means? Judged on disk, and on the answer where the task is a question.
- **took it**: when a reflex fits the request, did the agent run it?
- **what it cost**: requests to the model, tool calls, tokens in and out, seconds, dollars (Hermes's own usage
  report), and the tokens of the very first request: what a way of giving reflexes costs before the first word.
- **what it did**: every tool call with its duration (`observer`, a plugin that only watches), and every call to
  `reflex` with how the run ended (the command's own journal, `~/.reflexes/journal.jsonl`).

## The scenarios

`health`, `triage`, `preview`, `input`, `none` and `write` are the six worlds of `bench/use.py`. Two more:

- `twice`: a first session makes a reflex; a second one, new, asks for the same kind of thing in other words. Does
  the agent find what it made?
- `stopped`: the reflex that fits stops halfway (the second of three files cannot be done). Does the agent finish
  the rest by hand, without redoing what was done and without losing what was there?

## Running it

```bash
pip install -e .                                           # this checkout, where `reflex` then is
python bench/hermes/run.py run /tmp/hb --hermes "$(which hermes)"      # the fake model: no key, a few minutes
python bench/hermes/run.py report /tmp/hb
```

With `--model mock` (the default) a scripted fake model plays the agent: with reflexes it takes the one that fits,
without it does the task by hand. It proves the whole chain and costs nothing; its numbers say nothing about a real
model, except one: how long the prompt and the tool list are in each arm.

With a real model, the login comes from a Hermes home made for the bench (`--from-home`): its `auth.json` and
`.env` are copied into each world's own Hermes home, and nothing else.

```bash
# a home that only holds a login, and the model named here
HERMES_HOME=~/bench-login hermes auth add nous --no-browser
python bench/hermes/run.py run /tmp/hb --from-home ~/bench-login --provider nous --model deepseek/deepseek-v4-flash --reps 3
```

A login made in a browser is renewed every half hour, and each renewal uses up the one before. So the worlds of a
run and the home their login comes from share one store (`<from-home>/shared`), which is how Hermes keeps several
profiles on one login. The first pilot did not, and signed itself out thirty minutes in: every world renewed its own
copy. For the same reason `--from-home` refuses the Hermes home in real use: the bench would renew that login too.

## On a machine where Hermes is in real use

[ON_A_REAL_MACHINE.md](ON_A_REAL_MACHINE.md) is the whole procedure. In short: everything lives in one folder, a
separate Hermes of the same version is installed there, and the real one is only read.

```bash
# the real model settings, and a prompt as crowded as the real one (its skills and SOUL.md, copied into each world)
python bench/hermes/run.py run ~/reflex-lab/out --hermes ~/reflex-lab/hermes-venv/bin/hermes \
    --from-home ~/reflex-lab/login --like ~/.hermes --model as-is --noise
```

What keeps a session away from the real Hermes: the operating system fences it (`confine.py`: Landlock on Linux,
sandbox-exec on macOS), so it writes only in its world, cannot read the home folder around it and cannot signal a
process that is not its own; it runs with `HOME` and `HERMES_HOME` inside its world, so its
gateway, its locks, its sessions, its memory and its skills are the world's own; the real plugins, MCP servers,
memory, cron jobs and messaging are never loaded; Hermes's install-on-demand is off, so no home grows by 600 MB;
and the only process the bench ever stops is the gateway it started itself, by its number, after checking its name.
A run stops by itself as soon as a session reaches no model (a login that ran out, no credit, no network).

## What stays where

Everything a run leaves is under the folder given to `run`: the worlds, the agent's answers, the commands it ran.
On a real machine that may be private. Keep that folder out of the repository; `report` prints counts only.

## What the first runs said

About 440 real sessions on Linux, Hermes 2026.9.24, two cheap models through Nous Portal (DeepSeek V4 Flash, Claude
Haiku 5.5), five runs for each model in a cell of the three main arms and two in the others, $0.88 in all.
The records are not in the repository.

| Arm | Took the reflex that fits | Task done |
| --- | --- | --- |
| `none` | - | 46 of 56 |
| `note` | 45 of 60 | 74 of 80 |
| `skill` | 0 of 24 | 24 of 32 |
| `plugin` | 0 of 24 | 26 of 32 |
| `section` | 60 of 60 | 75 of 80 |

- **A cheap model takes a reflex when its name and what it does are in front of it, and never goes looking for
  one.** The skill is one line in an index, the tool sits behind Hermes's tool search: both were reached only when
  the user's request said the word "reflex". So the `section` arm adds no tool at all: a note in the prompt, and
  the `reflex` command in the terminal.
- **The note has to be alive.** In `twice`, the reflex made in the first session was never used by the second with
  a note written once (0 of 10), and every time with the section, which is written from the reflexes there are
  (10 of 10). It also holds when a bigger model writes and a small one runs: Sonnet 5.5 wrote the reflex in three
  or four requests, for three cents, and Flash and Haiku then took it 6 times of 6. Writing it themselves, the
  small ones needed three to twenty requests, and got there 10 times of 10.
- **What a reflex saves depends on the hands it replaces.** On the four routine tasks Flash went from 5.2 requests
  and 7.4 tool calls by hand to 2.4 and 1.4 (input tokens 68 k to 27 k, cost down 42 %). Haiku already did them in
  one or two commands and went from 2.5 requests to 2.9: nothing saved on tasks this short, except half of what it
  writes. Every session pays 10 to 14 k tokens of Hermes's own prompt before its first word; the note adds about 280.
- **A reflex carries what cannot be guessed.** By hand no agent filed the notes the owner's way (0 of 18, in every
  arm where the reflexes were not in the prompt). With them in the prompt, 20 of 20, and twice without even
  running the reflex: its one-line description was enough.
- **A world is a convention, not a wall.** One Flash session, looking for the place where reflexes are saved, read
  the head of the guide only (`reflex guide | head -40`), missed the line that said it, went through the package's
  source and wrote its reflex into the repository's `examples/`. Two changes came of it: the guide now says where a
  reflex goes in its first line, and every session is fenced by the operating system.
- **How a reflex says it stopped decides what a cheap model does next.** In `stopped` a file is in the way of the
  second of three steps. By hand, Haiku left it alone 7 times of 7 and Flash 4 of 7. After the reflex stopped,
  Haiku still left it alone (9 of 10), but Flash forced its way through 19 times of 20 (`gzip -f`, or deleting what
  was in the way). Several wordings were tried, on two scenarios (the second, `pushed`, is a `git push` refused
  because the remote moved on, where finishing properly means pulling first):

  | What the reflex says when a command does not go through | `stopped`, Flash | `pushed`, Flash |
  | --- | --- | --- |
  | as before: `stopped: RuntimeError: …` and what was already changed | destroyed 9 of 10 | finished 10 of 10 |
  | the note reworded ("never force past it") | destroyed 7 of 8 | - |
  | "Nothing is broken … leave the command as it is, tell the user" | destroyed 4 of 10 | finished 4 of 10 |
  | shorter: "leave that command alone: whether to force it is for the user to say" | finished 0 of 10 | finished 0 of 10 |
  | as before, plus one line: "Finish the task by hand. One rule: never overwrite, delete or force anything to get past what stopped it. If that is the only way, leave that part and tell the user." | destroyed 0 of 10, finished 10 | finished 10 of 10 |

  The careful wordings stopped the damage by stopping the work: told to leave the command alone, Flash left the
  task. What worked is the smallest change, which forbids one thing and asks for the rest. It is in the command
  now (`PAST_A_STOP`). Measured again with it: of 45 sessions on both scenarios and both models, nothing was
  destroyed, 40 finished the task, and 5 stopped and said so.

Five runs a cell tell 0 from 60, not 80 % from 90 %. The seconds of the pilot are not comparable with the later
runs (Hermes was installing 600 MB into every world of the pilot).

## What the second round said: the plugin as it ships

524 more sessions ($0.98), all on the plugin at the root of this repository (the `section` arm), fenced, with the
command as it now is. A third model joins the two cheap ones: `deepseek/deepseek-v4.1-flash-1017` set to think as
hard as it can, the model of the machine this plugin is first meant for.

**Installing it.** `hermes plugins install <this repository> --enable` is the whole of it. Hermes reads the
`pyproject.toml`, installs the package into its own environment after asking, and its `reflex` command is then on
the path of the agent's terminal. Hermes scans a plugin before installing it and blocks one with a finding it rates
high: it found two here, both fixed (`printenv` among the harmless reads, a shell pipe in a bench).

**Many reflexes.** `crowd.py` adds 10, 30 or 60 plausible reflexes to the three or four of a scenario, near misses
first.

| Reflexes in all | The one that fits is in the note | Taken | A wrong one taken |
| --- | --- | --- | --- |
| 13 | yes | 24 of 24 | 0 |
| 33 | yes | 23 of 24 | 0 |
| 63, it was run before | yes | 24 of 24 | 0 |
| 63, never run | no: cut for lack of room | 0 of 24 | 0 |

Sixty-three reflexes do not confuse a cheap model: it never took a near miss, and ran none when none fitted. What
runs out is room: Hermes gives a plugin 4,000 characters of prompt, which is about 35 reflexes. Past that the note
is cut, and what is cut is gone: "and 28 more: `reflex list` shows them all" was followed 0 times of 24. So what the
note keeps matters: the reflexes run or written most recently. Choosing by what the request is about is the next
step, and it is where habits begin.

**Long routines.** `backup` (a dated archive, checked, with its sha256, the old ones pruned, a line in a log) and
`report` (a weekly report with six counts) are about ten commands by hand. With no reflex the user has to spell
the routine out, sixty words each time; with one, "Back up my notes." Fresh profile, three runs a cell:

| | Requests | Tool calls | Input tokens | Seconds | Cost | Done |
| --- | --- | --- | --- | --- | --- | --- |
| Flash, by hand | 5.5 | 9.8 | 69 k | 57 | 0.29 ¢ | 5 of 6 |
| Flash, the reflex | 2.3 | 1.3 | 28 k | 20 | 0.14 ¢ | 6 of 6 |
| Haiku, by hand | 3.8 | 2.8 | 60 k | 26 | 0.16 ¢ | 6 of 6 |
| Haiku, the reflex | 2.8 | 1.8 | 42 k | 19 | 0.08 ¢ | 6 of 6 |

Half the cost for both models, where the short routines saved Haiku nothing.

**The small model.** With a decision model set up (TypeSafe Jev), the triage reflex no longer asks the agent: it
ends `done` by itself in half a second, every note filed right (10 of 10). Flash then needs 2 requests instead of 3.

**A loaded profile.** `loaded.py` makes up a Hermes home after a year of use: 104 skills instead of 44, a SOUL.md
and a memory in French, the model thinking hard. The agent's prompt grows by 80 %. Ten scenarios, three runs each:

| Model | Took the reflex that fits | Done, with the plugin | Done, by hand |
| --- | --- | --- | --- |
| DeepSeek V4.1 Flash, thinking hard | 27 of 27 | 29 of 30 | 21 of 24 |
| Claude Haiku 5.5 | 27 of 27 | 30 of 30 | 21 of 24 |
| DeepSeek V4 Flash | 22 of 27 | 30 of 30 | 18 of 24 |

The two better models are not distracted. Flash is, a little, and only on the short routines (7 of 12), which it
then does by hand; on the long ones and after a stop it took the reflex every time (15 of 15). The long routines
still cost 37 to 51 % less with the reflex. On the short ones the model that thinks hard takes the reflex and
looks around anyway, as its SOUL.md tells it to: 4.2 requests either way.

Three runs a cell: these numbers say which way things go, not how far.

## What this bench does not do yet

- A reflex's commands run with the command's own local host, not through Hermes's terminal tool: its backends
  (docker, ssh) and its approvals do not apply to them.
- One request per session, through `hermes -z`: no conversation of several turns, no gateway, no cron.
- A world never has a real profile's plugins or MCP servers. With `--like … --noise` it has its skills, its
  SOUL.md and its memory, which is most of what crowds the prompt.
- Nothing has run on macOS or on ARM. The CI workflow is ready to (tests and the fence), and has not been started.
