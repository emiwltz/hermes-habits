"""Find habits: a skill with assets/HABIT.yaml, plus the examples shipped with the plugin.

Skills are read through Hermes's public skills API. Each habit is loaded once
per file change, and identified by a fingerprint of its graph and of the other
files of its assets/ folder (a helper script): any change to them is a new
version, which starts again in shadow.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import graph
from .yaml12 import load as load_yaml

logger = logging.getLogger(__name__)

HABIT_FILE = Path("assets") / "HABIT.yaml"
# An older version kept a learned habit's text here. Nothing writes it any more; it stays out of the fingerprint
# so such a habit keeps its version.
LEARNED_FILE = Path("assets") / "LEARNED.md"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
KINDS = {"message": "request", "every": "watch"}
# The files of assets/ a version covers: listed on every message, so 50 at most are looked at (in walk order),
# and each counts by its size and its first megabyte. Past that, a change goes unseen. What the system, git or a
# helper's own run leaves there (.DS_Store, ._ files, hidden folders, __pycache__) is not the habit's.
MAX_DEPTH = 3  # runner.MAX_DEPTH: levels of use a run goes
ASSET_FILES = 50
ASSET_BYTES = 1_000_000


@dataclass
class Habit:
    name: str
    description: str
    path: Path  # the HABIT.yaml file
    graph: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    origin: str = "skill"  # skill or example
    files: str = ""  # the fingerprint of the other files of assets/, "" when HABIT.yaml is alone

    @property
    def version(self) -> str:
        """The graph's fingerprint (12), then its description's (8): the trigger picks a habit by its description,
        so a wider one is a new version, in shadow again. Before descriptions counted, a version was the first 12
        alone: the ledger carries such a version over (Ledger.status)."""
        return _version(self, 0) + hashlib.sha256(self.description.encode()).hexdigest()[:8]

    @property
    def kind(self) -> str:
        if not self.graph and self.issues:
            return "?"  # the file could not be read: no trigger to tell
        trigger = self.graph.get("trigger")
        if trigger == "message":
            return "request"
        if isinstance(trigger, dict) and trigger:
            return KINDS.get(next(iter(trigger)), "?")  # a trigger it does not know, such as the old event kind
        return "one-off"

    @property
    def valid(self) -> bool:
        return not self.issues


def _version(habit: Habit, depth: int) -> str:
    """The graph's fingerprint, with its other assets/ files and the versions of the habits it uses (as deep as a
    run goes): a sub-habit saved again, with a write maybe, sends its callers back to shadow. With HABIT.yaml alone
    and no use, it is the graph's alone, as before helper files and sub-habits counted."""
    canonical = json.dumps(habit.graph, sort_keys=True, ensure_ascii=False, default=str)
    nodes = habit.graph.get("nodes") if isinstance(habit.graph, dict) else None
    used = sorted({n["use"] for n in nodes.values() if isinstance(n, dict) and isinstance(n.get("use"), str)}
                  ) if isinstance(nodes, dict) else []
    subs = ""
    for name in used:  # a run refuses a use at the third level: nothing deeper runs
        sub = find(name) if depth + 1 < MAX_DEPTH else None
        subs += f"\nuse {name} " + (_version(sub, depth + 1) if sub is not None else "none")
    return hashlib.sha256((canonical + habit.files + subs).encode()).hexdigest()[:12]


def kind_of(habit: dict) -> str:
    return Habit("", "", Path("."), habit).kind


_cache: dict[str, tuple[tuple, Habit]] = {}


def _assets(path: Path) -> list[tuple[str, Path, int, int]]:
    """The other files of a habit's assets/ folder, as (relative path, path, size, mtime_ns), sorted."""
    folder, found = path.parent, []
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
        for name in sorted(n for n in names if n != ".DS_Store" and not n.startswith("._")):
            file = Path(root) / name
            try:
                stat = file.stat()
            except OSError:  # a broken link
                continue
            if file != path and file != folder / LEARNED_FILE.name:
                found.append((file.relative_to(folder).as_posix(), file, stat.st_size, stat.st_mtime_ns))
            if len(found) == ASSET_FILES:
                return sorted(found)
    return sorted(found)


def _fingerprint(files) -> str:
    digest = hashlib.sha256()
    for relative, file, size, _ in files:
        try:
            with open(file, "rb") as handle:
                data = handle.read(ASSET_BYTES)
        except OSError:  # unreadable: its name and size still count
            data = b""
        digest.update(f"\n{relative}\n{size}\n".encode() + data)
    return "\n" + digest.hexdigest()


