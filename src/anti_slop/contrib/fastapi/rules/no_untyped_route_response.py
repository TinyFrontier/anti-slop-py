"""``fastapi/no-untyped-route-response`` -- reject a route whose response has no type.

A route handler's return annotation is the response contract: FastAPI validates and
serializes the returned object against it and publishes it as the endpoint's OpenAPI
response schema. A handler that returns ``dict`` -- or nothing declared at all --
therefore ships an endpoint whose response shape exists only inside the function
body. Clients get no schema, the generated client has no model, and a field renamed
in the handler silently changes the wire contract with nothing to catch it.

Flagged, on a route handler (see ``_routes.py``):

* no return annotation at all;
* a return annotation of ``Any`` or of a dict-like container (``dict``,
  ``dict[str, Any]``, ``Dict``, ``Mapping``, ...), in the bare, subscripted,
  attribute-qualified or quoted spelling.

Not flagged:

* a route whose decorator carries ``response_model=<NamedType>``: that keyword IS the
  declared wire contract in idiomatic FastAPI -- on two production services it was
  the dominant spelling among handlers without a return annotation. A
  ``response_model`` of ``dict``/``Any`` still counts as no contract.
* ``-> None``: a 204, a redirect, or a pure side effect is a complete contract.
* ``-> Response`` / ``-> JSONResponse`` / ``-> StreamingResponse``, like every other
  annotation that names an actual type. Returning a ``Response`` object is a
  deliberate step *below* the serialization layer -- streaming, a custom status or
  media type, a file -- where the framework is asked not to model the body at all.
  Known limitation: this rule cannot tell such a response apart from a plain domain
  type, and does not try to; it judges only whether the contract was left open.

Known limitation: a container *of* an untyped element (``-> list[dict]``,
``-> dict[str, Any] | None``) is not flagged. The rule reads the return annotation
itself, not the types nested inside it; the core ``no-unsafe-dict-values`` rule is
what speaks about a dict-like container whose value type is ``Any``.
"""

from __future__ import annotations

import ast

from anti_slop.contrib.fastapi._routes import (
    annotated_type,
    annotation_base_name,
    route_decorators,
)
from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on
from anti_slop.engine.scopes import scope_table_for
from anti_slop.rules._annotations import DICT_CONTAINER_NAMES, is_any_annotation

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "fastapi/no-untyped-route-response"

_RECIPE = (
    "Annotate the return with a pydantic `BaseModel` naming the fields this endpoint"
    " actually sends -- FastAPI then validates the response against it, filters"
    " anything not declared, and publishes it as the endpoint's schema. Use `-> None`"
    " for a route that only has an effect, and pass `response_model=...` on the"
    " decorator only when the wire shape must differ from the returned type."
)

_MESSAGE_MISSING = (
    "Route handler `{name}` declares no return annotation, so the response contract"
    " is whatever the body happens to assemble: FastAPI serializes the returned"
    " object as-is and documents nothing, and every consumer of this endpoint has to"
    f" read the implementation to learn its shape. {_RECIPE}"
)

_MESSAGE_UNTYPED = (
    "Route handler `{name}` returns `{annotation}`: the response contract names no"
    " domain type, so the endpoint's schema is empty and a field renamed inside the"
    f" handler changes the wire format with nothing to catch it. {_RECIPE}"
)


def _check_function(
    context: RuleContext, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    table = scope_table_for(context, node)
    decorators = route_decorators(table, node)
    if not decorators:
        return
    response_model = _response_model(decorators)
    if response_model is not None:
        # `response_model=` on the decorator is the declared wire contract; it wins
        # over the return annotation because it is what FastAPI serializes against.
        if _is_untyped_response(response_model):
            context.report(
                response_model,
                "untyped",
                name=node.name,
                annotation=ast.unparse(response_model),
            )
        return
    annotation = node.returns
    if annotation is None:
        context.report(node, "missing", name=node.name)
        return
    if _is_untyped_response(annotation):
        context.report(
            annotation, "untyped", name=node.name, annotation=ast.unparse(annotation)
        )


def _response_model(decorators: tuple[ast.Call, ...]) -> ast.expr | None:
    for decorator in decorators:
        for keyword in decorator.keywords:
            if keyword.arg == "response_model":
                return keyword.value
    return None


def _is_untyped_response(annotation: ast.expr) -> bool:
    declared = annotated_type(annotation)
    if is_any_annotation(declared):
        return True
    return annotation_base_name(declared) in DICT_CONTAINER_NAMES


RULE = Rule(
    id=RULE_ID,
    description=(
        "A FastAPI route must declare its response type: a model, or None."
    ),
    messages={"missing": _MESSAGE_MISSING, "untyped": _MESSAGE_UNTYPED},
    handlers=(
        on(ast.FunctionDef, _check_function),
        on(ast.AsyncFunctionDef, _check_function),
    ),
)
