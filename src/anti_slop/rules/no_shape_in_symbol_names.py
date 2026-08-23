"""``no-shape-in-symbol-names`` -- reject a banned term in a *declared* name.

PLAN.md section 3.4, row 9. A name like ``compute_shape`` or ``board_shape`` names
what a value *looks like* (its structural shape) instead of what it *is* in the
domain -- a slop name that forces every reader to reconstruct intent from a
structural description. The configurable ``terms`` option (default ``("shape",)``)
lists the banned substrings, matched case-insensitively.

This rule looks only at *declarations* of new names, never at *uses* of existing
ones -- the false-positive surface of a naive substring rule is numpy/pandas'
``array.shape`` and similar attribute accesses, which are legitimate uses of an
existing, well-known attribute, not new names being introduced. Concretely, the rule
checks:

* ``def``/``async def`` function names, and ``class`` names.
* Parameter names of ``def``/``async def`` (all kinds: positional-only, regular,
  ``*args``, keyword-only, ``**kwargs``). Lambda parameters are out of scope --
  lambdas introduce no named declaration this rule can anchor a useful message to.
* Assignment targets that are bare ``ast.Name`` nodes, in ``Assign`` (including
  tuple/list destructuring, recursively), ``AnnAssign`` and ``AugAssign``, and the
  walrus operator (``ast.NamedExpr``).
* The ``asname`` of an ``import``/``from ... import`` alias -- only when an explicit
  ``as`` rename is present. A plain ``import shape_utils`` (no ``as``) is not
  flagged: the bound name there is the *module's* name, not a name this codebase
  chose, and out-of-repo module names are not this rule's business.

Deliberately **not** flagged, because the target is an attribute rather than a new
declared name: ``self.shape = value`` (an ``ast.Attribute`` assignment target) and
any attribute access such as ``array.shape`` (never a declaration to begin with).
String contents are never inspected -- only the AST's own name-carrying nodes.

Known limitations (accepted per PLAN.md section 3.4, row 9 and section 6 -- final
term list is a phase-5 tuning decision, not a phase-1 concern):

* ``match``/``case`` capture patterns (``case shape:``, ``case Point(x=shape):``)
  bind new names but are not walked by this rule.
* ``for``/``async for`` loop targets, comprehension targets and ``with``/``async
  with`` ``as`` targets are not walked either, even though they bind new names --
  only the four categories listed above are in scope for phase 1.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, StrListOption, on

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-shape-in-symbol-names"

_TERMS = StrListOption(
    name="terms",
    default=("shape",),
    description=(
        "Case-insensitive substrings banned from declared names (functions, classes,"
        " parameters, assignment targets, import aliases). An empty list disables the"
        " rule -- the escape hatch for scientific/ML codebases where these terms are"
        " the domain vocabulary."
    ),
)

_MESSAGE = (
    "`{name}` encodes `{term}` -- what the value looks like -- instead of naming the"
    " domain concept it represents. Rename it to what the value *is* in the domain"
    " (e.g. `board`, `image_size`, `tensor_dimensions`), reserving structural terms"
    " for places where the shape genuinely is the domain concept, such as a numpy"
    " array's own `.shape` attribute."
)


def _check_function(
    context: RuleContext, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    terms = context.str_list_option(_TERMS.name)
    if not terms:
        return
    _report_if_matched(context, node, node.name, terms)
    for parameter in _iter_parameters(node.args):
        _report_if_matched(context, parameter, parameter.arg, terms)


def _check_class(context: RuleContext, node: ast.ClassDef) -> None:
    terms = context.str_list_option(_TERMS.name)
    if not terms:
        return
    _report_if_matched(context, node, node.name, terms)


def _check_assign(context: RuleContext, node: ast.Assign) -> None:
    terms = context.str_list_option(_TERMS.name)
    if not terms:
        return
    for target in node.targets:
        _check_target(context, target, terms)


def _check_ann_assign(context: RuleContext, node: ast.AnnAssign) -> None:
    terms = context.str_list_option(_TERMS.name)
    if not terms:
        return
    _check_target(context, node.target, terms)


def _check_aug_assign(context: RuleContext, node: ast.AugAssign) -> None:
    terms = context.str_list_option(_TERMS.name)
    if not terms:
        return
    _check_target(context, node.target, terms)


def _check_named_expr(context: RuleContext, node: ast.NamedExpr) -> None:
    terms = context.str_list_option(_TERMS.name)
    if not terms:
        return
    _check_target(context, node.target, terms)


def _check_import(context: RuleContext, node: ast.Import | ast.ImportFrom) -> None:
    terms = context.str_list_option(_TERMS.name)
    if not terms:
        return
    for alias in node.names:
        if alias.asname is None:
            continue
        _report_if_matched(context, alias, alias.asname, terms)


def _check_target(context: RuleContext, target: ast.expr, terms: Sequence[str]) -> None:
    """Recurse into bare-name assignment targets; skip attributes and subscripts."""
    match target:
        case ast.Name(id=name):
            _report_if_matched(context, target, name, terms)
        case ast.Tuple(elts=elements) | ast.List(elts=elements):
            for element in elements:
                _check_target(context, element, terms)
        case ast.Starred(value=inner):
            _check_target(context, inner, terms)
        case _:
            return


def _report_if_matched(
    context: RuleContext,
    node: ast.expr | ast.stmt | ast.arg | ast.alias,
    name: str,
    terms: Sequence[str],
) -> None:
    term = _matched_term(name, terms)
    if term is not None:
        context.report(node, "shape_in_name", name=name, term=term)


def _matched_term(name: str, terms: Sequence[str]) -> str | None:
    lowered = name.lower()
    for term in terms:
        if term and term.lower() in lowered:
            return term
    return None


def _iter_parameters(arguments: ast.arguments) -> Iterator[ast.arg]:
    """Every declared parameter: positional-only, regular, ``*args``, keyword-only, ``**kwargs``."""
    yield from arguments.posonlyargs
    yield from arguments.args
    if arguments.vararg is not None:
        yield arguments.vararg
    yield from arguments.kwonlyargs
    if arguments.kwarg is not None:
        yield arguments.kwarg


RULE = Rule(
    id=RULE_ID,
    description="A declared name must not encode a banned structural term.",
    messages={"shape_in_name": _MESSAGE},
    handlers=(
        on(ast.FunctionDef, _check_function),
        on(ast.AsyncFunctionDef, _check_function),
        on(ast.ClassDef, _check_class),
        on(ast.Assign, _check_assign),
        on(ast.AnnAssign, _check_ann_assign),
        on(ast.AugAssign, _check_aug_assign),
        on(ast.NamedExpr, _check_named_expr),
        on(ast.Import, _check_import),
        on(ast.ImportFrom, _check_import),
    ),
    options=(_TERMS,),
)
