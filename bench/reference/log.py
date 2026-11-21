def reflex(rx):
    """Count the errors in the last 20 lines of ~/logs/app.log."""
    try:
        lines = rx.read("~/logs/app.log").splitlines()[-20:]
    except FileNotFoundError:
        return {"errors": 0, "kinds": [], "message": "The log ~/logs/app.log is missing."}
    errors = [line.split("ERROR ", 1)[1].strip() for line in lines if "ERROR " in line]
    return {"errors": len(errors), "kinds": sorted(set(errors)),
            "message": f"{len(errors)} errors in the last 20 lines."}
