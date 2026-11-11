"""Small assertions for dream_e2e.sh. `check.py json-has FILE EXPR` evaluates a Python expression with `d` = the JSON
of FILE; exit 0 when it is true. Standard library only."""
import json
import sys

_, kind, path, expr = sys.argv[:4]
d = json.load(open(path))
if kind == "json-has":
    sys.exit(0 if eval(expr, {"d": d, "any": any, "all": all, "len": len, "sum": sum}) else 1)
sys.exit(2)
