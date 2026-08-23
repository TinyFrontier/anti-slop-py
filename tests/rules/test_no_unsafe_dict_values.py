"""Tests for the ``no-unsafe-dict-values`` rule."""

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
    # An alias of a narrow dict type is exactly what this rule asks for.
    """
    Metadata = dict[str, str]

    def save(payload: Metadata) -> None: ...
    """,
    # Mutually recursive aliases resolve to nothing: the cycle guard stops the chain
    # instead of substituting forever.
    """
    A = B
    B = A

    def save(payload: A) -> None: ...
    """,
    # An alias imported from another module is not followed -- no cross-module
    # inference anywhere in this linter.
    """
    from other.module import Metadata

    def save(payload: Metadata) -> None: ...
    """,
    # An alias bound twice in the same scope proves nothing: there is no flow
    # analysis to say which binding is live at the annotation.
    """
    Metadata = dict[str, Any]
    Metadata = dict[str, str]

    def save(payload: Metadata) -> None: ...
    """,
    # An annotated *variable* is not a type alias, so its value is not a type.
    """
    config: Mapping[str, str] = {}

    def save(payload: config) -> None: ...
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
    # Phase 2: a bare alias hides the same dict. The definition is a plain `Assign`,
    # so the violation is reported where the name is used as a type.
    ("""
    Metadata = dict[str, Any]

    def save(payload: Metadata) -> None: ...
    """, 1),
    # A chain of aliases, each of which was supposed to be narrowing something.
    ("""
    Metadata = dict[str, Any]
    Payload = Metadata

    def save(payload: Payload) -> None: ...
    """, 1),
    # The alias sits at the value position of a real dict annotation.
    ("""
    Value = Any

    def save(values: dict[str, Value]) -> None: ...
    """, 1),
    # The alias is nested inside another container.
    ("""
    Metadata = dict[str, Any]

    def save(batches: list[Metadata]) -> None: ...
    """, 1),
    # A PEP 695 alias declares its right-hand side a type, so it is reported at the
    # definition as well as at the annotation that uses it.
    ("""
    type Metadata = dict[str, Any]

    def save(payload: Metadata) -> None: ...
    """, 2),
    # Every use of the alias is its own violation.
    ("""
    Metadata = dict[str, Any]

    def save(payload: Metadata) -> None: ...

    def load() -> Metadata: ...
    """, 2),
    # An alias used in an `AnnAssign` annotation.
    ("""
    Metadata = Mapping[str, object]

    config: Metadata = {}
    """, 1),
    # An alias defined and used inside a function body.
    ("""
    def build() -> None:
        Metadata = dict[str, Any]
        config: Metadata = {}
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


def test_alias_message_names_the_alias_and_what_it_resolves_to() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        Metadata = dict[str, Any]

        def save(payload: Metadata) -> None: ...
        """,
    )
    assert "`Metadata`" in diagnostic.message
    assert "`dict[str, Any]`" in diagnostic.message
    assert "TypedDict" in diagnostic.message
    # Anchored on the annotation that uses the alias, not on its definition.
    assert (diagnostic.line, diagnostic.col) == (3, 19)


def test_a_locally_narrowed_alias_shadows_the_module_one() -> None:
    assert_valid(
        RULE,
        ["""
        Metadata = dict[str, Any]

        def build() -> None:
            Metadata = dict[str, str]
            config: Metadata = {}
        """],
    )


def test_has_no_options() -> None:
    with pytest.raises(ValueError, match="allow-anything"):
        assert_valid(
            RULE,
            ["def save(values: dict[str, Any]) -> None: ..."],
            options={"allow-anything": True},
        )
