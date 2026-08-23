"""Tests for ``[tool.anti-slop].preset``: the levels each preset hands out.

A preset is a starting point derived from rule metadata. Two properties matter more
than any individual level: ``[tool.anti-slop.rules]`` always wins over it, and a
configuration without ``preset`` behaves exactly as it did before presets existed.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from anti_slop import CORE_RULES
from anti_slop.__main__ import main
from anti_slop.contrib.fastapi import GROUP_RULES
from anti_slop.engine.config import (
    PRESETS,
    Config,
    ConfigError,
    load_config,
)

ESCAPE_HATCH_HIGH = "no-any-parameters"
ESCAPE_HATCH_MEDIUM = "no-unsafe-dict-values"
ARCHITECTURAL = "no-object-parameters"
GROUP_RULE = "fastapi/no-dict-body-parameters"


def load(directory: Path, body: str) -> Config:
    path = directory / "pyproject.toml"
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return load_config(registry=CORE_RULES, explicit_path=path)


def levels(config: Config) -> dict[str, str]:
    return config.levels()


@pytest.mark.parametrize(
    ("preset", "high", "medium", "architectural"),
    [
        ("strict", "error", "error", "error"),
        ("recommended", "error", "error", "warn"),
        ("minimal", "error", "off", "off"),
        ("legacy", "warn", "warn", "off"),
    ],
)
def test_each_preset_sets_the_documented_levels(
    tmp_path: Path, preset: str, high: str, medium: str, architectural: str
) -> None:
    config = load(tmp_path, f"""
        [tool.anti-slop]
        preset = "{preset}"
    """)
    resolved = levels(config)

    assert config.preset == preset
    assert resolved[ESCAPE_HATCH_HIGH] == high
    assert resolved[ESCAPE_HATCH_MEDIUM] == medium
    assert resolved[ARCHITECTURAL] == architectural


@pytest.mark.parametrize("preset", [entry.name for entry in PRESETS])
def test_every_core_rule_gets_a_level_from_every_preset(
    tmp_path: Path, preset: str
) -> None:
    config = load(tmp_path, f"""
        [tool.anti-slop]
        preset = "{preset}"
    """)
    resolved = levels(config)

    assert set(resolved) == {rule.id for rule in CORE_RULES}
    assert all(level in {"error", "warn", "off"} for level in resolved.values())


def test_minimal_keeps_only_high_confidence_escape_hatch_rules(
    tmp_path: Path,
) -> None:
    config = load(tmp_path, """
        [tool.anti-slop]
        preset = "minimal"
    """)
    resolved = levels(config)
    enabled = {rule_id for rule_id, level in resolved.items() if level != "off"}

    assert enabled == {
        rule.id
        for rule in CORE_RULES
        if rule.metadata.tier == "escape-hatch"
        and rule.metadata.confidence == "high"
    }
    assert ESCAPE_HATCH_MEDIUM not in enabled


def test_rules_override_the_preset(tmp_path: Path) -> None:
    config = load(tmp_path, f"""
        [tool.anti-slop]
        preset = "legacy"

        [tool.anti-slop.rules]
        "{ARCHITECTURAL}" = "error"
        "{ESCAPE_HATCH_HIGH}" = "off"
    """)
    resolved = levels(config)

    assert resolved[ARCHITECTURAL] == "error"
    assert resolved[ESCAPE_HATCH_HIGH] == "off"
    # Untouched rules keep the preset's level.
    assert resolved[ESCAPE_HATCH_MEDIUM] == "warn"


def test_setting_an_option_does_not_re_raise_a_lowered_rule(tmp_path: Path) -> None:
    config = load(tmp_path, f"""
        [tool.anti-slop]
        preset = "recommended"

        [tool.anti-slop.rules]
        "{ARCHITECTURAL}" = {{ allow-object = true }}
    """)
    resolved = levels(config)

    assert resolved[ARCHITECTURAL] == "warn"
    assert config.rules[ARCHITECTURAL].options["allow-object"] is True


def test_a_table_with_an_explicit_level_still_wins(tmp_path: Path) -> None:
    config = load(tmp_path, f"""
        [tool.anti-slop]
        preset = "legacy"

        [tool.anti-slop.rules]
        "{ARCHITECTURAL}" = {{ level = "error", allow-object = true }}
    """)

    assert levels(config)[ARCHITECTURAL] == "error"


def test_without_a_preset_every_rule_is_error(tmp_path: Path) -> None:
    """The compatibility snapshot: no preset means exactly the previous behaviour."""
    config = load(tmp_path, """
        [tool.anti-slop]
        include = ["src"]
    """)
    resolved = levels(config)

    assert config.preset is None
    assert resolved == {rule.id: "error" for rule in CORE_RULES}
    assert all(setting.enabled for setting in config.rules.values())


def test_strict_is_indistinguishable_from_no_preset(tmp_path: Path) -> None:
    without = load(tmp_path, """
        [tool.anti-slop]
        include = ["src"]
    """)
    with_strict = load(tmp_path, """
        [tool.anti-slop]
        include = ["src"]
        preset = "strict"
    """)

    assert levels(with_strict) == levels(without)


def test_a_preset_does_not_touch_group_rules(tmp_path: Path) -> None:
    config = load(tmp_path, """
        [tool.anti-slop]
        preset = "minimal"
        groups = ["fastapi"]
    """)
    resolved = levels(config)

    assert {rule.id for rule in GROUP_RULES} <= set(resolved)
    assert all(resolved[rule.id] == "error" for rule in GROUP_RULES)
    assert resolved[ARCHITECTURAL] == "off"


def test_a_group_rule_is_still_configurable_by_name(tmp_path: Path) -> None:
    config = load(tmp_path, f"""
        [tool.anti-slop]
        preset = "legacy"
        groups = ["fastapi"]

        [tool.anti-slop.rules]
        "{GROUP_RULE}" = "warn"
    """)

    assert levels(config)[GROUP_RULE] == "warn"


def test_an_unknown_preset_lists_the_ones_that_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown preset 'paranoid'") as error:
        load(tmp_path, """
            [tool.anti-slop]
            preset = "paranoid"
        """)

    for preset in PRESETS:
        assert preset.name in str(error.value)


def test_a_non_string_preset_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="preset must be a string"):
        load(tmp_path, """
            [tool.anti-slop]
            preset = ["recommended"]
        """)


def test_a_preset_changes_what_a_run_does(tmp_path: Path) -> None:
    """End to end: the same violation blocks under `strict` and only warns under
    `legacy`, and disappears entirely under `minimal` when it is architectural.
    """
    module = tmp_path / "module.py"
    module.write_text(
        textwrap.dedent("""
            from typing import Any


            def handle(payload: Any) -> None:
                return None
        """).lstrip("\n"),
        encoding="utf-8",
    )
    architectural = tmp_path / "architectural.py"
    architectural.write_text(
        "def save(value: object) -> None:\n    return None\n", encoding="utf-8"
    )

    def run_with(preset: str, target: Path) -> int:
        config = tmp_path / "pyproject.toml"
        config.write_text(
            f'[tool.anti-slop]\npreset = "{preset}"\n', encoding="utf-8"
        )
        return main([str(target), "--config", str(config)])

    assert run_with("strict", module) == 1
    assert run_with("legacy", module) == 0
    assert run_with("strict", architectural) == 1
    assert run_with("minimal", architectural) == 0


def test_preset_names_are_unique_and_documented() -> None:
    names = [preset.name for preset in PRESETS]

    assert names == ["strict", "recommended", "minimal", "legacy"]
    assert len(set(names)) == len(names)
    assert all(preset.summary.strip() for preset in PRESETS)
