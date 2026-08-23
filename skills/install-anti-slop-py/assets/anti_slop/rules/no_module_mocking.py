"""``no-module-mocking`` -- reject patching another module's attributes in tests.

Module mocking makes the import graph the seam. The test then proves that the patched
name still exists at that path and is still spelled that way -- not that the code
under test works -- and the day someone renames, re-exports or inlines the target, the
patch quietly stops patching anything while the test keeps passing. The original
anti-slop bans ``vi.mock``/``jest.mock`` for exactly this; the Python equivalents are
``unittest.mock.patch``, the ``mocker`` fixture of pytest-mock, and pytest's
``monkeypatch.setattr``.

Recognized, in every form the same call takes -- decorator, context manager, and plain
call, which are all ``ast.Call`` nodes:

- ``patch(...)``, ``patch.object(...)``, ``patch.multiple(...)`` whenever ``patch``
  lexically resolves to an import from ``unittest.mock`` or from the ``mock``
  backport. That covers the aliased import (``from unittest.mock import patch as p``),
  the module form (``from unittest import mock`` then ``mock.patch(...)``) and the
  fully qualified ``unittest.mock.patch(...)``.
- ``mocker.patch(...)`` / ``mocker.patch.object(...)`` where ``mocker`` is a *parameter*
  of the enclosing function -- that is what a pytest-mock fixture is.
- ``monkeypatch.setattr(...)`` where ``monkeypatch`` is a parameter, in both its
  spellings: the string-path form ``setattr("pkg.mod.fn", fake)`` and the object form
  ``setattr(service, "run", fake)``. Both replace an attribute someone else owns.

Names are resolved lexically (``engine/scopes.py``), so a locally rebound name is not
this rule's business: ``patch = my_helper`` inside a test function shadows the import,
and ``patch("...")`` there reports nothing. By the same token ``patch.dict(...)`` and
``monkeypatch.setenv`` / ``delenv`` / ``chdir`` / ``syspath_prepend`` are left alone --
they configure process state (environment, cwd, sys.path) rather than replacing a
collaborator, and there is no dependency to inject in their place.

Known limitations: a ``patch`` re-exported through ``conftest.py`` or any other module
is not followed, since nothing in this linter does cross-module inference; and the two
pytest fixtures are recognized by their conventional parameter names, so a test that
renames them in its signature is not seen.
"""

from __future__ import annotations

import ast

from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on
from anti_slop.engine.scopes import (
    ParameterBinding,
    ScopeTable,
    dotted_parts,
    scope_table_for,
)

__all__ = ["MESSAGE", "MESSAGE_PROBLEM", "MESSAGE_RECIPE", "RULE", "RULE_ID"]

RULE_ID = "no-module-mocking"

# The diagnostic is assembled from two named halves on purpose: the problem statement
# is framework-independent, while the recipe is the part a contrib group specializes.
# The fastapi group composes its own message as
# `f"{MESSAGE_PROBLEM} <dependency_overrides recipe>"` rather than restating the
# problem, so both messages stay in sync from this one place.
MESSAGE_PROBLEM = (
    "`{target}` reaches into another module and swaps an attribute out at runtime."
    " The test then rests on the import graph instead of on the design: it proves the"
    " patched name still exists at that path and is still spelled that way, and after"
    " a rename, a re-export or an inlining the patch silently replaces nothing while"
    " the test keeps passing."
)
MESSAGE_RECIPE = (
    "Take the collaborator as an argument instead -- a parameter typed by a Protocol,"
    " an ABC, or a narrow callable type -- and pass a real test implementation from"
    " the test: an in-memory fake, a stub adapter, or the production object wired to a"
    " test double at the process edge. If the dependency is buried too deep to pass in"
    " today, lift it to the constructor or the call signature first; that refactoring"
    " is what the mock was standing in for."
)
MESSAGE = f"{MESSAGE_PROBLEM} {MESSAGE_RECIPE}"

# Dotted paths of `unittest.mock.patch` and its attribute forms, plus the same set for
# the `mock` backport distribution. `patch.dict` is deliberately absent: it edits a
# dictionary's contents (typically `os.environ`), it does not replace a collaborator.
PATCH_QUALIFIED_NAMES = frozenset(
    f"{root}{suffix}"
    for root in ("unittest.mock.patch", "mock.patch")
    for suffix in ("", ".object", ".multiple")
)

# Fixture name -> the attribute paths on it that replace a collaborator. The name only
# counts when it resolves to a function parameter, which is what a fixture is.
_FIXTURE_CALLS: dict[str, frozenset[tuple[str, ...]]] = {
    "mocker": frozenset({("patch",), ("patch", "object"), ("patch", "multiple")}),
    "monkeypatch": frozenset({("setattr",)}),
}


def _check_call(context: RuleContext, node: ast.Call) -> None:
    parts = dotted_parts(node.func)
    if parts is None:
        return
    table = scope_table_for(context, node)
    patched = _is_imported_patch(table, node.func) or _is_fixture_patch(
        table, node.func, parts
    )
    if not patched:
        return
    context.report(node, "module-mock", target=ast.unparse(node.func))


def _is_imported_patch(table: ScopeTable, func: ast.expr) -> bool:
    """True when ``func`` resolves to ``unittest.mock.patch`` or the backport."""
    return table.qualified_name(func) in PATCH_QUALIFIED_NAMES


def _is_fixture_patch(
    table: ScopeTable, func: ast.expr, parts: tuple[str, ...]
) -> bool:
    """True for a patching method on a ``mocker`` / ``monkeypatch`` fixture."""
    root, *attributes = parts
    allowed = _FIXTURE_CALLS.get(root)
    if allowed is None or tuple(attributes) not in allowed:
        return False
    match table.resolve(root, func):
        case ParameterBinding():
            return True
        case _:
            return False


RULE = Rule(
    id=RULE_ID,
    description="Inject dependencies through real seams instead of patching modules.",
    messages={"module-mock": MESSAGE},
    handlers=(on(ast.Call, _check_call),),
)
