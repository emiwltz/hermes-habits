"""What Habits says to people, in English and in French: the language setting picks one (en by default).

Only the plugin's own lines to the user live here, twins in both languages: a key one language lacks, or a field
one twin lacks, fails a test. What the model reads stays in English: it is written for the model, not shown.
These are the news lines (ready, retired, demoted) and the direct answer's footer;
the /habits cockpit keeps its own beside them, with the same say() and verbs (/habits try, /habits wrong).
"""

from __future__ import annotations

import re

LANGUAGES = ("en", "fr")

# Never shown: the context of a turn where a habit the plugin did not save runs dry beside it. Neither its procedure
# nor its end text reaches the model before the user's yes, so nothing it holds can give orders.
MODEL_LEARNING = ("[Habits] A habit is learning from this request. Do the task as you normally would; "
                  "do not call habit_run.")
# How the model passes a line on, when no message could be posted at the moment it happened.
MODEL_TELL = "[Habits] Before anything else, give the user this line as it is, on its own line: {line}"

TEXTS = {
    "en": {
        "ready": "❓ {name} matched my answer {agreed} times out of {n}{gain}. Shall I let it answer on its own? /habits",
        "gain": " · {fast} instead of {slow}",
        "retired": "─ {name} goes to rest: {why}. To bring it back: /habits try {name}",
        "demoted": "┄ {name} goes back to learning: {why}. It waits for your yes to act on its own again. /habits",
        "footer": "🔁 {name} · {took} · {calls} · not right? /habits wrong",
        "calls": "{n} model calls", "call": "1 model call", "no_calls": "0 model calls", "decided": " (+ {names})",
        # The ledger's notes, as the thresholds write them, in the user's words
        "why_errors": "{n} errors in a row", "why_agreement": "{right} right out of {n}",
        "why_spots": "{n} spot checks in a row under {percent} %", "why_woke": "it handed back {n} asks in a row",
        "why_wrong": "you said it was wrong",
        # The /habits cockpit: the grouped list
        "head": "🔁 Your habits · {active} · {learning}",
        "active_one": "automatic", "active_many": "automatic",
        "learning_one": "learning", "learning_many": "learning",
        "decisions_one": "❓ {count} decision ↓", "decisions_many": "❓ {count} decisions ↓",
        "paused_head": "⏸ Paused: no habit starts from a message or a schedule · /habits resume",
        "line_active": "━ {name} · automatic{gain}",
        "line_shadow": "┄ {name} · learning {compared}/{runs}{extra}",
        "line_ready": "❓ {name} · ready · {agreed}/{compared} right",
        "line_retired": "─ {name} · resting",
        "line_byname": "◇ {name} · called by name",
        "line_invalid": "◇ {name} · invalid · /habits {name}",
        "right": " · {percent} right",
        "more": "+{count} more · /habits <name>",
        "more_one": "+1 more · /habits <name>",
        "more_waiting": "{count:+# more waits|+# more wait}: {names}",
        "none": '🔁 No habits of your own yet. After a task you repeat, tell Hermes "make it a habit".',
        "none_shipped": "Built in, ready now: {names}.",
        "none_shipped_writes": "Also built in, and it writes files: {names}. Test it before you rely on it.",
        "none_try": "Try one: ask \"Is Hermes OK?\"",
        "none_try_cli": "Try one: hermes habits run hermes-health",
        # the decision cards
        "card_ready": "{name} · {agreed}/{compared} right{gain} · {access}. Last asks: {asks}. Let it answer on its "
                      "own?",
        "card_plain": "{name} · {agreed}/{compared} right{gain} · {access}. Let it answer on its own?",
        "card_writes": "It will do: {steps}",
        "quote": "\"{text}\"",
        "reads": "read-only ({steps})",
        "reads_alone": "read-only",
        "writes": "it writes",
        "yes": "Yes, automatic",
        "later": "Keep practicing",
        "rest": "Put it to rest",
        "ask_ready": "❓ {name}: /habits yes {name} · /habits try {name} · /habits rest {name}",
        # what each answer does
        "after_yes": "✅ {name} answers on its own now. I check behind it 1 time in {every}. Not right? /habits wrong",
        "after_yes_unchecked": "✅ {name} answers on its own now. Not right? /habits wrong",
        "after_yes_close": "✅ {name} answers on its own now. Its first runs stay as slow as before: Hermes checks behind "
                           "it until {n} runs agree, then it answers alone (1 run in {every} is still checked). Not "
                           "right? /habits wrong",
        "after_yes_close_one": "✅ {name} answers on its own now. Its next run stays as slow as before: Hermes checks "
                               "behind it until 1 run agrees, then it answers alone (1 run in {every} is still "
                               "checked). Not right? /habits wrong",
        "detail_checking": "Where it stands: Hermes still checks behind it ({agreed} of {needed} runs agreed), so its "
                           "runs are as slow as before; then it answers alone.",
        "detail_alone": "Where it stands: it answers alone; 1 run in {every} is still checked behind.",
        "undo_close": " I check behind it until {n} runs agree.", "undo_close_one": " I check behind it until 1 run agrees.",
        "changed": "🤔 {name} changed while you were deciding: it learns its new version first.",
        "after_later": "┄ OK, it keeps learning. I'll ask you again in {runs} runs.",
        "after_later_one": "┄ OK, it keeps learning. I'll ask you again in 1 run.",
        "after_rest": "─ {name} is resting. To bring it back: /habits try {name}",
        "after_try": "┄ {name} practices beside Hermes, without acting.",
        "learning_already": "┄ {name} is learning already ({compared}/{runs}).",
        "active_already": "━ {name} answers on its own already. Not right? /habits wrong",
        "active_already_called": "━ {name} acts on its own already when Hermes calls it. Not right? /habits wrong",
        "after_yes_called": "✅ {name} acts on its own now when Hermes calls it. Not right? /habits wrong",
        "resting": "─ {name} is resting. To bring it back: /habits try {name}",
        "byname": "◇ {name} is called by name: there is nothing to decide.",
        "example_byname": "◇ \"{name}\" is a built-in example: it is called by name and never acts on its own. "
                          "To try it: hermes habits run {name}",
        "example_yes": "◇ \"{name}\" is a built-in example: Hermes calls it by name and it never acts on its own. For a copy "
                       "of your own that can answer before the model, copy the folder {path} into your Hermes skills "
                       "folder under another name (for example my-{name}) and change the `name:` line at the top of its SKILL.md to "
                       "the same name, then say yes to the copy: hermes habits yes "
                       "my-{name}. A decision-model key (TYPESAFE_API_KEY) makes it fast; without one Hermes's own "
                       "model answers, slower. To try the example now: hermes habits run {name}",
        "invalid": "◇ {name} is invalid: /habits {name} says why.",
        "after_wrong": "↩︎ Noted: {name} goes back to learning and no longer acts on its own. Ask me again, I'll do it "
                       "myself.",
        "wrong_none": "No habit answered on its own in this conversation.",
        "after_undo": "↩︎ Undone: {name} is \"{state}\" again, as before {change} at {time}.",
        "undo_none": "Nothing to undo in the last 24 h.",
        "paused": "⏸ Habits paused: none starts from a message or a schedule, and the scheduled ones stay quiet. "
                  "/habits resume",
        "resumed": "▶︎ Back on: {active}, {learning}.",
        "which": "Which one? /habits {verb} <name>",
        "nothing_waits": "Nothing waits for your decision. /habits",
        "unknown": "No habit named \"{name}\". /habits lists them.",
        "unknown_cli": "No habit named \"{name}\". hermes habits list lists them.",
        "promote_made": "🤔 {name} was made by Hermes: only your yes in a chat makes it act (`/habits yes {name}`). "
                        "The model has a terminal too, so hermes habits yes cannot.",
        "error": "habits: {kind}: {exc}",
        "menu": "Your habits and their decisions: /habits, /habits <name>, /habits dream, /habits view, "
                "/habits help",
        # /habits <name>: one habit in detail
        "trigger_name": "called by name", "trigger_request": "starts from a message",
        "trigger_skill": "starts when its skill is loaded", "run_skill": "its skill was loaded",
        "trigger_every": "starts on a schedule: {period}", "trigger_at": " at {at}", "info": "{trigger} · version {version}", "example": " · example",
        "detail_gain": "{fast} instead of {slow}", "detail_tokens": "{active} tokens instead of {shadow}",
        "detail_checks": "{checks} · {wakes}", "detail_invalid": "invalid:",
        "checks_one": "check", "checks_many": "checks", "wakes_one": "handed back", "wakes_many": "handed back",
        "run_shadow": "dry", "run_active": "on its own", "run_spot": "spot check", "run_adhoc": "called by name",
        "status_end": "done", "status_wake": "handed back",
        "run_turn": " · turn {took}", "run_matched": " · {matched}/{planned} right", "run_judge": " · judge: {why}",
        "page": "page: {path}", "file": "file: {path}", "steps": "steps:", "page_chat": "diagram: /habits view {name}",
        "page_none": "diagram: hermes habits view {name}",
        "draft_head": "◇ {name} · a draft, not a habit yet · {state}",
        "draft_state_spotted": "waits for a place (3 practice at a time)", "draft_state_spotted_try": "waits for your try",
        "draft_state_practising": "practices beside Hermes", "draft_state_ready": "is ready for your yes",
        "draft_state_rest": "is at rest",
        "draft_saw": "The dream saw Hermes do this in {turns:# turn|# turns} on {days:# day|# days}{moments}.",
        "draft_legend": "closes the work: it was the last call of its turn · first gesture of a session: it was the "
                        "first call of a session",
        "draft_steps": "The reflex it would become, whole:",
        "draft_try_here": "Test it here (reads run, the rest is only planned, nothing is written): hermes habits run {name}",
        "draft_next_spotted": "It starts practicing when a place is free.",
        "draft_next_practising": "{matches} of {needed} so far: it asks for your yes in a chat after "
                                 "{left:# more time|# more times}; nothing to answer now.",
        "draft_next_rest": "It practices again when its action comes back.",
        "draft_note": "◇ {name} is a draft, not a habit yet: it {state}. {next}",
        "dream_home_missing": "{home} is not a folder: there is no history to read there.",
        "run_refused": "refused: {why}", "run_input_bad": "--input {pair}: write name=value",
        "run_unknown": "no habit named \"{name}\"; known habits: {known}", "run_invalid": "invalid reflex: {why}",
        "run_missing": "missing inputs: {names} (--input name=value)", "run_no_file": "no file {path}",
        "run_not_message": "--message: {name} does not start from a message",
        "run_done": "done", "run_handed": "handed back",
        "run_how_live": "live run", "run_how_reads": "it only reads: nothing to write",
        "run_how_dry": "dry run: writes planned, not done",
        "run_handed_head": "Handed back to Hermes ({reason}); its words for Hermes:",
        "run_stopped": "the reflex stopped here", "run_dry_said": "Dry run (nothing written): {message}",
        "run_dry_said_empty": "Dry run (nothing written).",
        "saved_alone": "✅ {name} is saved. You asked for it and it only reads, so from now on it answers such requests on its own, without a model turn. To stop it: /habits rest {name}.",
        "run_line_planned": "{name} · planned, not run · {planned:# call|# calls} only planned, none made",
        "run_planned_why": " · only planned: {why}",
        "why_web": "a call to the web: a test run never makes one",
        "why_unknown": "{program} is a program the plugin does not know as a reader",
        "why_network": "it reaches the network or another machine",
        "why_writes": "it may write or change something",
        "why_unsure": "the plugin cannot be sure that it only reads",
        "why_tool": "{tool} is not a local read",
        "run_line": "{name} · {outcome} in {took} s · {asked:# question|# questions}{who} · {calls:# tool call|# tool calls}"
                    " · {how}",
        "run_sends": "sends: {file}", "run_replay": "replay: {path}",
        "check_valid": "valid: the reflex follows every rule.", "check_format": "format: {why}",
        "check_normalized": "normalized: {note}", "removed": "removed: {what}", "removed_nothing": "nothing",
        "view_gallery": "The gallery of your habits", "view_one": "The diagram of {name}",
        "view_failed": "Could not write the page of {name}.",
        "view_open": "{said}: open the file in a browser for the interactive view.", "view_at": "{said}: {uri}",
        # states and changes, as an undo names them
        "state_active": "automatic",
        "state_shadow": "learning",
        "state_retired": "resting",
        "state_byname": "called by name",
        "change_yes": "your yes",
        "change_rest": "you put it to rest",
        "change_wrong": "your \"wrong\"",
        "change_try": "your trial",
        "change_undo": "your undo",
        "change_other": "your change",
        "help": "/habits · your habits, then the decisions waiting for you\n"
                "/habits <name> · one habit: its numbers and last 5 runs\n"
                "/habits yes <name> · let a habit answer on its own\n"
                "/habits try <name> · keep it practicing, or bring it back\n"
                "/habits rest <name> · put it to rest\n"
                "/habits wrong · the last habit that answered here got it wrong\n"
                "/habits undo · undo your last change (24 h)\n"
                "/habits pause · /habits resume · no habit starts from a message or a schedule, then start again\n"
                "/habits view [name] · the gallery, or one habit's diagram\n"
                "/habits dream · re-read Hermes's history now and show what comes back\n"
                "In a terminal: hermes habits list, show, view, check, dream, run, yes, rest, pause, resume, undo, "
                "reset, cleanup",
        # The dream and the reflexes
        "dream_off": "Learning is off: the dream does not run. To turn it on, type `hermes config set plugins.entries.habits.settings.learning practice` (practice can also be suggest or auto).",
        "dream_habits_off": "Habits is off (HABITS_OFF is set): the dream does not run.",
        "dream_empty": "Nothing to learn from yet: Hermes's history holds no turn from {days:the last day|the last # days}. "
                       "Work with Hermes for a few days, then run `hermes habits dream` again. To see a reflex act now: "
                       "`hermes habits run hermes-health`.",
        "dream_unread": "Hermes's history could not be read ({why}): nothing learned this time. "
                        "If Hermes was busy, try again in a minute; with --home, check that the folder holds a state.db.",
        "dream_failed": "The dream stopped on an error of Habits itself ({why}), not of Hermes's history: nothing "
                        "learned this time. Hermes's log has the detail.",
        "dream_busy": "A dream is already running: wait a minute, then run it again.",
        "dream_read": "Read {turns:# turn|# turns} in {sessions:# session|# sessions} over {days:# day|# days} ({took}).",
        "dream_partial": "Read the newest part only ({why}).",
        "dream_next": "Next: keep working with Hermes. The dream re-reads after a reply once a day; "
                      "`hermes habits dream` re-reads now. To see what practices: /habits.",
        "dream_next_suggest": "Next: nothing runs until you say try. `hermes habits dream` re-reads now; "
                              "`hermes habits list` shows what waits for your word.",
        "spotted_waiting": "{count:# more action waits|# more actions wait} for a place ({places} practice at a time): {names}.",
        "drafts_in_all": "{count:# draft waits for you|# drafts wait for you} in all: `hermes habits list` names them, one per line.",
        "resting_drafts": "- {count:# draft rests|# drafts rest} ({names}). If its action comes back, it practices again.",
        "more_drafts": "+{count} more: {names}",
        "spotted_full": " · in full: hermes habits show {name}",
        "dream_look": "This was a look: nothing was saved. The plain `hermes habits dream` reads the last {max} days and keeps what it finds as drafts.",
        "dream_look_home": "This was a look at another home: nothing was saved here. The plain `hermes habits dream` reads this home's own history and keeps what it finds as drafts.",
        "drafts_head": "The dream's drafts (not habits yet):",
        "draft_row": "- {name} · {state} · `{action}`",
        "draft_row_try": "waits for your try", "draft_row_place": "waits for a place",
        "draft_row_practising": "practices {matches} of {needed}", "draft_row_ready": "ready: waits for your yes",
        "draft_row_rest": "rests",
        "drafts_tail": "hermes habits show <name> prints one in full; hermes habits run <name> tests it.",
        "spotted_one": "1 action comes back:", "spotted_many": "{count} actions come back:",
        "spotted_line": "- `{action}` · {turns:# turn|# turns} on {days:# day|# days}{moments}",
        "spotted_none": "Nothing comes back often enough yet: a reflex is proposed at 3 turns on 2 days.",
        "near_line": "- closest: `{action}` · {turns:# turn|# turns} on {days:# day|# days}",
        "spotted_done_by": " · the reflex {name} already does it: no draft",
        "cut_note": "A command shown with … acts: hermes habits show <name> prints its full text.",
        "left_line": "Left out on purpose: {count} turns (secrets, typed text, noise, failed or refused calls, deleting "
                     "commands, commands that never end, invalid forms).",
        "list_empty": "🔁 No habits of your own yet. After a task you repeat, tell Hermes \"make it a habit\". "
                      "Run `hermes habits dream` to see what comes back in your history.",
        "moment_first": "· first gesture of a session ({percent})",
        "moment_last": "· closes the work ({percent})",
        "moment_skill": "· after the skill {skill} ({percent})",
        "moment_scheduled": "· in a scheduled job ({percent})",
        "practising_line": "- {name} practices beside Hermes · {matches} of {needed} the same (it runs once every 10 minutes "
                          "at most)",
        "practising_plan_line": "- {name} practices beside Hermes · came back {matches} of {needed} times",
        "card_learn": "{name} runs `{action}`: Hermes did it in {turns:# turn|# turns} on {days:# day|# days}{moments}. Beside Hermes: "
                      "{matches} of {compared} the same. Make it a reflex that acts on its own?",
        "card_learn_plan": "{name} would run `{action}` (it acts): Hermes did it in {turns:# turn|# turns} on {days:# day|# days}"
                           "{moments}; it came back {matches} times since. Make it a reflex that acts on its own?",
        "shown_in": "in {where}", "shown_timeout": "timeout {n}", "shown_background": "in the background",
        "card_try": "Hermes ran `{action}` in {turns:# turn|# turns} on {days:# day|# days}{moments}. Let a reflex practice it beside "
                    "Hermes?",
        "card_flagged": "With your yes it may also run these commands, as Hermes's own approval allows: {commands}",
        "card_change": "{name}: Hermes now runs `{sibling}` ({turns:# turn|# turns} on {days:# day|# days}), and `{action}` was not "
                       "seen for {quiet:# day|# days}. Change the reflex to the new form?",
        "card_rest_quiet": "{name} was not used for {quiet:# day|# days}. Put it to rest?",
        "no": "No", "try": "Try",
        "after_learned": "{name} is a reflex now: it acts on its own when Hermes calls it.",
        "after_learned_staged": "{name} waits for your approval of its skill files.",
        "after_changed_since": "{name} changed since this card was shown: see it again with /habits.",
        "after_renamed": "{name} is already the name of another skill, so this reflex is now called {new}. Say yes "
                         "again to make it a reflex under that name.",
        "card_learn_came": "{name} runs `{action}`: Hermes did it in {turns:# turn|# turns} on {days:# day|# days}{moments}; it came "
                           "back {matches:# time|# times} since, and nothing was compared: it cannot be run "
                           "safely beside Hermes. Make it a reflex that acts on its own?",
        "card_learn_grown": "{name} would run `{action}`: these came back together in {turns:# turn|# turns} on {days:# day|# days}"
                            "{moments}; the sequence came back {matches} times since, and nothing was compared. "
                            "Make it a reflex that acts on its own?",
        "card_folder_here": "It runs in the folder Hermes is in when it is called.",
        "procedure_folder": "It runs in {folder}.",
        "procedure_folder_here": "It runs in the folder you are in when you call it.",
        "after_no": "Noted: {name} will not be proposed again for {days:# day|# days}.",
        "try_full": "{name} waits: 3 reflexes already practice beside Hermes.",
        "after_changed": "{name} now runs `{action}`.",
        "news_learn": "Habits: {name} practiced well beside Hermes. /habits to make it a reflex.",
        "news_learned_auto": "Habits: {name} now acts on its own, as your setting learning: auto allows. /habits to see it or put it to rest.",
        "news_proposal": "Habits: a proposal about {name} is waiting in /habits.",
        "ask_dream": "Answer with /habits yes {name} or /habits no {name}.",
        "ask_dream_try": "Answer with /habits try {name} or /habits no {name}.",
        "ask_dream_again": "This is what your yes covers. Say /habits yes {name} again to give it, or /habits no {name}.",
        # a version the user was not shown, a reflex on hold, what a terminal says waits (last pass)
        "card_unseen": "{name} was saved again: this version is not one you said yes to. It would run: {steps}. Its "
                       "folder: {folder}. Let it answer on its own?",
        "ask_unseen": "Say /habits yes {name} again to let this version answer on its own, or /habits rest {name}.",
        "line_unseen": "❓ {name} · saved again · waits for your yes",
        "line_byname_wait": "◇ {name} · called by name · waits for your yes: /habits yes {name}",
        "card_copied": "{name} is a reflex you added yourself. Hermes may call it by name only after your yes. It would "
                       "run: {steps}. Its folder: {folder}. Let Hermes call it?",
        "ask_copied": "Say /habits yes {name} again to let Hermes call it, or /habits rest {name}.",
        "copied_already": "◇ {name} can be called by Hermes already. Not right? /habits wrong",
        "after_yes_copied": "✅ {name} can be called by Hermes now, and it runs only the steps you saw. Not right? "
                            "/habits wrong",
        "line_held": "┄ {name} · on hold · /habits yes {name} lets it act",
        "line_held_chat": "┄ {name} · on hold · your yes in a chat lets it act: `/habits yes {name}`",
        "after_try_held": "┄ {name} has nothing to practice on: it acts on its own only after your yes. /habits yes {name}",
        "after_rest_held": "─ {name} is resting. To bring it back: /habits yes {name}",
        "practising_rule": "A reflex that practices but whose action does not come back for {days} days rests and gives "
                           "its place back.",
        "waits_chat": "In a chat with Hermes, type: `/habits yes {name}` or `/habits no {name}`.",
        "waits_chat_try": "In a chat with Hermes, type: `/habits try {name}` or `/habits no {name}`.",
        "draft_invalid": "◇ {name} cannot become a reflex as it stands: {why}",
    },
    "fr": {
        "ready": "❓ {name} a fait comme moi {agreed} fois sur {n}{gain}. Je la laisse répondre seule ? /habits",
        "gain": " · {fast} au lieu de {slow}",
        "retired": "─ {name} passe au repos : {why}. La relancer : /habits essai {name}",
        "demoted": "┄ {name} repart en apprentissage : {why}. Elle attend ton oui pour agir seule à nouveau. /habits",
        "footer": "🔁 {name} · {took} · {calls} · pas ça ? /habits faux",
        "calls": "{n} appels au modèle", "call": "1 appel au modèle", "no_calls": "0 appel au modèle", "decided": " (+ {names})",
        "why_errors": "{n} erreurs d'affilée", "why_agreement": "{right} justes sur {n}",
        "why_spots": "{n} vérifications d'affilée sous {percent} %",
        "why_woke": "elle m'a rendu {n} demandes d'affilée", "why_wrong": "tu as dit qu'elle se trompait",
        "head": "🔁 Tes habitudes · {active} · {learning}",
        "active_one": "automatique", "active_many": "automatiques",
        "learning_one": "en apprentissage", "learning_many": "en apprentissage",
        "decisions_one": "❓ {count} décision ↓", "decisions_many": "❓ {count} décisions ↓",
        "paused_head": "⏸ En pause : aucune habitude ne part d'un message ou d'un horaire · /habits reprends",
        "line_active": "━ {name} · automatique{gain}",
        "line_shadow": "┄ {name} · apprend {compared}/{runs}{extra}",
        "line_ready": "❓ {name} · prête · {agreed}/{compared} justes",
        "line_retired": "─ {name} · au repos",
        "line_byname": "◇ {name} · par son nom",
        "line_invalid": "◇ {name} · invalide · /habits {name}",
        "right": " · {percent} justes",
        "more": "+{count} autres · /habits <nom>",
        "more_one": "+1 autre · /habits <nom>",
        "more_waiting": "{count:+# autre attend|+# autres attendent} : {names}",
        "none": "🔁 Pas encore d'habitude à toi. Après une tâche que tu répètes, dis à Hermes « fais-en une habitude ».",
        "none_shipped": "Fournis, prêts tout de suite : {names}.",
        "none_shipped_writes": "Fourni aussi, et il écrit des fichiers : {names}. Teste-le avant de t'y fier.",
        "none_try": "Essaie : demande « Hermes va bien ? »",
        "none_try_cli": "Essaie : hermes habits run hermes-health",
        "card_ready": "{name} · {agreed}/{compared} justes{gain} · {access}. Dernières demandes : {asks}. La laisser "
                      "répondre seule ?",
        "card_plain": "{name} · {agreed}/{compared} justes{gain} · {access}. La laisser répondre seule ?",
        "card_writes": "Elle fera : {steps}",
        "quote": "« {text} »",
        "reads": "lecture seule ({steps})",
        "reads_alone": "lecture seule",
        "writes": "elle écrit",
        "yes": "Oui, automatique",
        "later": "Encore à l'essai",
        "rest": "La mettre au repos",
        "ask_ready": "❓ {name} : /habits oui {name} · /habits essai {name} · /habits repos {name}",
        "after_yes": "✅ {name} répond seule maintenant. Je vérifie derrière elle 1 fois sur {every}. Pas ça ? /habits faux",
        "after_yes_unchecked": "✅ {name} répond seule maintenant. Pas ça ? /habits faux",
        "after_yes_close": "✅ {name} répond seule maintenant. Ses premières fois restent aussi lentes qu'avant : Hermes "
                           "vérifie derrière elle jusqu'à {n} fois d'accord, puis elle répond seule (1 fois sur "
                           "{every} reste vérifiée). Pas ça ? /habits faux",
        "after_yes_close_one": "✅ {name} répond seule maintenant. Sa prochaine fois reste aussi lente qu'avant : Hermes "
                               "vérifie derrière elle jusqu'à 1 fois d'accord, puis elle répond seule (1 fois sur "
                               "{every} reste vérifiée). Pas ça ? /habits faux",
        "detail_checking": "Où elle en est : Hermes vérifie encore derrière elle ({agreed} fois d'accord sur "
                           "{needed}), donc ses réponses sont aussi lentes qu'avant ; ensuite elle répond seule.",
        "detail_alone": "Où elle en est : elle répond seule ; 1 fois sur {every} reste vérifiée derrière elle.",
        "undo_close": " Je vérifie derrière elle jusqu'à {n} fois d'accord.",
        "undo_close_one": " Je vérifie derrière elle jusqu'à 1 fois d'accord.",
        "changed": "🤔 {name} a changé pendant que tu décidais : elle réapprend sa nouvelle version d'abord.",
        "after_later": "┄ D'accord, elle continue d'apprendre. Je te redemande dans {runs} passages.",
        "after_later_one": "┄ D'accord, elle continue d'apprendre. Je te redemande dans 1 passage.",
        "after_rest": "─ {name} est au repos. La relancer : /habits essai {name}",
        "after_try": "┄ {name} s'entraîne à côté de Hermes, sans agir.",
        "learning_already": "┄ {name} apprend déjà ({compared}/{runs}).",
        "active_already": "━ {name} répond déjà seule. Pas ça ? /habits faux",
        "active_already_called": "━ {name} agit déjà seule quand Hermes l'appelle. Pas ça ? /habits faux",
        "after_yes_called": "✅ {name} agit seule maintenant quand Hermes l'appelle. Pas ça ? /habits faux",
        "resting": "─ {name} est au repos. La relancer : /habits essai {name}",
        "byname": "◇ {name} se lance par son nom : rien à décider.",
        "example_byname": "◇ « {name} » est un exemple fourni : il se lance par son nom et n'agit jamais seul. "
                          "Pour l'essayer : hermes habits run {name}",
        "example_yes": "◇ « {name} » est un exemple fourni : Hermes l'appelle par son nom et il n'agit jamais seul. Pour "
                       "une copie à toi qui répond avant le modèle, copie le dossier {path} dans ton dossier de skills "
                       "Hermes sous un autre nom (par exemple mon-{name}), change la ligne `name:` en tête de son SKILL.md pour le "
                       "même nom, puis dis oui à la copie : hermes habits yes "
                       "mon-{name}. Une clé du modèle de décision (TYPESAFE_API_KEY) la rend rapide ; sans clé, le "
                       "modèle de Hermes répond, plus lentement. Pour essayer l'exemple tout de suite : hermes habits "
                       "run {name}",
        "invalid": "◇ {name} est invalide : /habits {name} dit pourquoi.",
        "after_wrong": "↩︎ Noté : {name} repart en apprentissage et n'agit plus seule. Redemande-moi, je le fais moi-même.",
        "wrong_none": "Aucune habitude n'a répondu seule dans cette conversation.",
        "after_undo": "↩︎ Annulé : {name} redevient « {state} », comme avant {change} de {time}.",
        "undo_none": "Rien à annuler depuis 24 h.",
        "paused": "⏸ Habitudes en pause : aucune ne part d'un message ou d'un horaire, et les habitudes planifiées se taisent. "
                  "/habits reprends",
        "resumed": "▶︎ C'est reparti : {active}, {learning}.",
        "which": "Laquelle ? /habits {verb} <nom>",
        "nothing_waits": "Rien n'attend ta décision. /habits",
        "unknown": "Pas d'habitude nommée « {name} ». /habits les liste.",
        "unknown_cli": "Pas d'habitude nommée « {name} ». hermes habits list les liste.",
        "promote_made": "🤔 {name} vient de Hermes : seul ton oui dans un chat la fait agir (`/habits oui {name}`). "
                        "Le modèle a un terminal lui aussi, donc hermes habits yes ne le peut pas.",
        "error": "habits : erreur {kind} : {exc}",
        "menu": "Tes habitudes et leurs décisions : /habits, /habits <nom>, /habits rêve, /habits voir, "
                "/habits aide",
        "trigger_name": "par son nom", "trigger_request": "part d'un message",
        "trigger_skill": "part quand sa skill est chargée", "run_skill": "sa skill chargée",
        "trigger_every": "part d'un horaire : {period}",
        "trigger_at": " à {at}", "info": "{trigger} · version {version}", "example": " · exemple",
        "detail_gain": "{fast} au lieu de {slow}", "detail_tokens": "{active} jetons au lieu de {shadow}",
        "detail_checks": "{checks} · {wakes}", "detail_invalid": "invalide :",
        "checks_one": "vérification", "checks_many": "vérifications", "wakes_one": "rendue", "wakes_many": "rendues",
        "run_shadow": "à blanc", "run_active": "seule", "run_spot": "contrôle", "run_adhoc": "par son nom",
        "status_end": "finie", "status_wake": "rendue",
        "run_turn": " · tour {took}", "run_matched": " · {matched}/{planned} justes", "run_judge": " · juge : {why}",
        "page": "page : {path}", "file": "fichier : {path}", "steps": "étapes :", "page_chat": "schéma : /habits voir {name}",
        "page_none": "schéma : hermes habits view {name}",
        "draft_head": "◇ {name} · un brouillon, pas encore une habitude · {state}",
        "draft_state_spotted": "attend une place (3 s'entraînent à la fois)", "draft_state_spotted_try": "attend ton essai",
        "draft_state_practising": "s'entraîne à côté de Hermes", "draft_state_ready": "est prêt pour ton oui",
        "draft_state_rest": "est au repos",
        "draft_saw": "Le rêve a vu Hermes le faire en {turns:# tour|# tours} sur {days:# jour|# jours}{moments}.",
        "draft_legend": "clôt le travail : c'était le dernier appel de son tour · premier geste d'une session : c'était "
                        "le premier appel d'une session",
        "draft_steps": "Le réflexe qu'il deviendrait, en entier :",
        "draft_try_here": "Teste-le ici (les lectures tournent, le reste est seulement prévu, rien n'est écrit) : hermes habits run {name}",
        "draft_next_spotted": "Il s'entraîne dès qu'une place se libère.",
        "draft_next_practising": "{matches} sur {needed} pour l'instant : il demande ton oui dans un chat après "
                                 "{left:# fois de plus|# fois de plus} ; rien à répondre maintenant.",
        "draft_next_rest": "Il s'entraîne de nouveau quand son action revient.",
        "draft_note": "◇ {name} est un brouillon, pas encore une habitude : il {state}. {next}",
        "dream_home_missing": "{home} n'est pas un dossier : il n'y a pas d'historique à lire là.",
        "run_refused": "refusé : {why}", "run_input_bad": "--input {pair} : écris nom=valeur",
        "run_unknown": "pas d'habitude nommée « {name} » ; habitudes connues : {known}",
        "run_invalid": "réflexe invalide : {why}",
        "run_missing": "valeurs manquantes : {names} (--input nom=valeur)", "run_no_file": "pas de fichier {path}",
        "run_not_message": "--message : {name} ne part pas d'un message",
        "run_done": "finie", "run_handed": "rendue à Hermes",
        "run_how_live": "passage pour de vrai", "run_how_reads": "elle ne fait que lire : rien à écrire",
        "run_how_dry": "passage à blanc : écritures prévues, pas faites",
        "run_handed_head": "Rendu à Hermes ({reason}) ; ses mots pour Hermes :",
        "run_stopped": "le réflexe s'est arrêté là", "run_dry_said": "À blanc (rien n'est écrit) : {message}",
        "run_dry_said_empty": "À blanc (rien n'est écrit).",
        "saved_alone": "✅ {name} est enregistrée. Tu l'as demandée et elle ne fait que lire : dès maintenant elle répond toute seule à ces demandes, sans tour de modèle. Pour l'arrêter : /habits rest {name}.",
        "run_line_planned": "{name} · prévu, pas lancé · {planned:# appel seulement prévu|# appels seulement prévus}, aucun fait",
        "run_planned_why": " · seulement prévu : {why}",
        "why_web": "un appel au web : un test n'en fait jamais",
        "why_unknown": "{program} est un programme que le plugin ne connaît pas comme lecteur",
        "why_network": "il atteint le réseau ou une autre machine",
        "why_writes": "il peut écrire ou changer quelque chose",
        "why_unsure": "le plugin ne peut pas être sûr qu'il ne fait que lire",
        "why_tool": "{tool} n'est pas une lecture locale",
        "run_line": "{name} · {outcome} en {took} s · {asked:# question|# questions}{who} · {calls:# appel d'outil|# appels d'outils}"
                    " · {how}",
        "run_sends": "envoie : {file}", "run_replay": "rejeu : {path}",
        "check_valid": "valide : le réflexe suit toutes les règles.", "check_format": "format : {why}",
        "check_normalized": "corrigé : {note}", "removed": "enlevé : {what}", "removed_nothing": "rien",
        "view_gallery": "La galerie de tes habitudes", "view_one": "Le schéma de {name}",
        "view_failed": "Impossible d'écrire la page de {name}.",
        "view_open": "{said} : ouvre le fichier dans un navigateur pour la vue interactive.", "view_at": "{said} : {uri}",
        "state_active": "automatique",
        "state_shadow": "en apprentissage",
        "state_retired": "au repos",
        "state_byname": "par son nom",
        "change_yes": "ton oui",
        "change_rest": "ta mise au repos",
        "change_wrong": "ton « faux »",
        "change_try": "ton essai",
        "change_undo": "ton annulation",
        "change_other": "ton changement",
        "help": "/habits · tes habitudes, puis les décisions qui t'attendent\n"
                "/habits <nom> · une habitude : ses chiffres et ses 5 derniers passages\n"
                "/habits oui <nom> · la laisser répondre seule\n"
                "/habits essai <nom> · la garder à l'essai, ou la relancer\n"
                "/habits repos <nom> · la mettre au repos\n"
                "/habits faux · la dernière habitude qui a répondu ici s'est trompée\n"
                "/habits annule · annule ton dernier changement (24 h)\n"
                "/habits pause · /habits reprends · plus rien ne part d'un message ou d'un horaire, puis repartir\n"
                "/habits voir [nom] · la galerie, ou le schéma d'une habitude\n"
                "/habits rêve · relire l'historique de Hermes maintenant et montrer ce qui revient\n"
                "Dans un terminal : hermes habits list, show, view, check, dream, run, yes, rest, pause, resume, undo, "
                "reset, cleanup",
        # Le rêve et les réflexes
        "dream_off": "L'apprentissage est coupé : le rêve ne tourne pas. Pour le rallumer, tape `hermes config set plugins.entries.habits.settings.learning practice` "
                     "(practice peut aussi être suggest ou auto).",
        "dream_habits_off": "Habits est coupé (HABITS_OFF est défini) : le rêve ne tourne pas.",
        "dream_empty": "Rien à apprendre pour l'instant : l'historique de Hermes ne contient aucun tour "
                       "{days:du dernier jour|des # derniers jours}. Travaille quelques jours avec Hermes, puis relance "
                       "`hermes habits dream`. Pour voir un réflexe agir tout de suite : `hermes habits run hermes-health`.",
        "dream_unread": "L'historique de Hermes n'a pas pu être lu ({why}) : rien appris cette fois. "
                        "Si Hermes était occupé, réessaie dans une minute ; avec --home, vérifie que le dossier contient un state.db.",
        "dream_failed": "Le rêve s'est arrêté sur une erreur de Habits lui-même ({why}), pas de l'historique de "
                        "Hermes : rien appris cette fois. Le détail est dans les logs de Hermes.",
        "dream_busy": "Un rêve tourne déjà : attends une minute, puis relance-le.",
        "dream_read": "Lu {turns:# tour|# tours} dans {sessions:# session|# sessions}, étalés sur {days:# jour|# jours} ({took}).",
        "dream_partial": "Seule la partie la plus récente a été lue ({why}).",
        "dream_next": "Ensuite : continue à travailler avec Hermes. Le rêve relit l'historique après une réponse, une "
                      "fois par jour ; `hermes habits dream` le relit tout de suite. Pour voir ce qui s'entraîne : /habits.",
        "dream_next_suggest": "Ensuite : rien ne s'exécute avant ton « essai ». `hermes habits dream` relit tout de "
                              "suite ; `hermes habits list` montre ce qui attend ton mot.",
        "spotted_waiting": "{count:# action de plus attend|# actions de plus attendent} une place ({places} "
                           "s'entraînent à la fois) : {names}.",
        "drafts_in_all": "{count:# brouillon attend|# brouillons attendent} en tout : `hermes habits list` les nomme, un par ligne.",
        "resting_drafts": "- {count:# brouillon est au repos|# brouillons sont au repos} ({names}). Si son action revient, il "
                          "s'entraîne de nouveau.",
        "more_drafts": "+{count} de plus : {names}",
        "spotted_full": " · en entier : hermes habits show {name}",
        "dream_look": "Ceci était un coup d'œil : rien n'a été gardé. La commande simple `hermes habits dream` lit les {max} derniers jours et garde ce qu'elle trouve en brouillons.",
        "dream_look_home": "Ceci était un coup d'œil sur un autre dossier Hermes : rien n'a été gardé ici. La commande simple `hermes habits dream` lit l'historique de ce dossier-ci et garde ce qu'elle trouve en brouillons.",
        "drafts_head": "Les brouillons du rêve (pas encore des habitudes) :",
        "draft_row": "- {name} · {state} · `{action}`",
        "draft_row_try": "attend ton essai", "draft_row_place": "attend une place",
        "draft_row_practising": "s'entraîne : {matches} sur {needed}", "draft_row_ready": "prêt : attend ton oui",
        "draft_row_rest": "au repos",
        "drafts_tail": "hermes habits show <nom> en affiche un en entier ; hermes habits run <nom> le teste.",
        "spotted_one": "1 action revient :", "spotted_many": "{count} actions reviennent :",
        "spotted_line": "- `{action}` · {turns:# tour|# tours} sur {days:# jour|# jours}{moments}",
        "spotted_none": "Rien ne revient assez souvent pour l'instant : un réflexe est proposé à 3 tours sur 2 jours.",
        "near_line": "- le plus proche : `{action}` · {turns:# tour|# tours} sur {days:# jour|# jours}",
        "spotted_done_by": " · le réflexe {name} le fait déjà : pas de brouillon",
        "cut_note": "Une commande affichée avec … agit : hermes habits show <nom> affiche son texte entier.",
        "left_line": "Laissés de côté exprès : {count} tours (secrets, texte tapé, bruit, appels échoués ou refusés, "
                     "commandes qui suppriment, commandes qui ne finissent jamais, formes invalides).",
        "list_empty": "🔁 Pas encore d'habitude à toi. Après une tâche que tu répètes, dis à Hermes « fais-en une "
                      "habitude ». Lance `hermes habits dream` pour voir ce qui revient dans ton historique.",
        "moment_first": "· premier geste d'une session ({percent})",
        "moment_last": "· clôt le travail ({percent})",
        "moment_skill": "· après le skill {skill} ({percent})",
        "moment_scheduled": "· dans une tâche planifiée ({percent})",
        "practising_line": "- {name} s'entraîne à côté de Hermes · {matches} identiques sur {needed} (il ne tourne qu'une "
                          "fois toutes les 10 minutes au plus)",
        "practising_plan_line": "- {name} s'entraîne à côté de Hermes · est revenu {matches} fois sur {needed}",
        "card_learn": "{name} lance `{action}` : Hermes l'a fait en {turns:# tour|# tours} sur {days:# jour|# jours}{moments}. À côté de "
                      "Hermes : {matches} identiques sur {compared}. En faire un réflexe qui agit seul ?",
        "card_learn_plan": "{name} lancerait `{action}` (il agit) : Hermes l'a fait en {turns:# tour|# tours} sur {days:# jour|# jours}"
                           "{moments} ; depuis, il est revenu {matches} fois. En faire un réflexe qui "
                           "agit seul ?",
        "shown_in": "dans {where}", "shown_timeout": "délai {n} s", "shown_background": "en arrière-plan",
        "card_try": "Hermes a lancé `{action}` en {turns:# tour|# tours} sur {days:# jour|# jours}{moments}. Laisser un réflexe "
                    "s'entraîner à le faire à côté de Hermes ?",
        "card_flagged": "Avec ton oui, il peut aussi lancer ces commandes, comme l'autorise l'approbation de Hermes : "
                        "{commands}",
        "card_change": "{name} : Hermes lance maintenant `{sibling}` ({turns:# tour|# tours} sur {days:# jour|# jours}), et `{action}` "
                       "n'a pas été vu depuis {quiet:# jour|# jours}. Passer le réflexe à la nouvelle forme ?",
        "card_rest_quiet": "{name} n'a pas servi depuis {quiet:# jour|# jours}. Le mettre au repos ?",
        "no": "Non", "try": "Essai",
        "after_learned": "{name} est un réflexe maintenant : il agit seul quand Hermes l'appelle.",
        "after_learned_staged": "{name} attend ton approbation de ses fichiers de skill.",
        "after_changed_since": "{name} a changé depuis l'affichage de cette carte : rouvre /habits.",
        "after_renamed": "{name} est déjà le nom d'un autre skill : ce réflexe s'appelle donc maintenant {new}. Redis "
                         "oui pour en faire un réflexe sous ce nom.",
        "card_learn_came": "{name} lance `{action}` : Hermes l'a fait en {turns:# tour|# tours} sur {days:# jour|# jours}{moments} ; "
                           "il est revenu {matches:# fois|# fois} depuis, sans rien comparer : on ne peut pas le "
                           "lancer sans risque à côté de Hermes. En faire un réflexe qui agit seul ?",
        "card_learn_grown": "{name} lancerait `{action}` : ces gestes reviennent ensemble en {turns:# tour|# tours} sur {days:# jour|# jours}"
                            "{moments} ; la suite est revenue {matches} fois depuis, sans rien comparer. En faire "
                            "un réflexe qui agit seul ?",
        "card_folder_here": "Il s'exécute dans le dossier où Hermes se trouve quand on l'appelle.",
        "procedure_folder": "Il s'exécute dans {folder}.",
        "procedure_folder_here": "Il s'exécute dans le dossier où tu te trouves quand tu l'appelles.",
        "after_no": "Noté : {name} ne sera plus proposé pendant {days:# jour|# jours}.",
        "try_full": "{name} attend : 3 réflexes s'entraînent déjà à côté de Hermes.",
        "after_changed": "{name} lance maintenant `{action}`.",
        "news_learn": "Habits : {name} s'est bien entraîné à côté de Hermes. /habits pour en faire un réflexe.",
        "news_learned_auto": "Habits : {name} agit maintenant seul, comme ton réglage learning: auto le permet. /habits pour le voir ou le mettre au repos.",
        "news_proposal": "Habits : une proposition sur {name} attend dans /habits.",
        "ask_dream": "Réponds avec /habits oui {name} ou /habits non {name}.",
        "ask_dream_try": "Réponds avec /habits essai {name} ou /habits non {name}.",
        "ask_dream_again": "Voilà ce que couvre ton oui. Redis /habits oui {name} pour le donner, ou /habits non {name}.",
        "card_unseen": "{name} a été enregistrée de nouveau : tu n'as pas dit oui à cette version. Elle lancerait : "
                       "{steps}. Son dossier : {folder}. La laisser répondre seule ?",
        "ask_unseen": "Redis /habits oui {name} pour laisser cette version répondre seule, ou /habits repos {name}.",
        "line_unseen": "❓ {name} · enregistrée de nouveau · attend ton oui",
        "line_byname_wait": "◇ {name} · appelé par son nom · attend ton oui : /habits oui {name}",
        "card_copied": "{name} est un réflexe que tu as ajouté toi-même. Hermes ne peut l'appeler par son nom qu'après "
                       "ton oui. Il lancerait : {steps}. Son dossier : {folder}. Laisser Hermes l'appeler ?",
        "ask_copied": "Redis /habits oui {name} pour laisser Hermes l'appeler, ou /habits repos {name}.",
        "copied_already": "◇ {name} peut déjà être appelé par Hermes. Pas ça ? /habits faux",
        "after_yes_copied": "✅ {name} peut être appelé par Hermes maintenant, et il ne lance que les étapes que tu as "
                            "vues. Pas ça ? /habits faux",
        "line_held": "┄ {name} · en attente · /habits oui {name} la laisse agir",
        "line_held_chat": "┄ {name} · en attente · ton oui dans un chat la laisse agir : `/habits oui {name}`",
        "after_try_held": "┄ {name} n'a rien sur quoi s'entraîner : elle n'agit seule qu'après ton oui. /habits oui {name}",
        "after_rest_held": "─ {name} est au repos. La relancer : /habits oui {name}",
        "practising_rule": "Un réflexe qui s'entraîne mais dont l'action ne revient pas pendant {days} jours passe au "
                           "repos et rend sa place.",
        "waits_chat": "Dans un chat avec Hermes, tape : `/habits oui {name}` ou `/habits non {name}`.",
        "waits_chat_try": "Dans un chat avec Hermes, tape : `/habits essai {name}` ou `/habits non {name}`.",
        "draft_invalid": "◇ {name} ne peut pas devenir un réflexe tel quel : {why}",
    },
}


