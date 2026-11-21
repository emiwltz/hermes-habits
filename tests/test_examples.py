"""The shipped examples, each played on a host that answers from a table: what they read, decide and change."""

import json
from pathlib import Path

import pytest

from conftest import FakeHost, decider
from reflexes import describe, resume, run

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def source(name: str) -> str:
    return (EXAMPLES / f"{name}.py").read_text()


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.py")), ids=lambda p: p.stem)
def test_every_example_is_a_reflex_that_says_what_it_is_for(path):
    about = describe(path.read_text())
    assert about["doc"] and len(about["doc"].splitlines()[0]) <= 110


def test_disk_space():
    host = FakeHost({"df": "Filesystem Size Used Avail Use% Mounted on\n/dev/disk1 100G 93G 7G 93% /\n"})
    outcome = run(source("disk-space"), {}, host)
    assert outcome.status == "done" and outcome.result.endswith("93% /") and host.ran == [["df", "-h", "/home/me"]]


def test_tidy_downloads_sorts_and_sends_known_installers_to_the_trash():
    host = FakeHost({"ls -p /home/me/Downloads": "Zoom.pkg\nphoto.jpg\nold/\nreport.pdf\n",
                     "ls /Applications": "Zoom.app\nSafari.app\n"})
    small = decider({"Zoom.pkg": ("installer", 0.97), "photo.jpg": ("image", 0.95), "report.pdf": ("document", 0.9)})
    small_yes = lambda q, options, items: ([("yes", 0.95)] if options == ["yes", "no"] else small(q, options, items))  # noqa: E731
    dry = run(source("tidy-downloads"), {}, host, dry=True, decider=small_yes)
    assert dry.status == "done" and dry.result["trashed"] == ["Zoom.pkg"]
    assert dry.planned[:2] == ["mkdir -p /home/me/Downloads/installer",
                               "mv -n /home/me/Downloads/Zoom.pkg /home/me/Downloads/installer/"]
    assert dry.planned[-1] == "mv -n /home/me/Downloads/installer/Zoom.pkg /home/me/.Trash/"
    assert len(dry.planned) == 7 and not any("old" in step for step in dry.planned)


def test_check_links_tells_broken_from_blocked():
    page = '<a href="https://a.example/ok">a</a> <a class="x" href="https://b.example/gone">b</a>' \
           '<a href="https://c.example/login">c</a><a href="https://a.example/ok">again</a><a href="/local">l</a>'
    codes = {"https://a.example/ok": "200", "https://b.example/gone": "404", "https://c.example/login": "403"}
    host = FakeHost({"curl": lambda argv: page if argv[-1] == "https://site.example" else codes[argv[-1]]})
    outcome = run(source("check-links"), {"page": "https://site.example"}, host)
    assert outcome.result["broken"] == ["https://b.example/gone"]
    assert outcome.result["blocked"] == ["https://c.example/login"] and outcome.result["checked"] == 3
    assert not any(e["verb"] in ("do", "write") for e in outcome.effects)


HEALTHY = {
    "cat": json.dumps({"gateway_state": "running", "pid": 4242, "platforms": {"telegram": {"state": "connected"}}}),
    "hermes logs agent --since 30m -n 5000": "2026-11-21 10:00:00 INFO ok\n",
    "hermes logs errors --since 1h --level ERROR -n 2000": "",
    "curl": "200", "df": "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/disk1 100 40 60 40% /\n",
    "find": "",
}


def test_hermes_health_says_healthy_with_six_reads_and_no_model():
    host = FakeHost(HEALTHY)
    outcome = run(source("hermes-health"), {}, host)
    assert outcome.status == "done" and outcome.result["healthy"] is True and len(host.ran) == 6
    assert "disk 40% used" in outcome.result["message"] and [e["verb"] for e in outcome.effects] == ["sh"] * 6


@pytest.mark.parametrize("change, said", [
    ({"cat": json.dumps({"gateway_state": "degraded"})}, "the gateway reports degraded"),
    ({"cat": json.dumps({"gateway_state": "running", "platforms": {"discord": {"state": "fatal"}}})},
     "messaging is down for discord"),
    ({"hermes logs agent --since 30m -n 5000": "x API call failed after 3 retries: 500\n"}, "a model call failed"),
    ({"curl": "000"}, "github.com cannot be reached"),
    ({"df": "h\n/dev/disk1 100 95 5 95% /\n"}, "the disk is 95% used"),
    ({"find": "/home/me/.hermes/gateway_state.json\n", "ps": " 4242\n"}, "it may be frozen"),
])
def test_hermes_health_names_what_needs_attention(change, said):
    outcome = run(source("hermes-health"), {}, FakeHost({**HEALTHY, **change}))
    assert outcome.result["healthy"] is False and said in outcome.result["message"]


