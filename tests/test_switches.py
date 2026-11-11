"""The two safety switches: permissions (hermes | strict) and model_text (quoted | never), and the test of 0.33.

Each switch is tested for each value, for a test run, for a draft (no saved habit), for a habit with the user's yes and
for one made automatic by the rules. Hermes's approval is faked with the result shapes verified in its source:
a refusal or a pending approval comes back as {"output": "", "exit_code": -1, "error": ..., "status": ...}."""
import json

import pytest

import types

from habits import catalog, deciders, graph, plugin, runner, tools
from test_plugin import FakeCtx, Inline

FLAGGED = """
spec: habits/0.1
start: fix
fallback: stuck
limits: {seconds: 30, tools: 8, questions: 0, llm: 0}
nodes:
  fix: {do: terminal, args: {command: "chmod 600 ~/x"}, next: remove}
  remove: {do: terminal, args: {command: "rm ~/x"}, next: fin}
  fin: {end: "Done."}
  stuck: {wake: "Handed back."}
"""
NAME = "tidy-x"


class Terminal(FakeCtx):
    """A Hermes whose terminal answers with `reply` (a dict) and records every command it was given."""

    def __init__(self, config=None, reply=None):
        super().__init__(config)
        self.reply = reply or {"output": "", "exit_code": 0, "error": None}

    def dispatch_tool(self, name, args):
        self.calls.append((name, args))
        return json.dumps(self.reply)


def build(tmp_path, monkeypatch, text=FLAGGED, config=None, reply=None, by="user", status="active", reach=True):
    """A saved habit, made automatic by `by` (user | rules | None for still learning)."""
    ctx = Terminal(config, reply)
    habits = plugin.Habits(ctx, home=tmp_path / "plugin-data" / "habits")
    skill = tmp_path / "skills" / NAME
    (skill / "assets").mkdir(parents=True)
    (skill / "assets" / "reflex.yml").write_text(text)
    monkeypatch.setattr(catalog, "skills", lambda: [catalog.read(skill / "assets" / "reflex.yml", NAME, "Tidy x", "skill")])
    monkeypatch.setattr(plugin.threading, "Thread", Inline)
    monkeypatch.setattr(plugin, "reachable", lambda: reach)
    habit = catalog.skills()[0]
    habits.ledger.adopt(NAME, habit.version, "asked")
    if by == "user":
        assert habits.ledger.approve(NAME, habit.version, by="user")
    elif by == "rules":
        habits.ledger.set_status(NAME, habit.version, "active", "by the thresholds", by="rules")
    return ctx, habits, habit


def commands(ctx):
    return [args.get("command") for name, args in ctx.calls if name == "terminal"]


def called(habits, **args):
    return json.loads(habits.tool_run({"name": NAME, **args}))


# ---- permissions ---------------------------------------------------------------------------------------------------

def test_hermes_after_yes_in_a_turn(tmp_path, monkeypatch):
    ctx, habits, _ = build(tmp_path, monkeypatch)
    assert habits.setting("permissions") == "hermes"
    reply = called(habits)
    assert reply["done"] is True
    assert commands(ctx) == ["chmod 600 ~/x", "rm ~/x"]  # Hermes's own rules decide: the plugin did not refuse


def test_strict_refuses_beforehand(tmp_path, monkeypatch):
    ctx, habits, _ = build(tmp_path, monkeypatch, config={"permissions": "strict"})
    reply = called(habits)
    assert reply["done"] is False and commands(ctx) == []


@pytest.mark.parametrize("how", ["cli_live", "tick_like"])
def test_never_outside_a_turn(tmp_path, monkeypatch, how):
    """`hermes habits run --live`, `tick` and the message starts call run() without turn=True."""
    ctx, habits, habit = build(tmp_path, monkeypatch)
    result = habits.run(habit, habit.graph, dry=False)
    assert result.status == "wake" and commands(ctx) == []


