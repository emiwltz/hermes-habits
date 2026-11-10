"""YAML 1.2 booleans on top of PyYAML, and no aliases.

PyYAML follows YAML 1.1, where yes, no, on and off are booleans: `then: {yes: a}`
would load as `{True: 'a'}`. Habits use YAML 1.2, where only true and false are.

A habit graph takes no alias (*name): a few hundred bytes of nested aliases expand
to millions of strings, and a graph comes from a model that may have read a web page.
"""

from __future__ import annotations

import re

import yaml

_BOOL = "tag:yaml.org,2002:bool"


class Yaml12Loader(yaml.SafeLoader):
    """SafeLoader that only reads true and false as booleans, and refuses an alias."""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise yaml.composer.ComposerError(None, None, "a habit takes no YAML alias (*name): write the value out",
                                              self.peek_event().start_mark)
        return super().compose_node(parent, index)


class AliasLoader(Yaml12Loader):
    """The bench's own scenario files, written by hand, share their blocks with aliases."""

    compose_node = yaml.SafeLoader.compose_node


class NoAliasDumper(yaml.SafeDumper):
    """Writes a value twice rather than as an anchor and an alias, which the loader would refuse."""

    def ignore_aliases(self, data):
        return True


Yaml12Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != _BOOL]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
Yaml12Loader.add_implicit_resolver(
    _BOOL, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def load(text: str, aliases: bool = False):
    """Parse YAML (or JSON) text with YAML 1.2 booleans; an alias is refused unless aliases is true."""
    return yaml.load(text, Loader=AliasLoader if aliases else Yaml12Loader)


def dump(data) -> str:
    """YAML text that load reads back as it is: no anchor, no alias."""
    return yaml.dump(data, Dumper=NoAliasDumper, sort_keys=False, allow_unicode=True, width=100)
