"""What a person sees: the terminal's words, the pages' words, in the vocabulary of the README (learning,
automatic, resting, called by name, done, handed back, decision model)."""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from habits import catalog, deciders, guard, runner, texts, tools
from test_graph import small
from test_plugin import cli_run, setup, with_habit  # noqa: F401 (setup: a fixture)

ROOT = Path(__file__).resolve().parents[1]
INTERNAL = re.compile(r"\b(shadow|S1|one-off|adhoc|oui|wakeAgent)\b")


def cli(habits, verb, **args):
    return habits.cli_handle(SimpleNamespace(habits_command=verb, **args))


def web_habit(tmp_path):
    path = tmp_path / "news" / "reflex.yml"
    path.parent.mkdir()
    path.write_text("spec: habits/0.1\nstart: read\nfallback: help\nnodes:\n"
                    "  read: {do: web_extract, args: {urls: ['https://example.com/news']}, as: page, next: done}\n"
                    "  done: {end: 'Read it.'}\n  help: {wake: Help.}\n")
    return path


def test_a_run_that_read_the_web_prints_plainly_in_a_terminal(setup, monkeypatch, tmp_path, capsys):
    ctx, habits = setup
    original = ctx.dispatch_tool
    monkeypatch.setattr(ctx, "dispatch_tool", lambda name, args: json.dumps({"results": [{"content": "x"}]})
                        if name == "web_extract" else original(name, args))
    path = web_habit(tmp_path)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    code, out, err = cli_run(habits, capsys, str(path), as_json=False)
    assert code == 0 and "untrusted_tool_result" not in out and out.startswith("Read it.")
    assert "note: this run read the web; the text below came from it" in err
    code, out, _ = cli_run(habits, capsys, str(path))  # --json: wrapped, a terminal or not
    assert "<untrusted_tool_result" in out["untrusted"]


def test_no_hermes_model_is_said_once_with_what_to_do():
    class AuxiliaryClientUnavailable(Exception):
        pass

    def complete(*_, **__):
        raise AuxiliaryClientUnavailable("no auxiliary client")
    chain = deciders.Chain([deciders.LLMDecider(complete)])
    try:
        chain.ask("state", [deciders.Question("Is it?", "yes_no", ["yes", "no"])])
    except deciders.DecisionError as exc:
        said = str(exc)
    assert "llm: llm:" not in said and "AuxiliaryClientUnavailable" not in said
    assert "no Hermes model is set up" in said and "hermes model" in said
    assert "TYPESAFE_API_KEY" in said

    def broken(*_, **__):
        raise TimeoutError()
    try:
        deciders.Chain([deciders.LLMDecider(broken)]).ask("s", [deciders.Question("Is it?", "yes_no", ["yes", "no"])])
    except deciders.DecisionError as exc:
        assert str(exc) == "no decision model answered (llm: the Hermes model did not answer (TimeoutError): check `hermes model`)"


def test_a_failed_run_says_the_decision_model_error_without_its_class(setup, monkeypatch, capsys):
    ctx, habits = setup

    def run(*_, **__):
        raise deciders.DecisionError("no decision model answered (llm: no Hermes model is set up)")
    monkeypatch.setattr(habits, "run", run)
    code, out, _ = cli_run(habits, capsys, "sort-downloads-by-type", as_json=False)
    assert code == 1 and out.strip() == "habits: no decision model answered (llm: no Hermes model is set up)"
    code, out, _ = cli_run(habits, capsys, "sort-downloads-by-type")
    assert out["message"] == "DecisionError: no decision model answered (llm: no Hermes model is set up)"  # as before


def test_the_terminal_says_the_states_in_the_readme_words(setup, monkeypatch, tmp_path, capsys):
    ctx, habits = setup
    cli(habits, "reset", name="sort-downloads-by-type")
    assert capsys.readouterr().out.splitlines()[-1] == "sort-downloads-by-type: learning"
    cli(habits, "retire", name="sort-downloads-by-type")
    assert capsys.readouterr().out.splitlines()[-1] == "sort-downloads-by-type: resting"
    with_habit(tmp_path, monkeypatch, "point-matin", yaml.safe_dump(small()))
    cli(habits, "reset", name="point-matin")
    assert capsys.readouterr().out.splitlines()[-1] == "point-matin: called by name"
    assert "◇ point-matin · called by name" in habits.listing()


def test_the_terminal_help_has_no_internal_or_french_word(setup):
    ctx, habits = setup
    parser = argparse.ArgumentParser(prog="hermes habits")
    habits.cli_setup(parser)
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = parser.format_help() + "".join(p.format_help() for p in sub.choices.values())
    assert not INTERNAL.search(helps), INTERNAL.search(helps)
    assert "/habits yes" in helps and "no run" not in helps
    assert "oui" not in habits.command.__doc__


