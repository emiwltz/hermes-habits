"""The texts Habits says: French and English twins, and the user's words for the ledger's notes."""

import string

from habits import texts


def test_every_text_has_its_twin_with_the_same_fields():
    fields = lambda text: {f for _, f, _, _ in string.Formatter().parse(text) if f}
    assert set(texts.TEXTS["en"]) == set(texts.TEXTS["fr"])
    for key, text in texts.TEXTS["en"].items():
        assert fields(text) == fields(texts.TEXTS["fr"][key]), key


def test_the_footers_in_french_and_english():
    fr = texts.say("footer", "fr", name="mac-agents-status", took=texts.seconds(1800, "fr"),
                   calls=texts.calls(0, "fr", jev=True))
    assert fr == "🔁 mac-agents-status · 1,8 s · 0 appel au modèle (+ Jev) · pas ça ? /habits faux"
    en = texts.say("footer", "en", name="mac-agents-status", took=texts.seconds(19400, "en"),
                   calls=texts.calls(2, "en"))
    assert en == "🔁 mac-agents-status · 19 s · 2 model calls · not right? /habits wrong"


def test_the_footer_names_the_decision_model_that_answered():
    assert texts.calls(0, "en", clef=True) == "0 model calls (+ Clef)"
    assert texts.calls(0, "fr", clef=True) == "0 appel au modèle (+ Clef)"
    assert texts.calls(0, "en", jev=True, clef=True) == "0 model calls (+ Jev, Clef)"
    assert texts.calls(1, "en") == "1 model call"


def test_the_french_lines_read_as_the_spec_writes_them():
    assert texts.say("retired", "fr", name="vault-watch-triage", why=texts.why("70% over the last 20", "fr")) == (
        "─ vault-watch-triage passe au repos : 14 justes sur 20. La relancer : /habits essai vault-watch-triage")
    assert texts.why("3 errors in a row", "fr") == "3 erreurs d'affilée"
    assert texts.language("FR") == "fr" and texts.language("de") == "en" and texts.language(None) == "en"
