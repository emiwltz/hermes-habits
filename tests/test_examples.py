"""The skills shipped with the plugin: the habits and writing-habits guides and the example habits."""

import json
import os
import re
import subprocess
import time
import textwrap
from pathlib import Path

import pytest

from habits import catalog, deciders, graph, runner
from habits.yaml12 import load as load_yaml
from test_schema import spec_example

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted(p for p in (ROOT / "examples").iterdir() if p.is_dir())
GUIDE = ROOT / "skills" / "writing-habits" / "SKILL.md"
HABITS_GUIDE = ROOT / "skills" / "habits" / "SKILL.md"
GUIDES = (HABITS_GUIDE, GUIDE)
SCANNER_PHRASES = ("ignore previous instructions", "you are now")


YAML_BLOCK = re.compile(r"^ *```yaml\n(.*?)^ *```", re.S | re.M)  # a block may be indented under a list item


def guide_blocks(path: Path = GUIDE) -> list[tuple[str, str]]:
    """Each yaml block of a guide, dedented, with the last **example-name** written before it."""
    text = path.read_text(encoding="utf-8")
    blocks = []
    for match in YAML_BLOCK.finditer(text):
        names = re.findall(r"\*\*([a-z0-9-]+)\*\*", text[:match.start()])
        blocks.append((names[-1] if names else "", textwrap.dedent(match.group(1))))
    return blocks


def all_blocks() -> list[tuple[Path, str, str]]:
    return [(path, name, block) for path in GUIDES for name, block in guide_blocks(path)]


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} has no frontmatter"
    end = text.find("\n---", 3)
    assert end > 0, f"{path}: frontmatter is not closed"
    data = load_yaml(text[4:end])
    assert isinstance(data, dict)
    return data


def test_the_seven_examples_are_shipped():
    assert [p.name for p in EXAMPLES] == [
        "check-links", "disk-space", "hacker-news-digest", "hermes-health", "summarize-articles", "tidy-downloads",
        "watch-hermes-releases"]


@pytest.mark.parametrize("folder", EXAMPLES, ids=lambda p: p.name)
def test_example_habit_loads_and_validates(folder):
    habit = graph.load((folder / "assets" / "reflex.yml").read_text(encoding="utf-8"))
    assert [str(issue) for issue in graph.validate(habit)] == []


@pytest.mark.parametrize("folder", EXAMPLES, ids=lambda p: p.name)
def test_example_skill_has_its_name_and_a_short_description(folder):
    meta = frontmatter(folder / "SKILL.md")
    assert meta.get("name") == folder.name
    description = meta.get("description")
    assert isinstance(description, str) and 0 < len(description) <= 60


def test_the_tidy_example_is_the_example_of_the_spec():
    shipped = graph.load((ROOT / "examples" / "tidy-downloads" / "assets" / "reflex.yml").read_text(encoding="utf-8"))
    assert shipped == spec_example()


def test_the_tidy_example_never_moves_over_a_file():
    shipped = graph.load((ROOT / "examples" / "tidy-downloads" / "assets" / "reflex.yml").read_text(encoding="utf-8"))
    moves = [n["args"]["command"] for n in shipped["nodes"].values() if " mv " in f' {n.get("args", {}).get("command", "")}']
    assert len(moves) == 2 and all("mv -n " in m for m in moves)


def test_the_catalog_finds_every_example_valid():
    found = {habit.name: habit for habit in catalog.examples()}
    assert sorted(found) == [p.name for p in EXAMPLES]
    assert all(habit.valid and habit.origin == "example" for habit in found.values())
    assert found["watch-hermes-releases"].kind == "watch"
    assert found["tidy-downloads"].kind == found["hermes-health"].kind == "request"
    assert found["check-links"].kind == found["summarize-articles"].kind == found["disk-space"].kind == "one-off"


def test_the_writing_guide_has_its_frontmatter():
    meta = frontmatter(GUIDE)
    assert meta == {"name": "writing-habits", "description": "Write Habits reflexes: format, rules, examples"}
    assert len(meta["description"]) <= 60


def test_the_habits_guide_has_its_frontmatter():
    meta = frontmatter(HABITS_GUIDE)
    assert meta == {"name": "habits", "description": "When to call a reflex (habit_run) and read its reply"}
    assert len(meta["description"]) <= 60


