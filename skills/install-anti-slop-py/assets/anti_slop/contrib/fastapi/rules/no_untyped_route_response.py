"""``fastapi/no-untyped-route-response`` -- reject a route whose response has no type.

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

The rule reads the return annotation itself, not the types nested inside it.
"""

from __future__ import annotations

import ast

from anti_slop.contrib.fastapi._routes import (
    annotated_type,
    annotation_base_name,
    route_decorators,
)
from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import (
    CONFIDENCE_POLICY,
    FIX_NONE,
    TIER_FRAMEWORK,
    Rule,
    RuleMetadata,
    on,
)
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


_METADATA = RuleMetadata(
    tier=TIER_FRAMEWORK,
    confidence=CONFIDENCE_POLICY,
    fix=FIX_NONE,
    tags=("fastapi", "typing"),
    problem=(
        "A route handler's return annotation is the response contract: FastAPI"
        " validates and serializes the returned object against it and publishes it as"
        " the endpoint's OpenAPI response schema. A handler that returns `dict` -- or"
        " declares nothing at all -- ships an endpoint whose response shape exists"
        " only inside the function body. Clients get no schema, the generated client"
        " has no model, and a field renamed inside the handler silently changes the"
        " wire contract with nothing to catch it."
    ),
    recipe=(
        "Annotate the return with a pydantic `BaseModel` naming the fields this"
        " endpoint actually sends: FastAPI then validates the response against it,"
        " filters anything not declared, and publishes it as the endpoint's schema."
        " Use `-> None` for a route that only has an effect, and pass"
        " `response_model=...` on the decorator only when the wire shape must differ"
        " from the returned type."
    ),
    when_to_disable=(
        "The `fastapi` group is opt-in and off unless `groups = [\"fastapi\"]` names"
        " it, so no preset ever turns this rule on. Inside a project that enables the"
        " group this is the least contested of the four -- the annotation is the"
        " endpoint's public contract. An existing service adopting the group stages"
        " it at `warn` until its handlers have models, since every handler is a"
        " separate small change."
    ),
    fp_caveats=(
        "Route handlers are recognized lexically, within one module: a router"
        " imported from elsewhere, an attribute chain (`@routers.v1.get(...)`) or"
        " `@app.api_route(...)` is not seen as a route at all. A"
        " `response_model=<NamedType>` keyword on the decorator counts as the"
        " contract and wins over the return annotation. A `Response` subclass cannot"
        " be told apart from a domain type and is accepted as one -- the rule judges"
        " only whether the contract was left open. A container *of* an untyped"
        " element (`-> list[dict]`) is not flagged here."
    ),
)

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
    metadata=_METADATA,
)