def test_the_message_start_and_the_tool_differ_only_by_turn(tmp_path, monkeypatch):
    ctx, habits, habit = build(tmp_path, monkeypatch)
    seen = []
    real = habits.run
    monkeypatch.setattr(habits, "run", lambda *a, **k: seen.append(k.get("turn", False)) or real(*a, **k))
    called(habits)
    assert seen == [True]  # habit_run's live run is a turn; the others never pass it


def test_never_for_a_test_run(tmp_path, monkeypatch):
    ctx, habits, habit = build(tmp_path, monkeypatch)
    result = habits.run(habit, habit.graph, dry=True, turn=True)
    assert commands(ctx) == []  # planned, never dispatched
    assert result.planned or result.failed or result.status == "wake"


def test_never_for_a_draft(tmp_path, monkeypatch):
    """A one-off the model wrote, or a draft: no saved habit, so no yes to lean on."""
    ctx, habits, habit = build(tmp_path, monkeypatch, by=None)
    result = habits.run(None, habit.graph, dry=False, turn=True)
    assert result.status == "wake" and commands(ctx) == []
    sent = json.loads(habits.tool_run({"scenario": habit.graph}))
    assert sent["done"] is False and commands(ctx) == []


def test_never_for_a_learning_habit(tmp_path, monkeypatch):
    ctx, habits, habit = build(tmp_path, monkeypatch, by=None)
    result = habits.run(habit, habit.graph, dry=False, turn=True)
    assert result.status == "wake" and commands(ctx) == []


def test_rules_promotion_is_not_a_yes(tmp_path, monkeypatch):
    ctx, habits, habit = build(tmp_path, monkeypatch, by="rules")
    assert habits.ledger.info(NAME)["status"] == "active" and not habits.yes_given(habit)
    result = habits.run(habit, habit.graph, dry=False, turn=True)
    assert result.status == "wake" and commands(ctx) == []


def test_a_yes_for_another_version_is_no_yes(tmp_path, monkeypatch):
    ctx, habits, habit = build(tmp_path, monkeypatch)
    other = types.SimpleNamespace(name=NAME, version="not-the-approved-one")
    assert habits.yes_given(habit) and not habits.yes_given(other)


def test_nobody_to_ask_refuses_all_the_same(tmp_path, monkeypatch):
    """No presence in this context: Hermes's own check would let everything through, so the plugin keeps refusing."""
    ctx, habits, habit = build(tmp_path, monkeypatch, reach=False)
    assert habits.yes_given(habit)
    reply = called(habits)
    assert reply["done"] is False and commands(ctx) == []


def test_reachable_fails_closed_when_hermes_cannot_be_read():
    assert plugin.reachable() is False  # the tests' interpreter cannot import Hermes


def test_reachable_reads_the_presence_of_a_chat(monkeypatch):
    import sys
    import types
    fake = types.SimpleNamespace(_presence=lambda: (None, False, True, False), _unattended_contexts=lambda: [])
    package = types.ModuleType("tools")
    monkeypatch.setitem(sys.modules, "tools", package)
    monkeypatch.setitem(sys.modules, "tools.approval", fake)
    assert plugin.reachable() is True
    fake._presence = lambda: (None, False, False, False)
    assert plugin.reachable() is False  # nobody, and no unattended rule: Hermes would allow silently
    fake._unattended_contexts = lambda: ["cron"]
    assert plugin.reachable() is True  # its configured cron/-q rule decides


@pytest.mark.parametrize("status, left", [("pending_approval", "fix"), ("blocked", "fix")])
def test_pending_approval_and_denial_hand_back(tmp_path, monkeypatch, status, left):
    reply = {"output": "", "exit_code": -1, "error": "" if status == "pending_approval" else "denied", "status": status}
    ctx, habits, _ = build(tmp_path, monkeypatch, reply=reply)
    out = called(habits)
    assert out["done"] is False
    assert any(left in line for line in out["left"])  # the step that was refused is listed
    assert commands(ctx) == ["chmod 600 ~/x"]  # it stopped there: nothing after a refused step


