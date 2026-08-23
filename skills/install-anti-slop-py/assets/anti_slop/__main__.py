"""Command line entry point: ``python -m anti_slop [paths...]``.

Two entry points share this module. The default one is the checker -- paths, filters
and a format -- and it is the one every existing invocation reaches. ``review`` is a
subcommand in front of it: ``python -m anti_slop review --base origin/main``.

**How the subcommand is dispatched, and why.** The first argument is read as a
subcommand name only when it is *exactly* ``review``; anything else goes to the
checker's own parser, unchanged, byte for byte. argparse subparsers were not used,
because a parser that has both an optional subcommand and a ``nargs="*"`` positional
cannot tell the two apart -- adding subparsers would have changed how the existing
invocation parses, which is the one thing this change must not do.

The edge case that costs is a repository with a directory called ``review``. The
subcommand wins, the same way it does in ``git``, ``pip`` or ``npm``, and a path is
named the way it is there too: ``python -m anti_slop ./review``, ``review/``, or
``python -m anti_slop -- review``. All three differ from the bare word, so all three
reach the checker as a path.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from anti_slop import CORE_RULES, __version__
from anti_slop.engine.baseline import (
    DEFAULT_BASELINE_NAME,
    BaselineOutcome,
    apply_baseline,
    load_baseline,
    write_baseline,
)
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
    Preset,
    known_rules,
    load_config,
    preset_named,
)
from anti_slop.engine.diff import ChangedLines, DiffError, changed_lines
from anti_slop.engine.fingerprint import fingerprints_for
from anti_slop.engine.review import (
    review_findings,
    review_json,
    review_summary,
    review_text,
)
from anti_slop.engine.rule import Diagnostic, Rule
from anti_slop.engine.runner import (
    EXIT_ERROR,
    EXIT_OK,
    RunOutcome,
    collect_changed_files,
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

REVIEW_COMMAND = "review"

# The preset `review` runs under unless `--preset` says otherwise: high and medium
# confidence block, architectural policy reports. It overrides the preset the
# repository configured -- see `load_config` -- because a posture chosen for full
# runs of a whole tree is not a decision about how an agent's diff is reviewed.
REVIEW_PRESET = "agent"

# The formats that carry a run summary. `json`, `sarif` and `github` are consumed by
# machines that expect one document (or one annotation per line) and nothing else, so
# the baseline summary would be corruption rather than information there.
_SUMMARY_FORMATS = ("text",)

_FORMATS = ("text", "json", "github", "sarif")

# `--list-rules` and `--explain` describe rules rather than findings, so they answer
# in the two formats that can carry a description; a diagnostics-only format is
# rejected instead of silently falling back to text.
_CATALOG_FORMATS = ("text", "json")

# `review` renders a report, not a stream of diagnostics: `github` annotates a diff
# without the confidence grouping that is the whole point here, and SARIF has no
# place for it either. Both stay available on the checker itself.
_REVIEW_FORMATS = ("text", "json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Reject low-evidence, low-signal Python patterns. Every diagnostic says"
            " what to write instead."
        ),
        epilog=(
            f"subcommand: {PROG} {REVIEW_COMMAND} --base BASE  --  the change this"
            f" working tree carries, as a report grouped by confidence"
            f" ({PROG} {REVIEW_COMMAND} --help)"
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
        "--baseline",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "hide the findings recorded in this baseline file; overrides"
            f" [tool.anti-slop].baseline (default: {DEFAULT_BASELINE_NAME} next to"
            " the configuration, used only when asked for)"
        ),
    )
    parser.add_argument(
        "--generate-baseline",
        action="store_true",
        dest="generate_baseline",
        help=(
            "write every current finding to the baseline file instead of reporting"
            " them, and exit 0; an existing baseline is replaced, not merged"
        ),
    )
    diff_modes = parser.add_mutually_exclusive_group()
    diff_modes.add_argument(
        "--diff",
        default=None,
        metavar="BASE",
        help=(
            "only report findings on lines this checkout changed since its merge base"
            " with BASE -- staged, unstaged and untracked included"
        ),
    )
    diff_modes.add_argument(
        "--diff-committed",
        default=None,
        dest="diff_committed",
        metavar="BASE",
        help=(
            "like --diff, but only what is committed: the merge base with BASE"
            " compared against HEAD, ignoring the working tree"
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


def build_review_parser() -> argparse.ArgumentParser:
    """The parser for ``anti-slop review`` -- the checker's diff mode, as a report."""
    parser = argparse.ArgumentParser(
        prog=f"{PROG} {REVIEW_COMMAND}",
        description=(
            "Review the change this working tree carries against BASE: the findings"
            " on the lines it touched, grouped by confidence, each with the reason"
            " the rule exists and the recipe that replaces it."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="files or directories to bound the review to; defaults to the changed files",
    )
    parser.add_argument(
        "--base",
        required=True,
        metavar="BASE",
        help=(
            "the revision to review against; the change is this working tree --"
            " staged, unstaged and untracked -- against its merge base with BASE"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="pyproject.toml to read; default is upward discovery from the cwd",
    )
    parser.add_argument(
        "--preset",
        default=REVIEW_PRESET,
        metavar="NAME",
        help=(
            "the posture to review under; overrides the repository's own preset,"
            f" which review never uses (default: {REVIEW_PRESET})"
        ),
    )
    # The full format list, so that `--format sarif` is answered with the reason it
    # does not apply here rather than with argparse's bare "invalid choice" -- the
    # same treatment `--list-rules` and `--explain` give it.
    parser.add_argument(
        "--format",
        choices=_FORMATS,
        default="text",
        dest="output_format",
        help=f"output format: {' or '.join(_REVIEW_FORMATS)}",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "hide the findings recorded in this baseline file; overrides"
            " [tool.anti-slop].baseline"
        ),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        metavar="N",
        help="worker processes for the file walk (default: auto)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch to the subcommand or to the checker; see the module docstring."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == REVIEW_COMMAND:
        return _review(arguments[1:])
    return _check(arguments)


def _check(argv: Sequence[str]) -> int:
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
        _reject_conflicting_modes(args)
        rules = _select_rules(config, args.rules)
        jobs = _validate_jobs(args.jobs)
        changed = _resolve_changed_lines(args, config)
        files = _collect_targets(args.paths, config, changed)
    except (ConfigError, DiffError) as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    try:
        outcome = run(files, rules, config.rules, jobs=jobs)
    except ConfigError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    # Diff filtering comes first and baseline second: a baseline records findings for
    # the whole project, so matching it against a diff-narrowed set would report the
    # narrow set's own entries as stale on every run.
    diagnostics = _within_diff(outcome.diagnostics, changed)
    fingerprints = _fingerprints(
        config,
        diagnostics,
        wanted=(
            args.generate_baseline
            or _baseline_requested(args, config)
            or args.output_format == "sarif"
        ),
    )

    if args.generate_baseline:
        if outcome.failures:
            _report_failures(outcome.failures)
            return EXIT_ERROR
        return _generate_baseline(args, config, fingerprints)

    try:
        baselined = _apply_baseline(
            args, config, diagnostics, fingerprints, whole_project=changed is None
        )
    except ConfigError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    _print_diagnostics(
        baselined.diagnostics,
        args.output_format,
        rules=rules,
        root=config.root,
        fingerprints=_by_diagnostic(diagnostics, fingerprints),
        summary=_summary_for(baselined, args.output_format),
    )
    _report_failures(outcome.failures)
    return RunOutcome(
        diagnostics=baselined.diagnostics, failures=outcome.failures
    ).exit_code()


def _review(argv: Sequence[str]) -> int:
    """``anti-slop review --base <ref> [paths...]``.

    A diff run of the working tree, rendered as a report. There is deliberately no
    ``--diff-committed`` counterpart: reviewing what has already been committed is
    what ``--diff-committed`` itself is for, and an agent's change is reviewed while
    it is still in the working tree, which is the whole reason this mode exists.
    """
    args = build_review_parser().parse_args(argv)

    try:
        _require_review_format(args.output_format)
        preset = _review_preset(args.preset)
        config = load_config(
            registry=CORE_RULES, explicit_path=args.config, preset=preset
        )
        jobs = _validate_jobs(args.jobs)
        changed = changed_lines(args.base, root=config.root, committed=False)
        files = _collect_targets(args.paths, config, changed)
    except (ConfigError, DiffError) as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    rules = config.enabled_rules()
    try:
        outcome = run(files, rules, config.rules, jobs=jobs)
    except ConfigError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    diagnostics = _within_diff(outcome.diagnostics, changed)
    fingerprints = _fingerprints(
        config, diagnostics, wanted=_baseline_requested(args, config)
    )
    try:
        baselined = _apply_baseline(
            args, config, diagnostics, fingerprints, whole_project=False
        )
        findings = review_findings(baselined.diagnostics, rules, config.root)
    except ConfigError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR

    if args.output_format == "json":
        print(review_json(args.base, preset.name, findings))
    else:
        if findings:
            print(review_text(findings))
            print()
        print(review_summary(findings))
        if baselined.summary() is not None:
            print(baselined.summary())

    _report_failures(outcome.failures)
    return RunOutcome(
        diagnostics=baselined.diagnostics, failures=outcome.failures
    ).exit_code()


def _review_preset(name: str) -> Preset:
    """``--preset``, defaulting to :data:`REVIEW_PRESET`; an unknown name exits 2."""
    return preset_named(name, f"{PROG} {REVIEW_COMMAND} --preset")


def _require_review_format(output_format: str) -> None:
    if output_format not in _REVIEW_FORMATS:
        available = " or ".join(_REVIEW_FORMATS)
        message = (
            f"{REVIEW_COMMAND} renders a report, not a diagnostics stream:"
            f" --format must be {available}, got {output_format!r}"
        )
        raise ConfigError(message)


def _reject_conflicting_modes(args: argparse.Namespace) -> None:
    """``--generate-baseline`` records a project, so it cannot record only a diff."""
    if not args.generate_baseline:
        return
    if args.diff is None and args.diff_committed is None:
        return
    message = (
        "--generate-baseline records the findings of a whole project, so it cannot"
        " be combined with --diff/--diff-committed: the file it wrote would silence"
        " only the lines that happened to be changed"
    )
    raise ConfigError(message)


def _validate_jobs(jobs: int | None) -> int | None:
    if jobs is not None and jobs < 1:
        message = f"--jobs must be a positive integer, got {jobs}"
        raise ConfigError(message)
    return jobs


def _resolve_changed_lines(
    args: argparse.Namespace, config: Config
) -> ChangedLines | None:
    """The lines the requested diff touched, or ``None`` when no diff was requested."""
    if args.diff is not None:
        return changed_lines(args.diff, root=config.root, committed=False)
    if args.diff_committed is not None:
        return changed_lines(args.diff_committed, root=config.root, committed=True)
    return None


def _collect_targets(
    cli_paths: Sequence[str], config: Config, changed: ChangedLines | None
) -> tuple[Path, ...]:
    """Which files to check: the diff's files, or the configured walk.

    Explicit paths always bound the walk. In diff mode they also *stay* the walk --
    the diff then only filters the diagnostics -- because a caller who named paths
    meant those paths. With no explicit paths, diff mode walks the changed files
    themselves instead of the whole ``include`` tree.
    """
    roots = resolve_roots(cli_paths, config)
    if changed is None or cli_paths:
        return collect_files(roots, config)
    return collect_changed_files(roots, changed.paths(), config)


def _within_diff(
    diagnostics: Sequence[Diagnostic], changed: ChangedLines | None
) -> tuple[Diagnostic, ...]:
    """Keep the diagnostics whose anchor line the change touched."""
    if changed is None:
        return tuple(diagnostics)
    return tuple(
        diagnostic
        for diagnostic in diagnostics
        if changed.contains(diagnostic.path, diagnostic.line)
    )


def _fingerprints(
    config: Config, diagnostics: Sequence[Diagnostic], *, wanted: bool
) -> tuple[str, ...]:
    """Fingerprints for the modes that consume them, and nothing for the ones that do not.

    Computing a fingerprint means reading the file a finding sits in a second time.
    Three modes need that -- writing a baseline, applying one, and SARIF output -- and
    a plain ``--format text`` run needs none of it.
    """
    if wanted:
        return fingerprints_for(diagnostics, config.root)
    return ()


def _baseline_requested(args: argparse.Namespace, config: Config) -> bool:
    """Whether a baseline was actually asked for, on the command line or in the file."""
    return args.baseline is not None or config.baseline is not None


def _by_diagnostic(
    diagnostics: Sequence[Diagnostic], fingerprints: Sequence[str]
) -> dict[Diagnostic, str]:
    """The fingerprints keyed by finding, or nothing when none were computed."""
    if not fingerprints:
        return {}
    return dict(zip(diagnostics, fingerprints, strict=True))


def _baseline_path(args: argparse.Namespace, config: Config) -> Path:
    """``--baseline``, else ``[tool.anti-slop].baseline``, else the default name.

    A CLI path is taken as written (relative to the working directory, like
    ``--config``); a configured one is relative to the configuration root, like
    ``include`` and ``exclude``.
    """
    if args.baseline is not None:
        return args.baseline
    if config.baseline is not None:
        return config.root / config.baseline
    return config.root / DEFAULT_BASELINE_NAME


def _apply_baseline(
    args: argparse.Namespace,
    config: Config,
    diagnostics: Sequence[Diagnostic],
    fingerprints: Sequence[str],
    *,
    whole_project: bool = True,
) -> BaselineOutcome:
    """Hide the baselined findings -- but only when a baseline was actually asked for.

    A baseline file that merely happens to sit at the default path is not applied: a
    run that silently hid findings because of a file nobody named would be the worst
    possible default for a tool whose whole point is saying what it found.
    """
    if not _baseline_requested(args, config):
        return BaselineOutcome(diagnostics=tuple(diagnostics), hidden=0, stale=0)
    baseline = load_baseline(_baseline_path(args, config))
    return apply_baseline(
        baseline, diagnostics, fingerprints, whole_project=whole_project
    )


def _generate_baseline(
    args: argparse.Namespace, config: Config, fingerprints: Sequence[str]
) -> int:
    path = _baseline_path(args, config)
    try:
        count = write_baseline(path, fingerprints)
    except ConfigError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_ERROR
    if args.output_format in _SUMMARY_FORMATS:
        distinct = len(set(fingerprints))
        print(f"wrote {path}: {count} findings across {distinct} fingerprints")
    return EXIT_OK


def _summary_for(baselined: BaselineOutcome, output_format: str) -> str | None:
    if output_format not in _SUMMARY_FORMATS:
        return None
    return baselined.summary()


def _report_failures(failures: Sequence[str]) -> None:
    for failure in failures:
        print(f"{PROG}: {failure}", file=sys.stderr)


def _print_diagnostics(
    diagnostics: Sequence[Diagnostic],
    output_format: str,
    *,
    rules: Sequence[Rule],
    root: Path,
    fingerprints: Mapping[Diagnostic, str],
    summary: str | None = None,
) -> None:
    if output_format == "json":
        print(format_json(diagnostics))
        return
    if output_format == "sarif":
        print(
            format_sarif(
                diagnostics,
                rules,
                root,
                tool_version=__version__,
                fingerprints=fingerprints,
            )
        )
        return
    if output_format == "github":
        if diagnostics:
            print(format_github(diagnostics))
        return
    if diagnostics:
        print(format_text(diagnostics))
    if summary is not None:
        print(summary)


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
