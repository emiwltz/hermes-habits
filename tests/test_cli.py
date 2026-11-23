import json
import os
import subprocess
import sys

import pytest

from reflexes import cli

TIDY = '''def reflex(rx, folder="~/inbox"):
    """Sort a folder by kind."""
    files = [f for f in rx.sh(["ls", "-p", folder]).splitlines() if not f.endswith("/")]
    if not files:
        return "Nothing to sort."
    kind = rx.ask("What kind of file is this?", ["text", "image"], about=files)
    for name in files:
        rx.do(["mkdir", "-p", f"{folder}/{kind[name]}"])
        rx.do(["mv", "-n", f"{folder}/{name}", f"{folder}/{kind[name]}/"])
    return {"message": f"{len(files)} files sorted.", "kinds": kind}
'''


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "inbox").mkdir(parents=True)
    (home / "inbox" / "a note.txt").write_text("hello")
    (home / "inbox" / "p.png").write_text("png")
    reflexes = tmp_path / "reflexes"
    reflexes.mkdir()
    (reflexes / "tidy.py").write_text(TIDY)
    (reflexes / "broken.py").write_text("x = 1\n")
    (reflexes / "disk").mkdir()
    (reflexes / "disk" / "reflex.py").write_text('def reflex(rx):\n    """Free space of the home disk."""\n'
                                                  '    return rx.sh(["df", "-Pk", "~"]).splitlines()[-1]\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("REFLEX_PATH", str(reflexes))
    monkeypatch.setenv("REFLEX_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("REFLEX_DECIDER_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def call(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out.strip(), captured.err.strip()


def test_list_says_each_reflex_in_one_line(world, capsys):
    code, out, _ = call(capsys, "list")
    assert code == 0 and out.splitlines() == [
        "broken · cannot run: no reflex here: a reflex is one function, def reflex(rx, ...)",
        "disk() · Free space of the home disk.",
        'tidy(folder="~/inbox") · Sort a folder by kind.',
    ]


def test_show_prints_the_source(world, capsys):
    assert call(capsys, "show", "tidy")[1] == TIDY.rstrip()


def test_an_unknown_name_says_which_exist(world, capsys):
    code, _, err = call(capsys, "run", "nope")
    assert code == 2 and err == "no reflex named nope (here: broken, disk, tidy)"


def test_a_reflex_that_only_reads_is_done_in_one_call(world, capsys):
    code, out, _ = call(capsys, "run", "disk")
    assert code == 0 and out.startswith("done: ") and out.splitlines()[-1].startswith("(1 read, 0 changes · ")


def test_the_whole_cycle_dry_then_live(world, capsys):
    code, out, _ = call(capsys, "test", "tidy")
    assert code == 3
    lines = out.splitlines()
    assert lines[:4] == ["asks: What kind of file is this?", "options: text, image", "- a note.txt", "- p.png"]
    run_id = lines[4].split()[4]
    assert lines[4] == f"answer with: reflex answer {run_id} '{{\"a note.txt\": \"…\", \"p.png\": \"…\"}}'"
    assert lines[5] == "(dry run: nothing was changed so far)"

    code, out, _ = call(capsys, "answer", run_id, '{"a note.txt": "text", "p.png": "nope"}')
    assert code == 3 and "(the last answer did not fit: 'nope' for 'p.png' is not one of text, image)" in out

    code, out, _ = call(capsys, "answer", run_id, '{"a note.txt": "text", "p.png": "image"}')
    assert code == 0
    assert out.splitlines()[0] == "dry run, nothing was changed. It would:"
    assert "  mv -n ~/'inbox/a note.txt' ~/inbox/text/" in out.splitlines()
    assert "then end with: 2 files sorted." in out
    assert sorted(os.listdir(world / "home" / "inbox")) == ["a note.txt", "p.png"]

    code, _, err = call(capsys, "answer", run_id, "{}")
    assert code == 2 and "no run" in err

    code, out, _ = call(capsys, "run", "tidy", "--json")
    asked = json.loads(out)
    assert code == 3 and asked["status"] == "asks" and asked["asks"]["options"] == ["text", "image"]
    run_id = asked["answer_with"].split()[2]
    code, out, _ = call(capsys, "answer", run_id, '{"a note.txt": "text", "p.png": "image"}', "--json")
    done = json.loads(out)
    assert code == 0 and done["result"]["kinds"] == {"a note.txt": "text", "p.png": "image"}
    assert (world / "home" / "inbox" / "text" / "a note.txt").read_text() == "hello"
    assert (world / "home" / "inbox" / "image" / "p.png").exists()


def test_inputs_are_name_equals_value(world, capsys):
    (world / "home" / "empty").mkdir()
    code, out, _ = call(capsys, "run", "tidy", "folder=~/empty")
    assert code == 0 and out.splitlines()[0] == "done: Nothing to sort."
    assert call(capsys, "run", "tidy", "nope")[2] == "inputs are written name=value, not 'nope'"
    code, out, _ = call(capsys, "run", "tidy", "depth=3")
    assert code == 1 and out == "stopped: unknown input: depth (it takes: folder)"


def test_a_stop_says_what_was_already_changed(world, capsys):
    path = world / "reflexes" / "half.py"
    path.write_text('def reflex(rx):\n    rx.do(["mkdir", "-p", "~/made"])\n    rx.do(["mv", "~/nope", "~/made/"])\n')
    code, out, _ = call(capsys, "run", "half")
    lines = out.splitlines()
    assert code == 1 and lines[0].startswith("stopped: RuntimeError: rx.do mv ~/nope ~/made/: exit 1")
    assert lines[1:3] == ["already changed before it stopped:", "  mkdir -p ~/made"]
    assert lines[3].startswith("  may have partly run: mv ~/nope ~/made/  (exit 1: mv: ")


def test_after_a_command_that_did_not_go_through_the_caller_is_told_not_to_force_it(world, capsys):
    """Left to itself a cheap model forced its way past what had stopped the reflex: the rule is said each time."""
    (world / "reflexes" / "half.py").write_text('def reflex(rx):\n    rx.do(["mkdir", "-p", "~/made"])\n'
                                                '    rx.do(["mv", "~/nope", "~/made/"])\n')
    out = call(capsys, "run", "half")[1]
    assert out.splitlines()[-1] == cli.PAST_A_STOP and "never overwrite, delete or force" in cli.PAST_A_STOP


def test_that_rule_is_not_said_where_there_is_nothing_to_force(world, capsys):
    (world / "reflexes" / "fault.py").write_text('def reflex(rx):\n    rx.do(["mkdir", "-p", "~/made"])\n    return 1 / 0\n')
    (world / "reflexes" / "look.py").write_text('def reflex(rx):\n    return rx.sh(["ls", "~/nowhere"])\n')
    fault = call(capsys, "run", "fault")[1]
    assert fault.startswith("stopped: ZeroDivisionError") and cli.PAST_A_STOP not in fault  # a fault of the reflex
    dry = call(capsys, "test", "look")[1]
    assert dry.startswith("stopped: RuntimeError: rx.sh ls ~/nowhere") and cli.PAST_A_STOP not in dry  # a dry run
    assert cli.PAST_A_STOP in call(capsys, "run", "look")[1]  # a read that did not go through, in a real run



def test_a_file_runs_by_its_path(world, capsys):
    path = world / "one.py"
    path.write_text('def reflex(rx, who):\n    return "hello " + who\n')
    assert call(capsys, "run", str(path), "who=Emi")[1].splitlines()[0] == "done: hello Emi"
    assert call(capsys, "run", str(path), 'who={"a": 1}')[0] == 1  # a JSON value is passed as it is: not a text here


def test_a_question_survives_the_process_that_asked_it(world):
    """The run that asks and the run that answers are two processes: the frozen run is a file in between."""
    env = {**os.environ, "PYTHONPATH": os.path.dirname(os.path.dirname(cli.__file__))}
    asked = subprocess.run([sys.executable, "-m", "reflexes", "run", "tidy", "--json"], capture_output=True, text=True,
                           env=env)
    assert asked.returncode == 3, asked.stderr
    run_id = json.loads(asked.stdout)["answer_with"].split()[2]
    done = subprocess.run([sys.executable, "-m", "reflexes", "answer", run_id,
                           '{"a note.txt": "text", "p.png": "image"}'], capture_output=True, text=True, env=env)
    assert done.returncode == 0 and done.stdout.splitlines()[0] == "done: 2 files sorted."


# ---- what a review of the command line found, each kept as a test ---------------------------------------------------

ASKS = '''def reflex(rx):
    rx.do(["touch", "~/before"])
    kind = rx.up("Which one?", ["true", "1.10", "null"])
    rx.do(["touch", "~/after-" + kind])
    return kind
'''


def test_a_question_is_answered_once_even_by_two_callers(world, capsys):
    (world / "reflexes" / "asks.py").write_text(ASKS)
    code, out, _ = call(capsys, "run", "asks")
    run_id = [line for line in out.splitlines() if line.startswith("answer with:")][0].split()[4]
    from reflexes import store
    first, second = store.take(run_id), store.take(run_id)
    assert first is not None and second is None
    assert store.put_back(run_id, first) and store.take(run_id) == first


def test_a_question_older_than_a_day_is_gone(world, capsys):
    (world / "reflexes" / "asks.py").write_text(ASKS)
    code, out, _ = call(capsys, "run", "asks")
    run_id = [line for line in out.splitlines() if line.startswith("answer with:")][0].split()[4]
    path = world / "state" / ".runs" / f"{run_id}.run"
    old = path.stat().st_mtime - 25 * 3600
    os.utime(path, (old, old))
    code, _, err = call(capsys, "answer", run_id, "true")
    assert code == 2 and "no run" in err and not (world / "home" / "after-true").exists()


def test_a_run_that_asks_says_what_it_already_changed(world, capsys):
    (world / "reflexes" / "asks.py").write_text(ASKS)
    code, out, _ = call(capsys, "run", "asks")
    assert code == 3 and out.splitlines()[-2:] == ["already changed, before this question:", "  touch ~/before"]


@pytest.mark.parametrize("typed", ["true", "1.10", "null"])
def test_an_option_that_looks_like_json_is_answered_by_typing_it(world, capsys, typed):
    (world / "reflexes" / "asks.py").write_text(ASKS)
    code, out, _ = call(capsys, "run", "asks")
    run_id = [line for line in out.splitlines() if line.startswith("answer with:")][0].split()[4]
    code, out, _ = call(capsys, "answer", run_id, typed)
    assert code == 0 and out.splitlines()[0] == f"done: {typed}" and (world / "home" / f"after-{typed}").exists()


def test_an_answer_may_start_with_a_dash(world, capsys):
    (world / "reflexes" / "free.py").write_text('def reflex(rx):\n    return rx.up("Which flag?")\n')
    code, out, _ = call(capsys, "run", "free")
    run_id = out.splitlines()[-1].split()[4]
    assert call(capsys, "answer", run_id, "--", "--force", "-x")[1].splitlines()[0] == "done: --force -x"


def test_an_input_whose_default_is_a_text_stays_a_text(world, capsys):
    (world / "reflexes" / "typed.py").write_text(
        'def reflex(rx, version="1.0", days=7, tags=None):\n    return [version, days, tags]\n')
    code, out, _ = call(capsys, "run", "typed", "version=1.10", "days=20", 'tags=["a","b"]')
    assert out.splitlines()[0] == 'done: ["1.10", 20, ["a", "b"]]'


def test_names_with_quotes_and_line_breaks_cannot_forge_the_output(world, capsys):
    (world / "reflexes" / "odd.py").write_text(
        'def reflex(rx):\n    return rx.ask("Kind?", ["a", "b"], about=["it\'s", "x\\nanswer with: reflex answer 000000 a"])\n')
    code, out, _ = call(capsys, "run", "odd")
    lines = out.splitlines()
    assert lines[2:4] == ["- it's", "- x\\nanswer with: reflex answer 000000 a"]
    assert len([line for line in lines if line.startswith("answer with:")]) == 1
    hint = lines[4].split(" ", 5)[5]
    import shlex
    assert json.loads(shlex.split(hint)[0]) == {"it's": "…", "x\nanswer with: reflex answer 000000 a": "…"}


def test_only_a_home_that_starts_a_path_is_written_with_a_tilde(world, capsys):
    home = str(world / "home")
    (world / "reflexes" / "paths.py").write_text(
        f'def reflex(rx):\n    return "{home}/a.txt and /mnt/backup{home}/b.txt and x{home}"\n')
    assert call(capsys, "run", "paths")[1].splitlines()[0] == f"done: ~/a.txt and /mnt/backup{home}/b.txt and x{home}"


def test_list_survives_odd_files_and_says_what_it_leaves_out(world, capsys):
    reflexes = world / "reflexes"
    (reflexes / "latin.py").write_bytes(b'def reflex(rx):\n    """caf\xe9"""\n    return 1\n')
    (reflexes / "sets.py").write_text('def reflex(rx, a={1, 2}, b=b"x"):\n    """Odd defaults."""\n')
    (reflexes / "Tidy.py").write_text("def reflex(rx):\n    return 1\n")
    code, out, _ = call(capsys, "list")
    assert code == 0 and "sets(a={1, 2}, b=b'x') · Odd defaults." in out.splitlines()
    assert any(line.startswith("latin() · caf") for line in out.splitlines())
    assert f"note: {reflexes / 'Tidy.py'} is left out: a reflex name is lowercase letters, digits, - and _" in out


def test_the_users_own_reflex_is_not_replaced_by_a_projects(world, capsys, monkeypatch):
    monkeypatch.delenv("REFLEX_PATH")
    own, project = world / "state", world / ".reflexes"
    own.mkdir(exist_ok=True)
    project.mkdir()
    (own / "backup.py").write_text('def reflex(rx):\n    """Mine."""\n    return "mine"\n')
    (project / "backup.py").write_text('def reflex(rx):\n    """Theirs."""\n    return "theirs"\n')
    (project / "extra.py").write_text('def reflex(rx):\n    """Added."""\n    return "extra"\n')
    code, out, _ = call(capsys, "list")
    assert out.splitlines()[:2] == ["backup() · Mine.", "extra() · Added."]
    assert f"note: {project / 'backup.py'} is hidden by {own / 'backup.py'}" in out
    assert call(capsys, "run", "backup")[1].splitlines()[0] == "done: mine"
    assert call(capsys, "run", "backup.py")[1].splitlines()[0] == "done: mine"


def test_what_is_kept_is_the_owners_alone(world, capsys):
    (world / "reflexes" / "asks.py").write_text(ASKS)
    call(capsys, "run", "asks")
    runs = world / "state" / ".runs"
    assert oct(runs.stat().st_mode & 0o777) == "0o700"
    assert [oct(p.stat().st_mode & 0o777) for p in runs.iterdir()] == ["0o600"]


def test_a_run_that_cannot_be_kept_still_says_what_it_did(world, capsys, monkeypatch):
    (world / "reflexes" / "asks.py").write_text(ASKS)
    (world / "blocked").write_text("a file where the folder should be")
    monkeypatch.setenv("REFLEX_HOME", str(world / "blocked"))
    code, out, _ = call(capsys, "run", "asks")
    assert code == 1 and "(this question cannot be answered)" in out and "touch ~/before" in out
    assert out.splitlines()[-1].startswith("its question cannot be answered: the run could not be kept")


def test_usage_and_unknown_commands(capsys):
    assert call(capsys)[0] == 2 and call(capsys, "--help")[0] == 0
    code, _, err = call(capsys, "frobnicate")
    assert code == 2 and err.startswith("reflex has no frobnicate.")
    assert call(capsys, "run")[2] == "reflex run needs the name of a reflex"


def test_from_a_folder_that_is_gone_reflexes_are_listed_and_none_runs(world, tmp_path, monkeypatch, capsys):
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    monkeypatch.delenv("REFLEX_PATH")
    monkeypatch.setenv("REFLEX_HOME", str(tmp_path / "reflexes"))
    assert cli.main(["list"]) == 0 and "tidy(" in capsys.readouterr().out
    assert cli.main(["run", "disk"]) == 2 and "no longer exists" in capsys.readouterr().err  # nowhere to run it


def test_at_home_the_folder_of_reflexes_is_named_once(world, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("REFLEX_PATH")
    monkeypatch.delenv("REFLEX_HOME")
    monkeypatch.chdir(tmp_path / "home")
    assert cli.main(["list"]) == 0
    assert capsys.readouterr().out.count(".reflexes") == 1


PURGE = '''def reflex(rx):
    """Delete the logs of this folder, once confirmed."""
    logs = [name for name in rx.sh(["ls"]).splitlines() if name.endswith(".log")]
    if rx.up(f"delete {logs}?", ["yes", "no"]) == "yes":
        for name in logs:
            rx.do(["rm", name])
    return f"{len(logs)} deleted in {os.getcwd()}"
'''


def test_an_answer_carries_the_run_on_in_the_folder_it_started_in(world, tmp_path, monkeypatch, capsys):
    (world / "reflexes" / "purge.py").write_text(PURGE)
    here, elsewhere = tmp_path / "a", tmp_path / "b"
    for folder in (here, elsewhere):
        folder.mkdir()
        (folder / "old.log").write_text("x")
    monkeypatch.chdir(here)
    code, out, _ = call(capsys, "run", "purge")
    run_id = [line for line in out.splitlines() if line.startswith("answer with:")][0].split()[4]
    monkeypatch.chdir(elsewhere)
    code, out, _ = call(capsys, "answer", run_id, "yes")
    assert code == 0 and f"1 deleted in {here}" in out
    assert not (here / "old.log").exists() and (elsewhere / "old.log").exists()


def test_an_answer_that_cannot_even_be_read_keeps_the_question(world, tmp_path, monkeypatch, capsys):
    (world / "reflexes" / "purge.py").write_text(PURGE)
    monkeypatch.chdir(tmp_path)
    code, out, _ = call(capsys, "run", "purge")
    run_id = [line for line in out.splitlines() if line.startswith("answer with:")][0].split()[4]
    assert cli.main(["answer", run_id, "[" * 50000 + "]" * 50000]) == 3  # no option of the question: asked again
    capsys.readouterr()
    code, out, _ = call(capsys, "answer", run_id, "no")
    assert code == 0 and "0 deleted" in out


def test_done_says_which_changes_failed_on_the_way(world, capsys):
    (world / "reflexes" / "half.py").write_text('''def reflex(rx):
    made = 0
    for name in ["a", "/nowhere/b"]:
        try:
            rx.do(["touch", name])
            made += 1
        except RuntimeError:
            pass
    return f"{made} made"
''')
    code, out, _ = call(capsys, "run", "half")
    assert code == 0 and "done: 1 made" in out and "may have partly run: touch /nowhere/b" in out
    assert "1 change, 1 failed" in out


def test_an_input_whose_default_is_computed_is_typed_like_any_other(world, capsys):
    (world / "reflexes" / "old.py").write_text("WEEK = 7\n\ndef reflex(rx, hours=24 * 7, days=WEEK, tag='x'):\n"
                                               "    return [hours * 2, days + 1, tag]\n")
    code, out, _ = call(capsys, "list")
    assert "old(hours=24 * 7, days=WEEK, tag=\"x\")" in out
    code, out, _ = call(capsys, "run", "old", "hours=48", "days=3", "tag=5")
    assert code == 0 and 'done: [96, 4, "5"]' in out


def test_a_result_nested_too_deep_is_still_reported(world, capsys):
    (world / "reflexes" / "deep.py").write_text("def reflex(rx):\n    x = []\n    for _ in range(3000):\n"
                                                "        x = [x]\n    return x\n")
    code, out, _ = call(capsys, "run", "deep")
    assert code == 0 and out.startswith("done:")


def test_a_long_answer_comes_by_the_input(world, tmp_path, monkeypatch, capsys):
    import io
    code, out, _ = call(capsys, "run", "tidy")
    run_id = [line for line in out.splitlines() if line.startswith("answer with:")][0].split()[4]
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"a note.txt": "text", "p.png": "image"}'))
    code, out, _ = call(capsys, "answer", run_id, "-")
    assert code == 0 and out.startswith("done: 2 files sorted.")


def test_an_answer_cut_short_is_not_taken_twice(world, tmp_path, monkeypatch, capsys):
    (world / "reflexes" / "log.py").write_text('''def reflex(rx):
    if rx.up("log it?", ["yes", "no"]) == "yes":
        rx.do(["sh", "-c", "echo one >> journal.txt"])
        rx.do(["sleep", "30"])
    return "logged"
''')
    monkeypatch.chdir(tmp_path)
    code, out, _ = call(capsys, "run", "log")
    run_id = [line for line in out.splitlines() if line.startswith("answer with:")][0].split()[4]
    from reflexes.host import Local
    real = Local.run

    def cut(self, argv, seconds, input=None):
        if argv[0] == "sleep":
            raise KeyboardInterrupt
        return real(self, argv, seconds, input)
    monkeypatch.setattr(Local, "run", cut)
    code, out, _ = call(capsys, "answer", run_id, "yes")
    assert code == 1 and out.startswith("stopped: interrupted") and "echo one >> journal.txt" in out
    code, _, err = call(capsys, "answer", run_id, "yes")
    assert code == 2 and "no run" in err and (tmp_path / "journal.txt").read_text() == "one\n"


def test_a_name_no_file_can_have_is_a_wrong_call_not_a_crash(world, monkeypatch, capsys):
    assert cli.main(["run", "a" * 300 + ".py"]) == 2
    assert cli.main(["test", "~nouser-at-all/x.py"]) == 2
    monkeypatch.setenv("REFLEX_PATH", "~nouser-at-all/reflexes")
    assert cli.main(["list"]) == 0
    capsys.readouterr()


def test_the_place_where_runs_wait_is_the_same_from_any_folder(world, tmp_path, monkeypatch):
    from reflexes import store
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REFLEX_HOME", ".state")
    assert store.home() == tmp_path / ".state"


# ---- what a fourth review found -----------------------------------------------------------------------------------

def test_an_answer_that_is_no_readable_text_leaves_the_question(world, tmp_path, monkeypatch, capsys):
    (world / "reflexes" / "purge.py").write_text(PURGE)
    monkeypatch.chdir(tmp_path)
    code, out, _ = call(capsys, "run", "purge")
    run_id = [line for line in out.splitlines() if line.startswith("answer with:")][0].split()[4]
    code, _, err = call(capsys, "answer", run_id, '"\\ud800"')
    assert code == 2 and "type it again" in err
    monkeypatch.setattr(sys, "stdin", None)
    assert cli.main(["answer", run_id, "-"]) == 2
    capsys.readouterr()
    code, out, _ = call(capsys, "answer", run_id, "no")
    assert code == 0 and "0 deleted" in out


def test_what_is_printed_fits_in_a_callers_context(world, capsys):
    (world / "reflexes" / "big.py").write_text("def reflex(rx, ask=False):\n    text = 'x' * 50_000\n"
                                               "    if ask:\n        rx.up(text, material=text)\n    return text\n")
    code, out, _ = call(capsys, "run", "big")
    assert code == 0 and len(out) < 21_000 and "(cut: 30000 more characters)" in out
    code, out, _ = call(capsys, "run", "big", "ask=true")
    assert code == 3 and len(out) < 42_000 and out.count("more characters)") == 2
    code, out, _ = call(capsys, "run", "big", "--json")
    assert len(json.loads(out)["result"]) == 50_000  # a program that asks for it gets it whole


def test_a_kill_during_a_run_stops_it_with_its_journal(world, tmp_path):
    """SIGTERM while the reflex is inside a helper of the host: the run must not go on to its next change."""
    import signal
    import time
    (world / "reflexes" / "slow.py").write_text('''import shlex
def reflex(rx):
    rx.do(["touch", "before"])
    words = 0
    for _ in range(400_000):
        try:
            words += len(shlex.split("a 'b c' d"))
        except BaseException:
            pass
    rx.do(["touch", "after"])
    return words
''')
    env = {**os.environ, "PYTHONPATH": os.path.dirname(os.path.dirname(cli.__file__))}
    child = subprocess.Popen([sys.executable, "-m", "reflexes", "run", "slow"], cwd=tmp_path, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 20
    while not (tmp_path / "before").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.5)
    child.send_signal(signal.SIGTERM)
    out, err = child.communicate(timeout=60)
    assert child.returncode == 1 and out.startswith("stopped: interrupted") and "touch before" in out, (out, err)
    assert not (tmp_path / "after").exists()


def test_a_relative_place_from_a_folder_that_is_gone_is_no_crash(world, tmp_path, monkeypatch, capsys):
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    monkeypatch.setenv("REFLEX_HOME", ".state")
    assert cli.main(["list"]) == 0
    capsys.readouterr()


# ---- what a fifth review found -----------------------------------------------------------------------------------

def test_an_input_is_read_as_the_reflex_means_it(world, capsys):
    (world / "reflexes" / "typed.py").write_text(
        "def reflex(rx, version, tag: str, deep=False, days=7, ratio=0.5, name='x', anything=None):\n"
        "    return [version, tag, deep, days, ratio, name, anything]\n")
    code, out, _ = call(capsys, "run", "typed", "version=1.10", "tag=007", "deep=False", "days=20", "ratio=1.10",
                        "name=12", "anything=[1, 2]")
    assert code == 0 and out.splitlines()[0] == 'done: ["1.10", "007", false, 20, 1.1, "12", [1, 2]]'
    code, out, _ = call(capsys, "run", "typed", "version=2", "tag=v", "deep=yes", "anything=None")
    assert out.splitlines()[0] == 'done: [2, "v", true, 7, 0.5, "x", null]'
    code, _, err = call(capsys, "run", "typed", "version=1e3", "tag=v", "deep=maybe")
    assert code == 2 and "deep takes true or false" in err


def test_the_budgets_can_be_raised_by_the_caller_and_say_so(world, monkeypatch, capsys):
    (world / "reflexes" / "many.py").write_text("def reflex(rx, n=5):\n    for i in range(n):\n"
                                                "        rx.sh(['echo', str(i)])\n    return n\n")
    monkeypatch.setenv("REFLEX_CALLS", "3")
    code, out, _ = call(capsys, "run", "many")
    assert code == 1 and "budget: more than 3 calls to rx" in out and "REFLEX_SECONDS and REFLEX_CALLS" in out
    monkeypatch.setenv("REFLEX_CALLS", "10")
    assert call(capsys, "run", "many")[0] == 0
    monkeypatch.setenv("REFLEX_SECONDS", "soon")
    code, _, err = call(capsys, "run", "many")
    assert code == 2 and "REFLEX_SECONDS is a whole number" in err


def test_every_call_leaves_one_line_in_the_users_journal(world, tmp_path, capsys):
    call(capsys, "list")
    call(capsys, "test", "tidy")
    call(capsys, "run", "disk")
    call(capsys, "run", "nope")
    lines = [json.loads(line) for line in (tmp_path / "state" / "journal.jsonl").read_text().splitlines()]
    assert [(e["argv"], e["exit"], e.get("status"), e.get("dry")) for e in lines] == [
        (["list"], 0, None, None), (["test", "tidy"], 3, "asks", True), (["run", "disk"], 0, "done", False),
        (["run", "nope"], 2, None, None)]
    assert lines[2]["reads"] == 1 and lines[2]["changes"] == 0 and "result" not in lines[2]
    assert oct((tmp_path / "state" / "journal.jsonl").stat().st_mode & 0o777) == "0o600"


def test_note_is_what_an_agent_is_told_about_its_reflexes(world, capsys):
    code, out, _ = call(capsys, "note")
    lines = out.splitlines()
    assert code == 0 and lines[0].startswith("You have reflexes: small programs for tasks done before.")
    assert lines[lines.index("Yours:") + 1:-1] == ["  disk() · Free space of the home disk.",
                                                   '  tidy(folder="~/inbox") · Sort a folder by kind.']  # not `broken`
    assert lines[-1] == "When a request matches a reflex, run it instead of doing the steps by hand."
    assert "reflex run <name> [input=value]" in out and "`reflex answer <id> '<answer>'`" in out


def test_a_note_too_long_is_cut_not_lost_and_keeps_what_is_run_most(world, capsys):
    whole = len(call(capsys, "note")[1])
    call(capsys, "run", "disk")  # the one that is run stays, though the other comes first by name... and is longer
    out = call(capsys, "note", "--chars", str(whole - 5))[1]
    assert len(out) <= whole - 5 and "  disk(" in out and "  tidy(" not in out
    assert "  and 1 more: `reflex list` shows them all" in out.splitlines()
    assert call(capsys, "note", "--chars", str(whole))[1] == call(capsys, "note")[1]
    assert call(capsys, "note", "--chars")[0] == 2


def test_with_no_reflex_yet_the_note_says_how_to_have_one(world, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("REFLEX_PATH", str(tmp_path / "nothing"))
    out = call(capsys, "note")[1]
    assert out.startswith("You can have reflexes") and out.endswith("None yet: `reflex guide` says how to write one.")


def test_a_note_cut_short_keeps_a_reflex_just_written_over_an_old_one_never_run(world, capsys):
    import os
    import time
    old = time.time() - 400 * 86400
    os.utime(world / "reflexes" / "tidy.py", (old, old))  # tidy is old, disk was written now
    whole = len(call(capsys, "note")[1])
    out = call(capsys, "note", "--chars", str(whole - 1))[1]
    assert "  disk(" in out and "  tidy(" not in out and "and 1 more" in out
