import pytest

from conftest import FakeHost, decider
from reflexes import Refused, describe, resume, run


def reflex(body: str, signature: str = "rx") -> str:
    lines = "\n".join("    " + line for line in body.strip("\n").splitlines())
    return f"def reflex({signature}):\n{lines}\n"


# ---- what a reflex says of itself -----------------------------------------------------------------------------

def test_describe_reads_the_description_and_the_inputs():
    about = describe('def reflex(rx, page, depth=2, *, quiet=False):\n    """Check a page.\n\n    More."""\n')
    assert about["doc"].splitlines()[0] == "Check a page."
    assert about["inputs"] == [{"name": "page", "required": True},
                               {"name": "depth", "required": False, "default": 2},
                               {"name": "quiet", "required": False, "default": False}]
    assert about["rx"] is True


def test_describe_takes_a_reflex_that_imports_rx_instead():
    about = describe("import rx\n\ndef reflex(folder='~'):\n    return rx.sh(['ls', folder])\n")
    assert about["rx"] is False and about["inputs"][0]["name"] == "folder"


@pytest.mark.parametrize("source, said", [
    ("x = 1\n", "no reflex here"),
    ("async def reflex(rx):\n    pass\n", "not async"),
    ("def reflex(rx:\n", "not valid Python"),
])
def test_describe_refuses_what_is_not_a_reflex(source, said):
    with pytest.raises(Refused, match=said):
        describe(source)


# ---- done -----------------------------------------------------------------------------------------------------

def test_a_reflex_returns_its_result(host):
    outcome = run(reflex("return {'message': 'hi ' + who, 'n': n * 2}", "rx, who, n=2"), {"who": "Emi"}, host)
    assert outcome.status == "done" and outcome.result == {"message": "hi Emi", "n": 4}


def test_a_reflex_that_prints_instead_of_returning_still_answers(host):
    outcome = run(reflex("print('disk is fine')"), {}, host)
    assert outcome.status == "done" and outcome.result == "disk is fine"


def test_a_reflex_may_import_rx_instead_of_taking_it():
    host = FakeHost({"ls": "a\nb\n"})
    outcome = run("import rx\n\ndef reflex():\n    return rx.sh(['ls']).splitlines()\n", {}, host)
    assert outcome.result == ["a", "b"]


@pytest.mark.parametrize("inputs, said", [
    ({"nope": 1, "who": "x"}, "unknown input: nope (it takes: who, n)"),
    ({}, "missing input: who (it takes: who, n)"),
])
def test_inputs_that_are_not_its_inputs_stop_it_before_it_runs(host, inputs, said):
    outcome = run(reflex("return 1", "rx, who, n=2"), inputs, host)
    assert outcome.status == "stopped" and outcome.reason == said and outcome.effects == []


def test_python_does_the_computing(host):
    outcome = run(reflex("""
import re, json
found = re.findall(r"(\\d+)%", "disk 93% used")
return json.dumps({"used": int(found[0]), "kinds": sorted({"b": 1, "a": 2})})
"""), {}, host)
    assert outcome.result == '{"used": 93, "kinds": ["a", "b"]}'


# ---- rx.sh ----------------------------------------------------------------------------------------------------

def test_sh_runs_a_read_and_gives_its_output_without_the_last_newline():
    host = FakeHost({"ls -p /home/me/Downloads": "a.txt\nb.txt\n"})
    outcome = run(reflex("return rx.sh(['ls', '-p', '~/Downloads'])"), {}, host)
    assert outcome.result == "a.txt\nb.txt"
    assert host.ran == [["ls", "-p", "/home/me/Downloads"]]
    assert outcome.effects[0]["verb"] == "sh" and outcome.effects[0]["what"] == "ls -p /home/me/Downloads"


def test_sh_takes_a_plain_line_and_splits_it_without_a_shell():
    host = FakeHost({"grep": "x"})
    run(reflex("return rx.sh(\"grep -c 'a|b' notes.txt\")"), {}, host)
    assert host.ran == [["grep", "-c", "a|b", "notes.txt"]]