def test_a_value_the_user_typed_stays_one_quoted_piece_under_the_yes(tmp_path, monkeypatch):
    """The yes covers the commands written in the reflex; a value filled in is one quoted word, never more."""
    text = FLAGGED.replace('fix: {do: terminal, args: {command: "chmod 600 ~/x"}, next: remove}',
                           'fix: {do: terminal, args: {command: "echo {trigger}"}, next: remove}').replace(
        'remove: {do: terminal, args: {command: "rm ~/x"}, next: fin}', 'remove: {end: "ok"}')
    ctx, habits, habit = build(tmp_path, monkeypatch, text=text)
    result = habits.run(habit, habit.graph, dry=False, trigger="x; rm -rf ~", turn=True)
    assert result.status == "end" and commands(ctx) == ["echo 'x; rm -rf ~'"]


def test_flagged_lists_every_flagged_command_uncut(tmp_path, monkeypatch):
    push = "git push --force origin main"
    text = FLAGGED.replace("fin: {end", f'ship: {{do: terminal, args: {{command: "{push}"}}, next: fin}}\n  fin: {{end')
    text = text.replace('remove: {do: terminal, args: {command: "rm ~/x"}, next: fin}',
                        'remove: {do: terminal, args: {command: "rm ~/x"}, next: ship}')
    ctx, habits, habit = build(tmp_path, monkeypatch, text=text)
    assert habits.flagged(habit)[:2] == ["chmod 600 ~/x", "rm ~/x"]
    assert habits.flagged(habit)[2:] in ([push], [])  # whole when Hermes's detector flags it; outside Hermes the
    # fallback list does not know git push --force, which is why the card also shows the raw command (0.17)


def test_flagged_is_empty_for_a_plain_reflex(tmp_path, monkeypatch):
    text = FLAGGED.replace("chmod 600 ~/x", "ls ~").replace("rm ~/x", "df -h ~")
    ctx, habits, habit = build(tmp_path, monkeypatch, text=text)
    assert habits.flagged(habit) == []


@pytest.mark.parametrize("permissions, delete_ok", [("hermes", True), ("strict", False)])
def test_the_early_checks_follow_permissions(tmp_path, monkeypatch, permissions, delete_ok):
    ctx, habits, habit = build(tmp_path, monkeypatch, config={"permissions": permissions})
    problems = tools.problems_of(graph.load(FLAGGED), allow=habits.allowances())
    assert (not any("deletes files" in p for p in problems)) is delete_ok


# ---- model_text ----------------------------------------------------------------------------------------------------

ASKS = """
spec: habits/0.1
start: pick
fallback: stuck
limits: {seconds: 30, tools: 8, questions: 0, llm: 2}
nodes:
  pick: {llm: "Name a folder.", schema: {folder: string}, as: pick, next: make}
  make: {do: terminal, args: {command: "TEMPLATE"}, next: fin}
  fin: {end: "Done."}
  stuck: {wake: "Handed back."}
"""


def asks(command):
    return graph.load(ASKS.replace("TEMPLATE", command))


def go(habit, value, **options):
    calls = []

    def tool(name, args):
        calls.append(args.get("command"))
        return json.dumps({"output": "", "exit_code": 0})
    env = runner.Env(tool=tool, decider=deciders.Chain([]), llm=lambda i, t, s: {"folder": value}, **options)
    return runner.run(habit, env, {}), calls


def test_model_text_quoted_fills_one_operand():
    habit = asks("mkdir -p {pick.folder}")
    assert graph.validate(habit, allow_llm_in_command=True) == []
    result, calls = go(habit, "a b; touch x", llm_in_commands=True)
    assert result.status == "end" and calls == ["mkdir -p 'a b; touch x'"]
    for bad in ("-x", "a\nb", "a`b`", "../up"):
        result, calls = go(habit, bad, llm_in_commands=True)
        assert result.status == "wake" and calls == [], bad


