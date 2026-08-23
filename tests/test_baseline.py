"""Baseline mode: ``--generate-baseline``, ``--baseline``, and the run summary."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from anti_slop.__main__ import main
from anti_slop.engine.baseline import (
    BASELINE_VERSION,
    DEFAULT_BASELINE_NAME,
    Baseline,
    apply_baseline,
    render_baseline,
)
from anti_slop.engine.fingerprint import FINGERPRINT_KEY
from anti_slop.engine.rule import Diagnostic

RULE_ID = "no-object-parameters"

VIOLATION = """
    def save(value: object) -> None:
        ...
"""

CLEAN = """
    def save(value: Payload) -> None:
        ...
"""


def make_project(tmp_path: Path, module_body: str, config: str = "") -> tuple[Path, Path]:
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(config).lstrip("\n"), encoding="utf-8"
    )
    module = tmp_path / "mod.py"
    module.write_text(textwrap.dedent(module_body).lstrip("\n"), encoding="utf-8")
    return tmp_path / "pyproject.toml", module


def crowded_project(tmp_path: Path, count: int, config: str = "") -> Path:
    """A project holding ``count`` distinct violations across several modules."""
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(config).lstrip("\n"), encoding="utf-8"
    )
    package = tmp_path / "pkg"
    package.mkdir()
    for module_index in range(5):
        body = "".join(
            f"def save_{module_index}_{index}(value: object) -> None:\n    ...\n\n\n"
            for index in range(count // 5)
        )
        (package / f"mod_{module_index}.py").write_text(body, encoding="utf-8")
    return tmp_path / "pyproject.toml"


def run(*arguments: str) -> int:
    return main(list(arguments))


def _diagnostic(line: int) -> Diagnostic:
    return Diagnostic(
        path=Path("mod.py"),
        line=line,
        col=1,
        end_line=line,
        end_col=2,
        rule_id=RULE_ID,
        message="use a domain type instead",
    )


# --------------------------------------------------------------------------- #
# the file itself
# --------------------------------------------------------------------------- #


def test_generated_baseline_document_is_versioned_sorted_and_diffable(
    tmp_path: Path,
) -> None:
    config = crowded_project(tmp_path, 50)
    assert run(str(tmp_path), "--config", str(config), "--generate-baseline") == 0

    path = tmp_path / DEFAULT_BASELINE_NAME
    text = path.read_text(encoding="utf-8")
    document = json.loads(text)

    assert document["version"] == BASELINE_VERSION == 1
    assert sum(document["entries"].values()) == 50
    assert all(count >= 1 for count in document["entries"].values())
    # Diffable in a pull request: indented, one entry per line, keys sorted, and a
    # trailing newline so the last entry is not marked "\ No newline at end of file".
    keys = list(document["entries"])
    assert keys == sorted(keys)
    assert text.endswith("\n")
    # One line per entry, plus the five lines of the object it sits in.
    assert text.count("\n") == len(keys) + 5


def test_render_baseline_counts_repeated_fingerprints() -> None:
    document = json.loads(render_baseline(("bbbb", "aaaa", "bbbb")))
    assert document["entries"] == {"aaaa": 1, "bbbb": 2}


def test_generate_prints_the_written_path_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _ = make_project(tmp_path, VIOLATION)
    code = run(str(tmp_path), "--config", str(config), "--generate-baseline")
    captured = capsys.readouterr()

    assert code == 0
    assert str(tmp_path / DEFAULT_BASELINE_NAME) in captured.out
    assert "1 findings across 1 fingerprints" in captured.out
    assert captured.err == ""


def test_generate_writes_the_path_named_by_the_baseline_flag(tmp_path: Path) -> None:
    config, _ = make_project(tmp_path, VIOLATION)
    target = tmp_path / "quality" / "accepted.json"
    code = run(
        str(tmp_path),
        "--config",
        str(config),
        "--generate-baseline",
        "--baseline",
        str(target),
    )

    assert code == 0
    assert json.loads(target.read_text(encoding="utf-8"))["version"] == BASELINE_VERSION


def test_generate_refuses_to_record_a_project_that_does_not_parse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, VIOLATION)
    module.write_text("def broken(\n", encoding="utf-8")

    code = run(str(tmp_path), "--config", str(config), "--generate-baseline")
    captured = capsys.readouterr()

    assert code == 2
    assert "syntax-error" in captured.err
    assert not (tmp_path / DEFAULT_BASELINE_NAME).exists()


# --------------------------------------------------------------------------- #
# the end-to-end adoption scenario
# --------------------------------------------------------------------------- #


def test_generate_then_clean_then_exactly_one_new_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = crowded_project(tmp_path, 50)
    baseline = tmp_path / DEFAULT_BASELINE_NAME

    assert run(str(tmp_path), "--config", str(config), "--generate-baseline") == 0
    capsys.readouterr()

    assert run(str(tmp_path), "--config", str(config), "--baseline", str(baseline)) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "50 baselined findings hidden (0 stale entries)"

    newcomer = tmp_path / "pkg" / "mod_5.py"
    newcomer.write_text(
        "def newly_written(value: object) -> None:\n    ...\n", encoding="utf-8"
    )
    assert run(str(tmp_path), "--config", str(config), "--baseline", str(baseline)) == 1
    captured = capsys.readouterr()
    findings = [line for line in captured.out.splitlines() if RULE_ID in line]
    assert len(findings) == 1
    assert findings[0].startswith(f"{newcomer}:1:")
    assert "50 baselined findings hidden (0 stale entries)" in captured.out


def test_a_finding_beyond_the_recorded_count_is_reported() -> None:
    baseline = Baseline(entries={"aaaa": 2})
    outcome = apply_baseline(
        baseline,
        (_diagnostic(1), _diagnostic(2), _diagnostic(3)),
        ("aaaa", "aaaa", "aaaa"),
    )

    assert outcome.hidden == 2
    assert outcome.stale == 0
    assert [diagnostic.line for diagnostic in outcome.diagnostics] == [3]


def test_stale_entries_are_counted_and_are_never_an_error() -> None:
    outcome = apply_baseline(
        Baseline(entries={"aaaa": 3, "bbbb": 1}), (_diagnostic(1),), ("aaaa",)
    )

    assert outcome.diagnostics == ()
    assert (outcome.hidden, outcome.stale) == (1, 2)
    assert outcome.summary() == "1 baselined findings hidden (2 stale entries)"


def test_a_partial_run_does_not_call_the_entries_it_never_looked_at_stale() -> None:
    outcome = apply_baseline(
        Baseline(entries={"aaaa": 1, "bbbb": 4}),
        (_diagnostic(1),),
        ("aaaa",),
        whole_project=False,
    )

    assert (outcome.hidden, outcome.stale) == (1, 0)


def test_no_summary_when_nothing_was_hidden_and_nothing_is_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _ = make_project(tmp_path, CLEAN)
    (tmp_path / DEFAULT_BASELINE_NAME).write_text(
        json.dumps({"version": 1, "entries": {}}), encoding="utf-8"
    )

    code = run(
        str(tmp_path),
        "--config",
        str(config),
        "--baseline",
        str(tmp_path / DEFAULT_BASELINE_NAME),
    )
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out == ""


def test_warn_findings_are_baselined_like_error_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _ = make_project(
        tmp_path,
        VIOLATION,
        f"""
        [tool.anti-slop.rules]
        "{RULE_ID}" = "warn"
        """,
    )
    baseline = tmp_path / DEFAULT_BASELINE_NAME

    assert run(str(tmp_path), "--config", str(config), "--generate-baseline") == 0
    capsys.readouterr()

    assert run(str(tmp_path), "--config", str(config), "--baseline", str(baseline)) == 0
    captured = capsys.readouterr()
    assert "warning:" not in captured.out
    assert captured.out.strip() == "1 baselined findings hidden (0 stale entries)"


# --------------------------------------------------------------------------- #
# where the summary may and may not appear
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("output_format", ["json", "sarif", "github"])
def test_the_summary_never_pollutes_a_machine_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], output_format: str
) -> None:
    config, _ = make_project(tmp_path, VIOLATION)
    baseline = tmp_path / DEFAULT_BASELINE_NAME
    assert run(str(tmp_path), "--config", str(config), "--generate-baseline") == 0
    capsys.readouterr()

    code = run(
        str(tmp_path),
        "--config",
        str(config),
        "--baseline",
        str(baseline),
        "--format",
        output_format,
    )
    captured = capsys.readouterr()

    assert code == 0
    assert "baselined findings hidden" not in captured.out
    if output_format == "github":
        assert captured.out == ""
    else:
        json.loads(captured.out)  # still exactly one document, and nothing else


def test_sarif_partial_fingerprint_is_the_key_the_baseline_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _ = make_project(tmp_path, VIOLATION)
    assert run(str(tmp_path), "--config", str(config), "--generate-baseline") == 0
    capsys.readouterr()
    recorded = json.loads(
        (tmp_path / DEFAULT_BASELINE_NAME).read_text(encoding="utf-8")
    )["entries"]

    assert run(str(tmp_path), "--config", str(config), "--format", "sarif") == 1
    document = json.loads(capsys.readouterr().out)

    (result,) = document["runs"][0]["results"]
    assert set(result["partialFingerprints"]) == {FINGERPRINT_KEY}
    assert result["partialFingerprints"][FINGERPRINT_KEY] in recorded


# --------------------------------------------------------------------------- #
# where the baseline path comes from
# --------------------------------------------------------------------------- #


def test_the_configured_baseline_applies_without_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _ = make_project(
        tmp_path,
        VIOLATION,
        """
        [tool.anti-slop]
        baseline = "quality/accepted.json"
        """,
    )

    assert run(str(tmp_path), "--config", str(config), "--generate-baseline") == 0
    capsys.readouterr()
    assert (tmp_path / "quality" / "accepted.json").is_file()

    assert run(str(tmp_path), "--config", str(config)) == 0
    assert "1 baselined findings hidden" in capsys.readouterr().out


def test_the_flag_beats_the_configured_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _ = make_project(
        tmp_path,
        VIOLATION,
        """
        [tool.anti-slop]
        baseline = "configured.json"
        """,
    )
    (tmp_path / "configured.json").write_text(
        json.dumps({"version": 1, "entries": {}}), encoding="utf-8"
    )
    named = tmp_path / "named.json"

    # Generating writes where the flag points, leaving the configured file alone.
    assert run(
        str(tmp_path),
        "--config",
        str(config),
        "--generate-baseline",
        "--baseline",
        str(named),
    ) == 0
    capsys.readouterr()
    assert json.loads((tmp_path / "configured.json").read_text(encoding="utf-8"))[
        "entries"
    ] == {}

    # And checking reads it: the configured (empty) baseline would have failed the run.
    assert run(str(tmp_path), "--config", str(config), "--baseline", str(named)) == 0
    assert "1 baselined findings hidden" in capsys.readouterr().out


def test_a_baseline_file_nobody_asked_for_is_not_applied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stray default-named file must never silently silence a run."""
    config, _ = make_project(tmp_path, VIOLATION)
    assert run(str(tmp_path), "--config", str(config), "--generate-baseline") == 0
    capsys.readouterr()

    assert (tmp_path / DEFAULT_BASELINE_NAME).is_file()
    assert run(str(tmp_path), "--config", str(config)) == 1
    captured = capsys.readouterr()
    assert RULE_ID in captured.out
    assert "baselined findings hidden" not in captured.out


