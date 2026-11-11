# habits/0.1

This page is the format of a reflex and the rules around it. It is for someone who writes a reflex, or builds another
runner for reflexes. Every argument, limit and message is in [docs/REFERENCE.md](docs/REFERENCE.md). What the plugin
does on your machine is in [docs/SAFETY.md](docs/SAFETY.md).

The words "must" and "must not" mean a rule the checker enforces. "Should" means advice.

## The words

| Word | Meaning |
| --- | --- |
| **reflex** | The file that runs: `assets/reflex.yml`. A few steps. |
| **habit** | A reflex plus its story: its skill, its state, its numbers. |
| **step** | One line of a reflex. The YAML key that holds the steps is `nodes`. |
| **start** | What sets a reflex off. The YAML key is `trigger`. |
| **done / handed back** | The only two ways a run comes back. |
| **decision model** | The small model that answers `ask` steps: Jev, else Hermes's own model. |
| **runner** | The part that runs a reflex. Here it is the Hermes plugin Habits. |

## 1. The file

A habit is a skill folder. `SKILL.md` holds its name, its description and the slow way to do the task by hand.
`assets/reflex.yml` holds the reflex:

```text
~/.hermes/skills/tidy-downloads/
├── SKILL.md
└── assets/
    └── reflex.yml
```

- A reflex saved under the older file name `assets/HABIT.yaml` is still read. With both files, `reflex.yml` wins.
- A helper file that a step calls lives in `assets/` too. It is part of the habit: changing it is a new version.
- The file is YAML 1.2, so only `true` and `false` are booleans: `yes` is text. It takes no YAML alias (`*name`).
- The file is 256 KB at most.
- Hermes can also run a one-off reflex that is not saved. `habit_run` takes it as an object, as YAML or JSON text,
  or as the path of a `.yaml`, `.yml` or `.json` file.
  A one-off reflex the model writes acts for real unless `dry: true`. It never deletes and never runs a command Hermes flags.

## 2. Top-level keys

| Key | Required | Value |
| --- | --- | --- |
| `spec` | yes | `habits/0.1` |
| `trigger` | no | The start: `message`, `skill`, or `{every: 30m}`. No `trigger` means "called". See section 6. |
| `inputs` | no | Names of the values the caller gives: `[page, depth]`. |
| `start` | yes | The name of the first step. |
| `fallback` | yes | The name of a `wake` step. Every run that goes wrong ends there. |
| `min_confidence` | no | Below this, an answer from the decision model is a doubt. A number from 0 to 1. |
| `limits` | no | `seconds`, `steps`, `tools`, `questions`, `llm`. See REFERENCE. |
| `nodes` | yes | 1 to 20 steps, each under its name. |

Step names match `[a-z0-9_-]{1,40}`. Variable names match `[A-Za-z_][A-Za-z0-9_]*`. A habit name matches
`[a-z0-9][a-z0-9_-]{0,39}`. Other top-level keys are ignored; do not add any.

## 3. Steps

A step holds exactly one of these seven keys, and only the fields in its row.

| Step | What it does | Its own fields | Where it goes next |
| --- | --- | --- | --- |
| `do` | Acts: calls a Hermes tool. | `args`, `read: true` when it only reads | `next`, optional `error` |
| `ask` | Decides: a closed question for the decision model. | one answer type | `then: {answer: step}`, or `next` |
| `check` | Tests a simple rule. No model. | `empty`, `changed`, `equals`, `contains`, `greater` | `then: {yes, no}` |
| `use` | Calls another habit by name, 3 levels deep at most. | the habit's name | `next`, optional `error` |
| `end` | Finishes: done. | a message | none |
| `wake` | Finishes: handed back. | a message for Hermes | none |
| `llm` | Advanced. One structured call to Hermes's model, no tool. | `schema` | `next`, optional `error` |

An `ask` has exactly one answer type:

- `yes_no: true`
- `choice: [a, b, c]`, or a mapping `{option: description}` (2 to 255 options)
- `score: [lowest, ..., highest]` (2 to 10 levels)
- `pick: <list>`: choose one item of a list variable. It takes `next`, and an optional `unsure` step.

An `ask` also takes an optional `min_confidence`. Put `other` last in a `choice`: the model leans to the first option.

Two rules about exits:

- A `do`, `llm` or `use` step needs `next`. `error` alone is not enough.
- `ask` and `check` have no `error` exit. Their failures go to the fallback.

Count and compare dates in a `check`, never in an `ask`. An `llm` step never holds a tool.

## 4. Modifiers

