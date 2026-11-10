"""The catalog: which skill folders are habits, under which name and description."""

import os
import sys
import types

import pytest

from habits import catalog, graph, guard, plugin
from test_graph import DEMO


def skill(root, folder, name=None, description="d", graph=DEMO, file="reflex.yml"):
    path = root / folder
    (path / "assets").mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {name or path.name}\ndescription: {description}\n---\n\nSteps.\n")
    (path / "assets" / file).write_text(graph)
    return path


@pytest.fixture
def hermes(tmp_path, monkeypatch):
    """A fake tools.skills_tool: the rows Hermes lists, and one skills folder."""
    rows = []
    module = types.ModuleType("tools.skills_tool")
    module.find_all_skills = lambda: list(rows)
    module.skill_search_dirs = lambda: ([("local", tmp_path)], tmp_path)
    monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.skills_tool", module)
    monkeypatch.setattr(catalog, "_cache", {})
    return tmp_path, rows


def test_a_broken_link_is_an_invalid_habit_not_a_crash(tmp_path):
    (tmp_path / "x" / "assets").mkdir(parents=True)
    link = tmp_path / "x" / "assets" / "reflex.yml"
    link.symlink_to(tmp_path / "nowhere")
    habit = catalog.read(link, "x", "d", "skill")
    assert not habit.valid and habit.issues[0].startswith("format:")


def test_a_habit_saved_by_v01_under_assets_habit_yaml_is_still_found_once(hermes):
    root, rows = hermes
    old = skill(root, "old-timer", file="HABIT.yaml")
    skill(root, "cat/new-one")
    rows += [{"name": "old-timer", "description": "d", "category": None}, {"name": "new-one", "description": "d", "category": "cat"}]
    assert [(h.name, h.path.name) for h in catalog.skills()] == [("new-one", "reflex.yml"), ("old-timer", "HABIT.yaml")]
    assert [h.name for h in catalog.skills()] == ["new-one", "old-timer"]  # again, from the cache: still once each
    assert catalog.find("old-timer").valid and set(catalog.folders()) == {"old-timer", "new-one"}
    assert catalog.skill_folder("old-timer") == (old, True)


def test_a_folder_with_both_files_uses_reflex_yml_and_counts_the_old_one_as_a_file_of_assets(hermes):
    root, rows = hermes
    folder = skill(root, "both")
    other = DEMO.replace("Downloads sorted.", "Sorted.")
    (folder / "assets" / "HABIT.yaml").write_text(other)
    rows.append({"name": "both", "description": "d", "category": None})
    found = catalog.skills()
    assert [(h.name, h.path.name) for h in found] == [("both", "reflex.yml")] and found[0].graph == graph.load(DEMO)
    assert catalog.reflex_file(folder) == folder / "assets" / "reflex.yml"
    assert found[0].files  # the legacy file is one more file of assets/: it is in the version
    (folder / "assets" / "HABIT.yaml").write_text(DEMO)
    assert catalog.skills()[0].version != found[0].version
    (folder / "assets" / "reflex.yml").unlink()  # the new file gone: the old one is the reflex again
    assert [h.path.name for h in catalog.skills()] == ["HABIT.yaml"]
    assert catalog.reflex_file(folder) == folder / "assets" / "HABIT.yaml"
    assert catalog.reflex_file(root / "nothing-yet") == root / "nothing-yet" / "assets" / "reflex.yml"


def test_one_file_that_cannot_be_read_never_hides_the_others(hermes, monkeypatch):
    root, rows = hermes
    for name in ("aaa", "bbb"):
        skill(root, name)
        rows.append({"name": name, "description": "d", "category": None})
    read = catalog.read
    monkeypatch.setattr(catalog, "read", lambda path, *a: 1 / 0 if "aaa" in str(path) else read(path, *a))
    assert [h.name for h in catalog.skills()] == ["bbb"]


