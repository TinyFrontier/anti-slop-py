"""``anti-slop review``: one change, read back as a review.

The subcommand runs nothing new. It composes three things that already exist -- the
working-tree diff (``engine/diff.py``), the confidence and prose of each rule's
metadata (``engine/rule.py``), and the ``agent`` preset -- into the shape a reviewer
reads: the findings of *this* change, grouped by how much each one can be trusted,
each carrying the reason it exists and the recipe that replaces it.

Sections are ordered ``HIGH CONFIDENCE`` -> ``MEDIUM CONFIDENCE`` -> ``POLICY`` and
an empty one is omitted, so the first thing on screen is the finding least worth
arguing with. Within a section the findings keep their run order -- by path, then
position -- so a review reads down a file the way a diff does.

``Why`` and ``Instead`` are the opening sentences of the rule's own ``problem`` and
``recipe`` blocks, selected by :func:`~anti_slop.engine.catalog.condense`. Nothing
here inspects the patch to generate prose about it: the standard diagnostic message
is already the contextual part -- it names the identifier, the annotation, the call --
and everything around it is the rule's fixed rationale. A review that invented an
explanation per hunk would be a language model with a linter's exit code.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from anti_slop.engine.catalog import condense
from anti_slop.engine.config import ConfigError
from anti_slop.engine.rule import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_POLICY,
    Diagnostic,
    Rule,
)

__all__ = [
    "SECTIONS",
    "ReviewFinding",
    "review_findings",
    "review_json",
    "review_summary",
    "review_text",
]

# Confidence -> section heading, in the order a review prints them.
SECTIONS: tuple[tuple[str, str], ...] = (
    (CONFIDENCE_HIGH, "HIGH CONFIDENCE"),
    (CONFIDENCE_MEDIUM, "MEDIUM CONFIDENCE"),
    (CONFIDENCE_POLICY, "POLICY"),
)

_INDENT = "  "

# The same marker `format_text` puts on a warn-severity diagnostic: a review does not
# invent a second vocabulary for the levels the configuration already speaks.
_WARNING_MARKER = "warning: "

_CLEAN_SUMMARY = "No findings on the lines this change touched."

# One finding, as `--format json` shapes it: the eight keys of a diff run plus the
# four this mode adds. The union is exact rather than `object` -- this file is linted
# by the rules it reports.
type FindingJson = dict[str, str | int]
type ReviewJson = dict[str, str | list[FindingJson]]


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """One diagnostic with the metadata a review shows next to it.

    ``path`` is POSIX and relative to the configuration root wherever the file lies
    under it -- the form a reviewer recognises, and the same form SARIF uses. Both
    output formats of this mode use it, so text and JSON name a file identically.
    """

    diagnostic: Diagnostic
    path: str
    confidence: str
    tier: str
    why: str
    instead: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.diagnostic.line}"


def review_findings(
    diagnostics: Sequence[Diagnostic], rules: Sequence[Rule], root: Path
) -> tuple[ReviewFinding, ...]:
    """Pair each diagnostic with its rule's metadata, in section order.

    ``rules`` is the registry the run used, so every diagnostic's rule is in it; a
    diagnostic naming a rule that is not is a bug in this program rather than a
    finding, and says so instead of being dropped quietly.
    """
    by_id = {rule.id: rule for rule in rules}
    findings = [_finding(diagnostic, by_id, root) for diagnostic in diagnostics]
    return _in_section_order(findings)


def review_text(findings: Sequence[ReviewFinding]) -> str:
    """The review itself: a block per non-empty section, blank line between them."""
    blocks: list[str] = []
    for confidence, heading in SECTIONS:
        section = [finding for finding in findings if finding.confidence == confidence]
        if not section:
            continue
        lines = [heading]
        for finding in section:
            lines.extend(_entry(finding))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def review_summary(findings: Sequence[ReviewFinding]) -> str:
    """The closing line: how many findings, per section, and how many block the run."""
    if not findings:
        return _CLEAN_SUMMARY
    parts: list[str] = []
    for confidence, heading in SECTIONS:
        count = sum(1 for finding in findings if finding.confidence == confidence)
        if count:
            parts.append(f"{count} {heading.lower()}")
    blocking = sum(
        1 for finding in findings if finding.diagnostic.severity != "warn"
    )
    warned = len(findings) - blocking
    return (
        f"{_plural(len(findings), 'finding')}: {', '.join(parts)}"
        f" ({blocking} blocking, {_plural(warned, 'warning')})"
    )


def review_json(
    base: str, preset: str, findings: Sequence[ReviewFinding]
) -> str:
    """The review as one document, for a harness rather than a reader.

    ``base`` and ``preset`` are the two facts a consumer cannot reconstruct from the
    findings: what the change was compared against, and which posture judged it. The
    findings come in the same order the text renders them, so the two outputs of one
    run can be read against each other line by line.
    """
    payload: ReviewJson = {
        "base": base,
        "preset": preset,
        "findings": [_finding_entry(finding) for finding in findings],
    }
    return json.dumps(payload, indent=2)


def _finding(
    diagnostic: Diagnostic, by_id: Mapping[str, Rule], root: Path
) -> ReviewFinding:
    rule = by_id.get(diagnostic.rule_id)
    if rule is None:
        message = (
            f"internal error: a diagnostic names rule {diagnostic.rule_id!r},"
            " which is not in the registry this run used"
        )
        raise ConfigError(message)
    metadata = rule.metadata
    return ReviewFinding(
        diagnostic=diagnostic,
        path=_relative(diagnostic.path, root),
        confidence=metadata.confidence,
        tier=metadata.tier,
        why=condense(metadata.problem),
        instead=condense(metadata.recipe),
    )


def _in_section_order(
    findings: Sequence[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    """Grouped by confidence, each group keeping the run's own path/position order."""
    ranks = {confidence: rank for rank, (confidence, _) in enumerate(SECTIONS)}
    return tuple(
        sorted(findings, key=lambda finding: ranks.get(finding.confidence, len(ranks)))
    )


def _entry(finding: ReviewFinding) -> tuple[str, ...]:
    diagnostic = finding.diagnostic
    marker = _WARNING_MARKER if diagnostic.severity == "warn" else ""
    return (
        f"{finding.location} {diagnostic.rule_id}",
        f"{_INDENT}{marker}{diagnostic.message}",
        f"{_INDENT}Why: {finding.why}",
        f"{_INDENT}Instead: {finding.instead}",
    )


def _finding_entry(finding: ReviewFinding) -> FindingJson:
    diagnostic = finding.diagnostic
    return {
        "path": finding.path,
        "line": diagnostic.line,
        "col": diagnostic.col,
        "endLine": diagnostic.end_line,
        "endCol": diagnostic.end_col,
        "rule": diagnostic.rule_id,
        "severity": diagnostic.severity,
        "message": diagnostic.message,
        "confidence": finding.confidence,
        "tier": finding.tier,
        "why": finding.why,
        "instead": finding.instead,
    }


def _relative(path: Path, root: Path) -> str:
    """``path`` as a POSIX path under ``root``; anything outside stays as written."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
