"""What counts as a FastAPI route handler, and how this group reads annotations.

Every rule in the ``fastapi`` group asks the same question first -- *is this function
a route handler?* -- and the answer has to be conservative, because a false positive
here turns into a false positive in four rules at once. A function is a route handler
when it carries a decorator *call* named after an HTTP method (``@x.get(...)``,
``@x.post(...)``, ..., ``@x.websocket(...)``) whose base ``x`` lexically resolves --
through ``engine/scopes.py``, within this one module -- to an assignment of
``FastAPI(...)`` or ``APIRouter(...)``, where the callee itself resolves to an import
from ``fastapi``. Import aliases are followed on both halves, so
``from fastapi import FastAPI as App`` / ``app = App()`` and ``import fastapi`` /
``router = fastapi.APIRouter()`` are both recognized.

Anything that cannot be resolved that way is *not* a handler. The consequences, all
deliberate and all in the direction of silence:

* A router imported from another module (``from .api import router`` then
  ``@router.get(...)``) resolves to an import, not to a construction call, so its
  handlers are invisible to this group -- there is no cross-module inference anywhere
  in this linter.
* A decorator base that is an attribute chain (``@routers.v1.get(...)``,
  ``@self.router.get(...)``) names no local binding to resolve, so it is not a
  handler either.
* A ``.get``/``.post`` decorator from a *different* framework (a Flask app, an
  arbitrary object with a ``get`` method) resolves to something other than
  ``FastAPI``/``APIRouter`` and is therefore left alone.
* ``@app.api_route(...)``, ``app.add_api_route(...)`` and the rarer verbs
  (``head``, ``options``, ``trace``) are not in the recognized set.

The annotation helpers below share the same discipline as the core rules' own
``_annotations.py``: a name is matched by its trailing identifier, in the bare, the
attribute-qualified and the quoted spelling, because there is no import resolution
that could tell ``mypkg.Request`` from ``fastapi.Request``. That can only make a rule
speak up about something merely *named* like a FastAPI type -- never stay silent
about the real one.
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.scopes import AssignmentBinding, ParameterBinding, ScopeTable

__all__ = [
    "APP_QUALIFIED_NAMES",
    "DEPENDENCY_MARKERS",
    "ROUTE_METHODS",
    "ROUTER_QUALIFIED_NAMES",
    "annotated_metadata",
    "annotated_type",
    "annotation_base_name",
    "declares_dependency_marker",
    "enclosing_route_handler",
    "is_fastapi_app",
    "is_request_annotation",
    "is_request_parameter",
    "is_route_handler",
    "route_decorators",
]

# The decorator attribute names that declare a route.
ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "websocket"})

# Dotted paths that construct an application, and the wider set that can carry route
# decorators (an application or a router).
APP_QUALIFIED_NAMES = frozenset({"fastapi.FastAPI", "fastapi.applications.FastAPI"})
ROUTER_QUALIFIED_NAMES = APP_QUALIFIED_NAMES | {
    "fastapi.APIRouter",
    "fastapi.routing.APIRouter",
}

# Parameter markers that say "this parameter is not the request body": a dependency,
# or a value read from the query string, headers, path or cookies. `Body`, `Form` and
# `File` are absent on purpose -- they mark a parameter that *is* the body.
DEPENDENCY_MARKERS = frozenset(
    {"Depends", "Security", "Query", "Header", "Path", "Cookie"}
)

def is_route_handler(
    table: ScopeTable, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> bool:
    """True when ``function`` carries a FastAPI route decorator (see module docstring)."""
    return bool(route_decorators(table, function))


def route_decorators(
    table: ScopeTable, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> tuple[ast.Call, ...]:
    """Every FastAPI route decorator call on ``function``, in source order."""
    matched: list[ast.Call] = []
    for decorator in function.decorator_list:
        if not _is_route_decorator(table, decorator):
            continue
        match decorator:
            case ast.Call() as call:
                matched.append(call)
            case _:
                continue
    return tuple(matched)


def enclosing_route_handler(
    context: RuleContext, table: ScopeTable, node: ast.AST
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The route handler ``node`` is written in, or ``None``.

    The *nearest* enclosing ``def`` decides, as in ``no-adhoc-isinstance``: a helper
    defined inside a handler is its own contract, and code in it is not handler code.
    """
    for ancestor in context.ancestors(node):
        match ancestor:
            case ast.FunctionDef() | ast.AsyncFunctionDef() as function:
                return function if is_route_handler(table, function) else None
            case _:
                continue
    return None


def is_fastapi_app(table: ScopeTable, expr: ast.expr) -> bool:
    """True when ``expr`` is a name that stands for a ``FastAPI`` application.

    Two spellings resolve: the module-level construction (``app = FastAPI()``) and a
    parameter annotated ``FastAPI`` -- the shape an application takes in a test, where
    it arrives as a fixture argument rather than as a module global.
    """
    match expr:
        case ast.Name(id=name):
            return _binds_application(table, name, expr)
        case _:
            return False


