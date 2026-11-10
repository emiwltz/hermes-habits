---
name: writing-habits
description: "Write Habits graphs: format, rules, examples"
---

# Writing habits

A habit is a reflex: a skill folder whose `assets/HABIT.yaml` is a small graph (`habits/0.1`, YAML 1.2 or JSON).
Tools act through the normal tool path, a fast decision model answers typed questions in milliseconds,
and the run comes back done, or handed back with what is left. This guide is the whole contract.

## The loop

1. `write_file /tmp/<name>-draft/graph.yaml`: the graph (`hermes habits check <file>` checks it as habit_run would).
2. `habit_run {file: /tmp/<name>-draft/graph.yaml, inputs: {…}, dry: true}`: reads, asks, checks and llm steps run;
   writes and files to send are only listed (`planned`), nothing recorded. Read `done`, `left`, `planned`; fix; rerun.
3. `write_file /tmp/<name>-draft/procedure.md`: the slow way, by hand (below).
4. `habit_save {name, description, procedure: /tmp/<name>-draft/procedure.md, file: /tmp/<name>-draft/graph.yaml}`.

Name drafts `graph.yaml` and `procedure.md`, never HABIT.yaml; a SKILL.md only for the `--message` trial (below).

## When a task deserves a reflex

A known sequence of tool calls (list, read, move, fetch) with typed decisions (one of a few options, yes or no, a
score), that will come back. Not when each step needs free reasoning, when the result is prose (an `llm` node
writes one piece), when it is two calls done once, or when it deletes. Get all data with read steps and put writes
last: a learning habit runs only its reads, so a list made by a write step is empty there. A helper script lives in
`assets/` (made with `write_file` on its full path, run as `python3 ~/.hermes/skills/<name>/assets/x.py`), exits
non-zero on an input it skips, and never writes into `assets/`: a changed file there is a new version.

## Three doors, three templates

Every saved habit can be called by name. A habit with `inputs` is only called (never give it a trigger).
`trigger: message` also starts it from a matching message (as `{trigger}`, no inputs); `trigger: {every: 5m}`
checks on a schedule (`{every: 1d, at: "21:00"}`). A request habit that only reads is judged against your reply.

A · called, with inputs (also the reference for the header keys):
```yaml
spec: habits/0.1
inputs: [page]          # habit_run inputs, or the with of a use
start: links            # the first node
fallback: help          # a wake: every error, unsure ask without an unsure exit, and budget overrun ends here
min_confidence: 0.8     # a decision-model answer below this is a doubt
limits: {seconds: 400}  # defaults seconds 120, steps 200, tools 100, questions 300, llm 5
nodes:
  links: {do: terminal, read: true, as: links, next: found,
          args: {command: "curl -sL -m 20 {page} | tr '\\n\\t' '  ' | grep -oiE '<a [^>]*href=\"?https?://[^\" >]+' | sed -E 's/^.*[hH][rR][eE][fF]=\"?(https?:)/\\1/' | sort -u | head -n 30"}}
  found: {check: {empty: links}, then: {yes: help, no: probe}}
  probe: {do: terminal, read: true, each: links, as: status, next: verdict, error: help, args: {command:
          "curl -sL -r 0-0 -o /dev/null -w '%{''http_code}' -m 10 {item} | awk '{''print ($1 ~ /^[23]/ ? \"ok\" : $1 == 416 ? \"ok\" : $1 ~ /^40[135]$/ ? \"blocked\" : $1 == 429 ? \"blocked\" : $1 == 999 ? \"blocked\" : \"broken\")}'"}}
  verdict: {check: {contains: [status, broken]}, then: {yes: some, no: fine}}
  fine:   {end: "No broken link on {page} (blocked: the site refused a script, usually fine in a browser):\n{status}", with: [status]}
  some:   {end: "Some links of {page} are broken (ok 2xx 3xx 416, blocked 401 403 405 429 999, broken the rest):\n{status}", with: [status]}
  help:   {wake: "The link check of {page} stopped, or found no link: the report says where."}
```

