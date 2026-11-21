import json

MEMORY = "~/.cache/drop-watch.json"


def reflex(rx):
    """Say which files of ~/drop are new since the last run."""
    names = sorted(rx.sh(["ls", "~/drop"]).splitlines())
    try:
        seen = json.loads(rx.read(MEMORY))
    except FileNotFoundError:
        seen = names  # the first run only remembers
    new = sorted(set(names) - set(seen))
    rx.write(MEMORY, json.dumps(names))
    return {"new": new, "message": f"{len(new)} new files in ~/drop." if new else "Nothing new in ~/drop."}
