"""The per-file, per-rule context handed to every rule handler.

A rule never builds a :class:`~anti_slop.engine.rule.Diagnostic` itself: it calls
:meth:`RuleContext.report` with a node and a message id, and the context resolves
the message template, the source position and any line suppression.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

from anti_slop.engine.comments import CommentMap
from anti_slop.engine.rule import AnchorNode, Diagnostic, OptionValue, Rule
from anti_slop.engine.walker import ParentMap

__all__ = ["RuleContext"]


class RuleContext:
    """Everything a rule may look at while analysing one file."""

    __slots__ = (
        "_comments",
        "_lines",
        "_options",
        "_parents",
        "_path",
        "_report",
        "_rule",
        "_severity",
        "_source",
    )

    def __init__(
        self,
        *,
        path: Path,
        source: str,
        lines: Sequence[str],
        comments: CommentMap,
        parents: ParentMap,
        rule: Rule,
        options: Mapping[str, OptionValue],
        report: Callable[[Diagnostic], None],
        severity: str = "error",
    ) -> None:
        self._path = path
        self._source = source
        self._lines = lines
        self._comments = comments
        self._parents = parents
        self._rule = rule
        self._options = options
        self._report = report
        self._severity = severity

    @property
    def path(self) -> Path:
        return self._path

    @property
    def rule(self) -> Rule:
        return self._rule

    @property
    def source(self) -> str:
        return self._source

    @property
    def comments(self) -> CommentMap:
        return self._comments

    @property
    def options(self) -> Mapping[str, OptionValue]:
        return self._options

    def source_line(self, line: int) -> str:
        """The 1-based source line, or an empty string when out of range."""
        if 1 <= line <= len(self._lines):
            return self._lines[line - 1]
        return ""

    def parent_of(self, node: ast.AST) -> ast.AST | None:
        return self._parents.parent_of(node)

    def ancestors(self, node: ast.AST) -> Iterator[ast.AST]:
        return self._parents.ancestors(node)

    def bool_option(self, name: str) -> bool:
        value = self._lookup_option(name)
        if isinstance(value, bool):
            return value
        message = f"option {name!r} of rule {self._rule.id!r} is not a boolean"
        raise TypeError(message)

    def str_list_option(self, name: str) -> tuple[str, ...]:
        value = self._lookup_option(name)
        if isinstance(value, tuple):
            return value
        message = f"option {name!r} of rule {self._rule.id!r} is not a string list"
        raise TypeError(message)

    def report(self, node: AnchorNode, message_id: str, **params: str) -> None:
        """Report a violation of ``self.rule`` anchored at ``node``.

        Diagnostics whose line carries a matching ``# anti-slop: ignore[...]`` --
        on the line itself or on the line above -- are dropped here.
        """
        template = self._rule.messages.get(message_id)
        if template is None:
            known = ", ".join(sorted(self._rule.messages)) or "<none>"
            message = (
                f"rule {self._rule.id!r} has no message {message_id!r}"
                f" (known messages: {known})"
            )
            raise LookupError(message)

        line = node.lineno
        if self._comments.suppresses(line, self._rule.id):
            return
        params = {name: _sanitize_fragment(value) for name, value in params.items()}

        end_line = node.end_lineno if node.end_lineno is not None else line
        end_col_offset = (
            node.end_col_offset if node.end_col_offset is not None else node.col_offset
        )
        self._report(
            Diagnostic(
                path=self._path,
                line=line,
                col=node.col_offset + 1,
                end_line=end_line,
                end_col=end_col_offset + 1,
                rule_id=self._rule.id,
                message=template.format(**params),
                severity=self._severity,
            )
        )

    def _lookup_option(self, name: str) -> OptionValue:
        try:
            return self._options[name]
        except KeyError as error:
            known = ", ".join(sorted(self._rule.option_names())) or "<none>"
            message = (
                f"rule {self._rule.id!r} has no option {name!r}"
                f" (known options: {known})"
            )
            raise LookupError(message) from error


_FRAGMENT_LIMIT = 80


def _sanitize_fragment(value: str) -> str:
    """Cap and single-line a message fragment derived from scanned source.

    Interpolated fragments -- identifiers, unparsed annotations -- come from
    UNTRUSTED third-party code, and diagnostics are read by coding agents. A
    fragment must never smuggle a multi-line block or an oversized payload into
    agent-visible output: whitespace runs collapse to one space, and anything
    longer than the cap is truncated with an ellipsis.
    """
    collapsed = " ".join(value.split())
    if len(collapsed) > _FRAGMENT_LIMIT:
        return collapsed[: _FRAGMENT_LIMIT - 1] + "…"
    return collapsed