def test_two_skills_with_one_name_keep_their_own_description(hermes):
    # Hermes names a same-tier duplicate by its path relative to the skills folder.
    root, rows = hermes
    skill(root, "cat-a/dup", description="from a")
    skill(root, "cat-b/dup", description="from b")
    rows += [{"name": "dup", "description": "from a", "category": "cat-a"},
             {"name": "cat-b/dup", "description": "from b", "category": "cat-b"}]
    found = catalog.skills()
    assert [(h.description, h.path.parent.parent.parent.name) for h in found] == [("from a", "cat-a")]


def test_a_skill_named_apart_from_its_folder_is_still_found(hermes):
    root, rows = hermes
    skill(root, "folder", name="tidying")
    rows.append({"name": "tidying", "description": "d", "category": None})
    assert [h.name for h in catalog.skills()] == ["tidying"]
    assert catalog.folders()["tidying"] == root / "folder"  # the edit guard knows it by that name too
    for args in ({"name": "tidying", "action": "patch", "old_string": "a", "new_string": "b"},
                 {"name": "tidying", "action": "remove_file", "file_path": "assets/reflex.yml"}):
        assert guard.edited_habit("skill_manage", args) == "tidying"


def test_a_graph_that_cannot_be_read_has_no_kind(tmp_path):
    path = tmp_path / "reflex.yml"
    path.write_text("nodes: [unclosed")
    habit = catalog.read(path, "x", "d", "skill")
    assert not habit.valid and habit.kind == "?"


def test_a_habit_whose_start_is_a_list_stays_listed_and_refused(tmp_path):
    path = skill(tmp_path, "tidying", graph=DEMO.replace("start: scan", "start: [scan]"))
    habit = catalog.read(path / "assets" / "reflex.yml", "tidying", "d", "skill")
    assert not habit.valid and "format: start must be the name of a node." in habit.issues


def test_a_check_that_crashes_keeps_the_habit_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "_cache", {})
    path = skill(tmp_path, "tidying")

    def boom(habit):
        raise TypeError("unhashable")
    monkeypatch.setattr(catalog.graph, "validate", boom)
    habit = catalog.read(path / "assets" / "reflex.yml", "tidying", "d", "skill")
    assert not habit.valid and habit.issues == ["format: the graph could not be checked (TypeError)"]


def test_a_habit_that_still_declares_an_event_is_invalid_with_no_kind(tmp_path):
    path = skill(tmp_path, "relance", graph=DEMO.replace("trigger: message", "trigger: {event: kanban_task_blocked}"))
    habit = catalog.read(path / "assets" / "reflex.yml", "relance", "d", "skill")
    assert not habit.valid and habit.kind == "?"  # neither a one-off nor anything that runs
    assert habit.issues == ["format: trigger {event: …} is no longer supported: check on a schedule with "
                            "{every: 30m}, or leave trigger out to run the habit by name."]


def test_a_habit_with_its_graph_alone_keeps_the_version_it_had(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "_cache", {})
    path = skill(tmp_path, "triage") / "assets" / "reflex.yml"
    # DEMO's version before helper files, then before descriptions counted: its first 12 characters.
    assert catalog.read(path, "triage", "d", "skill").version[:12] == "fc9e42872269"
    assert {h.name: h.version[:12] for h in catalog.examples()} == {
        "check-links": "888abdac64c9", "hacker-news-digest": "309aa4cfdcfb", "hermes-health": "548d7f1d2c2d",
        "summarize-articles": "2da2465e306a", "tidy-downloads": "ab2144000dd1", "watch-hermes-releases": "f39884296f73"}


