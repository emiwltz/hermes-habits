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

With a real model:

```bash
python bench/hermes/run.py run /tmp/hb --provider nous --model deepseek/deepseek-v4-pro --reps 3
```

On a machine where Hermes is already set up, `--from-home ~/.hermes --model as-is` takes the model and the login from
the real Hermes home (its `config.yaml`, `.env` and `auth.json`, copied into each world's own Hermes home) and
nothing else: the real sessions, memory and skills are not touched.

## What stays where

Everything a run leaves is under the folder given to `run`: the worlds, the agent's answers, the commands it ran.
On a real machine that may be private. Keep that folder out of the repository; `report` prints counts only.

## What this bench does not do yet

- The `plugin` arm calls the `reflex` command, so a reflex's commands run with the command's own local host. A host
  that runs them through Hermes's terminal tool (its backends, its approvals) is the next step.
- One request per session, through `hermes -z`: no conversation of several turns, no gateway, no cron.
- A fresh Hermes home has Hermes's bundled skills but not the user's own, nor their memory: the noise of a real
  profile is for the runs on a real machine.
