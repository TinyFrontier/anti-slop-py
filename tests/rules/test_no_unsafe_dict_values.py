"""Tests for the ``no-unsafe-dict-values`` rule (PLAN.md section 3.4, row 13)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_unsafe_dict_values import RULE

VALID_SNIPPETS = [
    # A safe, narrow value type.
    """
    def save(values: dict[str, str]) -> None: ...
    """,
    # A union value type with no slop member.
    """
    def load() -> Mapping[str, int | None]: ...
    """,
    # No annotation at all: implicit Any is a type-checker concern, not ours.
    """
    def save(values): ...
    """,
    # A bare, unsubscripted container names no value type to check.
    """
    def save(values: dict) -> None: ...
    """,
    # A safe value type in an `AnnAssign`.
    """
    config: dict[str, int] = {}
    """,
    # Lambdas cannot carry annotations, so they can never violate this rule.
    """
    build = lambda: {}
    """,
    # `Any`/`object` as a bare parameter (not inside a dict) belongs to
    # no-any-parameters / no-object-parameters, not this rule.
    """
    def save(value: Any, other: object) -> None: ...
    """,
    # `Any` inside a non-dict container is out of this rule's scope.
    """
    def save(values: list[Any]) -> None: ...
    """,
    # Alias resolution is phase 2's job: the plain (unannotated) `Assign` that
    # defines the alias, and every later use of the alias name, are both untouched.
    """
    Metadata = dict[str, Any]

    def save(payload: Metadata) -> None: ...
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation: a parameter.
    ("""
    def save(values: dict[str, Any]) -> None: ...
    """, 1),
    # A return annotation, with `object` rather than `Any`.
    ("""
    def load() -> Mapping[str, object]: ...
    """, 1),
    # A module-level `AnnAssign` variable.
    ("""
    config: dict[str, Any] = {}
    """, 1),
    # A class field.
    ("""
    class Box:
        values: Mapping[str, object]
    """, 1),
    # The attribute (fully qualified) spelling of the container.
    ("""
    import typing

    def save(values: typing.Mapping[str, Any]) -> None: ...
    """, 1),
    # `defaultdict`.
    ("""
    counts: defaultdict[str, Any] = defaultdict(int)
    """, 1),
    # A union member is the slop type: `X | Any`.
    ("""
    def save(values: dict[str, X | Any]) -> None: ...
    """, 1),
    # `Union[X, object]`.
    ("""
    def save(values: dict[str, Union[X, object]]) -> None: ...
    """, 1),
    # `Optional[Any]`.
    ("""
    def save(values: dict[str, Optional[Any]]) -> None: ...
    """, 1),
    # A dict nested inside a dict: only the inner, actually-unsafe one is reported.
    ("""
    def save(values: dict[str, dict[str, Any]]) -> None: ...
    """, 1),
    # The container is itself wrapped in `Optional`; still found by the tree walk.
    ("""
    def save(values: Optional[dict[str, Any]]) -> None: ...
    """, 1),
    # A direct `TypeAlias` declaration: PEP 613's right-hand side is the type itself,
    # syntactically visible without any alias resolution.
    ("""
    Metadata: TypeAlias = dict[str, Any]
    """, 1),
    # async def, and both star-args at once.
    ("""
    async def dispatch(*args: dict[str, Any], **kwargs: dict[str, Any]) -> None: ...
    """, 2),
    # Positional-only and keyword-only parameters.
    ("""
    def dispatch(values: dict[str, Any], /, other: Mapping[str, Any], *, extra: dict[str, Any]) -> None: ...
    """, 3),
    # Nested function inside a method.
    ("""
    class Repository:
        def save(self, value: Payload) -> None:
            def encode(raw: dict[str, Any]) -> bytes: ...
    """, 1),
    # A decorated function is still a function.
    ("""
    @retry(attempts=3)
    def save(values: dict[str, Any]) -> None: ...
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_names_the_annotation_and_the_value_type() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        def save(values: dict[str, Any]) -> None: ...
        """,
    )
    assert "`dict[str, Any]`" in diagnostic.message
    assert "`Any`" in diagnostic.message
    assert "TypedDict" in diagnostic.message


def test_has_no_options() -> None:
    with pytest.raises(ValueError, match="allow-anything"):
        assert_valid(
            RULE,
            ["def save(values: dict[str, Any]) -> None: ..."],
            options={"allow-anything": True},
        )
