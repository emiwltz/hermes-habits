---
name: hacker-news-digest
description: "Daily Hacker News stories close to your interests"
---

# Hacker News digest

A watch: once a day Hermes reads the Hacker News front page with no LLM. A fast decision model sorts
the 30 stories against your interests in one call (under a second), and Hermes wakes only when one of
them is close. With no key for the fast model, Hermes's own model answers the sort.

The slow way, the same steps as `assets/reflex.yml`:

1. Read the front page, one line per story:
   `curl -sfL --max-time 15 'https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30&attributesToRetrieve=title,url&attributesToHighlight=none' | grep -oE '"title":"([^"\\]|\\.)*"(,"url":"[^"]*")?'`
2. No answer: stop, the next check is tomorrow.
3. Mark each story close when it is mainly about one of the interests (AI agents, local and open
   models, self-hosting, Raspberry Pi, developer tools), far otherwise.
4. No close story: nothing to do today.
5. Otherwise send the user the close ones as a short list, each with its title, its link and one
   line on why it matters to them.
