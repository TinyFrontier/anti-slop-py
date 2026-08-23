"""Configuration: the ``[tool.anti-slop]`` section of ``pyproject.toml``.

Everything invalid is loud: an unknown rule id, an unknown option, a wrongly typed
option or an unknown level is a configuration error (CLI exit code 2), never a
silently ignored line.

``groups`` is the one key that changes *which rules exist*. Each entry names a
subpackage of :mod:`anti_slop.contrib` whose ``__init__`` exposes ``GROUP_RULES``
(rules whose ids are prefixed with the group name) and ``CORE_MESSAGE_OVERRIDES``
(replacement message templates for core rules, so a framework group can give a core
rule its own recipe). Enabling a group appends its rules to the effective registry --
:attr:`Config.registry` -- at ``error`` by default; leaving ``groups`` out reproduces
the core-only behaviour exactly.

``preset`` is the one key that changes *which levels rules start at*. It reads the
tier and confidence of each core rule's metadata and hands out levels accordingly
(see :data:`PRESETS`); ``[tool.anti-slop.rules]`` then overrides the result rule by
rule, so a preset is a starting point and never the last word. Leaving ``preset`` out
is not a preset -- every rule starts at ``error``, exactly as before presets existed.
"""

from __future__ import annotations

import importlib
import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol, cast

from anti_slop.engine.rule import (
    CONFIDENCE_HIGH,
    TIER_ARCHITECTURAL,
    TIER_ESCAPE_HATCH,
    BoolOption,
    OptionValue,
    Rule,
    RuleMetadata,
    StrListOption,
)

__all__ = [
    "CONTRIB_PACKAGE",
    "DEFAULT_EXCLUDE",
    "KNOWN_GROUPS",
    "PRESETS",
    "Config",
    "ConfigError",
    "GroupModule",
    "PathFilter",
    "Preset",
    "RuleSetting",
    "known_rules",
    "load_config",
    "load_group",
    "preset_named",
    "resolve_registry",
]

# TOML values, as far as this tool needs to understand them.
type TomlValue = str | int | float | bool | list[TomlValue] | dict[str, TomlValue]

SECTION = "anti-slop"

LEVEL_ERROR = "error"
LEVEL_WARN = "warn"
LEVEL_OFF = "off"
_LEVELS = (LEVEL_ERROR, LEVEL_WARN, LEVEL_OFF)

_TOP_LEVEL_KEYS = frozenset(
    {"baseline", "groups", "include", "exclude", "preset", "rules"}
)

# Where a group's subpackage lives, and the groups this distribution ships. The list
# is explicit rather than a directory scan so that an unknown name fails with the
# available groups spelled out, instead of with an import traceback.
CONTRIB_PACKAGE = "anti_slop.contrib"
KNOWN_GROUPS: tuple[str, ...] = ("fastapi",)