def test_a_value_holding_the_word_rm_is_refused_by_the_filled_check():
    habit = asks("echo {pick.folder}")
    # quoted, "x; rm -rf ~" is one word of an echo; but a value that makes the command delete is refused, whatever
    # the deleting switch says
    for value in ("x; rm -rf ~", "rm"):
        result, calls = go(habit, value, llm_in_commands=True, deleting=True)
        assert result.status == "wake", value
    habit = asks("rm -rf {pick.folder}")
    result, calls = go(habit, "x", llm_in_commands=True, deleting=True)
    assert calls == [] and "delete" in result.reason


@pytest.mark.parametrize("value", ["quoted", "never"])
def test_model_text_never_restores_both_checks(value):
    habit = asks("mkdir -p {pick.folder}")
    allow = value == "quoted"
    issues = graph.validate(habit, allow_llm_in_command=allow)
    assert bool(issues) is (not allow)
    if not allow:
        assert "puts a value written by an llm step into a terminal command" in str(issues[0])
    result, calls = go(habit, "photos", llm_in_commands=allow)
    assert (calls == ["mkdir -p photos"]) is allow and (result.status == "end") is allow


CODE_POSITIONS = ["{pick.folder} a", "sed {pick.folder} f", "sed -n {pick.folder}p f", "bash -c {pick.folder}",
                  "python3 -c {pick.folder}", "find . -exec {pick.folder} ;", "rm -rf {pick.folder}",
                  "chmod 777 {pick.folder}", "awk {pick.folder} f", "jq {pick.folder} f", "cat f | {pick.folder}",
                  "xargs {pick.folder}", "eval {pick.folder}", "python3 {pick.folder}", "FOO={pick.folder} ls"]


@pytest.mark.parametrize("command", CODE_POSITIONS)
@pytest.mark.parametrize("allow_llm", [True, False])
@pytest.mark.parametrize("allow_delete", [True, False])
def test_model_text_never_in_a_code_position(command, allow_llm, allow_delete):
    issues = graph.validate(asks(command), allow_llm_in_command=allow_llm, allow_delete=allow_delete)
    assert issues, command  # refused at validation under both values of both switches


MORE_CODE_POSITIONS = [
    "cd ~/proj\\n{pick.folder} --version", "timeout 30 {pick.folder}", "nice -n 5 {pick.folder}",
    "time -p {pick.folder}", "if {pick.folder}; then echo ok; fi", "! {pick.folder}",
    "for f in a b; do {pick.folder} $f; done", "ls | xargs -n 1 {pick.folder}", "awk -F , {pick.folder} data.csv",
    "ls *.csv | xargs awk {pick.folder}", "sed -i '' {pick.folder} notes.md", "nice sed {pick.folder} notes.md",
    "jq --arg k v {pick.folder} data.json", "timeout 30 python3 {pick.folder}", "env LC_ALL=C bash {pick.folder}",
    "eval echo {pick.folder}", "trap {pick.folder} EXIT", "rsync -a -e {pick.folder} a b:c", "echo x\\n\\nsh {pick.folder}",
    "while {pick.folder}; do sleep 1; done", "env -i A=1 {pick.folder}", "nohup {pick.folder} &",
    "xargs -I % {pick.folder} %", "echo done > {pick.folder}", "echo done >> {pick.folder}", "date | tee {pick.folder}",
    "ssh -v -oProxyCommand={pick.folder} host true", "npx {pick.folder}", "echo a 2>{pick.folder}",
]


@pytest.mark.parametrize("command", MORE_CODE_POSITIONS)
@pytest.mark.parametrize("allow_llm", [True, False])
def test_model_text_never_in_a_code_position_reached_another_way(command, allow_llm):
    assert graph.validate(asks(command), allow_llm_in_command=allow_llm, allow_delete=True), command


