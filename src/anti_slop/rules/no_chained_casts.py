"""``no-chained-casts`` -- reject a ``typing.cast`` whose value is itself a ``cast``.

Port of ``no-chained-type-assertions``.

Callee detection is deliberately broad: a bare name ``cast`` or *any* attribute
access ending in ``.cast`` (``typing.cast``, ``t.cast``, and -- as a known
false-positive surface -- unrelated ``.cast`` methods such as SQLAlchemy's ``cast()``
helper or a builder method literally named ``cast``). Narrowing this to imports
actually resolving to ``typing.cast`` needs scope resolution, which is out of reach
for this syntax-only rule.

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
from anti_slop.engine.rule import (
    CONFIDENCE_HIGH,
    FIX_NONE,
    TIER_ESCAPE_HATCH,
    Rule,
    RuleMetadata,
    on,
)

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


_METADATA = RuleMetadata(
    tier=TIER_ESCAPE_HATCH,
    confidence=CONFIDENCE_HIGH,
    fix=FIX_NONE,
    tags=("casts", "typing"),
    problem=(
        "A single `cast(Target, value)` already asks the reader to trust an unproven"
        " claim. Wrapping another `cast` around it compounds that trust without"
        " adding any evidence: the inner cast makes the checker accept a type, and"
        " the outer one overrides even that. The cascade exists precisely because no"
        " single step could be justified -- it is the shape a type error takes after"
        " it has been argued with rather than fixed."
    ),
    recipe=(
        "Parse the value into the target type once, at the boundary where it enters"
        " this function or module -- a dataclass or TypedDict constructor, a"
        " pydantic/msgspec model, an explicit `isinstance` check -- and pass the"
        " already-typed value through instead of re-casting it. If the intermediate"
        " step exists only to reach a function that widens the value, give that"
        " function a narrow parameter type instead."
    ),
    when_to_disable=(
        "Hard to justify as policy: a cast cascade is not an idiom any codebase"
        " depends on. The realistic reason to relax it is a codebase whose `cast` is"
        " not `typing.cast` at all -- SQLAlchemy's `cast()`, or a builder method"
        " named `cast` -- where the finding is a naming collision rather than a"
        " defect; suppress those lines by rule id, or set the rule to `off` if that"
        " collision is pervasive."
    ),
    fp_caveats=(
        "Callee detection is syntactic: a bare `cast` name or any attribute access"
        " ending in `.cast`, with no import resolution behind it, so an unrelated"
        " helper named `cast` is flagged as well. A triple nesting reports twice --"
        " once for the outer call and once for the middle one -- because each call is"
        " judged independently; only the innermost call, whose own value is not a"
        " cast, stays silent."
    ),
)

RULE = Rule(
    id=RULE_ID,
    description="A `cast` call must not take another `cast` call as its value.",
    messages={"chained_cast": _MESSAGE},
    handlers=(on(ast.Call, _check_call),),
    metadata=_METADATA,
)
