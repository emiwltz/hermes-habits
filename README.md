# Reflexes

A reflex is a small Python function that an agent runs without thinking. The agent's model wrote it once; from then
on one call does the whole task, with no model turn between the steps, and the model gets back only the result.

```python
def reflex(rx, folder="~/Downloads"):
    """Sort a downloads folder by file type."""
    files = [name for name in rx.sh(["ls", "-p", folder]).splitlines() if not name.endswith("/")]
    kind = rx.ask("What type of file is this?", ["installer", "image", "document", "other"], about=files)
    for name in files:
        rx.do(["mkdir", "-p", f"{folder}/{kind[name]}"])
        rx.do(["mv", "-n", f"{folder}/{name}", f"{folder}/{kind[name]}/"])
    return f"{len(files)} files sorted by type."
```

```text
$ reflex test tidy                       a dry run: it reads and asks, and only lists what it would change
asks: What type of file is this?
options: installer, image, document, other
- data.bin
answer with: reflex answer 6887fa '<one option>'

$ reflex answer 6887fa other             the run was frozen on that one question; it carries on from there
dry run, nothing was changed. It would:
  mkdir -p ~/Downloads/installer
  mv -n ~/Downloads/Zoom.pkg ~/Downloads/installer/
  ...
then end with: 4 files sorted by type.
```

## Why

An agent has one speed. Every gesture goes back through the big model, the hundredth identical one included, and the
whole context is read again each time. People do not work that way: what we do often moves down, out of attention,
and comes back up only when something is unusual. A reflex is that lower level for an agent:

- **rules** decide what rules can decide (plain Python, no model);
- a **small typed model** answers the closed questions (`rx.ask`: yes or no, one of a few options);
- what it is unsure of **goes up** to the big model as one precise question, and the run carries on with the answer;
- anything else **stops** the run, with the reason and what was already changed.

## What the model sees

To use reflexes, three lines in its prompt and one line per reflex:

```text
reflex run <name> [input=value]    run one. It ends "done", "asks" (answer with `reflex answer <id> '<answer>'`)
                                   or "stopped" (it says why and what it already changed)
reflex test <name> [input=value]   dry run: shows what it would change, changes nothing
reflex guide                       how to write one
tidy(folder="~/Downloads") · Sort a downloads folder by file type.
```

To write one, the guide that `reflex guide` prints ([reflexes/GUIDE.md](reflexes/GUIDE.md), about 800 tokens).
A reflex is a file `<name>.py` in `./.reflexes` or `~/.reflexes`.

| Through `rx` | Meaning | In a dry run |
| --- | --- | --- |
| `rx.sh(cmd)` | a command that only reads; a list of commands runs side by side | runs |
| `rx.do(cmd)` | a command that changes something | planned |
| `rx.read(path)`, `rx.write(path, text)` | a file | read; a write is planned |
| `rx.ask(question, options, about=items)` | a closed question for the small model; its doubts go up | asked |
| `rx.up(question, options)` | one precise question for the big model; the run freezes | asked |

## Measured

Two benches, in [bench/](bench/), run on 21 November 2026 with fresh model instances that had never seen the project.

**Writing a reflex, first try, from the guide alone** (eight small real tasks; the reflex is then run for real in a
throwaway home, dry and live, and what is on disk is checked):

| Written by | Reflexes that work, first try |
| --- | --- |
| Claude Haiku 5.5 | 22 of 24 |
| Claude Sonnet 5.5 | 16 of 16 |
| Claude Opus 5.5 | 8 of 8 |
| all | 46 of 48 |

The two that fail read a `df` column wrongly and misuse `rx.ask` once. An earlier round compared three guides on 96
reflexes written by Haiku and Sonnet: 91 worked (this guide 31 of 32, one with four verbs only 32 of 32, a
300-token card 28 of 32). Each writer made three tool calls (read the guide, read the task, write the file); five of the 96 then edited their
file once more. None could run or check its reflex.

**Using reflexes** (a request, the lines above in the prompt, a shell; six situations: a plain call, a question to
answer, a dry run asked for, an input to pass, a request no reflex fits, and "make that a reflex"):

| Situation | Right, of 5 | What the model did |
| --- | --- | --- |
| a request a reflex fits | 5 | `reflex run`, nothing by hand |
| a reflex that asks | 5 | `run`, then `answer` with the five notes classified; the reflex moved them |
| "show me first" | 5 | `reflex test`, nothing changed |
| an input to pass | 5 | `test` then `run` with `days=20` |
| a request no reflex fits | 5 | no reflex; one command by hand |
| "make that a reflex" | 5 | `guide`, wrote the file, `test`, `run`: four tool calls for a new working reflex |

Five runs per situation: Haiku twice, Sonnet twice, Opus once.

What these numbers are not: a comparison with another format on the same tasks (an earlier, smaller trial of the 0.1
YAML format gave 1 working reflex in 6 against 6 in 6), or a measure on a cheap non-Claude model. Both are to do.

## Try it

```bash
pip install -e .                      # Python 3.10+; brings the sandbox (pydantic-monty)
REFLEX_PATH=examples reflex list
REFLEX_PATH=examples reflex run disk-space
REFLEX_PATH=examples reflex test tidy-downloads
```

With no small model set up, every `rx.ask` goes up to the caller, so everything works without a key. With one
(`TYPESAFE_API_KEY` for Jev, or `REFLEX_DECIDER_URL`, `REFLEX_DECIDER_KEY` and `REFLEX_DECIDER_MODEL` for a model of
the same format), the closed questions are answered in a fraction of a second and only the doubts go up.

From Python: `from reflexes import run, resume, Local`.

## What keeps it safe, and what does not

- A reflex runs in a sandbox ([Pydantic Monty](https://github.com/pydantic/monty)) with no file, network, process or
  environment of its own. Every effect is a call the host answers, and each one is in the run's journal.
- Commands are lists of words and run without a shell: a value can never become a command.
- `rx.sh` accepts only commands that a table knows as reads ([reflexes/readers.py](reflexes/readers.py)); so a dry
  run changes nothing, and what it plans is all the reflex would do.
- A run has budgets: 120 s, 200 calls to `rx`, 10 s of its own computing.

Not covered: a reflex can read any file its user can read, and a read can go outside (`curl`), so a dry run is not
private; commands get the caller's environment; a reflex does what its caller could do, no more and no less. Who may
approve what is each host's business.

## Where this is going

This repository began as Habits, a plugin for Hermes Agent ([0.1 is on `main`](../../tree/main)). This branch
rebuilds its core for any agent: the reflex first. Next: Hermes as the first host, then habits (a reflex wired to a
moment: around a tool call, around a reply, on a clock), then the dream (compile what the agent does again and again
into reflexes, and prove them on its own history). The reasons are in [docs/DESIGN.md](docs/DESIGN.md).

MIT.
