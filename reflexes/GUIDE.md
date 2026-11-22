# Writing a reflex

A reflex is one Python function, `def reflex(rx, ...)`, that does a known task without the big model. Its parameters
are its inputs, its docstring says what it is for, and what it returns is the result (a text, or a dict of values).

It runs in a sandbox with no files, no network and no processes of its own. It touches the world only through `rx`:

- `rx.sh(cmd)` runs a command that only READS, and returns its output (a text, without the last newline).
- `rx.do(cmd)` runs a command that CHANGES something, and returns its output.
- `rx.read(path)` returns the text of a file. `rx.write(path, text)` writes one (`append=True` adds to it).
- `rx.ask(question, options)` puts a closed question to a small, fast model and returns the option it picks.
  Without `options` the question is yes or no and the answer is True or False. `context=text` gives it something
  to read. To ask the same question of several things, `about=items` returns `{item: answer}`, and `context` is
  then `{item: text}`, what the model reads for each (it sees nothing else). You never handle doubts: what the
  small model is unsure of goes up to the big model, and its answer comes back in the same place.
- `rx.up(question, options=None, **context)` asks the big model one precise thing and returns its answer. With
  `options` the answer is one of them. Rarely needed.

Commands. `cmd` is a list of words, run without a shell: `rx.do(["mv", "-n", src, dst])`. So there is no pipe, no
`>`, no `*` and no `$VAR`: call `rx.sh` once per program and do the filtering, counting and text work in Python.
A leading `~` in a word or a path is the home folder. A command that fails raises an error, which stops the run
with the reason; pass `ok=(0, 1)` to accept other exit codes, `ok=None` to accept any, or catch the error
(`RuntimeError`; a missing file in `rx.read` is `FileNotFoundError`). Give `rx.sh` a list of commands and it runs
them side by side and returns one output per command.

`rx.sh` takes only what plainly reads (ls, cat, find, grep, stat, df, ps, git status/log/diff, curl GET or HEAD...).
Anything else is a change and goes through `rx.do`.

Budgets. A command has 30 seconds (`timeout=120` gives it more), a run two minutes and 200 calls to `rx`. So for
many files, give one command several of them (`rx.do(["mv", "-t", dest, *paths])`) rather than one call each.

Dry run. A reflex can be run dry: reads and questions happen, `rx.do` and `rx.write` are only planned (`rx.do`
then returns ""). So do not read back what you have just changed: in a dry run it is not there.

Plain Python 3. re, json, math, datetime, os.path, collections, itertools and functools are ready (importing them
is fine). No other import, no `open()`.

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