DEFAULT_EXCLUDE: tuple[str, ...] = (
    "**/.git/**",
    "**/.venv/**",
    "**/venv/**",
    "**/node_modules/**",
    "**/__pycache__/**",
    "**/.mypy_cache/**",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/build/**",
    "**/dist/**",
)


class ConfigError(Exception):
    """Raised for any malformed configuration or suppression directive."""


@dataclass(frozen=True, slots=True)
class RuleSetting:
    """The resolved configuration of one rule: on/off plus fully defaulted options.

    ``severity`` distinguishes ``error`` from ``warn`` for an *enabled* rule; ``off``
    is not a severity, it is ``enabled=False``. Defaults to ``"error"`` so every
    existing construction of a ``RuleSetting`` -- test harnesses included -- keeps
    its prior meaning without naming the field.
    """

    rule_id: str
    enabled: bool
    options: Mapping[str, OptionValue]
    severity: Literal["error", "warn"] = "error"

    @property
    def level(self) -> str:
        """The configured level as written in ``pyproject.toml``: the two fields as one."""
        return self.severity if self.enabled else LEVEL_OFF


@dataclass(frozen=True, slots=True)
class Preset:
    """A named starting point for the levels of the *core* rules.

    A preset reads two things from a rule's metadata: its tier decides which of the
    two levels below applies, and its confidence decides whether a
    ``high_confidence_only`` preset keeps it at all. Rules of an opt-in group are
    never touched -- a group is enabled by ``groups`` and configured by name, and a
    preset chosen for the core has no opinion about framework policy.
    """

    name: str
    escape_hatch: str
    architectural: str
    summary: str
    high_confidence_only: bool = False

    def level_for(self, metadata: RuleMetadata) -> str:
        """The level this preset gives a rule with ``metadata``."""
        if metadata.tier == TIER_ESCAPE_HATCH:
            if self.high_confidence_only and metadata.confidence != CONFIDENCE_HIGH:
                return LEVEL_OFF
            return self.escape_hatch
        if metadata.tier == TIER_ARCHITECTURAL:
            return self.architectural
        return LEVEL_ERROR


# The presets this distribution ships, in order from strictest to most forgiving.
# `strict` is spelled out rather than treated as "no preset" so that writing it down
# means the same thing as leaving `preset` out entirely.
PRESETS: tuple[Preset, ...] = (
    Preset(
        name="strict",
        escape_hatch=LEVEL_ERROR,
        architectural=LEVEL_ERROR,
        summary="every core rule at error -- the behaviour with no preset at all",
    ),
    Preset(
        name="recommended",
        escape_hatch=LEVEL_ERROR,
        architectural=LEVEL_WARN,
        summary="escape hatches block, architectural policy reports",
    ),
    Preset(
        name="minimal",
        escape_hatch=LEVEL_ERROR,
        architectural=LEVEL_OFF,
        summary="only the high-confidence escape-hatch rules, at error",
        high_confidence_only=True,
    ),
    Preset(
        name="legacy",
        escape_hatch=LEVEL_WARN,
        architectural=LEVEL_OFF,
        summary="nothing blocks -- a starting point for a large old codebase",
    ),
)


def preset_named(name: str, where: str) -> Preset:
    """The preset called ``name``; an unknown name lists the ones that exist."""
    for preset in PRESETS:
        if preset.name == name:
            return preset
    available = ", ".join(entry.name for entry in PRESETS)
    message = f"{where}: unknown preset {name!r} (known presets: {available})"
    raise ConfigError(message)


class GroupModule(Protocol):
    """What the ``__init__`` of an :mod:`anti_slop.contrib` group must expose.

    ``GROUP_RULES`` holds the group's own rules, every id prefixed with the group
    name (``fastapi/no-dict-body-parameters``). ``CORE_MESSAGE_OVERRIDES`` maps a
    *core* rule id to the message ids it replaces, which is how a framework group
    gives an existing rule a framework-specific recipe without forking the rule.
    """

    GROUP_RULES: tuple[Rule, ...]
    CORE_MESSAGE_OVERRIDES: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved configuration.

    ``root`` is the directory the configuration applies to (the directory holding
    ``pyproject.toml``, or the working directory when no configuration was found).
    ``registry`` is the effective rule set -- the core rules, with any message
    overrides the enabled ``groups`` applied, followed by the rules those groups
    contribute. It is what the CLI runs and what ``--list-rules`` prints. ``preset``
    records which preset produced the starting levels in ``rules``, or ``None`` when
    the configuration named none. ``baseline`` is the baseline file the project wants
    applied to every run, relative to ``root``; ``--baseline`` overrides it.
    """

    root: Path
    source: Path | None
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    rules: Mapping[str, RuleSetting]
    groups: tuple[str, ...] = ()
    registry: tuple[Rule, ...] = ()
    preset: str | None = None
    baseline: str | None = None

    def levels(self) -> dict[str, str]:
        """Rule id -> configured level, for every rule in ``registry``."""
        return {
            rule.id: self.rules[rule.id].level
            for rule in self.registry
            if rule.id in self.rules
        }

    def enabled_rules(self, registry: Sequence[Rule] | None = None) -> tuple[Rule, ...]:
        """The enabled rules of ``registry``, or of this config's own registry."""
        source = self.registry if registry is None else registry
        return tuple(
            rule
            for rule in source
            if rule.id in self.rules and self.rules[rule.id].enabled
        )


class PathFilter:
    """Glob matcher for ``exclude`` patterns.

    ``*`` matches within one path segment, ``**`` crosses segments. A path is
    excluded when the pattern matches the path itself or any of its parent
    directories, so a bare ``build`` excludes everything underneath it.
    """

    __slots__ = ("_patterns",)

    def __init__(self, patterns: Iterable[str]) -> None:
        self._patterns = tuple(_glob_to_regex(pattern) for pattern in patterns)

    def excludes(self, relative: PurePosixPath) -> bool:
        candidates = [relative.as_posix()]
        candidates.extend(
            parent.as_posix() for parent in relative.parents if parent.as_posix() != "."
        )
        return any(
            pattern.fullmatch(candidate)
            for pattern in self._patterns
            for candidate in candidates
        )


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    parts: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**/", index):
                parts.append("(?:.*/)?")
                index += 3
                continue
            if pattern.startswith("**", index):
                parts.append(".*")
                index += 2
                continue
            parts.append("[^/]*")
            index += 1
            continue
        if char == "?":
            parts.append("[^/]")
            index += 1
            continue
        parts.append(re.escape(char))
        index += 1
    return re.compile("".join(parts))


def default_config(root: Path, registry: Sequence[Rule]) -> Config:
    """Every core rule at ``error`` with default options -- the zero-config baseline.

    No configuration file means no ``groups``: opt-in rules are never on by default.
    """
    return Config(
        root=root,
        source=None,
        include=(),
        exclude=DEFAULT_EXCLUDE,
        rules=_default_settings(registry),
        groups=(),
        registry=tuple(registry),
    )


def _default_settings(
    registry: Sequence[Rule], preset: Preset | None = None
) -> dict[str, RuleSetting]:
    """Every rule at ``error``, or at the level ``preset`` gives it."""
    return {
        rule.id: _setting_at(
            rule, LEVEL_ERROR if preset is None else preset.level_for(rule.metadata)
        )
        for rule in registry
    }


def _setting_at(rule: Rule, level: str) -> RuleSetting:
    return RuleSetting(
        rule_id=rule.id,
        enabled=level != LEVEL_OFF,
        options=rule.default_options(),
        severity=_severity_of(level),
    )


def load_group(name: str, where: str) -> GroupModule:
    """Import the contrib group ``name``; a name outside :data:`KNOWN_GROUPS` fails.

    ``where`` is the configuration location quoted in the error message.
    """
    if name not in KNOWN_GROUPS:
        available = ", ".join(KNOWN_GROUPS) or "<none>"
        message = f"{where}: unknown group {name!r} (known groups: {available})"
        raise ConfigError(message)
    try:
        module = importlib.import_module(f"{CONTRIB_PACKAGE}.{name}")
    except ImportError as error:
        message = f"{where}: group {name!r} cannot be imported: {error}"
        raise ConfigError(message) from error
    # SAFETY: every name in KNOWN_GROUPS is a subpackage shipped in this distribution
    # whose __init__ declares GROUP_RULES and CORE_MESSAGE_OVERRIDES; the group tests
    # import each one through this function and read both attributes, so a group that
    # stopped declaring either would fail the suite rather than reach a user.
    return cast("GroupModule", module)


def known_rules(core: Sequence[Rule], where: str) -> tuple[Rule, ...]:
    """Every rule this distribution ships: ``core`` plus every known group's rules.

    Not an effective registry -- no message overrides are applied and nothing here is
    enabled. It exists so ``--explain`` can answer for a group's rule without the
    group being configured: an explanation is documentation, and documentation should
    not require turning a rule on first.
    """
    rules = list(core)
    for name in KNOWN_GROUPS:
        rules.extend(load_group(name, where).GROUP_RULES)
    return tuple(rules)


def resolve_registry(
    core: Sequence[Rule], groups: Sequence[str], where: str
) -> tuple[Rule, ...]:
    """The effective rule set: ``core`` (with group overrides) plus the group rules.

    Rules keep their declaration order -- core first, then each group in the order it
    was configured -- so ``--list-rules`` shows the opt-in rules after the core ones.
    A group rule whose id lacks its own group prefix, a duplicate id, and an override
    naming a rule or a message that does not exist are all configuration errors: a
    group that misdeclares itself must not half-load.
    """
    rules = list(core)
    known = {rule.id for rule in core}
    overrides: dict[str, dict[str, str]] = {}

    for name in groups:
        module = load_group(name, where)
        prefix = f"{name}/"
        for rule in module.GROUP_RULES:
            if not rule.id.startswith(prefix):
                message = (
                    f"{where}: rule {rule.id!r} of group {name!r} is missing the"
                    f" {prefix!r} id prefix"
                )
                raise ConfigError(message)
            if rule.id in known:
                message = f"{where}: group {name!r} redeclares rule {rule.id!r}"
                raise ConfigError(message)
            known.add(rule.id)
            rules.append(rule)
        for rule_id, messages in module.CORE_MESSAGE_OVERRIDES.items():
            overrides.setdefault(rule_id, {}).update(messages)

    unknown = sorted(set(overrides) - known)
    if unknown:
        message = (
            f"{where}: group message override(s) for unknown rule(s):"
            f" {', '.join(unknown)}"
        )
        raise ConfigError(message)
    return tuple(_with_overrides(rule, overrides.get(rule.id), where) for rule in rules)


def _with_overrides(
    rule: Rule, messages: Mapping[str, str] | None, where: str
) -> Rule:
    """``rule`` with ``messages`` replacing the templates of the same message ids."""
    if not messages:
        return rule
    unknown = sorted(set(messages) - set(rule.messages))
    if unknown:
        known = ", ".join(sorted(rule.messages))
        message = (
            f"{where}: message override(s) {', '.join(unknown)} do not exist on rule"
            f" {rule.id!r} (its messages: {known})"
        )
        raise ConfigError(message)
    return replace(rule, messages={**rule.messages, **messages})


def find_pyproject(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a ``pyproject.toml``."""
    current = start.resolve()
    for directory in (current, *current.parents):
        candidate = directory / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def load_config(
    *,
    registry: Sequence[Rule],
    explicit_path: Path | None = None,
    start_dir: Path | None = None,
) -> Config:
    """Load configuration, either from ``explicit_path`` or by upward discovery."""
    start = start_dir if start_dir is not None else Path.cwd()

    if explicit_path is not None:
        if not explicit_path.is_file():
            message = f"config file not found: {explicit_path}"
            raise ConfigError(message)
        return _load_from(explicit_path, registry)

    discovered = find_pyproject(start)
    if discovered is None:
        return default_config(start, registry)
    return _load_from(discovered, registry)


def _load_from(path: Path, registry: Sequence[Rule]) -> Config:
    try:
        with path.open("rb") as handle:
            document: dict[str, TomlValue] = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        message = f"{path}: invalid TOML: {error}"
        raise ConfigError(message) from error
    except OSError as error:
        message = f"{path}: cannot be read: {error}"
        raise ConfigError(message) from error

    root = path.parent
    tool_table = document.get("tool")
    if not isinstance(tool_table, dict):
        return default_config(root, registry)
    section = tool_table.get(SECTION)
    if section is None:
        return default_config(root, registry)
    if not isinstance(section, dict):
        message = f"{path}: [tool.{SECTION}] must be a table"
        raise ConfigError(message)

    unknown = sorted(set(section) - _TOP_LEVEL_KEYS)
    if unknown:
        allowed = ", ".join(sorted(_TOP_LEVEL_KEYS))
        message = (
            f"{path}: unknown key(s) in [tool.{SECTION}]: {', '.join(unknown)}"
            f" (known keys: {allowed})"
        )
        raise ConfigError(message)

    include = _read_str_list(section.get("include"), path, f"[tool.{SECTION}].include")
    exclude_raw = section.get("exclude")
    exclude = (
        DEFAULT_EXCLUDE
        if exclude_raw is None
        else _read_str_list(exclude_raw, path, f"[tool.{SECTION}].exclude")
    )

    groups = _read_groups(section.get("groups"), path)
    preset_name = _read_preset(section.get("preset"), path)
    preset = None if preset_name is None else preset_named(preset_name, str(path))
    baseline = _read_baseline(section.get("baseline"), path)
    effective = resolve_registry(registry, groups, str(path))
    rules = _read_rules(section.get("rules"), path, effective, preset)
    return Config(
        root=root,
        source=path,
        include=include,
        exclude=exclude,
        rules=rules,
        groups=groups,
        registry=effective,
        preset=preset_name,
        baseline=baseline,
    )


def _read_preset(value: TomlValue | None, path: Path) -> str | None:
    """The ``preset`` key: a string naming one of :data:`PRESETS`, or nothing."""
    if value is None:
        return None
    if not isinstance(value, str):
        message = f"{path}: [tool.{SECTION}].preset must be a string"
        raise ConfigError(message)
    return value


def _read_baseline(value: TomlValue | None, path: Path) -> str | None:
    """The ``baseline`` key: a path relative to the configuration root, or nothing."""
    if value is None:
        return None
    if not isinstance(value, str):
        message = f"{path}: [tool.{SECTION}].baseline must be a string"
        raise ConfigError(message)
    if not value.strip():
        message = f"{path}: [tool.{SECTION}].baseline must not be empty"
        raise ConfigError(message)
    return value


def _read_groups(value: TomlValue | None, path: Path) -> tuple[str, ...]:
    """The ``groups`` array, rejecting a repeated entry as the mistake it is."""
    names = _read_str_list(value, path, f"[tool.{SECTION}].groups")
    seen: set[str] = set()
    for name in names:
        if name in seen:
            message = f"{path}: [tool.{SECTION}].groups lists {name!r} twice"
            raise ConfigError(message)
        seen.add(name)
    return names


def _read_str_list(
    value: TomlValue | None, path: Path, where: str
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        message = f"{path}: {where} must be an array of strings"
        raise ConfigError(message)
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            message = f"{path}: {where} must contain only strings"
            raise ConfigError(message)
        items.append(entry)
    return tuple(items)


def _read_rules(
    value: TomlValue | None,
    path: Path,
    registry: Sequence[Rule],
    preset: Preset | None = None,
) -> dict[str, RuleSetting]:
    """The preset's levels, then ``[tool.anti-slop.rules]`` on top of them.

    An entry in ``rules`` always wins: a preset is where a project starts, not a
    ceiling on what it can say afterwards.
    """
    known = {rule.id: rule for rule in registry}
    settings = _default_settings(registry, preset)
    if value is None:
        return settings
    if not isinstance(value, dict):
        message = f"{path}: [tool.{SECTION}.rules] must be a table"
        raise ConfigError(message)

    for rule_id, raw in value.items():
        rule = known.get(rule_id)
        if rule is None:
            available = ", ".join(sorted(known)) or "<none>"
            message = (
                f"{path}: unknown rule {rule_id!r} in [tool.{SECTION}.rules]"
                f" (known rules: {available})"
            )
            raise ConfigError(message)
        settings[rule_id] = _read_rule_setting(
            rule, raw, path, settings[rule_id].level
        )
    return settings


def _read_rule_setting(
    rule: Rule, raw: TomlValue, path: Path, fallback_level: str = LEVEL_ERROR
) -> RuleSetting:
    """One ``[tool.anti-slop.rules]`` entry, resolved.

    ``fallback_level`` is the level the rule already had -- the preset's, or ``error``
    without one -- and it applies to the table form when no ``level`` key is given, so
    that setting an option does not silently re-raise a rule the preset lowered.
    """
    where = f"[tool.{SECTION}.rules].{rule.id}"
    options = rule.default_options()

    if isinstance(raw, str):
        level = _read_level(raw, path, where)
        return RuleSetting(
            rule_id=rule.id,
            enabled=level != LEVEL_OFF,
            options=options,
            severity=_severity_of(level),
        )

    if not isinstance(raw, dict):
        levels = " | ".join(repr(level) for level in _LEVELS)
        message = (
            f"{path}: {where} must be {levels} or a table with a 'level' key"
        )
        raise ConfigError(message)

    level = fallback_level
    for key, entry in raw.items():
        if key == "level":
            if not isinstance(entry, str):
                message = f"{path}: {where}.level must be a string"
                raise ConfigError(message)
            level = _read_level(entry, path, f"{where}.level")
            continue
        spec = rule.option_spec(key)
        if spec is None:
            available = ", ".join(sorted(rule.option_names())) or "<none>"
            message = (
                f"{path}: unknown option {key!r} for rule {rule.id!r}"
                f" (known options: {available})"
            )
            raise ConfigError(message)
        options[key] = _read_option(spec, entry, path, f"{where}.{key}")

    return RuleSetting(
        rule_id=rule.id,
        enabled=level != LEVEL_OFF,
        options=options,
        severity=_severity_of(level),
    )


def _read_level(value: str, path: Path, where: str) -> str:
    if value not in _LEVELS:
        levels = " | ".join(repr(level) for level in _LEVELS)
        message = f"{path}: {where} must be one of {levels}, got {value!r}"
        raise ConfigError(message)
    return value


def _severity_of(level: str) -> Literal["error", "warn"]:
    """``level`` as a ``RuleSetting.severity``; irrelevant (defaults to error) for off."""
    return "warn" if level == LEVEL_WARN else "error"


def _read_option(
    spec: BoolOption | StrListOption, value: TomlValue, path: Path, where: str
) -> OptionValue:
    if isinstance(spec, BoolOption):
        if not isinstance(value, bool):
            message = f"{path}: {where} must be a boolean"
            raise ConfigError(message)
        return value
    if not isinstance(value, list):
        message = f"{path}: {where} must be an array of strings"
        raise ConfigError(message)
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            message = f"{path}: {where} must contain only strings"
            raise ConfigError(message)
        items.append(entry)
    return tuple(items)
