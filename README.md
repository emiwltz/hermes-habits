# Hermes Habits

A plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that gives it reflexes: small programs
that do a routine task in one call, below the model. Habits and the dream are built on them, next.

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

## In Hermes

```bash
hermes plugins install emiwltz/hermes-habits --enable
```

That is the whole installation. Hermes installs the package into its own environment (it asks first). Then, in any
chat with Hermes:

- "Is Hermes OK?" The agent finds two reflexes to begin with in `~/.reflexes`, and runs `hermes-health`: seven
  reads, one verdict, one call.
- "Make that a reflex." After any routine it has just done by hand. It reads the guide, writes the file, tries it
  dry, runs it. The next session has it.

The plugin is two files ([`plugin.yaml`](plugin.yaml), [`__init__.py`](__init__.py)): at the start of each session
it puts `reflex note` in the agent's prompt, and the agent runs reflexes with the `reflex` command in its terminal,
like any command. No tool, no skill, nothing to configure.

Measured on a real Hermes Agent (2026.9.24) with cheap models, in throwaway worlds ([the lab](bench/README.md),
973 sessions, $1.85):

- **It is taken.** The reflex that fits: 60 times of 60 on DeepSeek V4 Flash and Claude Haiku 5.5. In a profile
  made up to look like a year of use (104 skills, a SOUL.md and a memory, the prompt 80 % longer): 27 of 27 on
  Haiku and on DeepSeek V4.1 Flash thinking hard, 22 of 27 on V4 Flash.
- **The next session finds what the last one made** (10 of 10), also when a bigger model wrote it (6 of 6).
- **It saves most where the routine is long.** Ten commands by hand against one call: half the cost on both cheap
  models. On routines of one or two commands, Flash saves 40 %, Haiku nothing.
- **It carries what cannot be guessed.** By hand no agent filed notes the owner's way (0 of 18); with the reflex in
  its prompt, 20 of 20.
- **It does not push a cheap model into damage.** After a command that does not go through, a reflex ends with one
  rule (finish by hand, never overwrite, delete or force past what stopped it). Without it Flash forced its way 19
  times of 20; with it, 0 of 45, and 40 of the 45 still finished the task.
- **Its limit is room, not confusion.** With 63 reflexes a cheap model never took a wrong one. But Hermes gives a
  plugin 4,000 characters, about 35 reflexes, and one cut from the note is never taken (0 of 24).

## Why

An agent has one speed. Every gesture goes back through the big model, the hundredth identical one included, and the
whole context is read again each time. People do not work that way: what we do often moves down, out of attention,
and comes back up only when something is unusual. A reflex is that lower level for an agent:

- **rules** decide what rules can decide (plain Python, no model);
- a **small typed model** answers the closed questions (`rx.ask`: yes or no, one of a few options);
- what it is unsure of **goes up** to the big model as one precise question, and the run carries on with the answer;
- anything else **stops** the run, with the reason and what was already changed.

## What the model sees

To use reflexes, a note in its prompt: nine lines, and one line per reflex. `reflex note` prints it.

```text
You have reflexes: small programs for tasks done before. One call does the whole task, with no thinking in between.
  reflex run <name> [input=value]    run one. It ends "done" (use its result as it is), "asks" (it needs one answer
                                     from you: `reflex answer <id> '<answer>'`, and it carries on) or "stopped" (it
                                     says why and what it already changed; do the rest yourself)
  reflex test <name> [input=value]   dry run: shows what it would change, changes nothing
  reflex guide                       how to write one
Yours:
  tidy(folder="~/Downloads") · Sort a downloads folder by file type.
When a request matches a reflex, run it instead of doing the steps by hand.
```

The note has to be in front of the model, and it has to be alive. Told about its reflexes through a skill or a
tool it had to look for, a cheap model took the one that fitted 0 times of 48; with this note in its prompt, 60
times of 60. And a note written once never held the reflex made the day before. So a host asks `reflex note` at
the start of each session; `--chars N` keeps it to a size, with the reflexes run or written most recently.

To write one, the guide that `reflex guide` prints ([reflexes/GUIDE.md](reflexes/GUIDE.md), about 800 tokens).
A reflex is a file `<name>.py` in `./.reflexes` or `~/.reflexes`.

