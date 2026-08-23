"""``no-unsafe-dict-values`` -- reject dict-like annotations with an unsafe value type.

Flags a dict-like container (``dict``, ``Dict``, ``Mapping``, ``MutableMapping``,
``defaultdict`` -- bare name or attribute form, e.g. ``typing.Mapping``,
``collections.defaultdict``) whose value type -- the second element of the
``Container[K, V]`` subscript -- is ``Any``, ``object``, or a union that includes
either as a member (``X | Any``, ``Union[X, object]``, ``Optional[Any]``). Applies in
any annotation position: function parameters, return annotations, and ``AnnAssign``
variable/field annotations, wherever in that annotation tree the container appears
(so ``Optional[dict[str, Any]]`` and ``dict[str, dict[str, Any]]`` are both caught,
each anchored at the specific unsafe subscript).

Since phase 2 the rule also sees through *names*, using the lexical scopes of
``engine/scopes.py``. A name never made the value type any narrower:

    Metadata = dict[str, Any]

    def save(payload: Metadata) -> None: ...   # reported, on the annotation

Both directions are resolved -- the whole annotation being an alias
(``payload: Metadata``) and an alias sitting at the value position
(``dict[str, Value]`` where ``Value = Any``) -- through chains of any length
(``Alias = Metadata``), and with a cycle guard, so the mutually recursive ``A = B`` /
``B = A`` terminates and reports nothing. Resolution is lexical: an alias rebound
inside a function shadows the module-level one, and an alias bound more than once in
the same scope resolves to nothing at all, because this linter does no flow analysis.

Where the *definition* of an alias is reported depends on whether the definition
itself says it is a type. ``X: TypeAlias = dict[str, Any]`` (PEP 613) and
``type X = dict[str, Any]`` (PEP 695) declare a type outright, so the right-hand side
is checked as an annotation right there. A plain ``X = dict[str, Any]`` is
syntactically just an assignment, so it is left alone at the definition site and
reported at each annotation that uses it.

Detection of the value type itself is otherwise purely syntactic (see
``_annotations.py`` for the known limitations of matching ``Any``/``object`` and
dict-container names without import resolution).
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on
from anti_slop.engine.scopes import ScopeTable, scope_table_for
from anti_slop.rules._annotations import (
    container_value_annotation,
    is_slop_value_annotation_via_aliases,
    is_type_alias_marker,
    iter_parameters,
    resolve_alias_chain,
)

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-unsafe-dict-values"

_MESSAGE = (
    "`{annotation}` names an unsafe value type: `{value}` type-checks against"
    " anything, so nothing about a lookup's result is proven anywhere downstream and"
    " every consumer has to re-derive the actual shape by reading the producer."
    " Narrow the value type to a TypedDict, dataclass, model, or specific domain type"
    " -- or, if the shape genuinely varies per key, parse the raw payload into that"
    " type at the I/O boundary and store the parsed value instead of `{value}`."
)

_ALIAS_MESSAGE = (
    "`{alias}` resolves to `{resolved}` in this module, whose value type `{value}`"
    " type-checks against anything -- the name reads like a domain type while proving"
    " nothing about what a lookup returns. Give `{alias}` a narrow value type (a"
    " TypedDict, dataclass, model, or specific domain type) at its definition, or"
    " parse the raw payload into that type at the I/O boundary and annotate the"
    " parsed value here instead."
)


def _check_function(
    context: RuleContext, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    table = scope_table_for(context, node)
    for parameter in iter_parameters(node.args):
        if parameter.annotation is not None:
            _check_annotation_tree(context, table, parameter.annotation)
    if node.returns is not None:
        _check_annotation_tree(context, table, node.returns)


def _check_ann_assign(context: RuleContext, node: ast.AnnAssign) -> None:
    table = scope_table_for(context, node)
    _check_annotation_tree(context, table, node.annotation)
    if node.value is not None and is_type_alias_marker(node.annotation):
        _check_annotation_tree(context, table, node.value)


def _check_type_alias(context: RuleContext, node: ast.TypeAlias) -> None:
    """``type X = dict[str, Any]`` -- PEP 695 declares the right-hand side a type."""
    _check_annotation_tree(context, scope_table_for(context, node), node.value)


def _check_annotation_tree(
    context: RuleContext, table: ScopeTable, annotation: ast.expr
) -> None:
    """Check every dict container and every alias name reachable in ``annotation``."""
    for candidate in ast.walk(annotation):
        match candidate:
            case ast.Subscript() as subscript:
                _check_container(context, table, subscript)
            case ast.Name() as name:
                _check_alias_use(context, table, name)
            case _:
                pass


def _check_container(
    context: RuleContext, table: ScopeTable, subscript: ast.Subscript
) -> None:
    value_annotation = container_value_annotation(subscript)
    if value_annotation is None:
        return
    if not is_slop_value_annotation_via_aliases(table, value_annotation):
        return
    context.report(
        subscript,
        "value",
        annotation=ast.unparse(subscript),
        value=ast.unparse(value_annotation),
    )


def _check_alias_use(context: RuleContext, table: ScopeTable, name: ast.Name) -> None:
    """Report a name that expands to a dict-like type with an unsafe value type."""
    chain = resolve_alias_chain(table, name)
    for expansion in chain[1:]:
        unsafe = _unsafe_container(table, expansion)
        if unsafe is None:
            continue
        value_annotation = container_value_annotation(unsafe)
        if value_annotation is None:
            continue
        context.report(
            name,
            "alias",
            alias=name.id,
            resolved=ast.unparse(unsafe),
            value=ast.unparse(value_annotation),
        )
        return


def _unsafe_container(table: ScopeTable, annotation: ast.expr) -> ast.Subscript | None:
    """The first dict container with an unsafe value type inside ``annotation``."""
    for candidate in ast.walk(annotation):
        match candidate:
            case ast.Subscript() as subscript:
                value_annotation = container_value_annotation(subscript)
                if value_annotation is None:
                    continue
                if is_slop_value_annotation_via_aliases(table, value_annotation):
                    return subscript
            case _:
                pass
    return None


RULE = Rule(
    id=RULE_ID,
    description="Dict-like value types must name a domain type, not `Any`/`object`.",
    messages={"alias": _ALIAS_MESSAGE, "value": _MESSAGE},
    handlers=(
        on(ast.FunctionDef, _check_function),
        on(ast.AsyncFunctionDef, _check_function),
        on(ast.AnnAssign, _check_ann_assign),
        on(ast.TypeAlias, _check_type_alias),
    ),
)