def read(path: Path, name: str, description: str, origin: str) -> Habit:
    """Load one HABIT.yaml, from cache when neither it nor another file of its assets/ changed."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:  # a broken link, or a file removed since the folder was listed
        return Habit(name, description, path, issues=[f"format: {exc}"], origin=origin)
    files = _assets(path)
    stamp = (mtime, tuple((relative, size, changed) for relative, _, size, changed in files))
    cached = _cache.get(key)
    if cached and cached[0] == stamp and cached[1].description == description:
        return cached[1]
    habit = Habit(name, description, path, origin=origin, files=_fingerprint(files) if files else "")
    try:
        habit.graph = graph.load(path.read_text(encoding="utf-8"))
        habit.issues = [str(issue) for issue in graph.validate(habit.graph)]
    except (OSError, ValueError) as exc:
        habit.issues = [f"format: {exc}"]
    except Exception as exc:  # a check that failed: the habit stays listed, refused, with why
        logger.warning("habits: could not check %s: %s", path, exc, exc_info=True)
        habit.issues = [f"format: the graph could not be checked ({type(exc).__name__})"]
    _cache[key] = (stamp, habit)
    return habit


def _frontmatter(skill_md: Path) -> dict:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    try:
        data = load_yaml(text[3:end]) if end > 0 else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def examples() -> list[Habit]:
    found = []
    for path in sorted(EXAMPLES.glob("*/assets/HABIT.yaml")):
        folder = path.parent.parent
        meta = _frontmatter(folder / "SKILL.md")
        try:
            found.append(read(path, str(meta.get("name") or folder.name), str(meta.get("description") or ""), "example"))
        except Exception as exc:  # one file never hides the others
            logger.warning("habits: could not read %s: %s", path, exc)
    return found


_warned: set[str] = set()


def _warn_once(message: str, *args):
    key = message % args
    if key not in _warned:
        _warned.add(key)
        logger.warning(message, *args)


def skills() -> list[Habit]:
    """Habits saved as skills: one pass over the skill folders for assets/HABIT.yaml, kept to the
    skills Hermes lists (visible, enabled), so the cost stays flat with hundreds of skills."""
    try:
        from tools.skills_tool import find_all_skills, skill_search_dirs
    except ImportError:  # outside Hermes (tests)
        return []
    try:
        # Keyed by the name Hermes gives: the declared name, or the relative path of a same-tier duplicate.
        visible = {row.get("name") or "": row for row in find_all_skills() if ":" not in (row.get("name") or "")}
        roots, _active = skill_search_dirs()
    except Exception as exc:
        logger.warning("habits: could not list skills: %s", exc)
        return []
    found, seen = [], {}
    for tier, root in roots:
        try:
            paths = sorted(Path(root).glob("**/assets/HABIT.yaml"))
        except OSError:
            continue
        for path in paths:
            folder = path.parent.parent
            relative = folder.relative_to(root)
            if any(part.startswith(".") for part in relative.parts):
                continue  # .archive, .curator_backups and other hidden folders
            name = relative.as_posix() if relative.as_posix() in visible else folder.name
            if name not in visible:  # a SKILL.md whose name differs from its folder
                declared = str(_frontmatter(folder / "SKILL.md").get("name") or "")
                if declared in visible:
                    _warn_once("habits: the skill in %s is named %s: the habit takes that name", folder, declared)
                    name = declared
            row = visible.get(name)
            name = name.rsplit("/", 1)[-1]
            if row is None:  # hidden or disabled
                continue
            if name in seen:  # shadowed by a higher tier, or a second folder of the same name
                if seen[name] == tier:
                    _warn_once("habits: two habits are named %s; %s is left out", name, folder)
                continue
            seen[name] = tier
            try:
                found.append(read(path, name, str(row.get("description") or ""), "skill"))
            except Exception as exc:  # one file never hides the others
                logger.warning("habits: could not read %s: %s", path, exc)
    return found


def folders() -> dict[str, Path]:
    """Name -> folder of every saved habit, from the skill folders alone: cheap enough for a tool guard."""
    try:
        from tools.skills_tool import skill_search_dirs
        roots, _active = skill_search_dirs()
    except Exception:  # outside Hermes (tests) or an older Hermes
        return {}
    found: dict[str, Path] = {}
    for _tier, root in roots:
        try:
            for path in Path(root).glob("**/assets/HABIT.yaml"):
                folder = path.parent.parent
                if not any(part.startswith(".") for part in folder.relative_to(root).parts):
                    found.setdefault(folder.name, folder)
                    declared = str(_frontmatter(folder / "SKILL.md").get("name") or "")
                    if declared:  # skills() lists it by that name when it differs from the folder
                        found.setdefault(declared, folder)
        except (OSError, ValueError):
            continue
    return found


def skill_names() -> set[str] | None:
    """Every skill name Hermes lists, habit or not; None when it cannot say (outside Hermes)."""
    try:
        from tools.skills_tool import find_all_skills
        return {str(row.get("name") or "").rsplit("/", 1)[-1] for row in find_all_skills()}
    except Exception:
        return None


FOOTER = "## Habit\n\nThe Habits plugin"  # what habit_save writes at the end of a habit's SKILL.md


def skill_folder(name: str) -> tuple:
    """(the folder of a visible skill named name, whether habit_save wrote it), or (None, False): its folder name or
    the name its SKILL.md declares. A folder with a SKILL.md and no assets/HABIT.yaml is a habit half saved only
    when that SKILL.md ends as habit_save writes it."""
    try:
        from tools.skills_tool import skill_search_dirs
        roots, _active = skill_search_dirs()
    except Exception:  # outside Hermes (tests) or an older Hermes
        return None, False
    for _tier, root in roots:
        try:
            found = sorted(Path(root).glob("**/SKILL.md"))
        except OSError:
            continue
        for skill_md in found:
            folder = skill_md.parent
            if any(part.startswith(".") for part in folder.relative_to(root).parts):
                continue
            if folder.name != name and str(_frontmatter(skill_md).get("name") or "") != name:
                continue
            try:
                ours = (folder / HABIT_FILE).exists() or FOOTER in skill_md.read_text(encoding="utf-8")
            except OSError:
                ours = False
            return folder, ours
    return None, False


def all_habits() -> list[Habit]:
    """Saved habits first; an example never hides a saved habit of the same name."""
    saved = skills()
    names = {h.name for h in saved}
    return saved + [h for h in examples() if h.name not in names]


def find(name: str) -> Habit | None:
    for habit in all_habits():
        if habit.name == name:
            return habit
    return None