def test_an_example_is_said_to_be_one_not_unknown(setup, capsys):
    ctx, habits = setup
    moved, said = habits.yes("hermes-health", by="cli")
    assert not moved and "built-in example" in said and "No habit named" not in said
    assert "built-in example" in habits.rest("hermes-health")
    for verb in ("retire", "reset", "promote"):
        assert cli(habits, verb, name="hermes-health") == 1
        assert "built-in example" in capsys.readouterr().out
    assert habits.ledger.history("hermes-health") == []
    shown = habits.command("hermes-health").splitlines()[0]
    assert shown == "◇ hermes-health · called by name · example" and "/12" not in shown


def test_a_dry_run_and_a_hand_back_say_so_before_the_habits_words(setup, tmp_path, capsys):
    ctx, habits = setup
    code, text, _ = cli_run(habits, capsys, "sort-downloads-by-type", as_json=False)
    assert text.splitlines()[0] == "Dry run (nothing written): Downloads sorted."
    path = tmp_path / "few" / "reflex.yml"
    path.parent.mkdir()
    path.write_text("spec: habits/0.1\ninputs: [most]\nstart: many\nfallback: help\nnodes:\n"
                    "  many: {check: {greater: [most, 2]}, then: {yes: done, no: help}}\n"
                    "  done: {end: 'Many.'}\n  help: {wake: Too few to go on.}\n")
    code, text, _ = cli_run(habits, capsys, str(path), "most=1", as_json=False)
    lines = text.splitlines()
    assert lines[0].startswith("Handed back to Hermes") and lines[1] == "Too few to go on."


def test_drafts_in_one_folder_each_have_their_own_name_and_page(setup, tmp_path, capsys):
    ctx, habits = setup
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    for name in ("good", "other"):
        (drafts / f"{name}.yaml").write_text(yaml.safe_dump(small()))
    _, one, _ = cli_run(habits, capsys, str(drafts / "good.yaml"), as_json=False)
    _, two, _ = cli_run(habits, capsys, str(drafts / "other.yaml"), as_json=False)
    assert one.splitlines()[1].startswith("good · ") and two.splitlines()[1].startswith("other · ")
    assert one.rstrip().endswith("_run-good.html") and two.rstrip().endswith("_run-other.html")
    scratch = tmp_path / "tidy" / "graph.yaml"  # the skill's draft name: its folder names it
    scratch.parent.mkdir()
    scratch.write_text(yaml.safe_dump(small()))
    assert cli_run(habits, capsys, str(scratch), as_json=False)[1].splitlines()[1].startswith("tidy · ")


def test_the_list_shows_a_pause_even_with_no_habit_of_ones_own(setup, monkeypatch, capsys):
    ctx, habits = setup
    habits.pause(True)
    assert "⏸" in habits.listing()
    monkeypatch.setattr(catalog, "skills", lambda: [])
    assert "⏸" in habits.listing() and "hermes habits resume" in habits.listing()


def test_show_dates_its_runs_the_iso_way(setup):
    ctx, habits = setup
    habits.tool_run({"name": "sort-downloads-by-type"})
    run = habits.command("sort-downloads-by-type").splitlines()[3]
    assert run.startswith(time.strftime("  %Y-%m-%d ")), run


def test_a_dry_runs_steps_say_dry_run(setup, capsys):
    ctx, habits = setup
    reply = json.loads(habits.tool_run({"scenario": yaml.safe_dump(small(look={
        "do": "terminal", "args": {"command": "touch /tmp/x"}, "next": "done"})), "dry": True}))
    assert "planned (dry run)" in reply["report"] and "shadow" not in reply["report"] and "S1" not in reply["report"]
    code, text, _ = cli_run(habits, capsys, "sort-downloads-by-type", as_json=False)
    assert "planned (dry run)" in text and "shadow" not in text


def test_the_guard_points_to_the_english_verbs():
    assert "/habits (yes, undo, resume)" in guard.OWN and "/habits rest {name}" in guard.MERGE
    for text in (guard.OWN, guard.MERGE, guard.HAND_EDIT):
        assert not INTERNAL.search(text), text


def test_the_english_texts_say_called_by_name():
    english = re.sub(r"\{\w+\}", "", " ".join(texts.TEXTS["en"].values()))  # placeholders are no words
    assert "run by name" not in english and "called by name" in english
    assert not INTERNAL.search(english)