def test_a_helper_file_in_assets_is_part_of_the_version(tmp_path, monkeypatch):
    # A graph may call a helper script in its own assets/: a changed script is a new version, measured in shadow again.
    monkeypatch.setattr(catalog, "_cache", {})
    assets = skill(tmp_path, "triage") / "assets"
    path = assets / "reflex.yml"

    def version():
        return catalog.read(path, "triage", "d", "skill").version
    alone = version()
    (assets / "move.sh").write_text("mv -n a b\n")
    helper = version()
    (assets / "move.sh").write_text("mv -f a b\n")  # same size, same graph: the cache still notices
    os.utime(assets / "move.sh", ns=(1, 2))
    changed = version()
    assert len({alone, helper, changed}) == 3
    (assets / ".DS_Store").write_bytes(b"x")  # what the system or git leaves there is not the habit's
    (assets / "._move.sh").write_bytes(b"x")
    (assets / ".git").mkdir()
    (assets / ".git" / "HEAD").write_text("x")
    # Nor what a helper leaves when it runs: python3 writes __pycache__ next to a module it imports (seen in review).
    (assets / "__pycache__").mkdir()
    (assets / "__pycache__" / "regles.cpython-314.pyc").write_bytes(b"x")
    assert version() == changed
    (assets / ".move.sh").write_text("mv -n a b\n")  # a hidden helper the graph calls is the habit's
    hidden = version()
    (assets / ".move.sh").write_text("rm -f a b\n")
    assert len({changed, hidden, version()}) == 3
    (assets / ".move.sh").unlink()
    assert version() == changed
    (assets / "lib").mkdir()
    (assets / "lib" / "common.sh").write_text("x")
    assert version() != changed
    (assets / "lib" / "common.sh").unlink()
    assert version() == changed


def test_the_files_a_version_reads_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "_cache", {})
    monkeypatch.setattr(catalog, "ASSET_FILES", 3)
    monkeypatch.setattr(catalog, "ASSET_BYTES", 10)
    assets = skill(tmp_path, "triage") / "assets"
    path = assets / "reflex.yml"
    for name in "abcd":
        (assets / f"{name}.txt").write_text("0123456789xyz")
    before = catalog.read(path, "triage", "d", "skill").version
    (assets / "a.txt").write_text("0123456789XYZ")  # past the first 10 bytes: only the size counts
    (assets / "d.txt").write_text("changed")  # the 4th file: past the cap
    assert catalog.read(path, "triage", "d", "skill").version == before
    (assets / "b.txt").write_text("9123456789xyz")
    assert catalog.read(path, "triage", "d", "skill").version != before


def test_the_description_is_part_of_the_version(tmp_path, monkeypatch):
    # The trigger picks a habit by its description: a wider one is a habit that answers other asks, so it learns again.
    monkeypatch.setattr(catalog, "_cache", {})
    path = skill(tmp_path, "triage") / "assets" / "reflex.yml"
    narrow = catalog.read(path, "triage", "Sort my downloads", "skill")
    wide = catalog.read(path, "triage", "Any question about the Mac", "skill")
    assert narrow.version != wide.version and narrow.version[:12] == wide.version[:12]  # the same graph


def test_a_version_from_before_the_description_counted_keeps_its_state_and_runs(tmp_path, monkeypatch):
    from habits.ledger import Ledger
    from habits.runner import Result
    monkeypatch.setattr(catalog, "_cache", {})
    habit = catalog.read(skill(tmp_path, "triage") / "assets" / "reflex.yml", "triage", "Sort", "skill")
    ledger = Ledger(tmp_path / "habits.db")
    old = habit.version[:12]  # how the plugin named this graph before
    ledger.set_status("triage", old, "active", "100% over 12 runs", by="rules")
    run = ledger.record("triage", old, "request", "active", Result("end", "ok", "e", planned=[{}]))
    assert ledger.status("triage", habit.version) == "active"
    assert ledger.info("triage")["version"] == habit.version and ledger.runs("triage")[0]["id"] == run
    assert ledger.runs("triage")[0]["version"] == habit.version
    assert ledger.status("triage", habit.version[:12] + "0" * 8) == "shadow"  # another description: a new version


def test_an_old_learned_text_in_assets_is_no_part_of_the_version(hermes):
    # An older version kept a learned habit's text in assets/LEARNED.md. Nothing reads it now: the SKILL.md's
    # description is the one the habit goes by, and the file leaves the version as it was.
    root, rows = hermes
    folder = skill(root, "agents", description="How are my agents doing")
    rows.append({"name": "agents", "description": "How are my agents doing", "category": None})
    [habit] = catalog.skills()
    alone = habit.version
    (folder / "assets" / "LEARNED.md").write_text("---\nname: agents\ndescription: \"Autre chose\"\n---\n\n1. hc ls\n")
    catalog._cache.clear()
    [habit] = catalog.skills()
    assert habit.description == "How are my agents doing" and habit.version == alone