@pytest.mark.parametrize("command", ["jq --arg k {pick.folder} '.title = $k' note.json", "jq -r --arg k {pick.folder} .a f",
                                     "jq --arg k v . {pick.folder}", "cd ~/proj\\ncat {pick.folder}",
                                     "for f in a b; do echo {pick.folder} $f; done", "grep -c {pick.folder} notes/seen.txt",
                                     "grep -c seen {pick.folder}", "grep -F -- {pick.folder} notes.md",
                                     "if grep -q {pick.folder} notes.md; then echo yes; fi",
                                     "echo {pick.folder} >> ~/notes/log.md", "echo {pick.folder} | head -5",
                                     "printf '%s\\n' {pick.folder} | sort -u", "find {pick.folder} -maxdepth 0 -type f",
                                     "terminal-notifier -title Habits -message {pick.folder}", "mkdir -p ~/notes/{pick.folder}",
                                     "touch ~/notes/{pick.folder}.md", "ln -s a {pick.folder}", "open {pick.folder}",
                                     "echo {pick.folder} 2>/dev/null"])
def test_model_text_is_data_in_an_operand_of_a_program_on_the_list(command):
    assert graph.validate(asks(command), allow_llm_in_command=True) == [], command


# The turned-around rule (last pass): a program NOT on the short list of programs that read an argument as data refuses
# such a value, whatever the spelling: sed, awk and wrappers used to be told apart one by one, and 122 of 205 other
# spellings got through.
NOT_ON_THE_LIST = ["sed -n 1p {pick.folder}", "awk -F , '{{print $1}}' {pick.folder}", "timeout 30 ls {pick.folder}",
                   "nice -n 5 cat {pick.folder}", "sed -i '' s/a/b/ {pick.folder}", "ls | xargs -n 1 echo {pick.folder}",
                   "env LC_ALL=C ls {pick.folder}", "gsed {pick.folder} notes.md", "mawk {pick.folder} d", "yq {pick.folder} d",
                   "sqlite3 notes.db {pick.folder}", "mysql -e {pick.folder}", "git config alias.st {pick.folder}",
                   "tar xf a.tar --to-command={pick.folder}", "rsync -a --rsh={pick.folder} a b:c", "vim +{pick.folder} f",
                   "test {pick.folder} -eq 1", "[[ {pick.folder} -eq 1 ]]", "'env' {pick.folder}", ">/dev/null {pick.folder}",
                   "exec -a x {pick.folder}", "ionice -c3 {pick.folder}", "function f {{ {pick.folder}; }}; f",
                   *("echo {pick.folder} " + "|" + " " + reader for reader in ("/bin/" + "sh", "da" + "sh", "env " + "sh", "python3",
                                                                            "xargs -0 " + "sh -c")),
                   "echo {pick.folder} | tee -a ~/.zshrc",
                   "echo {pick.folder} >> ~/.zshrc", "echo {pick.folder} > /tmp/p.sh; sh /tmp/p.sh",
                   "echo {pick.folder} > ~/Library/LaunchAgents/x.plist", "bash <(echo {pick.folder})",
                   "sh < <(echo {pick.folder})", "eval $(echo {pick.folder})", "$(echo {pick.folder}) --version",
                   "y=$(echo {pick.folder}); $y", "git commit -m {pick.folder}", "docker exec web {pick.folder}",
                   "osascript -e {pick.folder}", "osascript {pick.folder}", "terminal-notifier -execute {pick.folder}",
                   "terminal-notifier -message hi -open {pick.folder}", "find . -name {pick.folder}",
                   "find ~/notes -exec {pick.folder} ;", "jq {pick.folder} f", "jq -f {pick.folder} f", "jq --arg {pick.folder} v .",
                   "jq --from-file={pick.folder} . f", "grep -f {pick.folder} f", "grep --file={pick.folder} f",
                   "printf {pick.folder}", "printf -v {pick.folder} hi", "curl -K {pick.folder}", "curl -o {pick.folder} u",
                   "mv --target-directory={pick.folder} a", "cp -t {pick.folder} a", "open -a {pick.folder}",
                   "echo a > {pick.folder}", "cat < {pick.folder}", "ssh pi cat {pick.folder}", "X={pick.folder} ls"]


