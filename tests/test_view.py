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


def test_the_page_carries_a_plain_summary_for_phones():
    text = view.page(data())
    static = text.split('<div class="wrap static">', 1)[1].split("</div>", 1)[0]
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
    plain = text.split('<div class="wrap static">', 1)[1].split("</div>", 1)[0]
    assert "<b>sort</b> · automatic · request" in plain and "96%" not in plain  # one block per habit
    assert "<b>exemple</b> · example · called by name" in plain and plain.count("agreement") == 1  # no scores for examples


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
    assert json.loads(shown) == ["on request", "every 5m", '{"event":"kanban_task_blocked"}', "called by name"]


def test_a_one_off_shows_no_promotion_and_counts_read_well():
    one_off = data(kind="one-off", status="live", summary={"runs": 1, "compared": 0, "promote_runs": 12})
    assert view.facts(one_off) == ["called by name, never compared · 1 run"]
    assert view.facts(data(kind="watch", summary={"runs": 1, "woke": 1})) == ["1 check · 1 wake"]
    assert view.facts(data(origin="one-off", kind="one-off", summary={"promote_runs": 12})) == []  # a scenario run once
    assert "/12" not in view.gallery([one_off]).split('<div class="wrap static">', 1)[1].split("</div>", 1)[0]
