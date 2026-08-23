"""``no-chained-casts`` -- reject a ``typing.cast`` whose value is itself a ``cast``.

PLAN.md section 3.4, row 1 (port of ``no-chained-type-assertions``). A single
``cast(Target, value)`` already asks the reader to trust an unproven claim; wrapping
another ``cast`` around it compounds that trust without adding evidence -- it just
makes the type checker agree with the previous, equally unproven claim. The value
should be parsed into the target type exactly once, at the boundary where it enters
the system.

Callee detection is deliberately broad, matching the plan: a bare name ``cast`` or
*any* attribute access ending in ``.cast`` (``typing.cast``, ``t.cast``, and -- as a
known false-positive surface -- unrelated ``.cast`` methods such as SQLAlchemy's
``cast()`` helper or a builder method literally named ``cast``). Narrowing this to
imports actually resolving to ``typing.cast`` needs scope resolution, which is out of
reach for this syntax-only rule; see PLAN.md section 3.2 (``scopes.py``, phase 2).

The value argument is the second positional argument, or the ``val`` keyword
argument, mirroring ``typing.cast(typ, val)``'s signature. Parentheses are invisible
to the AST, so ``cast(X, (cast(Y, v)))`` is caught by the same check as the
unparenthesized form.

Each ``cast(...)`` call is checked independently as the walker visits it, so a triple
nesting such as ``cast(A, cast(B, cast(C, v)))`` reports twice: once for the outer
call (whose value is the middle call) and once for the middle call (whose value is
the innermost call). The innermost call is not itself flagged, since its own value is
not a ``cast``.
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-chained-casts"

_MESSAGE = (
    "This `cast` wraps another `cast` as its value: cascading casts fabricate"
    " evidence, each nested `cast` just makes the type checker agree with the"
    " previous, equally unproven claim. Parse the value into the target type once,"
    " at the boundary where it enters this function or module, and pass the"
    " already-typed value through instead of re-casting it."
)


def _check_call(context: RuleContext, node: ast.Call) -> None:
    if not _is_cast_callee(node.func):
        return
    value = _value_argument(node)
    if value is None:
        return
    if _is_cast_call(value):
        context.report(node, "chained_cast")


def _value_argument(call: ast.Call) -> ast.expr | None:
    """The value passed to ``cast(typ, val)``: the second positional arg, or ``val=``."""
    if len(call.args) >= 2:
        return call.args[1]
    for keyword in call.keywords:
        if keyword.arg == "val":
            return keyword.value
    return None


def _is_cast_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and _is_cast_callee(node.func)


def _is_cast_callee(func: ast.expr) -> bool:
    """A bare ``cast`` name or any attribute access ending in ``.cast``."""
    match func:
        case ast.Name(id="cast"):
            return True
        case ast.Attribute(attr="cast"):
            return True
        case _:
            return False


RULE = Rule(
    id=RULE_ID,
    description="A `cast` call must not take another `cast` call as its value.",
    messages={"chained_cast": _MESSAGE},
    handlers=(on(ast.Call, _check_call),),
)
