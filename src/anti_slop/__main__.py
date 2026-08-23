"""Command line entry point: ``python -m anti_slop [paths...]`` (PLAN.md FR-1)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from anti_slop import CORE_RULES, __version__
from anti_slop.engine.config import Config, ConfigError, load_config
from anti_slop.engine.rule import BoolOption, Rule, StrListOption
from anti_slop.engine.runner import (
    EXIT_ERROR,
    collect_files,
    format_text,
    resolve_roots,
    run,
)

__all__ = ["cli", "main"]

PROG = "anti-slop"

# `json` and `github` land in phase 3 (PLAN.md section 5).
_FORMATS = ("text",)


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
        help="list the registered rules with their options and exit",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_rules:
        print(_render_rule_list(CORE_RULES))
        return 0

    try:
        config = load_config(registry=CORE_RULES, explicit_path=args.config)
        rules = _select_rules(config, args.rules)
        roots = resolve_roots(args.paths, config)
        files = collect_files(roots, config)
    except ConfigError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    try:
        outcome = run(files, rules, config.rules)
    except ConfigError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    if outcome.diagnostics:
        print(format_text(outcome.diagnostics))
    for failure in outcome.failures:
        print(f"{PROG}: {failure}", file=sys.stderr)
    return outcome.exit_code()


def _select_rules(config: Config, requested: Sequence[str] | None) -> tuple[Rule, ...]:
    enabled = config.enabled_rules(CORE_RULES)
    if not requested:
        return enabled
    known = {rule.id for rule in CORE_RULES}
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


def _render_rule_list(rules: Sequence[Rule]) -> str:
    lines: list[str] = []
    for rule in sorted(rules, key=lambda rule: rule.id):
        lines.append(f"{rule.id}  {rule.description}")
        for spec in rule.options:
            lines.append(f"    {spec.name} (default: {_render_default(spec)})")
    return "\n".join(lines)


def _render_default(spec: BoolOption | StrListOption) -> str:
    if isinstance(spec, BoolOption):
        return "true" if spec.default else "false"
    return "[" + ", ".join(repr(term) for term in spec.default) + "]"


def cli() -> None:
    """Console-script wrapper."""
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
