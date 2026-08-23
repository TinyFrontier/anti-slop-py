"""Tests for ``--explain``: five sections per rule, in text and in JSON."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from anti_slop import CORE_RULES
from anti_slop.__main__ import main
from anti_slop.contrib.fastapi import GROUP_RULES
from anti_slop.engine.catalog import explain_text, similar_rule_ids
from anti_slop.engine.rule import Rule

ALL_RULES: tuple[Rule, ...] = (*CORE_RULES, *GROUP_RULES)

SECTION_TITLES = (
    "What it catches",
    "Why it matters",
    "Write instead",
    "When to disable",
    "False-positive caveats",
)

GROUP_RULE_ID = "fastapi/no-state-attribute-access"


def sections_of(rendered: str) -> dict[str, str]:
    """Split rendered explain output into ``title -> body``."""
    found: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in rendered.splitlines():
        if line in SECTION_TITLES:
            if current is not None:
                found[current] = "\n".join(body).strip()
            current = line
            body = []
            continue
        if current is not None:
            body.append(line)
    if current is not None:
        found[current] = "\n".join(body).strip()
    return found


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.id)
def test_every_rule_explains_itself_in_five_non_empty_sections(rule: Rule) -> None:
    found = sections_of(explain_text(rule))

    assert tuple(found) == SECTION_TITLES
    for title in SECTION_TITLES:
        assert found[title].strip(), f"{rule.id}: section {title!r} is empty"


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.id)
def test_the_header_carries_tier_confidence_fix_and_tags(rule: Rule) -> None:
    rendered = explain_text(rule)
    header, tags, *_ = [line for line in rendered.splitlines() if line.strip()]

    assert rule.id in header
    assert rule.metadata.tier in header
    assert rule.metadata.confidence in header
    assert f"fix: {rule.metadata.fix}" in header
    assert tags == f"tags: {', '.join(rule.metadata.tags)}"


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.id)
def test_the_cli_explains_every_rule(
    rule: Rule, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--explain", rule.id])
    captured = capsys.readouterr()

    assert code == 0
    assert tuple(sections_of(captured.out)) == SECTION_TITLES


def test_a_group_rule_is_explained_without_enabling_the_group(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Documentation must not require turning a rule on first."""
    config = tmp_path / "pyproject.toml"
    config.write_text("[tool.anti-slop]\ninclude = [\"src\"]\n", encoding="utf-8")

    code = main(["--explain", GROUP_RULE_ID, "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 0
    assert GROUP_RULE_ID in captured.out
    assert "Depends" in captured.out

    listed = main(["--list-rules", "--config", str(config)])
    assert listed == 0
    assert "fastapi/" not in capsys.readouterr().out


def test_json_explain_carries_the_same_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--explain", "no-widen-then-cast", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["id"] == "no-widen-then-cast"
    assert payload["tier"] == "escape-hatch"
    assert payload["confidence"] == "high"
    assert payload["fix"] == "none"
    assert payload["tags"] == ["casts", "typing"]
    for key in ("summary", "problem", "recipe", "when_to_disable", "fp_caveats"):
        assert payload[key].strip()


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.id)
def test_json_explain_is_non_empty_for_every_rule(
    rule: Rule, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--explain", rule.id, "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    for key in ("summary", "problem", "recipe", "when_to_disable", "fp_caveats"):
        assert payload[key].strip()


def test_an_unknown_rule_exits_two_with_suggestions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--explain", "no-any-parameter"])
    captured = capsys.readouterr()

    assert code == 2
    assert "unknown rule 'no-any-parameter'" in captured.err
    assert "no-any-parameters" in captured.err
    assert captured.out == ""


def test_an_unrecognisable_id_exits_two_without_guessing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--explain", "zzzzzzzz"])
    captured = capsys.readouterr()

    assert code == 2
    assert "did you mean" not in captured.err


def test_suggestions_are_capped_at_three() -> None:
    suggestions = similar_rule_ids("no-any", (rule.id for rule in ALL_RULES))

    assert len(suggestions) <= 3
    assert all(candidate in {rule.id for rule in ALL_RULES} for candidate in suggestions)


def test_explain_answers_even_with_a_broken_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text(
        textwrap.dedent("""
            [tool.anti-slop]
            groups = ["django"]
        """).lstrip("\n"),
        encoding="utf-8",
    )
    code = main(["--explain", "no-chained-casts", "--config", str(config)])
    captured = capsys.readouterr()

    assert code == 0
    assert "What it catches" in captured.out


def test_explain_rejects_a_diagnostics_only_format(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--explain", "no-chained-casts", "--format", "github"])
    captured = capsys.readouterr()

    assert code == 2
    assert "--format must be text or json" in captured.err
