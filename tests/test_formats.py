"""Machine output formats, parallel walk, and the pre-commit hook manifest.

``--format json`` / ``--format github`` (FR-4), a ``ProcessPoolExecutor`` file walk
above the parallel threshold, and ``.pre-commit-hooks.yaml``.
"""

from __future__ import annotations

import json
import textwrap
import tomllib
from pathlib import Path

import pytest

from anti_slop.__main__ import main
from anti_slop.engine import runner
from anti_slop.engine.config import RuleSetting
from anti_slop.engine.rule import Diagnostic, Rule
from anti_slop.rules.no_any_parameters import RULE as NO_ANY_PARAMETERS
from anti_slop.rules.no_object_parameters import RULE as NO_OBJECT_PARAMETERS

REPO_ROOT = Path(__file__).resolve().parent.parent

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


# --------------------------------------------------------------------------- #
# --format json (FR-4)
# --------------------------------------------------------------------------- #


def test_json_format_is_empty_array_on_a_clean_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, CLEAN)
    code = main([str(module), "--config", str(config), "--format", "json"])
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.strip() == "[]"
    assert json.loads(captured.out) == []


def test_json_format_reports_the_exact_fr4_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, VIOLATION)
    code = main([str(module), "--config", str(config), "--format", "json"])
    captured = capsys.readouterr()

    assert code == 1
    payload = json.loads(captured.out)
    assert isinstance(payload, list)
    assert len(payload) == 1
    (entry,) = payload
    assert set(entry) == {
        "path", "line", "col", "endLine", "endCol", "rule", "severity", "message",
    }
    assert entry["path"] == str(module)
    assert entry["line"] == 1
    assert entry["col"] == 10
    assert entry["rule"] == "no-object-parameters"
    assert entry["severity"] == "error"
    assert "domain type" in entry["message"]
    # A single JSON document on stdout: exactly one line, no trailing chatter.
    assert captured.out.count("\n") == 1


def test_json_format_is_sorted_by_path_then_line_then_col(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "b_mod.py").write_text(
        "def save(value: object) -> None:\n"
        "    ...\n"
        "\n"
        "\n"
        "def save2(value: object) -> None:\n"
        "    ...\n",
        encoding="utf-8",
    )
    (source_dir / "a_mod.py").write_text(
        "def save(value: object) -> None:\n    ...\n", encoding="utf-8"
    )
    # `collect_files` sorts by `str(path)`; build the same ordering by hand so this
    # test does not need a full `Config` just to exercise `format_json`'s sorting.
    files = tuple(sorted(source_dir.glob("*.py"), key=str))
    settings = {
        NO_OBJECT_PARAMETERS.id: RuleSetting(
            rule_id=NO_OBJECT_PARAMETERS.id,
            enabled=True,
            options=NO_OBJECT_PARAMETERS.default_options(),
        )
    }

    outcome = runner.run(files, (NO_OBJECT_PARAMETERS,), settings)
    payload = json.loads(runner.format_json(outcome.diagnostics))

    assert len(payload) == 3  # a_mod: 1 violation, b_mod: 2 violations
    paths = [entry["path"] for entry in payload]
    assert paths == sorted(paths)
    # Within the same file, entries climb by line then column.
    b_mod_entries = [entry for entry in payload if entry["path"].endswith("b_mod.py")]
    assert [entry["line"] for entry in b_mod_entries] == sorted(
        entry["line"] for entry in b_mod_entries
    )


# --------------------------------------------------------------------------- #
# severity: warn vs error, across every format
# --------------------------------------------------------------------------- #


def _diagnostic(severity: str) -> Diagnostic:
    return Diagnostic(
        path=Path("mod.py"),
        line=1,
        col=10,
        end_line=1,
        end_col=15,
        rule_id="no-object-parameters",
        message="use a domain type instead",
        severity=severity,
    )


def test_format_text_error_diagnostic_has_no_marker() -> None:
    rendered = runner.format_text((_diagnostic("error"),))
    assert rendered == "mod.py:1:10 no-object-parameters use a domain type instead"


