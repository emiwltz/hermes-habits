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
    assert lines[1:] == ["already changed before it stopped:", "  mkdir -p ~/made"]


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
