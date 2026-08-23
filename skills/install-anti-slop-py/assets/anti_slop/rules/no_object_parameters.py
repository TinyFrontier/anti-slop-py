"""``no-object-parameters`` -- reject parameters annotated ``object``.

PLAN.md section 3.4, row 5. ``object`` is Python's safe top type, the counterpart of
TypeScript's ``unknown``, and the original anti-slop bans it for the same reason: a
parameter that accepts everything names no contract, so neither the caller nor the
body can prove anything about the value. This is deliberately stricter than the
official minimum (typing.python.org recommends ``object`` over ``Any``); teams that
want to stop at that minimum set ``allow-object = true``.

Built-in exception: the comparison dunders ``__eq__``, ``__ne__`` and
``__contains__``, where typeshed's own convention requires ``other: object``.

Field-tuned in phase 5: ``*args: object`` / ``**kwargs: object`` is the
typing-docs-blessed spelling for "accepts anything and never touches it" (stub
callbacks, pass-through wrappers), so variadic parameters are exempt by default;
``allow-variadic-object = false`` restores the strict reading.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import BoolOption, Rule, on

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-object-parameters"

# Dunders whose signature is fixed by the typeshed convention `other: object`.
_EXEMPT_DUNDERS = frozenset({"__eq__", "__ne__", "__contains__"})

_MESSAGE = (
    "Parameter `{name}` is annotated `object`, the top type: every call site type-checks"
    " and the body can prove nothing about the value. Annotate the domain type this"
    " function owns -- a dataclass, TypedDict, Protocol, or a narrow union. If `{name}`"
    " carries external input, parse it into that type at the I/O boundary and accept the"
    " parsed value here."
)

_ALLOW_OBJECT = BoolOption(
    name="allow-object",
    default=False,
    description=(
        "Allow parameters annotated `object`, for teams that stop at the official"
        " typing minimum of `object` over `Any`."
    ),
)

_ALLOW_VARIADIC_OBJECT = BoolOption(
    name="allow-variadic-object",
    default=True,
    description=(
        "Allow `*args: object` / `**kwargs: object` -- the typing-docs-blessed"
        " spelling for accepting arbitrary arguments without touching them."
    ),
)


def _check_function(
    context: RuleContext, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    if context.bool_option(_ALLOW_OBJECT.name):
        return
    if node.name in _EXEMPT_DUNDERS and _is_method(context, node):
        return
    allow_variadic = context.bool_option(_ALLOW_VARIADIC_OBJECT.name)
    for parameter, variadic in _iter_parameters(node.args):
        if variadic and allow_variadic:
            continue
        annotation = parameter.annotation
        if annotation is None:
            continue
        if not _is_object_annotation(annotation):
            continue
        context.report(parameter, "parameter", name=parameter.arg)


def _iter_parameters(arguments: ast.arguments) -> Iterator[tuple[ast.arg, bool]]:
    """Every declared parameter, with a flag marking ``*args`` / ``**kwargs``."""
    for parameter in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
        yield parameter, False
    if arguments.vararg is not None:
        yield arguments.vararg, True
    if arguments.kwarg is not None:
        yield arguments.kwarg, True


def _is_object_annotation(annotation: ast.expr) -> bool:
    match annotation:
        case ast.Name(id="object"):
            return True
        case ast.Attribute(attr="object", value=ast.Name(id="builtins")):
            return True
        case ast.Constant(value=str() as text):
            # A quoted annotation, e.g. `def save(value: "object") -> None`.
            return text.strip() == "object"
        case _:
            return False


def _is_method(context: RuleContext, node: ast.stmt) -> bool:
    """True when ``node`` is defined directly in a class body, not at module level."""
    match context.parent_of(node):
        case ast.ClassDef():
            return True
        case _:
            return False


RULE = Rule(
    id=RULE_ID,
    description="Parameters must name a domain type, not the `object` top type.",
    messages={"parameter": _MESSAGE},
    handlers=(
        on(ast.FunctionDef, _check_function),
        on(ast.AsyncFunctionDef, _check_function),
    ),
    options=(_ALLOW_OBJECT, _ALLOW_VARIADIC_OBJECT),
)
