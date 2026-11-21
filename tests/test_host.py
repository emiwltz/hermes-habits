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


def test_reading_a_folder_by_mistake_costs_no_open_file(local, tmp_path):
    (tmp_path / "sub").mkdir()
    before = len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None
    for _ in range(50):
        with pytest.raises(OSError):
            local.read("sub")
    assert before is None or len(os.listdir("/proc/self/fd")) == before


def test_a_command_stays_in_its_callers_group_and_can_be_stopped_from_outside(local):
    import threading
    out, _, _ = local.run(["sh", "-c", "ps -o pgid= -p $$"], 5)
    assert int(out) == os.getpgrp()  # a caller that kills our group, or Ctrl-C, reaches the command too
    result = {}
    worker = threading.Thread(target=lambda: result.update(code=local.run(["sleep", "30"], 60)[2]))
    began = time.monotonic()
    worker.start()
    time.sleep(0.3)
    local.stop()
    worker.join(5)
    assert result["code"] != 0 and time.monotonic() - began < 5 and not local.live


# ---- what a fourth review found -----------------------------------------------------------------------------------

def test_a_text_is_written_to_a_regular_file_only(local, tmp_path):
    local.write("notes/a.txt", "one\n")
    local.write("notes/a.txt", "two\n", append=True)
    assert (tmp_path / "notes" / "a.txt").read_text() == "one\ntwo\n"
    local.write("notes/a.txt", "three\n")
    assert (tmp_path / "notes" / "a.txt").read_text() == "three\n"
    os.mkfifo(tmp_path / "pipe")
    began = time.monotonic()
    for path in ("pipe", "/dev/null"):
        with pytest.raises(OSError):
            local.write(path, "x")
    assert time.monotonic() - began < 2  # a pipe nobody reads is refused at once, not waited for


def test_every_command_of_the_process_can_be_stopped_at_once(local, tmp_path):
    import threading
    other, result = Local(cwd=str(tmp_path), home=str(tmp_path)), {}
    workers = [threading.Thread(target=lambda h=h, n=n: result.update({n: h.run(["sleep", "30"], 60)[2]}))
               for n, h in (("a", local), ("b", other))]
    began = time.monotonic()
    for worker in workers:
        worker.start()
    time.sleep(0.3)
    hosts.stop_all()
    for worker in workers:
        worker.join(5)
    assert result["a"] != 0 and result["b"] != 0 and time.monotonic() - began < 5 and not hosts.LIVE


def test_curl_is_run_on_http_and_https_only_wherever_a_redirect_leads(local):
    import shutil
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from reflexes import readers
    assert readers.as_read(["curl", "-sL", "http://a"]) == ["curl", "--proto", "=http,https", "--proto-redir",
                                                            "=http,https", "-sL", "http://a"]
    assert readers.as_read(["ls", "-la"]) == ["ls", "-la"] and local.as_read(["curl", "x"])[1] == "--proto"
    if not shutil.which("curl"):
        pytest.skip("no curl here")

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "ftp://user:secret@127.0.0.1:9/x")
            self.end_headers()

        def log_message(self, *args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        out, err, code = local.run(local.as_read(["curl", "-sSL", "-m", "5", url]), 10)
    finally:
        server.shutdown()
    assert code != 0 and "ftp" in err.lower() and "disabled" in err.lower(), (out, err, code)
