# The lab

Where what the README claims was measured, and where what comes next will be. Nothing here is needed to use the
plugin. Two questions, each with its bench; both judge by what ends up on disk, not by what a model says.

## 1. Can a model write a reflex? (`write.py`)

Eight small real tasks (`tasks.py`): sort a folder by extension, triage notes with the small model, report on a git
project, watch a folder with a memory, find the broken links of a page, bump a version the big model decides,
delete old temporary files, summarize a log that may be missing.

A fresh model gets the guide (`reflexes/GUIDE.md`) and one task statement, and writes the reflex, first try: it
cannot run or check it. `write.py` then plays the reflex for real in a throwaway home (dry, live, and again where
the task has a memory), answers its questions as the big model would, and checks the files and the result.
`reference/` holds one reflex per task that passes.

```bash
python bench/write.py sort bench/reference/sort.py      # one task, one candidate
python bench/write.py --all <folder>                     # <folder>/<guide>/<task>/<model>-<n>.py
```

**101 of 104 worked, first try**: Claude Haiku 5.5 62 of 64, Sonnet 5.5 31 of 32, Opus 5.5 8 of 8, over three
rounds; the last one, 24 of 24, with the guide and the engine as they are now. An earlier round compared three
guides on 96 reflexes: the guide with all six calls 31 of 32, one with four calls only 32 of 32, a 300-token card
28 of 32. Each writer made three tool calls: read the guide, read the task, write the file.

## 2. Does a real Hermes agent take them? (`hermes/`)

One run is one world: a throwaway home folder with real files, a Hermes home of its own, and a request typed as a
user would (`hermes -z`). Two arms: `hand` (no reflex: the agent does the task by hand) and `plugin` (the plugin at
the root of this repository). Afterwards the world is read: what is on disk, every call to `reflex`, every tool
call and model request, and what Hermes says the session cost.

| File | What it is |
| --- | --- |
| `run.py` | builds the worlds, runs the sessions, writes the records, prints the report |
| `base.py` | the home every world starts from, and the three reflexes its user has |
| `scenarios.py` | what is asked, and what must be true afterwards |
| `mock.py` | a scripted fake model: the whole chain with no key and no cost |
| `observer/` | a Hermes plugin that only watches: tool calls and model requests |
| `confine.py` | the fence: a session writes only in its world and cannot read the home of whoever runs the bench |
| `crowd.py` | sixty more reflexes, for when there are many |
| `loaded.py` | a made-up Hermes home after a year of use, to crowd the agent's prompt |

| Scenario | What it asks |
| --- | --- |
| `health`, `triage`, `preview`, `input` | four short routines: a report, a sort that asks, a dry run, an input to pass |
| `none` | a request no reflex fits |
| `write` | "make that a reflex" |
| `twice` | a first session makes a reflex; a second, new, asks for the same kind of thing in other words |
| `stopped`, `pushed` | the reflex stops halfway (a file in the way; a refused `git push`): what does the agent do next? |
| `backup`, `report` | two routines of about ten commands by hand, each with a convention to follow |

```bash
pip install -e ".[dev]"
python bench/hermes/run.py run /tmp/lab --hermes "$(which hermes)"        # the fake model: every scenario, minutes
python bench/hermes/run.py report /tmp/lab

HERMES_HOME=~/bench-login hermes auth add nous --no-browser                # a sign-in made for the bench
python bench/hermes/run.py run /tmp/lab --login ~/bench-login --provider nous --model deepseek/deepseek-v4-flash --reps 3
```

The sign-in is never the one of a Hermes in use: the bench renews what it is given, and a sign-in renewed in two
places is lost in both. Everything a run leaves is under the folder given to `run`.

### What was found

973 real sessions on Hermes Agent 2026.9.24, $1.85 in all. Models: DeepSeek V4 Flash and Claude Haiku 5.5 through
Nous Portal, and for the loaded profile DeepSeek V4.1 Flash set to think as hard as it can. Two to five runs a
cell: enough to tell 0 from 60, not 80 % from 90 %.

**How must the agent be told it has reflexes?** Five ways were tried; only the last is still in the tree.

| How the agent learns it has reflexes | Took the one that fits | Task done |
| --- | --- | --- |
| nothing: by hand | - | 46 of 56 |
| a note in `AGENTS.md`, written once | 45 of 60 | 74 of 80 |
| a Hermes skill | 0 of 24 | 24 of 32 |
| a Hermes tool | 0 of 24 | 26 of 32 |
| the plugin: the note, in the prompt, asked at each session | 60 of 60 | 75 of 80 |

A cheap model takes a reflex when its name and what it does are in front of it, and never goes looking for one.
The skill is one line in an index and the tool sits behind Hermes's tool search: both were reached only when the
request said the word "reflex".

