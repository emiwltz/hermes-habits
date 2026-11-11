"""The e2e files are valid and hold nothing private (they are run by hand, never by pytest)."""

import py_compile
import re
import subprocess
from pathlib import Path

E2E = Path(__file__).parent / "e2e"


def test_shell_scripts_parse():
    for script in E2E.glob("*.sh"):
        assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0, script.name


def test_python_files_compile(tmp_path):
    for script in E2E.glob("*.py"):
        py_compile.compile(str(script), cfile=str(tmp_path / (script.name + "c")), doraise=True)


def test_nothing_private_in_the_e2e_files():
    for path in E2E.iterdir():
        text = path.read_text()
        assert not re.search(r"/Users/\w+", text), path.name


def test_make_history_is_standard_library_only():
    text = (E2E / "make_history.py").read_text()
    imports = set(re.findall(r"^(?:import|from) (\w+)", text, re.M))
    assert imports <= {"json", "sqlite3", "sys", "time"}  # --base is a plain argument: no os, no subprocess


def test_no_e2e_script_deletes_anything():
    """The installer's scan reads these files too: a command that deletes (jury.sh once had one) makes it say caution."""
    for path in E2E.glob("*.sh"):
        for line in path.read_text().splitlines():
            assert not re.match(r"\s*(?:\S+\s+&&\s+)?(?:rm|rmdir|unlink)\b", line), (path.name, line)


def test_make_history_takes_a_base_folder_and_leaves_no_invented_one(tmp_path):
    import sqlite3
    import types
    module = types.ModuleType("make_history")  # run from its text: no bytecode folder is written beside it
    exec(compile((E2E / "make_history.py").read_text(), "make_history.py", "exec"), module.__dict__)
    for name, base in (("a.db", None), ("b.db", str(tmp_path / "proj"))):
        db = sqlite3.connect(tmp_path / name)
        db.executescript("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, model_config TEXT, "
                         "parent_session_id TEXT, started_at REAL, ended_at REAL, end_reason TEXT, cwd TEXT, "
                         "message_count INT, tool_call_count INT);"
                         "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
                         "content TEXT, tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL, "
                         "token_count INT, finish_reason TEXT, reasoning TEXT, reasoning_content TEXT, "
                         "reasoning_details TEXT, codex_reasoning_items TEXT, codex_message_items TEXT);")
        db.commit()
        db.close()
        if base:
            module.build(str(tmp_path / name), 1_800_000_000.0, base, base + "/bin/notes")
        else:
            module.build(str(tmp_path / name), 1_800_000_000.0)
        text = "\n".join(str(row) for row in sqlite3.connect(tmp_path / name).execute(
            "SELECT content, tool_calls FROM messages UNION SELECT cwd, model_config FROM sessions"))
        assert ("/home/someone" in text) == (base is None)  # given a base, no invented folder is left
        assert (base or "/home/someone/proj") + "/TODO.md" in text
        assert (base + "/bin/notes wake" in text) if base else ("~/bin/notes wake" in text)


def test_the_project_the_history_works_in_is_made_for_real(tmp_path):
    base = tmp_path / "proj"
    made = subprocess.run(["bash", str(E2E / "make_project.sh"), str(base)], capture_output=True, text=True)
    assert made.returncode == 0, made.stderr

    def run(*command):
        return subprocess.run(command, capture_output=True, text=True, cwd=base)
    assert run("git", "-C", str(base), "branch", "--show-current").stdout.strip() == "main"
    assert "answer the mail" in (base / "TODO.md").read_text()
    assert "answer the mail" in run(str(base / "bin" / "notes"), "wake").stdout
    assert run("git", "-C", str(base), "pull").returncode == 0
    # a newer make also prints "Entering directory" and "Leaving directory" around the output with -C
    assert "lint ok" in run("make", "-C", str(base), "lint").stdout.splitlines()
    again = subprocess.run(["bash", str(E2E / "make_project.sh"), str(base)], capture_output=True, text=True)
    assert again.returncode == 1 and "already exists" in again.stderr  # it never touches what is there
