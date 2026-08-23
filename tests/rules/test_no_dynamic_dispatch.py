"""Tests for the ``no-dynamic-dispatch`` rule (PLAN.md section 3.4, row 7)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_dynamic_dispatch import RULE

VALID_SNIPPETS = [
    # `itemgetter` dispatches by index/key, not by name -- never flagged.
    """
    from operator import itemgetter

    key = itemgetter(0)
    """,
    # `vars(obj)` without a subscript is plain introspection.
    """
    info = vars(obj)
    """,
    # `globals()` without a subscript.
    """
    globals().update({"x": 1})
    """,
    # `locals()` without a subscript.
    """
    context = locals()
    """,
    # A plain dict subscript, unrelated to globals/locals/vars.
    """
    handler = handlers[name]
    """,
    # `attrgetter`/`methodcaller` referenced but never called: no `ast.Call`.
    """
    from operator import attrgetter, methodcaller

    factory = attrgetter
    caller_factory = methodcaller
    """,
    # The recommended fix: an explicit mapping dispatch table.
    """
    from collections.abc import Callable, Mapping

    COMMANDS: Mapping[str, Callable[[], None]] = {"start": start, "stop": stop}
    COMMANDS[name]()
    """,
]

INVALID_SNIPPETS = [
    # `globals()[name]` without a call.
    ("""
    value = globals()[name]
    """, 1),
    # `globals()[name]()` -- the subscript is flagged once, the outer call is not
    # separately reported.
    ("""
    globals()[name]()
    """, 1),
    # `locals()[name]`.
    ("""
    value = locals()[name]
    """, 1),
    # `vars(obj)[name]`.
    ("""
    value = vars(obj)[name]
    """, 1),
    # Bare `vars()[name]` (no argument -- introspects the local scope).
    ("""
    value = vars()[name]
    """, 1),
    # `attrgetter(...)` as a bare imported name.
    ("""
    from operator import attrgetter

    getter = attrgetter("x.y")
    """, 1),
    # `operator.methodcaller(...)` as the attribute-qualified form.
    ("""
    import operator

    caller = operator.methodcaller("run")
    """, 1),
    # Combined: a namespace-subscript call plus an attrgetter factory -- two
    # independent diagnostics.
    ("""
    import operator

    globals()[name]()
    runner = operator.attrgetter("value")
    """, 2),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_namespace_message_prescribes_an_explicit_mapping() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        value = globals()[name]
        """,
    )
    assert "globals()[...]" in diagnostic.message
    assert "Mapping[Literal[...], Callable[...]]" in diagnostic.message


def test_locals_message_names_the_local_namespace() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        value = locals()[name]
        """,
    )
    assert "local namespace" in diagnostic.message


def test_vars_message_names_the_dunder_dict() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        value = vars(obj)[name]
        """,
    )
    assert "__dict__" in diagnostic.message


def test_factory_message_names_the_callee() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        from operator import methodcaller

        caller = methodcaller("run")
        """,
    )
    assert "`methodcaller(...)`" in diagnostic.message
    assert "Mapping[Literal[...], Callable[...]]" in diagnostic.message
