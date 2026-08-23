"""Shared "slop annotation" detection for the phase-1 annotation rules.

``no_any_parameters``, ``no_any_returns`` and ``no_unsafe_dict_values`` all need to
recognize the same two escape-hatch types written as an explicit annotation:

- ``Any`` -- a bare name, an attribute access ending in ``.Any`` (``typing.Any``,
  ``t.Any``), or a quoted string annotation ``"Any"``.
- ``object`` -- a bare name, ``builtins.object``, or a quoted string annotation
  ``"object"``.

Phase 1 is purely syntactic: there is no scope resolution yet (``scopes.py`` arrives
in phase 2, PLAN.md section 5), so these helpers cannot tell ``typing.Any`` from some
unrelated attribute that merely happens to be named ``Any`` on a foreign object.

Known limitation: an attribute access ``SomeModule.Any`` where ``SomeModule`` is not
``typing``/``typing_extensions`` (or an import alias of one of those) still matches
``is_any_annotation``, because without import resolution there is no way to
distinguish it from the real thing. This can only produce a false positive (flagging
something that is not actually the `Any` escape hatch), never a false negative on the
real `typing.Any`. The equivalent risk does not apply to ``object``: its attribute
form is only matched as the fully qualified ``builtins.object``, following the
precedent set by ``no_object_parameters.py``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

__all__ = [
    "DICT_CONTAINER_NAMES",
    "container_value_annotation",
    "is_any_annotation",
    "is_object_annotation",
    "is_slop_annotation",
    "is_slop_value_annotation",
    "iter_parameters",
]

# Dict-like container names this module recognizes, as bare identifiers. The
# attribute form (`typing.Mapping`, `collections.defaultdict`, ...) is matched by
# its trailing attribute regardless of the base expression -- phase 1 does no import
# resolution, so `SomeModule.Mapping` is indistinguishable from `typing.Mapping`
# here (same known limitation as the `Any`/`object` detectors above).
DICT_CONTAINER_NAMES = frozenset({"dict", "Dict", "Mapping", "MutableMapping", "defaultdict"})

# Subscripted generics whose members are unioned at the value position (`Union[X,
# object]`, `Optional[Any]`), as opposed to the `X | Any` spelling, which is a
# `BinOp` and needs no name to recognize.
_UNION_NAMES = frozenset({"Union", "Optional"})


def is_any_annotation(annotation: ast.expr) -> bool:
    """True when ``annotation`` is a bare, attribute, or quoted spelling of ``Any``."""
    match annotation:
        case ast.Name(id="Any"):
            return True
        case ast.Attribute(attr="Any"):
            return True
        case ast.Constant(value=str() as text):
            return text.strip() == "Any"
        case _:
            return False


def is_object_annotation(annotation: ast.expr) -> bool:
    """True when ``annotation`` is a bare, ``builtins.object``, or quoted ``object``."""
    match annotation:
        case ast.Name(id="object"):
            return True
        case ast.Attribute(attr="object", value=ast.Name(id="builtins")):
            return True
        case ast.Constant(value=str() as text):
            return text.strip() == "object"
        case _:
            return False


def is_slop_annotation(annotation: ast.expr) -> bool:
    """True when ``annotation`` is directly the ``Any`` escape hatch or the ``object`` top type."""
    return is_any_annotation(annotation) or is_object_annotation(annotation)


def is_slop_value_annotation(annotation: ast.expr) -> bool:
    """True when ``annotation`` is a slop type, or a union that includes one as a member.

    Covers the direct case (``Any``, ``object``) as well as ``X | Any``,
    ``Union[X, object]`` and ``Optional[Any]``. Recurses through nested unions of
    either spelling so a chain like ``X | Y | Any`` is still caught.
    """
    if is_slop_annotation(annotation):
        return True
    match annotation:
        case ast.BinOp(left=left, op=ast.BitOr(), right=right):
            return is_slop_value_annotation(left) or is_slop_value_annotation(right)
        case ast.Subscript(value=value, slice=slice_expr) if _is_union_name(value):
            return any(
                is_slop_value_annotation(element)
                for element in _subscript_elements(slice_expr)
            )
        case _:
            return False


def container_value_annotation(node: ast.Subscript) -> ast.expr | None:
    """The value-type element of a dict-like container subscript, if any.

    Recognizes ``dict``, ``Dict``, ``Mapping``, ``MutableMapping``, ``defaultdict`` --
    as a bare name or an attribute access (``typing.Mapping``,
    ``collections.defaultdict``) -- subscripted as ``Container[K, V]``. Returns
    ``None`` when ``node`` is not such a two-argument container subscript: a
    different container entirely, or a dict-like container subscripted with some
    other argument count (a bare, unsubscripted container names no value type to
    check in the first place, and never reaches this function).
    """
    if not _is_dict_container_name(node.value):
        return None
    match node.slice:
        case ast.Tuple(elts=[_, value_annotation]):
            return value_annotation
        case _:
            return None


def iter_parameters(arguments: ast.arguments) -> Iterator[ast.arg]:
    """Every declared parameter: positional-only, regular, ``*args``, keyword-only, ``**kwargs``."""
    yield from arguments.posonlyargs
    yield from arguments.args
    if arguments.vararg is not None:
        yield arguments.vararg
    yield from arguments.kwonlyargs
    if arguments.kwarg is not None:
        yield arguments.kwarg


def _is_dict_container_name(value: ast.expr) -> bool:
    match value:
        case ast.Name(id=name) if name in DICT_CONTAINER_NAMES:
            return True
        case ast.Attribute(attr=attr) if attr in DICT_CONTAINER_NAMES:
            return True
        case _:
            return False


def _is_union_name(value: ast.expr) -> bool:
    match value:
        case ast.Name(id=name) if name in _UNION_NAMES:
            return True
        case ast.Attribute(attr=attr) if attr in _UNION_NAMES:
            return True
        case _:
            return False


def _subscript_elements(slice_expr: ast.expr) -> tuple[ast.expr, ...]:
    match slice_expr:
        case ast.Tuple(elts=elts):
            return tuple(elts)
        case _:
            return (slice_expr,)
