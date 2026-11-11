#!/usr/bin/env bash
# Make the small project that the made-up history of make_history.py --base works in, so that a reflex the dream
# drafts from it runs for real: a git repository with a TODO.md, a notes script, a Makefile with a lint target, and a
# local "remote" so that `git pull` has somewhere to pull from. Nothing here is real; it touches only <folder>.
# Usage: tests/e2e/make_project.sh <folder>      (the folder must not exist yet; it also makes <folder>-remote.git)
set -eu
BASE="${1:?usage: make_project.sh <folder>}"
if [ -e "$BASE" ]; then
  echo "$BASE already exists. Remove that throwaway folder yourself, then run this again." >&2
  exit 1
fi
REMOTE="$BASE-remote.git"
GIT="git -c user.name=Someone -c user.email=someone@example.invalid"

mkdir -p "$BASE/bin"
printf '# Todo\n\n- [ ] answer the mail\n- [ ] read the release notes\n- [x] water the plants\n' >"$BASE/TODO.md"
printf '#!/bin/sh\n# A made-up notes script: "notes wake" prints what is on the list for today.\necho "Notes for today:"\ngrep -e "- \\[ \\]" "$(dirname "$0")/../TODO.md" || true\n' >"$BASE/bin/notes"
chmod +x "$BASE/bin/notes"
printf 'lint:\n\t@echo lint ok\n' >"$BASE/Makefile"

git init -q -b main "$BASE"
$GIT -C "$BASE" add -A
$GIT -C "$BASE" commit -q -m "A small project to practise on"
git init -q --bare -b main "$REMOTE"
git -C "$BASE" remote add origin "$REMOTE"
git -C "$BASE" push -q -u origin main
echo "made the project $BASE (a git repository with TODO.md, bin/notes, a Makefile and a local remote)"
