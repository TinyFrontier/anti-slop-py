"""Tests for rule metadata: completeness, the declared layout, and the JSON listing.

Every rule this distribution ships -- the 15 core rules and the 4 of the ``fastapi``
group -- is swept for a full metadata block, so a new rule cannot reach a user with a
blank ``--explain`` section or an undeclared tier.
"""

from __future__ import annotations

import dataclasses
import json
import textwrap
from pathlib import Path

import pytest

from anti_slop import CORE_RULES
from anti_slop.__main__ import main
from anti_slop.contrib.fastapi import GROUP_RULES
from anti_slop.engine.catalog import (
    OPTION_TYPE_BOOL,
    OPTION_TYPE_STRING_LIST,
    rule_list_json,
)
from anti_slop.engine.config import load_config
from anti_slop.engine.rule import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_POLICY,
    CONFIDENCES,
    FIX_NONE,
    MAX_TAGS,
    TIER_ARCHITECTURAL,
    TIER_ESCAPE_HATCH,
    TIER_FRAMEWORK,
    TIERS,
    Rule,
    RuleMetadata,
)

ALL_RULES: tuple[Rule, ...] = (*CORE_RULES, *GROUP_RULES)

# The tier layout of README's "Opinionated by design": ten escape-hatch rules, five
# architectural ones, and every group rule at framework tier.
ESCAPE_HATCH_IDS = frozenset({
    "no-any-parameters",
    "no-any-returns",
    "no-any-type-aliases",
    "no-chained-casts",
    "no-conditional-empty-dict-spread",
    "no-dynamic-dispatch",
    "no-known-value-widening",
    "no-unsafe-dict-values",
    "no-widen-then-cast",
    "require-safety-comment",
})

ARCHITECTURAL_IDS = frozenset({
    "no-adhoc-isinstance",
    "no-module-mocking",
    "no-object-parameters",
    "no-shape-in-symbol-names",
    "no-string-attribute-access",
})

# The one escape-hatch rule that is not high confidence: alias resolution and unions
# make its findings worth a second look.
MEDIUM_CONFIDENCE_IDS = frozenset({"no-unsafe-dict-values"})

VALID_METADATA = RuleMetadata(
    tier=TIER_ESCAPE_HATCH,
    confidence=CONFIDENCE_HIGH,
    fix=FIX_NONE,
    tags=("typing",),
    problem="A problem statement.",
    recipe="A recipe.",
    when_to_disable="A posture.",
    fp_caveats="A caveat.",
)


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.id)
def test_every_rule_declares_complete_metadata(rule: Rule) -> None:
    metadata = rule.metadata
    assert metadata.tier in TIERS
    assert metadata.confidence in CONFIDENCES
    assert metadata.fix == FIX_NONE
    assert 1 <= len(metadata.tags) <= MAX_TAGS
    assert len(set(metadata.tags)) == len(metadata.tags)
    assert all(tag.strip() for tag in metadata.tags)
    assert rule.description.strip()
    for block in (
        metadata.problem,
        metadata.recipe,
        metadata.when_to_disable,
        metadata.fp_caveats,
    ):
        assert block.strip()


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.id)
def test_tier_matches_the_declared_layout(rule: Rule) -> None:
    if rule.id in ESCAPE_HATCH_IDS:
        assert rule.metadata.tier == TIER_ESCAPE_HATCH
    elif rule.id in ARCHITECTURAL_IDS:
        assert rule.metadata.tier == TIER_ARCHITECTURAL
    else:
        assert "/" in rule.id
        assert rule.metadata.tier == TIER_FRAMEWORK


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.id)
def test_confidence_matches_the_declared_layout(rule: Rule) -> None:
    if rule.metadata.tier != TIER_ESCAPE_HATCH:
        assert rule.metadata.confidence == CONFIDENCE_POLICY
    elif rule.id in MEDIUM_CONFIDENCE_IDS:
        assert rule.metadata.confidence == CONFIDENCE_MEDIUM
    else:
        assert rule.metadata.confidence == CONFIDENCE_HIGH


