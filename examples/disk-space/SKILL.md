---
name: disk-space
description: "How full is the disk of the home folder?"
---

# Disk space

Run it by name: `habit_run {name: "disk-space"}`, or from a terminal: `hermes habits run disk-space`.
It only reads and calls no model.

The slow way, the same step as `assets/reflex.yml`:

1. Run `df -h ~` and read the line of the home folder: size, used, free, percent used.
2. Answer with the free space and the percent used. Change nothing.
