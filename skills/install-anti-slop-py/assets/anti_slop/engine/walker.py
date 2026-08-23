"""One AST pass for all enabled rules.

The walker records parent links while it descends and dispatches every visited node
to the handlers subscribed to that node type. Subscribing to a base class (say
``ast.expr``) works too: handler lookup walks the MRO once per node type and caches
the result.

Parent links live in a :class:`ParentMap` keyed by node identity rather than in a
dynamically assigned ``node.parent`` attribute. It is the same information a rule
needs to climb to an owning statement, but it stays fully typed -- ``ast`` nodes
have no declared ``parent`` field.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Sequence
from typing import TYPE_CHECKING

from anti_slop.engine.rule import Handler, HandlerEntry

if TYPE_CHECKING:
    from anti_slop.engine.context import RuleContext

__all__ = ["ParentMap", "Walker"]


class ParentMap:
    """Parent links for the nodes of one file, filled in during the walk."""

    __slots__ = ("_parents",)

    def __init__(self) -> None:
        self._parents: dict[ast.AST, ast.AST] = {}

    def record(self, child: ast.AST, parent: ast.AST) -> None:
        self._parents[child] = parent

    def parent_of(self, node: ast.AST) -> ast.AST | None:
        return self._parents.get(node)

    def ancestors(self, node: ast.AST) -> Iterator[ast.AST]:
        """Yield the owners of ``node``, innermost first."""
        current = self._parents.get(node)
        while current is not None:
            yield current
            current = self._parents.get(current)


class Walker:
    """Collects subscriptions from every enabled rule, then runs a single pass."""

    __slots__ = ("_by_type", "_cache", "_parents")

    def __init__(self, parents: ParentMap) -> None:
        self._parents = parents
        self._by_type: dict[type[ast.AST], list[tuple[RuleContext, Handler]]] = {}
        self._cache: dict[type[ast.AST], tuple[tuple[RuleContext, Handler], ...]] = {}

    def subscribe(self, context: RuleContext, entries: Iterable[HandlerEntry]) -> None:
        """Register the handlers of one rule together with its per-file context."""
        for entry in entries:
            self._by_type.setdefault(entry.node_type, []).append(
                (context, entry.handler)
            )
        self._cache.clear()

    def run(self, tree: ast.Module) -> None:
        stack: list[ast.AST] = [tree]
        while stack:
            node = stack.pop()
            for context, handler in self._handlers_for(type(node)):
                handler(context, node)
            children: Sequence[ast.AST] = list(ast.iter_child_nodes(node))
            for child in reversed(children):
                self._parents.record(child, node)
                stack.append(child)

    def _handlers_for(
        self, node_type: type[ast.AST]
    ) -> tuple[tuple[RuleContext, Handler], ...]:
        cached = self._cache.get(node_type)
        if cached is not None:
            return cached
        collected: list[tuple[RuleContext, Handler]] = []
        for base in node_type.__mro__:
            collected.extend(self._by_type.get(base, ()))
        resolved = tuple(collected)
        self._cache[node_type] = resolved
        return resolved