def test_the_layout_covers_every_core_rule() -> None:
    assert ESCAPE_HATCH_IDS | ARCHITECTURAL_IDS == {rule.id for rule in CORE_RULES}
    assert len(ALL_RULES) == 19


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tier", "made-up-tier"),
        ("confidence", "certain"),
        ("fix", "auto"),
        ("problem", "   "),
        ("recipe", ""),
        ("when_to_disable", ""),
        ("fp_caveats", "\n"),
    ],
)
def test_invalid_metadata_is_rejected_at_construction(field: str, value: str) -> None:
    fields = {
        "tier": VALID_METADATA.tier,
        "confidence": VALID_METADATA.confidence,
        "fix": VALID_METADATA.fix,
        "tags": VALID_METADATA.tags,
        "problem": VALID_METADATA.problem,
        "recipe": VALID_METADATA.recipe,
        "when_to_disable": VALID_METADATA.when_to_disable,
        "fp_caveats": VALID_METADATA.fp_caveats,
    }
    fields[field] = value
    with pytest.raises(ValueError, match=field):
        RuleMetadata(**fields)


@pytest.mark.parametrize(
    "tags", [(), ("typing", "typing"), ("a", "b", "c", "d"), ("typing", " ")]
)
def test_invalid_tags_are_rejected(tags: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="tag"):
        RuleMetadata(
            tier=TIER_ESCAPE_HATCH,
            confidence=CONFIDENCE_HIGH,
            fix=FIX_NONE,
            tags=tags,
            problem="A problem statement.",
            recipe="A recipe.",
            when_to_disable="A posture.",
            fp_caveats="A caveat.",
        )


def test_metadata_is_a_mandatory_field_with_no_default() -> None:
    """A rule cannot be built without a metadata block -- there is nothing to fall
    back to, so omitting it is a `TypeError` at construction (and a type error before
    that) rather than a rule that ships with a blank explanation.
    """
    declared = {entry.name: entry for entry in dataclasses.fields(Rule)}

    assert "metadata" in declared
    assert declared["metadata"].default is dataclasses.MISSING
    assert declared["metadata"].default_factory is dataclasses.MISSING


def test_a_rule_without_a_description_is_rejected() -> None:
    with pytest.raises(ValueError, match="description"):
        Rule(
            id="probe",
            description="  ",
            messages={"probe": "probe"},
            handlers=(),
            metadata=VALID_METADATA,
        )


def test_rule_list_json_is_the_full_documented_schema(tmp_path: Path) -> None:
    """A snapshot of the whole listing: every rule, every key, every option."""
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        textwrap.dedent("""
            [tool.anti-slop]
            groups = ["fastapi"]
        """).lstrip("\n"),
        encoding="utf-8",
    )
    config = load_config(registry=CORE_RULES, explicit_path=config_path)
    payload = json.loads(rule_list_json(config.registry, config.levels()))

    assert payload == EXPECTED_RULE_LIST


