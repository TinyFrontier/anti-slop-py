"""Tests for the ``no-adhoc-isinstance`` rule (PLAN.md section 3.4, row 8)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_adhoc_isinstance import RULE

VALID_SNIPPETS = [
    # `isinstance` inside a function returning a bare `TypeGuard[...]` -- exempt by
    # default (`allow-in-type-guards` defaults to True).
    """
    from typing import TypeGuard

    def is_int(value: object) -> TypeGuard[int]:
        return isinstance(value, int)
    """,
    # `issubclass` inside a function returning `typing_extensions.TypeIs[...]` --
    # the attribute-qualified spelling.
    """
    import typing_extensions

    def is_int_subclass(cls: type) -> typing_extensions.TypeIs[type[int]]:
        return issubclass(cls, int)
    """,
    # A quoted (stringified) `TypeGuard[...]` return annotation.
    """
    def is_str(value: object) -> "TypeGuard[str]":
        return isinstance(value, str)
    """,
    # `async def` guard function.
    """
    from typing import TypeGuard

    async def is_ready(value: object) -> TypeGuard[Ready]:
        return isinstance(value, Ready)
    """,
    # The isinstance call sits inside nested control flow, but the nearest
    # enclosing `def` is still the guard function itself.
    """
    from typing import TypeGuard

    def is_number(value: object) -> TypeGuard[int | float]:
        if True:
            return isinstance(value, (int, float))
        return False
    """,
    # `TypeIs` imported from `typing`, multiple isinstance calls all inside the
    # same guard function.
    """
    from typing import TypeIs

    def is_pair(value: object) -> TypeIs[tuple[int, int]]:
        return (
            isinstance(value, tuple)
            and len(value) == 2
            and isinstance(value[0], int)
        )
    """,
    # No isinstance/issubclass call at all -- sanity baseline.
    """
    def add(a: int, b: int) -> int:
        return a + b
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation: module-level branching.
    ("""
    if isinstance(value, int):
        handle(value)
    """, 1),
    # `issubclass` at module level.
    ("""
    if issubclass(cls, Base):
        handle(cls)
    """, 1),
    # `isinstance` in a plain function with no return annotation at all.
    ("""
    def check(value: object) -> bool:
        return isinstance(value, int)
    """, 1),
    # `isinstance` in a function whose return annotation is unrelated to
    # TypeGuard/TypeIs.
    ("""
    def classify(value: object) -> str:
        if isinstance(value, int):
            return "int"
        if isinstance(value, str):
            return "str"
        return "other"
    """, 2),
    # `issubclass` with a tuple of classes, in a plain function.
    ("""
    def check(cls: type) -> bool:
        return issubclass(cls, (int, float))
    """, 1),
    # A function nested inside a TypeGuard guard, but the nested function has no
    # TypeGuard/TypeIs annotation of its own: the nearest `def` decides, so this
    # is still flagged even though the outer function is a guard.
    ("""
    from typing import TypeGuard

    def is_int(value: object) -> TypeGuard[int]:
        def inner() -> bool:
            return isinstance(value, int)
        return inner()
    """, 1),
    # `isinstance` inside a TypeGuard guard, but with `allow-in-type-guards`
    # explicitly turned off: strict mode flags everything.
    ("""
    from typing import TypeGuard

    def is_int(value: object) -> TypeGuard[int]:
        return isinstance(value, int)
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS[:-1])
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_allow_in_type_guards_false_flags_isinstance_inside_a_guard() -> None:
    snippet, count = INVALID_SNIPPETS[-1]
    assert_invalid(
        RULE, snippet, count=count, options={"allow-in-type-guards": False}
    )


def test_allow_in_type_guards_true_is_the_default() -> None:
    assert_valid(
        RULE,
        [
            """
            from typing import TypeGuard

            def is_int(value: object) -> TypeGuard[int]:
                return isinstance(value, int)
            """
        ],
    )


def test_message_prescribes_the_io_boundary_fix() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        if isinstance(value, int):
            handle(value)
        """,
    )
    assert "`isinstance(...)`" in diagnostic.message
    assert "I/O boundary" in diagnostic.message
    assert "TypeIs" in diagnostic.message


def test_unknown_option_is_rejected_by_the_harness() -> None:
    with pytest.raises(ValueError, match="unknown-option"):
        assert_valid(
            RULE,
            ["isinstance(value, int)"],
            options={"unknown-option": True},
        )