@pytest.mark.parametrize("call, said", [
    ("rx.sh(['rm', '-rf', 'x'])", "rx.sh only reads, and rm is not known as a command that only reads"),
    ("rx.sh('ls | wc -l')", "without a shell: | is not read"),
    ("rx.sh(['ls', '>', 'out'])", "without a shell: > is not read"),
    ("rx.sh(['echo', '$(whoami)'])", "without a shell"),
    ("rx.sh(['find', '.', '-delete'])", "find -delete writes"),
    ("rx.sh([])", "an empty command"),
    ("rx.sh(42)", "a list of words"),
    ("rx.sh(['ls'], nope=1)", "rx.sh(cmd, ok=(0,), timeout=None, input=None)"),
])
def test_sh_refuses_what_is_not_one_read(host, call, said):
    outcome = run(reflex(f"return {call}"), {}, host)
    assert outcome.status == "stopped" and said in outcome.reason
    assert host.ran == []


def test_a_failing_command_stops_the_run_with_its_error():
    host = FakeHost({"ls": ("", "ls: /nope: No such file or directory", 2)})
    outcome = run(reflex("rx.sh(['ls', '/nope'])\nreturn 'not reached'"), {}, host)
    assert outcome.status == "stopped"
    assert outcome.reason == "RuntimeError: rx.sh ls /nope: exit 2: ls: /nope: No such file or directory (line 2 of the reflex)"


def test_a_reflex_may_catch_a_failure_or_accept_an_exit_code():
    host = FakeHost({"ls": ("", "nope", 2), "grep": ("", "", 1)})
    outcome = run(reflex("""
try:
    rx.sh(['ls', '/nope'])
except RuntimeError as error:
    caught = str(error)
none = rx.sh(['grep', 'x', 'f'], ok=(0, 1))
any_code = rx.sh(['ls', '/nope'], ok=None)
return [caught, none, any_code]
"""), {}, host)
    assert outcome.result == ["rx.sh ls /nope: exit 2: nope", "", ""]


def test_sh_runs_a_list_of_commands_side_by_side_and_keeps_their_order():
    host = FakeHost({"curl": lambda argv: argv[-1][-1] * 3})
    outcome = run(reflex("return rx.sh([['curl', '-s', 'https://a'], ['curl', '-s', 'https://b']])"), {}, host)
    assert outcome.result == ["aaa", "bbb"]
    assert sorted(host.ran) == [["curl", "-s", "https://a"], ["curl", "-s", "https://b"]]
    assert [e["verb"] for e in outcome.effects] == ["sh", "sh"]


def test_sh_can_feed_a_command():
    host = FakeHost({"jq": "1"})
    run(reflex("return rx.sh(['jq', '.a'], input='{\"a\": 1}')"), {}, host)
    assert host.inputs == ['{"a": 1}']


# ---- rx.do, rx.read, rx.write -----------------------------------------------------------------------------------

def test_do_changes_something_and_a_dry_run_only_plans_it():
    source = reflex("""
names = rx.sh(['ls', '~/in']).splitlines()
for name in names:
    rx.do(['mv', '-n', '~/in/' + name, '~/out/'])
return str(len(names)) + ' moved'
""")
    host = FakeHost({"ls": "a b.txt\nc.txt\n"})
    dry = run(source, {}, host, dry=True)
    assert dry.status == "done" and dry.dry and dry.result == "2 moved"
    assert dry.planned == ["mv -n '/home/me/in/a b.txt' /home/me/out/", "mv -n /home/me/in/c.txt /home/me/out/"]
    assert host.ran == [["ls", "/home/me/in"]]
    live = run(source, {}, host)
    assert live.planned == [] and host.ran[-1] == ["mv", "-n", "/home/me/in/c.txt", "/home/me/out/"]


def test_a_failing_change_stops_the_run_and_the_journal_says_what_was_done():
    host = FakeHost({"mkdir": "", "mv": ("", "mv: cannot stat", 1)})
    outcome = run(reflex("rx.do(['mkdir', '-p', 'x'])\nrx.do(['mv', 'a', 'x/'])\nrx.do(['mkdir', 'y'])"), {}, host)
    assert outcome.status == "stopped" and "rx.do mv a x/: exit 1: mv: cannot stat" in outcome.reason
    assert [(e["what"], "error" in e) for e in outcome.effects] == [("mkdir -p x", False), ("mv a x/", True)]