**Does the next session find what the last one made?** With a note written once, never (0 of 10). With the plugin,
always (10 of 10), also when a bigger model wrote the reflex: Sonnet 5.5 wrote it in three or four requests, for
three cents, and both cheap models then took it (6 of 6).

**What does it save?** It depends on the hands it replaces, and on how long the routine is.

| | Requests | Tool calls | Input tokens | Cost |
| --- | --- | --- | --- | --- |
| short routines, Flash by hand | 5.2 | 7.4 | 68 k | 0.26 ¢ |
| short routines, Flash with the reflex | 2.4 | 1.4 | 27 k | 0.15 ¢ |
| short routines, Haiku by hand | 2.5 | 1.6 | 37 k | 0.12 ¢ |
| short routines, Haiku with the reflex | 2.9 | 1.9 | 42 k | 0.11 ¢ |
| long routines, Flash by hand | 5.5 | 9.8 | 69 k | 0.29 ¢ |
| long routines, Flash with the reflex | 2.3 | 1.3 | 28 k | 0.14 ¢ |
| long routines, Haiku by hand | 3.8 | 2.8 | 60 k | 0.16 ¢ |
| long routines, Haiku with the reflex | 2.8 | 1.8 | 42 k | 0.08 ¢ |

On the long ones the user also types three words instead of sixty: by hand the routine has to be spelled out each
time. Every session pays 10 to 14 k tokens of Hermes's own prompt before its first word; the note adds about 280.

**Does it carry what cannot be guessed?** By hand no agent filed notes the owner's way (0 of 18). With the reflex
in its prompt, 20 of 20, twice without even running it: its one-line description was enough.

**What does the agent do after a reflex stops?** In `stopped` a file is in the way of the second of three steps.
By hand Haiku left it alone 7 times of 7 and Flash 4 of 7. After the reflex stopped, Flash forced its way through
19 times of 20. What the reflex says at that moment decides:

| What the reflex says when a command does not go through | `stopped`, Flash | `pushed`, Flash |
| --- | --- | --- |
| `stopped: RuntimeError: …` and what was already changed | destroyed 9 of 10 | finished 10 of 10 |
| "Nothing is broken … leave the command as it is, tell the user" | destroyed 4 of 10 | finished 4 of 10 |
| "leave that command alone: whether to force it is for the user to say" | finished 0 of 10 | finished 0 of 10 |
| the first one, plus one line: "Finish the task by hand. One rule: never overwrite, delete or force anything to get past what stopped it. If that is the only way, leave that part and tell the user." | destroyed 0 of 10, finished 10 | finished 10 of 10 |

The careful wordings stopped the damage by stopping the work. The smallest change won, and is in the command.
Measured again with it on both scenarios and both models: of 45 sessions nothing was destroyed, 40 finished the
task, 5 stopped and said so.

**How many reflexes can there be?**

| Reflexes in all | The one that fits is in the note | Taken | A wrong one taken |
| --- | --- | --- | --- |
| 13 | yes | 24 of 24 | 0 |
| 33 | yes | 23 of 24 | 0 |
| 63, it was run before | yes | 24 of 24 | 0 |
| 63, never run | no: cut for lack of room | 0 of 24 | 0 |

Sixty-three reflexes do not confuse a cheap model. What runs out is room: Hermes gives a plugin 4,000 characters,
about 35 reflexes. What is cut from the note is gone: "and 28 more: `reflex list` shows them all" was followed 0
times of 24. The note keeps what was run or written most recently; choosing by what the request is about is where
habits begin.

**The small model.** With a decision model set up (TypeSafe Jev), the triage reflex no longer asks the agent: it
ends by itself in half a second, every note filed right (10 of 10), and Flash needs 2 requests instead of 3.

**A loaded profile.** 104 skills instead of 44, a SOUL.md and a memory in French, the prompt 80 % longer.

| Model | Took the reflex that fits | Done, with the plugin | Done, by hand |
| --- | --- | --- | --- |
| DeepSeek V4.1 Flash, thinking hard | 27 of 27 | 29 of 30 | 21 of 24 |
| Claude Haiku 5.5 | 27 of 27 | 30 of 30 | 21 of 24 |
| DeepSeek V4 Flash | 22 of 27 | 30 of 30 | 18 of 24 |

Flash is distracted a little, and only on the short routines (7 of 12), which it then does by hand.

**What an agent did that it should not.** One Flash session, looking for the place where reflexes are saved, read
only the head of the guide, missed the line that said it, and wrote its reflex into the repository the bench ran
from. Since then the guide says where a reflex goes in its first line, and every session is fenced.

### What it does not measure

- One request a session, through `hermes -z`: no conversation of several turns, no gateway, no cron.
- A world has no real profile's plugins or MCP servers.
- A reflex's commands run with the command's own host, not through Hermes's terminal tool.
- Nothing has run on macOS or on ARM.
