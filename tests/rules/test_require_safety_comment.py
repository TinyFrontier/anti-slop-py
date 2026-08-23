"""Tests for the ``require-safety-comment`` rule (PLAN.md section 3.4, row 15).

Every snippet lives inside a string literal, so the directives written here are
comments of the *analysed* snippet, never comments of this test file: ``tokenize``
sees a STRING token, not a COMMENT one. That is what keeps this file clean under
anti-slop's own self-lint.
"""

from __future__ import annotations

import pytest
from harness import assert_invalid, assert_valid, run_rule

from anti_slop.rules.require_safety_comment import RULE

VALID_SNIPPETS = [
    # The invariant stated directly above the cast.
    """
    # SAFETY: the row is selected by primary key, so the column is never NULL
    user = cast(User, row["user"])
    """,
    # The invariant stated at the end of the cast's own line.
    """
    user = cast(User, payload)  # SAFETY: the schema was validated by the decoder
    """,
    # A multi-line expression: the comment sits above the owning statement, several
    # lines above the cast node itself (the `commentOwnerKinds` port).
    """
    # SAFETY: rows come from our own primary key column
    users = [
        cast(User, row)
        for row in rows
    ]
    """,
    # An invariant that needs more than one line is still one comment block.
    """
    # SAFETY: the walker looks the handler up by the runtime type of the node it is
    # about to dispatch, so this is only ever called with an instance of that type.
    erased = cast(Handler, handler)
    """,
    # A coded suppression with the invariant on the same line.
    """
    total = registry.lookup(key)  # ty: ignore[unresolved-attribute]  # SAFETY: the loader fills the registry at import
    """,
    # A coded suppression with the invariant on the line above.
    """
    # SAFETY: the C extension is present in every supported build
    handle = library.open(path)  # pyright: ignore[reportAttributeAccessIssue]
    """,
    # The block climb applies to suppressions too.
    """
    # SAFETY: the loader registers every backend before the first lookup, so the
    # attribute exists by the time this line runs.
    total = registry.lookup(key)  # ty: ignore[unresolved-attribute]
    """,
    # The combined dialect: one directive, codes for two checkers.
    """
    # SAFETY: the parser rejects an empty body before this line runs
    body = message.body  # type: ignore[union-attr, ty:possibly-unbound]
    """,
    # anti-slop's own suppression is not a checker suppression.
    """
    def save(value: object) -> None:  # anti-slop: ignore[no-object-parameters]
        ...
    """,
    # A file with neither a cast nor a suppression.
    """
    def save(value: Payload) -> None:
        repository.store(value)
    """,
    # `SAFETY:` in prose is not a directive, and neither is a docstring mention.
    '''
    def save(value: Payload) -> None:
        """Store the value. The SAFETY: marker only counts in comments."""
        repository.store(value)
    ''',
    # An unrelated method literally named `cast` still needs the comment, but a
    # covered one is fine -- this documents the known syntactic limitation.
    """
    # SAFETY: the column type is fixed by the migration
    column = sqlalchemy.cast(table.c.amount, Numeric)
    """,
]

