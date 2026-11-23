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
| `section` | the same command, and a plugin that writes the note into the prompt, from the reflexes there are, at the start of each session (`section/reflexes_section`) |

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

164 real sessions on Linux, Hermes 2026.9.24, two cheap models through Nous Portal (DeepSeek V4 Flash, Claude Haiku
5.5), two runs for each model in every cell, $0.32 in all. The records are not in the repository.

| Arm | Took the reflex that fits | Task done |
| --- | --- | --- |
| `none` | - | 16 of 20 |
| `note` | 17 of 24 | 30 of 32 |
| `skill` | 0 of 24 | 24 of 32 |
| `plugin` | 0 of 24 | 26 of 32 |
| `section` | 24 of 24 | 30 of 32 |

- **A cheap model takes a reflex when its name and what it does are in front of it, and never goes looking for
  one.** The skill is one line in an index, the tool sits behind Hermes's tool search: both were reached only when
  the user's request said the word "reflex". So the `section` arm adds no tool at all: a note in the prompt, and
  the `reflex` command in the terminal.
- **The note has to be alive.** In `twice`, the reflex made in the first session was never used by the second with
  a note written once (0 of 4), and every time with the section, which is written from the reflexes there are (4 of 4).
- **What a reflex saves depends on the hands it replaces.** On the four routine tasks Flash went from 4.2 requests
  and 5.6 tool calls by hand to 2.4 and 1.4 (input tokens 50 k to 28 k). Haiku already did them in one command, and
  went from 2.5 requests to 2.6: no saving on tasks this short. Every session pays 10 to 14 k tokens of Hermes's
  own prompt before its first word; the note adds about 280.
- **A reflex carries what cannot be guessed.** By hand no agent filed the notes the owner's way (0 of 12). With
  the reflexes in its prompt, 8 of 8, and in one case without even running the reflex: its one-line description
  was enough.
- **A world is a convention, not a wall.** One Flash session, looking for the place where reflexes are saved, read
  the head of the guide only (`reflex guide | head -40`), missed the line that said it, went through the package's
  source and wrote its reflex into the repository's `examples/`. Two changes came of it: the guide now says where a
  reflex goes in its first line, and every session is fenced by the operating system.
- **After a reflex stops, the model is on its own.** In `stopped` Haiku left the file that was in the way alone
  8 times of 8, with or without a reflex. Flash forced its way through 7 times of 8. The reflex said what had
  stopped it and what it had already done; it did not say that forcing was not asked for.

Two runs a cell tell 0 from 24, not 80 % from 90 %. The seconds of the first four arms are not comparable with the
fifth (Hermes was installing 600 MB into every world of the pilot).

## What this bench does not do yet

- The `plugin` arm calls the `reflex` command, so a reflex's commands run with the command's own local host. A host
  that runs them through Hermes's terminal tool (its backends, its approvals) is the next step.
- One request per session, through `hermes -z`: no conversation of several turns, no gateway, no cron.
- A world never has the real profile's plugins, MCP servers or memory. With `--like … --noise` it has its skills
  and its SOUL.md, which is most of what crowds the prompt.
