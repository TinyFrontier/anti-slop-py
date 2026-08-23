"""``no-string-attribute-access`` -- reject ``getattr``/``setattr``/``delattr`` calls
(port of ``no-reflect-get``).

TypeScript's ``Reflect.get`` reaches an attribute through a string name instead of
static property access; Python's equivalent reflective trio is the builtins
``getattr``, ``setattr`` and ``delattr``. Both cases hide a property access from the
type checker and, when the name is computed rather than a literal, from the reader as
well. This rule flags every call to any of the three -- there is no legitimate use of
the reflective form itself, only a difference in *what to do instead*, which is why
detection produces one of two distinct messages depending on the name argument:

* **Literal name** (``getattr(obj, "attr")``, ``setattr(obj, "attr", value)``,
  ``delattr(obj, "attr")``): the attribute is already known at read time, so the call
  buys nothing over direct attribute syntax. The recipe is simply to write
  ``obj.attr`` / ``obj.attr = value`` / ``del obj.attr``. The one exception inside the
  literal case is ``getattr`` called with a third (default) argument: falling back to
  a default when an attribute may or may not exist is a *contract* question, not a
  syntax shortcut, so that variant gets its own recipe -- an explicit check against
  the contract actually expected (a ``Protocol`` the object should satisfy, or a plain
  attribute access once the object's type guarantees the attribute exists) instead of
  papering over the uncertainty with a default value.
* **Dynamic name** (the second argument is any expression other than a string
  literal, e.g. ``getattr(obj, field_name)``): the attribute touched depends on a
  runtime value, so nothing here is provably safe. The recipe is to parse the input
  that produced the name into a domain type at the I/O boundary and branch on that
  domain value, the same recipe the rest of anti-slop gives for every other
  reflective escape hatch.

Callee detection matches only the bare builtin names ``getattr``/``setattr``/
``delattr`` -- not an attribute access such as ``reflection.getattr(...)`` -- and the
name argument is always the second *positional* argument (none of the three builtins
accept keyword arguments in CPython). ``hasattr`` is deliberately never touched by
this rule: it is a plain existence check, not an attribute *access*, and is left as a
candidate for the opt-in ``strictness`` contrib group (``no-hasattr-narrowing``).

Without scope resolution (``scopes.py``, phase 2b) a local variable or parameter that
shadows ``getattr``/``setattr``/``delattr`` is indistinguishable from the builtin and
is still flagged; this is a known, accepted limitation until scope resolution lands.
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-string-attribute-access"

# The three reflective builtins this rule looks for, as bare names only.
_TARGET_BUILTINS = frozenset({"getattr", "setattr", "delattr"})

_MESSAGE_LITERAL_DIRECT = (
    "`{call}` looks an attribute up through `{func}` with a name already known at"
    " read time: this is just `{target}`. Write the direct attribute access instead"
    " -- the literal string buys nothing here, it only routes a fixed, compile-time"
    " name through a runtime call that hides it from the reader and from the type"
    " checker."
)

_MESSAGE_LITERAL_WITH_DEFAULT = (
    "`getattr({obj}, {name}, default)` falls back to a default when `{obj}` lacks"
    " the attribute: that is a contract question -- does `{obj}` reliably carry this"
    " attribute or not -- not an attribute-access shortcut. Write an explicit check"
    " against the contract you actually expect (a `Protocol` this object should"
    " satisfy, or a plain attribute access once the object's type guarantees the"
    " attribute exists) instead of hiding the uncertainty behind a `getattr`"
    " default."
)

_MESSAGE_DYNAMIC = (
    "`{func}({obj}, {name})` computes the attribute name at runtime: nothing proves"
    " which attribute this call actually touches, so neither the reader nor the type"
    " checker can verify it. Parse the input that produced `{name}` into a domain"
    " type at the I/O boundary and branch on that domain value instead of computing"
    " an attribute name dynamically."
)


def _check_call(context: RuleContext, node: ast.Call) -> None:
    func_name = _builtin_name(node.func)
    if func_name is None:
        return
    name_argument = _name_argument(node)
    if name_argument is None:
        # Malformed call (too few arguments) -- not this rule's concern; the type
        # checker or the runtime `TypeError` will speak to that on its own.
        return

    literal = _string_literal_value(name_argument)
    obj_source = ast.unparse(node.args[0])

    if literal is None:
        context.report(
            node,
            "dynamic",
            func=func_name,
            obj=obj_source,
            name=ast.unparse(name_argument),
        )
        return

    if func_name == "getattr" and _has_default(node):
        context.report(
            node,
            "literal_with_default",
            obj=obj_source,
            name=repr(literal),
        )
        return

    context.report(
        node,
        "literal_direct",
        call=ast.unparse(node),
        func=func_name,
        target=_direct_access_expr(func_name, obj_source, literal, node),
    )


def _builtin_name(func: ast.expr) -> str | None:
    match func:
        case ast.Name(id=name) if name in _TARGET_BUILTINS:
            return name
        case _:
            return None


def _name_argument(call: ast.Call) -> ast.expr | None:
    """The second positional argument -- the attribute name -- if present."""
    if len(call.args) < 2:
        return None
    return call.args[1]


def _has_default(call: ast.Call) -> bool:
    """True for ``getattr(obj, name, default)`` -- a third positional argument."""
    return len(call.args) >= 3


def _string_literal_value(node: ast.expr) -> str | None:
    match node:
        case ast.Constant(value=str() as text):
            return text
        case _:
            return None


def _direct_access_expr(
    func_name: str, obj_source: str, literal: str, node: ast.Call
) -> str:
    match func_name:
        case "setattr":
            value_source = ast.unparse(node.args[2]) if len(node.args) >= 3 else "value"
            return f"{obj_source}.{literal} = {value_source}"
        case "delattr":
            return f"del {obj_source}.{literal}"
        case _:
            return f"{obj_source}.{literal}"


RULE = Rule(
    id=RULE_ID,
    description=(
        "getattr()/setattr()/delattr() must not stand in for direct attribute"
        " access or unparsed dynamic access."
    ),
    messages={
        "literal_direct": _MESSAGE_LITERAL_DIRECT,
        "literal_with_default": _MESSAGE_LITERAL_WITH_DEFAULT,
        "dynamic": _MESSAGE_DYNAMIC,
    },
    handlers=(on(ast.Call, _check_call),),
)
