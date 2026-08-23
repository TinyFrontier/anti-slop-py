"""Tests for the ``no-shape-in-symbol-names`` rule."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_shape_in_symbol_names import RULE

VALID_SNIPPETS = [
    # An attribute *access* is not a declaration -- the numpy false-positive surface.
    """
    rows = array.shape[0]
    """,
    # An attribute *target* is not a bare-name declaration either.
    """
    class Layer:
        def __init__(self) -> None:
            self.input_shape = compute()
    """,
    # String contents are never inspected.
    """
    message = "the shape is round"
    """,
    # Lambda parameters are out of scope for this rule.
    """
    handler = lambda shape: shape
    """,
    # A plain (non-aliased) import binds the module's own name, not a chosen one.
    """
    import shape_utils
    """,
    # A `for` loop target is a known limitation, not walked by this rule.
    """
    for shape in items:
        process(shape)
    """,
    # A `with ... as` target is likewise not walked.
    """
    with open(path) as shape:
        read(shape)
    """,
]

INVALID_SNIPPETS = [
    # A function name.
    ("""
    def compute_shape(): ...
    """, 1),
    # An async function name.
    ("""
    async def compute_shape(): ...
    """, 1),
    # A class name.
    ("""
    class ShapeModel: ...
    """, 1),
    # A parameter name.
    ("""
    def resize(shape): ...
    """, 1),
    # Function name and two parameter names all match: three diagnostics.
    ("""
    def get_shape(shape, other_shape): ...
    """, 3),
    # A plain assignment target.
    ("""
    board_shape = compute()
    """, 1),
    # An annotated assignment target.
    ("""
    board_shape: Board = compute()
    """, 1),
    # An augmented assignment target.
    ("""
    shape_count += 1
    """, 1),
    # The walrus operator.
    ("""
    if (shape_val := compute()) is not None:
        use(shape_val)
    """, 1),
    # Tuple destructuring: only the matching element is flagged.
    ("""
    first_shape, second = pair()
    """, 1),
    # An aliased import.
    ("""
    import numpy as shape_lib
    """, 1),
    # An aliased from-import.
    ("""
    from module import Thing as shape_thing
    """, 1),
    # Case-insensitive matching.
    ("""
    Shape_Handler = get()
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_empty_terms_option_disables_the_rule() -> None:
    assert_valid(
        RULE,
        [
            """
            def get_shape():
                shape = compute()
                return shape
            """
        ],
        options={"terms": ()},
    )


# anti-slop: ignore[no-shape-in-symbol-names]
def test_default_terms_is_shape_only() -> None:
    assert_invalid(
        RULE,
        """
        board_shape = compute()
        """,
        count=1,
    )


def test_two_terms_option_matches_either() -> None:
    assert_invalid(
        RULE,
        """
        dims_count = 1
        board_shape = 2
        untouched = 3
        """,
        count=2,
        options={"terms": ("shape", "dims")},
    )


def test_message_names_the_symbol_and_the_term() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        board_shape = compute()
        """,
    )
    assert "`board_shape`" in diagnostic.message
    assert "`shape`" in diagnostic.message


def test_unknown_option_is_rejected_by_the_harness() -> None:
    with pytest.raises(ValueError, match="unknown-option"):
        assert_valid(
            RULE,
            ["board_shape = compute()"],
            options={"unknown-option": True},
        )
