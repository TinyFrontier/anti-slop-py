"""Lexical name resolution: what does *this* name mean *here*.

A rule that only pattern-matches syntax cannot tell ``patch("pkg.mod.fn")`` (the
``unittest.mock`` escape hatch) from a local helper that happens to be called
``patch``, nor ``payload: Metadata`` from ``payload: UserId`` when ``Metadata`` is an
alias of ``dict[str, Any]``. This module answers both questions the way the original
anti-slop does -- purely lexically, within one module, with no cross-module
inference and no type checker:

* :func:`build_scope_table` walks a parsed module once and records, for every scope
  (module / function / class / comprehension / PEP 695 type parameters), which names
  it binds and to what.
* :meth:`ScopeTable.resolve` answers "which binding does this name reach from this
  node", following Python's own lookup order -- including the rule that a class body
  is *not* visible from functions nested inside it.
* :meth:`ScopeTable.qualified_name` turns an expression like ``mock.patch.object``
  into the dotted path it was imported from (``unittest.mock.patch.object``),
  resolving import aliases on the way.

Usage from a rule handler::

    from anti_slop.engine.scopes import ImportBinding, scope_table_for

    def _check_call(context: RuleContext, node: ast.Call) -> None:
        table = scope_table_for(context, node)
        if table.qualified_name(node.func) == "unittest.mock.patch":
            context.report(node, "module-mock")

:func:`scope_table_for` finds the enclosing module through the walker's parent links
and caches one table per module, so every rule in the single pass shares one table.

Deliberate limits, all of which make a rule see *less* rather than claim more:

* A name bound more than once in the same scope (``x = 1`` then ``x = load()``)
  resolves to ``None``: this module does no flow analysis, so it refuses to guess
  which binding is live.
* ``global`` / ``nonlocal`` declarations, loop and ``with`` targets, ``except`` names,
  unpacking and ``match`` captures bind an :class:`OpaqueBinding`: the name is known
  to be bound here, but not to what.
* Relative imports (``from . import mock``) have no resolvable dotted path -- there is
  no package context in a single-module analysis -- so their ``qualified`` is ``None``.
* ``from x import *`` binds nothing.
* A walrus inside a comprehension is recorded in the comprehension scope, where Python
  would leak it to the enclosing function; from outside the comprehension the name
  then resolves to ``None`` rather than to the assignment.
* ``del name`` does not retract a binding: the table describes what a module *writes*,
  not what is live at a given point.
"""

from __future__ import annotations

import ast
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

if TYPE_CHECKING:
    from anti_slop.engine.context import RuleContext

__all__ = [
    "AssignmentBinding",
    "Binding",
    "DefinitionBinding",
    "ImportBinding",
    "OpaqueBinding",
    "ParameterBinding",
    "Scope",
    "ScopeKind",
    "ScopeTable",
    "TypeAliasBinding",
    "TypeParameterBinding",
    "build_scope_table",
    "dotted_parts",
    "scope_table_for",
]


class ScopeKind(Enum):
    """The kind of lexical scope a :class:`Scope` stands for."""

    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"
    COMPREHENSION = "comprehension"
    TYPE_PARAMS = "type-params"


@dataclass(frozen=True, slots=True)
class ImportBinding:
    """A name bound by ``import`` or ``from ... import``."""

    name: str
    """The local name this binding introduces."""

    module: str | None
    """``M`` in ``import M`` / ``from M import n``; ``None`` for ``from . import n``."""

    imported_name: str | None
    """``n`` in ``from M import n``; ``None`` when a whole module was imported."""

    level: int
    """Leading-dot count of a relative import; ``0`` for absolute imports."""

    node: ast.alias

    @property
    def qualified(self) -> str | None:
        """The dotted path the local name refers to, or ``None`` when unresolvable.

        ``from unittest.mock import patch as p`` -> ``unittest.mock.patch``;
        ``import unittest.mock`` -> ``unittest`` (the name it actually binds);
        ``import unittest.mock as m`` -> ``unittest.mock``.
        """
        if self.module is None or self.level > 0:
            return None
        if self.imported_name is not None:
            return f"{self.module}.{self.imported_name}"
        if self.node.asname is not None:
            return self.module
        return self.module.partition(".")[0]


