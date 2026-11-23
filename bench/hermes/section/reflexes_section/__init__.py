"""Reflexes for Hermes, the smallest way: what exists is said in the prompt, the `reflex` command does the rest.

The pilot of the Hermes bench showed that a cheap model takes a reflex when its name and what it does are in front
of it, and never goes looking for one: a tool behind a search or a skill in an index were taken 0 times in 24, a note
in the prompt 17 times in 24. A note in a file goes stale, though: a reflex made yesterday is not in it. So this
plugin writes the note itself, from `reflex list`, each time a session starts. It adds no tool: the agent calls
`reflex` in its terminal, like any command.
"""

import os
import subprocess
from pathlib import Path

NOTE = Path(__file__).with_name("NOTE.txt")  # the words of the note, with {listing} where the reflexes go


def _section(session) -> str:
    try:
        listed = subprocess.run([os.environ.get("REFLEX_COMMAND") or "reflex", "list"], capture_output=True, text=True,
                                timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    listing = listed.stdout.rstrip()
    if listed.returncode or not listing:  # no reflex yet: nothing to say
        return ""
    return NOTE.read_text(encoding="utf-8").format(listing="\n".join("  " + line for line in listing.splitlines()))


def register(ctx):
    ctx.register_system_prompt_section("reflexes.yours", _section, position="after_memory", max_chars=4000)