@pytest.mark.parametrize("path", [*GUIDES] + [p / "SKILL.md" for p in EXAMPLES], ids=lambda p: p.parent.name)
def test_no_phrase_the_hermes_scanner_flags(path):
    text = path.read_text(encoding="utf-8").lower()
    assert not [phrase for phrase in SCANNER_PHRASES if phrase in text]


@pytest.mark.parametrize("path, name, block", all_blocks(),
                         ids=[f"{p.parent.name}-{i}" for i, (p, _, _) in enumerate(all_blocks())])
def test_every_yaml_block_of_the_guides_is_a_valid_graph(path, name, block):
    """A model copies what it reads: every yaml block of both skills is a whole habits/0.1 graph that validates."""
    habit = graph.load(block)
    assert habit.get("spec") == graph.SPEC, f"{path.parent.name}: a yaml block that is no whole graph"
    assert [str(issue) for issue in graph.validate(habit)] == []


def test_the_guides_hold_their_graphs():
    assert len(guide_blocks(HABITS_GUIDE)) >= 1  # the one-off graph of the six moments
    assert len(guide_blocks(GUIDE)) >= 3  # one template per door: called, message, schedule
    doors = [graph.load(block).get("trigger") for _, block in guide_blocks(GUIDE)]
    assert None in doors and "message" in doors and any(isinstance(d, dict) and "every" in d for d in doors)


def test_a_habit_with_inputs_has_no_trigger():
    """A request habit or a watch is started with no inputs: inputs make a one-off."""
    habits = [graph.load(block) for _, _, block in all_blocks()]
    habits += [graph.load((p / "assets" / "reflex.yml").read_text(encoding="utf-8")) for p in EXAMPLES]
    assert [h for h in habits if h.get("inputs") and "trigger" in h] == []


def test_the_guide_quotes_the_examples_exactly():
    """A block named after a shipped example holds its nodes word for word."""
    shipped_names = {p.name for p in EXAMPLES}
    quoted = [(name, block) for _, name, block in all_blocks() if name in shipped_names]
    assert quoted
    for name, block in quoted:
        shipped = graph.load((ROOT / "examples" / name / "assets" / "reflex.yml").read_text(encoding="utf-8"))
        for node, body in graph.load(block)["nodes"].items():
            assert shipped["nodes"].get(node) == body, f"{name}: node {node} differs from the guide"


@pytest.mark.parametrize("path, most", [(HABITS_GUIDE, 80), (GUIDE, 170)], ids=["habits", "writing-habits"])
def test_the_guides_stay_short(path, most):
    assert len(path.read_text(encoding="utf-8").splitlines()) <= most


def test_the_guide_drafts_never_use_a_guarded_name():
    """The guard matches skill.md, reflex.yml and habit.yaml in any path: a draft named so is refused, or taken for
    a habit."""
    for path in GUIDES:
        spans = re.findall(r"`([^`\n]+)`", path.read_text(encoding="utf-8"))
        drafts = [s for s in spans if "/tmp/" in s]
        assert not [s for s in drafts if re.search(r"(?i)(habit\.yaml|reflex\.yml|skill\.md)", s)], path.parent.name
    loop = GUIDE.read_text(encoding="utf-8")
    assert "/tmp/<name>-draft/draft.yaml" in loop and "/tmp/<name>-draft/procedure.md" in loop


def test_the_guides_teach_no_learning_loop():
    for path in GUIDES:
        text = path.read_text(encoding="utf-8").lower()
        assert not [w for w in ("learned.md", "hermes habits learn", "writes on its own", "proposed") if w in text]


def test_the_guide_says_a_read_only_request_habit_is_promoted():
    # Seen in review: the guide still said a request habit with no write can never be promoted, steering the model
    # to save mac-agents-status or hermes-health as one-offs that the trigger never runs.
    text = " ".join(GUIDE.read_text(encoding="utf-8").split())
    assert "can never be promoted" not in text
    assert "A request habit that only reads is judged against your reply" in text


def test_the_guide_reads_whether_a_phrasing_would_run_from_fired():
    # For a habit whose ask or llm reads {trigger}, trigger.also decides too, so pick and
    # confirm can both pass while nothing fires. A model trying phrasings read "it runs".
    text = " ".join(GUIDE.read_text(encoding="utf-8").split())
    assert "`trigger.fired`" in text and "`trigger.also`" in text
    assert "`trigger.pick` and `trigger.confirm` say whether the habit would run" not in text