@dataclass(frozen=True, slots=True)
class AssignmentBinding:
    """A name bound by ``x = value``, ``x: T = value`` or ``(x := value)``."""

    name: str
    value: ast.expr | None
    """The assigned expression; ``None`` for a bare ``x: T`` declaration."""

    annotation: ast.expr | None
    """The declared annotation, when the binding came from an ``AnnAssign``."""

    node: ast.stmt | ast.expr


@dataclass(frozen=True, slots=True)
class TypeAliasBinding:
    """A name bound by the PEP 695 ``type X = ...`` statement."""

    name: str
    value: ast.expr
    node: ast.TypeAlias


@dataclass(frozen=True, slots=True)
class DefinitionBinding:
    """A name bound by ``def`` / ``async def`` / ``class``."""

    name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


@dataclass(frozen=True, slots=True)
class ParameterBinding:
    """A name bound as a parameter of the function or lambda it belongs to.

    This is how a rule recognizes a pytest fixture: ``mocker`` in
    ``def test_x(mocker): ...`` is a parameter, whereas a module-level ``mocker``
    would be an import or an assignment.
    """

    name: str
    node: ast.arg
    function: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


@dataclass(frozen=True, slots=True)
class TypeParameterBinding:
    """A name bound by a PEP 695 type parameter list (``def f[T]``, ``class C[T]``)."""

    name: str
    node: ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple


@dataclass(frozen=True, slots=True)
class OpaqueBinding:
    """The name is bound here, but this module does not track to what.

    Loop and ``with`` targets, ``except`` names, unpacking, augmented assignment,
    ``match`` captures and ``global`` / ``nonlocal`` declarations all land here. A
    rule that asks "is this name still the import I saw at the top of the file?" gets
    a definite "no" -- which is the answer that keeps it quiet rather than wrong.
    """

    name: str
    node: ast.AST


type Binding = (
    ImportBinding
    | AssignmentBinding
    | TypeAliasBinding
    | DefinitionBinding
    | ParameterBinding
    | TypeParameterBinding
    | OpaqueBinding
)


class Scope:
    """One lexical scope and the names it binds.

    Built by :func:`build_scope_table` and read-only afterwards: :meth:`declare` is
    part of the construction protocol, not of the query API.
    """

    __slots__ = ("_bindings", "_kind", "_node", "_parent")

    def __init__(self, kind: ScopeKind, node: ast.AST, parent: Scope | None) -> None:
        self._kind = kind
        self._node = node
        self._parent = parent
        self._bindings: dict[str, list[Binding]] = {}

    @property
    def kind(self) -> ScopeKind:
        return self._kind

    @property
    def node(self) -> ast.AST:
        """The syntax node this scope belongs to (module, function, class, ...)."""
        return self._node

    @property
    def parent(self) -> Scope | None:
        return self._parent

    @property
    def names(self) -> frozenset[str]:
        """Every name bound in this scope, however many times."""
        return frozenset(self._bindings)

    def declare(self, binding: Binding) -> None:
        """Record ``binding`` in this scope (construction only)."""
        self._bindings.setdefault(binding.name, []).append(binding)

    def declares(self, name: str) -> bool:
        """True when this scope binds ``name`` at all -- ambiguously or not."""
        return name in self._bindings

    def bindings(self, name: str) -> tuple[Binding, ...]:
        """Every binding of ``name`` in this scope, in source order."""
        return tuple(self._bindings.get(name, ()))

    def binding(self, name: str) -> Binding | None:
        """The single binding of ``name``, or ``None`` when absent or ambiguous."""
        found = self._bindings.get(name)
        if found is None or len(found) != 1:
            return None
        return found[0]

    def __repr__(self) -> str:
        return f"Scope({self._kind.value}, names={sorted(self._bindings)})"