def language(value) -> str:
    """The language a setting names, en for anything else."""
    value = str(value or "").strip().lower()
    return value if value in LANGUAGES else "en"


PLURAL = re.compile(r"\{(\w+):([^{}|]*)\|([^{}]*)\}")


def plural(text: str, lang: str, values: dict) -> str:
    """{days:# day|# days}: the first form when the field is 1 (0 too in French), the second otherwise, # standing for
    the number. A format spec has no | so an ordinary {x:.1f} is left to format()."""
    def pick(found):
        n = values.get(found[1])
        if not isinstance(n, int) or isinstance(n, bool):
            return found[3].replace("#", str(n))
        one = n == 1 or (n == 0 and lang == "fr")
        return (found[2] if one else found[3]).replace("#", str(n))
    return PLURAL.sub(pick, text)


def say(key: str, lang: str = "en", **values) -> str:
    """One text, filled in. A key the language lacks falls back on its English twin."""
    text = TEXTS.get(lang, TEXTS["en"]).get(key) or TEXTS["en"][key]
    return plural(text, lang, values).format(**values)


def period(every, lang: str = "en") -> str:
    """A schedule's "1d" or "5m" as words: every day, every 5 min (tous les jours, toutes les 5 min); a value that is
    no number and unit stays as written."""
    found = re.fullmatch(r"\s*(\d+)\s*([smhdw])\s*", str(every).lower())
    if not found or int(found[1]) == 0:
        return str(every)
    n, unit = int(found[1]), found[2]
    if lang == "fr":
        word, fem = {"s": ("s", False), "m": ("min", True), "h": ("heure", True), "d": ("jour", False),
                     "w": ("semaine", True)}[unit]
        if n == 1:
            return {"s": "toutes les secondes", "m": "toutes les minutes", "h": "toutes les heures",
                    "d": "tous les jours", "w": "toutes les semaines"}[unit]
        plural_word = word + ("s" if unit in "hdw" else "")
        return f"{'toutes' if fem else 'tous'} les {n} {plural_word}"
    if n == 1:
        return {"s": "every second", "m": "every minute", "h": "every hour", "d": "every day", "w": "every week"}[unit]
    word = {"s": "s", "m": "min", "h": "hours", "d": "days", "w": "weeks"}[unit]
    return f"every {n} {word}"


