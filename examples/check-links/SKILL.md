---
name: check-links
description: "Check the links of a web page and report broken ones"
---

# Check links

Input: `page`, the URL of the page. Run it by name:
`habit_run {name: "check-links", inputs: {page: "https://…"}}`.

The slow way, with the same commands as `assets/reflex.yml`:

1. List the absolute links of the page, 30 at most:
   `curl -sL --max-time 20 '<page>' | tr '\n\t' '  ' | grep -oiE '<a [^>]*href="?https?://[^" >]+' | sed -E 's/^.*[hH][rR][eE][fF]="?(https?:)/\1/' | sort -u | head -n 30`
2. No link: the page did not load, has none, or builds its links with JavaScript. Say so, and try `web_extract`
   or a browser.
3. Probe each link, one word each:
   `curl -sL -r 0-0 -o /dev/null -w '%{http_code}' --max-time 10 '<link>' | awk '{print ($1 ~ /^[23]/ ? "ok" : $1 == 416 ? "ok" : $1 ~ /^40[135]$/ ? "blocked" : $1 == 429 ? "blocked" : $1 == 999 ? "blocked" : "broken")}'`
   (`-r 0-0` asks for one byte, so a big file answers at once; 416 means the server refused the byte range, so the
   link exists). ok is 2xx, 3xx or 416. blocked is 401, 403, 405, 429 or 999: the site refused a script, and the
   link usually works in a browser. Any other code is broken: an HTTP error, or 000 when the link did not answer
   in 10 s.
4. Report each link as ok, blocked or broken, and say what each word means. A blocked link was not checked, so
   do not call it broken. Say that only absolute links were checked, the first 30 at most (relative links
   are not). Finding broken links is the job done, not a reason to check them again.
