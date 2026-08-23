"""``no-known-value-widening`` -- reject a literal value under an `Any`/`object` annotation.

Port of ``no-known-value-widening``. Flags an ``ast.AnnAssign`` whose annotation is
``Any`` or ``object`` and whose value is a syntactic literal (``ast.Dict``,
``ast.List``, ``ast.Set``, ``ast.Tuple`` or ``ast.Constant``).

The original TypeScript rule also special-cases object literals under a *narrower*
mapped/index-signature type, on the theory that TypeScript erases literal keys past
that boundary. That half of the rule does **not** port: Python's type checkers do not
infer literal dict keys in the first place, so ``handlers: dict[str, Handler] = {...}``
is already idiomatic and stays unflagged -- there is no narrower-but-still-lossy type
to compare against, only ``Any``/``object``.

Three deliberate exclusions, all because "syntactic literal" is a purely
AST-node-type check rather than a "provably constant" check:

* A call that *builds* a literal-looking value, e.g. ``x: Any = dict(a=1)``, is
  ``ast.Call``, not ``ast.Dict`` -- not flagged. Only literal *syntax* counts, not
  values a human would recognise as effectively constant.
* An f-string, e.g. ``x: object = f"{a}"``, is ``ast.JoinedStr``, not
  ``ast.Constant`` -- not flagged, since its actual value depends on interpolating
  ``a`` and is not statically known even though the template syntax is visible.
* A unary-negated numeric literal, e.g. ``x: Any = -1``, is ``ast.UnaryOp`` wrapping
  an ``ast.Constant``, not an ``ast.Constant`` itself -- not flagged, by the same
  strict node-type reading.
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

RULE_ID = "no-known-value-widening"

_LITERAL_TYPES = (ast.Dict, ast.List, ast.Set, ast.Tuple, ast.Constant)

_MESSAGE = (
    "The value assigned here is syntactically known right at this line -- a literal"
    " dict/list/set/tuple or constant -- but the `{annotation}` annotation throws that"
    " knowledge away: every reader and every downstream check has to treat it as"
    " arbitrary data again. Narrow the annotation to what is actually known: `Final`"
    " for a value that never changes, a `TypedDict` for a literal dict shape, or the"
    " specific domain type this value represents."
)


def _check_ann_assign(context: RuleContext, node: ast.AnnAssign) -> None:
    if node.value is None:
        return
    annotation_name = _widening_annotation_name(node.annotation)
    if annotation_name is None:
        return
    if not isinstance(node.value, _LITERAL_TYPES):
        return
    context.report(node, "known_value_widening", annotation=annotation_name)


def _widening_annotation_name(annotation: ast.expr) -> str | None:
    match annotation:
        case ast.Name(id="Any" | "object" as name):
            return name
        case ast.Attribute(attr="Any", value=ast.Name(id="typing")):
            return "Any"
        case ast.Attribute(attr="object", value=ast.Name(id="builtins")):
            return "object"
        case ast.Constant(value=str() as text) if text.strip() in {"Any", "object"}:
            return text.strip()
        case _:
            return None


_METADATA = RuleMetadata(
    tier=TIER_ESCAPE_HATCH,
    confidence=CONFIDENCE_HIGH,
    fix=FIX_NONE,
    tags=("typing",),
    problem=(
        "The value is written out on the same line -- a literal dict, list, set,"
        " tuple or constant -- so its shape is known to everyone who reads the"
        " assignment. Annotating it `Any` or `object` throws that knowledge away"
        " deliberately: from the next line on, every reader and every downstream"
        " check has to treat a value they can see as arbitrary data. This is evidence"
        " being discarded at the one point where it was free."
    ),
    recipe=(
        "Narrow the annotation to what is actually known. `Final` for a value that"
        " never changes, a `TypedDict` for a literal dict shape, an `Enum` or"
        " `Literal` union for a fixed set of constants, or the specific domain type"
        " the value represents. Dropping the annotation entirely is usually better"
        " than widening it: inference already knows the literal's type."
    ),
    when_to_disable=(
        "Little reason to: the finding is local, the evidence is on the same line,"
        " and the fix never spreads beyond it. A module of configuration constants"
        " deliberately typed loosely for a dynamic consumer is the one place a"
        " per-line suppression pays, and it is worth writing down why there."
    ),
    fp_caveats=(
        "`Literal` here means literal *syntax*, not `provably constant`, so the rule"
        " is quiet on values a human would still call known: `x: Any = dict(a=1)` is"
        " a call, an f-string is a `JoinedStr`, and `x: Any = -1` is a unary"
        " operation wrapping a constant. None of the three is flagged. `Any` and"
        " `object` are matched by spelling, without import resolution."
    ),
)

RULE = Rule(
    id=RULE_ID,
    description="A literal value's annotation must not widen it to `Any` or `object`.",
    messages={"known_value_widening": _MESSAGE},
    handlers=(on(ast.AnnAssign, _check_ann_assign),),
    metadata=_METADATA,
)
