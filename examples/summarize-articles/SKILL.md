---
name: summarize-articles
description: "Summarize a few web articles, skipping error pages"
---

# Summarize articles

Input: `urls`, a list of 3 page URLs at most (the summary reads 8,000 characters of pages at most).
Run it by name: `habit_run {name: "summarize-articles", inputs: {urls: ["https://…", "https://…"]}}`.
One question to the fast model (with no key, Hermes's own model answers it) and one call to Hermes's model.

The slow way, the same steps as `assets/HABIT.yaml`:

1. More than 3 URLs: ask for 3 at most.
2. Read each page: `web_extract` with `urls: ["<url>"]` and `char_limit: 2000`.
3. Set aside error pages and login walls.
4. Summarize each remaining page in three sentences, in one answer. If none is left, say so.
