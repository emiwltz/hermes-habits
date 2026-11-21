import re


def reflex(rx, page):
    """Find the broken links of a web page."""
    html = rx.sh(["curl", "-sL", "-m", "20", page])
    links = list(dict.fromkeys(re.findall(r'<a\s[^>]*href="(https?://[^"]+)"', html)))[:30]
    if not links:
        return {"checked": 0, "broken": [], "message": "No link on the page."}
    codes = rx.sh([["curl", "-s", "-I", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", link] for link in links],
                  ok=None)
    broken = sorted(link for link, code in zip(links, codes) if not code.isdigit() or int(code) >= 400 or int(code) == 0)
    return {"checked": len(links), "broken": broken, "message": f"{len(broken)} of {len(links)} links are broken."}
