"""Tests for the ``no-any-type-aliases`` rule (PLAN.md section 3.4, row 12)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_any_type_aliases import RULE

VALID_SNIPPETS = [
    # An alias that names a real type.
    """
    UserId = int
    """,
    # A PEP 695 alias of a narrow dict type.
    """
    type Payload = dict[str, str]
    """,
    # A PEP 613 alias of a container.
    """
    Rows: TypeAlias = Sequence[Row]
    """,
    # A union with a real member: the `Any` half is the parameter/return rules' job,
    # and this alias still narrows nothing away on its own.
    """
    Loose = str | None
    """,
    # Mutually recursive aliases: neither is evidence of `Any`, and the cycle guard
    # keeps resolution from substituting forever.
    """
    A = B
    B = A
    """,
    # A self-referential alias (a recursive type), likewise not `Any`.
    """
    Tree = list[Tree]
    """,
    # `Any` itself rebound to a real type: the alias resolves to `str`, not to the
    # escape hatch, because resolution is lexical rather than by spelling.
    """
    Any = str
    Metadata = Any
    """,
    # A variable annotated `Any` is not a type alias -- no-any-parameters and
    # no-any-returns own the annotation positions that matter.
    """
    fallback: Any = None
    """,
    # A plain assignment inside a function body binds a value, not a module alias.
    """
    def build():
        Metadata = Any
        return Metadata
    """,
    # An alias imported from another module is not followed: no cross-module
    # inference anywhere in this linter.
    """
    from other.module import Metadata

    Payload = Metadata
    """,
    # Chained assignment targets are not read as an alias declaration.
    """
    first = second = Any
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation: a module-level alias of `Any`.
    ("""
    Metadata = Any
    """, 1),
    # The same, with the import spelled out.
    ("""
    from typing import Any

    Metadata = Any
    """, 1),
    # An aliased import of `Any` still resolves to `Any`.
    ("""
    from typing import Any as AnyValue

    Metadata = AnyValue
    """, 1),
    # The attribute spelling.
    ("""
    import typing

    Metadata = typing.Any
    """, 1),
    # PEP 613.
    ("""
    Metadata: TypeAlias = Any
    """, 1),
    # PEP 695.
    ("""
    type Metadata = Any
    """, 1),
    # A two-step lexical chain: both names hide the same escape hatch.
    ("""
    A = Any
    B = A
    """, 2),
    # A longer chain, across all three alias spellings.
    ("""
    type A = Any
    B: TypeAlias = A
    C = B
    """, 3),
    # `Any | None` narrows nothing: the alias still stands for `Any`.
    ("""
    Metadata = Any | None
    """, 1),
    # `Optional[Any]`, the same union in its other spelling.
    ("""
    Metadata: TypeAlias = Optional[Any]
    """, 1),
    # `Union[Any, None]` under PEP 695.
    ("""
    type Metadata = Union[Any, None]
    """, 1),
    # A union of aliases that each resolve to `Any`.
    ("""
    A = Any
    B = Any
    C = A | B
    """, 3),
    # A quoted annotation on the right-hand side of a PEP 613 alias.
    ("""
    Metadata: TypeAlias = "Any"
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_message_names_the_alias_and_a_replacement() -> None:
    (diagnostic,) = assert_invalid(RULE, "Metadata = Any\n")
    assert "`Metadata`" in diagnostic.message
    assert "`Any`" in diagnostic.message
    assert "boundary" in diagnostic.message


def test_diagnostic_points_at_the_resolved_expression() -> None:
    (diagnostic,) = assert_invalid(RULE, "Metadata = Any\n")
    assert (diagnostic.line, diagnostic.col) == (1, 12)


def test_a_locally_shadowed_alias_resolves_to_the_local_binding() -> None:
    """The chain is followed in the scope each name is written in."""
    (diagnostic,) = assert_invalid(
        RULE,
        """
        Metadata = Any

        def build():
            Metadata = str
            Payload: TypeAlias = Metadata
            return Payload
        """,
        count=1,
    )
    # Only the module-level alias is reported: inside `build`, `Metadata` is `str`.
    assert diagnostic.line == 1


def test_local_resolution_also_finds_a_locally_hidden_any() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        Metadata = str

        def build():
            Metadata = Any
            Payload: TypeAlias = Metadata
            return Payload
        """,
        count=1,
    )
    assert diagnostic.line == 5


def test_an_ambiguous_alias_proves_nothing() -> None:
    """Two bindings of one name, no flow analysis: only the direct one is reported."""
    (diagnostic,) = assert_invalid(
        RULE,
        """
        Base = Any
        Base = str

        Metadata = Base
        """,
        count=1,
    )
    assert diagnostic.line == 1