def test_format_text_warn_diagnostic_gets_warning_marker() -> None:
    rendered = runner.format_text((_diagnostic("warn"),))
    assert rendered == (
        "mod.py:1:10 no-object-parameters warning: use a domain type instead"
    )


def test_json_format_carries_severity_for_both_levels() -> None:
    payload = json.loads(runner.format_json((_diagnostic("error"), _diagnostic("warn"))))
    assert [entry["severity"] for entry in payload] == ["error", "warn"]


def test_github_format_uses_warning_command_for_warn_severity() -> None:
    rendered = runner.format_github((_diagnostic("warn"),))
    assert rendered.startswith("::warning file=")


def test_github_format_uses_error_command_for_error_severity() -> None:
    rendered = runner.format_github((_diagnostic("error"),))
    assert rendered.startswith("::error file=")


# --------------------------------------------------------------------------- #
# --format github: GitHub Actions ``::error`` workflow-command annotations
# --------------------------------------------------------------------------- #


def test_github_format_prints_nothing_on_a_clean_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, CLEAN)
    code = main([str(module), "--config", str(config), "--format", "github"])
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out == ""


def test_github_format_annotation_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, VIOLATION)
    code = main([str(module), "--config", str(config), "--format", "github"])
    captured = capsys.readouterr()

    assert code == 1
    line = captured.out.strip()
    assert line.startswith("::error file=")
    assert f"file={module}" in line
    assert ",line=1,col=10,endLine=1,endColumn=" in line
    assert ",title=no-object-parameters::" in line
    assert "domain type" in line


def test_github_format_escapes_percent_cr_lf_colon_comma() -> None:
    diagnostic = Diagnostic(
        path=Path("weird,dir:name/mod.py"),
        line=1,
        col=1,
        end_line=1,
        end_col=5,
        rule_id="no-object-parameters",
        message="100% done\r\nnext line",
    )
    rendered = runner.format_github((diagnostic,))

    assert rendered == (
        "::error file=weird%2Cdir%3Aname/mod.py,line=1,col=1,endLine=1,"
        "endColumn=5,title=no-object-parameters::"
        "100%25 done%0D%0Anext line"
    )


def test_github_format_one_annotation_per_diagnostic_joined_by_newlines() -> None:
    first = Diagnostic(
        path=Path("mod.py"), line=1, col=1, end_line=1, end_col=2,
        rule_id="no-object-parameters", message="first",
    )
    second = Diagnostic(
        path=Path("mod.py"), line=2, col=1, end_line=2, end_col=2,
        rule_id="no-any-parameters", message="second",
    )
    rendered = runner.format_github((first, second))
    lines = rendered.splitlines()

    assert len(lines) == 2
    assert lines[0].endswith("::first")
    assert lines[1].endswith("::second")


# --------------------------------------------------------------------------- #
# --jobs: the parallel ProcessPoolExecutor walk must match the sequential one
# --------------------------------------------------------------------------- #


def _write_fixture_files(tmp_path: Path, count: int) -> tuple[Path, ...]:
    paths: list[Path] = []
    for index in range(count):
        module = tmp_path / f"mod_{index:03d}.py"
        if index % 3 == 0:
            module.write_text(
                f"def save_{index}(value: object) -> None:\n    ...\n",
                encoding="utf-8",
            )
        elif index % 3 == 1:
            module.write_text(
                f"def handle_{index}(payload):\n    return payload\n",
                encoding="utf-8",
            )
        else:
            module.write_text(
                f"def clean_{index}(value: int) -> int:\n    return value\n",
                encoding="utf-8",
            )
        paths.append(module)
    paths.sort(key=str)
    return tuple(paths)


def _rule_settings(*rules: Rule) -> dict[str, RuleSetting]:
    return {
        rule.id: RuleSetting(
            rule_id=rule.id, enabled=True, options=rule.default_options()
        )
        for rule in rules
    }


