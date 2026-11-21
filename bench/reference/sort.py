import os.path

KINDS = {"images": ("jpg", "jpeg", "png", "gif"), "docs": ("pdf", "txt", "md")}


def reflex(rx):
    """Sort the files of ~/inbox into ~/sorted/images, docs and other, by extension."""
    names = [n for n in rx.sh(["ls", "-p", "~/inbox"]).splitlines() if not n.endswith("/")]
    if not names:
        return "No file in ~/inbox."
    counts = {"images": 0, "docs": 0, "other": 0}
    for name in names:
        extension = os.path.splitext(name)[1][1:].lower()
        kind = next((k for k, extensions in KINDS.items() if extension in extensions), "other")
        rx.do(["mkdir", "-p", f"~/sorted/{kind}"])
        rx.do(["mv", "-n", f"~/inbox/{name}", f"~/sorted/{kind}/"])
        counts[kind] += 1
    return {**counts, "message": f"{len(names)} files sorted: {counts}"}
