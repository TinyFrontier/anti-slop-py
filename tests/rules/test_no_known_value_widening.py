"""Tests for the ``no-known-value-widening`` rule (PLAN.md section 3.4, row 3)."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_known_value_widening import RULE

VALID_SNIPPETS = [
    # A narrow annotation is idiomatic -- Python checkers don't infer dict keys, so
    # this is not the erased-literal-key case the original TS rule also covers.
    """
    handlers: dict[str, Handler] = {"start": on_start, "stop": on_stop}
    """,
    # The value is a call, not a syntactic literal, even under a wide annotation.
    """
    config: Any = load()
    """,
    # An f-string is `ast.JoinedStr`, not `ast.Constant`: its value depends on `a`.
    """
    x: object = f"{a}"
    """,
    # No value at all -- a bare declaration.
    """
    x: Any
    """,
    # A narrow, non-widening annotation with a literal is fine.
    """
    x: int = 5
    """,
    # A call that happens to build a dict is `ast.Call`, not `ast.Dict`.
    """
    config: Any = dict(a=1)
    """,
    # A unary-negated literal is `ast.UnaryOp`, not `ast.Constant`.
    """
    x: Any = -1
    """,
]

INVALID_SNIPPETS = [
    # The canonical violation.
    ("""
    config: Any = {"retries": 3}
    """, 1),
    # `object` with a list literal.
    ("""
    values: object = [1, 2, 3]
    """, 1),
    # Fully qualified `typing.Any`.
    ("""
    config: typing.Any = {"a": 1}
    """, 1),
    # Fully qualified `builtins.object`.
    ("""
    config: builtins.object = (1, 2)
    """, 1),
    # A quoted (stringified) annotation.
    ("""
    config: "Any" = {"a": 1}
    """, 1),
    # A set literal.
    ("""
    tags: Any = {"a", "b"}
    """, 1),
    # A bare scalar constant.
    ("""
    retries: Any = 3
    """, 1),
    # Inside a function body.
    ("""
    def build() -> None:
        config: Any = {"a": 1}
    """, 1),
    # Inside a class body.
    ("""
    class Settings:
        config: object = {"a": 1}
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
        config: Any = {"retries": 3}
        """,
    )
    assert "`Any`" in diagnostic.message
    assert "Final" in diagnostic.message
    assert "TypedDict" in diagnostic.message
