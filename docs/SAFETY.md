# Safety and disclosure

What the Habits plugin does on your machine, what leaves it, and what it refuses. Each line describes the code at this commit.

## 1. What it does on your machine

A habit is a reflex: a skill with a fast path. Its graph (`assets/HABIT.yaml`) runs Hermes tools, asks short typed questions to a decision model, and calls your Hermes model only where the graph says so. A habit starts through one of three doors: called (`habit_run` by name), message (a hook reads your message before the model answers), or schedule (a Hermes cron job). It is learning (it runs dry next to the model and is compared), automatic (it acts), or resting. Each run ends done, or handed back to the model with the reason. The plugin keeps a SQLite ledger and some HTML pages in its own data folder. It no longer has a learning loop of its own: a new habit comes only through `habit_save`.

## 2. What leaves the machine

The plugin opens one network connection of its own: a decision request. It sends no telemetry.

- **Where.** `POST https://api.typesafe.ai/v1/systemone` (TypeSafe Jev), only when `TYPESAFE_API_KEY` is set. The key is read with `agent.secret_scope.get_secret` and sent as `Authorization: Bearer`. It is never written anywhere.
- **What.** Each request holds `"model": "jev-latest"`, a `state` of 8,000 characters at most, and up to 50 questions, each with its text and its options (with their descriptions for a choice). A run with more questions sends more requests (2,000 questions per run at most). Nothing else: no session id, no file, no history.
- **What a state holds.** For a habit's question, the values the node names in `with`. Those can be tool output and file contents. The questions carry data too: an `each` question holds its whole item (a line of tool output, a file name), and a `pick` lists each item (200 characters at most) as an option. For the message door, `User message:` and your message, sent with the names and descriptions of your request habits that are learning or automatic, and up to 3 paths or URL hosts each habit acts on (never a URL's path or query).
- **Which messages.** The message door asks only while at least one request habit is learning or automatic, and never for a message over 2,000 characters, a `/command`, a subagent's or the curator's turn, a message that names a saved habit whose name holds `-` or `_`, or one that names `habit_`, or while `/habits pause` is on. A cron job's turn is read like a user message. It asks one question, then one or two more about the habit it picked.
- **One more.** When `habit_save` first saves a request habit whose steps all count as reads, it asks whether your last message asked for a habit (one yes/no, your message as the state).
- **Timeouts.** 5 s per call, one retry on 429 or 5xx, never past the time the run has left. A message-door hook stops within 20 s.
- **The fallback.** Without a key, or when Jev fails, the same questions go to your Hermes model through `ctx.llm` (15 s at most). `backend: llm` asks your Hermes model first. Your Hermes model also gets the `llm` nodes, and the judge calls that compare a learning run with the model's own turn (up to 6,000 characters per text) or check a watch (12,000 at most). Where those go is your Hermes provider's business, not the plugin's.

Any other network access is made by the Hermes tools a habit calls (`web_extract`, `curl` in the terminal, ...).

## 3. What it runs

