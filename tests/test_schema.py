"""habit.schema.json checks the structure of a habit; graph.validate stays the authority."""

import json
import re
from pathlib import Path

import pytest

from habits import graph
from test_graph import DEMO, small

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "habit.schema.json"


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validator():
    jsonschema = pytest.importorskip("jsonschema")
    return jsonschema.Draft202012Validator(schema())


def spec_example() -> dict:
    """The complete example of SPEC.md, its first yaml block that starts with spec:."""
    text = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, re.S)
    return graph.load(next(b for b in blocks if b.startswith("spec: habits/0.1")))


# One structural fault each: field value, node shape, exits, limits with trigger form, name pattern.
BROKEN = {
    "wrong spec": lambda: {**small(), "spec": "habits/9"},
    "two verbs in a node": lambda: small(done={"end": "Done.", "wake": "Also?"}),
    "check without its no exit": lambda: small(look={"check": {"empty": "trigger"}, "then": {"yes": "done"}}),
    "watch over its budget": lambda: {**small(), "trigger": {"every": "30m"}, "limits": {"seconds": 120}},
    "node name out of pattern": lambda: small(**{"Tidy up": {"end": "Never."}}),
    "time of day unquoted": lambda: {**small(), "trigger": {"every": "1d", "at": 1260}},
    "time of day on hours": lambda: {**small(), "trigger": {"every": "6h", "at": "21:00"}},
    "time of day every 7 days": lambda: {**small(), "trigger": {"every": "7d", "at": "09:00"}},
    "pick with then": lambda: picked(then={"unsure": "help"}),
    "pick with each": lambda: picked(each="files"),
    "pick without next": lambda: picked(next=None),
    "margin out of sort": lambda: picked(margin=2),
    "margin on a yes/no": lambda: small(look={"ask": "Ready?", "yes_no": True, "margin": 0.2, "next": "done"}),
    "unsure exit on a yes/no": lambda: small(look={"ask": "Ready?", "yes_no": True, "unsure": "help", "next": "done"}),
}

def picked(**change):
    """List the downloads, then pick one: next with it, unsure without."""
    node = {"ask": "Which one?", "pick": "files", "as": "chosen", "next": "done", "unsure": "help", **change}
    return small(look={**small()["nodes"]["look"], "next": "pick"}, pick={k: v for k, v in node.items() if v is not None})


# Structure SPEC.md forbids that the schema refuses on its own (§3 to §5).
REFUSED_BY_THE_SCHEMA = {
    "as with a reserved name": lambda: small(look={**small()["nodes"]["look"], "as": "doubts"}),
    "input with a reserved name": lambda: {**small(), "inputs": ["item"]},
    "do with with": lambda: small(look={**small()["nodes"]["look"], "with": ["files"]}),
    "use with each": lambda: small(look={"use": "other", "each": "files", "next": "done"}),
    "llm with an empty schema": lambda: small(look={"llm": "Sum up.", "schema": {}, "next": "done"}),
    "option that is not text or a whole number": lambda: small(
        look={"ask": "Which?", "choice": [1.5, True], "next": "done"}),
}


# (a) The schema itself.
def test_schema_is_json():
    data = schema()
    assert data["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert data["properties"]["spec"] == {"const": graph.SPEC}


def test_schema_matches_the_validator_constants():
    data = schema()
    limits = data["$defs"]["limits"]["properties"]
    assert {key: value["maximum"] for key, value in limits.items()} == graph.LIMIT_CAPS
    assert data["properties"]["nodes"]["maxProperties"] == graph.MAX_NODES
    assert data["$defs"]["node"]["oneOf"] == [{"$ref": f"#/$defs/{verb}"} for verb in graph.VERBS]
    assert len(data["$defs"]["trigger"]["oneOf"]) == 3 and "event" not in json.dumps(data["$defs"]["trigger"])
    every = data["$defs"]["trigger"]["oneOf"][2]["properties"]["every"]["pattern"]
    assert every == graph.EVERY.pattern  # whole minutes, hours or days: Hermes cron has no seconds
    assert data["$defs"]["trigger"]["oneOf"][2]["properties"]["at"]["pattern"] == graph.AT.pattern
    assert set(data["$defs"]["reserved"]["enum"]) == graph.BUILTINS


def test_schema_is_a_valid_draft_2020_12_schema():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(schema())


# (b) Valid habits pass, broken ones do not.
@pytest.mark.parametrize("habit", [graph.load(DEMO), small(), {**small(), "trigger": {"every": "1d", "at": "21:00"}},
                                   picked(), picked(unsure=None, margin=0.3, min_confidence=0.6)],
                         ids=["demo", "small", "daily at 21:00", "pick", "pick to the fallback"])
def test_valid_habits_pass(habit):
    errors = [error.message for error in validator().iter_errors(habit)]
    assert errors == []


def test_spec_example_is_the_tidy_example_and_valid():
    example = spec_example()
    assert example == graph.load((ROOT / "examples" / "tidy-downloads" / "assets" / "reflex.yml").read_text(encoding="utf-8"))
    assert graph.validate(example) == []
    assert list(validator().iter_errors(example)) == []


@pytest.mark.parametrize("name", BROKEN)
def test_broken_habits_fail(name):
    assert not validator().is_valid(BROKEN[name]())


@pytest.mark.parametrize("name", BROKEN)
def test_broken_habits_are_refused_by_the_validator_too(name):
    assert graph.validate(BROKEN[name]()) != []


@pytest.mark.parametrize("name", REFUSED_BY_THE_SCHEMA)
def test_forbidden_structure_fails_the_schema(name):
    assert not validator().is_valid(REFUSED_BY_THE_SCHEMA[name]())