# The hero: examples/hermes-health only reads, asks nothing and calls no model, so it answers when the model is down.
HEALTH = graph.load((ROOT / "examples" / "hermes-health" / "assets" / "reflex.yml").read_text(encoding="utf-8"))
# Every end says where it read: one gateway serves every profile from the default home, a profile's own file is
# read too (for a failed state only: its heartbeat is not), and hermes logs reads the active home.
READ = "Read: gateway file, heartbeat and disk of ~/.hermes; gateway file and logs of the active Hermes home."
HEALTHY = {"gateway_state.json 2>/dev/null": "", "-mmin +3": "", "hermes logs agent": "", "https://github.com": "200",
           "hermes logs errors": "", "df -Pk": "41"}


def test_hermes_health_only_reads_and_asks_nothing():
    nodes = HEALTH["nodes"].values()
    assert [n for n in nodes if "do" in n and not runner._reads(n)] == []
    assert [n for n in nodes if "ask" in n or "llm" in n or "use" in n] == []
    assert HEALTH["limits"]["questions"] == HEALTH["limits"]["llm"] == 0


def test_hermes_health_is_trusted():
    # no read prints what a server sent (curl gives a status code only), so a run may answer directly
    commands = [n["args"]["command"] for n in HEALTH["nodes"].values() if n.get("do") == "terminal"]
    assert len(commands) == 6 and not [c for c in commands if runner._server_text(c)]


def health_run(outputs=HEALTHY, fail=()):
    """A run of hermes-health on a fake terminal: each command gets the output of the first key it contains, and a
    command naming a key of fail exits 1 with an error text."""
    def tool(name, args):
        assert name == "terminal"
        command = args["command"]
        tool.commands.append(command)
        if any(key in command for key in fail):
            return json.dumps({"output": "df: ~/.hermes: No such file or directory", "exit_code": 1})
        out = next(value for key, value in outputs.items() if key in command)
        return json.dumps({"output": out, "exit_code": 0})
    tool.commands = []

    def nobody(question, state):
        raise AssertionError(f"hermes-health asked the decision model: {question.text}")
    env = runner.Env(tool=tool, decider=deciders.Chain([deciders.Fake(nobody)]), clock=lambda: 0.0)
    return runner.run(HEALTH, env), tool.commands


def test_hermes_health_healthy_path():
    result, commands = health_run()
    assert (result.status, result.node, result.failed) == ("end", "healthy", False)
    assert result.message.startswith("Hermes looks healthy: ") and "disk 41% used" in result.message
    assert result.counts["questions"] == result.counts["llm"] == 0 and result.doubts == []
    assert len(commands) == 6  # every signal is read before any check
    assert any("[0-9]{4}-" in c for c in commands) and any("%{''http_code}" in c for c in commands)  # left as written


def test_hermes_health_has_two_healthy_ends_with_and_without_recent_errors():
    # Seen on the juror path: a quiet hour ended the message on "Most frequent errors of the last hour, if any:".
    quiet, _ = health_run()
    assert quiet.message == ("Hermes looks healthy: gateway fine (or not running here), no model failure in the last "
                             "30 minutes, internet reachable, disk 41% used. No error in the last hour.\n" + READ)
    errors = "      3 ERROR tools.web: timeout after Ns"
    noisy, _ = health_run({**HEALTHY, "hermes logs errors": errors})
    assert (noisy.status, noisy.node, noisy.failed) == ("end", "healthy_with_errors", False)
    first, *rest = noisy.message.splitlines()
    assert first.startswith("Hermes looks healthy: ") and rest == ["Most frequent errors of the last hour:", errors, READ]
    assert noisy.counts["questions"] == noisy.counts["llm"] == 0


def test_hermes_health_works_with_no_gateway_file():
    # a CLI-only user has no gateway_state.json: the reads print nothing (|| true, /dev/null), the run ends healthy
    result, _ = health_run({**HEALTHY, "gateway_state.json 2>/dev/null": "", "-mmin +3": ""})
    assert (result.status, result.node) == ("end", "healthy")


