BLOCKED = {401, 403, 405, 429, 999}  # the site refused a script: usually fine in a browser


def reflex(rx, page):
    """Say which links of a web page are broken."""
    html = rx.sh(["curl", "-sL", "-m", "20", page])
    links = list(dict.fromkeys(re.findall(r'<a\s[^>]*href="?(https?://[^" >]+)', html, flags=re.I)))[:30]
    if not links:
        return {"broken": [], "blocked": [], "checked": 0, "message": f"No link found on {page}."}
    codes = rx.sh([["curl", "-sL", "-r", "0-0", "-o", "/dev/null", "-w", "%{http_code}", "-m", "10", link]
                   for link in links], ok=None)  # side by side; a refused connection prints 000
    status = {}
    for link, code in zip(links, codes):
        code = int(code) if code.isdigit() else 0
        status[link] = "ok" if 200 <= code < 400 or code == 416 else "blocked" if code in BLOCKED else "broken"
    broken = sorted(link for link in links if status[link] == "broken")
    blocked = sorted(link for link in links if status[link] == "blocked")
    return {"broken": broken, "blocked": blocked, "checked": len(links),
            "message": f"{len(broken)} broken and {len(blocked)} blocked out of {len(links)} links on {page}."}
