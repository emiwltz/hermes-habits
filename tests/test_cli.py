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
    assert "  mv -n '~/inbox/a note.txt' ~/inbox/text/" in out.splitlines()
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
