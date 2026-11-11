---
name: tidy-downloads
description: "Sort ~/Downloads by file type (macOS)"
---

# Tidy downloads

macOS only: it reads `/Applications` and moves installers to `~/.Trash`.

The slow way: what you do by hand when the habit does not run. These are the same commands as
`assets/reflex.yml`, in the same order, so that the comparison while learning finds each step.

1. List the files, 40 at most per run (run it again for the rest): `ls -p ~/Downloads | grep -v / | head -n 40`
2. List the installed applications: `ls /Applications`
3. For each file, choose its type: installer, image, document, archive or other.
4. Move each file into the folder of its type, never over a file already there:
   `mkdir -p ~/Downloads/<type> && mv -n ~/Downloads/<file> ~/Downloads/<type>/`
5. For each installer whose application is already in /Applications, move it to the Trash:
   `mv -n ~/Downloads/installer/<file> ~/.Trash/`
6. Never delete: the Trash is emptied by hand. Files whose type is in doubt stay in ~/Downloads;
   installers whose installation is in doubt stay in ~/Downloads/installer/. Tell the user about them.

Called by name with `habit_run`, the habit acts for real: only when the user asks to tidy their downloads.

A name with spaces goes between single quotes: `mv -n ~/Downloads/'My file.pdf' ~/Downloads/document/`.