@pytest.mark.parametrize("command", NOT_ON_THE_LIST)
@pytest.mark.parametrize("allow_llm", [True, False])
def test_model_text_is_refused_outside_the_programs_that_read_it_as_data(command, allow_llm):
    issues = graph.validate(asks(command.replace("{{", "{").replace("}}", "}")), allow_llm_in_command=allow_llm, allow_delete=True)
    assert issues, command


def test_the_refusal_names_the_program_and_where_such_a_value_may_go():
    issues = graph.validate(asks("gsed {pick.folder} notes.md"), allow_llm_in_command=True)
    text = str(issues[0])
    assert '"gsed"' in text and "operand, after the options" in text and "jq" in text and "mv" in text


def test_the_ordinary_uses_the_code_position_list_wrongly_refused_pass():
    for command in ("jq --arg t {pick.folder} '.title = $t' note.json", "grep -c {pick.folder} ~/notes/seen.txt"):
        assert graph.validate(asks(command), allow_llm_in_command=True) == [], command
    assert graph.validate(asks("docker run --rm alpine echo {pick.folder}"), allow_llm_in_command=True)  # not on the list


# ---- text from a tool's output (a web page, an MCP server, a screen) is marked as an llm's is (finding 31) ------------------

WEB = """
spec: habits/0.1
start: fetch
fallback: stuck
limits: {seconds: 30, tools: 8, questions: 0, llm: 0}
nodes:
  fetch: {do: web_extract, read: true, args: {urls: ["https://x.test"]}, as: page, next: make}
  make: {do: terminal, args: {command: "TEMPLATE"}, next: fin}
  fin: {end: "Done."}
  stuck: {wake: "Handed back."}
"""
WEB_EACH = """
spec: habits/0.1
start: fetch
fallback: stuck
limits: {seconds: 30, tools: 8, questions: 0, llm: 0}
nodes:
  fetch: {do: web_search, read: true, args: {query: "old files"}, as: files, next: make}
  make: {do: terminal, each: files, args: {command: "TEMPLATE"}, next: fin}
  fin: {end: "Done."}
  stuck: {wake: "Handed back."}
"""


def web(command, each=False):
    return graph.load((WEB_EACH if each else WEB).replace("TEMPLATE", command))


@pytest.mark.parametrize("command", ["{page} --version", "bash -c {page}", "python3 -c {page}", "awk {page} f",
                                     "timeout 30 {page}", "eval echo {page}", "cd ~/x\\n{page}"])
@pytest.mark.parametrize("allow_llm", [True, False])
def test_text_read_from_the_web_is_never_code_whatever_the_switches(command, allow_llm):
    assert graph.validate(web(command), allow_llm_in_command=allow_llm, allow_delete=True), command


@pytest.mark.parametrize("allow_delete", [True, False])
def test_text_read_from_the_web_never_fills_a_deleting_command(allow_delete):
    issues = graph.validate(web("rm {item}", each=True), allow_llm_in_command=True, allow_delete=allow_delete)
    assert any("deletes" in str(i) or "written by" in str(i) for i in issues), issues


def test_text_read_from_the_web_is_one_quoted_word_when_quoted():
    assert graph.validate(web("curl -s {page}"), allow_llm_in_command=True) == []
    assert graph.validate(web("curl -s {page}"), allow_llm_in_command=False) == []  # never is about a model's text


def run_web(command, page, each=False, **options):
    habit = web(command, each)
    calls = []

    def tool(name, args):
        if name.startswith("web_"):
            return json.dumps({"output": page, "exit_code": 0})
        calls.append(args.get("command"))
        return json.dumps({"output": "ok", "exit_code": 0})
    env = runner.Env(tool=tool, decider=deciders.Chain([]), **options)
    return runner.run(habit, env, {}), calls


