KINDS = ["installer", "image", "document", "archive", "other"]


def reflex(rx, folder="~/Downloads"):
    """Sort a downloads folder by file type; installers of apps already installed go to the Trash (macOS)."""
    files = [name for name in rx.sh(["ls", "-p", folder]).splitlines() if not name.endswith("/")][:40]
    if not files:
        return "Nothing to sort."
    kind = rx.ask("What type of file is this?", KINDS, about=files)
    for name in files:
        rx.do(["mkdir", "-p", f"{folder}/{kind[name]}"])
        rx.do(["mv", "-n", f"{folder}/{name}", f"{folder}/{kind[name]}/"])
    installers = [name for name in files if kind[name] == "installer"]
    trashed = []
    if installers:
        apps = rx.sh(["ls", "/Applications"], ok=None)
        installed = rx.ask("Is the application this installer installs already among the installed applications?",
                           about=installers, context={name: "Installed applications:\n" + apps for name in installers})
        trashed = [name for name in installers if installed[name]]
        for name in trashed:
            rx.do(["mv", "-n", f"{folder}/installer/{name}", "~/.Trash/"])  # never deleted: the Trash is emptied by hand
    return {"sorted": len(files), "trashed": trashed,
            "message": f"{len(files)} files sorted by type, {len(trashed)} installers moved to the Trash."}
