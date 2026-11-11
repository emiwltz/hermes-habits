# Safety and disclosure

What the Habits plugin does on your machine, what leaves it, and what it refuses. Each line describes the code at this commit. Checked against Hermes v0.23.0. What was not tried is written "not tried".

## 1. What runs, and when

A reflex is the file `assets/reflex.yml` of a habit's skill: a few steps. It starts in four ways:

- **called**: Hermes calls `habit_run` by name.
- **message**: a hook reads your message before the model answers.
- **schedule**: a Hermes cron job runs `hermes habits tick <name>`.
- **skill**: when `skill_view` loads the habit's skill, a hook (`transform_tool_result`) runs the reflex and adds its reply to the skill text, in a field `habits`. Only for a reflex with no inputs and no `ask` or `llm` step, never in a cron or curator session, 10 s at most. Automatic: it acts. Learning: it runs as a test, only if every step reads. Resting: nothing. Tried on a real Hermes: 2 of 2 (11 November, see BENCHMARKS).

A habit is learning (it runs as a test beside Hermes), automatic (it acts) or resting. What a fresh install adds to every session: a section of at most 2,400 characters (about 1,750 on a fresh install) in the system prompt, which lists your reflexes and the four shipped examples that only read and start on no schedule, and gives the call in two shapes: `habit_run {…}`, and `tool_call {calls: [{name: "habit_run", arguments: {…}}]}` for when Hermes keeps plugin tools behind its tool bridge (its default, `tools.tool_search.enabled: auto`; `off` lists them directly). Hermes may call a shipped example by name without a yes. The shipped `hermes-health` reflex sends one HEAD request to github.com and reads the gateway file and disk of `~/.hermes`, whatever `HERMES_HOME` says. Every run ends done, or handed back with what is left. The dream (section 7) learns from Hermes's history and makes reflexes of its own; you decide each one.

## 2. What a test run and practice never do

A test run (`dry: true`, or `hermes habits run` without `--live`) runs reads, questions and `llm` steps for real. Practice follows the same rule. A write, a message, a file to send and a deleting command are only listed. Nothing is recorded or scored. Hermes's permissions (section 4) never apply to them. A kanban read can still move ready tasks, as Hermes's tool does. A test run of a draft of the dream (`hermes habits run <draft>`) is stricter: a web call is only planned too, and each planned call says why it was not made.

## 3. Your yes

`/habits yes <name>` in a chat is recorded in the ledger as made by `user`. Typed in a terminal (`hermes habits yes`), it is recorded as `cli`: it lets a habit you wrote act, but it does not widen permissions (section 4), and it does not work on a habit Hermes or the dream made.

- It covers one version: the fingerprint of the reflex, its side files in `assets/`, the habits it uses and the skill's description. A change is a new version and starts learning again.
- A dream card shows the command in full with the folder it runs in, and every command Hermes would flag or that deletes, those of the reflexes it calls included. A yes is refused when the command is no longer the one shown.
- A typed `/habits yes <name>` on a reflex Hermes or the dream made shows that card first, unless this version was shown or had your yes before. Say yes again to give it. A message or schedule habit that is ready follows the same rule: the news line that says it is ready is not its card, so a yes typed right after it answers with the card (the steps, the commands that are sensitive or delete) and the yes is to be said again; a button, or the terminal's `hermes habits promote`, names the version and needs no second yes. The same holds for a card of the dream (a draft that is ready, a change): a typed yes counts only when the card's text went out in this conversation, that is in this session or in the last 10 minutes (a one-shot chat opens a session for each message); otherwise the answer is the card, and the yes is to be said again. In a chat without buttons, `/habits` shows each waiting card as text (what the reflex would run, its folder, the commands that are sensitive or delete, what was seen) before the words to type. What went out is kept in the plugin's ledger.
- A yes never overwrites another habit or skill: a draft whose name is taken gets a new name, says so, and asks again.
- **When a habit may act without a typed yes.** Two exceptions, and no other. (1) A message habit that only reads (a web GET counts as a read), saved by Hermes when the decision model judges that your message asked in so many words to keep the task as a habit, starts automatic at once, with no card and no `/habits yes`. This does not depend on `learning`. (2) Under `learning: auto` the thresholds alone make a habit automatic, recorded as made by `rules`: a message or schedule habit, write steps included, and every ready dream draft (one step or several, reading or acting; saved the same way, with a line of news). The exception: a dream draft that holds a deleting command or a command Hermes would ask approval for always waits for your yes, whatever `learning` says. Neither exception gets the permissions of section 4.
- Apart from these, only you give the yes. The guard refuses the model's attempts to give it that it can read (section 6).

