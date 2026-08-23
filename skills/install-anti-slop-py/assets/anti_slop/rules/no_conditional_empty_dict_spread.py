"""``no-conditional-empty-dict-spread`` -- reject a ternary spread with an empty branch.

PLAN.md section 3.4, row 2 (port of ``no-conditional-empty-object-spread``). The
pattern ``{**({"timeout": t} if t is not None else {})}`` fabricates an optional
field: the field's presence or absence is decided by an expression buried inside a
spread, invisible to anyone reading the dict's shape, and invisible to the type
checker (the annotation of the merged dict cannot express "this key exists only
sometimes"). The same shape shows up as a keyword spread in a ``dict(...)`` call:
``dict(**({"timeout": t} if t is not None else {}))``.

Detection, in AST terms:

* A dict-literal spread is ``ast.Dict`` with ``keys[i] is None`` (the ``**`` marker)
  and ``values[i]`` an ``ast.IfExp`` whose ``body`` or ``orelse`` is an empty
  ``ast.Dict`` (an ``ast.Dict`` with no keys at all -- not "a dict of falsy values").
* A ``dict(...)`` call spread is an ``ast.Call`` whose callee is the builtin ``dict``
  and which has a keyword argument with ``arg is None`` (the ``**`` marker) whose
  value is the same ``ast.IfExp`` shape.

Either branch being the empty dict is enough to flag -- the rule does not require
both, since hiding the field behind *either* side of the condition has the same
effect. An unconditional ``**{}`` (no ternary at all) is a different, harmless-looking
smell and is out of scope here; so is a spread of a plain variable or a spread inside
a call to something other than ``dict`` (e.g. ``configure(**({"r": 1} if c else {}))``)
-- narrowing that further needs call-target resolution, out of reach for this
syntax-only rule (PLAN.md phase 1 vs. phase 2, ``scopes.py``).
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-conditional-empty-dict-spread"

_MESSAGE = (
    "This conditional spread fabricates an optional field: whether the key ends up"
    " present or absent is decided by a condition buried inside a spread, invisible"
    " in the dict's shape and unexpressed in its type. Build the dict with explicit"
    " statements per branch (start from a base dict, then `if condition: d[\"key\"] ="
    " value`), or make the field genuinely optional in the contract, e.g. with"
    " `NotRequired[...]` on a `TypedDict`."
)


def _check_dict(context: RuleContext, node: ast.Dict) -> None:
    for key, value in zip(node.keys, node.values, strict=True):
        if key is not None:
            continue
        if _is_conditional_empty_dict_spread(value):
            context.report(value, "conditional_spread")


def _check_call(context: RuleContext, node: ast.Call) -> None:
    if not _is_dict_callee(node.func):
        return
    for keyword in node.keywords:
        if keyword.arg is not None:
            continue
        if _is_conditional_empty_dict_spread(keyword.value):
            context.report(keyword.value, "conditional_spread")


def _is_conditional_empty_dict_spread(value: ast.expr) -> bool:
    if not isinstance(value, ast.IfExp):
        return False
    return _is_empty_dict(value.body) or _is_empty_dict(value.orelse)


def _is_empty_dict(node: ast.expr) -> bool:
    return isinstance(node, ast.Dict) and not node.keys


def _is_dict_callee(func: ast.expr) -> bool:
    match func:
        case ast.Name(id="dict"):
            return True
        case ast.Attribute(attr="dict", value=ast.Name(id="builtins")):
            return True
        case _:
            return False


RULE = Rule(
    id=RULE_ID,
    description=(
        "A dict spread's source must not be a ternary with an empty dict on either"
        " branch."
    ),
    messages={"conditional_spread": _MESSAGE},
    handlers=(
        on(ast.Dict, _check_dict),
        on(ast.Call, _check_call),
    ),
)
