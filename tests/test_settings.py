"""The seven settings, the hidden overrides of the old ones (0.28) and what learning: off changes."""
from pathlib import Path

import pytest
import yaml

from habits import catalog, deciders, plugin
from test_plugin import FakeCtx, Inline, answer, llm_turn

ROOT = Path(__file__).resolve().parents[1]
SEVEN = ("learning", "caution", "dream", "decision_model", "permissions", "model_text", "language")
HIDDEN = ("promote", "backend", "min_confidence", "trigger_confidence", "promote_runs", "promote_agreement",
          "retire_window", "retire_agreement", "spot_check", "judge", "direct")


def habits_with(config=None):
    return plugin.Habits(FakeCtx(config or {}))


@pytest.mark.parametrize("key", SEVEN)
def test_defaults_and_choices(key):
    default = plugin.DEFAULTS[key]
    assert default == plugin.CHOICES[key][0] or key in ("learning", "dream", "language") or default in plugin.CHOICES[key]
    assert habits_with().setting(key) == ("manual" if key == "dream" else default)  # test contexts: no real dream
    for value in plugin.CHOICES[key]:
        assert habits_with({key: value}).setting(key) == value
        assert habits_with({key: value.upper()}).setting(key) == value  # case does not matter
    # a typo falls back on the default, except on the three safety switches, which fall on their strict side
    assert habits_with({key: "no-such-value"}).setting(key) == plugin.STRICT.get(key, default)


@pytest.mark.parametrize("key,strict", (("permissions", "strict"), ("model_text", "never"), ("dream", "manual")))
@pytest.mark.parametrize("value", (False, "off", "no", "false", "none", "restricted", "stict", "never-ever", 0))
def test_a_value_a_safety_switch_does_not_know_is_its_strict_side_with_one_warning(key, strict, value, caplog, monkeypatch):
    monkeypatch.setattr(plugin, "_warned", set())
    habits = habits_with({key: value})
    caplog.clear()
    assert habits.setting(key) == strict and habits.setting(key) == strict
    lines = [r.getMessage() for r in caplog.records if key in r.getMessage()]
    assert len(lines) == 1 and strict in lines[0]


def test_an_unset_safety_switch_is_still_the_default_without_a_warning(caplog):
    for key in ("permissions", "model_text", "dream"):
        # test contexts: no real dream, so an unset dream reads manual here
        assert habits_with().setting(key) == ("manual" if key == "dream" else plugin.DEFAULTS[key])
    assert not caplog.records


def test_the_other_settings_keep_the_default_on_an_unknown_value():
    assert habits_with({"learning": "stict"}).setting("learning") == "suggest"  # the default: nothing runs before a try
    assert habits_with({"caution": False}).setting("caution") == "normal"
    assert habits_with({"decision_model": "off"}).setting("decision_model") == "auto"


def test_the_british_spelling_of_practice_is_still_read_as_practice(caplog):
    assert plugin.DEFAULTS["learning"] == "suggest" and "practise" not in plugin.CHOICES["learning"]
    for written in ("practise", "Practise", " PRACTISE "):
        assert habits_with({"learning": written}).setting("learning") == "practice"
    assert not caplog.records  # an accepted spelling, not a typo: no warning
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert "practise" not in (ROOT / "plugin.yaml").read_text()
    assert "practice" in str(manifest)


def test_the_defaults_are_the_contract_ones():
    assert {k: plugin.DEFAULTS[k] for k in SEVEN} == {
        "learning": "suggest", "caution": "normal", "dream": "daily", "decision_model": "auto",
        "permissions": "hermes", "model_text": "quoted", "language": "en"}


def test_a_bare_off_in_yaml_is_the_word_off():
    assert habits_with({"learning": False}).setting("learning") == "off"  # YAML 1.1 reads a bare off as false


