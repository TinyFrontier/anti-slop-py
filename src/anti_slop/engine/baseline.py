"""Baseline mode: record today's findings so only tomorrow's fail the build.

A baseline file is a JSON document naming the findings a project has decided not to
fix yet::

    {
      "version": 1,
      "entries": {
        "0d61f8370cad1d41": 3,
        "1b6453892473a467": 1
      }
    }

Keys are :mod:`~anti_slop.engine.fingerprint` values; the value is how many findings
carried that fingerprint when the baseline was written. Counting, rather than listing
each finding, is what makes the file stable: inserting a violation identical to one
already recorded reports exactly one new finding instead of renumbering every later
hit. The keys are sorted and the document is indented, so a regenerated baseline
produces a readable diff in a pull request rather than one enormous line.

Applying a baseline hides up to ``count`` matches of each fingerprint, in the order
the diagnostics come in. Anything beyond the recorded count is reported normally.
Hidden findings are removed before the exit code is computed and before any format is
rendered, so a baselined run is genuinely clean rather than clean-with-a-flag.

An entry whose count exceeds the matches actually found is *stale* -- the violations
it recorded were fixed, or moved, or reformatted. Stale entries are counted and
mentioned in the run summary but are never an error: a build must not start failing
because somebody cleaned code up.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from anti_slop.engine.config import ConfigError
from anti_slop.engine.fingerprint import FINGERPRINT_LENGTH
from anti_slop.engine.rule import Diagnostic

__all__ = [
    "BASELINE_VERSION",
    "DEFAULT_BASELINE_NAME",
    "Baseline",
    "BaselineOutcome",
    "apply_baseline",
    "load_baseline",
    "render_baseline",
    "write_baseline",
]

# The on-disk format version. It exists so that a change to the fingerprint recipe or
# the entry shape is rejected loudly by an older or newer build instead of silently
# hiding the wrong findings.
BASELINE_VERSION = 1

DEFAULT_BASELINE_NAME = ".anti-slop-baseline.json"

_VERSION_KEY = "version"
_ENTRIES_KEY = "entries"
_TOP_LEVEL_KEYS = frozenset({_VERSION_KEY, _ENTRIES_KEY})

_HEX_DIGITS = frozenset("0123456789abcdef")

# JSON values, as far as this module needs to understand them -- the same shape
# `config.py` declares for TOML, for the same reason: every branch below narrows a
# parsed document explicitly instead of trusting it.
type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)


@dataclass(frozen=True, slots=True)
class Baseline:
    """A loaded baseline: fingerprint -> how many findings it recorded."""

    entries: Mapping[str, int]

    @property
    def total(self) -> int:
        """How many individual findings this baseline records."""
        return sum(self.entries.values())


@dataclass(frozen=True, slots=True)
class BaselineOutcome:
    """What a baseline did to one run's diagnostics."""

    diagnostics: tuple[Diagnostic, ...]
    hidden: int
    stale: int

    def summary(self) -> str | None:
        """The one-line run summary, or ``None`` when there is nothing to say."""
        if self.hidden == 0 and self.stale == 0:
            return None
        return (
            f"{self.hidden} baselined findings hidden"
            f" ({self.stale} stale entries)"
        )


def render_baseline(fingerprints: Sequence[str]) -> str:
    """The baseline document for ``fingerprints``, sorted, indented, newline-ended."""
    counts: dict[str, int] = {}
    for value in fingerprints:
        counts[value] = counts.get(value, 0) + 1
    document = {
        _VERSION_KEY: BASELINE_VERSION,
        _ENTRIES_KEY: {key: counts[key] for key in sorted(counts)},
    }
    return f"{json.dumps(document, indent=2, sort_keys=True)}\n"


