A reflex is one Python function `def reflex(rx, ...)`: its parameters are its inputs, its docstring its
description, its return value the result. It runs in a sandbox (no files, network or processes of its own; plain
Python with re, json, math, datetime, os.path, collections, itertools) and touches the world only through rx:

    rx.sh(cmd) -> str            a command that only reads (ls, cat, find, grep, git log, curl GET...)
    rx.do(cmd) -> str            a command that changes something; only planned in a dry run
    rx.read(path) -> str         rx.write(path, text, append=False)
    rx.ask(question, options=None, about=None, context=None)
                                 a closed question for a small model; about=[items] -> {item: option};
                                 no options -> True/False; context={item: text}; its doubts go up by themselves
    rx.up(question, **context)   one question for the big model; returns its answer

cmd is a list of words, run without a shell (no pipe, no >, no *; a leading ~ is home): rx.do(["mv", "-n", a, b]).
A failing command raises RuntimeError (ok=(0, 1) or ok=None accepts exit codes); a list of commands given to rx.sh
runs side by side and returns a list.
