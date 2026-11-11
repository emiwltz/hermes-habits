"""The words of the contract (section 2) in what the user reads: the new texts of the last pass, and the old ones.

Forbidden words are matched as stems (``promot`` also catches ``promote``), placeholders ``{field}`` are no words.
A test fails if a text uses a forbidden word.
"""

import re
import string

import pytest

from habits import texts

FORBIDDEN = re.compile(
    r"\b(graph|scenario|script|node|gate|verb|door|wake|baton|shadow|active|retir|S1|promot|approve|vouch|"
    r"graduat|spotter|journal|decider|backend|trigger)\w*|learning loop|HABIT\.yaml",
    re.IGNORECASE)

# The keys of plan 2.10 and their fields (the same in English and French).
NEW_KEYS = {
    "dream_off": set(), "dream_habits_off": set(), "dream_empty": {"days"}, "dream_unread": {"why"}, "dream_busy": set(),
    "dream_read": {"turns", "sessions", "days", "took"}, "dream_partial": {"why"}, "dream_next": set(),
    "spotted_one": set(), "spotted_many": {"count"},
    "spotted_line": {"action", "turns", "days", "moments"}, "spotted_none": set(),
    "near_line": {"action", "turns", "days"}, "left_line": {"count"}, "list_empty": set(),
    "moment_first": {"percent"}, "moment_last": {"percent"}, "moment_skill": {"skill", "percent"},
    "moment_scheduled": {"percent"},
    "practising_line": {"name", "matches", "needed"}, "practising_plan_line": {"name", "matches", "needed"},
    "card_learn": {"name", "action", "turns", "days", "moments", "matches", "compared"},
    "card_learn_plan": {"name", "action", "turns", "days", "moments", "matches"},
    "card_try": {"action", "turns", "days", "moments"}, "card_flagged": {"commands"},
    "card_change": {"name", "sibling", "turns", "days", "action", "quiet"},
    "card_rest_quiet": {"name", "quiet"},
    "no": set(), "try": set(),
    "after_learned": {"name"}, "after_learned_staged": {"name"}, "after_changed_since": {"name"},
    "after_no": {"name", "days"}, "try_full": {"name"}, "after_changed": {"name", "action"},
    "news_learn": {"name"}, "news_learned_auto": {"name"}, "news_proposal": {"name"},
    "ask_dream": {"name"}, "ask_dream_try": {"name"},
}
# Keys that exist before the pass and change their words (plan 2.10: help gets the dream line, menu mentions it).
CHANGED_KEYS = {"help": set(), "menu": set()}