def seconds(ms, lang: str = "en") -> str:
    """1.8 s (1,8 s in French); 19 s past 10 s."""
    value = (ms or 0) / 1000
    text = f"{value:.0f}" if value >= 10 else f"{value:.1f}"
    return (text.replace(".", ",") if lang == "fr" else text) + " s"


def calls(n: int, lang: str = "en", jev: bool = False) -> str:
    """0 model calls (+ Jev): what a direct answer cost, said as it was, with the decision model that answered."""
    names = "Jev" if jev else ""
    return say("no_calls" if not n else "call" if n == 1 else "calls", lang, n=n) + (
        say("decided", lang, names=names) if names else "")


# The ledger writes its notes in English, for the logs: told to the user, they are said again in the language.
WHY = (
    (re.compile(r"^(\d+) errors in a row$"), "why_errors", lambda m: {"n": m[1]}),
    (re.compile(r"^(\d+)% over the last (\d+)$"), "why_agreement",
     lambda m: {"right": round(int(m[1]) * int(m[2]) / 100), "n": m[2]}),
    (re.compile(r"^(\d+) spot checks in a row under (\d+)%$"), "why_spots", lambda m: {"n": m[1], "percent": m[2]}),
    (re.compile(r"^woke the model (\d+) times in a row$"), "why_woke", lambda m: {"n": m[1]}),
    (re.compile(r"^wrong, said the user$"), "why_wrong", lambda m: {}),
)


def why(note: str, lang: str = "en") -> str:
    """A ledger note in the user's words; one it does not know stays as it is."""
    note = str(note or "").strip()
    for pattern, key, fields in WHY:
        found = pattern.match(note)
        if found:
            return say(key, lang, **fields(found))
    return note


def count(key: str, n: int, lang: str = "en") -> str:
    """A number and its word: 1 automatique, 2 apprennent (0 takes the singular in French)."""
    word = say(f"{key}_one" if n == 1 or (n == 0 and lang == "fr") else f"{key}_many", lang, count=n)
    return word if str(n) in word else f"{n} {word}"


def percent(share: float, lang: str = "en") -> str:
    """100% (100 % in French)."""
    return f"{share * 100:.0f}" + (" %" if lang == "fr" else "%")
