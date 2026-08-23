"""Shared "slop annotation" detection for the annotation rules.

``no_any_parameters``, ``no_any_returns``, ``no_unsafe_dict_values`` and
``no_any_type_aliases`` all need to recognize the same two escape-hatch types written
as an explicit annotation:

- ``Any`` -- a bare name, an attribute access ending in ``.Any`` (``typing.Any``,
  ``t.Any``), or a quoted string annotation ``"Any"``.
- ``object`` -- a bare name, ``builtins.object``, or a quoted string annotation
  ``"object"``.

The predicates at the top of this module are purely syntactic: they judge one
expression on its spelling alone. The alias-aware layer at the bottom
(:func:`alias_target`, :func:`is_slop_value_annotation_via_aliases`) adds the one
thing spelling cannot give -- what a *name* stands for -- by asking
``engine/scopes.py`` for the lexical binding, following alias chains inside the
module with a cycle guard.

Known limitation: an attribute access ``SomeModule.Any`` where ``SomeModule`` is not
``typing``/``typing_extensions`` (or an import alias of one of those) still matches
``is_any_annotation``, because without import resolution there is no way to
distinguish it from the real thing. This can only produce a false positive (flagging
something that is not actually the `Any` escape hatch), never a false negative on the
real `typing.Any`. The equivalent risk does not apply to ``object``: its attribute
form is only matched as the fully qualified ``builtins.object``, following the
precedent set by ``no_object_parameters.py``.

Known limitation: a quoted (string) annotation is only matched as a whole word --
``"Any"``, ``"object"``. A quoted composite such as ``"dict[str, Any]"`` is not
re-parsed, and a name inside a quoted annotation is not alias-resolved.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from anti_slop.engine.scopes import (
    AssignmentBinding,
    ImportBinding,
    ScopeTable,
    TypeAliasBinding,
)

__all__ = [
    "ANY_QUALIFIED_NAMES",
    "DICT_CONTAINER_NAMES",
    "alias_target",
    "container_value_annotation",
    "is_any_annotation",
    "is_object_annotation",
    "is_slop_annotation",
    "is_slop_value_annotation",
    "is_slop_value_annotation_via_aliases",
    "is_type_alias_marker",
    "iter_parameters",
    "resolve_alias_chain",
    "union_members",
]

# Dotted paths whose import binds the `Any` escape hatch itself.
ANY_QUALIFIED_NAMES = frozenset({"typing.Any", "typing_extensions.Any"})

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
    members = union_members(annotation)
    if members is None:
        return False
    return any(is_slop_value_annotation(member) for member in members)


def union_members(annotation: ast.expr) -> tuple[ast.expr, ...] | None:
    """The members of a union annotation, or ``None`` when it is not a union.

    Covers both spellings -- ``X | Y`` and ``Union[X, Y]`` -- plus ``Optional[X]``,
    whose implicit ``None`` member is left out: every caller here asks about the
    non-``None`` half of the union.
    """
    match annotation:
        case ast.BinOp(left=left, op=ast.BitOr(), right=right):
            return (left, right)
        case ast.Subscript(value=value, slice=slice_expr) if _is_union_name(value):
            return _subscript_elements(slice_expr)
        case _:
            return None


def is_type_alias_marker(annotation: ast.expr) -> bool:
    """True for the PEP 613 ``TypeAlias`` marker, bare or as ``typing.TypeAlias``."""
    match annotation:
        case ast.Name(id="TypeAlias"):
            return True
        case ast.Attribute(attr="TypeAlias"):
            return True
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


def alias_target(table: ScopeTable, name: ast.Name) -> ast.expr | None:
    """The expression the type-alias ``name`` stands for, within this module.

    Follows exactly one hop, in the scope the name is *written* in, so a locally
    rebound alias resolves to the local binding. Returns ``None`` when the name is
    unbound, bound more than once, imported from another module, bound to something
    that is not an alias (a class, a parameter, a loop variable), or declared as an
    annotated *variable* (``config: Mapping[str, str] = {}``) rather than an alias --
    a variable's value is not a type, whatever it holds.
    """
    match table.resolve(name.id, name):
        case TypeAliasBinding(value=value):
            return value
        case AssignmentBinding(value=value, annotation=annotation) if value is not None:
            if annotation is not None and not is_type_alias_marker(annotation):
                return None
            return value
        case _:
            return None


def resolve_alias_chain(
    table: ScopeTable, annotation: ast.expr
) -> tuple[ast.expr, ...]:
    """``annotation`` followed by every expression it lexically expands to.

    ``Metadata`` with ``Metadata = Payload`` and ``Payload = dict[str, Any]`` yields
    ``(Metadata, Payload, dict[str, Any])``. Only a bare name expands -- a name
    *inside* a larger expression is left in place, and each rule decides for itself
    whether to look into it. A name already seen ends the chain, so the mutually
    recursive ``A = B`` / ``B = A`` terminates instead of substituting forever.
    """
    chain: list[ast.expr] = [annotation]
    seen: set[str] = set()
    current: ast.expr = annotation
    while True:
        match current:
            case ast.Name(id=name) if name not in seen:
                seen.add(name)
                target = alias_target(table, current)
                if target is None:
                    return tuple(chain)
                chain.append(target)
                current = target
            case _:
                return tuple(chain)


def is_slop_value_annotation_via_aliases(
    table: ScopeTable, annotation: ast.expr
) -> bool:
    """Like :func:`is_slop_value_annotation`, but resolving names through aliases.

    ``Value`` where ``Value = Any``, and ``X | Value``, both count. A name that is
    locally rebound to something else, or that resolves to an import of anything but
    ``typing.Any``, does not.
    """
    return _is_slop_value_resolved(table, annotation, frozenset())


def _is_slop_value_resolved(
    table: ScopeTable, annotation: ast.expr, seen: frozenset[str]
) -> bool:
    match annotation:
        case ast.Name(id=name):
            if name in seen:
                return False
            match table.resolve(name, annotation):
                case None:
                    # Unbound: a builtin (`object`) or a name imported by a snippet
                    # that does not spell out its imports. Judge it by spelling.
                    return is_slop_annotation(annotation)
                case ImportBinding() as imported:
                    return imported.qualified in ANY_QUALIFIED_NAMES
                case _:
                    target = alias_target(table, annotation)
                    if target is None:
                        return False
                    return _is_slop_value_resolved(table, target, seen | {name})
        case _:
            pass
    if is_slop_annotation(annotation):
        return True
    members = union_members(annotation)
    if members is None:
        return False
    return any(_is_slop_value_resolved(table, member, seen) for member in members)


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
