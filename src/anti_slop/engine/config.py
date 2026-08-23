"""Configuration: the ``[tool.anti-slop]`` section of ``pyproject.toml`` (PLAN.md FR-2).

Everything invalid is loud: an unknown rule id, an unknown option, a wrongly typed
option or an unknown level is a configuration error (CLI exit code 2), never a
silently ignored line.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from anti_slop.engine.rule import BoolOption, OptionValue, Rule, StrListOption

__all__ = [
    "DEFAULT_EXCLUDE",
    "Config",
    "ConfigError",
    "PathFilter",
    "RuleSetting",
    "load_config",
]

# TOML values, as far as this tool needs to understand them.
type TomlValue = str | int | float | bool | list[TomlValue] | dict[str, TomlValue]

SECTION = "anti-slop"

LEVEL_ERROR = "error"
LEVEL_OFF = "off"
_LEVELS = (LEVEL_ERROR, LEVEL_OFF)

_TOP_LEVEL_KEYS = frozenset({"include", "exclude", "rules"})

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
    """The resolved configuration of one rule: on/off plus fully defaulted options."""

    rule_id: str
    enabled: bool
    options: Mapping[str, OptionValue]


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved configuration.

    ``root`` is the directory the configuration applies to (the directory holding
    ``pyproject.toml``, or the working directory when no configuration was found).
    """

    root: Path
    source: Path | None
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    rules: Mapping[str, RuleSetting]

    def enabled_rules(self, registry: Sequence[Rule]) -> tuple[Rule, ...]:
        return tuple(
            rule
            for rule in registry
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
    """Every core rule at ``error`` with default options -- the zero-config baseline."""
    return Config(
        root=root,
        source=None,
        include=(),
        exclude=DEFAULT_EXCLUDE,
        rules={
            rule.id: RuleSetting(
                rule_id=rule.id, enabled=True, options=rule.default_options()
            )
            for rule in registry
        },
    )


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

    rules = _read_rules(section.get("rules"), path, registry)
    return Config(
        root=root, source=path, include=include, exclude=exclude, rules=rules
    )


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
    value: TomlValue | None, path: Path, registry: Sequence[Rule]
) -> dict[str, RuleSetting]:
    known = {rule.id: rule for rule in registry}
    settings = {
        rule.id: RuleSetting(
            rule_id=rule.id, enabled=True, options=rule.default_options()
        )
        for rule in registry
    }
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
        settings[rule_id] = _read_rule_setting(rule, raw, path)
    return settings


def _read_rule_setting(rule: Rule, raw: TomlValue, path: Path) -> RuleSetting:
    where = f"[tool.{SECTION}.rules].{rule.id}"
    options = rule.default_options()

    if isinstance(raw, str):
        level = _read_level(raw, path, where)
        return RuleSetting(rule_id=rule.id, enabled=level == LEVEL_ERROR, options=options)

    if not isinstance(raw, dict):
        levels = " | ".join(repr(level) for level in _LEVELS)
        message = (
            f"{path}: {where} must be {levels} or a table with a 'level' key"
        )
        raise ConfigError(message)

    level = LEVEL_ERROR
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

    return RuleSetting(rule_id=rule.id, enabled=level == LEVEL_ERROR, options=options)


def _read_level(value: str, path: Path, where: str) -> str:
    if value not in _LEVELS:
        levels = " | ".join(repr(level) for level in _LEVELS)
        message = f"{path}: {where} must be one of {levels}, got {value!r}"
        raise ConfigError(message)
    return value


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
