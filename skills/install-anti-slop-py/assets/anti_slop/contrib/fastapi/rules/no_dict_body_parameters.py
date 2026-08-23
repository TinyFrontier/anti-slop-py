"""``fastapi/no-dict-body-parameters`` -- reject a route body typed as a mapping.

FastAPI's whole value proposition is that the request body is decoded and *validated*
before the handler runs: the annotation on the parameter is the schema, the 422
response, and the OpenAPI contract, all at once. Annotating that parameter ``dict``,
``Mapping[str, Any]`` or ``Any`` opts out of every one of those without saying so --
the payload arrives unchecked, the handler re-derives the fields by hand
(``payload["email"]``), a missing key becomes a 500 instead of a 422, and the
generated schema documents the endpoint as accepting anything at all. This is the
framework-specific form of ``no-any-parameters``/``no-unsafe-dict-values``, reported
here with the recipe that actually applies: a pydantic model.

Flagged: a parameter of a route handler (see ``_routes.py`` for what counts as one)
whose annotation is ``Any`` or a dict-like container -- ``dict``, ``Dict``,
``Mapping``, ``MutableMapping``, ``defaultdict`` -- bare, subscripted, attribute
qualified (``typing.Mapping[str, Any]``), or quoted (``"dict[str, Any]"``). The
``Annotated[dict[str, Any], ...]`` spelling is unwrapped first.

Not flagged:

* A parameter marked as something other than the body -- ``Depends``, ``Security``,
  ``Query``, ``Header``, ``Path``, ``Cookie`` -- in either spelling, as a default
  value (``q: dict = Query(...)``) or as ``Annotated`` metadata. Those values do not
  come from the body, so a mapping there is a different question from this one.
  ``Body``/``Form``/``File`` are deliberately *not* in that exemption list: a
  parameter marked with them is the body.
* ``*args``/``**kwargs``: neither is a body parameter, whatever it is annotated with.
* A parameter with no annotation at all -- FastAPI treats it as a query parameter,
  and ``no-any-parameters`` already speaks about missing contracts in general.

Known limitations: a quoted annotation is read as text, not re-parsed, so only its
head name is matched; and a type alias that expands to ``dict[str, Any]`` in another
module is invisible here, as everywhere else in this linter.
"""

from __future__ import annotations

import ast

from anti_slop.contrib.fastapi._routes import (
    annotated_type,
    annotation_base_name,
    declares_dependency_marker,
    is_route_handler,
)
from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on
from anti_slop.engine.scopes import scope_table_for
from anti_slop.rules._annotations import DICT_CONTAINER_NAMES, is_any_annotation

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "fastapi/no-dict-body-parameters"

_MESSAGE = (
    "Route parameter `{parameter}: {annotation}` takes the request body as an"
    " unvalidated mapping: FastAPI hands the decoded JSON straight through, so no"
    " field is proven present or proven to be of any particular type, a malformed"
    " payload fails deep inside the handler as a 500 instead of at the boundary as a"
    " 422, and the generated OpenAPI schema tells clients the endpoint accepts"
    " anything. Declare a pydantic `BaseModel` with the fields this handler actually"
    " reads and annotate the parameter with it -- validation, the error response and"
    " the schema all follow from that one declaration. If this parameter is not the"
    " body, say so in the signature instead: `Depends(...)`, `Query(...)`,"
    " `Header(...)`, `Path(...)` or `Cookie(...)`."
)


def _check_function(
    context: RuleContext, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> None:
    table = scope_table_for(context, node)
    if not is_route_handler(table, node):
        return
    for parameter, default in _body_parameters(node.args):
        annotation = parameter.annotation
        if annotation is None or not _is_unvalidated_body(annotation):
            continue
        if declares_dependency_marker(parameter, default):
            continue
        context.report(
            parameter,
            "dict_body",
            parameter=parameter.arg,
            annotation=ast.unparse(annotation),
        )


def _body_parameters(
    arguments: ast.arguments,
) -> list[tuple[ast.arg, ast.expr | None]]:
    """Every parameter that can carry a body, paired with its default expression.

    ``*args``/``**kwargs`` are left out: FastAPI binds neither to the request body.
    Defaults are matched to parameters from the right, the way Python assigns them.
    """
    positional = [*arguments.posonlyargs, *arguments.args]
    padding = len(positional) - len(arguments.defaults)
    defaults: list[ast.expr | None] = [None] * padding + list(arguments.defaults)
    paired = list(zip(positional, defaults, strict=True))
    paired.extend(zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True))
    return paired


def _is_unvalidated_body(annotation: ast.expr) -> bool:
    """True for ``Any`` and for every dict-like container spelling."""
    declared = annotated_type(annotation)
    if is_any_annotation(declared):
        return True
    return annotation_base_name(declared) in DICT_CONTAINER_NAMES


RULE = Rule(
    id=RULE_ID,
    description=(
        "A FastAPI route body must be a pydantic model, not dict/Mapping/Any."
    ),
    messages={"dict_body": _MESSAGE},
    handlers=(
        on(ast.FunctionDef, _check_function),
        on(ast.AsyncFunctionDef, _check_function),
    ),
)