- **Hermes tools, through Hermes.** Every step calls `ctx.dispatch_tool`. Before each call the plugin checks the tool against the tool list Hermes resolved last in this process. That is usually your session's list, but it can be another agent's. When there is no list (a cron tick, or a process where no agent has run yet), every registered tool is allowed. So a scheduled habit may call a tool you turned off for your chats: the tool-list check cannot see that list from a cron tick. It also asks the other plugins' `pre_tool_call` guards. A guard that blocks or asks approval refuses the step, and so does an error while asking them (the step goes back to the model). Only a Hermes that has no such API (an older version) lets the call go on unasked, with one line in the log.
- **The shell, through the terminal tool.** Commands start in the terminal's home folder (`workdir: ~`) unless a node sets its own. Each command gets the run's time left as its timeout. The plugin starts one process itself: `hermes habits view` runs macOS `open` on the page it wrote. Every other command goes through Hermes's terminal tool.
- **Deletion commands refused.** A graph is refused when a word of a terminal command names `rm`, `rmdir`, `shred`, `unlink` or `dd`, or holds both `find` and `-delete`. This sees `/bin/rm`, `xargs rm`, `bash -c "rm ..."`, quotes (`r''m`), braces (`{r,}m`) and globs (`/bin/r[m]`). The command is checked again once its values are filled in. Free-code tools (`execute_code`, `delegate_task`) and tool actions that remove data (`skill_manage` delete, `cronjob_manage` remove, `memory` remove) are refused too. The check reads words, not effects. It does not see a name built at run time (`R=rm; $R x`), a file emptied or overwritten (`truncate`, `: > file`, `write_file`), `rsync --delete`, `mv` to `/dev/null`, or a deletion inside another program. Hermes's own check catches some of these (`git clean -f`, `git reset --hard`), not all.
- **No waiting for an approval.** A command Hermes's own `detect_dangerous_command` flags fails as a step error, so the habit hands back and the model does the action itself. Where that function cannot be imported, a built-in pattern stands in (`rm`, `chmod`, `chown`, `kill`, `sudo`, `| sh`, ...). Hermes's own approval checks still apply to the model's calls.
- **No secret files.** No habit attaches or sends a file whose name looks like a secret (`.env`, `id_rsa`, `*.pem`, `*.key`, `auth.json`, a name with `password`, `token`, `secret`, `api key`, ..., behind a backup suffix or a link) from a write step or an end: that step fails, in a dry run too. A step that only reads is not checked. A read that pipes a file to a remote reader over ssh also counts as a read and runs, in a dry run too: this check looks at what a step writes or sends, not at what a read prints. `habit_run` refuses to read a graph file Hermes's tools may not read, or one inside Hermes's home outside its skills and scratch folders.
- **Outside text is data.** After a run read the web, a browser, an MCP server, a server through `curl`, or an agent's screen, what it hands to the model comes inside Hermes's `<untrusted_tool_result>` block. Such a run never answers you directly.
- **Background processes.** A write step of the terminal tool may pass Hermes's `background` (and the terminal's other arguments, such as `notify_on_complete`): that command then keeps running after the habit's run has ended, outside its budgets. A read step may not: a read takes only `command`, `workdir` and `timeout`.
- **Dry runs.** A learning habit runs its read steps, questions and `llm` nodes for real and only plans its writes. A `kanban_list` read can still move ready kanban tasks, as Hermes's tool does.

## 4. Acting uninvited

- **Learning first.** A habit saved by `habit_save` starts learning. It runs dry; the model does the task; the plugin compares the two. Called by name with `habit_run`, a habit with a message or schedule door that is learning or resting runs dry too and hands the whole task back: it acts only once automatic, whichever door it comes through.
- **Thresholds.** It is ready after 12 compared runs at 90 % agreement (`promote_runs`, `promote_agreement`). Runs answered by the Hermes-model fallback do not count.
- **Your yes.** With `promote: ask` (the default), a ready habit waits for `/habits yes` in a chat or `hermes habits promote` in your terminal. With `promote: auto`, the thresholds alone make it automatic.
- **Two exceptions.** A request habit whose steps all count as reads (including `curl` or `ssh` steps marked `read: true`) starts automatic, with spot checks, when the decision model judges at `min_confidence` that your last message asked for a habit. A one-off habit (no message or schedule door) runs live when called by name; it has no learning phase. So do the shipped examples, which have no learning phase either.
- **Drafts.** A draft graph given to `habit_run` (scenario or file) runs live at once, like a one-off. Its steps meet the same refusals (section 3) and other plugins' guards, but no learning phase and no yes.
- **Version = fingerprint.** The version is a fingerprint of the graph as parsed, the other files in `assets/`, the habits it uses and the skill's description. A change to any of those is a new version: it starts learning again with fresh scores, and a yes for an older version does not carry over. Editing the body of `SKILL.md`, or a comment in `HABIT.yaml`, keeps the version.
- **Files the plugin did not save.** A habit copied, synced or cloned into your skills runs dry when called and when its message comes, until it is automatic. Its description stays out of the prompt section until then, but its name, the end message of a dry run and Hermes's skill index can reach the model. Under `promote: auto` the thresholds can still make it automatic.
- **Going back.** An automatic habit rests after agreement falls under 75 % over the last 20 runs, 3 errors in a row, or 2 low spot checks in a row. Once you said yes to a habit, or it agreed 3 times, a spot check only asks whether its answer is wrong, not whether it says as much as the model; the first 3 checks of a habit that became automatic without your yes compare it with the model's answer. A request habit that hands back 3 times in a row goes back to learning. `/habits wrong`, `rest`, `undo` and `pause` are yours.

