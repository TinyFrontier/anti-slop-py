"""``no-any-returns`` -- reject a return contract of ``Any`` (PLAN.md section 3.4, row 11).

Flags a return annotation that is directly ``Any``, and also ``Awaitable[Any]`` /
``Coroutine[..., Any]`` -- an async contract whose *result* type is erased to ``Any``
even though the coroutine/awaitable wrapper itself is named. The result type is the
last element of the subscript in both cases (``Awaitable`` takes one type argument,
``Coroutine[YieldType, SendType, ReturnType]`` takes three), matching the original
anti-slop's ``no-unknown-returns``, which bans the equivalent ``Promise<unknown>``.

Applies to both ``def`` and ``async def``. Detection is purely syntactic (see
``_annotations.py`` for the known limitations of matching ``Any`` without import
resolution).
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on
from anti_slop.rules._annotations import is_any_annotation

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-any-returns"

# `Awaitable[X]` and `Coroutine[_, _, X]`: the wrapper names whose last subscript
# argument is the actual result type this rule cares about.
_AWAITABLE_NAMES = frozenset({"Awaitable", "Coroutine"})

_MESSAGE = (
    "The return annotation is `{annotation}`: the contract names no domain type, so"
    " every caller receives proven nothing and has to re-derive what this function"
    " actually returns from the implementation. Name the domain type this function"
    " returns -- a dataclass, TypedDict, Protocol, or a narrow union. For an async"
    " contract, keep `Awaitable`/`Coroutine` but give its result type argument a real"
    " type instead of `Any`."
)


def _check_function(
    context: RuleContext, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    annotation = node.returns
    if annotation is None:
        return
    if not _is_slop_return_annotation(annotation):
        return
    context.report(annotation, "return", annotation=ast.unparse(annotation))


def _is_slop_return_annotation(annotation: ast.expr) -> bool:
    if is_any_annotation(annotation):
        return True
    result_type = _awaitable_result_type(annotation)
    return result_type is not None and is_any_annotation(result_type)


def _awaitable_result_type(annotation: ast.expr) -> ast.expr | None:
    """The result-type argument of ``Awaitable[X]``/``Coroutine[_, _, X]``, if any."""
    match annotation:
        case ast.Subscript(value=value, slice=slice_expr) if _is_awaitable_name(value):
            match slice_expr:
                case ast.Tuple(elts=[*_, last]):
                    return last
                case ast.Tuple():
                    return None
                case _:
                    return slice_expr
        case _:
            return None


def _is_awaitable_name(value: ast.expr) -> bool:
    match value:
        case ast.Name(id=name) if name in _AWAITABLE_NAMES:
            return True
        case ast.Attribute(attr=attr) if attr in _AWAITABLE_NAMES:
            return True
        case _:
            return False


RULE = Rule(
    id=RULE_ID,
    description="Return annotations must name a domain type, not `Any`.",
    messages={"return": _MESSAGE},
    handlers=(
        on(ast.FunctionDef, _check_function),
        on(ast.AsyncFunctionDef, _check_function),
    ),
)
