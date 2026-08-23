"""``no-any-type-aliases`` -- reject type aliases that resolve to ``Any``.

Port of the original anti-slop's ``no-unknown-type-aliases``. All three Python
spellings of an alias are covered:

- ``X = Any`` -- a plain module-level assignment of a type;
- ``X: TypeAlias = Any`` -- the PEP 613 marker, anywhere;
- ``type X = Any`` -- the PEP 695 statement, anywhere.

Resolution is lexical and module-local (``engine/scopes.py``): ``A = Any`` followed by
``B = A`` flags both, and ``from typing import Any as AnyValue`` followed by
``X = AnyValue`` flags ``X``. Chains are cycle-guarded, so the mutually recursive
``A = B`` / ``B = A`` terminates and reports nothing -- neither alias is evidence of
``Any``.

A union counts when every member of it resolves to ``Any`` (``Any | None``,
``Optional[Any]``, ``Union[Any, None]``): the ``None`` half narrows nothing, so the
alias still stands for ``Any``. A union that mixes ``Any`` with a real type
(``str | Any``) is a different smell and belongs to the parameter/return rules, which
see the ``Any`` member directly.

Deliberate limits: a plain ``X = Any`` is only read as an alias at module level (a
name assigned inside a function or class body is a value binding as far as this rule
is concerned), and an alias whose right-hand side is imported from another module is
not followed -- there is no cross-module inference anywhere in this linter.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import (
    CONFIDENCE_HIGH,
    FIX_NONE,
    TIER_ESCAPE_HATCH,
    Rule,
    RuleMetadata,
    on,
)
from anti_slop.engine.scopes import ImportBinding, ScopeTable, scope_table_for
from anti_slop.rules._annotations import (
    ANY_QUALIFIED_NAMES,
    alias_target,
    is_any_annotation,
    is_type_alias_marker,
    union_members,
)

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "no-any-type-aliases"

_MESSAGE = (
    "Type alias `{alias}` resolves to `Any`: every signature that names `{alias}`"
    " reads as a domain type while type-checking against anything, so the escape"
    " hatch is invisible at exactly the call sites that would have to justify it."
    " Name the type this alias was standing in for -- a dataclass, TypedDict,"
    " Protocol, or a narrow union -- and parse external input into it at the I/O"
    " boundary. If the value really is unknown at that boundary, keep `Any` visible"
    " in the boundary signature instead of hiding it behind `{alias}`."
)


def _check_assign(context: RuleContext, node: ast.Assign) -> None:
    """``X = Any`` -- read as an alias only at module level."""
    alias = _single_name_target(node.targets)
    if alias is None:
        return
    table = scope_table_for(context, node)
    if table.scope_of(node) is not table.module_scope:
        return
    _report_if_any(context, table, alias, node.value)


def _check_ann_assign(context: RuleContext, node: ast.AnnAssign) -> None:
    """``X: TypeAlias = Any`` -- the PEP 613 marker makes the intent explicit."""
    if node.value is None or not is_type_alias_marker(node.annotation):
        return
    alias = _name_of(node.target)
    if alias is None:
        return
    _report_if_any(context, scope_table_for(context, node), alias, node.value)


def _check_type_alias(context: RuleContext, node: ast.TypeAlias) -> None:
    """``type X = Any`` -- the PEP 695 statement."""
    alias = _name_of(node.name)
    if alias is None:
        return
    _report_if_any(context, scope_table_for(context, node), alias, node.value)


def _report_if_any(
    context: RuleContext, table: ScopeTable, alias: str, value: ast.expr
) -> None:
    if _resolves_to_any(table, value, frozenset()):
        context.report(value, "alias", alias=alias)


def _resolves_to_any(
    table: ScopeTable, annotation: ast.expr, seen: frozenset[str]
) -> bool:
    """True when ``annotation`` is ``Any``, or an alias chain that ends there."""
    match annotation:
        case ast.Name(id=name):
            if name in seen:
                return False
            match table.resolve(name, annotation):
                case None:
                    # Unbound: either the `Any` of a snippet that leaves its imports
                    # implicit, or a name from elsewhere. Judge it by spelling.
                    return is_any_annotation(annotation)
                case ImportBinding() as imported:
                    return imported.qualified in ANY_QUALIFIED_NAMES
                case _:
                    target = alias_target(table, annotation)
                    if target is None:
                        return False
                    return _resolves_to_any(table, target, seen | {name})
        case _:
            pass
    if is_any_annotation(annotation):
        return True
    members = union_members(annotation)
    if members is None:
        return False
    return _union_resolves_to_any(table, members, seen)


def _union_resolves_to_any(
    table: ScopeTable, members: Sequence[ast.expr], seen: frozenset[str]
) -> bool:
    """True when every non-``None`` member of a union resolves to ``Any``."""
    found_any = False
    for member in members:
        if _is_none_annotation(member):
            continue
        if not _resolves_to_any(table, member, seen):
            return False
        found_any = True
    return found_any


def _is_none_annotation(annotation: ast.expr) -> bool:
    match annotation:
        case ast.Constant(value=None):
            return True
        case ast.Name(id="None"):
            return True
        case _:
            return False


def _single_name_target(targets: Sequence[ast.expr]) -> str | None:
    """The alias name of ``X = ...``; ``None`` for chained or unpacked targets."""
    match targets:
        case [ast.Name(id=name)]:
            return name
        case _:
            return None


def _name_of(target: ast.expr) -> str | None:
    match target:
        case ast.Name(id=name):
            return name
        case _:
            return None


_METADATA = RuleMetadata(
    tier=TIER_ESCAPE_HATCH,
    confidence=CONFIDENCE_HIGH,
    fix=FIX_NONE,
    tags=("typing",),
    problem=(
        "An alias is a promise that a domain type is coming. `Metadata = Any` breaks"
        " that promise silently: every signature that names `Metadata` reads as if it"
        " were typed, while the checker has been switched off behind the name. The"
        " escape hatch becomes invisible at exactly the call sites that would"
        " otherwise have to justify it, and a chain of aliases can carry it several"
        " modules away from where anyone would think to look."
    ),
    recipe=(
        "Name the type the alias was standing in for -- a dataclass, TypedDict,"
        " Protocol, or a narrow union -- and parse external input into it at the I/O"
        " boundary. If the value really is unknown where it arrives, keep `Any`"
        " visible in that boundary signature instead of hiding it behind an alias:"
        " unknown data is allowed, an unknown that reads like a domain type is not."
    ),
    when_to_disable=(
        "Rarely. The rule bans a name that misrepresents itself, which is a defect on"
        " any team's terms. A codebase mid-migration, where one alias is the agreed"
        " staging post for data not yet modelled, suppresses that alias by rule id"
        " and leaves the rule on for every other one."
    ),
    fp_caveats=(
        "Alias resolution is lexical and module-local: a right-hand side imported"
        " from another module is not followed, and a plain `X = Any` counts as an"
        " alias only at module level (inside a function or class body it reads as a"
        " value binding). Chains are cycle-guarded, so mutually recursive aliases"
        " report nothing rather than looping. A union counts only when every non-"
        "`None` member resolves to `Any` (`Any | None`, `Optional[Any]`); a union"
        " that mixes `Any` with a real type is left to the parameter and return"
        " rules, which see the `Any` member directly."
    ),
)

RULE = Rule(
    id=RULE_ID,
    description="Type aliases must name a domain type, not hide `Any` behind a name.",
    messages={"alias": _MESSAGE},
    handlers=(
        on(ast.Assign, _check_assign),
        on(ast.AnnAssign, _check_ann_assign),
        on(ast.TypeAlias, _check_type_alias),
    ),
    metadata=_METADATA,
)