| Modifier | On | Meaning |
| --- | --- | --- |
| `as: name` | `do`, `ask`, `llm`, `use` | Keep the step's value in a variable. |
| `each: list` | `do`, `ask`, `llm` | Run once per item of a list variable. A text is split into lines. |
| `where: {var: value}` | with `each` | Keep the items whose value matches, compared as text. |
| `with: [vars]` | `ask`, `llm`, `use`, `end`, `wake` | Give variables to the step. |
| `doubts: drop` | `ask` with `each` | Leave an unsure item out instead of making it a doubt. |
| `margin: 0.15` | `ask` with `pick` | How far the top item must stand over the second. |

`with` means: the shared state of an `ask` or `llm`, the inputs of a `use`, the values returned by an `end` or `wake`.

## 5. Values and templates

- A variable comes from `inputs`, from an `as`, or from the runner. The runner sets three: `item` (the current
  item, inside `each` only), `doubts` (the items set aside, each with its step, reason and confidence) and `trigger`
  (the message that started the run, or `""`). `as` and `inputs` must not use these three names.
- A `do` stores the `output` field of the tool result when it has one, else the parsed JSON, else the text.
- After an `each`, the variable is a table: item to value. Inside a later `each`, `{kind}` is the current item's entry.
- A template is `{variable}`, `{variable.field}` or `{variable.0}`. There is no expression and no filter.
  Templates are filled in the texts of `ask`, `llm`, `end` and `wake`, and in `do` arguments.
- In a terminal `command`, each value is quoted as one shell argument by the runner. Write `mv {item} ~/.Trash/`,
  never `mv '{item}' ~/.Trash/`. A value that contains `..` or a backtick, starts with `-` or spans lines is refused.
- A missing value stays as written in a text. In a `do` argument it is a tool error.

## 6. The four starts and the two outcomes

| Start | In the file | Set off by | Where the result goes |
| --- | --- | --- | --- |
| called | no `trigger` | Hermes calls `habit_run` with the name | The reply to the call |
| message | `trigger: message` | A user message asks for it | Done: it answers. Handed back: Hermes gets the rest |
| schedule | `trigger: {every: 30m}` | A cron job (`hermes habits tick`) | Done: silence. Handed back: Hermes wakes |
| skill | `trigger: skill` | Hermes loads the reflex's own skill | The skill's result gets one more field, `habits` |

- `every` is a whole number then `m`, `h` or `d`. `{every: 1d, at: "21:00"}` runs at a time of day; `at` is 24-hour,
  quoted, and goes with `1d` only.
- A start on a skill takes no `inputs` and no `ask` or `llm` step. It runs once per session, in 10 seconds at most.
- Hermes can also call any habit by name. Whether it then acts for real depends on its state (section 9).

A run comes back in one of two ways:

- **Done.** The run reached an `end` step and acted for real.
- **Handed back.** The run reached a `wake` step, or something went wrong. It always lists what is left, and says
  what Hermes should do: only that. In doubt a reflex does not guess.

An unsure answer inside `each` puts the item in `doubts`. A later `each` skips it. An `end` reached with any doubt
becomes a hand back: a doubt is never lost.

## 7. Checks before a reflex runs

A reflex that breaks a rule or a safety check does not run and is not saved. Each issue names the rule, the step and
the fix:

```text
rule 3 (no loop): "look" goes back to "look". Repeat with "each" or with the trigger.
```

The runner checks when a reflex is run, when it is saved, when habits load, and in `hermes habits check <file>`.

| # | Rule | A valid reflex... |
| --- | --- | --- |
| 1 | no isolated step | reaches every step from `start` |
| 2 | no dead end | gives every step but `end` and `wake` an exit to a step that exists |
| 3 | no loop | never comes back to a step; it repeats with `each` or with a start |
| 4 | a fallback | names a `wake` step as `fallback` |
| 5 | one thing per step | has one key per step, one answer type per `ask`, no tool in an `llm` |
| 6 | no orphan answer | maps every answer of an `ask`, and both `yes` and `no` of a `check` |
| 7 | small | has 20 steps at most and every limit under its cap |

Four safety checks:

1. **Deleting.** Free-code tools (`execute_code`, `delegate_task`) and tool actions that remove data are always
   refused. A deleting terminal command (`rm`, `rmdir`, `shred`, `unlink`, `dd`, `find -delete`) is accepted in a file
   only under the setting `permissions: hermes`. It then runs only live, in a Hermes turn, for a habit that has your
   yes given in a chat, and Hermes's own approval rules decide. A test run only plans it. Otherwise it is refused.
2. **Watches.** A schedule start runs 60 seconds and 20 tool calls at most.
3. **`read: true` only reads.** It is refused unless the tool is a read tool or the terminal command is made only
   of readers. REFERENCE lists them.
4. **No `llm` value as code.** Text written by an `llm` step may enter a terminal command only as one quoted operand,
   under the setting `model_text: quoted`. It never takes the place of the program or of the code it runs. Under `never` it
   does not enter a command at all.

