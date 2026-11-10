import time

import pytest
import yaml

from habits.yaml12 import load


def test_yes_and_no_stay_text():
    assert load("then: {yes: a, no: b}") == {"then": {"yes": "a", "no": "b"}}


def test_on_and_off_stay_text():
    assert load("[on, off, Yes, NO]") == ["on", "off", "Yes", "NO"]


def test_true_and_false_are_booleans():
    assert load("[true, false, True, FALSE]") == [True, False, True, False]


def test_json_reads_too():
    assert load('{"a": [1, true, null]}') == {"a": [1, True, None]}


def test_pyyaml_itself_is_untouched():
    # Hermes reads its own config with safe_load: our loader must not change it.
    assert yaml.safe_load("yes") is True


# A few hundred bytes of nested aliases expand to millions of strings (38 s and 6.4 GB at 7 levels): a habit
# graph takes none. The bench's own scenario files keep theirs.
def bomb(levels: int) -> str:
    lines = ['l0: &l0 ["xxxxxxxxxx", "xxxxxxxxxx", "xxxxxxxxxx", "xxxxxxxxxx", "xxxxxxxxxx", '
             '"xxxxxxxxxx", "xxxxxxxxxx", "xxxxxxxxxx", "xxxxxxxxxx", "xxxxxxxxxx"]']
    lines += [f"l{n}: &l{n} [{', '.join([f'*l{n - 1}'] * 10)}]" for n in range(1, levels + 1)]
    return "\n".join(lines) + (
        f"\nspec: habits/0.1\nstart: a\nfallback: w\nnodes: {{a: {{do: terminal, read: true, args: {{command: ls, "
        f"extra: *l{levels}}}, next: e}}, e: {{end: ok}}, w: {{wake: help}}}}\n")


def test_an_alias_is_refused_at_once():
    text = bomb(7)
    assert len(text) < 1000
    started = time.monotonic()
    with pytest.raises(yaml.YAMLError) as caught:
        load(text)
    assert time.monotonic() - started < 1
    assert "alias" in str(caught.value)


def test_an_anchor_alone_is_fine_and_the_bench_keeps_its_aliases():
    assert load("a: &x 1\nb: 2") == {"a": 1, "b": 2}
    assert load("a: &x [1]\nb: *x", aliases=True) == {"a": [1], "b": [1]}