def test_a_deleting_command_is_never_filled_from_a_page_even_with_the_yes():
    result, calls = run_web("rm {item}", ["/Users/sam/Documents/thesis.docx", "/Users/sam/.zshrc"], each=True,
                            llm_in_commands=True, deleting=True)
    assert calls == [] and result.status == "wake", (calls, result.reason)


def test_text_read_from_the_web_is_refused_in_a_code_position_at_run_time_even_if_the_check_was_skipped():
    result, calls = run_web("{page} --version", "touch /tmp/pwned", llm_in_commands=True, deleting=True)
    assert calls == [] and result.status == "wake", (calls, result.reason)
    result, calls = run_web("curl -s {page}", "https://x.test/a", llm_in_commands=True)
    assert calls == ["curl -s https://x.test/a"] and result.status == "end"


def test_taint_goes_through_a_step_that_reads_the_page():
    """grep or jq over a page prints text from the page: one step of laundering does not clean it."""
    habit = graph.load(WEB.replace("TEMPLATE", "echo {page}").replace(
        "  fin: {end", "  again: {do: terminal, args: {command: \"{said} --version\"}, next: fin}\n  fin: {end").replace(
        "args: {command: \"echo {page}\"}, next: fin", "args: {command: \"echo {page}\"}, as: said, next: again"))
    calls = []

    def tool(name, args):
        if name.startswith("web_"):
            return json.dumps({"output": "touch /tmp/pwned", "exit_code": 0})
        calls.append(args.get("command"))
        return json.dumps({"output": "touch /tmp/pwned", "exit_code": 0})
    env = runner.Env(tool=tool, decider=deciders.Chain([]), llm_in_commands=True)
    result = runner.run(habit, env, {})
    assert calls == ["echo 'touch /tmp/pwned'"] and result.status == "wake", (calls, result.reason)


USE = """
spec: habits/0.1
start: sub
fallback: stuck
limits: {seconds: 30, tools: 8, questions: 0, llm: 0}
nodes:
  sub: {use: other, as: s, next: make}
  make: {do: terminal, args: {command: "TEMPLATE"}, next: fin}
  fin: {end: "Done."}
  stuck: {wake: "Handed back."}
"""


@pytest.mark.parametrize("command", ["{s.x} --version", "awk {s.x} data.csv", "bash -c {s.x}", "timeout 30 {s.x}"])
def test_a_value_a_called_reflex_returns_is_never_code(command):
    habit = graph.load(USE.replace("TEMPLATE", command))
    assert graph.validate(habit, allow_llm_in_command=True, allow_delete=True), command


def test_a_value_a_called_reflex_returns_is_still_a_plain_operand():
    habit = graph.load(USE.replace("TEMPLATE", "echo {s.x}"))
    assert graph.validate(habit, allow_llm_in_command=True, allow_delete=True) == []


# ---- the deleting switch at run time -------------------------------------------------------------------------------

def test_deleting_follows_the_env_flag():
    habit = graph.load(FLAGGED)
    for deleting, ran in ((True, ["chmod 600 ~/x", "rm ~/x"]), (False, ["chmod 600 ~/x"])):
        calls = []
        env = runner.Env(tool=lambda n, a: calls.append(a["command"]) or json.dumps({"output": "", "exit_code": 0}),
                         decider=deciders.Chain([]), deleting=deleting)
        result = runner.run(habit, env, {})
        assert calls == ran, deleting


def test_a_dry_run_plans_a_deleting_command_instead_of_refusing_it():
    """A test run runs nothing: with the default permissions it plans the delete, so the reflex can be tried before the
    user's yes. Only a value a model wrote keeps the refusal."""
    habit = graph.load(FLAGGED)
    calls = []
    env = runner.Env(tool=lambda n, a: calls.append(a["command"]) or json.dumps({"output": "", "exit_code": 0}),
                     decider=deciders.Chain([]), dry=True, deleting=False)
    result = runner.run(habit, env, {})
    assert result.status == "end", result.reason
    assert "rm ~/x" not in calls  # nothing is run but a read
    assert any("rm ~/x" in str(p) for p in result.planned), result.planned


