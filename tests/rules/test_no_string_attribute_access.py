"""Tests for the ``no-string-attribute-access`` rule."""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid

from anti_slop.rules.no_string_attribute_access import RULE

VALID_SNIPPETS = [
    # Direct attribute access, read/write/delete -- no reflective call at all.
    """
    value = obj.attr
    obj.attr = new_value
    del obj.attr
    """,
    # `hasattr` is deliberately never touched by this rule.
    """
    if hasattr(obj, "attr"):
        value = obj.attr
    """,
    # An attribute-form callee (`reflection.getattr`) is not the bare builtin.
    """
    value = reflection.getattr(obj, "attr")
    """,
    # `getattr` referenced but never called: no `ast.Call` to flag.
    """
    accessor = getattr
    """,
    # A method literally named `setattr`, called through attribute access.
    """
    class Box:
        def setattr(self, name: str, value: object) -> None: ...

    box = Box()
    box.setattr("attr", 1)
    """,
    # Unrelated builtin calls -- proving no cross-triggering.
    """
    length = len(items)
    text = str(value)
    """,
    # `getattr` shadowed as a local name and stored, then called through the
    # local name is still technically the same AST shape as the builtin and
    # would be flagged (known limitation); this snippet instead just proves a
    # bare reference to a shadowing name causes no crash or false report.
    """
    def getattr(obj, name):
        return None
    """,
]

INVALID_SNIPPETS = [
    # `getattr` with two arguments, literal name: direct-access recipe.
    ("""
    value = getattr(obj, "attr")
    """, 1),
    # `getattr` with three arguments (a default), literal name: contract recipe.
    ("""
    value = getattr(obj, "attr", None)
    """, 1),
    # `getattr` with two arguments, dynamic name.
    ("""
    value = getattr(obj, field_name)
    """, 1),
    # `getattr` with three arguments, dynamic name: still the dynamic message,
    # the default does not change which message applies once the name is dynamic.
    ("""
    value = getattr(obj, field_name, None)
    """, 1),
    # `setattr` with a literal name.
    ("""
    setattr(obj, "attr", 1)
    """, 1),
    # `setattr` with a dynamic name.
    ("""
    setattr(obj, field_name, 1)
    """, 1),
    # `delattr` with a literal name.
    ("""
    delattr(obj, "attr")
    """, 1),
    # `delattr` with a dynamic name.
    ("""
    delattr(obj, field_name)
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_literal_two_arg_message_prescribes_direct_access() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        value = getattr(obj, "attr")
        """,
    )
    assert "obj.attr" in diagnostic.message
    assert "direct attribute access" in diagnostic.message


def test_literal_with_default_message_prescribes_a_contract_check() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        value = getattr(obj, "attr", None)
        """,
    )
    assert "contract" in diagnostic.message
    assert "Protocol" in diagnostic.message


def test_dynamic_message_prescribes_parsing_at_the_boundary() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        value = getattr(obj, field_name)
        """,
    )
    assert "I/O boundary" in diagnostic.message
    assert "field_name" in diagnostic.message


def test_setattr_literal_message_prescribes_assignment() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        setattr(obj, "attr", 1)
        """,
    )
    assert "obj.attr = 1" in diagnostic.message


def test_delattr_literal_message_prescribes_del_statement() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        delattr(obj, "attr")
        """,
    )
    assert "del obj.attr" in diagnostic.message
