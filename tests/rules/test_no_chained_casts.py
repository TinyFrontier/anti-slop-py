"""Tests for the ``no-chained-casts`` rule (PLAN.md section 3.4, row 1)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_chained_casts import RULE

VALID_SNIPPETS = [
    # A single cast: nothing to chain.
    """
    x = cast(User, value)
    """,
    # cast used as an argument to a call that is not itself `cast`.
    """
    x = foo(cast(User, value))
    """,
    # The value is a plain call, not a cast.
    """
    x = cast(User, load_user())
    """,
    # Two independent casts, not nested.
    """
    x = cast(A, v1)
    y = cast(B, v2)
    """,
    # Only one positional argument: no value to inspect.
    """
    x = cast(User)
    """,
    # A `.cast` method unrelated to typing.cast, called with a non-cast value.
    """
    x = query.cast(Integer, column)
    """,
    # typing.cast whose value is a dict literal, not a nested cast.
    """
    x = typing.cast(dict[str, int], {"a": 1})
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation.
    ("""
    x = cast(User, cast(object, value))
    """, 1),
    # Parentheses are invisible to the AST.
    ("""
    x = cast(User, (cast(object, value)))
    """, 1),
    # Fully qualified `typing.cast` on both sides.
    ("""
    x = typing.cast(User, typing.cast(object, value))
    """, 1),
    # Mixed callee forms: bare `cast` outside, `t.cast` inside.
    ("""
    x = cast(User, t.cast(object, value))
    """, 1),
    # The value passed via the `val=` keyword.
    ("""
    x = cast(User, val=cast(object, value))
    """, 1),
    # Nested inside an unrelated container/comprehension.
    ("""
    items = [cast(User, cast(object, v)) for v in vs]
    """, 1),
    # Triple nesting reports once per cast whose value is itself a cast:
    # the outer cast (value = middle cast) and the middle cast (value = inner
    # cast) both report; the innermost cast's value is not a cast, so it does not.
    ("""
    x = cast(A, cast(B, cast(C, value)))
    """, 2),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_prescribes_parsing_at_the_boundary() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        x = cast(User, cast(object, value))
        """,
    )
    assert "cast" in diagnostic.message
    assert "boundary" in diagnostic.message