def test_promote_auto_becomes_learning_auto_when_learning_unset(caplog):
    assert habits_with({"promote": "auto"}).setting("learning") == "auto"
    assert habits_with({"promote": "auto"}).setting("promote") == "auto"
    assert habits_with({"promote": "auto"}).rules().promote == "auto"
    assert habits_with({"promote": "ask"}).setting("learning") == "suggest"
    assert habits_with().setting("promote") == "ask"
    # an explicit learning wins over the old name
    assert habits_with({"promote": "auto", "learning": "suggest"}).setting("learning") == "suggest"
    assert habits_with({"promote": "auto", "learning": "suggest"}).rules().promote == "ask"


def test_learning_auto_alone_is_the_old_promote_auto():
    habits = habits_with({"learning": "auto"})
    assert habits.setting("promote") == "auto" and habits.rules().promote == "auto"
    assert tools_next(habits).startswith("It starts learning: it runs as a test next to you and becomes automatic")
    assert tools_next(habits_with({"learning": "practise"})).endswith("only after their yes in /habits.")


def tools_next(habits):
    from habits import tools
    return tools.learning_next(habits.setting("learning"), habits.setting("promote_runs"))


@pytest.mark.parametrize("old, new", [("llm", "hermes"), ("jev", "jev"), ("auto", "auto")])
def test_backend_llm_becomes_hermes(old, new):
    habits = habits_with({"backend": old})
    assert habits.setting("decision_model") == new
    assert habits.setting("backend") == old  # the old name still reads as deciders.build knows it
    assert habits_with({"backend": old, "decision_model": "jev"}).setting("decision_model") == "jev"  # explicit wins


@pytest.mark.parametrize("model, order", [("hermes", ["llm"]), ("jev", ["jev", "llm"]), ("auto", ["jev", "llm"])])
def test_decision_model_picks_the_chain(monkeypatch, model, order):
    habits = habits_with({"decision_model": model})
    monkeypatch.setattr(habits, "secret", lambda name: "key")
    assert [b.name for b in plugin.Habits.decider(habits).backends] == order
    monkeypatch.setattr(habits, "secret", lambda name: None)
    assert [b.name for b in plugin.Habits.decider(habits).backends] == ["llm"]


def test_the_old_backend_llm_still_keeps_jev_behind(monkeypatch):
    habits = habits_with({"backend": "llm"})  # unchanged from before the night
    monkeypatch.setattr(habits, "secret", lambda name: "key")
    assert [b.name for b in plugin.Habits.decider(habits).backends] == ["llm", "jev"]


def test_old_number_set_wins_over_caution():
    for caution in ("relaxed", "normal", "strict"):
        assert habits_with({"promote_runs": 4, "caution": caution}).setting("promote_runs") == 4
    assert habits_with({"min_confidence": "0.5"}).setting("min_confidence") == 0.5
    assert habits_with({"promote_runs": "many"}).setting("promote_runs") == 12  # unreadable: the caution row


def test_caution_feeds_every_number():
    strict = habits_with({"caution": "strict"})
    assert strict.setting("promote_runs") == 20 and strict.setting("min_confidence") == .85
    assert strict.setting("retire_window") == 30 and strict.setting("spot_check") == .20
    assert strict.setting("trigger_confidence") == .75 and strict.setting("promote_agreement") == .95
    assert strict.setting("retire_agreement") == .85
    rules = strict.rules()
    assert (rules.promote_runs, rules.promote_agreement, rules.retire_window, rules.retire_agreement) == (20, .95, 30, .85)
    relaxed = habits_with({"caution": "relaxed"})
    assert (relaxed.setting("promote_runs"), relaxed.setting("min_confidence")) == (8, .75)
    normal = habits_with()  # caution normal equals today's defaults exactly
    assert (normal.setting("promote_runs"), normal.setting("promote_agreement"), normal.setting("retire_window"),
            normal.setting("retire_agreement"), normal.setting("spot_check"), normal.setting("min_confidence"),
            normal.setting("trigger_confidence")) == (12, .9, 20, .75, .1, .8, .7)


def test_judge_and_direct_still_default_on():
    habits = habits_with()
    assert habits.setting("judge") is True and habits.setting("direct") is True
    assert habits_with({"judge": "off"}).setting("judge") is False