@pytest.mark.parametrize("key, output, end", [
    ("https://github.com", "000", "internet_attention"),
    ("gateway_state.json 2>/dev/null", '"state":"fatal"', "gateway_attention"),
    ("-mmin +3", "/home/user/.hermes/gateway_state.json", "frozen_attention"),
    ("hermes logs agent", "2026-11-10 10:00:01,123 ERROR agent.turn_recovery: API call failed after 3 retries.",
     "model_attention"),
    ("df -Pk", "93", "disk_attention"),
])
def test_hermes_health_says_what_needs_attention_and_still_ends(key, output, end):
    result, _ = health_run({**HEALTHY, key: output})
    assert (result.status, result.node, result.failed) == ("end", end, False)
    lines = result.message.splitlines()
    assert lines[0].startswith("Hermes needs attention: ") and output in lines[0]  # a one-line verdict first
    assert "" not in lines and "Nothing was changed." in result.message and lines[-1] == READ


def test_hermes_health_does_not_read_a_missing_curl_as_online():
    result, _ = health_run({**HEALTHY, "https://github.com": "bash: curl: command not found"})
    assert (result.status, result.node, result.failed) == ("wake", "help", True)


def bash(command, home, stdin=None, hermes_home=None):
    """Run a read of hermes-health through bash in a fake home; Hermes joins stderr to the output."""
    env = {"HOME": str(home), "PATH": os.environ["PATH"]}
    if hermes_home is not None:
        env["HERMES_HOME"] = str(hermes_home)
    done = subprocess.run(["bash", "-c", command], input=stdin, capture_output=True, text=True, env=env)
    return (done.stdout + done.stderr).strip()


def gateway_file(tmp_path, state, minutes_old=0):
    """A gateway_state.json written the way Hermes writes it: compact JSON (gateway/status.py)."""
    (tmp_path / ".hermes").mkdir()
    path = tmp_path / ".hermes" / "gateway_state.json"
    path.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    then = time.time() - minutes_old * 60
    os.utime(path, (then, then))
    return path


@pytest.mark.parametrize("state, found", [
    ({"gateway_state": "degraded", "platforms": {"telegram": {"state": "fatal"}}},
     '"gateway_state":"degraded"\n"state":"fatal"'),
    ({"gateway_state": "startup_failed"}, '"gateway_state":"startup_failed"'),
    ({"gateway_state": "running", "platforms": {"telegram": {"state": "retrying"}}}, '"state":"retrying"'),
    ({"gateway_state": "running", "platforms": {"telegram": {"state": "connected"}}}, ""),
    # a normal stop: adapters write disconnected and the file stays
    ({"gateway_state": "stopped", "platforms": {"api_server": {"state": "disconnected"}}}, ""),
])
def test_hermes_health_reads_the_gateway_file_hermes_writes(tmp_path, state, found):
    gateway_file(tmp_path, state)
    assert bash(HEALTH["nodes"]["gateway"]["args"]["command"], tmp_path) == found


@pytest.mark.parametrize("state, minutes_old, stale", [
    ("running", 10, True), ("running", 0, False), ("stopped", 10, False),
])
def test_hermes_health_finds_a_frozen_gateway_file(tmp_path, state, minutes_old, stale):
    path = gateway_file(tmp_path, {"gateway_state": state, "pid": os.getpid()}, minutes_old)  # a process that is there
    assert bash(HEALTH["nodes"]["frozen"]["args"]["command"], tmp_path) == (str(path) if stale else "")


def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid  # a process that has ended: its number is free


def test_hermes_health_is_quiet_when_the_gateway_file_says_running_but_its_process_is_gone(tmp_path):
    # A gateway killed or lost in a reboot leaves its file saying running forever: that is no machine in trouble.
    gateway_file(tmp_path, {"gateway_state": "running", "pid": dead_pid()}, 600)
    assert bash(HEALTH["nodes"]["frozen"]["args"]["command"], tmp_path) == ""


def test_hermes_health_counts_only_a_model_call_that_failed_for_good(tmp_path):
    pipe = HEALTH["nodes"]["model"]["args"]["command"].split("|", 1)[1]  # what follows hermes logs agent
    log = ("2026-11-10 10:00:01,123 WARNING agent.turn_recovery: API call failed (attempt 1/3) error_type=x\n"
           "2026-11-10 10:00:02,123 INFO hermes_cli.goals: goal judge: API call failed (x)\n")
    assert bash("cat |" + pipe, tmp_path, log) == ""
    failed = "2026-11-10 10:00:09,123 ERROR agent.turn_recovery: API call failed after 3 retries. | provider=p"
    assert bash("cat |" + pipe, tmp_path, log + failed + "\n") == failed