## 4. The `permissions` setting

`hermes` (default). A habit with your yes has, inside a Hermes turn, the rights Hermes's model has in that turn. The plugin stops refusing beforehand a command Hermes would flag and a deleting command written in the reflex. The command goes to Hermes's terminal tool, and Hermes's rules decide:

- **Hermes asks** (CLI, gateway) about what its detector flags. I ran it on `rm -r`, `chmod 777`, a download piped into a shell, `git reset --hard`, `git push --force` and `find . -delete`: all flagged.
- **When nobody is there** (cron, `-q`, other unattended modes), Hermes applies the mode you set for that context: `approvals.cron_mode`, `approvals.single_query_mode` or `approvals.unattended_mode` in Hermes's config. Refusing is Hermes's default for these runs, not a rule of the plugin: with a mode set to `approve`, the command runs with no question. A refusal, or an approval left pending, is a failed step: the reflex hands back.
- **Its hard floor always holds**: recursive delete of the root, of a system folder or of your home folder, formatting a disk, `dd` onto a disk, a fork bomb, a shutdown or reboot.
- **It does not ask about** a plain removal of one file (`rm notes.txt` is not flagged), `mv`, `truncate` or `kill 123`. A reflex with your yes can delete a file without a question. If you turned approvals off in Hermes (`/yolo` or mode off), nothing is asked (from Hermes's source; not tried).
- If the plugin cannot see that Hermes has a way to ask or to refuse (its private API is unreadable, or no chat or unattended rule is set), it refuses beforehand, as `strict` does.
- A value an `llm` step wrote, or one read from outside (a `web_*`, `browser_*` or `mcp_*` tool, `curl`, `wget`), never fills a deleting command.

`strict`: the plugin refuses every flagged and every deleting command beforehand, and a reflex that holds one is refused at `hermes habits check` and `habit_save`.

It does not apply to: a test run, practice, a draft, a learning habit, a one-off reflex the model writes, a habit made automatic by the rules, and any run outside a turn (`hermes habits run --live`, `tick`, a schedule). A one-off reflex the model writes acts for real unless `dry: true`. It never deletes and never runs a command Hermes flags. A message start is not tried inside a turn, so it stays strict. A value the plugin does not know for `permissions`, `model_text` or `dream` is read on its strict side (`strict`, `never`, `manual`), with one warning in the log.

## 5. The `model_text` setting

With `model_text: quoted` (default), text a model wrote, and text read from outside (a web, browser or MCP tool, `x_search`, `session_search`, a screen, `curl`, `ssh`, `gh` and other network programs), may fill a template of a terminal command only in a command whose program treats that argument as data, and only in an operand after the program's options. Those programs are: `mv`, `cp`, `mkdir`, `touch`, `ln`, `open`, `echo`, `printf` (fixed format), `say`, `cat`, `head`, `tail`, `wc`, `ls`, `find` (starting paths), `grep`, `curl` and `wget` (the URL), `jq` (the value of `--arg NAME VALUE`, with a fixed program) and `terminal-notifier` (`-message`, `-title`). Anything else (`sed`, `awk`, `sh`, `python`, `osascript`, `git`, `docker`, a wrapper such as `env` or `timeout`, a substitution or subshell, a redirect target, a pipe into anything but a plain filter) is refused when the reflex is checked, with a message that names the program. The same test runs on the filled command at run time, whatever the switches say, for a value from outside. Deleting commands and commands Hermes flags never take such a value. Hermes tools that are not the terminal take values as data already. A file written by one step and run by another is not followed; `open` hands the thing to its default app; `curl` and `wget` write wherever the reflex's own `-o` or `-O` says. `model_text: never` keeps a model's text out of every command. Text read from a local file is not marked: a deleting command your yes covers may take one.

## 6. The guard

A `pre_tool_call` hook reads the model's own calls. It is a text check, not a wall; what it cannot see is listed under "Known limits" below.

- **What it reads.** Only the calls that visibly concern the plugin's world. That world is: the `hermes` program and any name that starts with `habit` (`hermes habits`, `/habits`, `habit_save`, `HABITS_OFF`); the plugin's data folder (`$HERMES_HOME/plugin-data/habits`: the ledger `habits.db`, the `paused` and `off` files); the plugin's code folder (`$HERMES_HOME/plugins/habits`, and the folder that code really runs from); a habit's files (`SKILL.md`, `assets/reflex.yml`, `assets/LEARNED.md`, and any path inside a habit's folder); Hermes's settings file (`config.yaml`), its history (`state.db`) and its scripts folder. A call concerns that world when its text names it (as written, with its quotes undone, or spelled backwards), when one of its paths lands in it once links are resolved, when it runs a script or applies a diff whose text names it, or when it is a `find` that deletes or runs a program. Every `skill_manage` call, and every cron job created or changed, is read as well.
- **It fails closed there.** In a call that concerns the plugin's world, the guard refuses what it cannot read rather than guess. Apart from that, if the guard's own code breaks on `terminal`, `write_file`, `patch`, `execute_code` or `skill_manage`, that call is refused whatever it concerns.
- **Everything else passes untouched.** A call that shows nothing of that world is not read at all: no shell reading, no listing of your skills. The suite sends 252 ordinary developer calls (git, npm, docker, pytest, a project's own `habits.db` or `config.yaml`, a README that mentions the ledger) through the registered hook, and none is refused (`tests/test_guard_ordinary.py`).
- **It stays on when Habits is switched off.** `HABITS_OFF` and the `off` file turn every other hook off (section 9), never the guard. A call that sets `HABITS_OFF`, for itself or for a `hermes` it starts, is refused.
- **It protects the plugin's own code, and lets the model read it.** `$HERMES_HOME/plugins/habits` is protected like the data folder, but only against what changes it: a write, a patch, a move, a delete, a redirection, `sed -i`, a git command that changes the repository (`checkout`, `commit`, `add`, `reset`, `stash`, `merge`…), or a program other than a test run is refused. Reading passes: `ls`, `cat`, `grep`, `cd <it> && git status`, `git log`, `git diff`, `git show`, and the test suite (`pytest`, `python -m pytest`, `python -m unittest`, with options that only read; `--basetemp`, `--junitxml` and `-p <plugin>` are refused). **If you installed the plugin as a link to a working copy, that copy is the plugin's code**: the model can read it and run its tests through the link or by its real path, and cannot edit it. A `cd` into the plugin's code folder passes only with a command on the same line; the guard then remembers that the session's shell stands there, and refuses a later line that writes or runs something until it starts with a `cd` to a full path elsewhere. The data folder is read the same way (`ls`, `cat`, `sqlite3 -readonly`, a select); `cd` into it is refused, and a `sqlite3` that runs a shell, writes a file or attaches one is no read.

It blocks the calls that would:

- **give itself the yes**, or change a state: any `hermes habits` command but `list`, `show`, `view`, `check`, `tick`, `help` and a test run; `/habits yes`, `no`, `try`, `rest`, `wrong`, `dream` passed to `hermes`; a write to the ledger (`habits.db`) or to the plugin's folders.
- **write Hermes's history**: `state.db` and its side files may be read, but a write through the terminal, `write_file` or `execute_code` is refused: the plain ways to plant gestures for the dream to find. A `find` over the Hermes home that deletes, or runs a program that writes, and whose tests can match `state.db` is refused the same way.
- **change a setting**: `hermes config set|unset` of the plugin's settings, `hermes config edit`, and any write to Hermes's `config.yaml` (the Habits settings live in that file; `hermes config set` of another setting passes).
- **change the plugin's code**, the guard included.
- **edit a habit's files** (`SKILL.md`, `assets/reflex.yml`): they change through `habit_save` only; a habit is never deleted or merged through `skill_manage`.
- **run the dream**: `hermes habits dream` is blocked. The model may tell you to open `/habits`.
- **start a `hermes` turn whose message it cannot read**, or one that carries `/habits` or `HABITS_OFF`; or take the plugin off (`hermes plugins disable|uninstall|update|install` naming Habits, and `hermes plugins update` of every plugin).

**Reading is never an edit.** A call that only looks at a habit's files, the plugin's folders or Hermes's own files passes: `cat`, `ls`, `find` (also with an `-exec` that runs a reader), `grep`, `stat`, `git status|log|diff|show`, `python3 -c` or `execute_code` whose code only reads (`open()` to read, `json.load`), inside an `if` or a loop too. A variable the line itself sets, once, to a plain path, in a command of its own and before using it (`S=<folder>; find $S -type f; cat $S/SKILL.md`), is read as that path, so such a look passes and a write through it is refused under the name of the habit it really touches. A variable the line does not surely set stays unknown: a write through it is refused, and the refusal says it cannot tell where the line writes rather than naming a habit. A look around the Hermes home passes the same way: a `printf` with a written format, a `read -r` into the line's own variable, `xargs` of a plain reader, a `sqlite3` select given on its command line (opened plainly, `-readonly` or with `immutable=1`), and a count in `$(( ))` over names the line surely set to numbers. A `sqlite3` fed its commands from a pipe or a file, or one that calls `writefile(` or `edit(` or a dot-command that runs a shell, is no read. Two limits: a line that looks at a habit's file and also runs something that is not a read (`cat <habit>/SKILL.md; make test`) is still refused as an edit; run the two apart. And a read line the guard cannot parse (a count over a command's output, `${x:-y}`) that also holds a shell's name, as `du -sh` does, is refused.

How it reads a call that concerns the plugin's world (any other call is not read): a terminal line is cut the way the shell cuts it, and refused when a command is named by a variable or a substitution, when a shell or an interpreter reads its program from a pipe, `/dev/stdin` or a here-string, or when a path it writes may be, as a glob, a variable or a link, the plugin's folders, the settings or a habit's file. A line that would leave the shell standing in the plugin's data or code folder is refused. Code is folded the way Python folds it and read like a line. A file the model writes (`write_file`, `patch`) is judged by where it goes; its text is read only when it is a script that acts (a shell or Python suffix, a `#!` first line, a shell start file or an env file), and a patch is judged on the file it leaves. A script is read again when a shell, Python or `source` runs it, and a diff when `patch` or `git apply` reads it: each is refused when its text names the ledger, the plugin's data folder or the settings file. **Length**, again only in a call that concerns the plugin's world: a terminal line over 200,000 characters is refused, unless the excess is a heredoc that only feeds `cat` or `tee` and names none of the plugin's files or settings; code over 400,000 characters is refused.

Known limits, each one a text check that cannot be closed by text:

- **A target hidden from reading.** A path built at run time inside code, a script that finds the ledger by walking folders or glues its name from pieces, an encoded script: the guard sees no text to refuse. The same holds for a script that changes between the check and the run, and for a script handed to `delegate_task` or to a scheduled job's prompt.
- Text decoded at run time and piped into a shell (`base64 -d | sh`) passes: the line names nothing of the plugin.
- A program named by a variable, or glued from variables, passes when no piece of the line names `hermes` or `habits`.
- Code that builds a name at run time (character codes, base64, a module or a command held in a variable) passes when its text names nothing of the plugin.
- A program piped into a shell or an interpreter, in a line that names nothing of the plugin, is not read.
- A long call that names nothing of the plugin is not read, whatever its length.
- A `Makefile`, a `package.json`, a `.sql` or `.js` file is not read when it is written, even when it names the ledger; a Python file that only defines functions is read for the programs it runs, not for what its functions write.
- **A row forged in the ledger file.** The ledger trusts an automatic state when its own history records a move by you. It cannot tell a row that code of the first kind wrote there from yours: the file is not signed. Closing this needs a signed record or a file the model's shell cannot write; it is not built.
- A hard link made before the guard watched, a symlink made by a process it does not see, a path put together across two calls, a shell alias, a relative path after an earlier `cd`: not followed (except a shell left in the plugin's code folder, which is).
- A program in perl, ruby or node that builds a command at run time is refused only when its text names hermes or habits plainly.
- A `hermes` turn started by another program (a gateway's web API, a message to a channel that runs `/habits` as you) is not seen.
- `send_message` of `/habits yes x` is not refused; whether a channel runs it as you is not verified.

## 7. The dream

- **Reads**: Hermes's history on this machine (`state.db`, through `hermes_state.SessionDB` read only), the last 60 days, 3,000 sessions and 20 s at most. Per turn: the tool calls, and whether each failed (the keys `status`, `error`, `success`, `exit_code` of its result, read in memory). Never an output. It calls no model. `hermes habits dream --home <folder>` reads another Hermes's history, read only. With `--home` or `--days` the dream is a look: it prints what it finds and saves nothing, in either home.
- **Keeps** (in `habits.db`): counts (turns, days), the command or arguments of a draft, when it was first and last seen, the moments it comes at, verdicts of practice (same, shape, differs, error, seen), and one row per dream. No output.
- **Spots** an action seen in 3 turns on 2 days. At most 3 decisions show at a time and one line of news a day.
- **Proposes only**: a terminal command, a file read (`read_file`, `search_files`), and a call of another tool whose arguments are all plain (a number, one word, a path, a URL with no user, query or fragment). A call that carries text someone typed never becomes a draft: `send_message`, `write_file`, `patch`, a browser tool other than `browser_navigate`, speech, image and vision tools, `skill_manage`, `cronjob_manage`, `session_search`, and any other call with an argument that is not plain.
- **Never proposes**: a failed or refused call, a deleting command (`rm`, a prune, `rsync --delete`, `git clean`, `git reset --hard`, a redirection that empties a file, and their kin), a command that never ends (`tail -f`, `watch`, a dev server, `docker compose up` without `-d`), an action last seen more than 30 days ago, `execute_code`, `delegate_task`, `hermes habits` or `hermes config`, a command over 2,000 characters, a private file (shell rc and history files, `.netrc`, `~/.ssh` and its kin, Hermes's own secrets files, a file whose name says credentials or password when it is read, key files such as `.pem` or `.kdbx`), an environment dump, a name or a text shaped like a key or a password (a quoted word of 8 or more mixed letters and digits, an option ending in `-key`, `-token`, `-password`, `-file`, `-header`, a `Cookie` or `Authorization` header). This is a word-and-shape check, not a proof: a secret spelled in a way it does not know can pass, such as a one-word password in a plain argument. It also leaves out harmless look-alikes (a file only named `tokens.md`, `--output-file`, a version such as "v2.0.1-rc1" in quotes). In its report, an action that acts is shown without its arguments unless they are plain.
- **Practice** (default `learning: suggest`: nothing runs and nothing practices until you say try, or set `practice` or `auto`). With practice, the plugin re-runs by itself, in the background, the local read commands Hermes just ran (a local command that reads, `read_file`, `search_files`), in the same folder Hermes ran them in, at most once per draft per 10 minutes, 3 drafts at a time, 10 s at most each. A command that depends on its folder, with no known folder, is not run. A recursive command (`find`, `du`, `grep -r`, `ls -R`) is never run from the home folder or the root. A call that only adds a timeout counts as the same action; the command and the folder are what is compared. The dream drafts nothing for a command that a shipped example or one of your reflexes already does on its own. A draft can be looked at and test-run from a terminal (`hermes habits show <draft>`, `hermes habits run <draft>`). The output is compared in memory, and only the verdict is kept. An output shaped like a key sends the draft away. Anything else (an act, the web, an MCP tool) is only planned and counted as "came back".
- **The yes** saves the reflex through the same path as `habit_save` and never overwrites a habit that exists. A no holds 30 days.
- **When it runs**: after a reply, in a background thread, when the last dream ended more than 20 h ago, never in a cron or curator session, never with `learning: off`. `dream: manual`: only on `hermes habits dream` or `/habits dream`.

## 8. What leaves the machine

No telemetry. The plugin's own outgoing traffic is decision questions, and model calls on your own provider.

- **Where the questions go**: `POST https://api.typesafe.ai/v1/systemone` (TypeSafe Jev), only when `TYPESAFE_API_KEY` is set. The key is read with `agent.secret_scope.get_secret`, sent as `Authorization: Bearer`, never stored. A redirect is refused. Without a key, or with `decision_model: hermes`, the questions go to your Hermes model through `ctx.llm`; where they go then is your provider's business.
- **Path 1, the message start.** As soon as you have one message habit (learning or automatic), every message of 2,000 characters or less that is not a slash command is sent there, to decide whether it starts a habit. It goes with the names and descriptions of your message habits (the plugin does not cut a description; Hermes caps one it writes at 1,024 characters) and up to 3 paths or URL hosts each. Without a Jev key that is one extra call to your Hermes model before every reply, inside a hook that may wait up to 20 s.
- **Path 2, saving a habit.** When Hermes saves a habit, your last message (8,000 characters at most) is sent once to ask whether you asked for a habit.
- **Path 3, the steps.** A request holds `"model"` (`jev-latest`), a `state` of 8,000 characters at most, and up to 50 questions of 8,000 characters at most, with their options. These caps are per request, not per run: a run may ask 300 questions by default (2,000 at most), and each request repeats the state. The state holds the values a step names in `with`, which can be tool output or file contents.
- **Calls on your own provider, whatever `decision_model` says.** One check after each turn a message habit learns on (your request, the habit's message and Hermes's reply are sent); a check on about 1 in 10 quiet runs of a schedule habit (the run report, 12,000 characters at most); and each `llm` step of a reflex, with the values the step names.
- **Not sent**: the history the dream reads, drafts, ledger, session id, files.
- **Limits**: 5 s per call, one retry on 429 or 5xx.

Any other network access is made by the Hermes tools a habit calls (`web_extract`, `curl`), and by the shipped `hermes-health` (section 1).

## 9. Files written

Under `$HERMES_HOME/plugin-data/habits/`, unencrypted: `habits.db` (runs with a trace of 64,000 characters at most, each step's output cut to 400; habits, history, news, the dream's drafts, proposals, practice and dreams), `pages/` (HTML pages of runs), `locks/`, and the switch files `paused` and `off`. Tool arguments and outputs of runs are stored as they come and can hold secrets. A run that a message started also keeps that message (cut to 300 characters, cleared after 14 days), and while a message habit learns, the arguments of Hermes's own tool calls of that turn are held until the turn is compared. The ledger file is created readable by you only (`0600`, like Hermes's `state.db`); its folder, the pages and the lock files take your account's default permissions (the pages came out readable by others in a throwaway home). Do not copy or open up the folder.

To read the ledger yourself, open it plainly: `sqlite3 ~/.hermes/plugin-data/habits/habits.db "select habit, status from habits"` (a `select` changes nothing; another Hermes home has it under its own `plugin-data/habits/`). It is kept in SQLite's WAL mode, so `sqlite3 -readonly` and `?mode=ro` answer "unable to open database file" whenever no Hermes process has it open; `sqlite3 "file:<its full path>?immutable=1"` opens it read-only then, and can miss the latest writes while Hermes runs.

Elsewhere: a habit is a skill folder in your skills folder, written by `habit_save` or by your yes on a card. Saving a schedule habit writes `$HERMES_HOME/scripts/habits-<name>.sh` and a cron job `habits-<name>`, at once, before any yes. While it learns, each check that would hand back wakes the model, a paid turn. `HABITS_OFF` turns every hook off except the guard.

## 10. Private imports of Hermes

All are lazy and wrapped. `hermes plugins validate` lists seven private ones:

| Import | Without it |
| --- | --- |
| `tools.approval._presence`, `_unattended_contexts` | the plugin refuses flagged and deleting commands, as `strict` |
| `hermes_cli.plugins.get_pre_tool_call_directive` | other plugins' guards are not asked (logged once); if it fails when asked, the step is refused |
| `tools.approval_detection.detect_dangerous_command`, `tools.approval.detect_dangerous_command` | a built-in pattern (`rm`, `chmod`, `kill`, `sudo`, a pipe into a shell) |
| `tools.skill_usage.bump_use` | the curator may archive an idle habit |
| `agent.file_safety.get_read_block_error` | the plugin's own path checks |

Other Hermes modules the plugin imports lazily, not flagged by the validator:

| Module | Without it |
| --- | --- |
| `hermes_state.SessionDB`, `hermes_constants.get_hermes_home` | the dream says it could not read the history |
| `tools.skills_tool.find_all_skills`, `skill_search_dirs` | the list of habits is empty, so the guard knows no habit folder (it still guards the ledger, the plugin's folders and the settings); not tried further |
| `tools.registry` | no list of known tools and no tool schemas, so a reflex's steps are checked less closely |
| `plugins.plugin_storage.plugin_data_dir` | the plugin cannot find its data folder (not tried further) |
| `agent.secret_scope.get_secret` | no key, so no call to TypeSafe |
| `gateway.platforms.base` | a file to send is found by the plugin's own reading alone |
| `model_tools`, `agent.turn_context` (looked up in `sys.modules`) | `model_tools` missing: the session's tool list is empty; `agent.turn_context`: not tried |

## 11. Remove everything

`hermes habits cleanup` removes every cron job and every file of `$HERMES_HOME/scripts` whose name starts with `habits-`, including ones the plugin did not create, and nothing else: not `plugin-data/habits/`, not your habit skills. Then `hermes plugins uninstall habits`, then delete `$HERMES_HOME/plugin-data/habits/` and the habit skills you no longer want. Removing the plugin first leaves the jobs in place.
