def reflex(rx):
    """Bump the version of ~/app, by the part the big model picks from the last commits."""
    major, minor, patch = (int(part) for part in rx.read("~/app/VERSION").strip().split("."))
    subjects = rx.sh(["git", "-C", "~/app", "log", "-5", "--format=%s"]).splitlines()
    part = rx.up("Which part of the version should this release bump: major, minor or patch? Answer one word.",
                 current=f"{major}.{minor}.{patch}", last_commits=subjects)
    part = str(part).strip().lower()
    if part not in ("major", "minor", "patch"):
        part = rx.up("Answer exactly one of: major, minor, patch.")
    new = {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}[part]
    rx.write("~/app/VERSION", new + "\n")
    rx.write("~/app/CHANGELOG.md", f"- {new}\n", append=True)
    return {"version": new, "message": f"Version bumped to {new} ({part})."}