# Real-shaped agent.log lines (hermes_logging: asctime, level, [session], logger name, message).
PREFIX = "2026-11-10 21:14:03,512 ERROR [20261110_211201_ab12cd] agent.turn_recovery: "
FAILED_FOR_GOOD = {
    "402": PREFIX + "Non-retryable client error: Error code: 402 - {'error': {'message': 'Insufficient credits. "
                    "Add more using https://openrouter.ai/settings/credits', 'code': 402}}",
    "401": PREFIX + "Non-retryable client error: Error code: 401 - {'error': {'message': 'User not found.', "
                    "'code': 401}}",
    "503": PREFIX + "API call failed after 3 retries. HTTP 503: Service Unavailable | provider=openrouter "
                    "model=deepseek/deepseek-chat msgs=14 tokens=~5,120",
}
NOT_A_FAILURE_FOR_GOOD = (
    # a retry, a streaming attempt the retry loop takes over, the auxiliary calls, a platform's own error
    "2026-11-10 21:14:01,100 WARNING [s] agent.turn_recovery: API call failed (402) error_type=billing x summary=y\n"
    "2026-11-10 21:14:01,200 ERROR [s] agent.chat_completion_helpers: Streaming failed before delivery: "
    "Error code: 402\n"
    "2026-11-10 21:14:01,300 WARNING [s] agent.title_generator: Title generation failed: Error code: 402\n"
    "2026-11-10 21:14:01,400 WARNING [s] agent.auxiliary_client: Auxiliary compression: HTTP 402 on openrouter and "
    "all fallbacks exhausted (fallback_chain + main agent model). Raising the primary error.\n"
    "2026-11-10 21:14:01,500 WARNING gateway.platforms.telegram: [Telegram] Non-retryable fatal (401); discarding 2 "
    "held inbound message(s)\n"
    "2026-11-10 21:14:01,600 INFO hermes_cli.goals: goal judge: API call failed (x)\n")


@pytest.mark.parametrize("code", sorted(FAILED_FOR_GOOD))
def test_hermes_health_names_a_model_call_that_failed_without_or_after_retries(tmp_path, code):
    # Seen on 10 Nov: a 402 (credits used up) is not retried, so 'API call failed after' never came: "no failure".
    pipe = HEALTH["nodes"]["model"]["args"]["command"].split("|", 1)[1]
    line = FAILED_FOR_GOOD[code]
    found = bash("cat |" + pipe, tmp_path, NOT_A_FAILURE_FOR_GOOD + line + "\n")
    assert found == line[:300] and f"{code}" in found
    result, _ = health_run({**HEALTHY, "hermes logs agent": found})
    assert (result.status, result.node) == ("end", "model_attention") and found in result.message


def test_hermes_health_ignores_retries_auxiliary_calls_and_platforms(tmp_path):
    pipe = HEALTH["nodes"]["model"]["args"]["command"].split("|", 1)[1]
    assert bash("cat |" + pipe, tmp_path, NOT_A_FAILURE_FOR_GOOD) == ""


def test_hermes_health_prints_only_the_last_failure(tmp_path):
    pipe = HEALTH["nodes"]["model"]["args"]["command"].split("|", 1)[1]
    log = FAILED_FOR_GOOD["503"] + "\n" + FAILED_FOR_GOOD["402"] + "\n"
    assert bash("cat |" + pipe, tmp_path, log) == FAILED_FOR_GOOD["402"][:300]


def test_hermes_health_gives_a_verdict_in_a_profile_or_another_home(tmp_path):
    # Seen live in a profile (HERMES_HOME = the profile folder): the disk read printed that home too, so every run
    # handed back. The disk is the same disk and one gateway serves every profile: the read prints only the number.
    (tmp_path / ".hermes").mkdir()
    disk = HEALTH["nodes"]["disk"]["args"]["command"]
    assert bash(disk, tmp_path).isdigit()
    assert bash(disk, tmp_path, hermes_home=tmp_path / ".hermes").isdigit()
    profile = tmp_path / ".hermes" / "profiles" / "work"
    profile.mkdir(parents=True)
    assert bash(disk, tmp_path, hermes_home=profile).isdigit()
    assert bash(disk, tmp_path, hermes_home=tmp_path / "elsewhere").isdigit()  # a container's home, not created
    # The verdict is read on a stand-in number, as in the tests above: the real read is a number in every home, and
    # which number is this machine's disk (over 89% full it rightly ends on "needs attention").
    result, _ = health_run()
    assert (result.status, result.node, result.failed) == ("end", "healthy", False)
    assert result.message.splitlines()[-1] == READ


