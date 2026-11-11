#!/usr/bin/env bash
# Build a standing throwaway Hermes for a juror (or for Emi) to try the dream in: the plugin installed from a checkout's
# committed HEAD, Hermes's own state.db, a synthetic history in it. Prints the command to try. Never touches ~/.hermes.
# Usage: tests/e2e/jury.sh [checkout] [home]     (defaults: this checkout, ~/goinfre/habits-nuit/jury)
# It never deletes anything: an existing home makes it stop.
# ~/goinfre is purged after 48 h without a change: remove the folder and run this script again to bring it back.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
CHECKOUT="$(cd "${1:-$HERE/../..}" && pwd)"
HOME_DIR="${2:-$HOME/goinfre/habits-nuit/jury}"
case "$HOME_DIR" in "$HOME/.hermes"|"$HOME/.hermes/"*) echo "refusing: that is the everyday Hermes" >&2; exit 2;; esac

if [ -e "$HOME_DIR" ] && [ -n "$(ls -A "$HOME_DIR" 2>/dev/null)" ]; then
  echo "$HOME_DIR already exists. Remove that throwaway folder yourself, then run this again." >&2
  exit 1
fi
INSTALL_DIR="$(hermes --version 2>/dev/null | sed -n 's/^Install directory: //p')"
HPY="$INSTALL_DIR/venv/bin/python"
[ -x "$HPY" ] || { echo "cannot find Hermes's own python ($HPY)" >&2; exit 1; }

mkdir -p "$HOME_DIR"
echo "A throwaway Hermes made from the committed HEAD of $CHECKOUT ($(git -C "$CHECKOUT" log -1 --format=%h 2>/dev/null))" >"$HOME_DIR/.habits-jury"
export HERMES_HOME="$HOME_DIR"
hermes plugins install "file://$CHECKOUT" --enable --yes-deps >"$HOME_DIR/.install.log" 2>&1 \
  || { echo "install failed, see $HOME_DIR/.install.log" >&2; exit 1; }
PYTHONPATH="$INSTALL_DIR" "$HPY" -c 'from hermes_state import SessionDB; SessionDB().close()'
# beside the home, not in it: Hermes treats every path inside its own home as private, and the dream leaves such turns out
PROJECT="$HOME_DIR-proj"
bash "$HERE/make_project.sh" "$PROJECT" >/dev/null
python3 "$HERE/make_history.py" "$HOME_DIR/state.db" --base "$PROJECT"

cat <<MSG

Ready. The history in it is made up (a few commands that come back on several days), and the small project it
happened in is real: $PROJECT (a git repository with a TODO.md and a notes script, beside the home), so a draft runs for real.
Try the dream:

    HERMES_HOME=$HOME_DIR hermes habits dream

Then:  HERMES_HOME=$HOME_DIR hermes habits list
To read your own history from this throwaway home (read only, nothing written there):

    HERMES_HOME=$HOME_DIR hermes habits dream --home ~/.hermes
MSG
