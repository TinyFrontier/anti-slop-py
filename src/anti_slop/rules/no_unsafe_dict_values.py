"""``no-unsafe-dict-values`` -- reject dict-like annotations with an unsafe value type
(PLAN.md section 3.4, row 13).

Flags a dict-like container (``dict``, ``Dict``, ``Mapping``, ``MutableMapping``,
``defaultdict`` -- bare name or attribute form, e.g. ``typing.Mapping``,
``collections.defaultdict``) whose value type -- the second element of the
``Container[K, V]`` subscript -- is ``Any``, ``object``, or a union that includes
either as a member (``X | Any``, ``Union[X, object]``, ``Optional[Any]``). Applies in
any annotation position: function parameters, return annotations, and ``AnnAssign``
variable/field annotations, wherever in that annotation tree the container appears
(so ``Optional[dict[str, Any]]`` and ``dict[str, dict[str, Any]]`` are both caught,
each anchored at the specific unsafe subscript).

Alias resolution (``Metadata = dict[str, Any]``, then later ``x: Metadata``) is out of
scope for phase 1 -- it needs lexical scope/import resolution, which arrives with
``scopes.py`` in phase 2 (PLAN.md section 5). A direct ``X: TypeAlias = dict[str,
Any]`` still triggers, though: PEP 613's right-hand side *is* the type syntactically,
so whenever the declared annotation is the ``TypeAlias`` marker itself, this rule also
walks the assigned value as if it were the annotation. A bare ``Metadata = dict[str,
Any]`` (no ``TypeAlias`` annotation at all) is a plain ``Assign``, which this rule does
not subscribe to, so the alias *definition* site is left untouched too -- consistent
with alias resolution being entirely phase 2's job.

Detection of the value type itself is purely syntactic (see ``_annotations.py`` for
the known limitations of matching ``Any``/``object`` and dict-container names without
import resolution).
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on
from anti_slop.rules._annotations import (
    container_value_annotation,
    is_slop_value_annotation,
    iter_parameters,
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


def _check_function(
    context: RuleContext, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    for parameter in iter_parameters(node.args):
        if parameter.annotation is not None:
            _check_annotation_tree(context, parameter.annotation)
    if node.returns is not None:
        _check_annotation_tree(context, node.returns)


def _check_ann_assign(context: RuleContext, node: ast.AnnAssign) -> None:
    _check_annotation_tree(context, node.annotation)
    if node.value is not None and _is_type_alias_marker(node.annotation):
        _check_annotation_tree(context, node.value)


def _check_annotation_tree(context: RuleContext, annotation: ast.expr) -> None:
    """Check every dict-container subscript reachable inside ``annotation``."""
    for candidate in ast.walk(annotation):
        if not isinstance(candidate, ast.Subscript):
            continue
        value_annotation = container_value_annotation(candidate)
        if value_annotation is None:
            continue
        if not is_slop_value_annotation(value_annotation):
            continue
        context.report(
            candidate,
            "value",
            annotation=ast.unparse(candidate),
            value=ast.unparse(value_annotation),
        )


def _is_type_alias_marker(annotation: ast.expr) -> bool:
    """True for the PEP 613 ``TypeAlias`` marker, bare or as ``typing.TypeAlias``."""
    match annotation:
        case ast.Name(id="TypeAlias"):
            return True
        case ast.Attribute(attr="TypeAlias"):
            return True
        case _:
            return False


RULE = Rule(
    id=RULE_ID,
    description="Dict-like value types must name a domain type, not `Any`/`object`.",
    messages={"value": _MESSAGE},
    handlers=(
        on(ast.FunctionDef, _check_function),
        on(ast.AsyncFunctionDef, _check_function),
        on(ast.AnnAssign, _check_ann_assign),
    ),
)