class ScopeTable:
    """The scopes of one module, plus the node-to-scope index built alongside them."""

    __slots__ = ("_module_scope", "_scopes")

    def __init__(self, module_scope: Scope, scopes: dict[ast.AST, Scope]) -> None:
        self._module_scope = module_scope
        self._scopes = scopes

    @property
    def module_scope(self) -> Scope:
        return self._module_scope

    def scope_of(self, node: ast.AST) -> Scope:
        """The scope ``node`` is written in; the module scope when it is unknown.

        Annotations, decorators and parameter defaults belong to the *enclosing*
        scope, matching Python's evaluation rules, so a name written in a signature
        resolves the way the interpreter would resolve it.
        """
        return self._scopes.get(node, self._module_scope)

    def resolve(self, name: str, node: ast.AST) -> Binding | None:
        """The binding ``name`` reaches from ``node``, or ``None`` when unknown."""
        return self.resolve_in(name, self.scope_of(node))

    def resolve_in(self, name: str, scope: Scope) -> Binding | None:
        """The binding ``name`` reaches from ``scope``, or ``None`` when unknown.

        ``None`` means "this module cannot say": the name is unbound (a builtin, or
        injected from elsewhere), bound more than once in the scope that owns it, or
        bound opaquely. Rules must read ``None`` as *no claim*, never as *absent*.
        """
        current: Scope | None = scope
        innermost = True
        while current is not None:
            visible = innermost or current.kind is not ScopeKind.CLASS
            if visible and current.declares(name):
                return current.binding(name)
            current = current.parent
            innermost = False
        return None

    def qualified_name(self, expr: ast.expr) -> str | None:
        """The dotted path ``expr`` names, resolved through imports.

        ``patch`` after ``from unittest.mock import patch`` -> ``unittest.mock.patch``;
        ``mock.patch.object`` after ``from unittest import mock`` ->
        ``unittest.mock.patch.object``. Returns ``None`` unless the root of the
        attribute chain resolves to an import with a resolvable path -- a locally
        rebound or unbound root proves nothing about what the expression refers to.
        """
        parts = dotted_parts(expr)
        if parts is None:
            return None
        root, *attributes = parts
        match self.resolve(root, expr):
            case ImportBinding() as imported:
                base = imported.qualified
                if base is None:
                    return None
                return ".".join((base, *attributes))
            case _:
                return None


def dotted_parts(expr: ast.expr) -> tuple[str, ...] | None:
    """Split ``a.b.c`` into ``("a", "b", "c")``; ``None`` if not a pure name chain."""
    attributes: list[str] = []
    current: ast.expr = expr
    while True:
        match current:
            case ast.Attribute(value=value, attr=attr):
                attributes.append(attr)
                current = value
            case ast.Name(id=name):
                attributes.append(name)
                attributes.reverse()
                return tuple(attributes)
            case _:
                return None


def build_scope_table(module: ast.Module) -> ScopeTable:
    """Index every scope and binding of ``module``."""
    return _Builder().build(module)


def scope_table_for(context: RuleContext, node: ast.AST) -> ScopeTable:
    """The scope table of the module ``node`` belongs to, built once per module.

    The enclosing module is found through the walker's parent links, so this works
    from any handler without the rule having to subscribe to ``ast.Module``.

    Pass the node the handler was *dispatched on*. The walker records the parent link
    of a node just before descending into it, so an ancestor chain exists for the
    dispatched node but not yet for the children a handler reaches into by itself.
    Build the table once at the top of the handler and thread it down.
    """
    module = _enclosing_module(context, node)
    with _CACHE_LOCK:
        cached = _CACHE.get(module)
    if cached is not None:
        return cached
    table = build_scope_table(module)
    with _CACHE_LOCK:
        _CACHE[module] = table
    return table


# One table per parsed module, dropped as soon as the tree is. Building twice under a
# race is harmless -- a table is immutable once built -- but the mapping itself is
# guarded, since a future parallel runner (phase 3) may share this process.
_CACHE: WeakKeyDictionary[ast.Module, ScopeTable] = WeakKeyDictionary()
_CACHE_LOCK = threading.Lock()

