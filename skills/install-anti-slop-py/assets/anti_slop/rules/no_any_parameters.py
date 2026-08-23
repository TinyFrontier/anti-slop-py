"""``no-any-parameters`` -- reject parameters annotated ``Any``.

Port of the original anti-slop's ``no-unknown-parameters``. Every declared parameter
of a ``def``/``async def`` -- positional-only, regular, ``*args``, keyword-only,
``**kwargs`` -- is matched against ``_annotations.is_any_annotation``; a parameter
with no annotation at all is not this rule's business.

There is no carve-out for the TypeScript original's ``Error.cause`` convention:
Python chains exceptions with ``raise ... from e``, which needs no ``Any``-typed
parameter anywhere, so nothing transfers.

Detection is purely syntactic (see ``_annotations.py`` for the known limitations of
matching ``Any`` without import resolution).
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
from anti_slop.rules._annotations import is_any_annotation, iter_parameters

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-any-parameters"

_MESSAGE = (
    "Parameter `{name}` is annotated `Any`, which switches the type checker off for"
    " every use of this value: nothing about the input is proven anywhere downstream,"
    " and every call site type-checks no matter what it passes. Annotate the domain"
    " type this function owns -- a dataclass, TypedDict, Protocol, or a narrow union."
    " If `{name}` carries external input, parse it into that type at the I/O boundary"
    " and accept the parsed value here. For a decorator or another pass-through"
    " signature, replace `*args: Any, **kwargs: Any` with `ParamSpec` so the wrapped"
    " callable's own signature survives instead of being erased."
)


def _check_function(
    context: RuleContext, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    for parameter in iter_parameters(node.args):
        annotation = parameter.annotation
        if annotation is None:
            continue
        if not is_any_annotation(annotation):
            continue
        context.report(parameter, "parameter", name=parameter.arg)


_METADATA = RuleMetadata(
    tier=TIER_ESCAPE_HATCH,
    confidence=CONFIDENCE_HIGH,
    fix=FIX_NONE,
    tags=("typing",),
    problem=(
        "`Any` is Python's unsafe escape hatch, the counterpart of TypeScript's"
        " `any`: it switches the type checker off for every use of the value. A"
        " parameter annotated `Any` therefore names no contract at all -- every call"
        " site type-checks no matter what it passes, and nothing about the input is"
        " proven anywhere downstream. Whatever the caller knew about the value is"
        " discarded at the signature, and the body has to re-derive it by reading the"
        " callers."
    ),
    recipe=(
        "Annotate the domain type this function owns -- a dataclass, TypedDict,"
        " Protocol, or a narrow union. When the parameter carries external input,"
        " parse it into that type at the I/O boundary and accept the parsed value"
        " here. For a decorator or another pass-through signature, replace"
        " `*args: Any, **kwargs: Any` with `ParamSpec`, so the wrapped callable's own"
        " signature survives instead of being erased."
    ),
    when_to_disable=(
        "Rarely: an `Any` parameter is a hole in the type system rather than a style"
        " preference, and this rule is one of the ten near-universal ones. A module"
        " that genuinely implements a dynamic protocol -- a serializer that accepts"
        " arbitrary user objects, a plugin dispatch layer -- suppresses the individual"
        " signatures with `# anti-slop: ignore[no-any-parameters]` rather than turning"
        " the rule off, so the exception stays visible where it is taken."
    ),
    fp_caveats=(
        "`Any` is matched by spelling, not by import resolution: a bare `Any`,"
        " `typing.Any`, and any attribute access ending in `.Any` all count, so an"
        " unrelated `Any` from another module is flagged too. The failure mode is"
        " one-directional -- a false positive is possible, a miss on the real"
        " `typing.Any` is not."
    ),
)

RULE = Rule(
    id=RULE_ID,
    description="Parameters must name a domain type, not the `Any` escape hatch.",
    messages={"parameter": _MESSAGE},
    handlers=(
        on(ast.FunctionDef, _check_function),
        on(ast.AsyncFunctionDef, _check_function),
    ),
    metadata=_METADATA,
)
