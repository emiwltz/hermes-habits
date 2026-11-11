"""Read a Hermes home's history with the dream's own reader and print one JSON object: counts, every spotted key,
what was left out by reason. Run with Hermes's own interpreter (the one that has hermes_state):

    PYTHONPATH=<hermes install dir>:<installed plugin folder> <hermes python> probe.py [--home FOLDER] [--shapes]

--home reads another Hermes home read only (default: HERMES_HOME). --shapes keeps only the tool and the program name
of each key (for a real history: never full arguments)."""
import json
import sys
import time
from collections import Counter

from habits import dream


def shape(key):
    tool, _, rest = key.partition(":")
    if tool == "terminal":
        words = rest.split()
        return "terminal:" + (words[0].rsplit("/", 1)[-1] if words else "?")
    return tool


def main(argv):
    home = argv[argv.index("--home") + 1] if "--home" in argv else None
    shapes = "--shapes" in argv
    began = time.monotonic()
    store = dream.open_store(home) if home else dream.open_store()
    read = dream.read_turns(store, time.time())
    spotted, left, near = dream.spot(read.turns)
    took = time.monotonic() - began
    show = shape if shapes else (lambda k: k)
    print(json.dumps({
        "status": read.status, "why": read.why, "sessions": read.sessions, "turns": len(read.turns),
        "skipped": read.skipped, "seconds": round(took, 2),
        "spotted": [{"key": show(s.action.key), "turns": s.turns, "days": s.days,
                     "scheduled": s.moments.get("scheduled", 0)} for s in spotted],
        "near": [{"key": show(n.action.key), "turns": n.turns, "days": n.days} for n in near],
        "left": dict(left),
        "turns_by_session": dict(Counter(t.session for t in read.turns)) if not shapes else {},
    }))


main(sys.argv[1:])