def test_read_and_write_files():
    host = FakeHost(files={"/home/me/state.json": '{"last": "v1"}'})
    source = reflex("""
import json
state = json.loads(rx.read('~/state.json'))
try:
    rx.read('~/nope')
except FileNotFoundError:
    missing = True
rx.write('~/state.json', json.dumps({'last': 'v2'}))
rx.write('~/log.txt', 'seen v2\\n', append=True)
return [state['last'], missing]
""")
    dry = run(source, {}, host, dry=True)
    assert dry.result == ["v1", True] and host.files["/home/me/state.json"] == '{"last": "v1"}'
    assert dry.planned == ["write /home/me/state.json (14 characters)", "append to /home/me/log.txt (8 characters)"]
    run(source, {}, host)
    assert host.files["/home/me/state.json"] == '{"last": "v2"}' and host.files["/home/me/log.txt"] == "seen v2\n"


# ---- rx.ask and rx.up ---------------------------------------------------------------------------------------------

TRIAGE = reflex("""
kinds = rx.ask('What is this note?', ['task', 'idea'], about=notes, context=texts)
for name in notes:
    rx.do(['mv', name, kinds[name] + '/'])
return kinds
""", "rx, notes, texts=None")


def test_ask_with_a_sure_small_model_answers_at_once(host):
    small = decider({"a.txt": ("task", 0.95), "b.txt": ("idea", 0.9)})
    outcome = run(TRIAGE, {"notes": ["a.txt", "b.txt"]}, host, decider=small)
    assert outcome.status == "done" and outcome.result == {"a.txt": "task", "b.txt": "idea"}
    assert small.calls == [("What is this note?", ["task", "idea"], [("a.txt", ""), ("b.txt", "")])]
    assert [e for e in outcome.effects if e["verb"] == "ask"][0]["by"] == "fake"


def test_what_the_small_model_is_unsure_of_goes_up_and_the_run_carries_on(host):
    small = decider({"a.txt": ("task", 0.95), "b.txt": ("idea", 0.4)})
    asked = run(TRIAGE, {"notes": ["a.txt", "b.txt"], "texts": {"b.txt": "hmm"}}, host, decider=small)
    assert asked.status == "asks" and host.ran == []
    assert asked.asks == {"question": "What is this note?", "options": ["task", "idea"],
                          "about": [{"item": "b.txt", "context": "hmm"}]}
    done = resume(asked.state, {"b.txt": "idea"}, host)
    assert done.status == "done" and done.result == {"a.txt": "task", "b.txt": "idea"}
    assert host.ran == [["mv", "a.txt", "task/"], ["mv", "b.txt", "idea/"]]
    assert [e["verb"] for e in done.effects] == ["ask", "do", "do"]


def test_with_no_small_model_every_question_goes_up(host):
    asked = run(TRIAGE, {"notes": ["a.txt"]}, host)
    assert asked.status == "asks" and [a["item"] for a in asked.asks["about"]] == ["a.txt"]
    assert resume(asked.state, "task", host).result == {"a.txt": "task"}


def test_a_small_model_that_fails_is_a_doubt_not_a_crash(host):
    def broken(question, options, items):
        raise TimeoutError("no answer")
    asked = run(TRIAGE, {"notes": ["a.txt"]}, host, decider=broken)
    assert asked.status == "asks" and "nobody (TimeoutError" in asked.effects[0]["by"]


@pytest.mark.parametrize("answer, said", [
    ({"b.txt": "nope"}, "'nope' for 'b.txt' is not one of task, idea"),
    ({}, "no answer for 'b.txt'"),
])
def test_an_answer_that_does_not_fit_asks_again(host, answer, said):
    small = decider({"a.txt": ("task", 0.95)})
    asked = run(TRIAGE, {"notes": ["a.txt", "b.txt"]}, host, decider=small)
    again = resume(asked.state, answer, host)
    assert again.status == "asks" and again.asks["error"] == said and again.state == asked.state
    assert resume(again.state, {"b.txt": "task"}, host).status == "done"