# Fallback for a node with no recorded parent chain: an empty module binds nothing,
# so every resolution against it answers "no claim" instead of raising mid-run.
_DETACHED_MODULE = ast.Module(body=[], type_ignores=[])


def _enclosing_module(context: RuleContext, node: ast.AST) -> ast.Module:
    match node:
        case ast.Module() as module:
            return module
        case _:
            pass
    outermost: ast.AST | None = None
    for ancestor in context.ancestors(node):
        outermost = ancestor
    match outermost:
        case ast.Module() as module:
            return module
        case _:
            return _DETACHED_MODULE


class _Builder:
    """Single-pass construction of a :class:`ScopeTable`."""

    __slots__ = ("_scopes",)

    def __init__(self) -> None:
        self._scopes: dict[ast.AST, Scope] = {}

    def build(self, module: ast.Module) -> ScopeTable:
        module_scope = Scope(ScopeKind.MODULE, module, None)
        self._scopes[module] = module_scope
        for statement in module.body:
            self._visit(statement, module_scope)
        return ScopeTable(module_scope, self._scopes)

    def _visit(self, node: ast.AST, scope: Scope) -> None:
        self._scopes[node] = scope
        match node:
            case ast.FunctionDef() | ast.AsyncFunctionDef() as function:
                self._visit_function(function, scope)
            case ast.Lambda() as function:
                self._visit_lambda(function, scope)
            case ast.ClassDef() as definition:
                self._visit_class(definition, scope)
            case ast.Import(names=names):
                self._visit_import(names, scope)
            case ast.ImportFrom(module=module, names=names, level=level):
                self._visit_import_from(module, names, level, scope)
            case ast.Assign() as assignment:
                self._visit_assign(assignment, scope)
            case ast.AnnAssign() as assignment:
                self._visit_ann_assign(assignment, scope)
            case ast.AugAssign(target=target, value=value):
                self._visit(value, scope)
                self._bind_target(target, scope, node)
            case ast.NamedExpr() as assignment:
                self._visit_named_expr(assignment, scope)
            case ast.TypeAlias() as alias:
                self._visit_type_alias(alias, scope)
            case ast.For() | ast.AsyncFor() as loop:
                self._visit_loop(loop, scope)
            case ast.With() | ast.AsyncWith() as block:
                self._visit_with(block, scope)
            case ast.ExceptHandler() as handler:
                self._visit_except_handler(handler, scope)
            case ast.Global(names=names) | ast.Nonlocal(names=names):
                for name in names:
                    scope.declare(OpaqueBinding(name=name, node=node))
            case ast.ListComp() | ast.SetComp() | ast.GeneratorExp() as comprehension:
                self._visit_comprehension(comprehension, scope)
            case ast.DictComp() as comprehension:
                self._visit_dict_comprehension(comprehension, scope)
            case ast.MatchAs(pattern=pattern, name=name):
                if pattern is not None:
                    self._visit(pattern, scope)
                if name is not None:
                    scope.declare(OpaqueBinding(name=name, node=node))
            case ast.MatchStar(name=name):
                if name is not None:
                    scope.declare(OpaqueBinding(name=name, node=node))
            case ast.MatchMapping(rest=rest) as pattern:
                self._visit_children(pattern, scope)
                if rest is not None:
                    scope.declare(OpaqueBinding(name=rest, node=node))
            case _:
                self._visit_children(node, scope)

    def _visit_children(self, node: ast.AST, scope: Scope) -> None:
        for child in ast.iter_child_nodes(node):
            self._visit(child, scope)

    def _visit_import(self, names: Sequence[ast.alias], scope: Scope) -> None:
        for alias in names:
            self._scopes[alias] = scope
            local = alias.asname if alias.asname is not None else alias.name
            scope.declare(
                ImportBinding(
                    name=local.partition(".")[0],
                    module=alias.name,
                    imported_name=None,
                    level=0,
                    node=alias,
                )
            )

    def _visit_import_from(
        self,
        module: str | None,
        names: Sequence[ast.alias],
        level: int,
        scope: Scope,
    ) -> None:
        for alias in names:
            self._scopes[alias] = scope
            if alias.name == "*":
                continue
            local = alias.asname if alias.asname is not None else alias.name
            scope.declare(
                ImportBinding(
                    name=local,
                    module=module,
                    imported_name=alias.name,
                    level=level,
                    node=alias,
                )
            )

    def _visit_assign(self, node: ast.Assign, scope: Scope) -> None:
        self._visit(node.value, scope)
        for target in node.targets:
            match target:
                case ast.Name(id=name):
                    self._scopes[target] = scope
                    scope.declare(
                        AssignmentBinding(
                            name=name, value=node.value, annotation=None, node=node
                        )
                    )
                case _:
                    self._bind_target(target, scope, node)

    def _visit_ann_assign(self, node: ast.AnnAssign, scope: Scope) -> None:
        self._visit(node.annotation, scope)
        if node.value is not None:
            self._visit(node.value, scope)
        match node.target:
            case ast.Name(id=name) as target:
                self._scopes[target] = scope
                scope.declare(
                    AssignmentBinding(
                        name=name,
                        value=node.value,
                        annotation=node.annotation,
                        node=node,
                    )
                )
            case _:
                self._visit(node.target, scope)

    def _visit_named_expr(self, node: ast.NamedExpr, scope: Scope) -> None:
        self._visit(node.value, scope)
        match node.target:
            case ast.Name(id=name) as target:
                self._scopes[target] = scope
                scope.declare(
                    AssignmentBinding(
                        name=name, value=node.value, annotation=None, node=node
                    )
                )
            case _:
                self._visit(node.target, scope)

    def _visit_type_alias(self, node: ast.TypeAlias, scope: Scope) -> None:
        match node.name:
            case ast.Name(id=name) as target:
                self._scopes[target] = scope
                scope.declare(TypeAliasBinding(name=name, value=node.value, node=node))
            case _:
                self._visit(node.name, scope)
        header = self._type_param_scope(node, node.type_params, scope)
        self._visit(node.value, header)

    def _visit_loop(self, node: ast.For | ast.AsyncFor, scope: Scope) -> None:
        self._visit(node.iter, scope)
        self._bind_target(node.target, scope, node)
        for statement in (*node.body, *node.orelse):
            self._visit(statement, scope)

    def _visit_with(self, node: ast.With | ast.AsyncWith, scope: Scope) -> None:
        for item in node.items:
            self._scopes[item] = scope
            self._visit(item.context_expr, scope)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars, scope, node)
        for statement in node.body:
            self._visit(statement, scope)

    def _visit_except_handler(self, node: ast.ExceptHandler, scope: Scope) -> None:
        if node.type is not None:
            self._visit(node.type, scope)
        if node.name is not None:
            scope.declare(OpaqueBinding(name=node.name, node=node))
        for statement in node.body:
            self._visit(statement, scope)

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.GeneratorExp,
        scope: Scope,
    ) -> None:
        inner = Scope(ScopeKind.COMPREHENSION, node, scope)
        self._visit_generators(node.generators, scope, inner)
        self._visit(node.elt, inner)

    def _visit_dict_comprehension(self, node: ast.DictComp, scope: Scope) -> None:
        inner = Scope(ScopeKind.COMPREHENSION, node, scope)
        self._visit_generators(node.generators, scope, inner)
        self._visit(node.key, inner)
        self._visit(node.value, inner)

    def _visit_generators(
        self,
        generators: Sequence[ast.comprehension],
        outer: Scope,
        inner: Scope,
    ) -> None:
        for index, generator in enumerate(generators):
            self._scopes[generator] = inner
            # Only the leftmost iterable is evaluated in the enclosing scope.
            self._visit(generator.iter, outer if index == 0 else inner)
            self._bind_target(generator.target, inner, generator)
            for condition in generator.ifs:
                self._visit(condition, inner)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, scope: Scope
    ) -> None:
        scope.declare(DefinitionBinding(name=node.name, node=node))
        for decorator in node.decorator_list:
            self._visit(decorator, scope)
        header = self._type_param_scope(node, node.type_params, scope)
        for parameter in _iter_args(node.args):
            if parameter.annotation is not None:
                self._visit(parameter.annotation, header)
        self._visit_defaults(node.args, header)
        if node.returns is not None:
            self._visit(node.returns, header)
        inner = Scope(ScopeKind.FUNCTION, node, header)
        self._bind_parameters(node.args, inner, node)
        for statement in node.body:
            self._visit(statement, inner)

    def _visit_lambda(self, node: ast.Lambda, scope: Scope) -> None:
        self._visit_defaults(node.args, scope)
        inner = Scope(ScopeKind.FUNCTION, node, scope)
        self._bind_parameters(node.args, inner, node)
        self._visit(node.body, inner)

    def _visit_class(self, node: ast.ClassDef, scope: Scope) -> None:
        scope.declare(DefinitionBinding(name=node.name, node=node))
        for decorator in node.decorator_list:
            self._visit(decorator, scope)
        header = self._type_param_scope(node, node.type_params, scope)
        for base in node.bases:
            self._visit(base, header)
        for keyword in node.keywords:
            self._visit(keyword, header)
        inner = Scope(ScopeKind.CLASS, node, header)
        for statement in node.body:
            self._visit(statement, inner)

    def _visit_defaults(self, arguments: ast.arguments, scope: Scope) -> None:
        for default in arguments.defaults:
            self._visit(default, scope)
        for default in arguments.kw_defaults:
            if default is not None:
                self._visit(default, scope)

    def _bind_parameters(
        self,
        arguments: ast.arguments,
        scope: Scope,
        function: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    ) -> None:
        self._scopes[arguments] = scope
        for parameter in _iter_args(arguments):
            self._scopes[parameter] = scope
            scope.declare(
                ParameterBinding(
                    name=parameter.arg, node=parameter, function=function
                )
            )

    def _type_param_scope(
        self,
        node: ast.AST,
        type_params: Sequence[ast.type_param],
        scope: Scope,
    ) -> Scope:
        """The PEP 695 scope holding ``type_params``; ``scope`` when there are none."""
        if not type_params:
            return scope
        inner = Scope(ScopeKind.TYPE_PARAMS, node, scope)
        for parameter in type_params:
            self._scopes[parameter] = inner
            match parameter:
                case ast.TypeVar(bound=bound) as type_var:
                    inner.declare(
                        TypeParameterBinding(name=type_var.name, node=type_var)
                    )
                    if bound is not None:
                        self._visit(bound, inner)
                case ast.ParamSpec() | ast.TypeVarTuple() as variadic:
                    inner.declare(
                        TypeParameterBinding(name=variadic.name, node=variadic)
                    )
                case _:
                    pass
        return inner

    def _bind_target(self, target: ast.expr, scope: Scope, owner: ast.AST) -> None:
        """Bind every name in an assignment-like ``target`` opaquely."""
        self._scopes[target] = scope
        match target:
            case ast.Name(id=name):
                scope.declare(OpaqueBinding(name=name, node=owner))
            case ast.Tuple(elts=elements) | ast.List(elts=elements):
                for element in elements:
                    self._bind_target(element, scope, owner)
            case ast.Starred(value=value):
                self._bind_target(value, scope, owner)
            case _:
                # `obj.attr = ...` / `obj[key] = ...` bind no new name; the target is
                # an ordinary load expression as far as name resolution goes.
                self._visit_children(target, scope)


def _iter_args(arguments: ast.arguments) -> Iterator[ast.arg]:
    yield from arguments.posonlyargs
    yield from arguments.args
    if arguments.vararg is not None:
        yield arguments.vararg
    yield from arguments.kwonlyargs
    if arguments.kwarg is not None:
        yield arguments.kwarg