def write_baseline(path: Path, fingerprints: Sequence[str]) -> int:
    """Write the baseline for ``fingerprints`` to ``path``; return the finding count.

    The file is overwritten wholesale -- regenerating a baseline is how a project
    accepts its current state, not a merge of old and new.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_baseline(fingerprints), encoding="utf-8")
    except OSError as error:
        message = f"cannot write baseline {path}: {error}"
        raise ConfigError(message) from error
    return len(fingerprints)


def load_baseline(path: Path) -> Baseline:
    """Read and validate the baseline at ``path``.

    Every malformed shape is a configuration error, never a shrug: a baseline that
    cannot be understood would otherwise hide an arbitrary set of findings.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        message = (
            f"baseline file not found: {path}"
            " (generate one with --generate-baseline)"
        )
        raise ConfigError(message) from error
    except (OSError, UnicodeDecodeError) as error:
        message = f"{path}: cannot be read: {error}"
        raise ConfigError(message) from error

    try:
        document: JsonValue = json.loads(text)
    except json.JSONDecodeError as error:
        message = f"{path}: invalid JSON: {error}"
        raise ConfigError(message) from error

    if not isinstance(document, dict):
        message = f"{path}: a baseline must be a JSON object"
        raise ConfigError(message)
    return Baseline(entries=_read_entries(document, path))


def _read_entries(document: Mapping[str, JsonValue], path: Path) -> dict[str, int]:
    unknown = sorted(set(document) - _TOP_LEVEL_KEYS)
    if unknown:
        allowed = ", ".join(sorted(_TOP_LEVEL_KEYS))
        message = (
            f"{path}: unknown key(s) in the baseline: {', '.join(unknown)}"
            f" (known keys: {allowed})"
        )
        raise ConfigError(message)

    version = document.get(_VERSION_KEY)
    if version != BASELINE_VERSION:
        message = (
            f"{path}: baseline version {version!r} is not supported"
            f" (this build writes and reads version {BASELINE_VERSION});"
            " regenerate it with --generate-baseline"
        )
        raise ConfigError(message)

    entries = document.get(_ENTRIES_KEY)
    if not isinstance(entries, dict):
        message = f"{path}: baseline '{_ENTRIES_KEY}' must be an object"
        raise ConfigError(message)

    resolved: dict[str, int] = {}
    for key, value in entries.items():
        _check_fingerprint(key, path)
        resolved[key] = _check_count(key, value, path)
    return resolved


def _check_fingerprint(key: str, path: Path) -> None:
    if len(key) == FINGERPRINT_LENGTH and set(key) <= _HEX_DIGITS:
        return
    message = (
        f"{path}: baseline key {key!r} is not a fingerprint"
        f" ({FINGERPRINT_LENGTH} lowercase hex digits)"
    )
    raise ConfigError(message)


def _check_count(key: str, value: JsonValue, path: Path) -> int:
    # `bool` is a subclass of `int`, and `true` is not a count.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        message = (
            f"{path}: baseline count for {key!r} must be a positive integer,"
            f" got {value!r}"
        )
        raise ConfigError(message)
    return value


def apply_baseline(
    baseline: Baseline,
    diagnostics: Sequence[Diagnostic],
    fingerprints: Sequence[str],
    *,
    whole_project: bool = True,
) -> BaselineOutcome:
    """Hide up to the recorded count of each fingerprint; report everything beyond it.

    ``fingerprints`` is positional against ``diagnostics`` -- entry *i* is the
    fingerprint of diagnostic *i* -- which is how duplicate findings in one file stay
    distinguishable from one another.

    ``whole_project=False`` says the diagnostics are a subset of what the project
    holds -- a diff-scoped run. Staleness is then unobservable rather than zero:
    every entry for a file the run never looked at would be counted stale, on every
    run, which is noise dressed up as information. The count is reported as zero
    instead, and a project learns about stale entries from its full runs.
    """
    remaining = dict(baseline.entries)
    kept: list[Diagnostic] = []
    hidden = 0

    for diagnostic, value in zip(diagnostics, fingerprints, strict=True):
        budget = remaining.get(value, 0)
        if budget > 0:
            remaining[value] = budget - 1
            hidden += 1
            continue
        kept.append(diagnostic)

    stale = (
        sum(1 for budget in remaining.values() if budget > 0) if whole_project else 0
    )
    return BaselineOutcome(diagnostics=tuple(kept), hidden=hidden, stale=stale)
