---
name: reflexes
description: Small saved programs that do a routine task in one call (a report, a sort, a clean-up, a check). Use it when a request looks like something done before, or when asked to make a task repeatable.
version: 0.1.0
metadata:
  hermes:
    tags: [Automation, Routine, Reflexes]
    requires_toolsets: [terminal]
---

# Reflexes

You have reflexes: small programs for tasks done before. One call does the whole task, with no thinking in between.

```text
reflex list                         the reflexes here, one per line
reflex run <name> [input=value]     run one. It ends "done" (use its result as it is), "asks" (it needs one answer
                                    from you: `reflex answer <id> '<answer>'`, and it carries on) or "stopped" (it
                                    says why and what it already changed; do the rest yourself)
reflex test <name> [input=value]    dry run: shows what it would change, changes nothing
reflex guide                        how to write one
```

Start with `reflex list`. When a request matches a reflex, run it instead of doing the steps by hand.