INVALID_SNIPPETS = [
    # The canonical bare cast.
    ("""
    user = cast(User, payload)
    """, 1),
    # An empty `# SAFETY:` states no invariant, so it is no safety comment.
    ("""
    # SAFETY:
    user = cast(User, payload)
    """, 1),
    # Two lines above is too far: the comment belongs to whatever is between.
    ("""
    # SAFETY: this describes the import, not the cast
    import models

    user = cast(User, payload)
    """, 1),
    # A trailing comment on the statement above ends the block: the invariant up
    # there belongs to the import, not to the cast.
    ("""
    # SAFETY: this one is about the import below it
    import models  # an ordinary trailing note
    user = cast(User, payload)
    """, 1),
    # A coded suppression without an invariant: one defect, the missing SAFETY.
    ("""
    body = message.body  # type: ignore[union-attr]
    """, 1),
    # Same for the ty dialect.
    ("""
    total = registry.lookup(key)  # ty: ignore[unresolved-attribute]
    """, 1),
    # Same for the pyright dialect.
    ("""
    handle = library.open(path)  # pyright: ignore[reportAttributeAccessIssue]
    """, 1),
    # A code-less type suppression with no invariant: two independent defects.
    ("""
    body = message.body  # type: ignore
    """, 2),
    # The same directive *with* an invariant: the missing code still counts.
    ("""
    # SAFETY: the parser rejects an empty body before this line runs
    body = message.body  # type: ignore
    """, 1),
    # Empty brackets are as blanket as no brackets.
    ("""
    # SAFETY: the parser rejects an empty body before this line runs
    body = message.body  # type: ignore[]
    """, 1),
    # A bare ty suppression with an invariant: the missing rule still counts.
    ("""
    # SAFETY: the loader fills the registry at import
    total = registry.lookup(key)  # ty: ignore
    """, 1),
    # A comment covers one statement, not a region: the second cast is uncovered.
    ("""
    # SAFETY: the row is selected by primary key
    user = cast(User, row)
    order = cast(Order, row)
    """, 1),
    # The comment sits above the nested `def`, not above the statement that casts.
    ("""
    def build(rows: list[Row]) -> list[User]:
        # SAFETY: rows come from our own table
        def convert(row: Row) -> User:
            return cast(User, row)

        return [convert(row) for row in rows]
    """, 1),
    # Async, decorated, and inside a comprehension -- still a cast.
    ("""
    @retry(attempts=3)
    async def load(keys: list[str]) -> list[User]:
        return [cast(User, await fetch(key)) for key in keys]
    """, 1),
    # The keyword spelling of `typing.cast`.
    ("""
    user = typing.cast(typ=User, val=payload)
    """, 1),
]


def test_valid_snippets() -> None:
    assert_valid(RULE, VALID_SNIPPETS)


@pytest.mark.parametrize(("snippet", "count"), INVALID_SNIPPETS)
def test_invalid_snippets(snippet: str, count: int) -> None:
    assert_invalid(RULE, snippet, count=count)


def test_cast_message_names_the_target_and_prescribes_a_fix() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        user = cast(User, payload)
        """,
    )
    assert "`User`" in diagnostic.message
    assert "# SAFETY:" in diagnostic.message
    assert "isinstance" in diagnostic.message
    assert diagnostic.line == 1
    assert diagnostic.col == 8


def test_quoted_cast_target_is_rendered_without_its_quotes() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        user = cast("User", payload)
        """,
    )
    assert "`User`" in diagnostic.message


def test_bare_type_ignore_message_recommends_the_ty_dialect() -> None:
    diagnostics = run_rule(
        RULE,
        """
        # SAFETY: the parser rejects an empty body before this line runs
        body = message.body  # type: ignore
        """,
    )
    (diagnostic,) = diagnostics
    assert "# ty: ignore[<rule>]" in diagnostic.message
    assert "ty ignores the codes inside" in diagnostic.message


def test_bare_dialect_suppression_message_names_the_checker() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        # SAFETY: the loader fills the registry at import
        total = registry.lookup(key)  # pyright: ignore
        """,
    )
    assert "# pyright: ignore[<rule>]" in diagnostic.message


def test_a_suppression_diagnostic_points_at_the_directive() -> None:
    (diagnostic,) = assert_invalid(
        RULE,
        """
        body = message.body  # noqa  # ty: ignore[unresolved-attribute]
        """,
    )
    assert diagnostic.line == 1
    # 1-based column of the `#` that opens the ty directive, not of the comment.
    assert diagnostic.col == 30
    assert diagnostic.end_col == 64


def test_both_defects_of_a_bare_uncommented_suppression_are_reported() -> None:
    missing_safety, missing_code = assert_invalid(
        RULE,
        """
        body = message.body  # type: ignore
        """,
        count=2,
    )
    assert "silences the checker without saying why" in missing_safety.message
    assert "names no error code" in missing_code.message


def test_rule_id_suppression_silences_the_cast_report() -> None:
    diagnostics = run_rule(
        RULE,
        """
        user = cast(User, payload)  # anti-slop: ignore[require-safety-comment]
        """,
    )
    assert diagnostics == ()


def test_rule_id_suppression_silences_a_checker_suppression_report() -> None:
    diagnostics = run_rule(
        RULE,
        """
        # anti-slop: ignore[require-safety-comment]
        body = message.body  # type: ignore[union-attr]
        """,
    )
    assert diagnostics == ()
