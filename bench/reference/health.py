def reflex(rx):
    """Report on ~/proj: changed files, TODO lines in its Python files, disk use."""
    changed = [line for line in rx.sh(["git", "-C", "~/proj", "status", "--porcelain"]).splitlines() if line]
    counts = rx.sh(["grep", "-rc", "--include=*.py", "TODO", "~/proj"], ok=(0, 1)).splitlines()
    todos = sum(int(line.rsplit(":", 1)[1]) for line in counts if ":" in line)
    disk = int(rx.sh(["df", "-Pk", "~"]).splitlines()[-1].split()[4].rstrip("%"))
    return {"changed": len(changed), "todos": todos, "disk": disk,
            "message": f"{len(changed)} changed files, {todos} TODO lines, disk {disk}% used."}
