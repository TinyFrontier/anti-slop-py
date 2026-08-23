"""``fastapi/no-raw-request-parsing`` -- reject reading the body off ``Request``.

``await request.json()`` inside a route handler steps around the boundary the
framework exists to provide. The body arrives as whatever ``json.loads`` produced,
nothing is validated, a malformed payload raises inside the handler (a 500 where the
framework would have answered 422), the endpoint's OpenAPI schema documents no
request body at all, and every reader has to go through the function body to find out
what the endpoint accepts. It is the FastAPI-shaped version of parsing at the wrong
layer, and the fix is always the same: put the body in the signature.

Flagged: ``request.json()``, ``request.form()`` and ``request.body()`` -- with or
without ``await`` -- called inside a route handler on a name that lexically resolves
to a parameter of *that* handler annotated ``Request`` (bare, ``fastapi.Request``,
``starlette.requests.Request``, quoted, or wrapped in ``Annotated[...]``).

Not flagged, all for the same reason -- nothing proves the receiver is the handler's
own request object, or that this code is handler code:

* the same calls in a function that is not a route handler (a middleware, a helper,
  a test), including a nested function defined inside a handler: the *nearest*
  enclosing ``def`` decides;
* a receiver that is not a ``Request``-annotated parameter (an unannotated one, a
  module-level object, an attribute chain);
* ``request.stream()``, ``request.headers``, ``request.query_params`` and the rest of
  the request API: this rule is about parsing the *body*, not about touching the
  request object at all -- a handler that genuinely needs the raw stream is doing
  something the signature cannot express.
"""

from __future__ import annotations

import ast

from anti_slop.contrib.fastapi._routes import (
    enclosing_route_handler,
    is_request_parameter,
)
from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on
from anti_slop.engine.scopes import scope_table_for

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "fastapi/no-raw-request-parsing"

# Methods of `starlette.requests.Request` that decode the request body.
_BODY_READERS = frozenset({"json", "form", "body"})

_MESSAGE = (
    "`{call}` decodes the request body by hand, behind the validating boundary the"
    " route already has: nothing checks the payload, a malformed body fails deep in"
    " the handler as a 500 instead of at the edge as a 422, and the endpoint's schema"
    " documents no request body at all. Declare the body in the signature instead --"
    " a pydantic `BaseModel` parameter for JSON, `Form(...)` fields or `UploadFile`"
    " for form data -- and let FastAPI decode and validate it before `{handler}`"
    " runs. Keep the `Request` parameter only for what genuinely is not the body."
)


def _check_call(context: RuleContext, node: ast.Call) -> None:
    receiver = _body_reader_receiver(node.func)
    if receiver is None:
        return
    table = scope_table_for(context, node)
    handler = enclosing_route_handler(context, table, node)
    if handler is None:
        return
    if not is_request_parameter(table, receiver, handler):
        return
    context.report(node, "raw_body", call=ast.unparse(node), handler=handler.name)


def _body_reader_receiver(func: ast.expr) -> ast.expr | None:
    """The object a body-reading method is called on, if that is what ``func`` is."""
    match func:
        case ast.Attribute(attr=attr, value=receiver) if attr in _BODY_READERS:
            return receiver
        case _:
            return None


RULE = Rule(
    id=RULE_ID,
    description=(
        "A FastAPI route must declare its body in the signature, not read it off"
        " Request."
    ),
    messages={"raw_body": _MESSAGE},
    handlers=(on(ast.Call, _check_call),),
)
