import json
import re
import shutil
import subprocess

import pytest

from habits import view

HOSTILE = "<!--<script>alert(1)</script>--> & \"quotes\" </body>"


def data(**extra):
    return {"name": "sort", "description": HOSTILE, "kind": "request", "status": "active", "origin": "skill",
            "habit": {"spec": "habits/0.1", "start": "look", "fallback": "help", "nodes": {
                "look": {"do": "terminal", "args": {"command": "ls"}, "next": "done"},
                "done": {"end": "Done."}, "help": {"wake": "Help."}}},
            "issues": [], "summary": {"agreement": 1.0, "compared": 12, "shadow_ms": 16800, "active_ms": 2100,
                                      "shadow_tokens": 41000, "active_tokens": 0, "saved": {"runs": 3}},
            "run": {"status": "end", "message": HOSTILE, "ms": 2100,
                    "steps": [{"node": "look", "summary": HOSTILE}], "doubts": []}, **extra}


def scripts(text):
    return re.findall(r"<script>(.*?)</script>", text, flags=re.S)


def test_no_value_can_escape_the_page_script():
    text = view.page(data())
    body = scripts(text)[-1]
    assert "<" not in body.split("= ", 1)[1].split(";\n", 1)[0]  # the data line holds no "<" at all
    assert text.count("</script>") == len(scripts(text))


def plain_of(text):
    """The page's plain HTML summary: what sits between the static and the scripted block."""
    return text.split('<div class="wrap static">', 1)[1].split('<div class="wrap app">', 1)[0]


def test_the_plain_page_has_the_whole_header_and_footer():
    # What a phone's file preview shows: the reflex's name in the frame, the label chip, the dashed rule, the mark.
    text = view.page(data())
    assert '<span id="ftitle">~/reflexes/sort</span>' in text
    static = plain_of(text)
    assert "class='lab'>Run · done<" in static and "<div class='titles'><h1>sort</h1></div>" in static
    assert "class=\"mark\"" in static.replace("'", '"') and "Habits</span></footer>" in static
    handed = view.static({**data(), "run": {"status": "wake", "steps": [], "ms": 1}})
    assert "class='lab hand'>Run · handed back<" in handed
    gallery = plain_of(view.gallery([data()]))
    assert "All habits" in gallery and "<h1>Your habits</h1>" in gallery and "Habits</span></footer>" in gallery


def test_the_plain_steps_read_as_words_and_are_cut_at_a_word():
    nodes = {"a": {"check": {"empty": "gateway"}, "next": "b"}, "b": {"check": {"greater": ["disk", 89]}, "next": "c"},
             "c": {"check": {"contains": ["fit", "close"]}, "next": "d"}, "d": {"end": "word " * 60}}
    plain = view.static({"name": "x", "habit": {"start": "a", "nodes": nodes}})
    assert "<b>a</b> · check · empty gateway" in plain and "<b>b</b> · check · disk greater than 89" in plain
    assert "<b>c</b> · check · fit contains close" in plain and "{'" not in plain
    cut = view.cut("one two three four five six", 12)
    assert cut == "one two…" and view.cut("short", 12) == "short"


def test_one_start_is_named_one_way():
    assert [view.start(t) for t in (None, "message", "skill", {"every": "1d"}, {"every": "1d", "at": "21:00"})] == [
        "called", "message", "skill", "schedule · every 1d", "schedule · every 1d at 21:00"]
    assert view.head({"origin": "example", "habit": {"trigger": {"every": "1d"}}}) == "example · schedule · every 1d"
    assert view.head({"status": "live", "habit": {}}) == "called"


def test_the_page_carries_a_plain_summary_for_phones():
    text = view.page(data())
    static = plain_of(text)
    assert "<h1>sort</h1>" in static and "&lt;!--&lt;script&gt;" in static
    assert "2.1 s instead of 16.8 s per request" in static and "0 model tokens instead of 41.0k" in static
    assert "<b>look</b> · terminal" not in static and "<b>look</b> · do" in static


