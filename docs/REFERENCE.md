# Reference

Everything you can look up: every step and modifier, every setting, every command, the dream in full, and the limits
of a run. The short version of the format is [SPEC.md](../SPEC.md). What the plugin does on your machine, and what
leaves it, is [SAFETY.md](SAFETY.md).

Hermes thinks. A reflex acts. The dream learns. A **habit** is a reflex plus its story: its skill, its state, its
numbers. A run always comes back **done**, or **handed back** with what is left.

Every example below is a file that was run in a throwaway Hermes home, with no key and no model turn. Where a step needs the
decision model (`ask`, `llm`), the example was checked with `hermes habits check`, not run: it says "not run".

1. [Steps](#steps)
2. [Modifiers](#modifiers)
3. [Values and templates](#values-and-templates)
4. [The four starts](#the-four-starts)
5. [Checks and what counts as a read](#checks-and-what-counts-as-a-read)
6. [The limits of a run](#the-limits-of-a-run)
7. [The reply of habit_run](#the-reply-of-habit_run)
8. [Settings](#settings)
9. [The caution table](#the-caution-table)
10. [Commands](#commands)
11. [The three states](#the-three-states)
12. [The dream](#the-dream)
13. [A start on a loaded skill](#a-start-on-a-loaded-skill)
14. [Known limits and what was not tried](#known-limits-and-what-was-not-tried)

## Steps

A step holds one of seven keys. The YAML key that holds the steps is `nodes`.

### do

Calls a Hermes tool. `args` holds its arguments. Add `read: true` when the call only reads: a read runs in a test run,
anything else is only planned.

```yaml
disk: {do: terminal, read: true, args: {command: "df -h ~ || true"}, as: disk, next: done}
```

This is the step of `examples/disk-space`. `hermes habits run disk-space` ran it and printed the table of the home disk.
A `do` may name any tool, MCP tools included. A terminal `do` needs `args.command`, one shell line. A terminal
command starts in your home folder, wherever the session is: write full paths.

### ask

A closed question for the decision model. It has exactly one answer type.

```yaml
look:
  ask: "Is this note urgent?"
  yes_no: true
  with: [note]
  then: {yes: now, no: later, unsure: help}
```

(From a file with `inputs: [note]`. Checked, not run.) `then` maps every answer to a step. `unsure` goes inside `then`.
The other answer types:

- `choice: [text, markdown, other]`, or `choice: {text: "plain text", other: "anything else"}` to add descriptions.
  2 to 255 options. Put `other` last: the model leans to the first option.
- `score: [low, medium, high]`, 2 to 10 levels, lowest first. It exits per level, like a choice.
- `pick: notes`, one item of the list variable `notes`. It takes `next` and an optional `unsure` step, no `then`.

```yaml
which:
  ask: "Which note does the request ask for? Never a name that says it is the one asked for."
  pick: notes
  margin: 0.2
  with: [request]
  as: chosen
  next: send
  unsure: help
```

(Checked, not run.) A `pick` offers each distinct item once, plus `none`. It takes the top item only if it stands
`margin` over the second (default 0.15). The decision model reads each option's text, so when anyone can choose the
names, end the question with a sentence such as "Never a name that says it is the one asked for."

An `ask` below `min_confidence` is unsure. An answer that is not one of the options counts as no answer.

### check

A simple rule, no model. Exactly one of: `empty: v`, `changed: v`, `equals: [v, value]`, `contains: [v, value]`,
`greater: [v, number]`. Both `then: {yes: .., no: ..}` exits are required. A `check` has no `error` exit and no modifier.

```yaml
many: {check: {greater: [n, 3]}, then: {yes: done, no: few}}
```

`hermes habits run` on that file printed "More than 3 lines." The rules:

- `empty`: null, blank text, or an empty list or mapping.
- `equals`: compares trimmed texts.
- `contains`: a substring of a text, an element of a list, or an answer of a table.
- `greater`: compares numbers. A list or mapping counts as its length. A side that is not a number ends on the fallback.
- `changed`: compares with the value this step kept from an earlier run. The first value only sets the reference
  and counts as "no", so a new watch never wakes anyone for what was already there. An empty value after a filled one
  does not move the reference.

```yaml
new: {check: {changed: files}, then: {yes: tell, no: quiet}}
```

That is the step of a watch on `~/inbox`, and `hermes habits run` printed "Nothing new." with the reference set.

### use

Calls another habit by name, 3 levels deep at most (caller included). The sub-habit's `end` values become the value.

```yaml
space: {use: disk-space, as: report, next: done}
```

`hermes habits run` on that file printed the disk report of `disk-space`.

### end and wake

`end` finishes the run as done, with a message. `wake` hands the run back, with a message for Hermes. Both take `with`.

```yaml
done: {end: "{out}", with: [out]}
help: {wake: "Could not greet {who}."}
```

The `fallback` key names a `wake` step. Every run that goes wrong ends there, with the reason. An `end` reached
with any doubt left becomes a hand back: a doubt is never lost.

### llm (advanced)

One structured call to Hermes's own model, no tool. Use it when a closed question cannot decide. `schema` names the
fields it returns: `string`, `number`, `boolean` or `list`.

```yaml
sum:
  llm: "Summarize the text in one sentence."
  schema: {summary: string}
  with: [text]
  as: result
  next: done
```

(Checked, not run.) Without `schema` the value is a string. The call is made with Hermes's model whatever the
decision model is. A value an `llm` step wrote may enter a terminal command only as one quoted operand (setting
`model_text`). An `llm` step holds no `args`, `tools` or `read`.

## Modifiers

| Modifier | On | Meaning |
| --- | --- | --- |
| `as: name` | `do`, `ask`, `llm`, `use` | Keep the step's value in a variable |
| `each: list` | `do`, `ask`, `llm` | Once per item, in order; a text is split into lines |
| `where: {var: value}` | with `each` | Only items whose value matches, compared as text |
| `with: [vars]` | `ask`, `llm`, `use`, `end`, `wake` | State, inputs or returned values |
| `doubts: drop` | `ask` with `each` | An unsure item is left out, not a doubt |
| `margin: 0.15` | `ask` with `pick` | How far the top item must stand over the second |

**as.** `greet` printed "hello Emi" with this step: `say: {do: terminal, read: true, args: {command: "echo hello
{who}"}, as: out, next: done}` and `--input who=Emi`.

**each.** The step runs once per item. In a test run the writes are planned, one per item:

```yaml
list: {do: terminal, read: true, args: {command: "printf 'a.txt\nb.md\nc.txt\n'"}, as: files, next: move}
move: {do: terminal, each: files, args: {command: "mkdir -p ~/sorted && mv -n ~/inbox/{item} ~/sorted/"}, next: done}
```

`hermes habits run` on that file planned three `mv` commands and wrote nothing. An item that fails or is unsure
goes into `doubts`; every later `each` skips it. When every item fails, the step takes its `error` exit.

**where.** With `each`, it keeps the items whose value (in an earlier table) equals the text. Write `{installed: yes}`,
not `true`: a `yes_no` answer is the text `yes`.

```yaml
move:
  do: terminal
  each: files
  where: {seen: a.txt}
  args: {command: "mv -n ~/inbox/{item} ~/text/"}
  next: done
```

`seen` came from an earlier `each` step. The test run planned one `mv`, for `a.txt`. Without `each`, `where` does nothing.

**with.** An `ask` or `llm` gets the variables as shared state (sent once, cut to 8,000 characters). In an `ask` with
`each`, a table of an earlier `each` gives each question its item's entry. A `use` gets them as inputs. An `end` or
`wake` returns them as `values`; a name with no value is null.

**doubts: drop.**

```yaml
kind:
  ask: "Is this file a note?"
  yes_no: true
  each: files
  doubts: drop
  as: note
  next: done
```

(Checked, not run.) A file whose answer is unsure gets no entry and is not a doubt, so the run is not handed back for
a file the reflex does not need. The report still lists it.

**margin.** See `pick` above.

## Values and templates

- A variable comes from `inputs`, from an `as`, or from the runner. The runner sets `item` (inside `each` only),
  `doubts` (items set aside, each with step, reason and confidence) and `trigger` (the message that started the run,
  the check time of a watch, or `""`). `as` and `inputs` must not use these three names.
- An `ask` stores its answer (a `pick`: the item). A `do` stores the `output` field of the tool result when it has
  one, else the parsed JSON, else the text. An `llm` stores its schema's object, else a string. A `use` stores the
  `with` values of the sub-habit's `end`, else its message. A `check` stores nothing.
- A tool result with an `error` field, or a non-zero `exit_code`, is an error. A read that exits 1 with no output
  (grep found nothing) gives `""`. A read that is one pipe fails when any command before the last one fails.
- The text of a value: a string as is; null as `""`; a list of strings, one item per line; anything else as JSON.
  `where`, `equals`, `contains`, options and `then` keys compare texts.
- A template is `{variable}`, `{variable.field}` or `{variable.0}`. No expression, no filter. An argument that is
  exactly one template gets the value itself (a list stays a list). Anywhere else it gets the text.
- In a terminal command each value is quoted for you. Write `mv {item} ~/.Trash/`, never `'{item}'`. Write a literal
  brace as `{''word}`: `awk '{''print $1}'`. A value that contains `..` or a backtick, starts with `-` or spans
  lines is refused.
- In the message of an `end` or `wake`, a mapping of plain values is written one `key: value` per line.

| Where a value is missing | What happens |
| --- | --- |
| a text (`ask`, `llm`, `end`, `wake`) | the template stays as written |
| a `do` argument | a tool error: the `error` exit or the fallback; inside `each`, the item becomes a doubt |
| the variable of a `check` | the fallback |
| the source of an `each`, or one that is not a list or a text | the fallback |
| the `where` value of one item | the item is skipped; it is not a doubt |
| a `with` name of an `ask` or `llm` | the state shows `name: (no value)` |
| a `with` name of an `end`, `wake` or `use` | null |

The decision model reads its question and its state, nothing else. Keep the question short and the material in
`with`. It counts and compares dates badly: do that in a `check`. To judge every item use `each`; to find one among
many, use `pick`. Its confidence is how sure it sounds: leave `min_confidence` at 0.8.

## The four starts

| Start | In the file | Set off by |
| --- | --- | --- |
| called | no `trigger` | Hermes: `habit_run {name, inputs}`, or a reflex it writes on the spot |
| message | `trigger: message` | The hook before each user turn |
| schedule | `trigger: {every: 30m}` or `{every: 1d, at: "21:00"}` | A Hermes cron job that runs `hermes habits tick <name>` |
| skill | `trigger: skill` | Hermes loading the reflex's own skill ([below](#a-start-on-a-loaded-skill)) |

### Called

Hermes calls `habit_run` with a name, or with a one-off reflex (`reflex`, as an object or as YAML or JSON text; or
`file`, the path of a `.yaml`, `.yml` or `.json` file). With `habits: [..]` it calls 2 to 8 reflexes in one call (a list of one is taken too).

- A habit that starts on a message or a schedule runs for real by name only when it is automatic. Learning or resting,
  it runs as a test and hands the whole task back: acting uninvited is earned.
- A habit with no start of its own runs for real by name, whatever its state, except a resting one, which is refused
  until `hermes habits reset`.
- A one-off reflex the model writes acts for real unless `dry: true`. It never deletes and never runs a command Hermes flags.
- A second call while one runs answers `busy`. `message` gives the user's words to a reflex that reads `{trigger}`.
- The shipped examples run for real by name. The model's prompt lists only the ones that only read.

### Message

On each top-level user message of 2,000 characters at most (not a `/command`), the hook asks the decision model one
choice: "none", or one of your learning and automatic message habits, each shown with its description and up to 3 paths
or hosts it acts on. The pick needs `trigger_confidence`. Then one more choice on the same message: run it as it is, run
it differently, run it and more, or not now. Only "run" at `min_confidence` runs it. Nothing runs when the message
names a saved habit, mentions `habit_` tools, or when less than 2 s of the hook's 20 s are left.

- Learning: the reflex runs as a test, the model does the task, and the plugin compares the two.
- Automatic and done: on Hermes 0.22.6 or later with the setting `direct` on, the reflex's message is the reply and no model is called, unless
  the run read the web, a browser, an MCP tool, a server through `curl` or an agent's screen. Otherwise Hermes gets
  "Done: use this result as it is" and confirms in one sentence.
- Automatic and handed back: Hermes gets the report and `left`, and does only that.

A description should name its target and its way: "Sort ~/Downloads by file type", not "Sort a folder".

### Schedule

`every` is a whole number then `m`, `h` or `d` (Hermes cron counts in minutes), `5m` at the least. `at` goes with `every: 1d` only,
quoted and 24-hour: `{every: 1d, at: "21:00"}` is the cron job `0 21 * * *`. Saving a watch writes a small shell
file in the `scripts` folder of the Hermes home and a cron job `habits-<name>`. It does this when `habit_save` writes the files, before
your yes. Inside a scheduled run (a turn a cron job started) `habit_save` refuses a watch and says to save it from a
chat, unless you set Hermes's `cron.allow_agent_scheduling: true`: Hermes keeps scheduled runs from scheduling jobs. A learning watch writes nothing: when it would hand back or write, Hermes is woken with the report and does
the task itself, unattended (a paid model turn each time). A watch that checks more often than every 6 hours holds
a hand back caused by a failed read until the 3rd such check in a row. A daily watch wakes at once.
`hermes habits cleanup` removes every cron job and every file of `scripts` whose name starts with `habits-`.

## Checks and what counts as a read

A reflex that breaks a rule or a safety check never runs and is never saved. Each issue names the rule, the step and
the fix. The seven rules are in [SPEC.md](../SPEC.md#7-checks-before-a-reflex-runs). This was run:

```text
hermes habits check bad-loop.yml
rule 3 (no loop): "look" goes back to "look". Repeat with "each" or with the trigger.
```

For a valid file, `check` prints a line that starts with `valid:` and exits 0.

**Safety check 1, deleting.**

- A deleting terminal command names `rm`, `rmdir`, `shred`, `unlink`, `dd`, or `find -delete`, even behind `xargs`,
  `bash -c`, a path, or a brace or glob the shell turns into one. It is a check on words: it cannot see a deletion
  inside another program.
- Under `permissions: hermes` (the default), `check` and `habit_save` accept such a command. It runs only live,
  in the two cases of [permissions](#settings), and Hermes's own approval decides. A test run only plans it.
- Under `permissions: strict`, it is refused.
- Free-code tools (`execute_code`, `delegate_task`) and tool actions that remove data are refused under both:
  `skill_manage` with `delete` or `remove_file`, `cronjob_manage` with `remove`, `memory` with `remove` or `delete`.

**Safety check 2.** A schedule start runs 60 seconds and 20 tool calls at most, sub-habits included.

**Safety check 3, `read: true` only reads.** It is refused unless the tool is a read tool (`web_extract`,
`web_search`, `read_file`, `search_files`, `kanban_list`, `kanban_show`, `skill_view`) or the terminal command is made
only of readers. The command is read as a shell reads it: pipes, `;`, `&&`, `||`, `$( )`. A read passes only
`command`, `workdir` and `timeout`.

- Readers, any arguments: `ls cat head grep egrep fgrep wc stat cut tr jq column nl rev fold fmt expand basename dirname
  realpath readlink pwd whoami uname id du df echo test [ true false ps pgrep pidof uptime free vm_stat sw_vers md5
  md5sum shasum sha256sum diff cmp comm ioreg`.
- Readers without the options that write, run something or never end: `find` (no `-exec`, `-ok`, `-delete`, `-fprint`,
  `-fls`), `tail` (no `-f`), `curl` (a plain GET or HEAD), `sort` (no `-o`), `uniq`, `date` (no `-s`), `sleep` (10 s at
  most), `hostname`, `file`, `printf` (no `-v`), `env` (alone), `sed` (`-n -E -r -s -u -z -e`, no `w` or `e`), `awk`
  (no `system`, no pipe, no print to a file).
- Named subcommands only: `git` (`status`, `log`, `diff`, `show`, `blame`, `describe`, `ls-files`, `shortlog`,
  `rev-parse`, listing `branch` and `tag`, `remote -v`, `config --get`; with `-C <path>` and `--no-pager`, and only the
  options that choose what to print: no `-c`, `--output`, `--ext-diff`, `--textconv`, `--show-signature`), `hermes logs`
  (no `-f`), `hermes habits check|show|list`, `systemctl` (`is-active`, `status`, `show`, `list-units`), `journalctl`
  (no `-f`), `ssh` with `-o BatchMode=yes`, a host and a remote command that is itself a read.
- Redirections only to `/dev/null`, between streams (`2>&1`) or from a file. Anything else is a write: `xargs`, `eval`,
  a heredoc, a variable but `$HOME`, `$(( ))`, a subshell.
- The classifier also knows two command-line tools from the author's own setup (`hc` and `ob sync-status`).
  Machines that share reflexes should run the same plugin version: an older one refuses a newer reader.

A read that reaches another machine (`curl`, `ssh`, `hc`) runs in a test run only if the step says `read: true`.

**Safety check 4, no `llm` value as code.** Text written by an `llm` step, or an item, table or pick taken from it, fills
a terminal command only as an operand, after the options, of a program that takes it as data (the list is under
`model_text` below); in any other program, as the program itself, as a script or as code, it is refused.
At run time the filled command is checked again.

Also refused: a template inside quotes; a command with a template that holds double quotes, backslashes, backticks or
a heredoc outside single quotes; an `ssh` with no remote command (it would wait for a person); `trigger: {event: ..}`,
which no longer exists.

## The limits of a run

`limits:` in the file sets each budget, up to its cap. Going over ends the run on the fallback.

| Limit | What counts | Default | Cap | Schedule start |
| --- | --- | --- | --- | --- |
| `seconds` | wall time of the run | 120 | 600 | 60 |
| `steps` | each step but `end` and `wake`; each item of an `each` | 200 | 1,000 | 200 |
| `tools` | each tool call, planned calls included | 100 | 500 | 20 |
| `questions` | each question to the decision model | 300 | 2,000 | 300 |
| `llm` | each `llm` call | 5 | 20 | 5 |

`questions` and `llm` may be 0. A reflex with an `ask` under `questions: 0` is refused. Other limits of the plugin:

| Where | Limit |
| --- | --- |
| a reflex | 20 steps; the file is 256 KB |
| `use` | 3 levels deep, caller included |
| the message start | the hook has 20 s in all; a decision call stops at 5 s (Jev) or 15 s (Hermes's model) |
| the skill start | 10 s ([below](#a-start-on-a-loaded-skill)) |
| the dream's practice run | 10 s |
| `habit_run {habits: [..]}` | 1 to 8 entries, 300 s for the whole call |
| `ask` state | 8,000 characters |
| a decision call with `each` | up to 50 questions per call with Jev |

## The reply of habit_run

Every reply is JSON with `done` and `next`. `done` is true only when the whole task was done for real. These replies
come from the plugin's own tool function, called with a stand-in for Hermes's terminal tool, on the files above.

| Field | When | What |
| --- | --- | --- |
| `done` | always | true: done, use this result as it is; do not redo or re-check it |
| `status` | always | `end`, `wake`, `refused`, `busy`, `error` (and `not run` in a fan-out) |
| `message` | a run | the `end` or `wake` message, without `MEDIA:` lines |
| `values` | a run | the `with` values of the end (6,000 characters of JSON at most) |
| `left` | not done | what is left: the failing step, the doubts, writes planned and not done (20 lines of 160 characters) |
| `next` | always | one sentence: done, or handed back with why, do only what is left |
| `report` | a run | the head line, then the first 4 and last 8 steps; 1,500 characters at most |
| `issues` | refused | one line per broken rule or check |
| `dry`, `planned`, `planned_count` | `dry: true` | the writes and files it would make (20 lines at most) and how many |
| `normalized` | slips fixed | up to 5 fixes of the reflex's form |
| `repair` | a saved habit failed | ask the user before saving a repair with `habit_save` |
| `attach` | the end sends a file | the `MEDIA:` line to put in the reply |
| `untrusted` | the run read outside | message, report, left, planned and values, inside Hermes's untrusted block |

**Done** (`greet.yml` with `inputs: {who: Emi}`):

```json
{"done": true, "status": "end", "message": "hello Emi", "values": {"out": "hello Emi\n"}, "left": [],
 "next": "Done: use this result as it is; do not redo or re-check it.",
 "report": "end at 'done' after 0.0 s (rules only, 0 questions, 1 tool calls)\n- say: terminal ok"}
```

**Handed back** (a read of a folder that does not exist):

```json
{"done": false, "status": "wake", "message": "The listing failed.", "values": {},
 "left": ["step look and the steps after it: stopped: \"look\": exit 1: ls: /nonexistent-folder: No such file or directory"],
 "next": "Handed back (step look failed): do only what is left.",
 "report": "wake at 'help' after 0.0 s (rules only, 0 questions, 1 tool calls)\nreason: \"look\": exit 1: ..."}
```

**Refused** (`bad-loop.yml`; nothing ran):

```json
{"done": false, "status": "refused",
 "issues": ["rule 3 (no loop): \"look\" goes back to \"look\". Repeat with \"each\" or with the trigger."],
 "next": "Not run: fix the reflex from these issues and call again; after 3 refusals, do the task the usual way.",
 "left": ["all of it: nothing ran"]}
```

**Test run** (`dry: true` on `sort-by-extension.yml`; paths in backticks; `done` is true only for a test run that
planned nothing):

```json
{"done": false, "status": "end", "message": "Dry run (nothing written): Sorted a.txt\nb.md\nc.txt\n.", "values": {},
 "left": [],
 "next": "Dry run: nothing was written; it would do the 3 writes listed in planned. If they are right, call again without dry, or save it with habit_save.",
 "dry": true,
 "planned": ["move: terminal $ mkdir -p ~/sorted && mv -n `~/inbox/a.txt` ~/sorted/",
             "move: terminal $ mkdir -p ~/sorted && mv -n `~/inbox/b.md` ~/sorted/",
             "move: terminal $ mkdir -p ~/sorted && mv -n `~/inbox/c.txt` ~/sorted/"],
 "planned_count": 3}
```

(The `report` field is left out of that one.) **Busy:** `status: "busy"`, `left: ["all of it: nothing ran"]`.
**Fan-out:** the reply has `results` (one per entry, each with `done`, `status`, `message`, `values`, `left`, `next`),
a `summary` line and one `next`. One handed back never stops the next; an entry that would start with less than 2 s of
the 300 s left is `not run`.

While Habits is off (`HABITS_OFF` set, or the `off` file), every call is refused with "do the task the usual way".
A habit that is not automatic yet, called by name, runs as a test and answers `"left": ["the task itself: it ran dry
and wrote nothing"]`.

## Settings

Seven settings and the Jev key. Set them in Hermes's settings for the plugin (`plugins.entries.habits.settings` in
Hermes's config). A value the plugin does not know falls back on the default: a typo never turns a yes off.

| Setting | Values (default first) | What it does |
| --- | --- | --- |
| `learning` | `suggest`, `practice`, `off`, `auto` | How much the plugin learns and how much it decides alone |
| `caution` | `normal`, `relaxed`, `strict` | How much proof a habit needs ([table](#the-caution-table)) |
| `dream` | `daily`, `manual` | When the dream re-reads Hermes's history |
| `decision_model` | `auto`, `jev`, `hermes` | Who answers `ask` steps |
| `permissions` | `hermes`, `strict` | Whether a habit with your yes may run what Hermes's approval allows |
| `model_text` | `quoted`, `never` | Whether text a model wrote may enter a command |
| `language` | `en`, `fr` | The language of the plugin's own lines |
| `typesafe_api_key` | secret; or the env variable `TYPESAFE_API_KEY` | The Jev key |

**learning.**

- `off`: nothing is spotted, practiced or dreamed, and a message habit that is learning does not practice. Habits that
  are already automatic keep acting. `hermes habits dream` says learning is off.
- `suggest` (the default): on a fresh install the dream only reads and shows. Nothing runs and nothing practices until you say try (`/habits try <name>`) or set `practice` or `auto`. Message habits as in `practice`.
- `practice`: the plugin re-runs by itself, in the background, local read commands Hermes just ran, in the same folder, at most once per draft per 10 minutes, three drafts at a time. Every habit waits for your yes.
- `auto`: a message or schedule habit that meets its thresholds becomes automatic with no yes, write steps included (it never gets the permissions of a yes). Every ready dream
  draft is saved and made automatic by the rules, with no question and one line of news (within the one line a
  day): one step or several, reading or acting. The exception: a draft that holds a deleting command, or a command
  Hermes would ask approval for (the commands the yes card lists), always waits for your yes, whatever the setting.
  A dream-made reflex not used for a month rests on its own.

`practise`, the British spelling, is still read as `practice`.

**decision_model.** `auto` asks Jev when its key is set, else Hermes's model. `jev` asks Jev first, then Hermes's
model. `hermes` asks Hermes's model alone: no question goes to Jev. `llm` steps and judge calls always use Hermes's
model. Without a key everything works, slower.

**permissions.**

- `hermes`: when Hermes calls a saved habit with `habit_run`, or its skill loads, and the habit's current version
  became automatic by your yes in a chat, the plugin no longer refuses beforehand a command Hermes would ask about, or a
  deleting command written in the reflex. Hermes's own approval decides: it asks in a chat; when nobody can be asked
  (cron, `-q`) it applies the mode you set (`approvals.cron_mode`, `approvals.single_query_mode`,
  `approvals.unattended_mode`), which refuses by default and approves without a question if you set it to approve;
  its hard floor always holds. A refusal or a pending approval is a failed step: the reflex hands back.
  The yes card names such commands.
- Never, even under `hermes`: in a test run, in a draft, in practice, in a habit that is learning, in a one-off
  reflex the model wrote, in a habit made automatic by the rules or by `hermes habits yes`, in a message start, in a
  schedule start, or in `hermes habits run --live` and `hermes habits tick`. There the plugin refuses both beforehand.
- `strict`: the plugin refuses both beforehand everywhere.

**model_text.**

- `quoted`: text written by an `llm` step may fill a terminal command as one operand, quoted, and only in a command
  whose program takes that operand as data (`mv`, `cp`, `mkdir`, `touch`, `ln`, `open`, `echo`, `printf`, `say`, `cat`,
  `head`, `tail`, `wc`, `ls`, `find`, `grep`, `curl`, `wget`, `jq --arg`, `terminal-notifier`), after its options. Any
  other program (`sed`, `awk`, `sh`, `python`, `git`, a wrapper such as `env`) is refused with a message that names
  it. The whole rule: SAFETY.md, section 5.
- `never`: it never enters a command (refused when the reflex is checked, and again at run time).
- Under both, it is never the program, the program text of `sed`, `awk` or `jq`, or the code after `-c` or `-e`.
- Text read from outside (a `web_*`, `browser_*` or `mcp_*` tool, `x_search`, `session_search`, a screen, `curl`,
  `wget`, `ssh`, `gh` and other network programs) is held to those same positions
  under both, and never fills a deleting command. Under `never` it may still be one quoted operand.

A value the plugin does not know for `permissions`, `model_text` or `dream` is read on its strict side (`strict`,
`never`, `manual`), with one warning in the log. For the other settings it is read as the default.

**language.** The plugin's own lines to you: `/habits`, `hermes habits list`, the dream, the news. What the model
reads stays English.

**Jev key.** Optional. It sends the questions of `ask` steps to Jev, fast, instead of to Hermes's model. Nothing else
leaves the machine through this key.

### Old keys that are still read

Earlier builds had other keys. They stay readable and are not in the settings schema. `promote: auto` with `learning`
unset means `learning: auto`. `backend` (`auto`, `jev`, `llm`) with `decision_model` unset picks the decision model;
`llm` keeps Jev behind it. A number the user set by hand wins over the caution row: `min_confidence`,
`trigger_confidence`, `promote_runs`, `promote_agreement`, `retire_window`, `retire_agreement`, `spot_check`. `judge`
(default true) lets Hermes's model match steps written differently while a habit learns. `direct` (default true) is
the direct answer of a message start.

## The caution table

`caution` sets these numbers (`CAUTION` in `habits/ledger.py`). Normal is what Habits always did.

| Number | relaxed | normal | strict | What it does |
| --- | --- | --- | --- | --- |
| `ready_matches` | 2 | 3 | 5 | A dream draft is ready after this many matches |
| `rest_differs` | 3 | 2 | 1 | A dream draft rests after this many differs in a row |
| `promote_runs` | 8 | 12 | 20 | Message or schedule habit: compared runs before it is ready |
| `promote_agreement` | 0.85 | 0.90 | 0.95 | Message or schedule habit: share that must agree |
| `retire_window` | 15 | 20 | 30 | Automatic habit: runs looked at to decide a rest |
| `retire_agreement` | 0.70 | 0.75 | 0.85 | Automatic habit: rests below this agreement |
| `spot_check` | 0.05 | 0.10 | 0.20 | Automatic habit: share of runs checked beside Hermes |
| `min_confidence` | 0.75 | 0.80 | 0.85 | An `ask` answer below this is a doubt |
| `trigger_confidence` | 0.65 | 0.70 | 0.75 | A message picks a habit at this confidence |

These stay the same: 3 errors in a row rest an automatic habit; 2 spot checks in a row under the agreement rest it;
a message habit that handed back 3 times in a row goes back to learning; every run is checked until 3 agree.

## Commands

### In a chat: /habits

Each word has a French twin (`oui`, `non`, `essai`, `repos`, `faux`, `annule`, `reprends`, `rêve`).

| Command | What it does |
| --- | --- |
| `/habits` | Your habits by state (8 lines, then "+N more"), then up to 3 decisions, with buttons where the chat has them |
| `/habits <name>` | One habit: state, description, start, version, gains, last 5 runs |
| `/habits yes [name]` | Your yes: let a habit act on its own. With no name, the one decision waiting. `promote` still works |
| `/habits no [name]` | Answer a dream card no: the action is not proposed again for 30 days. On another habit it rests |
| `/habits try [name]` | Let a suggested draft practice, or bring a resting habit back to learning |
| `/habits rest [name]` | Put a habit to rest |
| `/habits wrong` | The last habit that answered here got it wrong: back to learning |
| `/habits undo` | Undo your last change of the last 24 hours, if the habit is still where it put it |
| `/habits pause`, `/habits resume` | No habit starts from a message or a schedule; then start again |
| `/habits view [name]` | The gallery, or one habit's diagram |
| `/habits dream` | Re-read Hermes's history now and show what comes back |
| `/habits help` | The commands |

A reflex that starts on a skill appears in `/habits` with its real state: learning, automatic or resting.

### In a terminal: hermes habits

Options come from `hermes habits <command> --help`.

| Command | What it does |
| --- | --- |
| `list` | The list `/habits` shows (also the default): every habit with its state, then every draft of the dream, one per line, with what it waits for (your try, a place, N of M matches, your yes, or it rests); not the runs |
| `show <name> [--json]` | The detail: state, file, steps in order, each text whole (for a draft: its whole command or call, its folder, the moments it was seen at, and what it still needs). `--json`: `habit`, `path`, `kind`, `origin`, `version`, `valid`, `issues`, `uses` (unknown name: exit 1) |
| `view [name]` | Write the gallery or one diagram page, print its path, open it on macOS |
| `yes <name>` | Your yes, as `/habits yes` (`promote` still works). Exit 1 when nothing moved |
| `rest <name>` | Put a habit to rest (`retire` still works); a watch also loses its cron job and file |
| `reset <name>` | Back to learning with fresh numbers (a habit called by name: back to called by name) |
| `undo [name]` | Undo your last change, the chat's or the terminal's. Exit 1 when nothing was undone |
| `pause`, `resume` | As in a chat |
| `tick <name>` | Run a watch once and print what its cron job reads |
| `check <file>` | Check a reflex file as `habit_run` and `habit_save` would, without running it |
| `run <target>` | Run a reflex here, without a model turn; a test unless `--live`; records nothing. For a draft of the dream: a local read runs, anything else (a web call included) is only planned, and each planned call says why ("planned, not run" when none was made) |
| `dream` | The dream now ([below](#the-dream)); it never says yes to anything. With `--days` or `--home` it is a look: nothing is saved |
| `cleanup` | Remove every cron job and every file of `$HERMES_HOME/scripts` whose name starts with `habits-` (including ones the plugin did not create): run it before removing the plugin |

`hermes habits run <name or file>` options:

| Option | Meaning |
| --- | --- |
| `--input NAME=VALUE` | A value for an input (JSON when it reads as JSON); repeat for each. `trigger=TEXT` sets `{trigger}` |
| `--live` | Do its writes for real (never under `~/Downloads`) |
| `--within DIR` | Start its commands in DIR; with `--live`, refuse any write outside DIR |
| `--message TEXT` | A message habit runs only if its start would take that message |
| `--memory FILE` | A JSON file that keeps what `check: changed` compares with, between runs |
| `--json` | One JSON object; exit 1 on a refusal or an error |

It prints the result, a summary line, the steps, and `replay: <path>`, a page of that run. A missing input is
refused ("missing inputs: who (--input name=value)"). With `--json`, the keys are `habit`, `version`, `kind`,
`mode` (`test` or `live`), `status`, `message`, `step`, `reason`, `failed`, `doubts`, `doubted`, `values`, `planned`,
`planned_count`, `tools`, `files`, `counts` (`steps`, `tools`, `questions`, `llm`), `backends`, `trigger`, `asks`,
`llm_ms`, `within`, `memory`, `model_calls`, `ms`, `plan_ms`, `plan_model_calls`. The value of `kind` is `one-off`
(called only), `request` (message) or `watch` (schedule).

The model may run `hermes habits list`, `show`, `view`, `check`, `tick`, `help`, and `run` as a test. Any other
`hermes habits` command is refused for the model: only you change a state.

To read the ledger behind these commands: `sqlite3 ~/.hermes/plugin-data/habits/habits.db "select habit, status from habits"`, opened plainly; `-readonly` and `?mode=ro` fail on it while no Hermes process has it open, and `?immutable=1` works ([SAFETY.md](SAFETY.md), section 9).

## The three states

A habit that starts on a message or a schedule is **learning**, **automatic** or **resting**.

```text
saved ──► learning ── enough agreeing runs, then your yes ──► automatic
             ▲  ▲                                                │  │
             │  └── a message habit handed back 3 times in a row ┘  │
             │      low agreement over the last runs, low spot      │
             │      checks, or 3 errors in a row                    ▼
             └──── a new version, /habits try, hermes habits reset ─ resting
```

- **Learning.** The reflex runs as a test beside Hermes: reads, asks, checks and `llm` run; writes are planned. A run
  counts when it was compared, planned at least one write, and no question went to Hermes's model as a fallback.
  Agreement = planned writes Hermes also made, divided by planned writes, summed over counted runs. Writes match
  when the same tool has equal arguments once normalized (spaces, quotes, `~`, trailing `/`, variables set on the
  line, a line split into simple commands, loops and globs unrolled). A message habit that only reads plans nothing:
  its answer is judged against Hermes's reply by one call to Hermes's model.
- **Ready.** At `promote_runs` counted runs with `promote_agreement`, the habit stays learning and is ready. It waits
  for your answer in `/habits`. Your yes is bound to the version the card showed: a habit changed meanwhile is refused.
  "Keep practicing" asks again after half as many runs. Under `learning: auto`, the thresholds alone decide.
- **Automatic.** A message habit answers before Hermes. It is checked beside Hermes: every run until 3 agree, then
  the `spot_check` share. A habit that only reads is checked only while a model reply was seen in the last 10 minutes.
- **Saved automatic.** A new message habit that only reads (a web GET counts) and sends no file starts automatic, with
  no yes and whatever `learning` says, when the decision model judges that your message asked to keep a habit. Only a
  first save does that. It does not get the permissions of a yes.
- **Wrong.** `/habits wrong` puts the last habit that answered on its own in this conversation back to learning. That
  run counts as a disagreement.
- **Resting.** By you, or by the rules: agreement under `retire_agreement` over the last `retire_window` counted runs,
  2 spot checks in a row under it, or 3 automatic runs in a row on the fallback. A rest by the rules is told once, in
  the next reply to you, with how to bring it back. A watch that rests keeps its cron job, silent.
- **A new version.** The version is a fingerprint of the reflex, the other files of `assets/`, the habits it `use`s and
  the description. Any change starts learning again with fresh numbers. Editing the body of `SKILL.md` keeps it.
- **Repair.** When a saved habit fails, the reply carries `repair`: Hermes proposes the fix to you and saves it with
  `habit_save` only once you agree.
- **A file the plugin did not save.** A reflex copied in by hand, synced or cloned never acts on its own before your
  yes (or, under `learning: auto`, the thresholds). It learns like any other. The model's prompt lists its name and state
  without its description, and called by name it runs as a test. One with no start of its own waits for your yes too:
  `/habits` and `hermes habits list` show it as "called by name · waits for your yes", the yes (in a chat or in a
  terminal) shows what it would run first and is to be said again, and after it Hermes may call it.
- **A reflex the dream made** has a start of "called". `/habits wrong` reaches it when Hermes called it by name in
  this conversation. Nothing runs it beside Hermes, so once it is not automatic it shows "on hold" and only your
  yes brings it back.
- **A skill-start reflex** shows its real state (learning, automatic, resting) in `/habits`, in
  `hermes habits list` and in the model's prompt. Until your yes, Hermes calling it by name runs it as a test.

Only you change a state, with the two exceptions above (a saved message habit that only reads, and `learning: auto`). The
guard reads the model's calls that visibly concern the plugin's world (the `hermes` program and `hermes habits`, the
plugin's data and code folders, a habit's files, Hermes's settings and history, `HABITS_OFF`). In a call that concerns
the plugin it refuses what it cannot read; any other call passes untouched, and `HABITS_OFF` turns every hook off except
the guard. What it cannot see (a target hidden from
reading, such as a path built at run time inside code or an encoded script; a row forged in the ledger file) is a known limit, named in
[SAFETY.md](SAFETY.md), section 6. The ledger trusts an automatic state only when its own history records the move.

## The dream

The dream is the part that learns. It re-reads the history Hermes already keeps, spots what comes back, and proposes
a small reflex. It calls no model, adds nothing to a turn, and writes nothing in Hermes's history.

```text
Hermes's history ──► spots what comes back ──► drafts a reflex ──► practices beside Hermes ──► your yes
 (read only)          3 turns, 2 days           (no inputs)         compares, keeps verdicts     saved,
                                                                                                 automatic
```

### What it reads

- Hermes's session database, `state.db` in the Hermes home, through Hermes's own `SessionDB`, read only. A home with no
  `state.db` reads as empty, not as an error.
- For each session: the tool calls Hermes made, and the status of each result (parsed in memory, never kept).
- A turn is the messages from one user message to the next. Its day is the local date of that message.
- Left out of the reading: sub-agent sessions; the copied part of a branched session; a session that continues another
  after Hermes compressed it (its first turn is not a "first gesture"). A call wrapped in `tool_call` is unwrapped.
- A call that was blocked, pending approval, denied, in error, `success: false`, or not runnable (exit -1, 126, 127)
  does not count. A non-zero exit with output (grep found nothing) is a normal gesture and counts.

### The window

The last 60 days. `--days N` reads 1 to 60. It reads at most 3,000 sessions and spends at most 20 seconds; past either
it says "Read the newest part only" (status `partial`). It counts from zero each time, so two dreams never count a turn
twice.

### What "comes back" means

An action comes back when it appears in at least 3 turns on at least 2 different days. Several calls of the same action
in one turn count once. The list is ranked by days, then turns, then last seen.

### The key of an action

Two calls are the same gesture when they have the same key.

- For a terminal call: the command with white space collapsed and these trimmers removed from its end, repeatedly:
  `| head` or `| tail` (with an optional count), `2>&1`, `2>/dev/null`, `| cat`, and `; echo $?`. A trimmer changes the
  output, not the gesture. The working folder is not part of the key.
- For `read_file`: the path without a trailing `/`.
- For any other tool: its name and its arguments as sorted JSON.
- A key over 2,000 characters is left out.

Only three kinds of call may become a draft: a terminal command, `read_file` and `search_files`, and a call of another
tool (an MCP tool, `browser_navigate`) whose arguments are all plain. Plain means a number, a boolean, nothing, or a
string of at most 200 characters without a space that is one word, identifier or option name, a path, or a URL with no
user, query or fragment, and that holds no piece of 8 or more mixed letters and digits; lists and objects of those
count too.

The reflex runs the most common raw form, trimmers included, in the folder shared by at least 80 % of its turns
(none otherwise).

### What it leaves out, and why

| Reason | What | Why |
| --- | --- | --- |
| own | `habit_run`, `habit_save` | the plugin's own tools |
| bookkeeping | `tool_search`, `tool_describe`, `todo`, `memory`, `clarify` | no gesture to repeat |
| moment | `skill_view` | used only to tell "after skill X" |
| code | `execute_code`, `delegate_task` | free code cannot be summed up in a step |
| free text | `send_message`, `write_file`, `patch`, `text_to_speech`, `image_generate`, `vision_analyze`, `skill_manage`, `cronjob_manage`, `session_search`, every `browser_*` tool but `browser_navigate`, and any other tool call with an argument that is not plain (above) | it carries what someone typed, or only moves a screen |
| noise | `sleep`, `echo`, `cd`, `true`, `false`, `:`, `pwd`, `clear` | a pause or a comment is no gesture |
| key | a command or argument that looks like a key, token or password, or a `$NAME` that holds one | it would be copied and shown |
| secrets | a private file, in any spelling: shell startup and history files, `.netrc`, `.npmrc`, `.pypirc`, `.pgpass`, `.git-credentials`, the folders `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`, `~/.docker`, `~/.config/gh`, `~/.config/gcloud`, Hermes's own secrets and config files; the commands `env` and `printenv`; a `user:password` pair | a secrets file is never proposed and never re-run |
| deletes | a deleting command: `rm` and its kin, a prune, `rsync --delete`, `git clean`, `git branch -D`, `git stash drop`, `git reset --hard`, `truncate`, `kubectl delete`, an uninstall or purge, a redirection that empties a file | the dream never drafts a delete |
| endless | a command that never ends: `tail -f`, `logs -f`, `journalctl -f`, `watch`, `top`, a dev server, `docker compose up` without `-d`, `ping` without `-c` (sent to the background it is fine) | a reflex must finish |
| settings | `hermes habits ...`, `hermes config ...` | the dream does not change settings |
| long | a key over 2,000 characters | too big to show in a card |
| failed | a call that failed (above) | a refused command is no habit |
| invalid | a reflex that would not pass the checks | it could not run |

`hermes habits dream` prints one line: "Left out on purpose: N turns". `--json` gives `left`, the number of distinct turns.
A secrets file is never proposed, and practice never runs one. An action whose last sighting is more than 30 days old
is no habit any more: it is not proposed and not counted as left out.

The `key` and `secrets` rows read words and shapes, on the careful side. Left out although harmless: a file that only
says token or password in its name and is read (`tokens.md`, `.env.example`), an option ending in `-file`, `-key` or
`-header` (`--output-file`), a quoted word of 8 or more mixed letters and digits (a version such as "v2.0.1-rc1").
Not seen: a one-word password in a plain argument (`{"pin": "hunter"}` under a name that does not say secret).

### The moments

For each action that comes back, the dream notes when it happens: the first gesture of a session, the call that closes
the turn's work, the call just after a given skill was loaded, a scheduled job. It shows a moment only when it holds
for at least half of the action's turns, two at most, the largest first; on a tie "after the skill" and "in a scheduled
job" come before the other two. "First gesture" and "closes the work" are counted only over turns that hold other
calls: in a turn of one call they are true of every gesture and say nothing. The line that explains the two is
printed only when one of them is shown. A moment is evidence for you; it starts nothing.

### What it proposes

Three things, never more than 3 at a time:

- **Create.** A draft: one fixed action, or a fixed sequence, with no inputs. It becomes a card when it is ready.
- **Change.** A saved dream-made reflex that is automatic, whose own form was not seen for 14 days, while a
  sibling form is spotted (same program and subcommand; for other tools, the same path or url). Your yes saves the
  reflex again with the sibling's action, as a new version, automatic at once by your yes.
- **Rest.** A saved dream-made reflex that was not used (in the history or in its own runs) for 30 days. Your yes rests
  it. Under `learning: auto` it rests on its own.

A draft that keeps differing rests on its own (below).

**Growing.** Two to four things that come together (each in at least 80 % of the other's turns, together in 3 turns
on 2 days) become one draft of several steps, in their usual order. A part can be a plain action, or a dream-made
reflex Hermes calls by name: it becomes a `use` step. Two such reflexes called together are enough. A grown draft
is never run beside Hermes: only its sequence is seen, and it always waits for your yes.

### The life of a draft

```text
spotted ──► practicing ──► ready ──► (your yes) ──► saved, automatic
   ▲          │    │
   │          │    └── differs in a row, or its action not seen for 30 days ──► resting
   │          └── an output looks like a key ──► gone
   └── resting, when its action comes back (3 turns on 2 days since it rested)
```

A draft has a name from its program and up to two of its words (`df`, `git-pull`, `notes-wake`), 40 characters
at most, cut between words and never inside one, with `-2` if taken. The value of an option is never a word of the
name (the folder of `git -C <folder>` is not). A name or a hand-back sentence can so be shorter than the command it
stands for; `hermes habits show <name>` prints the command whole.
It is not a habit yet, and it is not a skill folder: it lives in the plugin's ledger until your yes.

| | relaxed | normal | strict |
| --- | --- | --- | --- |
| ready after this many matches (same or shape) | 2 | 3 | 5 |
| resting after this many differs in a row | 3 | 2 | 1 |

- The dream drafts nothing for a command that a shipped example or one of your own reflexes already does on its own
  (a reflex whose only acting step is that command): the action's line names that reflex. A draft made before gives
  its place back.
- A learned reflex of one command is described by the command it replaces, under the rules of a name (no option
  value, a path by its last name), and by the moment it was seen at when that fits in 60 characters: "Runs `notes wake
  | tail -40` at session start".
- With `learning: practice` or `auto`, a new draft starts practicing at once, up to 3 at a time. With `suggest` (the
  default) it waits for your "try".
- A match is a `same` or a `shape` verdict (below). A draft that acts counts a match each time Hermes does the exact
  same action again.
- A draft that is ready stops practicing and waits for you.
- A draft that practices but whose action does not come back for 30 days rests and frees its place. A resting draft
  starts again, counts at zero, when its action comes back in 3 turns on 2 days after it rested.
- A **no** holds for 30 days for that action. After that it can be proposed again only if it came back in at least 3
  turns on 2 days after the no. A reflex you delete by hand counts as a no.

### What practice runs, and what it only counts

Practice happens when Hermes does an action a practicing draft is made of. It never adds to Hermes's turn: the work runs
in the background.

| The draft is... | Practice does |
| --- | --- |
| a local read: a terminal reader that stays on the machine, `read_file`, `search_files` | **Runs** the reflex as a test, 10 s at most, at most once per 10 minutes per draft, and compares its output with Hermes's |
| anything else: an act, a web call, a call to another machine (`curl`, `ssh`), an MCP tool, an unknown program | **Plans and counts.** Nothing runs. A match only means "Hermes did this exact action again" |
| the same action in another form, or in another folder than the draft's | Counts as `seen`; it does not move toward ready |
| a grown draft of several steps | Counts at the end of a turn when every step came back in order; nothing is run or compared |
| a call that failed, was blocked, was refused or waits for an approval | Not counted: Hermes did not do it |

The comparison is in memory. It looks at the first 20,000 characters, with colour codes removed:

- `same`: equal.
- `shape`: equal once clock times, dates, epoch times, long ids, and a count that sits among words (`3 files` and
  `5 files`) are masked. Two different numbers with nothing around them differ.
- `differs`: anything else. A failure of the reflex where Hermes succeeded is an `error`, which counts as a differ.

Only the verdict is kept, never an output. If an output looks like a key, the draft is dropped. An action counts once per
turn per session.

### The yes

When a draft is ready it becomes a card in `/habits`: what it runs, in how many turns on how many days, the moments,
and either "Beside Hermes: M of N the same" (a read) or "it came back M times since" (an act, or a grown draft, for
which nothing was compared). The card shows the exact command in full with the folder it runs in, each reflex a grown
draft calls, and every command Hermes's approval would ask about, those of the reflexes it calls included.

- Your yes saves it through the same path as `habit_save`, with the start "called" and the description "Runs ..." (60
  characters at most), then makes it automatic. Hermes calls it by name.
- The yes is bound to the exact form the card showed. If the draft changed since, it is refused.
- A typed `/habits yes <name>` shows the card first, unless the card of this very version went out in this
  conversation (this session, or the last 10 minutes) or the version had your yes before. Say yes again to give it.
  In a chat without buttons, `/habits` shows each waiting card as text, then the words to type.
- A yes typed on a draft that still practices decides nothing: it says where the draft stands and how many times
  more its action must come back.
- The yes never overwrites another habit or skill: if the draft's name is taken, the draft gets a new name, says so,
  and asks for your yes again under it.
- If skill writes need Hermes's approval first, the yes waits for it and ends the next time `/habits` is opened or at
  the next dream. After 24 hours without the files, the draft is ready again.
- The terminal cannot say this yes: `hermes habits yes` refuses a reflex Hermes made, because the model has a terminal
  too. Answer in a chat.

### Caps

- 3 decisions shown at a time, oldest first (cards for ready drafts and dream proposals together).
- 3 drafts practicing. 3 proposals open.
- One line of news a day, all kinds together. `/habits` always shows everything.
- One dream a day: a daily dream starts when the last dream that read something (`ok`, `empty` or `partial`) began
  more than 20 hours ago. After one that read nothing (`unread`, `failed`) it waits 1 hour.

### When it runs

- `dream: daily` (default): in a background thread after a reply went out, when the platform is not a cron job or the
  curator, a process checks at most every 10 minutes, and the 20-hour rule allows it. One process alone dreams (a file
  lock). A dream that ended `unread` or `failed` does not count for the 20 hours: the next one waits 1 hour.
- `dream: manual`: only `hermes habits dream` and `/habits dream`.
- Never when `learning` is `off`, when Habits is off (`HABITS_OFF`) or when it is paused.
- A dream that is killed costs nothing: every write is idempotent and the next one starts again.

### hermes habits dream

```text
hermes habits dream [--json] [--days N] [--home FOLDER]
```

It runs a dream now, whatever the daily rule says, and prints what it read, what comes back (top 5), what practices, and
the next step. It never says yes to anything.

- `--days N`: how many days of history to read, 1 to 60. Other values are refused (exit 2). It is a **look**: it
  prints what comes back in those days and saves nothing (no draft made or moved, no proposal, no row for the dream),
  and says so in its last line.
- `--home FOLDER`: read the history of another Hermes home (its `state.db`, read only). A look too: nothing is saved,
  in this home or in that one. A folder with no `state.db` says "no history store" and exits 0.
- `--json`: one JSON object.

Only the plain `hermes habits dream` (the last 60 days of this home) keeps what it finds as drafts.

It was run in a throwaway home on a synthetic history (`tests/e2e/make_history.py`: invented commands, 29 sessions; the
project's folder is written `/home/someone/proj` here):

```text
Read 61 turns in 27 sessions over 41 days (0.1 s).
5 actions come back:
- `git -C /home/someone/proj branch --show-current` · 8 turns on 8 days
- `df -h ~` · 5 turns on 5 days · the reflex disk-space already does it: no draft
- `notes wake …` · 5 turns on 5 days · in full: hermes habits show notes-wake
- `git pull` · 5 turns on 5 days · in a scheduled job (100%)
- `read_file {"path":"/home/someone/proj/TODO.md"}` · 4 turns on 4 days
6 drafts wait for you in all: `hermes habits list` names them, one per line.
A command shown with … acts: hermes habits show <name> prints its full text.
Left out on purpose: 23 turns (secrets, typed text, noise, failed or refused calls, deleting commands, commands that never end, invalid forms).
❓ Hermes ran `git -C /home/someone/proj branch --show-current (in /home/someone/proj)` in 8 turns on 8 days. Let a reflex practice it beside Hermes?
   In a chat with Hermes, type: `/habits try git-branch` or `/habits no git-branch`.
❓ Hermes ran `/home/someone/proj/bin/notes wake | tail -40 (in /home/someone/proj)` in 5 turns on 5 days. Let a reflex practice it beside Hermes?
   In a chat with Hermes, type: `/habits try notes-wake` or `/habits no notes-wake`.
❓ Hermes ran `git pull (in /home/someone/proj)` in 5 turns on 5 days · in a scheduled job (100%). Let a reflex practice it beside Hermes?
   In a chat with Hermes, type: `/habits try git-pull` or `/habits no git-pull`.
+3 more wait: read-todo, make-lint, git-status-git-log-uptime
Next: nothing runs until you say try. `hermes habits dream` re-reads now; `hermes habits list` shows what waits for your word.
```

That is the whole output of the final build on 11 November, with the default `learning: suggest`: the `df -h ~` line names the
shipped reflex that already does it, the command that acts is shown short with where to read it whole, every waiting
draft is named, and nothing practices until a try. The made-up turns hold one call each, so no "first gesture" or
"closes the work" is shown. With `--days 3` it read 23 turns in 10 sessions over 3 days, showed the one action that
comes back in them, and ended on "This was a look: nothing was saved." With nothing that comes back, it lists up to 3
gestures seen at least twice, with the turns and days each lacks.

The keys of `--json`:

| Key | Value |
| --- | --- |
| `status` | `ok`, `empty`, `partial`, `unread` (Hermes's history could not be read), `failed` (an error of the plugin itself), `off` or `busy` |
| `turns`, `sessions` | how many turns and sessions were read |
| `days` | the real span of days read, counted inclusively |
| `took_ms` | time spent |
| `spotted` | up to 5 of `{action, turns, days, moments}`, with `name` (its draft) when it has one; `moments` holds `first`, `last`, `scheduled` (shares from 0 to 1) and `skill` (`{name, share}`) when they apply |
| `look` | only in a look: `days` or `home`, which option made it one |
| `near` | when nothing is spotted: up to 3 of `{action, turns, days, missing: {turns, days}}` |
| `left` | turns left out on purpose |
| `practicing` | `{name, action, same, shape, needed}` per draft practicing |
| `proposals` | `{kind, name, action}` per open decision |
| `why` | a short reason when `status` is `unread`, `failed`, `partial` or `off` |

## A start on a loaded skill

`trigger: skill` starts a reflex when Hermes loads its own skill (the `skill_view` tool). Its reply goes into the skill's
result, in one more field, `habits`, so the model reads both in one place.

```yaml
spec: habits/0.1
trigger: skill
start: system
fallback: help
nodes:
  system: {do: terminal, read: true, args: {command: "uname -s"}, as: name, next: done}
  done:   {end: "Running on {name}."}
  help:   {wake: "Could not read the system name."}
```

`hermes habits check` passed it. `hermes habits run` printed "Running on Darwin." With the file saved as the habit
`system-name` in a skills folder of the throwaway home, `hermes habits yes system-name` said it answers on its own.
The list now shows its real state (changed after that run; tests cover it, the list was not typed again). Not tried:
`/habits yes` for it in a chat, and a real load of the skill inside a Hermes turn.

What happens when the skill loads:

| The habit is... | The reflex... |
| --- | --- |
| automatic | runs for real |
| learning | runs as a test, and only if every step reads |
| resting | does not run |

Its limits:

- The reflex takes no `inputs` and no `ask` or `llm` step: there is no decision model to ask inside a tool result.
- It must be the user's own habit, valid, in a skills folder. The shipped examples do not start this way.
- It runs once per session and skill. It has 10 seconds.
- It does not run for a side file of the skill, for a second view that Hermes answers with a stub, or for a failed view.
- It does not run in a cron job or in the curator's session, or in a session whose turn the plugin did not see.
- With `learning: off`, a learning reflex does not run.
- A skill start cannot start another skill start.
- Under `permissions: hermes`, an automatic reflex with your yes in a chat may run what Hermes's approval allows.

The reply the model reads is `The reflex <name> ran when this skill loaded (<state>): <done or handed back>. <message>`.
The state is "automatic", or "a test run, it is learning".

## Known limits and what was not tried

- The gain depends on the task's size. The message start adds one decision question per message, two when it picks.
- Without a Jev key, an `ask` step goes to Hermes's model: slower, and a learning habit with `ask` steps does not
  count toward its yes, since a fallback answer is not counted.
- A message habit that only reads is judged by Hermes's model: one more call per learning turn.
- Exact matching unrolls simple `for` loops, multi-file `mv`, globs and variables. `while` or `case` logic is left to
  the judge.
- The deleting check and the guard on a habit's files read words. A deletion inside another program, or a path built so
  that no text names it, is not seen.
- A watch with write steps can become ready on judged silent checks before its acting branch is judged.
- A start on a message or a schedule shows no event kind: `trigger: {event: ..}` is refused.
- The dream drafts one fixed action or a fixed sequence, with no inputs. Growing counts only reflexes the dream made
  as parts: a habit you or Hermes saved by hand is not grown into a larger one.
- Hermes 0.22.5 or later; Linux and macOS. Machines that share habits should run the same plugin version.
- Tried since on a real Hermes (see BENCHMARKS): on 10 and 11 November, real model turns, a message start, the start on a loaded skill (2 of 2), `/habits yes` in a chat and a dream on a real history; on 7 to 9 November, on builds before v0.1, `ask` and `llm` steps and a schedule start in a cron job on a Raspberry Pi (87 checks). Not tried on the build of 11 November: a schedule start, a real MCP server.
