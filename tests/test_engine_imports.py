"""The engine imports nothing from Hermes: graph, runner, shell, deciders, yaml12 and dream run in a
bare interpreter. Locks a property that held at b3494b0 (the plan, 4.4)."""
import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ["graph", "runner", "shell", "deciders", "yaml12", "dream"]
HERMES = ("gateway", "tools", "agent", "hermes_cli", "plugins", "hermes_state", "hermes_constants", "model_tools")
ALLOWED = set(sys.stdlib_module_names) | {"yaml", "habits"}
engine = ENGINE


def test_the_engine_is_all_there():
    assert all((ROOT / "habits" / f"{name}.py").exists() for name in engine)


def test_engine_imports_nothing_from_hermes():
    script = textwrap.dedent(f"""
        import importlib.abc, importlib.machinery, json, sys
        sys.path.insert(0, {str(ROOT)!r})

        class Refuse(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in {HERMES!r}:
                    raise ImportError("the engine asked for Hermes: " + name)
                return None

        sys.meta_path.insert(0, Refuse())
        import importlib
        for name in {engine!r}:
            importlib.import_module("habits." + name)
        from habits import deciders, graph, runner, yaml12
        habit = graph.load(open({str(ROOT / "examples" / "hermes-health" / "assets" / "reflex.yml")!r}).read())
        assert graph.validate(habit) == [], graph.validate(habit)
        calls = []
        def tool(name, args):
            calls.append(name)
            return json.dumps({{"output": "", "exit_code": 0}})
        result = runner.run(habit, runner.Env(tool=tool, decider=deciders.Chain([])), {{}})
        assert result.status in ("end", "wake") and calls, (result.status, calls)
        assert not [m for m in sys.modules if m.split(".")[0] in {HERMES!r}]
        print("ok", result.status)
    """)
    done = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.startswith("ok"), done.stderr[-800:]


@pytest.mark.parametrize("name", engine)
def test_no_module_level_import_outside_the_standard_library(name):
    tree = ast.parse((ROOT / "habits" / f"{name}.py").read_text())
    found = []
    for node in tree.body:  # module level only: a lazy import inside a function is the caller's choice
        if isinstance(node, ast.Import):
            found += [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            found.append((node.module or "").split(".")[0])
    assert not [m for m in found if m not in ALLOWED], (name, found)
