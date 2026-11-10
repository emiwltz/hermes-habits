---
name: watch-hermes-releases
description: "Tell the user when a new Hermes release is out"
---

# Watch Hermes releases

A watch: Hermes checks every 5 minutes with no LLM, and wakes you only when a new release is out.
An example never runs on its own: save it to start the schedule door.

The slow way, the same steps as `assets/reflex.yml`:

1. Read the latest release tag:
   `curl -sfL --max-time 10 https://api.github.com/repos/NousResearch/hermes-agent/releases/latest | grep -m1 '"tag_name"'`
2. No answer or no tag: stop, the next check comes in 5 minutes.
3. Same tag as at the last check: nothing to do.
4. New tag: tell the user in two sentences at most, with the version and the link
   https://github.com/NousResearch/hermes-agent/releases/latest