def test_hidden_keys_are_in_the_defaults_and_out_of_the_schema():
    schema = yaml.safe_load((ROOT / "plugin.yaml").read_text())["config_schema"]
    for key in HIDDEN:
        assert key in plugin.DEFAULTS and key not in schema, key


def test_config_schema_has_the_seven():
    schema = yaml.safe_load((ROOT / "plugin.yaml").read_text())["config_schema"]
    assert set(schema) == set(SEVEN) | {"typesafe_api_key"}
    for key in SEVEN:
        assert schema[key]["enum"] == list(plugin.CHOICES[key]), key
        assert schema[key]["default"] == plugin.DEFAULTS[key], key
        assert schema[key]["label"] and schema[key]["description"].count(". ") <= 3
    assert schema["typesafe_api_key"]["type"] == "secret" and schema["typesafe_api_key"]["env"] == "TYPESAFE_API_KEY"


def test_the_manifest_speaks_the_contract_words():
    text = (ROOT / "plugin.yaml").read_text().split("config_schema:")[1].lower()
    for banned in ("shadow", "graph", "scenario", "promote", "s1", "retire", "backend"):
        assert banned not in text, banned


# ---- learning: off ---------------------------------------------------------------------------------------------------

def message_habit(tmp_path, monkeypatch, config):
    from test_graph import DEMO
    ctx = FakeCtx(config)
    habits = plugin.Habits(ctx, home=tmp_path / "plugin-data" / "habits")
    monkeypatch.setattr(habits, "decider", lambda: deciders.Chain([deciders.Fake(answer)]))
    skill = tmp_path / "skills" / "sort-downloads-by-type"
    (skill / "assets").mkdir(parents=True)
    (skill / "assets" / "reflex.yml").write_text(DEMO)
    saved = [catalog.Habit("sort-downloads-by-type", "Sort the downloads", skill / "assets" / "reflex.yml")]
    monkeypatch.setattr(catalog, "skills", lambda: [catalog.read(h.path, h.name, h.description, "skill") for h in saved])
    monkeypatch.setattr(plugin.threading, "Thread", Inline)
    habits.ledger.adopt(saved[0].name, catalog.skills()[0].version, "asked")
    return ctx, habits


def test_learning_off_keeps_automatic_habits(tmp_path, monkeypatch):
    for learning, shadow_runs in (("off", 0), ("practise", 1), ("suggest", 1), ("auto", 1)):
        ctx, habits = message_habit(tmp_path / learning, monkeypatch, {"learning": learning})
        name, version = "sort-downloads-by-type", catalog.skills()[0].version
        # a learning habit: practiced dry beside the model, unless learning is off
        habits.on_pre_llm_call(session_id="s", user_message="Sort my downloads please")
        habits.on_post_llm_call(session_id="s")
        assert len(habits.ledger.runs(name, 10)) == shadow_runs, learning
    # an automatic habit keeps acting with learning off
    ctx, habits = message_habit(tmp_path / "active", monkeypatch, {"learning": "off"})
    version = catalog.skills()[0].version
    assert habits.ledger.approve("sort-downloads-by-type", version, by="user")
    reply = habits.on_pre_llm_call(session_id="a", user_message="Sort my downloads please")
    assert reply and "context" in reply and any(name == "terminal" for name, _ in ctx.calls)


def test_the_decision_model_text_says_what_the_code_does():
    text = yaml.safe_load((ROOT / "plugin.yaml").read_text())["config_schema"]["decision_model"]["description"]
    assert "hermes asks your Hermes model only" in text and "jev or hermes asks that one first" not in text
    # and the code agrees: hermes has no second model, jev has Hermes's model behind it
    for key in ("permissions", "model_text", "dream"):
        word = plugin.STRICT[key]
        desc = yaml.safe_load((ROOT / "plugin.yaml").read_text())["config_schema"][key]["description"]
        assert f"read as {word}" in desc


def test_the_manifest_default_for_learning_is_the_code_default():
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    assert manifest["config_schema"]["learning"]["default"] == plugin.DEFAULTS["learning"] == "suggest"
