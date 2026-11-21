def reflex(rx, urls):
    """Summarize a few web articles (three at most), leaving out error pages and login walls."""
    urls = [urls] if isinstance(urls, str) else list(urls)
    if len(urls) > 3:
        return "Give 3 URLs at most: more pages would not fit in what the summary can read."
    pages = rx.sh([["curl", "-sL", "-m", "20", url] for url in urls], ok=None)
    texts = {}
    for url, html in zip(urls, pages):
        body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
        texts[url] = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", body)).strip()[:2000]
    is_article = rx.ask("Is this page an article (not an error page, an empty page or a login wall)?",
                        about=urls, context=texts)
    articles = {url: texts[url] for url in urls if is_article[url]}
    if not articles:
        return "None of these pages is an article I could read."
    summary = rx.up("Summarize each of these pages in three sentences, under its URL.", pages=articles)
    return {"summarized": list(articles), "skipped": [url for url in urls if url not in articles],
            "message": str(summary)}