def test_rule_list_json_reaches_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text("[tool.anti-slop]\n", encoding="utf-8")
    code = main(["--list-rules", "--format", "json", "--config", str(config_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert [entry["id"] for entry in payload] == sorted(
        rule.id for rule in CORE_RULES
    )
    assert all(entry["default_level"] == "error" for entry in payload)


def test_rule_list_json_reports_the_preset_level(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        textwrap.dedent("""
            [tool.anti-slop]
            preset = "recommended"
        """).lstrip("\n"),
        encoding="utf-8",
    )
    code = main(["--list-rules", "--format", "json", "--config", str(config_path)])
    payload = json.loads(capsys.readouterr().out)
    levels = {entry["id"]: entry["default_level"] for entry in payload}

    assert code == 0
    assert levels["no-any-parameters"] == "error"
    assert levels["no-object-parameters"] == "warn"


def test_rule_list_text_carries_tier_and_confidence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["--list-rules"])
    captured = capsys.readouterr()

    assert code == 0
    assert "no-any-parameters  [escape-hatch, high]  " in captured.out
    assert "no-object-parameters  [architectural, policy]  " in captured.out
    assert "no-unsafe-dict-values  [escape-hatch, medium]  " in captured.out


def test_option_types_are_named_in_the_schema() -> None:
    payload = json.loads(rule_list_json(CORE_RULES, {}))
    by_id = {entry["id"]: entry for entry in payload}

    assert by_id["no-object-parameters"]["options"] == [
        {"name": "allow-object", "type": OPTION_TYPE_BOOL, "default": False},
        {"name": "allow-variadic-object", "type": OPTION_TYPE_BOOL, "default": True},
    ]
    assert by_id["no-shape-in-symbol-names"]["options"] == [
        {"name": "terms", "type": OPTION_TYPE_STRING_LIST, "default": ["shape"]}
    ]


def _entry(
    rule_id: str,
    tier: str,
    confidence: str,
    tags: list[str],
    options: list[dict[str, str | bool | list[str]]] | None = None,
) -> dict[str, str | list[str] | list[dict[str, str | bool | list[str]]]]:
    """One expected listing row, with the fields a snapshot must pin down.

    ``summary`` is filled in from the rule itself: the snapshot is about the schema
    and the classification, not about re-typing every rule's one-line description.
    """
    rule = next(entry for entry in ALL_RULES if entry.id == rule_id)
    return {
        "id": rule_id,
        "summary": rule.description,
        "tier": tier,
        "confidence": confidence,
        "default_level": "error",
        "fix": FIX_NONE,
        "tags": tags,
        "options": [] if options is None else options,
    }


EXPECTED_RULE_LIST = [
    _entry(
        "no-adhoc-isinstance",
        TIER_ARCHITECTURAL,
        CONFIDENCE_POLICY,
        ["typing", "narrowing"],
        [{"name": "allow-in-type-guards", "type": OPTION_TYPE_BOOL, "default": True}],
    ),
    _entry("no-any-parameters", TIER_ESCAPE_HATCH, CONFIDENCE_HIGH, ["typing"]),
    _entry("no-any-returns", TIER_ESCAPE_HATCH, CONFIDENCE_HIGH, ["typing"]),
    _entry("no-any-type-aliases", TIER_ESCAPE_HATCH, CONFIDENCE_HIGH, ["typing"]),
    _entry(
        "no-chained-casts", TIER_ESCAPE_HATCH, CONFIDENCE_HIGH, ["casts", "typing"]
    ),
    _entry(
        "no-conditional-empty-dict-spread",
        TIER_ESCAPE_HATCH,
        CONFIDENCE_HIGH,
        ["dict", "typing"],
    ),
    _entry("no-dynamic-dispatch", TIER_ESCAPE_HATCH, CONFIDENCE_HIGH, ["reflection"]),
    _entry("no-known-value-widening", TIER_ESCAPE_HATCH, CONFIDENCE_HIGH, ["typing"]),
    _entry("no-module-mocking", TIER_ARCHITECTURAL, CONFIDENCE_POLICY, ["testing"]),
    _entry(
        "no-object-parameters",
        TIER_ARCHITECTURAL,
        CONFIDENCE_POLICY,
        ["typing"],
        [
            {"name": "allow-object", "type": OPTION_TYPE_BOOL, "default": False},
            {
                "name": "allow-variadic-object",
                "type": OPTION_TYPE_BOOL,
                "default": True,
            },
        ],
    ),
    _entry(
        "no-shape-in-symbol-names",
        TIER_ARCHITECTURAL,
        CONFIDENCE_POLICY,
        ["naming"],
        [{"name": "terms", "type": OPTION_TYPE_STRING_LIST, "default": ["shape"]}],
    ),
    _entry(
        "no-string-attribute-access",
        TIER_ARCHITECTURAL,
        CONFIDENCE_POLICY,
        ["reflection"],
    ),
    _entry(
        "no-unsafe-dict-values",
        TIER_ESCAPE_HATCH,
        CONFIDENCE_MEDIUM,
        ["typing", "dict"],
    ),
    _entry(
        "no-widen-then-cast", TIER_ESCAPE_HATCH, CONFIDENCE_HIGH, ["casts", "typing"]
    ),
    _entry(
        "require-safety-comment",
        TIER_ESCAPE_HATCH,
        CONFIDENCE_HIGH,
        ["casts", "suppressions"],
    ),
    _entry(
        "fastapi/no-dict-body-parameters",
        TIER_FRAMEWORK,
        CONFIDENCE_POLICY,
        ["fastapi", "typing"],
    ),
    _entry(
        "fastapi/no-raw-request-parsing",
        TIER_FRAMEWORK,
        CONFIDENCE_POLICY,
        ["fastapi"],
    ),
    _entry(
        "fastapi/no-state-attribute-access",
        TIER_FRAMEWORK,
        CONFIDENCE_POLICY,
        ["fastapi", "reflection"],
    ),
    _entry(
        "fastapi/no-untyped-route-response",
        TIER_FRAMEWORK,
        CONFIDENCE_POLICY,
        ["fastapi", "typing"],
    ),
]