def test_ask_without_options_is_yes_or_no_and_gives_booleans(host):
    small = decider({"deploy now": ("yes", 0.99), "x": ("no", 0.9), "y": ("yes", 0.9)})
    source = reflex("return [rx.ask('Is this urgent?', about='deploy now'), rx.ask('Keep?', about=['x', 'y'])]")
    assert run(source, {}, host, decider=small).result == [True, {"x": False, "y": True}]
    asked = run(source, {}, host)
    assert asked.asks == {"question": "Is this urgent?", "options": ["yes", "no"], "about": [{"item": "deploy now"}]}
    assert resume(asked.state, True, host).status == "asks"  # the second question, in its turn


def test_ask_about_nothing_asks_nothing(host):
    small = decider({})
    assert run(reflex("return rx.ask('Kind?', ['a', 'b'], about=[])"), {}, host, decider=small).result == {}
    assert small.calls == []


def test_up_freezes_the_run_and_carries_on_with_any_answer(host):
    source = reflex("""
rx.do(['touch', 'before'])
answer = rx.up('Which branch should I deploy?', branches=['main', 'dev'])
rx.do(['touch', answer['branch']])
return answer
""")
    asked = run(source, {}, host)
    assert asked.status == "asks" and asked.asks == {"question": "Which branch should I deploy?",
                                                     "branches": ["main", "dev"]}
    assert host.ran == [["touch", "before"]]
    done = resume(asked.state, {"branch": "dev"}, host)
    assert done.result == {"branch": "dev"} and host.ran == [["touch", "before"], ["touch", "dev"]]


def test_a_dry_run_that_asks_stays_dry_after_the_answer(host):
    asked = run(TRIAGE, {"notes": ["a.txt"]}, host, dry=True)
    done = resume(asked.state, "idea", host)
    assert done.dry and done.planned == ["mv a.txt idea/"] and host.ran == []


def test_a_state_that_is_not_a_frozen_run_is_refused(host):
    assert resume(b"junk", "x", host).status == "stopped"


# ---- stopped: errors, budgets, and what the sandbox does not have ---------------------------------------------------

def test_an_error_in_the_reflex_stops_it_with_the_line(host):
    outcome = run(reflex("x = {}\nreturn x['missing']"), {}, host)
    assert outcome.status == "stopped" and outcome.reason == "KeyError: missing (line 3 of the reflex)"


def test_the_budget_of_calls_stops_a_runaway_reflex():
    host = FakeHost({"ls": ""})
    outcome = run(reflex("while True:\n    rx.sh(['ls'])"), {}, host, limits={"effects": 5})
    assert outcome.status == "stopped" and outcome.reason == "budget: more than 5 calls to rx"
    assert len(host.ran) == 5


def test_a_reflex_that_computes_forever_is_cut(host):
    outcome = run(reflex("n = 0\nwhile True:\n    n += 1"), {}, host, limits={"cpu": 1})
    assert outcome.status == "stopped" and "time limit" in outcome.reason


@pytest.mark.parametrize("body, said", [
    ("return open('/etc/passwd').read()", "PermissionError"),
    ("import os\nreturn os.system('echo hi')", "AttributeError"),
    ("import subprocess", "ModuleNotFoundError"),
    ("import socket", "ModuleNotFoundError"),
    ("import os\nreturn list(os.environ)", "not supported"),
    ("return rx.nope(1)", "has no attribute 'nope'"),
])
def test_the_sandbox_has_no_file_no_process_and_no_network_of_its_own(host, body, said):
    outcome = run(reflex(body), {}, host)
    assert outcome.status == "stopped" and said in outcome.reason and host.ran == []


# ---- details that matter to the reflex's author -------------------------------------------------------------------

def test_ask_gives_back_the_callers_own_items_as_keys(host):
    small = decider({"1": ("a", 0.9)})
    asked = run(reflex("return rx.ask('Kind?', ['a', 'b'], about=[1, 2])"), {}, host, decider=small)
    assert asked.asks["about"] == [{"item": 2}]
    done = resume(asked.state, {"2": "b"}, host)
    assert done.result == {1: "a", 2: "b"}


def test_a_host_that_raises_is_a_failed_command_not_a_crash():
    class Broken(FakeHost):
        def run(self, argv, seconds, input=None):
            raise OSError("the disk is gone")
    outcome = run(reflex("return rx.sh(['ls'])"), {}, Broken())
    assert outcome.status == "stopped" and "exit 126: OSError: the disk is gone" in outcome.reason