def test_hermes_health_with_no_gateway_and_a_dead_one_is_no_alarm():
    assert run(source("hermes-health"), {}, FakeHost({**HEALTHY, "cat": ("", "No such file", 1)})).result["healthy"]
    killed = {**HEALTHY, "find": "/home/me/.hermes/gateway_state.json\n", "ps": ("", "", 1)}
    assert run(source("hermes-health"), {}, FakeHost(killed)).result["healthy"]


def test_watch_hermes_releases_remembers_then_wakes_on_a_new_one():
    release = lambda tag: json.dumps({"tag_name": tag, "reactions": {"total": 12}})  # noqa: E731
    host = FakeHost({"curl": release("v0.23.3")})
    assert run(source("watch-hermes-releases"), {}, host).result == "Watching Hermes releases from v0.23.3 on."
    assert run(source("watch-hermes-releases"), {}, host).result == "No new Hermes release."
    host.commands["curl"] = release("v0.24.0")
    dry = run(source("watch-hermes-releases"), {}, host, dry=True)
    assert dry.status == "asks" and host.files["/home/me/.reflexes/memory/hermes-release.txt"] == "v0.23.3\n"
    woken = run(source("watch-hermes-releases"), {}, host)
    assert woken.status == "asks" and "v0.24.0 (the last one seen was v0.23.3)" in woken.asks["question"]
    assert resume(woken.state, "told", host).result["new"] == "v0.24.0"
    assert run(source("watch-hermes-releases"), {}, host).result == "No new Hermes release."
    host.commands["curl"] = ("", "curl: (22) 503", 22)
    assert "did not answer" in run(source("watch-hermes-releases"), {}, host).result


def test_hacker_news_digest_wakes_the_big_model_only_for_close_stories():
    hits = {"hits": [{"title": "A local model beats GPT", "url": "https://a"}, {"title": "Football results", "url": None},
                     {"title": "Ask HN: self-hosting mail?"}]}
    host = FakeHost({"curl": json.dumps(hits)})
    far = decider({"A local model beats GPT": ("no", 0.9), "Football results": ("no", 0.99),
                   "Ask HN: self-hosting mail?": ("no", 0.9)})
    assert run(source("hacker-news-digest"), {}, host, decider=far).result.startswith("Nothing on Hacker News close")
    close = decider({"A local model beats GPT": ("yes", 0.95), "Football results": ("no", 0.99),
                     "Ask HN: self-hosting mail?": ("yes", 0.9)})
    asked = run(source("hacker-news-digest"), {}, host, decider=close)
    assert asked.status == "asks" and [s["title"] for s in asked.asks["stories"]] == [
        "A local model beats GPT", "Ask HN: self-hosting mail?"]
    assert resume(asked.state, "Two stories for you…", host).result["message"] == "Two stories for you…"


def test_summarize_articles_leaves_out_what_is_not_an_article():
    pages = {"https://a": "<html><script>x()</script><h1>Title</h1><p>Real  text here.</p></html>",
             "https://b": "<html><body>Please log in</body></html>"}
    host = FakeHost({"curl": lambda argv: pages[argv[-1]]})
    small = decider({"https://a": ("yes", 0.95), "https://b": ("no", 0.95)})
    asked = run(source("summarize-articles"), {"urls": ["https://a", "https://b"]}, host, decider=small)
    assert asked.status == "asks" and asked.asks["pages"] == {"https://a": "Title Real text here."}
    assert small.calls[0][2][0] == ("https://a", "Title Real text here.")
    done = resume(asked.state, "https://a: a summary.", host)
    assert done.result["skipped"] == ["https://b"] and done.result["message"] == "https://a: a summary."
    assert "3 URLs at most" in run(source("summarize-articles"), {"urls": list("abcd")}, host).result


def test_hermes_health_stops_when_hermes_itself_cannot_be_read():
    gone = {**HEALTHY, "hermes logs agent --since 30m -n 5000": ("", "hermes: command not found", 127)}
    outcome = run(source("hermes-health"), {}, FakeHost(gone))
    assert outcome.status == "stopped" and "exit 127: hermes: command not found" in outcome.reason
