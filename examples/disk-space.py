def reflex(rx):
    """Free space of the home disk."""
    return "Disk space of the home folder:\n" + rx.sh(["df", "-h", "~"])