def test_first_match_reads_as_python_means_it(host):
    outcome = run(reflex("""
xs = [1, 2, 3]
return [next((x for x in xs if x > 1), None), next(x for x in xs), next((x for x in xs if x > 9), 'none')]
"""), {}, host)
    assert outcome.result == [2, 1, "none"]


def test_the_home_folder_a_short_sleep_and_three_pure_helpers_are_there(host):
    outcome = run(reflex("""
import os.path, time, shlex, textwrap, string
time.sleep(0.01)
return [os.path.expanduser('~/x'), shlex.quote('a b'), textwrap.shorten('hello wonderful world', 12), string.digits]
"""), {}, host)
    assert outcome.result == ["/home/me/x", "'a b'", "hello [...]", "0123456789"]


@pytest.mark.parametrize("body, said", [
    ("from pathlib import Path\nreturn Path('/etc/passwd').read_text()", "no files of its own: read with rx.read(path)"),
    ("import os\nreturn os.listdir('/')", "no files of its own"),
    ("import subprocess", "no subprocess in the sandbox: run a command with rx.sh"),
    ("import requests", "no requests in the sandbox: fetch with rx.sh(['curl', '-s', url])"),
    ("from urllib.parse import urlparse", "no urllib in the sandbox"),
])
def test_what_the_sandbox_lacks_says_what_to_use_instead(host, body, said):
    outcome = run(reflex(body), {}, host)
    assert outcome.status == "stopped" and said in outcome.reason


def test_the_journal_says_which_reads_left_the_machine():
    host = FakeHost({"curl": "200", "ls": ""})
    outcome = run(reflex("rx.sh(['curl', '-s', 'https://a'])\nrx.sh(['ls'])"), {}, host)
    assert [e.get("outside", False) for e in outcome.effects] == [True, False]


def test_the_environment_stays_outside(host):
    outcome = run(reflex("import os\nreturn [os.getenv('SECRET_KEY'), os.getenv('HOME')]"), {}, host)
    assert outcome.result == [None, "/home/me"]


def test_the_everyday_modules_are_there_without_an_import(host):
    outcome = run(reflex("""
found = re.findall(r"\\d+", "a1b22")
return [found, json.dumps({"n": math.floor(2.5)}), os.path.basename("/a/b.txt"), collections.Counter("aab")["a"]]
"""), {}, host)
    assert outcome.result == [["1", "22"], '{"n": 2}', "b.txt", 2]


def test_a_name_the_reflex_binds_itself_is_left_alone(host):
    source = "import os.path\nre = 'mine'\njson = 3\n\ndef reflex(rx):\n    return [re, json, os.path.basename('/a/b')]\n"
    assert run(source, {}, host).result == ["mine", 3, "b"]


def test_line_numbers_are_those_of_the_source_the_author_wrote(host):
    source = "import re\n\ndef reflex(rx):\n    x = 1\n    return {}['missing']\n"
    assert run(source, {}, host).reason == "KeyError: missing (line 5 of the reflex)"


def test_a_future_import_stays_first_and_lines_stay_true(host):
    source = ("from __future__ import annotations  # as some write it\n\n"
              "def reflex(rx, n: int = 2) -> dict:\n    found = re.findall('a', 'aa')\n    return {}['missing']\n")
    assert run(source, {}, host).reason == "KeyError: missing (line 5 of the reflex)"


def test_an_error_after_a_resume_still_names_the_authors_line(host):
    asked = run(reflex("answer = rx.up('Which?')\nreturn {}[answer]"), {}, host)
    assert resume(asked.state, "nope", host).reason == "KeyError: nope (line 3 of the reflex)"


def test_up_with_options_takes_only_one_of_them(host):
    asked = run(reflex("return rx.up('Which part?', ['major', 'minor'], commits=['a', 'b'])"), {}, host)
    assert asked.asks == {"question": "Which part?", "options": ["major", "minor"], "commits": ["a", "b"]}
    again = resume(asked.state, "the minor one", host)
    assert again.status == "asks" and again.asks["error"] == "'the minor one' is not one of major, minor"
    assert resume(asked.state, " minor ", host).result == "minor"
