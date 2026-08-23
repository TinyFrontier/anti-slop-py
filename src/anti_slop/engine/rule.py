"""Rule and diagnostic primitives: the vocabulary every anti-slop rule is written in.

A rule is data, not a class hierarchy: an id, agent-executable messages, an option
schema, and a tuple of node-type -> handler bindings. The walker (``walker.py``)
collects the bindings of every enabled rule and dispatches one AST pass to all of
them at once.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from anti_slop.engine.context import RuleContext

__all__ = [
    "AnchorNode",
    "BoolOption",
    "Diagnostic",
    "Handler",
    "HandlerEntry",
    "OptionSpec",
    "OptionValue",
    "Rule",
    "StrListOption",
    "on",
]

# Nodes that carry source positions and can therefore anchor a diagnostic.
type AnchorNode = (
    ast.expr | ast.stmt | ast.arg | ast.excepthandler | ast.keyword | ast.alias
)

# The value a rule option can hold after configuration parsing.
type OptionValue = bool | tuple[str, ...]

# A handler receives the per-file, per-rule context and the node it subscribed to.
type Handler = Callable[[RuleContext, ast.AST], None]

_RULE_ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One reported violation. Columns are 1-based, matching editor conventions."""

    path: Path
    line: int
    col: int
    end_line: int
    end_col: int
    rule_id: str
    message: str

    @property
    def sort_key(self) -> tuple[str, int, int, str]:
        return (str(self.path), self.line, self.col, self.rule_id)


@dataclass(frozen=True, slots=True)
class BoolOption:
    """A boolean rule option, named in kebab-case as it appears in pyproject.toml."""

    name: str
    default: bool
    description: str


@dataclass(frozen=True, slots=True)
class StrListOption:
    """A list-of-strings rule option (e.g. the term list of a naming rule)."""

    name: str
    default: tuple[str, ...]
    description: str


type OptionSpec = BoolOption | StrListOption


@dataclass(frozen=True, slots=True)
class HandlerEntry:
    """A single ``node type -> handler`` subscription, produced by :func:`on`."""

    node_type: type[ast.AST]
    handler: Handler


def on[NodeT: ast.AST](
    node_type: type[NodeT],
    handler: Callable[[RuleContext, NodeT], None],
) -> HandlerEntry:
    """Bind ``handler`` to ``node_type`` for the shared single-pass walker."""
    # SAFETY: the walker looks the handler up by the runtime type of the node it is
    # about to dispatch, so `handler` is only ever called with an instance of
    # `node_type` (or a subclass). Widening the declared parameter to `ast.AST` for
    # storage cannot therefore produce a call with an unexpected node type.
    erased = cast("Handler", handler)
    return HandlerEntry(node_type=node_type, handler=erased)


@dataclass(frozen=True, slots=True)
class Rule:
    """A rule definition.

    ``messages`` maps a message id to a ``str.format`` template; every template must
    say what to do *instead*, not merely that something is forbidden.
    """

    id: str
    description: str
    messages: Mapping[str, str]
    handlers: tuple[HandlerEntry, ...]
    options: tuple[OptionSpec, ...] = ()

    def __post_init__(self) -> None:
        if _RULE_ID_PATTERN.fullmatch(self.id) is None:
            message = f"rule id {self.id!r} is not kebab-case"
            raise ValueError(message)
        if not self.messages:
            message = f"rule {self.id!r} declares no messages"
            raise ValueError(message)
        seen: set[str] = set()
        for spec in self.options:
            if spec.name in seen:
                message = f"rule {self.id!r} declares option {spec.name!r} twice"
                raise ValueError(message)
            seen.add(spec.name)

    def option_names(self) -> frozenset[str]:
        return frozenset(spec.name for spec in self.options)

    def option_spec(self, name: str) -> OptionSpec | None:
        for spec in self.options:
            if spec.name == name:
                return spec
        return None

    def default_options(self) -> dict[str, OptionValue]:
        return {spec.name: spec.default for spec in self.options}
