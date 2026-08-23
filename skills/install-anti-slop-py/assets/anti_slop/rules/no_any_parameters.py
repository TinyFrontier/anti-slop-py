"""``no-any-parameters`` -- reject parameters annotated ``Any``.

``Any`` is Python's unsafe escape hatch, the counterpart of TypeScript's ``any``: it
switches the type checker off for every use of the value, so a parameter annotated
``Any`` names no contract at all -- the original anti-slop's ``no-unknown-parameters``
bans exactly this for TypeScript's ``unknown``.

No exceptions. The TypeScript original carves out the ``cause`` convention (rethrowing
under ``Error.cause``, itself typed ``unknown``); that convention is a TypeScript-ism.
Python's own exception-chaining idiom is ``raise ... from e``, which needs no
``Any``-typed parameter anywhere, so nothing here transfers and no carve-out is
offered.

Detection is purely syntactic (see ``_annotations.py`` for the known limitations of
matching ``Any`` without import resolution).
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on
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


RULE = Rule(
    id=RULE_ID,
    description="Parameters must name a domain type, not the `Any` escape hatch.",
    messages={"parameter": _MESSAGE},
    handlers=(
        on(ast.FunctionDef, _check_function),
        on(ast.AsyncFunctionDef, _check_function),
    ),
)
