"""The Hermes plugin: thirty lines at the root of the repository, tried without Hermes."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("habits_plugin", ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("REFLEX_HOME", str(tmp_path / "reflexes"))
    monkeypatch.setenv("REFLEX_COMMAND", str(Path(sys.executable).parent / "reflex"))
    monkeypatch.chdir(tmp_path)
    return module


class Hermes:
    def __init__(self):
        self.sections = {}

    def register_system_prompt_section(self, id, content, *, position, max_chars):
        self.sections[id] = (content, position, max_chars)


def test_a_new_user_is_given_two_reflexes_and_the_agent_is_told_of_them(plugin, tmp_path):
    hermes = Hermes()
    plugin.register(hermes)
    assert sorted(p.name for p in (tmp_path / "reflexes").glob("*.py")) == ["disk-space.py", "hermes-health.py"]
    content, position, most = hermes.sections["habits.reflexes"]
    told = content({"session_id": "s"})
    assert position == "after_memory" and len(told) <= plugin.ROOM <= most
    assert told.startswith("You have reflexes") and "  disk-space() · " in told and "  hermes-health(" in told


def test_the_two_are_given_once_a_folder_emptied_stays_empty(plugin, tmp_path):
    plugin.start()
    for path in (tmp_path / "reflexes").glob("*.py"):
        path.unlink()
    plugin.start()
    assert not list((tmp_path / "reflexes").glob("*.py"))
    assert plugin.note().startswith("You can have reflexes")  # with none, the note says how to have one


def test_without_the_command_the_agent_is_told_nothing_and_nothing_breaks(plugin, monkeypatch, tmp_path):
    monkeypatch.setenv("REFLEX_COMMAND", str(tmp_path / "no-such-command"))
    assert plugin.note() == ""
