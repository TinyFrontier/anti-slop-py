"""RuleTester-style harness: run one rule over a snippet.

``assert_valid`` and ``assert_invalid`` drive the real engine -- comment map,
walker, context, suppressions -- with a single rule enabled, so a rule test
exercises the same path the CLI does.
"""

from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from pathlib import Path

from anti_slop.engine.config import RuleSetting
from anti_slop.engine.rule import (
    CONFIDENCE_HIGH,
    FIX_NONE,
    TIER_ESCAPE_HATCH,
    Diagnostic,
    OptionValue,
    Rule,
    RuleMetadata,
)
from anti_slop.engine.runner import check_source

__all__ = [
    "PROBE_METADATA",
    "SNIPPET_PATH",
    "assert_invalid",
    "assert_valid",
    "run_rule",
]

SNIPPET_PATH = Path("snippet.py")

# Metadata for the probe rules a few engine tests build to drive the walker and the
# scope table. Every field of `RuleMetadata` is mandatory, and a probe is a `Rule`
# like any other; this constant keeps that requirement from being restated in every
# test module that needs a throwaway rule.
PROBE_METADATA = RuleMetadata(
    tier=TIER_ESCAPE_HATCH,
    confidence=CONFIDENCE_HIGH,
    fix=FIX_NONE,
    tags=("testing",),
    problem="Test-only probe rule: it drives the engine, it does not judge code.",
    recipe="Nothing to write instead -- a probe never runs outside the test suite.",
    when_to_disable="Never: a probe is constructed in a test, never configured.",
    fp_caveats="Reports exactly what its own handlers report, by construction.",
)


def run_rule(
    rule: Rule,
    snippet: str,
    options: Mapping[str, OptionValue] | None = None,
) -> tuple[Diagnostic, ...]:
    """Run ``rule`` alone over ``snippet`` and return the diagnostics."""
    resolved = rule.default_options()
    if options:
        unknown = sorted(set(options) - rule.option_names())
        if unknown:
            message = f"rule {rule.id!r} has no option(s): {', '.join(unknown)}"
            raise ValueError(message)
        resolved.update(options)

    setting = RuleSetting(rule_id=rule.id, enabled=True, options=resolved)
    return check_source(
        path=SNIPPET_PATH,
        source=_normalize(snippet),
        rules=(rule,),
        settings={rule.id: setting},
    )


def assert_valid(
    rule: Rule,
    snippets: Sequence[str],
    options: Mapping[str, OptionValue] | None = None,
) -> None:
    """Assert that ``rule`` reports nothing for every snippet."""
    for snippet in snippets:
        diagnostics = run_rule(rule, snippet, options)
        assert not diagnostics, (
            f"expected {rule.id!r} to accept this snippet, got"
            f" {len(diagnostics)} diagnostic(s):\n"
            f"{_describe(snippet, diagnostics)}"
        )


def assert_invalid(
    rule: Rule,
    snippet: str,
    count: int | None = None,
    options: Mapping[str, OptionValue] | None = None,
) -> tuple[Diagnostic, ...]:
    """Assert that ``rule`` reports ``count`` (or at least one) violation."""
    diagnostics = run_rule(rule, snippet, options)
    expected = 1 if count is None else count
    assert len(diagnostics) == expected, (
        f"expected {rule.id!r} to report {expected} diagnostic(s), got"
        f" {len(diagnostics)}:\n{_describe(snippet, diagnostics)}"
    )
    for diagnostic in diagnostics:
        assert diagnostic.rule_id == rule.id
        assert diagnostic.message, "every diagnostic must carry a message"
    return diagnostics


def _normalize(snippet: str) -> str:
    return textwrap.dedent(snippet).lstrip("\n").rstrip() + "\n"


def _describe(snippet: str, diagnostics: Sequence[Diagnostic]) -> str:
    rendered = "\n".join(
        f"  {diagnostic.line}:{diagnostic.col} {diagnostic.rule_id}"
        f" {diagnostic.message}"
        for diagnostic in diagnostics
    )
    return f"--- snippet ---\n{_normalize(snippet)}--- diagnostics ---\n{rendered}"
