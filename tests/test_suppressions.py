"""Tests for line and file suppressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from harness import run_rule

from anti_slop.engine.comments import build_comment_map
from anti_slop.engine.config import ConfigError
from anti_slop.rules.no_object_parameters import RULE

SOURCE_PATH = Path("snippet.py")


def test_ignore_on_the_violating_line() -> None:
    diagnostics = run_rule(
        RULE,
        """
        def save(value: object) -> None:  # anti-slop: ignore[no-object-parameters]
            ...
        """,
    )
    assert diagnostics == ()


def test_ignore_on_the_line_above() -> None:
    diagnostics = run_rule(
        RULE,
        """
        # anti-slop: ignore[no-object-parameters]
        def save(value: object) -> None: ...
        """,
    )
    assert diagnostics == ()


def test_ignore_two_lines_above_does_not_suppress() -> None:
    diagnostics = run_rule(
        RULE,
        """
        # anti-slop: ignore[no-object-parameters]

        def save(value: object) -> None: ...
        """,
    )
    assert len(diagnostics) == 1


def test_ignore_of_a_different_rule_does_not_suppress() -> None:
    diagnostics = run_rule(
        RULE,
        """
        def save(value: object) -> None:  # anti-slop: ignore[no-any-parameters]
            ...
        """,
    )
    assert len(diagnostics) == 1


def test_ignore_accepts_several_rule_ids() -> None:
    diagnostics = run_rule(
        RULE,
        """
        def save(value: object) -> None:  # anti-slop: ignore[no-any-parameters, no-object-parameters]
            ...
        """,
    )
    assert diagnostics == ()


def test_ignore_allows_a_trailing_rationale() -> None:
    diagnostics = run_rule(
        RULE,
        """
        def save(value: object) -> None:  # anti-slop: ignore[no-object-parameters] third-party callback signature
            ...
        """,
    )
    assert diagnostics == ()


def test_skip_file_within_the_first_five_lines() -> None:
    diagnostics = run_rule(
        RULE,
        """
        # anti-slop: skip-file
        def save(value: object) -> None: ...
        def load(value: object) -> None: ...
        """,
    )
    assert diagnostics == ()


def test_skip_file_beyond_the_first_five_lines_is_an_error() -> None:
    source = "\n".join(
        ["x = 1"] * 6 + ["# anti-slop: skip-file", "def save(value: object): ..."]
    )
    with pytest.raises(ConfigError, match="first 5 lines"):
        build_comment_map(SOURCE_PATH, source)


def test_ignore_without_a_rule_id_is_an_error() -> None:
    with pytest.raises(ConfigError, match="must name the rule"):
        build_comment_map(SOURCE_PATH, "def save(value: object): ...  # anti-slop: ignore\n")


def test_ignore_with_empty_brackets_is_an_error() -> None:
    with pytest.raises(ConfigError, match="suppresses nothing"):
        build_comment_map(SOURCE_PATH, "x = 1  # anti-slop: ignore[]\n")


def test_unknown_directive_is_an_error() -> None:
    with pytest.raises(ConfigError, match="unknown anti-slop directive"):
        build_comment_map(SOURCE_PATH, "x = 1  # anti-slop: disable-all\n")


def test_malformed_rule_id_is_an_error() -> None:
    with pytest.raises(ConfigError, match="not a valid rule id"):
        build_comment_map(SOURCE_PATH, "x = 1  # anti-slop: ignore[NoObjectParameters]\n")


def test_comment_map_indexes_every_comment_by_line() -> None:
    comments = build_comment_map(SOURCE_PATH, "# header\nx = 1  # trailing\n")
    assert comments.text_at(1) == "# header"
    assert comments.text_at(2) == "# trailing"
    assert comments.text_at(3) is None
    assert comments.skip_file is False
