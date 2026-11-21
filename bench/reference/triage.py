def reflex(rx):
    """Triage the notes of ~/notes/inbox into task, idea or reference, by their content."""
    names = [n for n in rx.sh(["ls", "~/notes/inbox"]).splitlines() if n.endswith(".txt")]
    if not names:
        return {"moved": 0, "message": "No note."}
    texts = {name: rx.read(f"~/notes/inbox/{name}") for name in names}
    kinds = rx.ask("Is this note a task, an idea or a reference?", ["task", "idea", "reference"],
                   about=names, context=texts)
    for name in names:
        rx.do(["mkdir", "-p", f"~/notes/{kinds[name]}"])
        rx.do(["mv", "-n", f"~/notes/inbox/{name}", f"~/notes/{kinds[name]}/"])
    return {"moved": len(names), "message": f"{len(names)} notes moved."}
