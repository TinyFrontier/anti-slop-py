"""``FindingFingerprint``: what a change may do to a finding without renaming it.

The unit tests below pin the recipe; the end-to-end ones drive a real baseline through
the CLI, because "the baseline still matches" is the only statement about a
fingerprint anybody actually cares about.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from anti_slop.__main__ import main
from anti_slop.engine.fingerprint import (
    FINGERPRINT_LENGTH,
    SourceLines,
    fingerprint,
    normalize_line,
)

RULE_ID = "no-object-parameters"
BASELINE = ".anti-slop-baseline.json"

TWO_IDENTICAL_VIOLATIONS = """
    class First:
        def handle(self, value: object) -> None:
            ...


    class Second:
        def handle(self, value: object) -> None:
            ...
"""

NESTED_VIOLATION = """
    def outer_a() -> None:
        def inner(value: object) -> None:
            ...


    def outer_b() -> None:
        return None
"""

MOVED_NESTED_VIOLATION = """
    def outer_a() -> None:
        return None


    def outer_b() -> None:
        def inner(value: object) -> None:
            ...
"""


def make_project(tmp_path: Path, module_body: str, name: str = "mod.py") -> Path:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    module = tmp_path / name
    module.write_text(textwrap.dedent(module_body).lstrip("\n"), encoding="utf-8")
    return module


def generate(tmp_path: Path) -> int:
    """Record every current finding of ``tmp_path`` in its default baseline file."""
    return main(
        [
            str(tmp_path),
            "--config",
            str(tmp_path / "pyproject.toml"),
            "--generate-baseline",
        ]
    )


def check(tmp_path: Path) -> int:
    """Re-run ``tmp_path`` against the baseline written by :func:`generate`."""
    return main(
        [
            str(tmp_path),
            "--config",
            str(tmp_path / "pyproject.toml"),
            "--baseline",
            str(tmp_path / BASELINE),
        ]
    )


# --------------------------------------------------------------------------- #
# the recipe itself
# --------------------------------------------------------------------------- #


def test_fingerprint_is_sixteen_lowercase_hex_digits() -> None:
    value = fingerprint(
        rule_id=RULE_ID, relative_path="mod.py", line_text="def save(value: object):"
    )
    assert len(value) == FINGERPRINT_LENGTH == 16
    assert set(value) <= set("0123456789abcdef")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  def save(value: object):  ", "def save(value: object):"),
        ("def save(value:  object):", "def save(value: object):"),
        ("def\tsave(value:\t\tobject):", "def save(value: object):"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_line_strips_and_collapses_whitespace_runs(
    text: str, expected: str
) -> None:
    assert normalize_line(text) == expected


def test_extra_spaces_inside_the_line_do_not_change_the_fingerprint() -> None:
    single = fingerprint(
        rule_id=RULE_ID, relative_path="mod.py", line_text="def save(value: object):"
    )
    doubled = fingerprint(
        rule_id=RULE_ID, relative_path="mod.py", line_text="def  save(value:  object):"
    )
    assert single == doubled


def test_indentation_does_not_change_the_fingerprint() -> None:
    top_level = fingerprint(
        rule_id=RULE_ID, relative_path="mod.py", line_text="def save(value: object):"
    )
    indented = fingerprint(
        rule_id=RULE_ID,
        relative_path="mod.py",
        line_text="            def save(value: object):",
    )
    assert top_level == indented


def test_the_path_and_the_rule_are_part_of_the_identity() -> None:
    base = fingerprint(
        rule_id=RULE_ID, relative_path="mod.py", line_text="def save(value: object):"
    )
    other_path = fingerprint(
        rule_id=RULE_ID,
        relative_path="pkg/mod.py",
        line_text="def save(value: object):",
    )
    other_rule = fingerprint(
        rule_id="no-any-parameters",
        relative_path="mod.py",
        line_text="def save(value: object):",
    )
    assert base != other_path
    assert base != other_rule


def test_source_lines_reads_a_file_once_and_answers_out_of_range_with_empty(
    tmp_path: Path,
) -> None:
    module = tmp_path / "mod.py"
    module.write_text("first\nsecond\n", encoding="utf-8")
    sources = SourceLines()

    assert sources.line(module, 1) == "first"
    assert sources.line(module, 2) == "second"
    assert sources.line(module, 3) == ""
    assert sources.line(module, 0) == ""

    module.unlink()
    assert sources.line(module, 1) == "first"  # answered from the first read
    assert SourceLines().line(module, 1) == ""  # a file that is gone is not an error


# --------------------------------------------------------------------------- #
# what a baseline survives
# --------------------------------------------------------------------------- #


def test_shifting_a_block_down_keeps_the_baseline_matching(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = make_project(tmp_path, TWO_IDENTICAL_VIOLATIONS)
    assert generate(tmp_path) == 0
    capsys.readouterr()

    shifted = f"import os\nimport sys\n\n\n{module.read_text(encoding='utf-8')}"
    module.write_text(shifted, encoding="utf-8")

    assert check(tmp_path) == 0
    assert "2 baselined findings hidden (0 stale entries)" in capsys.readouterr().out


def test_an_identical_violation_inserted_above_reports_exactly_one_new_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The regression an ordinal-numbered baseline cannot pass.

    Numbering findings ("the second hit in this file") would renumber both existing
    hits when a third identical one is inserted above them, and report all three as
    new. Counting fingerprints reports one.
    """
    module = make_project(tmp_path, TWO_IDENTICAL_VIOLATIONS)
    assert generate(tmp_path) == 0
    capsys.readouterr()

    inserted = textwrap.dedent("""
        class Zeroth:
            def handle(self, value: object) -> None:
                ...


    """).lstrip("\n")
    module.write_text(
        inserted + module.read_text(encoding="utf-8"), encoding="utf-8"
    )

    assert check(tmp_path) == 1
    captured = capsys.readouterr()
    findings = [line for line in captured.out.splitlines() if RULE_ID in line]
    assert len(findings) == 1
    # Three findings now share one fingerprint, so which of the three physical lines
    # is the reported one is arbitrary -- only "one more than recorded" is meaningful.
    assert any(f":{line}:" in findings[0] for line in (2, 7, 12))
    assert "2 baselined findings hidden (0 stale entries)" in captured.out


