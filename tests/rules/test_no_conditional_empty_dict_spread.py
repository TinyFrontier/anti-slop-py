"""Tests for the ``no-conditional-empty-dict-spread`` rule (PLAN.md section 3.4, row 2)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_conditional_empty_dict_spread import RULE

VALID_SNIPPETS = [
    # Both branches are non-empty: no field is silently dropped.
    """
    config = {**({"timeout": t} if t is not None else {"timeout": DEFAULT})}
    """,
    # An unconditional empty spread -- a different, harmless-looking smell.
    """
    config = {**{}}
    """,
    # A plain spread of a variable, no ternary involved.
    """
    config = {**other}
    """,
    # A `dict(...)` call with a plain kwarg spread, no ternary.
    """
    config = dict(a=1, **other)
    """,
    # The ternary is a named value, not a spread -- an explicit optional field.
    """
    config = {"extra": {"timeout": t} if t is not None else {}}
    """,
    # The ternary's branches are variables, not dict literals: emptiness is unknown.
    """
    config = {**(a if condition else b)}
    """,
    # A spread of the same shape, but the call target is not `dict`.
    """
    config = configure(**({"retries": r} if r else {}))
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation: empty dict in the `else` branch.
    ("""
    config = {**({"timeout": t} if t is not None else {})}
    """, 1),
    # Empty dict in the `if` branch instead.
    ("""
    config = {**({} if not t else {"timeout": t})}
    """, 1),
    # The same shape as a keyword spread into a `dict(...)` call.
    ("""
    config = dict(**({"timeout": t} if t is not None else {}))
    """, 1),
    # A conditional spread alongside an explicit key in the same dict literal.
    ("""
    config = {"mode": "prod", **({"timeout": t} if t is not None else {})}
    """, 1),
    # Two independent conditional spreads in the same dict literal.
    ("""
    config = {
        **({"a": a} if a else {}),
        **({"b": b} if b else {}),
    }
    """, 2),
    # Inside a comprehension.
    ("""
    configs = [{**({"timeout": t} if t else {})} for t in timeouts]
    """, 1),
    # `builtins.dict` spelled out.
    ("""
    import builtins

    config = builtins.dict(**({"timeout": t} if t is not None else {}))
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_prescribes_explicit_statements_or_notrequired() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        config = {**({"timeout": t} if t is not None else {})}
        """,
    )
    assert "optional" in diagnostic.message
    assert "NotRequired" in diagnostic.message