def test_hermes_health_reads_the_gateway_file_of_the_active_home_too(tmp_path):
    # A standalone profile (gateway.standalone: true) or a multiplexer launched from a profile writes its own
    # gateway_state.json there, which the default home's file does not show.
    gateway = HEALTH["nodes"]["gateway"]["args"]["command"]
    gateway_file(tmp_path, {"gateway_state": "running", "platforms": {"telegram": {"state": "fatal"}}})
    profile = tmp_path / ".hermes" / "profiles" / "work"
    profile.mkdir(parents=True)
    assert bash(gateway, tmp_path, hermes_home=profile) == '"state":"fatal"'
    (profile / "gateway_state.json").write_text('{"gateway_state":"startup_failed"}', encoding="utf-8")
    assert bash(gateway, tmp_path, hermes_home=profile) == '"gateway_state":"startup_failed"\n"state":"fatal"'
    # the default home named twice (HERMES_HOME pinned to it) prints each finding once
    assert bash(gateway, tmp_path, hermes_home=tmp_path / ".hermes") == '"state":"fatal"'


def test_hermes_health_hands_back_when_the_disk_cannot_be_read():
    result, _ = health_run(fail=("df -Pk",))
    assert (result.status, result.node, result.failed) == ("wake", "help", True)
    assert "Fix nothing without the user's yes" in result.message
@pytest.mark.parametrize("path", GUIDES, ids=lambda p: p.parent.name)
def test_every_fence_of_the_guides_is_a_yaml_block(path):
    """A graph in a ``` or ```yml fence would escape the check that every block is a valid graph."""
    fences = re.findall(r"^ *```", path.read_text(encoding="utf-8"), re.M)
    assert len(fences) == 2 * len(guide_blocks(path))


def test_every_reflex_the_habits_guide_calls_is_listed_in_the_prompt():
    """Only shipped examples that only read are listed as callable reflexes: the guide must name only those."""
    from habits import trigger
    # A reflex's name, not a tool's: `tool_call {calls: [{name: habit_run, …}]}` names the tool itself.
    names = set(re.findall(r"\{name: ([a-z0-9-]+)[,}]", HABITS_GUIDE.read_text(encoding="utf-8")))
    assert names
    shipped = {p.name: graph.load((p / "assets" / "reflex.yml").read_text(encoding="utf-8")) for p in EXAMPLES}
    assert sorted(n for n in names if n not in shipped or not trigger.reads_all(shipped[n])) == []


def check_links(probe, page_links="https://a.example/ok\nhttps://b.example/gone"):
    """A run of the shipped check-links with fake tools: probe(command) gives each link's (output, exit code)."""
    links = graph.load((ROOT / "examples" / "check-links" / "assets" / "reflex.yml").read_text(encoding="utf-8"))

    def tool(name, args):
        command = args["command"]
        if "--max-time 20" in command:
            return json.dumps({"output": page_links, "exit_code": 0})
        output, code = probe(command)
        return json.dumps({"output": output, "exit_code": code})

    env = runner.Env(tool=tool, decider=deciders.Chain([]), clock=lambda: 0.0)
    return runner.run(links, env, {"page": "https://p.example"})


def test_check_links_ends_done_with_the_broken_links_it_found():
    # Seen live: a hand-back on a broken link made the model re-check every link itself (12 tool calls). Finding
    # one is the habit's job done: it ends with the table of each link, in its message and its values.
    result = check_links(lambda command: ("broken" if "gone" in command else "ok", 0))
    assert (result.status, result.node) == ("end", "some") and not result.doubts and not result.failed
    assert result.values == {"status": {"https://a.example/ok": "ok", "https://b.example/gone": "broken"}}
    assert "https://b.example/gone: broken" in result.message and "report" not in result.message
    every = check_links(lambda command: ("ok", 0))
    assert (every.status, every.node) == ("end", "fine") and "https://a.example/ok: ok" in every.message