def fields(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def without_fields(text):
    return re.sub(r"\{[^}]*\}", "", text)


def forbidden_in(text):
    return [m.group(0) for m in FORBIDDEN.finditer(without_fields(text))]


def test_every_new_key_is_in_both_languages_with_the_fields_of_the_plan():
    for key, expected in NEW_KEYS.items():
        for lang in texts.LANGUAGES:
            assert key in texts.TEXTS[lang], (key, lang)
            assert fields(texts.TEXTS[lang][key]) == expected, (key, lang)


def test_the_changed_keys_keep_their_fields():
    for key, expected in CHANGED_KEYS.items():
        for lang in texts.LANGUAGES:
            assert fields(texts.TEXTS[lang][key]) == expected, (key, lang)


def test_the_new_texts_are_pinned_in_english_and_french():
    assert texts.say("spotted_one", "en") == "1 action comes back:"
    assert texts.say("spotted_many", "en", count=3) == "3 actions come back:"
    assert texts.say("spotted_many", "fr", count=3) == "3 actions reviennent :"
    assert texts.say("spotted_line", "en", action="pdf-merge", turns=3, days=2, moments=" · closes the work (60%)") == (
        "- `pdf-merge` · 3 turns on 2 days · closes the work (60%)")
    assert texts.say("moment_first", "fr", percent="40%") == "· premier geste d'une session (40%)"
    assert texts.say("no", "fr") == "Non" and texts.say("try", "fr") == "Essai"


def test_no_forbidden_word_in_the_english_texts():
    for key, text in texts.TEXTS["en"].items():
        assert not forbidden_in(text), (key, forbidden_in(text))


def test_no_forbidden_word_in_the_french_texts():
    for key, text in texts.TEXTS["fr"].items():
        assert not forbidden_in(text), (key, forbidden_in(text))


def test_the_new_english_texts_use_no_forbidden_word_at_all():
    for key in NEW_KEYS:
        assert not forbidden_in(texts.TEXTS["en"][key]), key
        assert not forbidden_in(texts.TEXTS["fr"][key]), key


@pytest.mark.parametrize("key", sorted(NEW_KEYS))
def test_each_new_text_formats_with_its_fields(key):
    values = {f: 1 for f in NEW_KEYS[key]}
    for lang in texts.LANGUAGES:
        assert texts.say(key, lang, **values)


# ---- E2: what the model reads, and what leaves as JSON ---------------------------------------------------------

import json
from types import SimpleNamespace

from habits import cockpit, display, graph, tools

# What the model reads. The YAML words (trigger, wake, nodes, start) are the file format and stay.
MODEL_FORBIDDEN = re.compile(
    r"\b(graph|scenario|script|node|gate|verb|door|baton|shadow|active|retir|S1|promot|approve|vouch|graduat|"
    r"spotter|journal|decider|backend)\w*|learning loop|HABIT\.yaml", re.IGNORECASE)


def schema_texts(schema):
    found = []
    if isinstance(schema, dict):
        found += [v for k, v in schema.items() if k == "description" and isinstance(v, str)]
        for v in schema.values():
            found += schema_texts(v)
    elif isinstance(schema, list):
        for v in schema:
            found += schema_texts(v)
    return found


def test_no_forbidden_word_in_what_the_model_reads():
    model = {"PROMPT_INTRO": cockpit.PROMPT_INTRO, "PROMPT_WHEN": " ".join(cockpit.PROMPT_WHEN),
             "PROMPT_LIST": cockpit.PROMPT_LIST, "PROMPT_TAIL": cockpit.PROMPT_TAIL,
             "PROMPT_NONE_OWN": cockpit.PROMPT_NONE_OWN, "PROMPT_CURATOR": cockpit.PROMPT_CURATOR,
             "RUN": tools.RUN_DESCRIPTION, "SAVE": tools.SAVE_DESCRIPTION,
             "LEARNING_LEFT": tools.LEARNING_LEFT, "LEARNING_NEXT": tools.LEARNING_NEXT, "FIX": tools.FIX,
             "MODEL_LEARNING": texts.MODEL_LEARNING, "MODEL_TELL": texts.MODEL_TELL,
             "learning_next": tools.learning_next("practice", 3) + tools.learning_next("auto", 3)}
    model.update({f"schema {i}": t for i, t in enumerate(schema_texts(tools.RUN_SCHEMA) + schema_texts(tools.SAVE_SCHEMA))})
    for name, text in model.items():
        assert not [m.group(0) for m in MODEL_FORBIDDEN.finditer(without_fields(text))], (name, text[:80])


def test_the_model_texts_say_what_is_true_now():
    for text in (tools.RUN_DESCRIPTION, tools.SAVE_DESCRIPTION, cockpit.PROMPT_TAIL):
        assert "promote: auto" not in text and "never delete (" not in text.lower()
    assert "learning to auto" in tools.SAVE_DESCRIPTION and "permissions" in tools.SAVE_DESCRIPTION
    assert "trigger: skill" in tools.SAVE_DESCRIPTION and "trigger: skill" in cockpit.PROMPT_TAIL
    assert "dream" in cockpit.PROMPT_TAIL and "automatic" in cockpit.PROMPT_TAIL


def test_the_instructions_that_campaigns_showed_to_matter_are_kept():
    assert "use its result as it is, do not redo or re-check it" in cockpit.PROMPT_INTRO
    assert "ONE call" in cockpit.PROMPT_WHEN_SEVERAL and "never two calls" in tools.RUN_DESCRIPTION
    assert "do not redo or re-check it" in tools.RUN_DESCRIPTION and "untrusted" in tools.RUN_DESCRIPTION


def test_the_tool_parameter_is_reflex_and_scenario_is_still_accepted():
    for schema in (tools.RUN_SCHEMA, tools.SAVE_SCHEMA):
        assert "reflex" in schema["parameters"]["properties"] and "scenario" not in schema["parameters"]["properties"]
    entry = tools.RUN_SCHEMA["parameters"]["properties"]["habits"]["items"]["properties"]
    assert "reflex" in entry and "scenario" not in entry
    assert tools.graph_source({"reflex": "spec: x"}) == "spec: x"
    assert tools.graph_source({"scenario": "spec: y"}) == "spec: y"  # the old name, not documented
    assert tools.fan_entries({"habits": [{"reflex": "a"}, {"scenario": "b"}]})[1] == []
    with pytest.raises(ValueError):
        tools.graph_source({"reflex": "a", "file": "b.yaml"})


def test_the_issues_the_model_reads_say_step_not_node():
    habit = {"spec": "habits/0.1", "start": "nowhere", "nodes": {"a": {"llm": "think", "next": "b"}, "b": {"end": "x"}}}
    issues = " ".join(str(i) for i in graph.validate(habit))
    assert "step" in issues and not re.search(r"\bnodes?\b(?! must)", issues.replace("nodes must be", ""))
    assert "no isolated step" in str(graph.Issue("1", "a", "x")) and "node" not in " ".join(graph.RULES.values())


def test_public_names_the_contract_words_and_leaves_what_people_wrote():
    data = {"mode": "dry", "node": "a", "shadow_ms": 1, "active_ms": 2, "learning_ms": 3,
            "planned": [{"node": "s", "tool": "terminal", "args": {"node": "kept"}}],
            "doubted": [{"item": "i", "node": "d"}], "values": {"node": 5, "shadow_x": 1}, "state": "shadow",
            "status": "end"}
    out = display.public(data)
    assert out["mode"] == "test" and out["step"] == "a" and "node" not in out
    assert out["learning_ms"] == 3 and out["automatic_ms"] == 2 and "shadow_ms" not in out
    assert out["planned"][0]["step"] == "s" and out["planned"][0]["args"] == {"node": "kept"}
    assert out["doubted"][0]["step"] == "d" and out["values"] == data["values"]
    assert out["state"] == "learning" and out["status"] == "end"
    assert display.public({"mode": "live"})["mode"] == "live"
    assert {display.public({"state": k})["state"] for k in ("shadow", "active", "retired", "live")} == {
        "learning", "automatic", "resting", "called"}
    assert data["node"] == "a" and data["mode"] == "dry"  # a copy


def test_the_internal_summary_keys_are_unchanged():
    import inspect
    from habits import ledger, view
    assert "shadow_ms" in inspect.getsource(ledger) and "shadow_ms" in inspect.getsource(display)


def test_the_cli_names_yes_and_rest_and_still_takes_the_old_words(capsys):
    import argparse
    from habits.plugin import Habits
    parser = argparse.ArgumentParser(prog="hermes habits")
    Habits.cli_setup(SimpleNamespace(), parser)
    parser.print_help()
    shown = capsys.readouterr().out
    assert "yes" in shown and "rest" in shown
    assert not re.search(r"\b(promote|retire|shadow|status|script|node)\b", shown)
    for old, new in (("promote", "yes"), ("retire", "rest")):
        assert parser.parse_args([old, "x"]).habits_command == old  # the old word is accepted
        assert parser.parse_args([new, "x"]).habits_command == new
    for sub in ("list", "show", "run", "dream"):
        parser.parse_args([sub] + (["x"] if sub in ("show", "run") else []))


def test_the_texts_name_the_new_cli_words():
    for lang in texts.LANGUAGES:
        for key in ("help", "promote_made"):
            assert "promote" not in texts.TEXTS[lang][key] and "retire" not in texts.TEXTS[lang][key]
        assert " yes, rest," in texts.TEXTS[lang]["help"]


# ---- the pages a user reads say what the code does, in one set of words -----------------------------------------------

from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAGES = {name: (ROOT / name).read_text(encoding="utf-8") for name in (
    "README.md", "SPEC.md", "docs/REFERENCE.md", "docs/SAFETY.md", "skills/habits/SKILL.md",
    "skills/writing-habits/SKILL.md")}
ONE_OFF = ("A one-off reflex the model writes acts for real unless `dry: true`. It never deletes and never runs a "
           "command Hermes flags.")


def test_the_pages_and_the_manifest_are_spelled_the_american_way():
    read = dict(PAGES, **{"plugin.yaml": (ROOT / "plugin.yaml").read_text(encoding="utf-8"),
                          "docs/BENCHMARKS.md": (ROOT / "docs/BENCHMARKS.md").read_text(encoding="utf-8")})
    for name, text in read.items():
        found = re.findall(r"(?i)\bpractis\w*", text)
        assert found in ([], ["practise"]) and (not found or name == "docs/REFERENCE.md"), (name, found)
    assert "`practise`, the British spelling, is still read as `practice`" in PAGES["docs/REFERENCE.md"]
    english = " ".join(v for v in texts.TEXTS["en"].values() if isinstance(v, str))
    assert not re.findall(r"(?i)\bpractis\w*", english)


def test_no_page_says_the_model_cannot_give_itself_the_yes():
    """The guard reads text: it blocks what it can read, and the pages name what it cannot."""
    for name, text in PAGES.items():
        flat = " ".join(text.split())
        assert not re.search(r"(?i)the model (cannot|can't|can never)( give itself the yes|\b[^.]{0,40}\bitself the yes)", flat), name
        assert "The model cannot (section 6)" not in flat, name
    for name in ("README.md", "SPEC.md", "docs/REFERENCE.md", "docs/SAFETY.md"):
        assert re.search(r"refuses what it cannot read",
                         " ".join(PAGES[name].split())), name
    safety = " ".join(PAGES["docs/SAFETY.md"].split())
    assert "A path built at run time inside code" in safety and "A row forged in the ledger file" in safety


def test_the_one_off_sentence_reads_the_same_everywhere():
    for name, text in PAGES.items():
        assert ONE_OFF in " ".join(text.split()), name


def test_the_pages_use_the_contract_words():
    for name, text in PAGES.items():
        prose = re.sub(r"`[^`\n]*`", "", re.sub(r"(?s)```.*?```", "", text))  # file keys and commands are not prose
        found = re.findall(r"(?i)\b(promot\w*|retir\w*|graphs?|nodes?|doors?|shadow\w*|scenario|baton)\b", prose)
        if name == "docs/SAFETY.md":
            found = [w for w in found if w != "node"]  # the JavaScript runtime, beside perl and ruby
        assert not found, (name, found)
        assert not re.search(r"(?i)\b(openai|anthropic|groq|ollama|mistral|gemini)\b", prose), name
