# Writing a reflex

A reflex is one Python function, `def reflex(rx, ...)`, that does a known task without the big model. Its parameters
are its inputs, its docstring says what it is for, and what it returns is the result (a text, or a dict of values).

It runs in a sandbox with no files, no network and no processes of its own. It touches the world only through `rx`:

- `rx.sh(cmd)` runs a command that only READS, and returns its output (a text, without the last newline).
- `rx.do(cmd)` runs a command that CHANGES something. In a dry run it is only planned, and returns "".
- `rx.read(path)` returns the text of a file. `rx.write(path, text)` writes one (`append=True` adds to it).
  In a dry run a write is only planned.
- `rx.ask(question, options, about=items, context=texts)` puts a closed question to a small, fast model.
  With a list in `about` it asks once per item and returns `{item: option}`. Without `options` the question is yes
  or no and the answers are True or False. `context` is optional: `{item: text}`, what the model reads for that item
  (it sees nothing else). You never handle doubts: what the small model is unsure of goes up to the big model, and
  its answer comes back in the same place.
- `rx.up(question, **context)` asks the big model one precise thing and returns its answer, as it gives it. Say in
  the question what form you expect. Rarely needed.

Commands. `cmd` is a list of words, run without a shell: `rx.do(["mv", "-n", src, dst])`. So there is no pipe, no
`>`, no `*` and no `$VAR`: call `rx.sh` once per program and do the filtering, counting and text work in Python.
A leading `~` in a word or a path is the home folder. A command that fails raises an error, which stops the run
with the reason; pass `ok=(0, 1)` to accept other exit codes, `ok=None` to accept any, or catch the error
(`RuntimeError`; a missing file in `rx.read` is `FileNotFoundError`). Give `rx.sh` a list of commands and it runs
them side by side and returns one output per command.

`rx.sh` takes only what plainly reads (ls, cat, find, grep, stat, df, ps, git status/log/diff, curl GET or HEAD...).
Anything else is a change and goes through `rx.do`.

Plain Python 3: re, json, math, datetime, os.path, collections, itertools. No other import, no `open()`.

```python
def reflex(rx, folder="~/Screenshots"):
    """File the screenshots that show an error into an errors folder."""
    names = [n for n in rx.sh(["ls", folder]).splitlines() if n.endswith(".png")]
    if not names:
        return "No screenshot."
    shows_error = rx.ask("Does this screenshot's name suggest an error message?", about=names)
    kept = [n for n in names if shows_error[n]]
    if kept:
        rx.do(["mkdir", "-p", f"{folder}/errors"])
    for name in kept:
        rx.do(["mv", "-n", f"{folder}/{name}", f"{folder}/errors/"])
    return {"filed": kept, "message": f"{len(kept)} of {len(names)} screenshots filed."}
```
