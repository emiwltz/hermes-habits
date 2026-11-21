# Design note: reflexes, second take

This page is why the code looks the way it does. Version 0.1 (the hackathon plugin for Hermes Agent) stays whole at
commit `3fe9af2`, on `main`. This branch starts again from its ideas, not from its code.

## The aim

An agent has one speed: every gesture goes back through the big model, even the hundredth identical one, and the
whole context is read again each time. We want what practice gives a driver. **Depth**: several levels under the one
that thinks. **Graduality**: a gesture moves down as it becomes routine, and comes back up when the situation is new.

Three words carry it:

- A **reflex** is what runs under the big model: a small program, mostly deterministic.
- A **habit** is a reflex wired to a moment (called by the model, around a tool call, around a reply, on a clock),
  with a trust that is earned.
- The **dream** watches what the agent does, compiles what comes back, and proves it before proposing it.

This branch builds the reflex first, on its own, for any agent. Habits and the dream come back on top of it, in
Hermes first.

## What 0.1 taught us

| Kept | Why |
| --- | --- |
| A run ends done, or hands back what is left | The model can trust a result or act on a precise remainder |
| Reading and changing are told apart, and a dry run only reads | A plan can be shown before anything changes |
| Small closed questions go to a small typed model; a doubt is never guessed | Cheap decisions, with a way out |
| Budgets on time and calls | A reflex always ends |
| A reflex never does what its caller could not do | No new power, only less thinking |

| Changed | From | To | Evidence |
| --- | --- | --- | --- |
| The format | a YAML graph of steps | one Python function | A cheap model wrote a working reflex 1 time in 6 in YAML (with a 3,700-token guide) and 6 in 6 in Python (540 tokens) |
| Where computing happens | shell pipelines inside steps (12 of 15 terminal steps of the shipped examples) | Python, which has no effect | No shell line to parse any more: a command is a list of words |
| Safety | reading shell text, and a guard of 3,574 lines | a sandbox with no file, network or process; every effect is a call the host answers | The sandbox refuses `open`, `os.system`, `subprocess`; an endless loop is cut |
| Handing back | the run ends, the model finishes by hand | the run freezes on one question and carries on with the answer | A frozen run is about 5 to 10 KB and resumes in another process in 16 ms |
| Writing a reflex | 12 to 41 model calls for "make it a habit" | to be measured (the bench in `bench/`) | |

| Dropped for now | Why |
| --- | --- |
| The start on a message, the states, the judge, the guard | They serve autonomy, which is the habit's business: it comes back later, simpler |
| The dream as written | It saw only exact repeats of one gesture (2 % of replies on a real history) |
| The cockpit, the pages, two languages | Not needed to prove the reflex |

## The reflex

One function, `def reflex(rx, ...)`. Its inputs are its parameters, its description is its docstring. It runs in a
sandbox (Pydantic Monty) and touches the world only through `rx`:

| Call | Meaning | In a dry run |
| --- | --- | --- |
| `rx.sh(cmd)` | a command that only reads | runs |
| `rx.do(cmd)` | a command that changes something | planned |
| `rx.read(path)` | the text of a file | runs |
| `rx.write(path, text)` | write a file | planned |
| `rx.ask(question, options, about=...)` | a closed question for the small model; what it is unsure of goes up | asked |
| `rx.up(question)` | one precise question for the big model; the run freezes | asked |

A run ends in one of three ways: **done** (its result), **asks** (a question, and the frozen run), **stopped** (why,
and what was already changed).

Graduality lives twice. Inside a reflex: a rule in Python, then the small model, then one question to the big model,
then a stop. Over time: observed, proposed, called by name, wired to a moment. That second ladder is the habit's.

## What is settled, and what is not

Settled: the function, the sandbox, the three ends, `sh` and `do` told apart by a table of commands that only read
(`reflexes/readers.py`), no shell anywhere.

Open:

- **Who approves what.** The core gives the means (a dry run that is complete because the sandbox has no other way
  out, a list of what a stopped run already changed). The policy belongs to each host.
- **The small model can be sure and wrong** (11.5 % of errors above 90 % confidence for Jev in one outside measure).
  The bar is 0.8 for now and must be fitted.
- **What was read outside** (a web page) can still become an argument of a change.
- **The sandbox knows part of Python's library** (no `shlex`, `textwrap`, `urllib.parse`).
- **The name.** `reflex` and `reflexes` are taken on PyPI; the package is `agent-reflexes` for now.

## Next

1. The surface the model sees, chosen by experiment (`bench/`).
2. Hermes as the first host: call a reflex, keep a script that worked, one line per reflex in the prompt.
3. A profiler of what the agent does and what it costs, read-only, with no model.
4. Moments (habits), then the dream, then a second host.