def test_the_gallery_holds_every_habit_and_their_page_template(tmp_path):
    path = view.write_gallery(tmp_path, [data(), data(name="exemple", origin="example")])
    text = path.read_text()
    assert path.name == "_gallery.html"
    blob = scripts(text)[-1].split("const G = ", 1)[1].split(";\nconst $", 1)[0]
    loaded = json.loads(blob)
    assert [h["name"] for h in loaded["habits"]] == ["sort", "exemple"]
    assert view.TOKEN in loaded["template"] and view.STATIC not in loaded["template"]
    assert "<" not in blob and text.count("</script>") == len(scripts(text))
    plain = plain_of(text)
    assert "<b>sort</b> · automatic · called" in plain and "96%" not in plain  # one block per habit, one start word
    assert "<b>exemple</b> · example · called" in plain and plain.count("agreement") == 1  # no scores for examples


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("make", [lambda: view.page(data()), lambda: view.gallery([data()])])
def test_the_scripts_parse(tmp_path, make):
    for i, body in enumerate(scripts(make())):
        source = tmp_path / f"s{i}.js"
        source.write_text(body)
        subprocess.run(["node", "--check", str(source)], check=True, capture_output=True)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_gallery_shows_a_trigger_it_does_not_know_as_written():
    # An old trigger: {event: …} is invalid and never runs: "by name" said otherwise.
    trigger = re.search(r"function trigger\(t\) \{.*?\n\}", view.gallery([data()]), flags=re.S).group(0)
    shown = subprocess.run(["node", "-e", trigger + "\nconsole.log(JSON.stringify(['message', {every: '5m'}, "
                            "{event: 'kanban_task_blocked'}, undefined].map(trigger)))"],
                           check=True, capture_output=True, text=True).stdout
    assert json.loads(shown) == ["message", "schedule · every 5m", '{"event":"kanban_task_blocked"}', "called"]


def test_a_one_off_shows_no_promotion_and_counts_read_well():
    one_off = data(kind="one-off", status="live", summary={"runs": 1, "compared": 0, "promote_runs": 12})
    assert view.facts(one_off) == ["called by name, never compared · 1 run"]
    assert view.facts(data(kind="watch", summary={"runs": 1, "woke": 1})) == ["1 check · 1 handed back"]
    assert view.facts(data(origin="one-off", kind="one-off", summary={"promote_runs": 12})) == []  # a scenario run once
    assert "/12" not in plain_of(view.gallery([one_off]))


@pytest.mark.parametrize("make", [lambda: view.page(data()), lambda: view.gallery([data()])])
def test_the_pages_load_nothing_from_outside_and_carry_their_font(make):
    text = make()
    refs = re.findall(r"""(?:src|href)\s*=\s*["']([^"']*)["']|url\(\s*["']?([^"')]*)""", text)
    outside = [a or b for a, b in refs if re.match(r"(?i)(https?:)?//", a or b)]
    assert outside == []  # the only address in a page is the SVG namespace, which is not a request
    assert "@font-face" in text and "Departure Mono" in text and "data:font/woff2;base64," in text
    assert (view.VIEW / "DepartureMono-LICENSE.txt").is_file() and "DepartureMono-LICENSE" in text


BANNED = r"\b(nodes?|gates?|graphs?|scenarios?|verbs?|doors?|batons?|triggers?|shadow|promote|approve|vouch|retired)\b"


def sentences(text):
    """The words a reader sees in a page: the text between tags and the plain quoted strings of its script."""
    text = re.sub(r"<style>.*?</style>", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    found = re.findall(r">([^<>{}\n]+)<", text)
    found += re.findall(r"""['"`]([A-Za-z][A-Za-z ,.:;'’·()/-]*)['"`]""", text)
    return [s for s in found if " " in s.strip() and "'" not in s.strip("'")]  # a quoted list of keys is code, not words


@pytest.mark.parametrize("make", [lambda: view.page(data()), lambda: view.gallery([data()])])
def test_page_words(make):
    # the contract's words: no node, gate, graph, scenario, verb, door or baton in anything a reader sees
    assert [s for s in sentences(make()) if re.search(BANNED, s, re.I)] == []


@pytest.mark.parametrize("make", [lambda: view.page(data()), lambda: view.gallery([data()])])
def test_tokens_are_the_films(make):
    text = make().upper()
    for token in ("#08090B", "#0C0E10", "#EDEBDD", "#A8A6C4", "#FF5F1A", "#2516FF", "#8A86FF", "#FF3B5C"):
        assert token in text, token
    assert "RGBA(237,235,221,.28)" in text.replace(" ", "")
    assert 'class="mark"' in make() and "Habits" in make()  # the mark ◇─■─○ is drawn in SVG


@pytest.mark.parametrize("make", [lambda: view.page(data()), lambda: view.gallery([data()])])
def test_pages_embed_the_font_and_load_nothing(make):
    text = make()
    assert "@font-face" in text and "data:font/woff2;base64," in text and "Departure Mono" in text
    assert not re.findall(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", text, re.I)
    assert "url(http" not in text and "@import" not in text


def test_licence_shipped():
    licence = (view.VIEW / "DepartureMono-LICENSE.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE" in licence.upper() and "Helena Zhang" in licence
