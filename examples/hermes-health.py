def reflex(rx, home=""):
    """Is Hermes OK? Six read-only signals, one verdict, no model."""
    home = home or os.getenv("HERMES_HOME") or "~/.hermes"  # this Hermes's own home, wherever it is kept
    # Three reads that must work (a Hermes with no logs or no home cannot be judged: the run stops and says why)...
    logs, errors, disk = rx.sh([
        ["hermes", "logs", "agent", "--since", "30m", "-n", "5000"],
        ["hermes", "logs", "errors", "--since", "1h", "--level", "ERROR", "-n", "2000"],
        ["df", "-Pk", home],
    ])
    # ...and three that may fail: no gateway file means no gateway runs here, an unreachable site prints 000.
    gateway, internet, stale = rx.sh([
        ["cat", f"{home}/gateway_state.json"],
        ["curl", "-s", "-I", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", "https://github.com"],
        ["find", home, "-maxdepth", "1", "-name", "gateway_state.json", "-mmin", "+3"],
    ], ok=None)
    try:
        state = json.loads(gateway)
    except ValueError:
        state = {}  # no gateway runs here
    problems = []
    if state.get("gateway_state") in ("startup_failed", "degraded"):
        problems.append(f"the gateway reports {state['gateway_state']} (next: hermes gateway status)")
    down = [name for name, platform in (state.get("platforms") or {}).items()
            if isinstance(platform, dict) and platform.get("state") in ("fatal", "retrying")]
    if down:
        problems.append(f"messaging is down for {', '.join(sorted(down))} (next: hermes gateway status)")
    if stale and state.get("gateway_state") == "running" and state.get("pid"):
        if rx.sh(["ps", "-p", str(state["pid"]), "-o", "pid="], ok=None).strip():
            problems.append("the gateway says running but has not written its heartbeat for 3 minutes: it may be "
                            "frozen (next: hermes gateway status)")
    failures = [line for line in logs.splitlines()
                if "Non-retryable client error" in line or re.search(r"API call failed after \d+ retries", line)]
    if failures:
        problems.append(f"a model call failed in the last 30 minutes: {failures[-1][:300]} (next: hermes model)")
    if not internet.isdigit() or int(internet) == 0:
        problems.append("github.com cannot be reached (next: check the network)")
    found = re.search(r"(\d+)%", disk.splitlines()[-1] if disk else "")
    if not found:
        raise RuntimeError(f"df gave no figure for {home}")
    used = int(found.group(1))
    if used > 89:
        problems.append(f"the disk is {used}% used (next: df -h {home})")
    counted = collections.Counter(re.sub(r"\d+", "N", line[25:125]) for line in errors.splitlines()
                                  if re.match(r"\d{4}-", line))
    frequent = [f"{count}x {text}" for text, count in counted.most_common(3)]
    if problems:
        return {"healthy": False, "problems": problems, "errors": frequent,
                "message": "Hermes needs attention: " + "; ".join(problems) + ". Nothing was changed."}
    return {"healthy": True, "problems": [], "errors": frequent,
            "message": "Hermes looks healthy: gateway fine (or not running here), no model failure in the last 30 "
                       f"minutes, internet reachable, disk {used}% used."
                       + (" Most frequent errors of the last hour: " + "; ".join(frequent) if frequent else "")}
