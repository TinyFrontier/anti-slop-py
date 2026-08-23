"""Tests for the ``no-any-returns`` rule (PLAN.md section 3.4, row 11)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_any_returns import RULE

VALID_SNIPPETS = [
    # A named domain type.
    """
    def load() -> User: ...
    """,
    # No return annotation at all: implicit Any is a type-checker concern, not ours.
    """
    def load(): ...
    """,
    # `object` belongs elsewhere, not this rule: it only bans `Any`.
    """
    def load() -> object: ...
    """,
    # A real result type inside `Awaitable` is exactly the fix this rule wants.
    """
    def load() -> Awaitable[User]: ...
    """,
    # `Coroutine`'s first two arguments (yield/send types) are conventionally `Any`
    # even in well-typed code -- only the *last* element (the result type) matters.
    """
    def load() -> Coroutine[Any, Any, User]: ...
    """,
    # Lambdas cannot carry annotations, so they can never violate this rule.
    """
    computed = lambda: 42
    """,
    # A quoted annotation naming a real type is not `Any`.
    """
    def load() -> "User": ...
    """,
    # A parameter annotated `Any` belongs to no-any-parameters, not this rule.
    """
    def process(value: Any) -> User: ...
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation.
    ("""
    def load() -> Any: ...
    """, 1),
    # async def.
    ("""
    async def load() -> Any: ...
    """, 1),
    # `Awaitable[Any]` -- the wrapper is named but the result type is erased.
    ("""
    def load() -> Awaitable[Any]: ...
    """, 1),
    # `Coroutine[..., Any]` -- the result type, the last subscript element, is `Any`.
    ("""
    def load() -> Coroutine[Any, Any, Any]: ...
    """, 1),
    # async def returning an `Awaitable[Any]` explicitly.
    ("""
    async def load() -> Awaitable[Any]: ...
    """, 1),
    # A quoted (stringified) annotation.
    ("""
    def load() -> "Any": ...
    """, 1),
    # Qualified `typing.Any` and aliased `t.Any`.
    ("""
    import typing

    def load() -> typing.Any: ...
    """, 1),
    ("""
    import typing as t

    def load() -> t.Any: ...
    """, 1),
    # Nested function inside a method.
    ("""
    class Repository:
        def save(self) -> None:
            def encode() -> Any: ...
    """, 1),
    # A decorated function is still a function.
    ("""
    @retry(attempts=3)
    def load() -> Any: ...
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_names_the_annotation_and_prescribes_a_fix() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        def load() -> Any: ...
        """,
    )
    assert "`Any`" in diagnostic.message
    assert "domain type" in diagnostic.message
    assert diagnostic.line == 1
    assert diagnostic.col == 15
    assert diagnostic.end_col == 18


def test_awaitable_message_names_the_full_annotation() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        def load() -> Awaitable[Any]: ...
        """,
    )
    assert "`Awaitable[Any]`" in diagnostic.message


def test_has_no_options() -> None:
    with pytest.raises(ValueError, match="allow-anything"):
        assert_valid(
            RULE,
            ["def load() -> Any: ..."],
            options={"allow-anything": True},
        )