def test_moving_a_violation_into_another_function_keeps_the_baseline_matching(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = make_project(tmp_path, NESTED_VIOLATION)
    assert generate(tmp_path) == 0
    capsys.readouterr()

    module.write_text(
        textwrap.dedent(MOVED_NESTED_VIOLATION).lstrip("\n"), encoding="utf-8"
    )

    assert check(tmp_path) == 0
    assert "1 baselined findings hidden (0 stale entries)" in capsys.readouterr().out


def test_renaming_the_file_invalidates_the_baseline_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A documented limitation, pinned so it stays a decision rather than a surprise."""
    module = make_project(tmp_path, NESTED_VIOLATION)
    assert generate(tmp_path) == 0
    capsys.readouterr()

    module.rename(tmp_path / "renamed.py")

    assert check(tmp_path) == 1
    captured = capsys.readouterr()
    assert "renamed.py" in captured.out
    assert "0 baselined findings hidden (1 stale entries)" in captured.out


def test_reformatting_the_anchored_line_invalidates_the_baseline_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other documented limitation: the anchored line's text *is* the identity."""
    module = make_project(tmp_path, NESTED_VIOLATION)
    assert generate(tmp_path) == 0
    capsys.readouterr()

    module.write_text(
        module.read_text(encoding="utf-8").replace(
            "def inner(value: object) -> None:", "def renamed(value: object) -> None:"
        ),
        encoding="utf-8",
    )

    assert check(tmp_path) == 1
    assert "0 baselined findings hidden (1 stale entries)" in capsys.readouterr().out


def test_padding_the_anchored_line_with_spaces_keeps_the_baseline_matching(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = make_project(tmp_path, NESTED_VIOLATION)
    assert generate(tmp_path) == 0
    capsys.readouterr()

    module.write_text(
        module.read_text(encoding="utf-8").replace(
            "def inner(value: object) -> None:", "def  inner(value:  object) -> None:"
        ),
        encoding="utf-8",
    )

    assert check(tmp_path) == 0
    assert "1 baselined findings hidden (0 stale entries)" in capsys.readouterr().out
