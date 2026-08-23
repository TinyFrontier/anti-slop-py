"""``--format sarif``: a single SARIF 2.1.0 document (engine/sarif.py)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from anti_slop.__main__ import main
from anti_slop.engine import runner
from anti_slop.engine.config import RuleSetting
from anti_slop.engine.rule import Diagnostic, Rule
from anti_slop.engine.sarif import SARIF_SCHEMA_URI, SARIF_VERSION, format_sarif
from anti_slop.rules.no_any_parameters import RULE as NO_ANY_PARAMETERS
from anti_slop.rules.no_object_parameters import RULE as NO_OBJECT_PARAMETERS

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


def _settings(*rules: Rule) -> dict[str, RuleSetting]:
    return {
        rule.id: RuleSetting(rule_id=rule.id, enabled=True, options=rule.default_options())
        for rule in rules
    }


# --------------------------------------------------------------------------- #
# top-level structure, over the CLI
# --------------------------------------------------------------------------- #


def test_sarif_top_level_document_structure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, VIOLATION)
    code = main([str(module), "--config", str(config), "--format", "sarif"])
    captured = capsys.readouterr()

    assert code == 1
    document = json.loads(captured.out)
    assert document["version"] == SARIF_VERSION == "2.1.0"
    assert document["$schema"] == SARIF_SCHEMA_URI
    assert len(document["runs"]) == 1
    run_obj = document["runs"][0]
    assert set(run_obj) >= {"tool", "originalUriBaseIds", "results"}
    driver = run_obj["tool"]["driver"]
    assert driver["name"] == "anti-slop"
    assert driver["informationUri"] == "https://github.com/TinyFrontier/anti-slop-py"
    assert "rules" in driver
    # A single JSON document on stdout: exactly one line, no trailing chatter.
    assert captured.out.count("\n") == 1


def test_sarif_empty_run_is_a_valid_document_with_empty_arrays(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, CLEAN)
    code = main([str(module), "--config", str(config), "--format", "sarif"])
    captured = capsys.readouterr()

    assert code == 0
    document = json.loads(captured.out)
    run_obj = document["runs"][0]
    assert run_obj["results"] == []
    assert run_obj["tool"]["driver"]["rules"] == []


# --------------------------------------------------------------------------- #
# results: level, message, region
# --------------------------------------------------------------------------- #


def test_sarif_result_fields_for_an_error_severity_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, VIOLATION)
    code = main([str(module), "--config", str(config), "--format", "sarif"])
    captured = capsys.readouterr()
    assert code == 1

    document = json.loads(captured.out)
    (result,) = document["runs"][0]["results"]
    assert result["ruleId"] == "no-object-parameters"
    assert result["level"] == "error"
    assert "domain type" in result["message"]["text"]
    region = result["locations"][0]["physicalLocation"]["region"]
    assert (region["startLine"], region["startColumn"]) == (1, 10)
    assert region["endLine"] >= region["startLine"]
    assert region["endColumn"] > region["startColumn"]


def test_sarif_uri_is_posix_relative_to_config_root_with_srcroot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    module = nested / "mod.py"
    module.write_text(textwrap.dedent(VIOLATION).lstrip("\n"), encoding="utf-8")

    code = main(
        [str(module), "--config", str(tmp_path / "pyproject.toml"), "--format", "sarif"]
    )
    captured = capsys.readouterr()
    assert code == 1

    document = json.loads(captured.out)
    run_obj = document["runs"][0]
    artifact = run_obj["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]
    assert artifact["uri"] == "pkg/sub/mod.py"
    assert artifact["uriBaseId"] == "SRCROOT"
    assert (
        run_obj["originalUriBaseIds"]["SRCROOT"]["uri"] == f"{tmp_path.resolve().as_uri()}/"
    )


# --------------------------------------------------------------------------- #
# severity mapping: warn -> "warning", error -> "error"
# --------------------------------------------------------------------------- #


def _diagnostic(severity: str) -> Diagnostic:
    return Diagnostic(
        path=Path("mod.py"),
        line=1,
        col=10,
        end_line=1,
        end_col=15,
        rule_id=NO_OBJECT_PARAMETERS.id,
        message="use a domain type instead",
        severity=severity,
    )


def test_sarif_warn_severity_maps_to_warning_level() -> None:
    document = json.loads(
        format_sarif(
            (_diagnostic("warn"),),
            (NO_OBJECT_PARAMETERS,),
            Path.cwd(),
            tool_version="0.0.0",
        )
    )
    (result,) = document["runs"][0]["results"]
    assert result["level"] == "warning"


def test_sarif_error_severity_maps_to_error_level() -> None:
    document = json.loads(
        format_sarif(
            (_diagnostic("error"),),
            (NO_OBJECT_PARAMETERS,),
            Path.cwd(),
            tool_version="0.0.0",
        )
    )
    (result,) = document["runs"][0]["results"]
    assert result["level"] == "error"


# --------------------------------------------------------------------------- #
# tool.driver.rules: only rules seen in results, ruleIndex correct, full metadata
# --------------------------------------------------------------------------- #


def test_sarif_rules_array_contains_only_rules_seen_in_results(tmp_path: Path) -> None:
    module = tmp_path / "mod.py"
    module.write_text(
        "from typing import Any\n\n\ndef save(value: object) -> None:\n    ...\n",
        encoding="utf-8",
    )
    rules = (NO_OBJECT_PARAMETERS, NO_ANY_PARAMETERS)
    outcome = runner.run((module,), rules, _settings(*rules))
    document = json.loads(
        format_sarif(outcome.diagnostics, rules, tmp_path, tool_version="0.0.0")
    )

    driver_rules = document["runs"][0]["tool"]["driver"]["rules"]
    assert {entry["id"] for entry in driver_rules} == {"no-object-parameters"}


def test_sarif_rule_index_matches_the_rules_array_position(tmp_path: Path) -> None:
    module = tmp_path / "mod.py"
    module.write_text(
        "from typing import Any\n\n\ndef handle(value: Any) -> None:\n    ...\n\n\n"
        "def save(value: object) -> None:\n    ...\n",
        encoding="utf-8",
    )
    rules = (NO_OBJECT_PARAMETERS, NO_ANY_PARAMETERS)
    outcome = runner.run((module,), rules, _settings(*rules))
    document = json.loads(
        format_sarif(outcome.diagnostics, rules, tmp_path, tool_version="0.0.0")
    )

    run_obj = document["runs"][0]
    driver_rules = run_obj["tool"]["driver"]["rules"]
    assert len(run_obj["results"]) == 2
    for result in run_obj["results"]:
        assert driver_rules[result["ruleIndex"]]["id"] == result["ruleId"]


def test_sarif_rule_entry_carries_metadata_prose_and_properties(tmp_path: Path) -> None:
    module = tmp_path / "mod.py"
    module.write_text(textwrap.dedent(VIOLATION).lstrip("\n"), encoding="utf-8")
    rules = (NO_OBJECT_PARAMETERS,)
    outcome = runner.run((module,), rules, _settings(*rules))
    document = json.loads(
        format_sarif(outcome.diagnostics, rules, tmp_path, tool_version="0.0.0")
    )

    (entry,) = document["runs"][0]["tool"]["driver"]["rules"]
    metadata = NO_OBJECT_PARAMETERS.metadata
    assert entry["id"] == "no-object-parameters"
    assert entry["shortDescription"]["text"] == NO_OBJECT_PARAMETERS.description
    assert entry["fullDescription"]["text"] == metadata.problem
    assert entry["help"]["text"] == metadata.recipe
    assert entry["helpUri"].startswith("https://github.com/TinyFrontier/anti-slop-py#")
    assert entry["properties"]["tier"] == metadata.tier
    assert entry["properties"]["confidence"] == metadata.confidence
    assert entry["properties"]["tags"] == list(metadata.tags)


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #


def test_sarif_output_is_deterministic_across_repeated_calls(tmp_path: Path) -> None:
    module = tmp_path / "mod.py"
    module.write_text(
        "from typing import Any\n\n\ndef save(value: object) -> None:\n    ...\n\n\n"
        "def handle(value: Any) -> None:\n    ...\n",
        encoding="utf-8",
    )
    rules = (NO_OBJECT_PARAMETERS, NO_ANY_PARAMETERS)
    outcome = runner.run((module,), rules, _settings(*rules))

    first = format_sarif(outcome.diagnostics, rules, tmp_path, tool_version="0.0.0")
    second = format_sarif(outcome.diagnostics, rules, tmp_path, tool_version="0.0.0")

    assert first == second


def test_sarif_cli_output_is_deterministic_across_two_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, VIOLATION)

    main([str(module), "--config", str(config), "--format", "sarif"])
    first = capsys.readouterr().out
    main([str(module), "--config", str(config), "--format", "sarif"])
    second = capsys.readouterr().out

    assert first
    assert first == second


# --------------------------------------------------------------------------- #
# sarif is a run-only format: --list-rules / --explain reject it (exit 2)
# --------------------------------------------------------------------------- #


def test_sarif_rejected_for_list_rules(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--list-rules", "--format", "sarif"])
    captured = capsys.readouterr()

    assert code == 2
    assert "--format must be text or json" in captured.err


def test_sarif_rejected_for_explain(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--explain", "no-object-parameters", "--format", "sarif"])
    captured = capsys.readouterr()

    assert code == 2
    assert "--format must be text or json" in captured.err