| Through `rx` | Meaning | In a dry run |
| --- | --- | --- |
| `rx.sh(cmd)` | a command that only reads; a list of commands runs side by side | runs |
| `rx.do(cmd)` | a command that changes something | planned |
| `rx.read(path)`, `rx.write(path, text)` | a file | read; a write is planned |
| `rx.ask(question, options, about=items)` | a closed question for the small model; its doubts go up | asked |
| `rx.up(question, options)` | one precise question for the big model; the run freezes | asked |

## Can a model write one?

A fresh model gets the guide and a task, and writes the reflex, first try, without running it. The reflex is then
run for real in a throwaway home, dry and live, and what is on disk is checked (eight small real tasks, in
[the lab](bench/README.md)):

| Written by | Reflexes that work, first try |
| --- | --- |
| Claude Haiku 5.5 | 62 of 64 |
| Claude Sonnet 5.5 | 31 of 32 |
| Claude Opus 5.5 | 8 of 8 |
| all | 101 of 104 |

The three that fail: a `df` column read wrongly, one misuse of `rx.ask`, and a harmless `curl` option that the
table of reads did not know (it does now). In Hermes, the two cheap models were asked "make that a reflex" 20
times, and made one that works 20 times.

## Without Hermes

The command line is all an agent with a shell needs, whatever the agent.

```bash
pip install -e .                      # Python 3.10+ on Linux or macOS; brings the sandbox (pydantic-monty)
REFLEX_PATH=examples reflex list
REFLEX_PATH=examples reflex run disk-space
REFLEX_PATH=examples reflex test tidy-downloads
```

With no small model set up, every `rx.ask` goes up to the caller, so everything works without a key. With one
(`TYPESAFE_API_KEY` for Jev, or `REFLEX_DECIDER_URL`, `REFLEX_DECIDER_KEY` and `REFLEX_DECIDER_MODEL` for a model of
the same format), the closed questions are answered in a fraction of a second and only the doubts go up.

From Python: `from reflexes import run, resume, Local`.

Everything here was built and measured on Linux (x86-64, Python 3.10 to 3.13). The table of reads was written with
macOS in mind and reviewed against its manuals, but nothing has run on a Mac or on ARM yet (the CI is set to).

## What keeps it safe, and what does not

- A reflex runs in a sandbox ([Pydantic Monty](https://github.com/pydantic/monty)) with no file, network, process or
  environment of its own. Every effect is a call the host answers, and each one is in the run's journal.
- Commands are lists of words and run without a shell: a value can never become a command.
- `rx.sh` accepts only commands that a table knows as reads ([reflexes/readers.py](reflexes/readers.py)); so a dry
  run changes nothing, and what it plans is all the reflex would do.
- A run has budgets: 120 s and 200 calls to `rx` (`REFLEX_SECONDS` and `REFLEX_CALLS` raise them), 30 s for one
  command unless it says `timeout=`, 10 s of its own computing, 8 MB for one output or one file, 200 doubts in one
  question. Ctrl-C or a kill ends it "stopped: interrupted", with what it had done.
- `curl` is run on HTTP and HTTPS only, wherever a redirect leads.
- Five rounds of adversarial review (models set to break it, others to check each claim) are in the tests: every
  fault they found is a test that now passes. In the last one a model wrote and used twelve ordinary reflexes from
  the guide alone, and all twelve worked.

Not covered: a reflex can read any file its user can read, and a read can go outside (`curl`), so a dry run is not
private; commands and `os.getenv` get the environment the host gives them (the caller's own, for the local host); a
reflex does what its caller could do, no more and no less. Who may approve what is each host's business.

## Where this is going

The reflex is the first of three levels. Next come **habits**: a reflex wired to a moment (around a tool call,
around a reply, on a clock), with a trust that is earned; and, since a prompt has room for some thirty reflexes,
which ones to show for the request at hand. Then the **dream**: what the agent does again and again, compiled into
reflexes and proven on its own history before it is proposed. The reasons for what is built so far are in
[docs/DESIGN.md](docs/DESIGN.md).

MIT.
