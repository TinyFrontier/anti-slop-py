"""End-to-end CLI tests: CLI -> config -> diagnostic -> suppression (PLAN.md FR-1)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from anti_slop.__main__ import main

RULE_ID = "no-object-parameters"

VIOLATION = """
    def save(value: object) -> None:
        ...
"""

CLEAN = """
    def save(value: Payload) -> None:
        ...
"""

SUPPRESSED = """
    def save(value: object) -> None:  # anti-slop: ignore[no-object-parameters]
        ...
"""


def make_project(tmp_path: Path, module_body: str, config: str = "") -> tuple[Path, Path]:
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(config).lstrip("\n"), encoding="utf-8"
    )
    module = tmp_path / "mod.py"
    module.write_text(textwrap.dedent(module_body).lstrip("\n"), encoding="utf-8")
    return tmp_path / "pyproject.toml", module


def test_violation_exits_one_and_prints_the_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, VIOLATION)
    code = main([str(module), "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 1
    assert f"{module}:1:10 {RULE_ID}" in captured.out
    assert "domain type" in captured.out
    assert captured.err == ""


def test_clean_file_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, CLEAN)
    code = main([str(module), "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out == ""


def test_suppressed_violation_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, SUPPRESSED)
    code = main([str(module), "--config", str(config)])
    capsys.readouterr()
    assert code == 0


def test_rule_turned_off_in_config_exits_zero(tmp_path: Path) -> None:
    config, module = make_project(
        tmp_path,
        VIOLATION,
        f"""
        [tool.anti-slop.rules]
        "{RULE_ID}" = "off"
        """,
    )
    assert main([str(module), "--config", str(config)]) == 0


def test_allow_object_option_from_config_exits_zero(tmp_path: Path) -> None:
    config, module = make_project(
        tmp_path,
        VIOLATION,
        f"""
        [tool.anti-slop.rules]
        "{RULE_ID}" = {{ allow-object = true }}
        """,
    )
    assert main([str(module), "--config", str(config)]) == 0


def test_directory_walk_uses_include_when_no_paths_are_given(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "mod.py").write_text(
        textwrap.dedent(VIOLATION).lstrip("\n"), encoding="utf-8"
    )
    config = tmp_path / "pyproject.toml"
    config.write_text(
        textwrap.dedent("""
            [tool.anti-slop]
            include = ["src"]
        """).lstrip("\n"),
        encoding="utf-8",
    )
    assert main(["--config", str(config)]) == 1


def test_excluded_directory_is_skipped(tmp_path: Path) -> None:
    vendored = tmp_path / "tools" / "anti_slop"
    vendored.mkdir(parents=True)
    (vendored / "mod.py").write_text(
        textwrap.dedent(VIOLATION).lstrip("\n"), encoding="utf-8"
    )
    config = tmp_path / "pyproject.toml"
    config.write_text(
        textwrap.dedent("""
            [tool.anti-slop]
            include = ["tools"]
            exclude = ["tools/anti_slop/**"]
        """).lstrip("\n"),
        encoding="utf-8",
    )
    assert main(["--config", str(config)]) == 0


def test_list_rules(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--list-rules"])
    captured = capsys.readouterr()

    assert code == 0
    assert RULE_ID in captured.out
    assert "allow-object (default: false)" in captured.out


def test_rule_filter_selects_a_single_rule(tmp_path: Path) -> None:
    config, module = make_project(tmp_path, VIOLATION)
    assert main([str(module), "--config", str(config), "--rule", RULE_ID]) == 1


def test_unknown_rule_filter_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, VIOLATION)
    code = main([str(module), "--config", str(config), "--rule", "no-such-rule"])
    captured = capsys.readouterr()

    assert code == 2
    assert "unknown rule(s) passed to --rule" in captured.err


def test_missing_config_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([str(tmp_path), "--config", str(tmp_path / "nope.toml")])
    captured = capsys.readouterr()

    assert code == 2
    assert "config file not found" in captured.err


def test_missing_path_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, _ = make_project(tmp_path, CLEAN)
    code = main([str(tmp_path / "ghost.py"), "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 2
    assert "path does not exist" in captured.err


def test_unparseable_file_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, "def save(\n")
    code = main([str(module), "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 2
    assert "syntax-error" in captured.err


def test_malformed_suppression_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(
        tmp_path, "def save(value: object) -> None:  # anti-slop: ignore\n    ...\n"
    )
    code = main([str(module), "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 2
    assert "must name the rule" in captured.err


def test_non_positive_jobs_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, CLEAN)
    code = main([str(module), "--config", str(config), "--jobs", "0"])
    captured = capsys.readouterr()

    assert code == 2
    assert "--jobs must be a positive integer" in captured.err


def test_unsupported_format_exits_two(tmp_path: Path) -> None:
    config, module = make_project(tmp_path, CLEAN)
    with pytest.raises(SystemExit) as excinfo:
        main([str(module), "--config", str(config), "--format", "xml"])
    assert excinfo.value.code == 2


def test_default_excludes_apply_outside_the_config_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Linting another repository must still skip its `.venv` (phase-5 field bug)."""
    config, _ = make_project(tmp_path, CLEAN)

    other_repo = tmp_path / "elsewhere" / "target-repo"
    site = other_repo / ".venv" / "lib" / "site-packages"
    site.mkdir(parents=True)
    (site / "vendored.py").write_text(
        textwrap.dedent(VIOLATION).lstrip("\n"), encoding="utf-8"
    )
    owned = other_repo / "app.py"
    owned.write_text(textwrap.dedent(VIOLATION).lstrip("\n"), encoding="utf-8")

    code = main([str(other_repo), "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 1
    assert "app.py" in captured.out
    assert ".venv" not in captured.out


def test_linted_file_syntax_warnings_stay_off_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A target file's own SyntaxWarning (bad escape) is not this tool's output."""
    config, module = make_project(tmp_path, CLEAN)
    module.write_text('PATTERN = "\\d+"\n', encoding="utf-8")

    code = main([str(module), "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
