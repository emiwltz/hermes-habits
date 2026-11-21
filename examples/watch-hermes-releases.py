MEMORY = "~/.reflexes/memory/hermes-release.txt"
LATEST = "https://api.github.com/repos/NousResearch/hermes-agent/releases/latest"


def reflex(rx):
    """Say when a new Hermes release is out (a watch: run it on a schedule)."""
    try:
        tag = json.loads(rx.sh(["curl", "-sfL", "-m", "10", LATEST])).get("tag_name", "")
    except (RuntimeError, ValueError):
        return "GitHub did not answer with a release; try again later."
    try:
        seen = rx.read(MEMORY).strip()
    except FileNotFoundError:
        seen = ""
    if not tag or tag == seen:
        return "No new Hermes release."
    rx.write(MEMORY, tag + "\n")
    if not seen:
        return f"Watching Hermes releases from {tag} on."  # the first run only remembers: nothing is news yet
    rx.up(f"A new Hermes release is out: {tag} (the last one seen was {seen}). Tell the user, with the link "
          "https://github.com/NousResearch/hermes-agent/releases/latest")
    return {"new": tag, "message": f"New Hermes release: {tag}."}