def test_a_live_run_without_the_permission_says_what_is_true_not_that_habits_never_delete():
    habit = graph.load(FLAGGED)
    env = runner.Env(tool=lambda n, a: json.dumps({"output": "", "exit_code": 0}), decider=deciders.Chain([]),
                     deleting=False)
    result = runner.run(habit, env, {})
    assert "habits never delete" not in str(result.reason).lower()
    assert "permissions" in str(result.reason)


# ---- MCP steps (0.33) ----------------------------------------------------------------------------------------------

MCP = """
spec: habits/0.1
start: clock
fallback: stuck
limits: {seconds: 30, tools: 4, questions: 0, llm: 0}
nodes:
  clock: {do: mcp__toy__clock, args: {zone: "UTC"}, next: fin}
  fin: {end: "Done."}
  stuck: {wake: "Handed back."}
"""


def test_mcp_step_validates_and_dispatches():
    habit = graph.load(MCP)
    assert graph.validate(habit) == []
    calls = []
    live = runner.Env(tool=lambda n, a: calls.append((n, a)) or json.dumps({"time": "12:00"}), decider=deciders.Chain([]))
    assert runner.run(habit, live, {}).status == "end" and calls == [("mcp__toy__clock", {"zone": "UTC"})]
    calls.clear()
    test_run = runner.Env(tool=lambda n, a: calls.append((n, a)) or "{}", decider=deciders.Chain([]), dry=True)
    result = runner.run(habit, test_run, {})
    assert calls == [] and [p["tool"] for p in result.planned] == ["mcp__toy__clock"]  # planned, not dispatched


# ---- the same list rules the filled command at run time, and more doors count as outside (last pass) ----------------------

@pytest.mark.parametrize("command", ["gsed {page} f", "echo {page} " + "|" + " /bin/" + "sh", "mawk {page} f", "git config alias.x {page}",
                                     "echo {page} >> ~/.zshrc", "test {page} -eq 1"])
def test_text_from_outside_is_refused_at_run_time_outside_the_list(command):
    result, calls = run_web(command, "touch /tmp/pwned", llm_in_commands=True, deleting=True)
    assert calls == [] and result.status == "wake", (command, calls, result.reason)


def test_text_from_outside_fills_an_operand_of_a_listed_program_at_run_time():
    result, calls = run_web("echo {page} | head -1", "hello", llm_in_commands=True)
    assert calls and calls[0].startswith("echo hello | head -1") and result.status == "end", (calls, result.reason)


@pytest.mark.parametrize("node", [
    {"do": "x_search", "args": {"query": "q"}}, {"do": "session_search", "args": {"query": "q"}},
    {"do": "feishu_doc_read", "args": {}}, {"do": "read_terminal", "args": {}}, {"do": "vision_analyze", "args": {}},
    {"do": "video_analyze", "args": {}},
    {"do": "terminal", "args": {"command": "lynx -dump https://x.test"}}, {"do": "terminal", "args": {"command": "http GET https://x.test"}},
    {"do": "terminal", "args": {"command": "ssh pi cat /srv/setup.txt"}}, {"do": "terminal", "args": {"command": "gh issue view 12 --json body"}},
    {"do": "terminal", "args": {"command": "dig +short TXT x.test"}}, {"do": "terminal", "args": {"command": "c''url -s https://x.test"}},
    {"do": "terminal", "args": {"command": "git ls-remote https://x.test/r.git"}},
    {"do": "terminal", "args": {"command": "python3 -c 'import urllib.request as u; print(u.urlopen(\"https://x.test\").read())'"}}])
def test_more_doors_that_bring_other_peoples_text_are_marked_outside(node):
    assert graph.from_outside(node), node


def test_a_plain_local_step_is_not_outside():
    assert not graph.from_outside({"do": "terminal", "args": {"command": "ls ~/notes | sort"}})
    assert not graph.from_outside({"do": "read_file", "args": {"path": "a.md"}})