## 5. What the model cannot do

A `pre_tool_call` hook (the guard) blocks the model's own calls that would:

- **change a state**: `hermes habits promote|undo|resume|reset|retire`, `hermes config set|unset` of the plugin's settings, Hermes's plugin-command code or `/habits` with a state word passed to `hermes`;
- **approve itself**: only your chat or your terminal moves a state, and the ledger records who did;
- **edit a habit's files by hand**: `SKILL.md`, `assets/HABIT.yaml`, `assets/LEARNED.md` change through `habit_save` only, whether the write comes from `write_file`, `patch`, `skill_manage`, a terminal line or code; a habit is never deleted or merged through `skill_manage`;
- **write the ledger**: any write that names `habits.db`, or both `plugin-data` and `habits`.

The guard fails closed, while any habit exists, on a line that names a habit's file (`SKILL.md`, `HABIT.yaml`, `LEARNED.md`) but whose target it cannot place: a relative path, a variable, a glob, or code that writes by itself. A line that names no such file is not refused, so `cp x "$DEST"` passes, and so does a terminal `rm -r` or `mv` of a habit's whole folder. The honest limit: this is a text check, and a model with a shell cannot be fully contained. A path built so that no text names it is not seen. So the ledger trusts an automatic state only when its own history records the move, and anything else goes back to learning.

## 6. Scheduled habits

- **What saving a watch creates.** A script `$HERMES_HOME/scripts/habits-<name>.sh` that runs `hermes habits tick <name>`, and a Hermes cron job `habits-<name>` (through `cronjob_manage`, `deliver: origin`, `every <n>m|h|d` or `M H * * *`). Saving again updates the same job. This happens when `habit_save` writes the habit's files, before any yes of yours: the job exists from that moment. With skills write approval on, the first save only stages the files; the job comes with the `habit_save` after you approve them.
- **What a tick does.** A full `hermes` CLI process outside any conversation. It prints `{"wakeAgent": false}` unless the watch has news, and always exits 0: a failed tick or a removed plugin never wakes the model.
- **A learning watch wakes the model.** While it learns, a watch only reads and plans its writes; each time it would hand back or write, the cron job wakes the model with the report and the planned calls, and the model does the task itself, unattended. Each such wake is a paid model turn, from the first tick.
- **Approvals under cron.** Nobody is there to approve, and the plugin never waits. A command Hermes would flag fails, and the watch wakes the model with the reason. Budgets are shorter: 60 s and 20 tool calls per check.
- **List and remove.** `hermes cron list` shows the jobs. `hermes habits retire <name>` removes one watch's job and script. `hermes habits cleanup` removes every `habits-*` job and script: run it before you remove the plugin. Removing the plugin first leaves the jobs and scripts in place; their ticks stay silent, and `hermes cron list` still shows them. A watch retired by the thresholds keeps its job; its checks stay silent.

## 7. What is stored

Everything is unencrypted, under `$HERMES_HOME/plugin-data/habits/`. No key of the plugin's own is stored. Tool arguments and outputs are stored as they come and can hold secrets.

- `habits.db` (SQLite). `runs`: each run, its status and scores, and a trace of 64,000 characters of JSON at most. It holds each step's output cut to 400 characters, the final message (8,000 at most), the planned writes with their arguments, and the values the last node names, whole. `runs` also has `said`, the request the run answered (300 characters, cleared once it is 14 days old, at the next recorded run). `habits`, `history`, `news`: states, who changed them, news still to tell. `memory`: the last value of each `check: changed` node. `calls`: the model's tool calls in a learning or spot-checked turn, with full arguments, deleted at the comparison; older ones are cleared at the next comparison. `failures`: a watch's failed checks in a row.
- `pages/`: HTML diagram pages with the last run's trace (the same traces, up to 30 one-off pages). `locks/`: one lock file per habit. `paused` and `off`: switch files.
- Your habits themselves are skills (`SKILL.md`, `assets/HABIT.yaml`) in your skills folder.

To delete everything: `hermes habits cleanup`, remove the plugin, delete `$HERMES_HOME/plugin-data/habits/`, then delete the habit skills you no longer want.

## 8. Hooks, tools, commands, settings

