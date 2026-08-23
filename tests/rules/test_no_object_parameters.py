"""Tests for the ``no-object-parameters`` rule (PLAN.md section 3.4, row 5)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_object_parameters import RULE

VALID_SNIPPETS = [
    # A named domain type.
    """
    def save(value: User) -> None: ...
    """,
    # No annotation at all: implicit Any is a type-checker concern, not ours.
    """
    def save(value): ...
    """,
    # `object` inside a container annotation belongs to no-unsafe-dict-values.
    """
    def save(values: dict[str, object]) -> None: ...
    """,
    # `object` as a return annotation belongs to no-any-returns.
    """
    def load(key: str) -> object: ...
    """,
    # typeshed convention: comparison dunders must accept `object`.
    """
    class Money:
        def __eq__(self, other: object) -> bool: ...
        def __ne__(self, other: object) -> bool: ...
        def __contains__(self, item: object) -> bool: ...
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
        payload: object
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation.
    ("""
    def save(value: object) -> None: ...
    """, 1),
    # async def.
    ("""
    async def save(value: object) -> None: ...
    """, 1),
    # Positional-only and keyword-only parameters.
    ("""
    def dispatch(value: object, /, other: object, *, extra: object) -> None: ...
    """, 3),
    # A method that is not one of the exempt comparison dunders.
    ("""
    class Repository:
        def save(self, value: object) -> None: ...
    """, 1),
    # `__eq__` at module level is a plain function, not a typeshed-shaped dunder.
    ("""
    def __eq__(left: object, right: object) -> bool: ...
    """, 2),
    # Fully qualified builtins.object.
    ("""
    import builtins

    def save(value: builtins.object) -> None: ...
    """, 1),
    # A quoted (stringified) annotation.
    ("""
    def save(value: "object") -> None: ...
    """, 1),
    # Nested function inside a method.
    ("""
    class Repository:
        def save(self, value: Payload) -> None:
            def encode(raw: object) -> bytes: ...
    """, 1),
    # A decorated function is still a function.
    ("""
    @retry(attempts=3)
    def save(value: object) -> None: ...
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_allow_object_option_disables_the_rule() -> None:
    assert_valid(
        RULE,
        [
            """
            def save(value: object) -> None: ...
            """,
            """
            async def dispatch(*args: object, **kwargs: object) -> None: ...
            """,
        ],
        options={"allow-object": True},
    )


def test_allow_object_false_is_the_default() -> None:
    assert_invalid(
        RULE,
        """
        def save(value: object) -> None: ...
        """,
        count=1,
        options={"allow-object": False},
    )


def test_message_names_the_parameter_and_prescribes_a_fix() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        def save(payload: object) -> None: ...
        """,
    )
    assert "`payload`" in diagnostic.message
    assert "I/O boundary" in diagnostic.message
    assert diagnostic.line == 1
    assert diagnostic.col == 10
    assert diagnostic.end_col == 25


def test_unknown_option_is_rejected_by_the_harness() -> None:
    with pytest.raises(ValueError, match="allow-anything"):
        assert_valid(
            RULE,
            ["def save(value: object) -> None: ..."],
            options={"allow-anything": True},
        )


def test_variadic_object_is_allowed_by_default() -> None:
    assert_valid(
        RULE,
        [
            "def stub(*args: object, **kwargs: object) -> bool: ...",
            "async def _send(**_kwargs: object) -> None: ...",
        ],
    )


def test_variadic_object_flagged_when_strict() -> None:
    assert_invalid(
        RULE,
        "def stub(*args: object, **kwargs: object) -> bool: ...",
        count=2,
        options={"allow-variadic-object": False},
    )


def test_named_parameters_still_flagged_alongside_variadic() -> None:
    assert_invalid(
        RULE,
        "def parse(raw: object, **kwargs: object) -> None: ...",
        count=1,
    )