def test_check_links_tells_a_link_it_could_not_check_from_a_broken_one():
    # Seen live: a site answered 429 to curl and worked in a browser. The habit said "broken", the model did not
    # believe it and re-checked every link (13 model calls instead of 2).
    blocked = check_links(lambda command: ("blocked" if "gone" in command else "ok", 0))
    assert (blocked.status, blocked.node) == ("end", "careful") and not blocked.failed
    assert blocked.message.startswith("No broken link among the absolute links of https://p.example (links with a full http(s) address, 30 at most). Some sites refused an automated check")
    assert "https://b.example/gone: blocked" in blocked.message
    both = check_links(lambda command: ("blocked" if "gone" in command else "broken", 0))
    assert (both.status, both.node) == ("end", "some")  # a broken link wins
    for words in ("ok", "blocked", "broken"):
        assert words in both.message  # the end says what each word means: no reason to check again
    assert "fetched once with curl" in both.message


def test_check_links_lists_the_links_a_reader_can_click_not_every_href():
    # Seen live: a <link rel="preconnect" href="https://…algolia.net"> in the head, a network hint, was reported as a
    # broken link and the model re-checked by hand (8 model calls instead of 2).
    links = graph.load((ROOT / "examples" / "check-links" / "assets" / "reflex.yml").read_text(encoding="utf-8"))
    command = links["nodes"]["links"]["args"]["command"].split("| tr ", 1)[1]
    html = ('<head><link data-rh="true" rel="preconnect" href="https://hint.example" crossorigin="anonymous">'
            '<link rel=preload href=https://hint2.example></head>\n'
            '<a href="https://a">x</a> <a class="x" href="https://b?q=1&href=2">y</a> <a href=https://c>z</a>\n'
            '<a\n  href="https://m.example/x">m</a> <A HREF="https://u.example">u</A>\n')
    done = subprocess.run(["sh", "-c", "tr " + command], input=html, capture_output=True, text=True, timeout=10)
    assert done.stdout.split() == ["https://a", "https://b?q=1&href=2", "https://c", "https://m.example/x",
                                "https://u.example"], done.stderr


@pytest.mark.parametrize("code, word", [
    ("200", "ok"), ("204", "ok"), ("301", "ok"), ("302", "ok"), ("416", "ok"),
    ("401", "blocked"), ("403", "blocked"), ("405", "blocked"), ("429", "blocked"), ("999", "blocked"),
    ("404", "broken"), ("410", "broken"), ("500", "broken"), ("503", "broken"), ("000", "broken"), ("4290", "broken"),
])
def test_check_links_awk_gives_one_word_per_http_code(code, word):
    links = graph.load((ROOT / "examples" / "check-links" / "assets" / "reflex.yml").read_text(encoding="utf-8"))
    command = links["nodes"]["probe"]["args"]["command"]
    awk = command.split("| awk ", 1)[1].strip().replace("{''", "{")  # the '' that keeps {print} from being a template
    done = subprocess.run(["sh", "-c", f"printf %s {code} | awk {awk}"], capture_output=True, text=True, timeout=10)
    assert done.stdout.strip() == word, done.stderr


def test_check_links_hands_back_only_real_trouble():
    assert check_links(lambda command: ("", 0), page_links="").node == "nolinks"  # the page did not load, or no link
    stopped = check_links(lambda command: ("timed out", 124))  # no probe could run at all
    assert (stopped.status, stopped.node) == ("wake", "help")
    # Seen in review: one probe that failed to run became a doubt, and the message still said every link answers.
    one = check_links(lambda command: ("timed out", 124) if "gone" in command else ("ok", 0))
    assert one.status == "wake" and one.node == "some" and "Every link" not in one.message


def test_the_guide_template_probes_links_as_the_shipped_check_links():
    # A server that refuses the one-byte range answers 416: the link exists; a refused script is "blocked", not
    # "broken": in the guide's template too.
    guide = GUIDE.read_text(encoding="utf-8")
    assert '$1 == 416 ? \\"ok\\"' in guide and '\\"blocked\\"' in guide


def test_check_links_probe_is_a_read_that_prints_one_word_per_link():
    links = graph.load((ROOT / "examples" / "check-links" / "assets" / "reflex.yml").read_text(encoding="utf-8"))
    probe = links["nodes"]["probe"]
    assert runner._reads(probe) and runner._reads(links["nodes"]["links"])  # awk with no system, pipe or >: a reader
    assert "-f" not in probe["args"]["command"].split()  # curl prints 000 and goes on: a broken link fails nothing
    assert not graph.validate(links) and not graph.drafting(links)