# --------------------------------------------------------------------------- #
# a baseline that cannot be trusted is an error, never a shrug
# --------------------------------------------------------------------------- #


def test_a_missing_baseline_file_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _ = make_project(tmp_path, VIOLATION)
    code = run(
        str(tmp_path), "--config", str(config), "--baseline", str(tmp_path / "no.json")
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "baseline file not found" in captured.err
    assert "--generate-baseline" in captured.err


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("not json at all", "invalid JSON"),
        ("[]", "must be a JSON object"),
        ('{"version": 2, "entries": {}}', "is not supported"),
        ('{"entries": {}}', "is not supported"),
        ('{"version": 1}', "'entries' must be an object"),
        ('{"version": 1, "entries": []}', "'entries' must be an object"),
        ('{"version": 1, "entries": {"short": 1}}', "is not a fingerprint"),
        ('{"version": 1, "entries": {"00112233445566GG": 1}}', "is not a fingerprint"),
        ('{"version": 1, "entries": {"0011223344556677": 0}}', "positive integer"),
        ('{"version": 1, "entries": {"0011223344556677": true}}', "positive integer"),
        ('{"version": 1, "entries": {"0011223344556677": "3"}}', "positive integer"),
        ('{"version": 1, "entries": {}, "extra": 1}', "unknown key"),
    ],
)
def test_a_malformed_baseline_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str, expected: str
) -> None:
    config, _ = make_project(tmp_path, VIOLATION)
    baseline = tmp_path / DEFAULT_BASELINE_NAME
    baseline.write_text(content, encoding="utf-8")

    code = run(str(tmp_path), "--config", str(config), "--baseline", str(baseline))
    captured = capsys.readouterr()

    assert code == 2
    assert expected in captured.err


def test_a_non_string_baseline_key_in_the_configuration_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _ = make_project(
        tmp_path,
        VIOLATION,
        """
        [tool.anti-slop]
        baseline = 3
        """,
    )
    code = run(str(tmp_path), "--config", str(config))
    captured = capsys.readouterr()

    assert code == 2
    assert "baseline must be a string" in captured.err


def test_generate_baseline_refuses_to_record_only_a_diff(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A baseline of the changed lines alone would silence the wrong findings."""
    config, _ = make_project(tmp_path, VIOLATION)

    code = run(
        str(tmp_path), "--config", str(config), "--generate-baseline", "--diff", "main"
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "cannot be combined with --diff" in captured.err
    assert not (tmp_path / DEFAULT_BASELINE_NAME).exists()
