"""``fastapi/no-state-attribute-access`` -- reject the ``app.state`` grab bag.

``app.state`` is Starlette's untyped attribute bag: it holds whatever some startup
hook happened to put there, under whatever name, and nothing anywhere declares what
that is. Every read of it is a dynamic attribute access on an object typed as a plain
``object`` -- which is exactly why the core ``no-string-attribute-access`` rule
drowns in it on a real FastAPI service (241 hits from ``app.state.*`` alone on one
115k-line field repository). Nothing proves the attribute was ever set, nothing
proves it holds what the line assumes, a typo surfaces as an ``AttributeError`` at
request time, and a test can quietly reach into the same bag to hand a handler its
collaborator without the handler ever declaring one.

Flagged everywhere in the module -- handlers, startup hooks, middlewares, and tests
alike, because the tests are where this pattern spreads:

* ``X.state.attr`` (read, write, or ``del``) where ``X`` lexically resolves to a
  ``FastAPI(...)`` construction in this module, or is a parameter annotated
  ``FastAPI`` -- the shape an application takes when it arrives as a test fixture;
* ``request.app.state.attr`` where ``request`` is a parameter annotated ``Request``;
* ``getattr``/``setattr``/``delattr`` on either of those ``.state`` objects, which is
  the same access wearing a reflective disguise.

Not flagged: ``request.state.attr``. Per-request state set by a middleware is a
different object with a different lifetime, and the honest fix there is a dependency
that computes the value per request -- a separate conversation from the application
grab bag this rule is about. A ``.state`` chain whose base cannot be resolved (a
router imported from another module, an attribute chain, an unannotated parameter) is
also left alone: without a resolvable binding there is nothing to prove it is an
application at all.
"""

from __future__ import annotations

import ast

from anti_slop.contrib.fastapi._routes import is_fastapi_app, is_request_parameter
from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on
from anti_slop.engine.scopes import ScopeTable, scope_table_for

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "fastapi/no-state-attribute-access"

# The reflective builtins, which reach the same bag through a computed name.
_REFLECTIVE_BUILTINS = frozenset({"getattr", "setattr", "delattr"})

_RECIPE = (
    "Provide the value through a dependency instead: a small provider function that"
    " returns the typed object, injected where it is needed with"
    " `Depends(get_thing)`. The handler then declares what it needs, the type is"
    " checkable at every use, and a test replaces it with"
    " `app.dependency_overrides[get_thing] = ...` rather than by writing into a"
    " shared bag."
)

_MESSAGE_ATTRIBUTE = (
    "`{target}` reads the application state bag: `state` is an untyped attribute"
    " container, so nothing proves `{attribute}` was ever set or that it holds what"
    " this line assumes -- a rename or a missing startup hook fails at request time"
    f" with an AttributeError. {_RECIPE}"
)

_MESSAGE_REFLECTIVE = (
    "`{call}` reaches into the application state bag through a computed name: on top"
    " of `state` being an untyped attribute container, the attribute touched here is"
    " not even fixed, so neither a reader nor a type checker can say what this line"
    f" does. {_RECIPE}"
)


def _check_attribute(context: RuleContext, node: ast.Attribute) -> None:
    state_owner = _state_owner(node.value)
    if state_owner is None:
        return
    table = scope_table_for(context, node)
    if not _is_application_state(table, state_owner):
        return
    context.report(
        node, "state_attribute", target=ast.unparse(node), attribute=node.attr
    )


def _check_call(context: RuleContext, node: ast.Call) -> None:
    state_owner = _reflective_state_owner(node)
    if state_owner is None:
        return
    table = scope_table_for(context, node)
    if not _is_application_state(table, state_owner):
        return
    context.report(node, "state_reflective", call=ast.unparse(node))


def _state_owner(expr: ast.expr) -> ast.expr | None:
    """The base of a ``<base>.state`` expression, or ``None``."""
    match expr:
        case ast.Attribute(attr="state", value=base):
            return base
        case _:
            return None


def _reflective_state_owner(node: ast.Call) -> ast.expr | None:
    """The ``<base>`` of ``getattr(<base>.state, ...)`` and its two siblings."""
    match node:
        case ast.Call(func=ast.Name(id=name), args=[target, *_]) if (
            name in _REFLECTIVE_BUILTINS
        ):
            return _state_owner(target)
        case _:
            return None


def _is_application_state(table: ScopeTable, base: ast.expr) -> bool:
    """True when ``base`` is an application object -- directly or via ``request.app``."""
    if is_fastapi_app(table, base):
        return True
    match base:
        case ast.Attribute(attr="app", value=receiver):
            return is_request_parameter(table, receiver)
        case _:
            return False


RULE = Rule(
    id=RULE_ID,
    description=(
        "app.state is an untyped grab bag: provide the value with Depends instead."
    ),
    messages={
        "state_attribute": _MESSAGE_ATTRIBUTE,
        "state_reflective": _MESSAGE_REFLECTIVE,
    },
    handlers=(
        on(ast.Attribute, _check_attribute),
        on(ast.Call, _check_call),
    ),
)