def test_the_readme_habit_is_the_shipped_disk_space_without_comments():
    shipped = (ROOT / "examples" / "disk-space" / "assets" / "reflex.yml").read_text(encoding="utf-8")
    bare = "\n".join(re.sub(r"\s+#.*$|^#.*$", "", line).rstrip() for line in shipped.splitlines())
    bare = "\n".join(line for line in bare.splitlines() if line) + "\n"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    at = readme.index("This is `examples/disk-space`")
    block = readme[readme.index("```yaml\n", at) + 8:readme.index("```", readme.index("```yaml\n", at) + 8)]
    assert block == bare


def test_the_guides_teach_that_done_with_findings_is_done():
    # Seen live: check-links handed back on a broken link it had found, and the model re-checked every link itself.
    writing = GUIDE.read_text(encoding="utf-8")
    assert "found something ends done" in writing and "hand back only work or a decision that is left" in writing
    assert "done with findings is still done: do not re-check them" in HABITS_GUIDE.read_text(encoding="utf-8")


def test_a_check_is_named_by_what_yes_means():
    # Seen live: the report listed "disk_ok: greater disk: no" on a healthy machine and the model told the user about
    # "a nuance". A check named _ok must have yes as its healthy answer; the disk check asks if the disk is full.
    nodes = HEALTH["nodes"]
    assert "disk_ok" not in nodes and nodes["disk_full"]["then"] == {"yes": "disk_attention", "no": "quiet"}
    for name, node in nodes.items():
        if name.endswith("_ok"):
            assert not node["then"]["yes"].endswith("_attention"), name


def test_tidy_downloads_reads_40_files_at_most_so_a_big_folder_stays_within_the_budget():
    # Seen: 300 files cost 4 steps each, past the 200 steps of a run, and the habit handed back having done nothing.
    tidy = graph.load((ROOT / "examples" / "tidy-downloads" / "assets" / "reflex.yml").read_text(encoding="utf-8"))
    assert "head -n 40" in tidy["nodes"]["list"]["args"]["command"]
    assert "40 files at most" in tidy["nodes"]["done"]["end"] and "40 files at most" in tidy["nodes"]["help"]["wake"]

    def tool(name, args):
        command = args["command"]
        if command.startswith("ls -p"):  # the real list, cut where the command cuts it
            names = [f"file{i}.pdf" for i in range(300)]
            return json.dumps({"output": "\n".join(names[:40] if "head -n 40" in command else names), "exit_code": 0})
        return json.dumps({"output": "Safari.app", "exit_code": 0})

    answers = lambda question, state: ("document", 0.9) if question.kind == "choice" else ("no", 0.9)
    env = runner.Env(tool=tool, decider=deciders.Chain([deciders.Fake(answers)]), clock=lambda: 0.0, dry=True)
    result = runner.run(tidy, env)
    assert (result.status, result.node) == ("end", "done")
    assert len(result.planned) == 40


def test_disk_space_is_one_read_started_by_a_call_and_runs_dry():
    spec = graph.load((ROOT / "examples" / "disk-space" / "assets" / "reflex.yml").read_text(encoding="utf-8"))
    assert "trigger" not in spec and "inputs" not in spec  # started by a call, nothing to give
    reads = [n for n in spec["nodes"].values() if "do" in n]
    assert len(reads) == 1 and runner._reads(reads[0]) and reads[0]["args"]["command"].startswith("df -h ~")
    assert [n for n in spec["nodes"].values() if "ask" in n or "llm" in n or "use" in n] == []
    table = "Filesystem Size Used Avail Capacity\n/dev/disk3s1 460Gi 300Gi 150Gi 67%"

    def tool(name, args):
        tool.commands.append(args["command"])
        return json.dumps({"output": table, "exit_code": 0})
    tool.commands = []

    def nobody(question, state):
        raise AssertionError("disk-space asked the decision model")
    env = runner.Env(tool=tool, decider=deciders.Chain([deciders.Fake(nobody)]), clock=lambda: 0.0)
    result = runner.run(spec, env)
    assert (result.status, result.failed) == ("end", False) and "67%" in result.message
    assert len(tool.commands) == 1 and result.counts["questions"] == result.counts["llm"] == 0
