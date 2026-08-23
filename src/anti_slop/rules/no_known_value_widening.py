"""``no-known-value-widening`` -- reject a literal value under an `Any`/`object` annotation.

Port of ``no-known-value-widening``. This is the phase-1
base case only: it flags an ``ast.AnnAssign`` whose annotation is ``Any`` or
``object`` and whose value is a syntactic literal (``ast.Dict``, ``ast.List``,
``ast.Set``, ``ast.Tuple`` or ``ast.Constant``). The value's shape is known right at
the assignment; annotating it ``Any``/``object`` throws that knowledge away for every
reader and every downstream check.

The original TypeScript rule also special-cases object literals under a *narrower*
mapped/index-signature type, on the theory that TypeScript erases literal keys past
that boundary. That half of the rule does **not** port: Python's type checkers do not
infer literal dict keys in the first place, so ``handlers: dict[str, Handler] = {...}``
is already idiomatic and stays unflagged -- there is no narrower-but-still-lossy type
to compare against, only ``Any``/``object``.

Two deliberate exclusions, both because "syntactic literal" is a purely
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
from anti_slop.engine.rule import Rule, on

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


RULE = Rule(
    id=RULE_ID,
    description="A literal value's annotation must not widen it to `Any` or `object`.",
    messages={"known_value_widening": _MESSAGE},
    handlers=(on(ast.AnnAssign, _check_ann_assign),),
)
