"""``fastapi/no-state-attribute-access`` -- reject the ``app.state`` grab bag.

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
from anti_slop.engine.rule import (
    CONFIDENCE_POLICY,
    FIX_NONE,
    TIER_FRAMEWORK,
    Rule,
    RuleMetadata,
    on,
)
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


_METADATA = RuleMetadata(
    tier=TIER_FRAMEWORK,
    confidence=CONFIDENCE_POLICY,
    fix=FIX_NONE,
    tags=("fastapi", "reflection"),
    problem=(
        "`app.state` is Starlette's untyped attribute bag: it holds whatever some"
        " startup hook happened to put there, under whatever name, and nothing"
        " anywhere declares what that is. Every read is a dynamic attribute access on"
        " an object typed as a plain `object` -- nothing proves the attribute was"
        " ever set or that it holds what the line assumes, and a typo or a missing"
        " startup hook surfaces as an `AttributeError` at request time. Tests make it"
        " worse by reaching into the same bag to hand a handler a collaborator the"
        " handler never declared."
    ),
    recipe=(
        "Provide the value through a dependency: a small provider function that"
        " returns the typed object, injected where it is needed with"
        " `Depends(get_thing)`. The handler then declares what it needs, the type is"
        " checkable at every use, and a test replaces it with"
        " `app.dependency_overrides[get_thing] = ...` rather than by writing into a"
        " shared bag."
    ),
    when_to_disable=(
        "The `fastapi` group is opt-in and off unless `groups = [\"fastapi\"]` names"
        " it, so no preset ever turns this rule on. Its reason to exist is volume:"
        " one 115k-line field service produced 241 core `no-string-attribute-access`"
        " hits from `app.state` alone, and this rule answers them with a specific"
        " recipe instead of a generic one. A service whose startup wiring genuinely"
        " lives in `app.state` and is not being migrated should stage it at `warn`"
        " rather than suppress every line."
    ),
    fp_caveats=(
        "The base must resolve lexically, in the same module, to a `FastAPI(...)`"
        " construction or to a parameter annotated `FastAPI` (or reach the"
        " application through a `Request`-annotated parameter's `.app`). An"
        " application imported from another module, an attribute chain, or an"
        " unannotated fixture parameter is left alone -- nothing there proves it is"
        " an application. `request.state.attr` is deliberately never flagged:"
        " per-request state is a different object with a different lifetime."
    ),
)

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
    metadata=_METADATA,
)
