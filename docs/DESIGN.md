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
| The format | a YAML graph of steps | one Python function | In a first trial a cheap model wrote a working reflex 1 time in 6 in YAML (3,700-token guide) and 6 in 6 in Python. On the bench since: 46 of 48 first try (Haiku 22 of 24) |
| Where computing happens | shell pipelines inside steps (12 of 15 terminal steps of the shipped examples) | Python, which has no effect | No shell line to parse any more: a command is a list of words |
| Safety | reading shell text, and a guard of 3,574 lines | a sandbox with no file, network or process; every effect is a call the host answers | The sandbox refuses `open`, `os.system`, `subprocess`; an endless loop is cut |
| Handing back | the run ends, the model finishes by hand | the run freezes on one question and carries on with the answer | A frozen run is about 5 to 10 KB and resumes in another process in 16 ms |
| Writing a reflex | 12 to 41 model calls for "make it a habit" | four tool calls: read the guide, write the file, dry run, run | The use bench, 5 of 5 |
| Size | 18,506 lines, 4,963 tests | about 1,500 lines, about 350 tests | |

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
| `rx.sh(cmd)` | a command that only reads; a list of commands runs side by side | runs |
| `rx.do(cmd)` | a command that changes something | planned |
| `rx.read(path)` | the text of a file | runs |
| `rx.write(path, text)` | write a file | planned |
| `rx.ask(question, options, about=...)` | a closed question for the small model; what it is unsure of goes up | asked |
| `rx.up(question, options)` | one precise question for the big model; the run freezes | asked |

A run ends in one of three ways: **done** (its result), **asks** (a question, and the frozen run), **stopped** (why,
and what was already changed).

Graduality lives twice. Inside a reflex: a rule in Python, then the small model, then one question to the big model,
then a stop. Over time: observed, proposed, called by name, wired to a moment. That second ladder is the habit's.

### Why six calls and not four

A bench compared three guides (96 reflexes, first try). The guide with four calls only (files through `cat` and
`tee`) scored as well as the one with six (32 of 32 against 31 of 32): for a model the difference is nil. The six
stay because a host maps them onto its own tools one for one, and a file write is safer as a call with a path and a
text than as a command fed on its input.

### What the engine does for the author

The author is a model writing ordinary Python. So the engine does not ask it to be careful where it can simply be
forgiving: the everyday modules are there without an import; `next(x for x in ...)` reads as Python means it (the
sandbox builds a generator expression as a list); a reach for `open`, `pathlib`, `subprocess` or `requests` is
answered with what to use instead; `~` is the home folder everywhere; a doubt of the small model goes up by itself.

### Which commands only read

`rx.sh` accepts a command only if `reflexes/readers.py` knows it as a read. The first table listed programs and
the options to refuse; a review turned it over in an hour (a long option cut short, a bundle, a subcommand hidden
behind an option, `curl -w %output{file}`, a bare `git stash`). The table now lists what is fine and refuses the
rest: for each program whose options matter, the options known to only read; a subcommand first; a program by its
name or from a system folder. It is safe at the price of being incomplete: a read it does not know goes through
`rx.do`, which a dry run plans instead of running.

The sturdier answer is the operating system's: run every read in a sandbox that cannot write (bubblewrap, Landlock,
Seatbelt), and the table is only needed for the network. That is for later, and for hosts that already have one.

## Hosts

The engine knows nothing of its host but a few members: run a command, read and write a file, say whether a command
only reads, how many commands it takes at once. `reflexes/host.py` is the local one. A probe ran the engine inside
Hermes's own Python (3.14) with Hermes's terminal tool as the host: the `hermes-health` example made its six reads
through Hermes and gave its verdict in 2.5 s, with no model. Hermes runs the commands of one task in one shell, so
its host takes one command at a time.

For an agent with a shell and nothing else, the command line is the host: `reflex run`, `test`, `answer`, `guide`.

## What is settled, and what is not

Settled: the function, the sandbox, the six calls, the three ends, no shell anywhere, a question answered once.

Open:

- **Who approves what.** The core gives the means (a dry run that is complete because the sandbox has no other way
  out, a list of what a stopped run already changed). The policy belongs to each host.
- **The small model can be sure and wrong** (11.5 % of errors above 90 % confidence for Jev in one outside measure).
  The bar is 0.8 for now and must be fitted.
- **Reads are not private.** A reflex can read what its user can read and a read can go outside (`curl`), so a dry
  run changes nothing but can tell. The journal marks the reads that left the machine.
- **The sandbox knows part of Python**: no class inheritance, no `yield`, part of the library.
- **Only Claude models wrote and used reflexes so far.** A cheap model of another family is to measure.
- **The name.** `reflex` and `reflexes` are taken on PyPI; the package is `agent-reflexes` for now, the command
  `reflex`.

## Next

1. Hermes as the first host: call a reflex, keep a script that worked, one line per reflex in the prompt.
2. A profiler of what the agent does and what it costs, read-only, with no model.
3. Moments (habits): a reflex around a tool call, around a reply, on a clock. Then the dream, then a second host.
