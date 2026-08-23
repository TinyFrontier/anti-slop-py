"""Tests for the opt-in group mechanism: ``[tool.anti-slop].groups``."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from anti_slop import CORE_RULES
from anti_slop.__main__ import main
from anti_slop.contrib.fastapi import GROUP_RULES, MOCKING_RECIPE
from anti_slop.engine.config import (
    KNOWN_GROUPS,
    Config,
    ConfigError,
    load_config,
    load_group,
)
from anti_slop.rules.no_module_mocking import (
    MESSAGE_PROBLEM,
    MESSAGE_RECIPE,
    RULE_ID as NO_MODULE_MOCKING_ID,
)

GROUP_RULE_ID = "fastapi/no-dict-body-parameters"

WITH_GROUP = """
[tool.anti-slop]
groups = ["fastapi"]
"""

HANDLER_WITH_DICT_BODY = """
from fastapi import FastAPI

app = FastAPI()

@app.post("/users")
async def create_user(payload: dict) -> UserOut:
    return UserOut(**payload)
"""

FIXTURE_PATCH = """
def test_create(mocker):
    mocker.patch("app.services.user_store.save")
"""


def write_pyproject(directory: Path, body: str) -> Path:
    path = directory / "pyproject.toml"
    path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    return path


def load(directory: Path, body: str) -> Config:
    path = write_pyproject(directory, body)
    return load_config(registry=CORE_RULES, explicit_path=path)


def make_project(tmp_path: Path, module_body: str, config: str) -> tuple[Path, Path]:
    path = write_pyproject(tmp_path, config)
    module = tmp_path / "mod.py"
    module.write_text(textwrap.dedent(module_body).lstrip("\n"), encoding="utf-8")
    return path, module


def test_no_groups_key_keeps_the_core_registry_exactly(tmp_path: Path) -> None:
    config = load(tmp_path, """
        [tool.anti-slop]
        include = ["src"]
    """)
    assert config.groups == ()
    assert config.registry == CORE_RULES
    assert not [rule for rule in config.registry if "/" in rule.id]


def test_enabled_group_adds_its_rules_at_error(tmp_path: Path) -> None:
    config = load(tmp_path, WITH_GROUP)

    assert config.groups == ("fastapi",)
    group_ids = [rule.id for rule in GROUP_RULES]
    assert group_ids == [
        "fastapi/no-dict-body-parameters",
        "fastapi/no-raw-request-parsing",
        "fastapi/no-state-attribute-access",
        "fastapi/no-untyped-route-response",
    ]
    for rule_id in group_ids:
        assert config.rules[rule_id].enabled is True
    # Core rules keep their place, and the group rules follow them.
    assert config.registry[: len(CORE_RULES)] == tuple(
        rule for rule in config.registry if "/" not in rule.id
    )
    assert {rule.id for rule in config.enabled_rules()} >= set(group_ids)


def test_every_known_group_declares_the_registry_surface() -> None:
    """The invariant the ``load_group`` cast rests on, checked for every group."""
    for name in KNOWN_GROUPS:
        module = load_group(name, "<test>")
        assert module.GROUP_RULES
        assert all(rule.id.startswith(f"{name}/") for rule in module.GROUP_RULES)
        for rule_id, messages in module.CORE_MESSAGE_OVERRIDES.items():
            (core,) = [rule for rule in CORE_RULES if rule.id == rule_id]
            assert set(messages) <= set(core.messages)


def test_unknown_group_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown group 'django'"):
        load(tmp_path, """
            [tool.anti-slop]
            groups = ["django"]
        """)


def test_group_listed_twice_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="lists 'fastapi' twice"):
        load(tmp_path, """
            [tool.anti-slop]
            groups = ["fastapi", "fastapi"]
        """)


def test_groups_must_be_strings(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must contain only strings"):
        load(tmp_path, """
            [tool.anti-slop]
            groups = [1]
        """)


def test_group_rule_is_configured_by_its_prefixed_id(tmp_path: Path) -> None:
    config = load(tmp_path, f"""
        [tool.anti-slop]
        groups = ["fastapi"]

        [tool.anti-slop.rules]
        "{GROUP_RULE_ID}" = "off"
    """)
    assert config.rules[GROUP_RULE_ID].enabled is False
    assert GROUP_RULE_ID not in {rule.id for rule in config.enabled_rules()}


def test_group_rule_without_its_prefix_is_unknown(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown rule 'no-dict-body-parameters'"):
        load(tmp_path, """
            [tool.anti-slop]
            groups = ["fastapi"]

            [tool.anti-slop.rules]
            no-dict-body-parameters = "off"
        """)


def test_group_rule_is_unknown_while_the_group_is_off(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=f"unknown rule '{GROUP_RULE_ID}'"):
        load(tmp_path, f"""
            [tool.anti-slop.rules]
            "{GROUP_RULE_ID}" = "error"
        """)


def test_group_overrides_the_core_mocking_recipe(tmp_path: Path) -> None:
    config = load(tmp_path, WITH_GROUP)
    (rule,) = [
        rule for rule in config.registry if rule.id == NO_MODULE_MOCKING_ID
    ]

    assert rule.messages["module-mock"] == f"{MESSAGE_PROBLEM} {MOCKING_RECIPE}"
    assert "dependency_overrides" in rule.messages["module-mock"]
    # The override replaces the recipe half only, and never mutates the core rule.
    assert MESSAGE_RECIPE not in rule.messages["module-mock"]
    (core,) = [rule for rule in CORE_RULES if rule.id == NO_MODULE_MOCKING_ID]
    assert core.messages["module-mock"] == f"{MESSAGE_PROBLEM} {MESSAGE_RECIPE}"


def test_core_registry_keeps_its_message_without_the_group(tmp_path: Path) -> None:
    config = load(tmp_path, """
        [tool.anti-slop]
        include = ["src"]
    """)
    (rule,) = [rule for rule in config.registry if rule.id == NO_MODULE_MOCKING_ID]
    assert rule.messages["module-mock"] == f"{MESSAGE_PROBLEM} {MESSAGE_RECIPE}"


def test_overridden_message_reaches_the_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, FIXTURE_PATCH, WITH_GROUP)
    code = main(
        [str(module), "--config", str(config), "--rule", NO_MODULE_MOCKING_ID]
    )
    captured = capsys.readouterr()

    assert code == 1
    assert "app.dependency_overrides" in captured.out
    assert "Protocol" not in captured.out


def test_group_violation_is_reported_under_its_prefixed_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, HANDLER_WITH_DICT_BODY, WITH_GROUP)
    code = main([str(module), "--config", str(config), "--rule", GROUP_RULE_ID])
    captured = capsys.readouterr()

    assert code == 1
    assert f"{module}:6:23 {GROUP_RULE_ID}" in captured.out


def test_the_same_file_is_clean_without_the_group(tmp_path: Path) -> None:
    config, module = make_project(
        tmp_path,
        HANDLER_WITH_DICT_BODY,
        """
        [tool.anti-slop.rules]
        no-any-returns = "off"
        """,
    )
    assert main([str(module), "--config", str(config)]) == 0


def test_unknown_rule_filter_lists_the_group_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, module = make_project(tmp_path, HANDLER_WITH_DICT_BODY, WITH_GROUP)
    code = main([str(module), "--config", str(config), "--rule", "no-such-rule"])
    captured = capsys.readouterr()

    assert code == 2
    assert GROUP_RULE_ID in captured.err


def test_suppression_by_prefixed_id_silences_the_group_rule(tmp_path: Path) -> None:
    config, module = make_project(
        tmp_path,
        """
        from fastapi import FastAPI

        app = FastAPI()

        @app.post("/users")
        # anti-slop: ignore[fastapi/no-dict-body-parameters]
        async def create_user(payload: dict) -> UserOut:
            return UserOut(**payload)
        """,
        WITH_GROUP,
    )
    assert main([str(module), "--config", str(config), "--rule", GROUP_RULE_ID]) == 0


def test_list_rules_shows_group_rules_after_the_core_ones(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_pyproject(tmp_path, WITH_GROUP)
    code = main(["--list-rules", "--config", str(config)])
    captured = capsys.readouterr()
    listed = [line.split("  ")[0] for line in captured.out.splitlines()]

    assert code == 0
    assert listed.index("require-safety-comment") < listed.index(GROUP_RULE_ID)
    assert [rule.id for rule in GROUP_RULES] == [
        entry for entry in listed if entry.startswith("fastapi/")
    ]


def test_list_rules_hides_group_rules_when_the_group_is_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_pyproject(tmp_path, """
        [tool.anti-slop]
        include = ["src"]
    """)
    code = main(["--list-rules", "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 0
    assert "fastapi/" not in captured.out
    assert "no-module-mocking" in captured.out


def test_list_rules_reports_a_broken_config_instead_of_listing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_pyproject(tmp_path, """
        [tool.anti-slop]
        groups = ["django"]
    """)
    code = main(["--list-rules", "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 2
    assert "unknown group 'django'" in captured.err
