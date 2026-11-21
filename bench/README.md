# The benches

Two questions decide whether reflexes are worth having: can a model write one, and does a model that has some use
them well? Each has a bench. Both run fresh model instances that know nothing of this project, and both judge by
what ends up on disk, not by what the model says.

## Writing (`tasks.py`, `run.py`)

Eight small real tasks: sort a folder by extension, triage notes with the small model, report on a git project,
watch a folder with a memory, find the broken links of a page, bump a version the big model decides, delete old
temporary files, summarize a log that may be missing.

A model gets the guide (`reflexes/GUIDE.md`) and one task statement, and writes the reflex, first try: it cannot
run or check it. `run.py` then plays the reflex for real in a throwaway home (dry, live, and again where the task
has a memory), answers its questions as the big model would, and checks the files and the result.

```bash
python bench/run.py sort bench/reference/sort.py           # one task, one candidate
python bench/run.py --all <folder>                          # <folder>/<guide>/<task>/<model>-<n>.py
```

`reference/` holds one reflex per task that passes; `guides/` the three guides compared on 21 November 2026.

| Round | Reflexes | Work, first try |
| --- | --- | --- |
| three guides, Haiku and Sonnet | 96 | 91: the full guide 31 of 32, four verbs only 32 of 32, a 300-token card 28 of 32 |
| the final guide | 48 | 46: Haiku 22 of 24, Sonnet 16 of 16, Opus 8 of 8 |
| the final guide, after the reviews | 32 | 31: Haiku 24 of 24, Sonnet 7 of 8 |

What failed: a `df` column read wrongly (twice), a reflex that read back in a dry run what it had only planned, a
missing-file case not caught, a sub-folder moved with the files, a command that is no reader (`printenv`), and one
misuse of `rx.ask`. A forgotten `import re` failed once before the engine made the everyday modules available
without an import. In the last round the one failure was `curl --max-redirs`, which the table of reads refused:
harmless options like it were added, and all 32 pass when replayed.

## Using (`use.py`)

Six worlds, each a home folder with three reflexes installed and a `reflex` command that logs its calls. An agent
gets one request and the short note a host would put in its prompt (`INDEX` in `use.py`), and works with its own
shell.

```bash
python bench/use.py make <folder>      # build the six worlds; each holds its prompt.md
python bench/use.py check <folder>     # afterwards: which reflex calls were made, what is on disk
python bench/use.py stats <records>    # tool calls per agent, from a workflow's records
```

On 21 November 2026, five runs per world (Haiku twice, Sonnet twice, Opus once): 30 of 30 right. A request a reflex
fits took one `reflex run` and nothing by hand; a reflex that asks was answered through `reflex answer`; "show me
first" was a `reflex test`; a request no reflex fits ran none; "make that a reflex" took `guide`, one file written,
`test`, `run`.

## What these benches do not say

They ran on Claude models only; a cheap model of another family is still to measure. The tasks are ours. The use
bench gives the agent an explicit note about its reflexes: whether a model thinks of a reflex with less prompting
is the question the habit layer exists for.
