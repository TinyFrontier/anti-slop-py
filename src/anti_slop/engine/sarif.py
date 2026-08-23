"""SARIF 2.1.0 output: one machine-readable JSON document for CI systems and
static-analysis viewers that already speak the OASIS format.

The document mirrors ``format_json`` (FR-4, in ``runner.py``) in what it reports --
the same diagnostics, in the same sorted order, the same severity mapping -- but
shapes it into SARIF's ``run`` model: one ``tool.driver.rules`` entry per rule that
produced at least one result (never the whole registry, so a reader never has to
cross-reference rules nothing found), and one ``results`` entry per diagnostic with a
``SRCROOT``-relative artifact location.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from anti_slop.engine.catalog import listing_key
from anti_slop.engine.rule import Diagnostic, Rule

__all__ = ["SARIF_SCHEMA_URI", "SARIF_VERSION", "format_sarif"]

SARIF_VERSION = "2.1.0"

# The OASIS-published schema document for SARIF 2.1.0 (errata01); it answers with a
# `Link: rel="canonical"` response header naming this exact URL, so it is the
# canonical `$schema` rather than one of the several mirrors tools also use.
SARIF_SCHEMA_URI = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/"
    "sarif-schema-2.1.0.json"
)

_TOOL_NAME = "anti-slop"
_INFORMATION_URI = "https://github.com/TinyFrontier/anti-slop-py"

# Every rule's `helpUri` points at the same anchor rather than a per-rule slug:
# GitHub's heading-to-anchor algorithm is unspecified for an id containing "/"
# (every `fastapi/...` rule), so guessing a per-rule slug risks a link that quietly
# 404s. The "Rules" section lists every rule by id under one well-known, stable
# anchor instead.
_RULES_SECTION_URI = f"{_INFORMATION_URI}#rules"

_SRCROOT = "SRCROOT"

_LEVEL_WARNING = "warning"
_LEVEL_ERROR = "error"

# The JSON shape, built bottom-up so every literal below type-checks against a
# precise union instead of `object`/`Any`.
type TextJson = dict[str, str]
type ArtifactLocationJson = dict[str, str]
type RegionJson = dict[str, int]
type PhysicalLocationJson = dict[str, ArtifactLocationJson | RegionJson]
type LocationJson = dict[str, PhysicalLocationJson]
type RulePropertiesJson = dict[str, str | list[str]]
type SarifRuleJson = dict[str, str | TextJson | RulePropertiesJson]
type ResultJson = dict[str, str | int | TextJson | list[LocationJson]]
type DriverJson = dict[str, str | list[SarifRuleJson]]
type ToolJson = dict[str, DriverJson]
type UriBaseJson = dict[str, str]
type OriginalUriBaseIdsJson = dict[str, UriBaseJson]
type RunJson = dict[str, ToolJson | OriginalUriBaseIdsJson | list[ResultJson]]
type SarifDocumentJson = dict[str, str | list[RunJson]]


def format_sarif(
    diagnostics: Sequence[Diagnostic],
    rules: Sequence[Rule],
    root: Path,
    *,
    tool_version: str,
) -> str:
    """A single SARIF 2.1.0 document, one run, over ``diagnostics``.

    ``rules`` is the rule set the run executed under; only the rules that produced at
    least one diagnostic are reproduced in ``tool.driver.rules``, sorted by
    :func:`~anti_slop.engine.catalog.listing_key` -- SARIF readers resolve a result's
    ``ruleId``/``ruleIndex`` against that array, not against every rule this
    distribution ships. ``root`` becomes ``SRCROOT`` in ``originalUriBaseIds``, and
    every artifact location is written relative to it.

    ``diagnostics`` is expected already sorted the way :func:`~anti_slop.engine.
    runner.format_json` expects it -- by ``(path, line, col, rule_id)`` -- and is
    reproduced in that order, so calling this twice on the same run produces
    byte-identical output. An empty ``diagnostics`` still produces a complete,
    valid document with empty ``rules``/``results`` arrays.
    """
    resolved_root = root.resolve()
    used_ids = {diagnostic.rule_id for diagnostic in diagnostics}
    used_rules = sorted((rule for rule in rules if rule.id in used_ids), key=listing_key)
    rule_index = {rule.id: index for index, rule in enumerate(used_rules)}

    document: SarifDocumentJson = {
        "version": SARIF_VERSION,
        "$schema": SARIF_SCHEMA_URI,
        "runs": [_run_entry(diagnostics, used_rules, rule_index, resolved_root, tool_version)],
    }
    return json.dumps(document)


def _run_entry(
    diagnostics: Sequence[Diagnostic],
    used_rules: Sequence[Rule],
    rule_index: Mapping[str, int],
    root: Path,
    tool_version: str,
) -> RunJson:
    return {
        "tool": {"driver": _driver_entry(used_rules, tool_version)},
        "originalUriBaseIds": {_SRCROOT: {"uri": f"{root.as_uri()}/"}},
        "results": [_result_entry(diagnostic, rule_index, root) for diagnostic in diagnostics],
    }


def _driver_entry(used_rules: Sequence[Rule], tool_version: str) -> DriverJson:
    return {
        "name": _TOOL_NAME,
        "informationUri": _INFORMATION_URI,
        "version": tool_version,
        "rules": [_rule_entry(rule) for rule in used_rules],
    }


def _rule_entry(rule: Rule) -> SarifRuleJson:
    metadata = rule.metadata
    return {
        "id": rule.id,
        "shortDescription": {"text": rule.description},
        "fullDescription": {"text": metadata.problem},
        "help": {"text": metadata.recipe},
        "helpUri": _RULES_SECTION_URI,
        "properties": {
            "tier": metadata.tier,
            "confidence": metadata.confidence,
            "tags": list(metadata.tags),
        },
    }


def _result_entry(
    diagnostic: Diagnostic, rule_index: Mapping[str, int], root: Path
) -> ResultJson:
    level = _LEVEL_WARNING if diagnostic.severity == "warn" else _LEVEL_ERROR
    # Wave 2: a `partialFingerprints` entry -- a content hash of the anchored region
    # that survives line churn -- belongs here, once the analyzer can compute one.
    return {
        "ruleId": diagnostic.rule_id,
        "ruleIndex": rule_index[diagnostic.rule_id],
        "level": level,
        "message": {"text": diagnostic.message},
        "locations": [_location_entry(diagnostic, root)],
    }


def _location_entry(diagnostic: Diagnostic, root: Path) -> LocationJson:
    return {
        "physicalLocation": {
            "artifactLocation": _artifact_location(diagnostic.path, root),
            "region": {
                "startLine": diagnostic.line,
                "startColumn": diagnostic.col,
                "endLine": diagnostic.end_line,
                "endColumn": diagnostic.end_col,
            },
        }
    }


def _artifact_location(path: Path, root: Path) -> ArtifactLocationJson:
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        # A target outside `root` (e.g. linting another checkout by absolute path)
        # has no SRCROOT-relative form. An absolute file:// URI is still a valid
        # artifactLocation on its own, just without a uriBaseId -- the same case
        # `runner._is_excluded` falls back for when matching exclude patterns.
        return {"uri": path.resolve().as_uri()}
    return {"uri": relative.as_posix(), "uriBaseId": _SRCROOT}
