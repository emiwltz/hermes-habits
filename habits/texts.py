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
        "none": '🔁 No habits of your own yet. After a task you repeat, tell me "make it a habit".',
        "none_shipped": "Built in, ready now: {names}.",
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
        "later": "Keep practising",
        "rest": "Put it to rest",
        "ask_ready": "❓ {name}: /habits yes {name} · /habits try {name} · /habits rest {name}",
        # what each answer does
        "after_yes": "✅ {name} answers on its own now. I check behind it 1 time in {every}. Not right? /habits wrong",
        "after_yes_unchecked": "✅ {name} answers on its own now. Not right? /habits wrong",
        "after_yes_close": "✅ {name} answers on its own now. I check behind it its next {n} runs, then 1 time in "
                           "{every}. Not right? /habits wrong",
        "after_yes_close_one": "✅ {name} answers on its own now. I check behind it its next run, then 1 time in "
                               "{every}. Not right? /habits wrong",
        "undo_close": " I check behind it its next {n} runs.", "undo_close_one": " I check behind it its next run.",
        "changed": "🤔 {name} changed while you were deciding: it learns its new version first.",
        "after_later": "┄ OK, it keeps learning. I'll ask you again in {runs} runs.",
        "after_later_one": "┄ OK, it keeps learning. I'll ask you again in 1 run.",
        "after_rest": "─ {name} is resting. To bring it back: /habits try {name}",
        "after_try": "┄ {name} is on trial: it rehearses beside me without acting.",
        "learning_already": "┄ {name} is learning already ({compared}/{runs}).",
        "active_already": "━ {name} answers on its own already. Not right? /habits wrong",
        "resting": "─ {name} is resting. To bring it back: /habits try {name}",
        "byname": "◇ {name} is called by name: there is nothing to decide.",
        "example_byname": "◇ \"{name}\" is a built-in example: it is called by name and never acts on its own. "
                          "hermes habits run {name} tries it.",
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
                        "The model has a terminal too, so hermes habits promote cannot.",
        "error": "habits: {kind}: {exc}",
        "menu": "Your habits and their decisions: /habits, /habits <name>, /habits view, /habits help",
        # /habits <name>: one habit in detail
        "trigger_name": "called by name", "trigger_request": "on request", "trigger_every": "every {every}",
        "trigger_at": " at {at}", "info": "{trigger} · version {version}", "example": " · example",
        "detail_gain": "{fast} instead of {slow}", "detail_tokens": "{active} tokens instead of {shadow}",
        "detail_checks": "{checks} · {wakes}", "detail_invalid": "invalid:",
        "checks_one": "check", "checks_many": "checks", "wakes_one": "wake", "wakes_many": "wakes",
        "run_shadow": "dry", "run_active": "on its own", "run_spot": "spot check", "run_adhoc": "called by name",
        "status_end": "done", "status_wake": "handed back",
        "run_turn": " · turn {took}", "run_matched": " · {matched}/{planned} right", "run_judge": " · judge: {why}",
        "page": "page: {path}", "file": "file: {path}", "steps": "steps:", "page_chat": "diagram: /habits view {name}",
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
                "/habits try <name> · keep it practising, or bring it back\n"
                "/habits rest <name> · put it to rest\n"
                "/habits wrong · the last habit that answered here got it wrong\n"
                "/habits undo · undo your last change (24 h)\n"
                "/habits pause · /habits resume · no habit starts from a message or a schedule, then start again\n"
                "/habits view [name] · the gallery, or one habit's diagram\n"
                "In a terminal: hermes habits list, show, view, check, run, promote, pause, resume, undo, "
                "retire, reset, cleanup",
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
        "learning_one": "apprend", "learning_many": "apprennent",
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
        "none": "🔁 Pas encore d'habitude à toi. Après une tâche que tu répètes, dis-moi « fais-en une habitude ».",
        "none_shipped": "Fournis, prêts tout de suite : {names}.",
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
        "after_yes_close": "✅ {name} répond seule maintenant. Je vérifie derrière elle ses {n} prochaines fois, puis 1 "
                           "fois sur {every}. Pas ça ? /habits faux",
        "after_yes_close_one": "✅ {name} répond seule maintenant. Je vérifie derrière elle sa prochaine fois, puis 1 "
                               "fois sur {every}. Pas ça ? /habits faux",
        "undo_close": " Je vérifie derrière elle ses {n} prochaines fois.",
        "undo_close_one": " Je vérifie derrière elle sa prochaine fois.",
        "changed": "🤔 {name} a changé pendant que tu décidais : elle réapprend sa nouvelle version d'abord.",
        "after_later": "┄ D'accord, elle continue d'apprendre. Je te redemande dans {runs} passages.",
        "after_later_one": "┄ D'accord, elle continue d'apprendre. Je te redemande dans 1 passage.",
        "after_rest": "─ {name} est au repos. La relancer : /habits essai {name}",
        "after_try": "┄ {name} est à l'essai : elle s'entraîne à blanc à côté de moi.",
        "learning_already": "┄ {name} apprend déjà ({compared}/{runs}).",
        "active_already": "━ {name} répond déjà seule. Pas ça ? /habits faux",
        "resting": "─ {name} est au repos. La relancer : /habits essai {name}",
        "byname": "◇ {name} se lance par son nom : rien à décider.",
        "example_byname": "◇ « {name} » est un exemple fourni : il se lance par son nom et n'agit jamais seul. "
                          "hermes habits run {name} l'essaie.",
        "invalid": "◇ {name} est invalide : /habits {name} dit pourquoi.",
        "after_wrong": "↩︎ Noté : {name} repart en apprentissage et n'agit plus seule. Redemande-moi, je le fais moi-même.",
        "wrong_none": "Aucune habitude n'a répondu seule dans cette conversation.",
        "after_undo": "↩︎ Annulé : {name} redevient « {state} », comme avant {change} de {time}.",
        "undo_none": "Rien à annuler depuis 24 h.",
        "paused": "⏸ Habitudes en pause : aucune ne part d'un message ou d'un horaire, les veilles se taisent. "
                  "/habits reprends",
        "resumed": "▶︎ C'est reparti : {active}, {learning}.",
        "which": "Laquelle ? /habits {verb} <nom>",
        "nothing_waits": "Rien n'attend ta décision. /habits",
        "unknown": "Pas d'habitude nommée « {name} ». /habits les liste.",
        "unknown_cli": "Pas d'habitude nommée « {name} ». hermes habits list les liste.",
        "promote_made": "🤔 {name} vient de Hermes : seul ton oui dans un chat la fait agir (`/habits oui {name}`). "
                        "Le modèle a un terminal lui aussi, donc hermes habits promote ne le peut pas.",
        "error": "habits : erreur {kind} : {exc}",
        "menu": "Tes habitudes et leurs décisions : /habits, /habits <nom>, /habits view, /habits help",
        "trigger_name": "par son nom", "trigger_request": "sur demande", "trigger_every": "toutes les {every}",
        "trigger_at": " à {at}", "info": "{trigger} · version {version}", "example": " · exemple",
        "detail_gain": "{fast} au lieu de {slow}", "detail_tokens": "{active} jetons au lieu de {shadow}",
        "detail_checks": "{checks} · {wakes}", "detail_invalid": "invalide :",
        "checks_one": "vérification", "checks_many": "vérifications", "wakes_one": "réveil", "wakes_many": "réveils",
        "run_shadow": "à blanc", "run_active": "seule", "run_spot": "contrôle", "run_adhoc": "par son nom",
        "status_end": "finie", "status_wake": "rendue",
        "run_turn": " · tour {took}", "run_matched": " · {matched}/{planned} justes", "run_judge": " · juge : {why}",
        "page": "page : {path}", "file": "fichier : {path}", "steps": "étapes :", "page_chat": "schéma : /habits view {name}",
        "view_gallery": "La galerie de tes habitudes", "view_one": "Le schéma de {name}",
        "view_failed": "Impossible d'écrire la page de {name}.",
        "view_open": "{said} : ouvre le fichier dans un navigateur pour la vue interactive.", "view_at": "{said} : {uri}",
        "state_active": "automatique",
        "state_shadow": "apprend",
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
                "/habits view [nom] · la galerie, ou le schéma d'une habitude\n"
                "Dans un terminal : hermes habits list, show, view, check, run, promote, pause, resume, undo, "
                "retire, reset, cleanup",
    },
}


def language(value) -> str:
    """The language a setting names, en for anything else."""
    value = str(value or "").strip().lower()
    return value if value in LANGUAGES else "en"


def say(key: str, lang: str = "en", **values) -> str:
    """One text, filled in. A key the language lacks falls back on its English twin."""
    text = TEXTS.get(lang, TEXTS["en"]).get(key) or TEXTS["en"][key]
    return text.format(**values)


def seconds(ms, lang: str = "en") -> str:
    """1.8 s (1,8 s in French); 19 s past 10 s."""
    value = (ms or 0) / 1000
    text = f"{value:.0f}" if value >= 10 else f"{value:.1f}"
    return (text.replace(".", ",") if lang == "fr" else text) + " s"


def calls(n: int, lang: str = "en", jev: bool = False, clef: bool = False) -> str:
    """0 model calls (+ Jev): what a direct answer cost, said as it was, with the decision model that answered."""
    names = ", ".join(name for name, answered in (("Jev", jev), ("Clef", clef)) if answered)
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
