import os.path


def reflex(rx, days=7):
    """Delete the .tmp files of ~/work that are older than some days."""
    found = rx.sh(["find", "~/work", "-maxdepth", "1", "-type", "f", "-name", "*.tmp", "-mtime", f"+{int(days)}"])
    paths = sorted(line for line in found.splitlines() if line)
    for path in paths:
        rx.do(["rm", path])
    names = [os.path.basename(path) for path in paths]
    return {"deleted": names, "message": f"{len(names)} files deleted." if names else "Nothing to delete."}