| Kind | Name | What it is for |
| --- | --- | --- |
| Tool | `habit_run` | Run a habit by name, or a draft graph |
| Tool | `habit_save` | Check a graph and save it as a skill; schedule a watch |
| Hook | `pre_llm_call` | The message door; news lines |
| Hook | `pre_tool_call` | The guard (section 5) |
| Hook | `post_tool_call` | Notes the model's calls in a learning turn |
| Hook | `post_llm_call` | Keeps the model's reply for the comparison |
| Hook | `post_api_request` | Counts the turn's tokens and calls (no content) |
| Hook | `on_session_finalize` | Waits up to 30 s for a comparison before the process exits |
| Prompt section | `habits` | 2,700 characters at most: the reflexes and the relay rules for the model |
| Skill | `habits` | When to pass the baton to a reflex |
| Skill | `writing-habits` | How to write a graph |
| Command | `/habits` | `yes`, `try`, `rest`, `wrong`, `pause`, `resume`, `undo`, `view` (French words too) |
| CLI | `hermes habits` | `list`, `show`, `view`, `check`, `run`, `promote`, `pause`, `resume`, `undo`, `tick`, `retire`, `reset`, `cleanup` |
| Settings | `config_schema` | `promote`, `language`, `backend`, `min_confidence`, `trigger_confidence`, `promote_runs`, `promote_agreement`, `retire_window`, `retire_agreement`, `spot_check`, `judge`, `direct`, and the secret `typesafe_api_key` (`TYPESAFE_API_KEY`) |

`HABITS_OFF` (an environment variable, declared under `optional_env` in `plugin.yaml`), or an `off` file touched in the last 10 minutes, turns every hook off; the tools stay.

## 9. Internal Hermes imports

Every Hermes import is lazy, inside a function, and wrapped so that a missing one never breaks Hermes.

| Import | Why | Without it |
| --- | --- | --- |
| `plugins.plugin_storage.plugin_data_dir` | The plugin's data folder | None: required inside Hermes |
| `agent.secret_scope.get_secret` | Reads `TYPESAFE_API_KEY` | No key: the Hermes model decides |
| `hermes_cli.plugins.get_pre_tool_call_directive` | Asks other plugins' guards before a habit's call | Missing (an older Hermes): logged once, the call goes on. Failing when asked: the step is refused |
| `tools.approval_detection` / `tools.approval` `.detect_dangerous_command` | Hermes's own dangerous-command check | A built-in pattern |
| `tools.skills_tool` (`find_all_skills`, `skill_search_dirs`) | Finds habits among your skills | No habits found |
| `tools.registry` (`registry`, `discover_builtin_tools`) | Tool names and schemas; loads tools in a CLI tick | Names not checked |
| `tools.skill_usage.bump_use` (private) | Tells the curator a habit ran | The curator may archive an idle habit |
| `agent.file_safety.get_read_block_error` | Refuses a graph file Hermes would not read | The plugin's own path checks |
| `hermes_constants` | Hermes's home, for the same check | `~/.hermes` and `HERMES_HOME` |
| `gateway.platforms.base.BasePlatformAdapter.extract_media` | Reads `MEDIA:` tags as the gateway does | The plugin's own reading |
| `sys.modules["model_tools"]._last_resolved_tool_names` (read only) | The tool list Hermes resolved last | Every registered tool allowed |
| `tools.terminal_tool`, `tools.file_tools` | A CLI run loads only the tools it calls | All built-in tools load |
| `sys.modules["agent.turn_context"].PRE_LLM_BLOCK_EXIT_REASON` (read only) | Whether Hermes ends a turn on a hook's answer | No direct answer |

## 10. Known limits

- The deletion check reads the words of a command, not its effects (section 3). Hermes's approval checks are the next line, and they miss some of these.
- The guard is a text check. If the guard itself errors, the call goes through; the ledger's history check is what still holds.
- Under `promote: auto`, a habit nobody saved can become automatic on the thresholds alone.
- Traces can keep whole tool outputs (up to 64,000 characters): a habit that reads private files leaves them in `habits.db` and in its pages.
- The tool check uses the list Hermes resolved last in the process, which can be another agent's. With no list, as in a cron tick, a habit may call any registered tool.
- Windows has no `fcntl`: one run per habit across processes is not guaranteed there.