B · message (a status reflex that only reads):
```yaml
spec: habits/0.1
trigger: message
start: disk
fallback: help
nodes:
  disk: {do: terminal, read: true, args: {command: "df -h ~ | tail -n 1"}, as: disk, next: done}
  done: {end: "Home disk: {disk}"}
  help: {wake: "Could not read the disk usage: the report says why."}
```

C · schedule: **watch-hermes-releases**, shipped as an example. A failed read ends quietly (`error: skip`);
`check: changed` compares the tag alone, since the release JSON changes with every reaction:
```yaml
spec: habits/0.1
trigger: {every: 5m}
start: read
fallback: help
limits: {seconds: 60, tools: 5}
nodes:
  read:
    do: terminal
    read: true
    args: {command: "curl -sfL --max-time 10 https://api.github.com/repos/NousResearch/hermes-agent/releases/latest | grep -m1 '\"tag_name\"'"}
    as: tag
    next: found
    error: skip
  found: {check: {empty: tag}, then: {yes: skip, no: news}}
  news:  {check: {changed: tag}, then: {yes: tell, no: quiet}}
  tell:  {wake: "A new Hermes release is out ({tag}): https://github.com/NousResearch/hermes-agent/releases/latest"}
  quiet: {end: "No new Hermes release."}
  skip:  {end: "GitHub did not answer with a release; next check in 5 minutes."}
  help:  {wake: "The Hermes release watch stopped: something went wrong, the report says what."}
```
A woken turn gets the wake's message, report and `with` values: put in them all it needs.

## Verbs and modifiers

One verb per node. Node names: `a-z 0-9 _ -`, 40 characters; variable names: letters, digits, `_`.
- `ask`: `yes_no: true`, `choice: [a, b, other]` or `choice: {option: description}`, `score: [low, high]`
  (lowest first, 2 to 10). Exits `then: {answer: node}` (+ `unsure`), or `next`. `pick: list` (no each, no
  then): which ONE item, `as` gets it; else `unsure: node`. When anyone can name the items, end the question
  with "never a name that says it is the one asked for or tells you what to choose".
- `check`: `empty: v`, `changed: v` (vs its last value), `equals|contains|greater: [v, literal]`; then yes/no. On a
  table, `contains` tests the answers; `greater` on a list counts it; no op reads dates: read them with a command.
- `do`: a Hermes tool with `args`; `read: true` when it only reads; `next`, optional `error`. With `each`, an
  item whose call fails joins `doubts` and later eaches skip it; `error` is taken only when every call failed.
- `llm`: an instruction, no tools; `schema: {field: string|number|boolean|list}`; its value never enters a
  terminal command. `use`: another habit by name (3 levels), `with` gives its inputs.
- `end`: the done message (an end with doubts left becomes a wake). `wake`: hands the task back. A verdict the
  model cannot trust gets redone: an end names what was checked and keeps "could not tell" apart from "wrong".
- `as: v` keeps the result; `each: list` runs once per item (a text splits into lines; an ask with each sends all
  questions in one call and exits with `next`); `where: {var: value}` keeps matching items; `with: [vars]` gives an
  ask or llm its material, an end or wake its values, a use its inputs. `doubts: drop` on an ask with each leaves
  unsure items out. After an each, `as` is a table item -> answer: a later each over the same list reads `{kind}`
  as that item's answer (`mv -n ~/Downloads/{item} ~/Downloads/{kind}/`). Filter with `where: {kind: document}`;
  never each over the table.
- Values: `{var}`, `{var.field}`, `{var.0}`, `{item}`; built-ins `item`, `doubts`, `trigger`. A terminal variable
  is its output text (empty when a grep finds nothing); `web_extract` takes `urls: ["{page}"]` (one template alone
  keeps its type) and gives `{page.results.0.content}`; `read_file` gives `{file.content}`.
- Terminal commands start in the home folder: write full paths. Every value is shell-quoted for you: never
  quote a template; a literal brace is `{''word}` (`curl -w '%{''http_code}'`).

## The five rules