A runner must also:

- take the same path every time it gets the same answers and the same tool results: a run has no randomness;
- treat its own failure like the absence of the plugin: log it, stop the run, let Hermes go on;
- go to the fallback when a budget runs out, a tool fails with no `error` exit, or the decision model does not answer.

## 8. A test run

`habit_run` with `dry: true`, and `hermes habits run` without `--live`, run a reflex as a test:

- Reads run for real: `read: true` steps, read tools, and terminal commands made only of readers.
- `ask`, `check` and `llm` steps run. They use the decision model, so a test run may call a model.
- Every other `do` is only planned: listed, not done. So is every file an `end` would send.
- Nothing is recorded or scored, and the reference of a `check: changed` does not move.
- A test run is done only if it ended and planned nothing.

The dream and the learning of a habit use the same test run beside Hermes.

## 9. The three states

A habit that starts on a message, a schedule or a skill is in one of three states. You see them in `/habits`.

- **learning**: the reflex runs as a test beside Hermes. It never acts alone.
- **automatic**: the reflex acts on its own when its start fires.
- **resting**: it does nothing on its own.

| Move | What moves it |
| --- | --- |
| saved, then learning | A new reflex with a message or schedule start. A reflex copied in by hand. |
| learning, then automatic | Enough compared runs that agree with Hermes, then your yes. `learning: auto`: no yes (a message or schedule habit, write steps included, or any ready dream draft; a dream draft with a deleting or approval-asking command always waits for your yes). |
| saved, then automatic | A new message habit that only reads, saved because your message asked to keep the task. |
| automatic, then learning | `/habits wrong`. Or a message habit that handed back 3 times in a row. |
| any, then resting | You say rest. Or the rules: low agreement over the last runs, or 3 errors in a row. |
| resting, then learning | A new version, `/habits try`, or `hermes habits reset`. |

- Agreement is the share of planned writes that Hermes also made. REFERENCE has the numbers for each caution.
- A state belongs to a version: the fingerprint of the reflex and of the other files in `assets/`.
  Any change is a new version, in learning again.
- Only you change a state. The guard reads only the model's calls that visibly concern the plugin's world (the
  `hermes habits` command, its data and code folders, a habit's files, the settings). In a call that concerns the
  plugin it refuses what it cannot read; any other call passes untouched. `HABITS_OFF` turns every hook off except
  the guard. What it cannot see is a known limit, named in docs/SAFETY.md, section 6.
- A reflex the dream made starts as a draft, not as a habit. Your yes saves it, with the start "called", and makes it
  automatic. REFERENCE tells the life of a draft.

## 10. A whole example

The shipped example `examples/tidy-downloads` sorts the Downloads folder on macOS.

```yaml
spec: habits/0.1
trigger: message                # runs when the user's message asks for it (it answers on its own after the user's yes)
start: list
fallback: help                  # every run that goes wrong ends here
min_confidence: 0.8
nodes:
  # Read-only steps: they also run while the habit is learning.
  # 40 files at most: each file costs one step per each step (four), and a run has 200 steps by default.
  list:
    do: terminal
    read: true
    args: {command: "ls -p ~/Downloads | grep -v / | head -n 40"}
    as: files
    next: apps
  apps: {do: terminal, read: true, args: {command: "ls /Applications"}, as: apps, next: classify}
  classify:                     # one question per file, in one call; kind becomes a table file -> answer
    ask: "What type of file is this?"
    choice: [installer, image, document, archive, other]   # "other" last: the model leans to the first option
    each: files
    as: kind
    next: move
  move:                         # a write, only planned in a test; {item} and {kind} are shell-quoted
    do: terminal
    each: files
    args: {command: "mkdir -p ~/Downloads/{kind} && mv -n ~/Downloads/{item} ~/Downloads/{kind}/"}
    next: installed
  installed:                    # installers only, with the list of apps as shared state
    ask: "Is this application already installed?"
    yes_no: true
    each: files
    where: {kind: installer}
    with: [apps]
    as: installed
    next: trash
  trash:                        # never delete: the Trash is emptied by hand
    do: terminal
    each: files
    where: {installed: yes}     # YAML 1.2: yes is text, the answer itself
    args: {command: "mv -n ~/Downloads/installer/{item} ~/.Trash/"}
    next: doubts
  doubts: {check: {empty: doubts}, then: {yes: done, no: help}}
  done:   {end: "Files sorted by type. 40 files at most per run: run it again for the rest."}
  help:
    wake: "I sorted what I could (40 files at most per run). Some files are left to decide: the report lists them."
```

When every answer is confident and every move works, the run ends on `done`. An unsure file or a failed move becomes a
doubt. Then `doubts` leads to `help`, which hands the task back with the files that are left.
