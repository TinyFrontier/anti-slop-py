"""Tests for the ``no-any-parameters`` rule."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_any_parameters import RULE

VALID_SNIPPETS = [
    # A named domain type.
    """
    def save(value: User) -> None: ...
    """,
    # No annotation at all: implicit Any is a type-checker concern, not ours.
    """
    def save(value): ...
    """,
    # `object` belongs to no-object-parameters, not this rule.
    """
    def save(value: object) -> None: ...
    """,
    # `Any` inside a container annotation belongs to no-unsafe-dict-values.
    """
    def save(values: dict[str, Any]) -> None: ...
    """,
    # `Any` as a return annotation belongs to no-any-returns.
    """
    def load(key: str) -> Any: ...
    """,
    # Lambdas cannot carry annotations, so they can never violate this rule.
    """
    identity = lambda value: value
    handler = lambda *args, **kwargs: None
    """,
    # A default value does not make the annotation acceptable or unacceptable.
    """
    def save(value: Payload = DEFAULT) -> None: ...
    """,
    # An attribute annotation is not a parameter.
    """
    class Box:
        payload: Any
    """,
    # A quoted annotation naming a real type is not `Any`.
    """
    def save(value: "User") -> None: ...
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation.
    ("""
    def save(value: Any) -> None: ...
    """, 1),
    # async def.
    ("""
    async def save(value: Any) -> None: ...
    """, 1),
    # Star-args, both of them -- the ParamSpec case the message calls out.
    ("""
    def dispatch(*args: Any, **kwargs: Any) -> None: ...
    """, 2),
    # Positional-only and keyword-only parameters.
    ("""
    def dispatch(value: Any, /, other: Any, *, extra: Any) -> None: ...
    """, 3),
    # No dunder or "cause" exception exists for this rule (see module docstring).
    ("""
    class Repository:
        def save(self, value: Any) -> None: ...
    """, 1),
    ("""
    def __eq__(left: Any, right: object) -> bool: ...
    """, 1),
    # Qualified `typing.Any` and aliased `t.Any`.
    ("""
    import typing

    def save(value: typing.Any) -> None: ...
    """, 1),
    ("""
    import typing as t

    def save(value: t.Any) -> None: ...
    """, 1),
    # A quoted (stringified) annotation.
    ("""
    def save(value: "Any") -> None: ...
    """, 1),
    # Nested function inside a method.
    ("""
    class Repository:
        def save(self, value: Payload) -> None:
            def encode(raw: Any) -> bytes: ...
    """, 1),
    # A decorated function is still a function.
    ("""
    @retry(attempts=3)
    def save(value: Any) -> None: ...
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_names_the_parameter_and_prescribes_a_fix() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        def save(payload: Any) -> None: ...
        """,
    )
    assert "`payload`" in diagnostic.message
    assert "I/O boundary" in diagnostic.message
    assert "ParamSpec" in diagnostic.message
    assert diagnostic.line == 1
    assert diagnostic.col == 10
    assert diagnostic.end_col == 22


def test_has_no_options() -> None:
    with pytest.raises(ValueError, match="allow-anything"):
        assert_valid(
            RULE,
            ["def save(value: Any) -> None: ..."],
            options={"allow-anything": True},
        )