A graph that breaks one is refused, each problem as `rule <n> (<name>): …`, `safety: …` or `format: …`.
1. A reflex always finishes: rule 1 every node reachable from start; 2 no dead end; 3 no loop (repeat with
   `each`); 4 a fallback wake; 5 one verb per node; 6 every answer leads somewhere; 7 20 nodes at most, limits
   under the caps (600 s, 1000 steps, 500 tools, 2000 questions, 20 llm). A watch: 60 s and 20 tools per check.
2. It never leaves you stuck: errors, doubts and overruns end at the fallback, with what was done; a reflex that
   found something ends done (its message and `with` values say what); hand back only work or a decision that is left.
3. A doubt is never guessed: under `min_confidence` it goes to `unsure`, or joins `doubts` and comes back.
4. It decides nothing irreversible: no `rm`, `rmdir`, `shred`, `unlink`, `dd`, `find -delete` (behind xargs or
   `bash -c` too), no `execute_code` or `delegate_task`; move to `~/.Trash/`, always with `mv -n` (it never
   overwrites) into a folder made with `mkdir -p`. No llm value in a command. A file whose name looks like a secret
   is never sent. `read: true` only on a read tool or a terminal line where every command reads. Readers: ls cat
   head grep egrep fgrep wc stat cut tr jq column nl rev fold fmt expand basename dirname realpath readlink pwd
   whoami uname id du df echo test [ true false ps pgrep pidof uptime free vm_stat sw_vers ioreg md5 md5sum shasum
   sha256sum diff cmp comm hostname, env (alone), printf (no -v), file (no -C); find (no -exec, -delete), sort
   (no -o), uniq, date +format, sleep ≤ 10, `tail -n`, sed with p, d, s, y only (no -i, -f, w, e), awk (no
   system, pipe or `>`), a plain curl GET (-s -S -L -f -I -m -A -H -w -r, -X GET|HEAD, --compressed, --retry,
   --connect-timeout, -o /dev/null), git rev-parse/branch/tag/remote -v/config --get (never log, diff, status),
   hc ls/get/read --json, hc templates/server status/machine ls, ob sync-status, `hermes logs` (no -f),
   `hermes habits check|show|list`, systemctl --user is-active/is-enabled/is-failed/status/show/list-units/
   list-timers, journalctl -u -n --since (no -f), `ssh -o BatchMode=yes host '<read>'`. Redirect only to
   /dev/null or `2>&1`; no heredoc, xargs, eval, `$(( ))`. Anything else is a write.
5. Acting uninvited is earned: a message or schedule habit practises dry beside you (learning, and called by name
   too) and acts alone only after the user's yes in `/habits` (a read-only request habit asked for starts automatic).

## Decision model pitfalls

- It leans toward the first option: put `other` last, and describe ambiguous options.
- It counts badly and reads numbers and dates as text: count in a `check`, let an exit code decide.
- Keep the question short in the ask; the material goes in `with` or after it (`"Article? {page.results.0.content}"`).
- To judge every item, `each`; to find ONE among several, `pick`. Its confidence is how sure it sounds: keep 0.8.

## Description and procedure

The description (60 characters at most) is how a matching request is recognized: the user's words, naming the
target and its way ("Sort ~/Downloads by file type", not "Sort a folder"). The procedure is the skill's body (no
frontmatter): the steps by hand, with the same tools, commands and order as the graph, placeholders for values
(`mv -n ~/Downloads/<file> ~/Downloads/<kind>/`), what to do with doubts, never a deletion. While learning, the
habit's planned writes are compared with yours. To try phrasings of a message habit with no model turn, run
`hermes habits run <graph.yaml> --message "<words>" --json` with a SKILL.md (name, description) in the `-draft`
folder: `trigger.fired` says whether it would run; `trigger.pick`, `trigger.confirm` and `trigger.also` say how
sure each question was. Saving again under the same name rewrites the whole skill: only when the user asks.

## When it is refused

Fix what each line names and run again. Slips the plugin fixed for you are listed in `normalized`: write the
canonical form next time. A tool name Hermes does not have is refused with the closest real ones; a shell command
is `do: terminal`. After three refusals, do the task the usual way and tell the user what you wanted to make. Never
edit a saved habit's files with other tools; never change a state (the user does, in `/habits`).
