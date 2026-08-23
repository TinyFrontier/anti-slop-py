"""Command line entry point: ``python -m anti_slop [paths...]``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from anti_slop import CORE_RULES, __version__
from anti_slop.engine.catalog import (
    explain_json,
    explain_text,
    rule_list_json,
    rule_list_text,
    similar_rule_ids,
)
from anti_slop.engine.config import (
    PRESETS,
    Config,
    ConfigError,
    known_rules,
    load_config,
)
from anti_slop.engine.rule import Diagnostic, Rule
from anti_slop.engine.runner import (
    EXIT_ERROR,
    collect_files,
    format_github,
    format_json,
    format_text,
    resolve_roots,
    run,
)
from anti_slop.engine.sarif import format_sarif

__all__ = ["cli", "main"]

PROG = "anti-slop"

_FORMATS = ("text", "json", "github", "sarif")

# `--list-rules` and `--explain` describe rules rather than findings, so they answer
# in the two formats that can carry a description; a diagnostics-only format is
# rejected instead of silently falling back to text.
_CATALOG_FORMATS = ("text", "json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Reject low-evidence, low-signal Python patterns. Every diagnostic says"
            " what to write instead."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="files or directories to check; defaults to [tool.anti-slop].include",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="pyproject.toml to read; default is upward discovery from the cwd",
    )
    parser.add_argument(
        "--rule",
        action="append",
        default=None,
        metavar="ID",
        dest="rules",
        help="only run this rule (repeatable)",
    )
    parser.add_argument(
        "--format",
        choices=_FORMATS,
        default="text",
        dest="output_format",
        help="output format",
    )
    parser.add_argument(
        "--list-rules",
        action="store_true",
        help=(
            "list the active rules -- core plus the rules of every enabled group --"
            " with their tier, confidence and options, and exit"
        ),
    )
    parser.add_argument(
        "--explain",
        default=None,
        metavar="RULE",
        help=(
            "print what a rule catches, why it matters, what to write instead, when"
            " to turn it off and its known false positives, and exit; works for the"
            " rules of an opt-in group without enabling the group"
        ),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        metavar="N",
        help=(
            "worker processes for the file walk; files above the parallel"
            " threshold stay sequential only with --jobs 1 (default: auto)"
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # `--explain` is answered before any configuration is read: it describes a rule,
    # and which rules exist for it is every rule this distribution ships, group rules
    # included. A repository with a broken pyproject.toml can still ask what a rule
    # means.
    if args.explain is not None:
        try:
            return _explain(args.explain, args.output_format)
        except ConfigError as error:
            print(f"{PROG}: {error}", file=sys.stderr)
            return EXIT_ERROR

    try:
        config = load_config(registry=CORE_RULES, explicit_path=args.config)
    except ConfigError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    # `--list-rules` reads the configuration first: which rules exist depends on
    # `[tool.anti-slop].groups` and which levels they run at depends on
    # `[tool.anti-slop].preset`, so the listing has to be the configured one.
    if args.list_rules:
        try:
            print(_render_rule_list(config, args.output_format))
        except ConfigError as error:
            print(f"{PROG}: {error}", file=sys.stderr)
            return EXIT_ERROR
        return 0

    try:
        rules = _select_rules(config, args.rules)
        roots = resolve_roots(args.paths, config)
        files = collect_files(roots, config)
        jobs = _validate_jobs(args.jobs)
    except ConfigError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    try:
        outcome = run(files, rules, config.rules, jobs=jobs)
    except ConfigError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    _print_diagnostics(
        outcome.diagnostics, args.output_format, rules=rules, root=config.root
    )
    for failure in outcome.failures:
        print(f"{PROG}: {failure}", file=sys.stderr)
    return outcome.exit_code()


def _validate_jobs(jobs: int | None) -> int | None:
    if jobs is not None and jobs < 1:
        message = f"--jobs must be a positive integer, got {jobs}"
        raise ConfigError(message)
    return jobs


def _print_diagnostics(
    diagnostics: Sequence[Diagnostic],
    output_format: str,
    *,
    rules: Sequence[Rule],
    root: Path,
) -> None:
    if output_format == "json":
        print(format_json(diagnostics))
        return
    if output_format == "sarif":
        print(format_sarif(diagnostics, rules, root, tool_version=__version__))
        return
    if not diagnostics:
        return
    if output_format == "github":
        print(format_github(diagnostics))
        return
    print(format_text(diagnostics))


def _select_rules(config: Config, requested: Sequence[str] | None) -> tuple[Rule, ...]:
    enabled = config.enabled_rules()
    if not requested:
        return enabled
    known = {rule.id for rule in config.registry}
    unknown = sorted(set(requested) - known)
    if unknown:
        available = ", ".join(sorted(known)) or "<none>"
        message = (
            f"unknown rule(s) passed to --rule: {', '.join(unknown)}"
            f" (known rules: {available})"
        )
        raise ConfigError(message)
    wanted = set(requested)
    return tuple(rule for rule in enabled if rule.id in wanted)


def _render_rule_list(config: Config, output_format: str) -> str:
    """Core rules first, then each enabled group's rules, alphabetical within both."""
    _require_catalog_format(output_format, "--list-rules")
    if output_format == "json":
        return rule_list_json(config.registry, config.levels())
    return rule_list_text(config.registry)


def _explain(rule_id: str, output_format: str) -> int:
    """Print one rule's five explain sections; an unknown id exits with 2."""
    _require_catalog_format(output_format, "--explain")
    registry = known_rules(CORE_RULES, PROG)
    for rule in registry:
        if rule.id != rule_id:
            continue
        print(explain_json(rule) if output_format == "json" else explain_text(rule))
        return 0

    suggestions = similar_rule_ids(rule_id, (rule.id for rule in registry))
    hint = f"; did you mean: {', '.join(suggestions)}" if suggestions else ""
    message = (
        f"unknown rule {rule_id!r} passed to --explain{hint}"
        " (run --list-rules for the rules of this configuration)"
    )
    raise ConfigError(message)


def _require_catalog_format(output_format: str, flag: str) -> None:
    if output_format not in _CATALOG_FORMATS:
        available = " or ".join(_CATALOG_FORMATS)
        message = (
            f"{flag} describes rules, not findings:"
            f" --format must be {available}, got {output_format!r}"
        )
        raise ConfigError(message)


def cli() -> None:
    """Console-script wrapper."""
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
