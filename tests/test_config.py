"""Tests for ``[tool.anti-slop]`` parsing (PLAN.md FR-2)."""

from __future__ import annotations

import textwrap
from pathlib import Path, PurePosixPath

import pytest

from anti_slop import CORE_RULES
from anti_slop.engine.config import (
    DEFAULT_EXCLUDE,
    Config,
    ConfigError,
    PathFilter,
    load_config,
)

RULE_ID = "no-object-parameters"


def write_pyproject(directory: Path, body: str) -> Path:
    path = directory / "pyproject.toml"
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return path


def load(directory: Path, body: str) -> Config:
    path = write_pyproject(directory, body)
    return load_config(registry=CORE_RULES, explicit_path=path)


def test_missing_section_enables_every_rule_with_defaults(tmp_path: Path) -> None:
    config = load(tmp_path, """
        [project]
        name = "demo"
    """)
    assert config.source is None
    assert config.exclude == DEFAULT_EXCLUDE
    assert config.rules[RULE_ID].enabled is True
    assert config.rules[RULE_ID].options["allow-object"] is False


def test_level_error_and_off(tmp_path: Path) -> None:
    enabled = load(tmp_path, f"""
        [tool.anti-slop.rules]
        "{RULE_ID}" = "error"
    """)
    assert enabled.rules[RULE_ID].enabled is True

    disabled = load(tmp_path, f"""
        [tool.anti-slop.rules]
        "{RULE_ID}" = "off"
    """)
    assert disabled.rules[RULE_ID].enabled is False
    assert RULE_ID not in {rule.id for rule in disabled.enabled_rules(CORE_RULES)}


def test_table_form_carries_level_and_options(tmp_path: Path) -> None:
    config = load(tmp_path, f"""
        [tool.anti-slop.rules]
        "{RULE_ID}" = {{ level = "error", allow-object = true }}
    """)
    setting = config.rules[RULE_ID]
    assert setting.enabled is True
    assert setting.options["allow-object"] is True


def test_table_form_defaults_to_error_when_level_is_omitted(tmp_path: Path) -> None:
    config = load(tmp_path, f"""
        [tool.anti-slop.rules]
        "{RULE_ID}" = {{ allow-object = true }}
    """)
    assert config.rules[RULE_ID].enabled is True


def test_include_and_exclude(tmp_path: Path) -> None:
    config = load(tmp_path, """
        [tool.anti-slop]
        include = ["src", "tests"]
        exclude = [".venv/**", "tools/anti_slop/**"]
    """)
    assert config.include == ("src", "tests")
    assert config.exclude == (".venv/**", "tools/anti_slop/**")
    assert config.root == tmp_path
    assert config.source == tmp_path / "pyproject.toml"


def test_unknown_rule_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown rule 'no-such-rule'"):
        load(tmp_path, """
            [tool.anti-slop.rules]
            no-such-rule = "error"
        """)


def test_unknown_option_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown option 'allow-anything'"):
        load(tmp_path, f"""
            [tool.anti-slop.rules]
            "{RULE_ID}" = {{ allow-anything = true }}
        """)


def test_wrongly_typed_option_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must be a boolean"):
        load(tmp_path, f"""
            [tool.anti-slop.rules]
            "{RULE_ID}" = {{ allow-object = "yes" }}
        """)


def test_unknown_level_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must be one of"):
        load(tmp_path, f"""
            [tool.anti-slop.rules]
            "{RULE_ID}" = "warn"
        """)


def test_unknown_top_level_key_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        load(tmp_path, """
            [tool.anti-slop]
            includes = ["src"]
        """)


def test_include_must_be_strings(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must contain only strings"):
        load(tmp_path, """
            [tool.anti-slop]
            include = ["src", 7]
        """)


def test_invalid_toml_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid TOML"):
        load(tmp_path, """
            [tool.anti-slop
        """)


def test_missing_explicit_config_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        load_config(registry=CORE_RULES, explicit_path=tmp_path / "nope.toml")


def test_discovery_walks_up_to_the_nearest_pyproject(tmp_path: Path) -> None:
    write_pyproject(tmp_path, f"""
        [tool.anti-slop.rules]
        "{RULE_ID}" = "off"
    """)
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    config = load_config(registry=CORE_RULES, start_dir=nested)
    assert config.source == tmp_path / "pyproject.toml"
    assert config.rules[RULE_ID].enabled is False


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (".venv/lib/mod.py", True),
        ("src/pkg/__pycache__/mod.cpython-312.pyc", True),
        ("build/lib/mod.py", True),
        ("build", True),
        ("src/pkg/mod.py", False),
        ("src/venv_helpers.py", False),
        ("rebuild/mod.py", False),
    ],
)
def test_path_filter_matches_segments_and_ancestors(
    candidate: str, expected: bool
) -> None:
    path_filter = PathFilter([".venv/**", "**/__pycache__/**", "build"])
    assert path_filter.excludes(PurePosixPath(candidate)) is expected


def test_path_filter_star_does_not_cross_segments() -> None:
    path_filter = PathFilter(["*.py"])
    assert path_filter.excludes(PurePosixPath("mod.py")) is True
    assert path_filter.excludes(PurePosixPath("src/mod.py")) is False
