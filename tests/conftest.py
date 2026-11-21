import pytest

from reflexes import readers


class FakeHost:
    """A host that runs nothing: commands answer from a table, files live in a dict."""

    home = "/home/me"

    def __init__(self, commands=None, files=None):
        self.commands = commands or {}  # "ls -p /x" -> output, or (output, errors, code), or a function of argv
        self.files = dict(files or {})
        self.ran, self.inputs = [], []

    def path(self, word):
        return self.home + word[1:] if word == "~" or word.startswith("~/") else word

    def reads_only(self, argv):
        return readers.why_not(argv)

    def reads_outside(self, argv):
        return readers.outside(argv)

    def run(self, argv, seconds, input=None):
        self.ran.append(list(argv))
        self.inputs.append(input)
        found = self.commands.get(" ".join(argv), self.commands.get(argv[0], ""))
        if callable(found):
            found = found(argv)
        return found if isinstance(found, tuple) else (found, "", 0)

    def read(self, path):
        if path not in self.files:
            raise FileNotFoundError(2, "No such file or directory")
        return self.files[path]

    def write(self, path, text, append=False):
        self.files[path] = (self.files.get(path, "") if append else "") + text


@pytest.fixture
def host():
    return FakeHost()


def decider(table, name="fake"):
    """A small model that answers from {item text: (answer, confidence)}; anything else is a doubt."""
    def decide(question, options, items):
        decide.calls.append((question, list(options), list(items)))
        return [table.get(text, (options[0], 0.1)) for text, _ in items]
    decide.calls, decide.name = [], name
    return decide
