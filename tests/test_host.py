import os
import time

import pytest

from reflexes import host as hosts
from reflexes import resume, run
from reflexes.host import Local


@pytest.fixture
def local(tmp_path):
    return Local(cwd=str(tmp_path), home=str(tmp_path))


def test_a_command_gives_its_output_its_errors_and_its_exit_code(local):
    assert local.run(["sh", "-c", "echo out; echo err >&2; exit 3"], 5) == ("out\n", "err\n", 3)
    assert local.run(["cat"], 5, "fed") == ("fed", "", 0)
    assert local.run(["no-such-program"], 5)[2] == 127


def test_a_command_that_is_late_is_stopped_and_what_it_said_is_kept(local):
    began = time.monotonic()
    out, err, code = local.run(["sh", "-c", "echo first; sleep 30"], 0.5)
    assert (out, code) == ("first\n", 124) and "timed out" in err and time.monotonic() - began < 5


def test_a_late_command_is_stopped_with_what_it_started(local, tmp_path):
    out, err, code = local.run(["sh", "-c", "(sleep 2; touch later) & echo started; wait"], 0.5)
    assert (out, code) == ("started\n", 124)
    time.sleep(2.5)
    assert not (tmp_path / "later").exists()  # the child of the shell did not outlive it


def test_an_output_without_end_is_stopped_before_it_fills_the_memory(local, monkeypatch):
    monkeypatch.setattr(hosts, "MOST", 100_000)
    began = time.monotonic()
    out, err, code = local.run(["cat", "/dev/zero"], 20)
    assert (out, code) == ("", 125) and "narrow the command" in err and time.monotonic() - began < 5


def test_only_a_regular_file_is_read(local, tmp_path, monkeypatch):
    (tmp_path / "note.txt").write_text("héllo")
    assert local.read("note.txt") == "héllo"
    os.mkfifo(tmp_path / "pipe")
    for path in ("pipe", "/dev/zero", str(tmp_path)):
        with pytest.raises(OSError):
            local.read(path)
    monkeypatch.setattr(hosts, "MOST", 3)
    with pytest.raises(OSError, match="read a part of it"):
        local.read("note.txt")


def test_a_reflex_that_reads_a_pipe_stops_instead_of_waiting(local, tmp_path):
    os.mkfifo(tmp_path / "pipe")
    outcome = run("def reflex(rx):\n    return rx.read('pipe')\n", {}, local, dry=True, limits={"seconds": 3})
    assert outcome.status == "stopped" and "not a regular file" in outcome.reason and outcome.ms < 3000


def test_git_does_not_refresh_its_index_behind_a_read(local):
    assert local.environment()["GIT_OPTIONAL_LOCKS"] == "0"


def test_a_frozen_run_knows_the_folder_it_started_in(local, tmp_path):
    from reflexes.engine import started_in
    asked = run("def reflex(rx):\n    return rx.up('go?') + os.getcwd()\n", {}, local)
    assert started_in(asked.state) == str(tmp_path) and started_in(b"junk") is None
    assert resume(asked.state, "yes ", local).result == "yes " + str(tmp_path)
