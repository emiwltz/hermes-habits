FRONT = ("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30"
         "&attributesToRetrieve=title,url&attributesToHighlight=none")


def reflex(rx, interests="AI agents, local and open models, self-hosting, Raspberry Pi, developer tools"):
    """Read the Hacker News front page and hand over only the stories close to your interests."""
    try:
        hits = json.loads(rx.sh(["curl", "-sfL", "-m", "15", FRONT])).get("hits", [])
    except (RuntimeError, ValueError):
        return "Hacker News did not answer; try again later."
    stories = {hit["title"]: hit.get("url") or "" for hit in hits if hit.get("title")}
    if not stories:
        return "Nothing on the front page."
    close = rx.ask(f"Is this Hacker News story mainly about one of these interests: {interests}?",
                   about=list(stories))
    kept = [{"title": title, "url": stories[title]} for title in stories if close[title]]
    if not kept:
        return "Nothing on Hacker News close to your interests today."
    digest = rx.up("Write the user a short digest of these Hacker News stories: each with its title, its link, and "
                   "one line on why it matters to them.", stories=kept, interests=interests)
    return {"stories": kept, "message": str(digest)}
