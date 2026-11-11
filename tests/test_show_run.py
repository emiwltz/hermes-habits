"""`hermes habits run <draft>` tells the truth: a step that only planned says it was planned and why; a web tool is
planned, never called."""

import argparse
import contextlib
import io

from habits import dream
from test_seams import world  # noqa: F401  (the fixture)


def put_draft(habits, name, tool, args):
    action = dream.Action(dream.key_of(tool, args), tool, args, dream.raw_of(tool, args))
    habits.ledger.put_draft(action.key, name=name, tool=tool, args=args, raw=action.raw, parts=[],
                            graph=dream.draft_reflex([action], ""), reads=0, turns=4, days=3, moments={}, cwd="")


def run_says(habits, *argv):
    parser = argparse.ArgumentParser()
    habits.cli_setup(parser)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        habits.cli_handle(parser.parse_args(["run", *argv]))
    return out.getvalue()


def test_a_step_that_is_only_planned_says_so_and_why_never_done_with_a_tool_call(world):
    ctx, habits = world
    put_draft(habits, "unknown", "terminal", {"command": "hm-mem rappel"})
    said = run_says(habits, "unknown")
    assert "unknown · planned, not run · 1 call only planned, none made" in said
    assert "done in" not in said and "tool call" not in said
    assert "only planned: hm-mem is a program the plugin does not know as a reader" in said
    assert ctx.ran == []                                   # and it did not run


def test_a_call_that_reaches_the_network_or_is_no_local_read_says_why(world):
    ctx, habits = world
    put_draft(habits, "net", "terminal", {"command": "curl -s http://localhost:3000"})
    put_draft(habits, "mcp", "mcp_homeassistant_get_state", {"entity_id": "sensor.office_co2"})
    assert "only planned: it reaches the network or another machine" in run_says(habits, "net")
    said = run_says(habits, "mcp")
    assert "only planned: mcp_homeassistant_get_state is not a local read" in said and "done in" not in said
    assert not any(name == "mcp_homeassistant_get_state" for name, _ in ctx.calls)


def test_a_web_tool_of_a_draft_is_planned_and_never_called(world):
    ctx, habits = world
    put_draft(habits, "web", "web_extract", {"urls": ["https://example.com/feed"]})
    said = run_says(habits, "web")
    assert not any(name == "web_extract" for name, _ in ctx.calls)
    assert "web_extract ok" not in said and "only planned: a call to the web: a test run never makes one" in said
    assert "done in" not in said and "tool call" not in said
    habits.ctx.config["language"] = "fr"
    assert "seulement prévu : un appel au web" in run_says(habits, "web")


def test_a_read_that_really_ran_still_says_done_and_counts_its_call(world):
    ctx, habits = world
    put_draft(habits, "read", "read_file", {"path": "/x/y.md"})
    said = run_says(habits, "read")
    assert "read · done in " in said and "1 tool call" in said and "only planned" not in said
    assert ("read_file", {"path": "/x/y.md"}) in ctx.calls
