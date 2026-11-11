"""What a stranger reads in a terminal (the last pass, findings 34, 50, 51, 52, 53, 55, 56): every line is true, names
a command that exists, counts in the singular when it is one, and speaks the user's language."""

import re
import string
from types import SimpleNamespace

import pytest

from habits import display, texts
from test_plugin import cli_run, setup  # noqa: F401 (setup: a fixture)

OLD_WORDS = re.compile(r"\b(graph|node|promote|retire|shadow|S1|door|wakes|wake node)\b", re.I)


def fields(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def stub(key, lang):
    """A text filled with made-up values: numbers where a count is likely, a name elsewhere."""
    return {f: (2 if f in ("count", "n", "turns", "days", "sessions", "quiet", "matches", "needed", "compared",
                           "agreed", "asked", "calls") else "zz-name") for f in fields(texts.TEXTS[lang][key])}


# ---- 34: a hint never names a command that does not exist -------------------------------------------------------------

@pytest.mark.parametrize("lang", texts.LANGUAGES)
def test_no_text_turns_into_a_show_that_has_no_name_to_show(lang):
    for key in texts.TEXTS[lang]:
        if key.startswith(("news_", "ask_dream")):  # lines of a chat or of the model, never printed by terminal()
            continue
        said = display.terminal(texts.say(key, lang, **stub(key, lang)))
        for word in re.findall(r"hermes habits show (\S+)", said):
            assert word.strip("<>.,:;") in ("name", "nom", "zz-name"), (key, lang, said)


@pytest.mark.parametrize("lang", texts.LANGUAGES)
def test_the_dream_ends_on_a_command_a_terminal_has(lang):
    said = display.terminal(texts.say("dream_next", lang) + texts.say("dream_busy", lang))
    assert "hermes habits show" not in said and "hermes habits list" in said


def test_the_yes_and_the_rest_a_terminal_is_told_are_its_own_commands():
    assert display.terminal("/habits yes x · /habits rest x") == "hermes habits yes x · hermes habits rest x"
    assert display.terminal("/habits oui x · /habits repos x") == "hermes habits yes x · hermes habits rest x"


# ---- 55 and 53: counts, and French that says what English says -------------------------------------------------------

def test_a_count_of_one_is_singular_in_both_languages():
    one = dict(turns=1, sessions=1, days=1, took="0.1 s")
    assert texts.say("dream_read", "en", **one) == "Read 1 turn in 1 session over 1 day (0.1 s)."
    assert texts.say("dream_read", "fr", **one) == "Lu 1 tour dans 1 session, étalés sur 1 jour (0.1 s)."
    assert texts.say("spotted_line", "fr", action="ls", turns=2, days=1, moments="") == "- `ls` · 2 tours sur 1 jour"
    assert texts.say("dream_empty", "en", days=1).startswith("Nothing to learn from yet: Hermes's history holds no turn from the last day.")
    assert "du dernier jour" in texts.say("dream_empty", "fr", days=1)
    assert "des 3 derniers jours" in texts.say("dream_empty", "fr", days=3)


def test_the_french_read_line_is_the_span_not_a_window_ending_today():
    assert "derniers" not in texts.say("dream_read", "fr", turns=9, sessions=9, days=3, took="1 s")


def test_a_young_history_ends_on_no_dangling_closest_and_the_empty_one_has_a_next_step(setup):
    ctx, habits = setup
    said = habits.dream_text({"status": "ok", "turns": 3, "sessions": 2, "days": 1, "took_ms": 100, "spotted": [],
                              "near": [], "left": 0})
    assert "1 day" in said and "1 days" not in said and "Closest" not in said and "closest" not in said
    empty = habits.dream_text({"status": "empty"}, 60)
    assert "hermes habits dream" in empty and "hermes habits run hermes-health" in empty


def test_learning_off_names_the_command_that_turns_it_on():
    for lang in texts.LANGUAGES:
        said = texts.say("dream_off", lang)
        assert "hermes config set plugins.entries.habits.settings.learning" in said


def test_the_period_of_a_schedule_is_said_in_words():
    assert texts.period("1d") == "every day" and texts.period("5m") == "every 5 min" and texts.period("2h") == "every 2 hours"
    assert texts.period("1d", "fr") == "tous les jours" and texts.period("5m", "fr") == "toutes les 5 min"
    assert texts.period("2h", "fr") == "toutes les 2 heures" and texts.period("soon", "fr") == "soon"


# ---- 50 and 52: show says only what is true ----------------------------------------------------------------------------

def test_show_names_a_page_only_when_the_file_exists(setup, capsys):
    ctx, habits = setup
    name = "sort-downloads-by-type"
    assert f"diagram: hermes habits view {name}" in habits.detail(name)
    assert "page:" not in habits.detail(name)
    path, _ = habits.view(name)
    assert path.exists() and f"page: {path}" in habits.detail(name)


def test_an_example_is_called_by_name_on_every_line_of_its_show(setup):
    ctx, habits = setup
    shown = habits.detail("hacker-news-digest")
    assert shown.count("called by name") == 2 and "every 1d" not in shown and "on request" not in shown


def test_the_terminal_help_uses_the_words_of_the_contract():
    import argparse
    parser = argparse.ArgumentParser()
    shown = []

    def collect(p):
        for action in p._actions:
            if isinstance(action, argparse._SubParsersAction):
                shown.extend(a.help or "" for a in action._choices_actions)
                for sub in action.choices.values():
                    collect(sub)
            else:
                shown.append(action.help or "")
    display.Display.cli_setup(None, parser)
    collect(parser)
    text = " ".join(shown)
    assert not re.search(r"\b(promote|retire|trigger|request habit|one-off|wake)\b", text, re.I), text
    for verb in ("yes", "rest"):
        assert verb in parser.format_help()


# ---- 51: check and run speak the words of the contract -------------------------------------------------------------

def test_check_says_reflex_and_step_not_graph_and_node(setup, tmp_path, capsys):
    ctx, habits = setup
    good = tmp_path / "ok.yml"
    good.write_text("spec: habits/0.1\nstart: a\nfallback: h\nnodes:\n  a: {end: Done.}\n  h: {wake: Help.}\n")
    assert habits.cli_handle(SimpleNamespace(habits_command="check", file=str(good))) == 0
    assert capsys.readouterr().out.strip() == "valid: the reflex follows every rule."
    bad = tmp_path / "bad.yml"
    bad.write_text("spec: habits/0.1\nstart: zz\nnodes:\n  'A B': {end: Done.}\n")
    assert habits.cli_handle(SimpleNamespace(habits_command="check", file=str(bad))) == 1
    out = capsys.readouterr().out
    assert out and not OLD_WORDS.search(out), out
    notfile = tmp_path / "x.txt"
    notfile.write_text("hi")
    habits.cli_handle(SimpleNamespace(habits_command="check", file=str(notfile)))
    assert not OLD_WORDS.search(capsys.readouterr().out)
    habits.cli_handle(SimpleNamespace(habits_command="run", target=str(notfile), input=[], live=False, json=False,
                                      within=None, message=None, memory=None))
    assert not OLD_WORDS.search(capsys.readouterr().out)


# ---- 56: the French terminal ---------------------------------------------------------------------------------------------

def test_run_speaks_french_when_the_language_is_french(setup, capsys):
    ctx, habits = setup
    ctx.config["language"] = "fr"
    code, out, _ = cli_run(habits, capsys, "sort-downloads-by-type", as_json=False)
    assert "en " in out and "question" in out and "étapes :" in out and "steps:" not in out and "replay:" not in out
    assert "done in" not in out and "tool call" not in out and "rejeu :" in out
    code, out, _ = cli_run(habits, capsys, "nope", as_json=False)
    assert code == 1 and out.startswith("refusé : pas d'habitude nommée « nope »")
    ctx.config["language"] = "en"
    code, out, _ = cli_run(habits, capsys, "nope", as_json=False)
    assert out.startswith('refused: no habit named "nope"')
    code, out, _ = cli_run(habits, capsys, "nope")  # --json stays English whatever the language
    assert out == {"status": "refused", "message": 'no habit named "nope"; known habits: sort-downloads-by-type'} or out["status"] == "refused"


def test_the_french_chat_commands_are_french_words(setup):
    ctx, habits = setup
    ctx.config["language"] = "fr"
    assert habits.command("aide").startswith("/habits · tes habitudes")
    assert "habits voir" in texts.say("help", "fr") and "/habits view" not in texts.say("help", "fr")
    assert "habits aide" in texts.say("menu", "fr")
    said = habits.command("voir")
    assert "galerie" in said.lower()


def test_show_of_a_draft_the_dream_listed_says_it_is_a_draft_not_that_it_does_not_exist(setup, capsys):
    ctx, habits = setup
    habits.ledger.put_draft("terminal:df -h ~", name="df", tool="terminal", args={"command": "df -h ~"}, raw="{}")
    habits.cli_handle(SimpleNamespace(habits_command="show", name="df", json=False))
    out = capsys.readouterr().out
    assert "df · a draft, not a habit yet" in out and "No habit named" not in out
    # this draft only waits: the line must not say that it practices already
    assert "practices beside Hermes" not in out and "waits for" in out
    habits.cli_handle(SimpleNamespace(habits_command="show", name="nope", json=False))
    assert capsys.readouterr().out.startswith('No habit named "nope"')


def test_the_example_card_says_how_to_try_it_in_words_that_work_in_both_languages():
    for lang, start in (("en", "To try it: hermes habits run x"), ("fr", "Pour l'essayer : hermes habits run x")):
        assert texts.say("example_byname", lang, name="x").endswith(start)


def test_a_refused_file_lists_its_rules_without_a_dot_and_a_semicolon_in_a_row(setup, tmp_path, capsys):
    ctx, habits = setup
    bad = tmp_path / "bad.yml"
    bad.write_text("spec: habits/0.1\nstart: a\nnodes:\n  a: {do: terminal, args: {command: ls}, next: zz}\n  b: {end: x}\n")
    code, out, _ = cli_run(habits, capsys, str(bad), as_json=False)
    assert code == 1 and out.startswith("refused: invalid reflex: rule ") and ".;" not in out


def test_an_unread_history_gives_a_remedy_that_is_true_for_a_missing_store_too():
    for lang in texts.LANGUAGES:
        assert "state.db" in texts.say("dream_unread", lang, why="no history store")
