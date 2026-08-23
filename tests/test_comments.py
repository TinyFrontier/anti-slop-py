"""Tests for the phase-2 comment primitives.

Line and file suppressions of anti-slop's own directives live in
``test_suppressions.py``; this module covers the two primitives added for
``require-safety-comment``: ``# SAFETY:`` lookup (including the climb to the owning
statement) and type-checker suppression parsing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from anti_slop.engine.comments import (
    CommentMap,
    build_comment_map,
    checker_suppressions,
    safety_above,
    safety_comment_for,
    safety_invariant_at,
)
from anti_slop.engine.walker import ParentMap

SOURCE_PATH = Path("snippet.py")


def _map(source: str) -> CommentMap:
    return build_comment_map(SOURCE_PATH, source)


def _parent_map(tree: ast.Module) -> ParentMap:
    parents = ParentMap()
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents.record(child, node)
    return parents


def _cast_call(source: str) -> tuple[ast.Call, ParentMap]:
    """The ``cast(...)`` call of ``source``, plus a filled parent map."""
    tree = ast.parse(source)
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "cast"
    )
    return call, _parent_map(tree)


# --------------------------------------------------------------------------- #
# safety_invariant_at
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("# SAFETY: the row is selected by primary key", "the row is selected by primary key"),
        ("#SAFETY:tight", "tight"),
        ("# SAFETY : spaced colon", "spaced colon"),
        ("# noqa  # SAFETY: after another directive", "after another directive"),
    ],
)
def test_safety_invariant_is_the_text_after_the_marker(comment: str, expected: str) -> None:
    comments = _map(f"x = 1  {comment}\n")
    assert safety_invariant_at(comments, 1) == expected


@pytest.mark.parametrize(
    "comment",
    [
        "# SAFETY:",
        "# SAFETY:   ",
        "# safety: lower case is not the marker",
        "# UNSAFETY-ish prose without a colon",
        "# just an ordinary comment",
    ],
)
def test_lines_without_a_stated_invariant_have_none(comment: str) -> None:
    comments = _map(f"x = 1  {comment}\n")
    assert safety_invariant_at(comments, 1) is None


def test_safety_invariant_on_a_line_without_a_comment_is_none() -> None:
    comments = _map("x = 1\n")
    assert safety_invariant_at(comments, 1) is None
    assert safety_invariant_at(comments, 99) is None


# --------------------------------------------------------------------------- #
# safety_comment_for
# --------------------------------------------------------------------------- #


def test_safety_on_the_nodes_own_line() -> None:
    source = "user = cast(User, raw)  # SAFETY: parsed by the loader\n"
    call, parents = _cast_call(source)
    assert safety_comment_for(_map(source), call, parents.parent_of) == "parsed by the loader"


def test_safety_on_the_line_directly_above_the_node() -> None:
    source = "# SAFETY: parsed by the loader\nuser = cast(User, raw)\n"
    call, parents = _cast_call(source)
    assert safety_comment_for(_map(source), call, parents.parent_of) is not None


def test_safety_above_the_owning_statement_of_a_multiline_expression() -> None:
    source = (
        "# SAFETY: rows come from our own primary key column\n"
        "users = [\n"
        "    cast(User, row)\n"
        "    for row in rows\n"
        "]\n"
    )
    call, parents = _cast_call(source)
    assert call.lineno == 3, "the cast must be far enough down to need the owner climb"
    assert safety_comment_for(_map(source), call, parents.parent_of) is not None


def test_safety_trailing_on_the_owning_statements_own_line() -> None:
    source = "value = compute(  # SAFETY: the handler registry is loaded at import\n    other,\n    cast(User, raw),\n)\n"
    call, parents = _cast_call(source)
    assert call.lineno == 3
    assert safety_comment_for(_map(source), call, parents.parent_of) is not None


def test_safety_two_lines_above_does_not_cover_the_node() -> None:
    source = "# SAFETY: too far away\n\nuser = cast(User, raw)\n"
    call, parents = _cast_call(source)
    assert safety_comment_for(_map(source), call, parents.parent_of) is None


def test_empty_safety_does_not_cover_the_node() -> None:
    source = "# SAFETY:\nuser = cast(User, raw)\n"
    call, parents = _cast_call(source)
    assert safety_comment_for(_map(source), call, parents.parent_of) is None


def test_a_multiline_safety_block_covers_the_node_below_it() -> None:
    source = (
        "# SAFETY: the walker looks the handler up by the runtime type of the node\n"
        "# it is about to dispatch, so it is only ever called with that node type.\n"
        "erased = cast(Handler, handler)\n"
    )
    call, parents = _cast_call(source)
    assert safety_comment_for(_map(source), call, parents.parent_of) is not None


def test_safety_above_climbs_only_through_own_line_comments() -> None:
    source = (
        "# SAFETY: this one is about the import below it\n"
        "import models  # an ordinary trailing note\n"
        "user = cast(User, payload)\n"
    )
    call, parents = _cast_call(source)
    assert safety_comment_for(_map(source), call, parents.parent_of) is None


def test_safety_above_stops_at_a_blank_line() -> None:
    comments = _map("# SAFETY: far above\n# still the same block\n\nx = 1\n")
    assert safety_above(comments, 2) == "far above"
    assert safety_above(comments, 4) is None


def test_safety_lookup_on_a_position_less_node_uses_the_owner_only() -> None:
    source = "# SAFETY: the parser guarantees a str here\nvalue = raw.strip()\n"
    tree = ast.parse(source)
    parents = _parent_map(tree)
    load = next(node for node in ast.walk(tree) if isinstance(node, ast.Load))
    assert safety_comment_for(_map(source), load, parents.parent_of) is not None


def test_safety_lookup_on_the_module_itself_is_none() -> None:
    source = "# SAFETY: a module owns no statement\nx = 1\n"
    tree = ast.parse(source)
    assert safety_comment_for(_map(source), tree, lambda _node: None) is None


# --------------------------------------------------------------------------- #
# checker_suppressions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("comment", "checker", "codes"),
    [
        ("# type: ignore", "type", ()),
        ("# type: ignore[arg-type]", "type", ("arg-type",)),
        ("# ty: ignore[unresolved-attribute]", "ty", ("unresolved-attribute",)),
        ("# pyright: ignore[reportUnknownMemberType]", "pyright", ("reportUnknownMemberType",)),
        ("#type:ignore[arg-type]", "type", ("arg-type",)),
        ("# type: ignore[arg-type, ty:possibly-unbound]", "type", ("arg-type", "ty:possibly-unbound")),
    ],
)
def test_every_dialect_is_recognized(
    comment: str, checker: str, codes: tuple[str, ...]
) -> None:
    (suppression,) = checker_suppressions(_map(f"x = load()  {comment}\n"))
    assert suppression.checker == checker
    assert suppression.codes == codes
    assert suppression.has_codes is bool(codes)
    assert suppression.line == 1


def test_empty_brackets_count_as_no_code() -> None:
    (suppression,) = checker_suppressions(_map("x = load()  # type: ignore[]\n"))
    assert suppression.codes == ()
    assert suppression.has_codes is False


def test_one_comment_can_carry_two_directives() -> None:
    source = "x = load()  # type: ignore[arg-type]  # pyright: ignore[reportAny]\n"
    first, second = checker_suppressions(_map(source))
    assert (first.checker, second.checker) == ("type", "pyright")
    assert first.col < second.col, "each directive reports at its own column"


def test_anti_slop_suppressions_are_not_checker_suppressions() -> None:
    source = "def save(value: object) -> None:  # anti-slop: ignore[no-object-parameters]\n    ...\n"
    assert checker_suppressions(_map(source)) == ()


def test_prose_and_other_directives_are_not_suppressions() -> None:
    source = "x = 1  # noqa: E501\ny = 2  # SAFETY: nothing to suppress here\nz = 3  # types: ignore\n"
    assert checker_suppressions(_map(source)) == ()


def test_directive_span_points_at_the_directive_not_the_comment() -> None:
    source = "x = load()  # noqa  # ty: ignore[unresolved-attribute]\n"
    (suppression,) = checker_suppressions(_map(source))
    assert source[suppression.col : suppression.end_col] == "# ty: ignore[unresolved-attribute]"
    assert suppression.text == "# ty: ignore[unresolved-attribute]"


def test_suppressions_are_returned_in_source_order() -> None:
    source = "a = 1  # pyright: ignore[reportAny]\nb = 2  # ty: ignore[bad]\nc = 3  # type: ignore\n"
    lines = [suppression.line for suppression in checker_suppressions(_map(source))]
    assert lines == [1, 2, 3]


# --------------------------------------------------------------------------- #
# The comment map itself stays backwards compatible.
# --------------------------------------------------------------------------- #


def test_comment_map_records_the_column_of_every_comment() -> None:
    comments = _map("# header\nx = 1  # trailing\n")
    assert comments.column_at(1) == 0
    assert comments.column_at(2) == 7
    assert comments.column_at(3) is None


def test_comment_map_separates_own_line_comments_from_trailing_ones() -> None:
    comments = _map("# header\nx = 1  # trailing\ndef f() -> None:\n    # indented\n    ...\n")
    assert comments.is_standalone(1) is True
    assert comments.is_standalone(2) is False
    assert comments.is_standalone(4) is True
    assert comments.is_standalone(5) is False


def test_new_fields_default_to_empty_for_a_hand_built_map() -> None:
    comments = CommentMap(
        path=SOURCE_PATH, by_line={1: "# hi"}, ignores={}, skip_file=False
    )
    assert comments.column_at(1) is None
    assert comments.is_standalone(1) is False
    assert comments.text_at(1) == "# hi"