def test_the_pages_speak_the_readme_words():
    for page in ("template.html", "gallery.html"):
        text = (ROOT / "view" / page).read_text(encoding="utf-8")
        for old in ("Shadow run", "S1 question", "in shadow", "run active", "Woke the LLM", "Planned in shadow",
                    "Fake S1", "'S1'", "run by name", "One-off scenario"):
            assert old not in text, (page, old)
        assert "Learning" in text and "Automatic" in text and "Resting" in text and "Called by name" in text


def test_any_model_failure_is_said_as_a_sentence_with_what_to_check():
    # Seen in the audit: an auto-detected free model that failed printed "(llm: NotFoundError)", a class name only.
    class NotFoundError(Exception):
        pass

    def gone(*_, **__):
        raise NotFoundError("model not found")

    def garbled(*_, **__):
        return {"answers": "no"}
    said = []
    for complete in (gone, garbled):
        try:
            deciders.Chain([deciders.LLMDecider(complete)]).ask("s", [deciders.Question("Is it?", "yes_no", ["yes", "no"])])
        except deciders.DecisionError as exc:
            said.append(str(exc))
    assert said[0] == ("no decision model answered (llm: the Hermes model did not answer (NotFoundError): "
                       "check `hermes model`)")
    assert said[1].startswith("no decision model answered (llm: the Hermes model's answer could not be read (")


def test_the_summary_line_says_done(setup, capsys):
    ctx, habits = setup
    code, text, _ = cli_run(habits, capsys, "sort-downloads-by-type", as_json=False)
    assert code == 0 and text.splitlines()[1].startswith("sort-downloads-by-type · done in ")
    assert " · ended in " not in text


def test_the_help_says_a_pause_leaves_a_call_by_name():
    for lang in ("en", "fr"):
        line = next(x for x in texts.TEXTS[lang]["help"].splitlines() if x.startswith("/habits pause"))
        assert "stop every habit" not in line and "tout arrêter" not in line
    assert "from a message or a schedule" in texts.TEXTS["en"]["help"]


def test_the_terminal_says_scheduled_not_watch(setup):
    ctx, habits = setup
    parser = argparse.ArgumentParser(prog="hermes habits")
    habits.cli_setup(parser)
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = parser.format_help() + "".join(p.format_help() for p in sub.choices.values())
    assert "every watch" not in helps and "(pick, confirm)" not in helps
    assert habits.tick("nothing-here")[0] == "habits: no valid scheduled habit named 'nothing-here'"


def test_the_first_example_speaks_the_readme_words():
    text = (ROOT / "examples" / "tidy-downloads" / "assets" / "reflex.yml").read_text(encoding="utf-8")
    assert not re.search(r"\b(shadow|S1)\b", text)


def test_a_card_and_a_page_never_say_one_off():
    from habits import view
    for d in ({"name": "check-links", "kind": "one-off", "status": "live", "origin": "skill", "summary": {"runs": 2}},
              {"name": "hermes-health", "kind": "request", "status": "shadow", "origin": "example", "summary": {}}):
        plain = view.static(d) + view.gallery([d]).split("<script>", 1)[0]
        assert "one-off" not in plain and "learning" not in plain, plain
    assert "example · called" in view.static({"name": "hermes-health", "kind": "request", "origin": "example"})
    gallery = (ROOT / "view" / "gallery.html").read_text(encoding="utf-8")
    assert "cap(d.kind" not in gallery and "Watch checks" not in gallery
    template = (ROOT / "view" / "template.html").read_text(encoding="utf-8")
    assert "watch: 'Watch'" not in template and "D.origin === 'example'" in template


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_a_page_says_run_once_only_for_a_graph_drawn_for_one_run():
    template = (ROOT / "view" / "template.html").read_text(encoding="utf-8")
    trigger = re.search(r"^const trigger = .*$", template, flags=re.M).group(0)
    shown = []
    for origin in ("one-off", "skill", "example"):
        script = (f"const D = {{origin: {json.dumps(origin)}, kind: 'one-off'}}, oneoff = true, "
                  f"once = D.origin === 'one-off';\n{trigger}\nconsole.log(trigger(undefined))")
        shown.append(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout.strip())
    assert shown == ["run once", "called", "called"]


def test_a_replay_keeps_the_habits_origin(setup, capsys):
    ctx, habits = setup
    code, text, _ = cli_run(habits, capsys, "hermes-health", as_json=False)
    page = Path(text.rstrip().splitlines()[-1].removeprefix("replay: ")).read_text(encoding="utf-8")
    assert '"origin": "example"' in page