def test_jobs_one_stays_sequential_regardless_of_file_count(tmp_path: Path) -> None:
    # `parallel_threshold=2` makes 6 files well above the threshold that would
    # otherwise route to a `ProcessPoolExecutor`; `jobs=1` must still win.
    files = _write_fixture_files(tmp_path, 6)
    rules = (NO_OBJECT_PARAMETERS,)
    settings = _rule_settings(NO_OBJECT_PARAMETERS)

    outcome = runner.run(files, rules, settings, jobs=1, parallel_threshold=2)

    assert not outcome.failures
    assert len(outcome.diagnostics) == 2  # indices 0 and 3 violate (see the helper)


def test_parallel_walk_matches_sequential_walk_on_a_fixture_set(
    tmp_path: Path,
) -> None:
    # `parallel_threshold` is a `run()` argument precisely so a test can exercise
    # the `ProcessPoolExecutor` path on a small, fast fixture set instead of
    # reaching in to patch the module's real (20-file) default.
    files = _write_fixture_files(tmp_path, 8)
    rules = (NO_OBJECT_PARAMETERS, NO_ANY_PARAMETERS)
    settings = _rule_settings(NO_OBJECT_PARAMETERS, NO_ANY_PARAMETERS)

    sequential = runner.run(files, rules, settings, jobs=1, parallel_threshold=2)
    auto_parallel = runner.run(files, rules, settings, parallel_threshold=2)
    explicit_parallel = runner.run(files, rules, settings, jobs=2, parallel_threshold=2)

    assert auto_parallel.diagnostics == sequential.diagnostics
    assert auto_parallel.failures == sequential.failures
    assert explicit_parallel.diagnostics == sequential.diagnostics
    assert explicit_parallel.failures == sequential.failures
    assert sequential.diagnostics  # sanity: the fixture does produce violations


def test_parallel_walk_reports_unparseable_files_as_failures(tmp_path: Path) -> None:
    files = _write_fixture_files(tmp_path, 5)
    broken = tmp_path / "broken.py"
    broken.write_text("def broken(\n", encoding="utf-8")
    files = (*files, broken)
    rules = (NO_OBJECT_PARAMETERS,)
    settings = _rule_settings(NO_OBJECT_PARAMETERS)

    sequential = runner.run(files, rules, settings, jobs=1, parallel_threshold=2)
    parallel = runner.run(files, rules, settings, jobs=2, parallel_threshold=2)

    assert len(sequential.failures) == 1
    assert sequential.failures == parallel.failures
    assert sequential.diagnostics == parallel.diagnostics


# --------------------------------------------------------------------------- #
# .pre-commit-hooks.yaml
# --------------------------------------------------------------------------- #


def test_pre_commit_hooks_manifest_has_the_expected_structure() -> None:
    manifest_path = REPO_ROOT / ".pre-commit-hooks.yaml"
    text = manifest_path.read_text(encoding="utf-8")

    # Hand-rolled structural smoke checks (this project's own zero-dependency rule
    # applies to the repo's dev tooling too: no PyYAML in this test). A single
    # hook, list-of-one-mapping shape: exactly one top-level `- id:` entry.
    assert text.startswith("- id: anti-slop\n")
    assert text.count("\n- id:") == 0  # no second hook in the list
    assert "\n  language: python\n" in text
    assert "\n  entry: anti-slop\n" in text
    assert "\n  types: [python]\n" in text
    assert "\n  require_serial: false\n" in text
    assert "\t" not in text  # YAML forbids tabs for indentation


def test_pre_commit_hooks_entry_matches_the_console_script_name() -> None:
    manifest_path = REPO_ROOT / ".pre-commit-hooks.yaml"
    manifest_text = manifest_path.read_text(encoding="utf-8")

    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    scripts = pyproject["project"]["scripts"]
    assert scripts.keys() == {"anti-slop"}

    assert "\n  entry: anti-slop\n" in manifest_text