def is_request_parameter(
    table: ScopeTable,
    expr: ast.expr,
    function: ast.FunctionDef | ast.AsyncFunctionDef | None = None,
) -> bool:
    """True when ``expr`` is a name bound to a ``Request``-annotated parameter.

    When ``function`` is given, the parameter must belong to that function -- which is
    how a rule asks "is this the handler's own request object" rather than "is this
    some request-shaped name from an enclosing scope".
    """
    match expr:
        case ast.Name(id=name):
            return _binds_request(table, name, expr, function)
        case _:
            return False


def is_request_annotation(annotation: ast.expr) -> bool:
    """True for ``Request``, ``fastapi.Request``, ``starlette.requests.Request``, quoted."""
    return annotation_base_name(annotated_type(annotation)) == "Request"


def annotated_type(annotation: ast.expr) -> ast.expr:
    """The type half of ``Annotated[T, ...]``; ``annotation`` itself otherwise."""
    match annotation:
        case ast.Subscript(value=value, slice=ast.Tuple(elts=[first, *_])) if (
            annotation_base_name(value) == "Annotated"
        ):
            return first
        case _:
            return annotation


def annotated_metadata(annotation: ast.expr) -> tuple[ast.expr, ...]:
    """The metadata arguments of ``Annotated[T, m1, m2]``; empty for anything else."""
    match annotation:
        case ast.Subscript(value=value, slice=ast.Tuple(elts=[_, *metadata])) if (
            annotation_base_name(value) == "Annotated"
        ):
            return tuple(metadata)
        case _:
            return ()


def annotation_base_name(annotation: ast.expr) -> str | None:
    """The trailing identifier of an annotation, ``None`` when it has none.

    ``dict`` -> ``dict``; ``dict[str, int]`` -> ``dict``; ``fastapi.Request`` ->
    ``Request``; ``"starlette.requests.Request"`` -> ``Request``. A quoted annotation
    is read as text rather than re-parsed, the same limitation the core annotation
    helpers carry.
    """
    match annotation:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=attr):
            return attr
        case ast.Subscript(value=value):
            return annotation_base_name(value)
        case ast.Constant(value=str() as text):
            head = text.strip().split("[", 1)[0].strip()
            return head.rsplit(".", 1)[-1] or None
        case _:
            return None


def declares_dependency_marker(parameter: ast.arg, default: ast.expr | None) -> bool:
    """True when the parameter is marked as something other than the request body.

    Both spellings count: the default-value form (``db: Session = Depends(get_db)``)
    and the modern ``Annotated[Session, Depends(get_db)]`` form. Markers are matched
    by their trailing name, so ``fastapi.Depends(...)`` counts too.
    """
    if _is_marker_call(default):
        return True
    annotation = parameter.annotation
    if annotation is None:
        return False
    return any(
        _is_marker_call(metadata) for metadata in annotated_metadata(annotation)
    )


def _is_marker_call(expr: ast.expr | None) -> bool:
    match expr:
        case ast.Call(func=func):
            return annotation_base_name(func) in DEPENDENCY_MARKERS
        case _:
            return False


def _is_route_decorator(table: ScopeTable, decorator: ast.expr) -> bool:
    match decorator:
        case ast.Call(func=ast.Attribute(attr=attr, value=value)) if (
            attr in ROUTE_METHODS
        ):
            return _binds_router(table, value)
        case _:
            return False


def _binds_router(table: ScopeTable, expr: ast.expr) -> bool:
    """True when ``expr`` is a name assigned a ``FastAPI``/``APIRouter`` construction."""
    match expr:
        case ast.Name(id=name):
            pass
        case _:
            return False
    match table.resolve(name, expr):
        case AssignmentBinding(value=ast.Call(func=func)):
            return table.qualified_name(func) in ROUTER_QUALIFIED_NAMES
        case _:
            return False


def _binds_request(
    table: ScopeTable,
    name: str,
    expr: ast.expr,
    function: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> bool:
    match table.resolve(name, expr):
        case ParameterBinding(node=parameter, function=owner):
            if function is not None and owner is not function:
                return False
            annotation = parameter.annotation
            return annotation is not None and is_request_annotation(annotation)
        case _:
            return False


def _binds_application(table: ScopeTable, name: str, expr: ast.expr) -> bool:
    match table.resolve(name, expr):
        case AssignmentBinding(value=ast.Call(func=func)):
            return table.qualified_name(func) in APP_QUALIFIED_NAMES
        case ParameterBinding(node=parameter):
            annotation = parameter.annotation
            if annotation is None:
                return False
            return annotation_base_name(annotated_type(annotation)) == "FastAPI"
        case _:
            return False
