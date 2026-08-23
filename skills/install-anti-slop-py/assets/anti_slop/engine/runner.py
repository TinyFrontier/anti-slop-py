"""File discovery, the per-file engine pass, and output formatting.

Exit codes: ``0`` clean, ``1`` violations found, ``2``
configuration or usage error -- including a target file that does not parse,
because a file that cannot be analysed must not be reported as clean.
"""

from __future__ import annotations

import ast
import json
import os
import warnings
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from anti_slop.engine.comments import build_comment_map
from anti_slop.engine.config import Config, ConfigError, PathFilter, RuleSetting
from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Diagnostic, Rule
from anti_slop.engine.walker import ParentMap, Walker

__all__ = [
    "EXIT_ERROR",
    "EXIT_OK",
    "EXIT_VIOLATIONS",
    "AnalysisError",
    "RunOutcome",
    "check_source",
    "collect_files",
    "format_github",
    "format_json",
    "format_text",
    "resolve_roots",
    "run",
]

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2

_PYTHON_SUFFIXES = (".py", ".pyi")

# Above this many collected files, `run()` distributes work across a
# `ProcessPoolExecutor` instead of walking files one at a time.
_PARALLEL_FILE_THRESHOLD = 20


class AnalysisError(Exception):
    """A target file could not be read or parsed."""


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Result of a run: diagnostics plus files that could not be analysed."""

    diagnostics: tuple[Diagnostic, ...]
    failures: tuple[str, ...]

    def exit_code(self) -> int:
        if self.failures:
            return EXIT_ERROR
        if any(diagnostic.severity == "error" for diagnostic in self.diagnostics):
            return EXIT_VIOLATIONS
        return EXIT_OK


def check_source(
    *,
    path: Path,
    source: str,
    rules: Sequence[Rule],
    settings: Mapping[str, RuleSetting],
) -> tuple[Diagnostic, ...]:
    """Run ``rules`` over one in-memory source string.

    Raises :class:`AnalysisError` when the source does not parse, and
    :class:`~anti_slop.engine.config.ConfigError` for a malformed suppression.
    """
    comments = build_comment_map(path, source)
    if comments.skip_file:
        return ()

    try:
        # A linted file's own SyntaxWarnings (e.g. invalid escape sequences) are
        # its author's concern, not this tool's output; keep stderr clean.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        line = error.lineno if error.lineno is not None else 1
        col = error.offset if error.offset is not None else 1
        message = f"{path}:{line}:{col} syntax-error {error.msg}"
        raise AnalysisError(message) from error

    collected: list[Diagnostic] = []
    parents = ParentMap()
    walker = Walker(parents)
    lines = source.splitlines()

    for rule in rules:
        setting = settings.get(rule.id)
        if setting is None or not setting.enabled:
            continue
        context = RuleContext(
            path=path,
            source=source,
            lines=lines,
            comments=comments,
            parents=parents,
            rule=rule,
            options=setting.options,
            report=collected.append,
            severity=setting.severity,
        )
        walker.subscribe(context, rule.handlers)

    walker.run(tree)
    collected.sort(key=lambda diagnostic: diagnostic.sort_key)
    return tuple(collected)


def check_file(
    path: Path, rules: Sequence[Rule], settings: Mapping[str, RuleSetting]
) -> tuple[Diagnostic, ...]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        message = f"{path}: cannot be read: {error}"
        raise AnalysisError(message) from error
    except UnicodeDecodeError as error:
        message = f"{path}: is not valid UTF-8: {error}"
        raise AnalysisError(message) from error
    return check_source(path=path, source=source, rules=rules, settings=settings)


def resolve_roots(cli_paths: Sequence[str], config: Config) -> tuple[Path, ...]:
    """Paths given on the command line win; otherwise fall back to ``include``."""
    if cli_paths:
        return tuple(Path(entry) for entry in cli_paths)
    if config.include:
        return tuple(config.root / entry for entry in config.include)
    return (config.root,)


def collect_files(roots: Iterable[Path], config: Config) -> tuple[Path, ...]:
    """Expand ``roots`` into Python files, honouring ``exclude``."""
    path_filter = PathFilter(config.exclude)
    found: list[Path] = []
    seen: set[Path] = set()

    for root in roots:
        if not root.exists():
            message = f"path does not exist: {root}"
            raise ConfigError(message)
        candidates = (
            [root]
            if root.is_file()
            else sorted(
                candidate
                for suffix in _PYTHON_SUFFIXES
                for candidate in root.rglob(f"*{suffix}")
            )
        )
        for candidate in candidates:
            if candidate.suffix not in _PYTHON_SUFFIXES:
                continue
            if _is_excluded(candidate, root, config, path_filter):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(candidate)

    found.sort(key=str)
    return tuple(found)


def _is_excluded(path: Path, root: Path, config: Config, path_filter: PathFilter) -> bool:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(config.root.resolve())
    except ValueError:
        # The file lives outside the config root (linting another repository).
        # Exclude patterns must still apply, so match against the scanned root:
        # otherwise defaults like `.venv/**` silently stop working out of tree.
        base = root if root.is_dir() else root.parent
        try:
            relative = resolved.relative_to(base.resolve())
        except ValueError:
            return False
    return path_filter.excludes(PurePosixPath(relative.as_posix()))


# Populated once per worker process by `_init_worker`, then read by every task that
# process serves. A worker process is created for, and torn down at the end of, one
# `run()` call, so process-global state here does not leak across runs. This avoids
# re-pickling the rule registry and resolved settings for every single file -- only
# each file's `Path` crosses the process boundary per task.
_worker_rules: tuple[Rule, ...] = ()
_worker_settings: Mapping[str, RuleSetting] = {}


def _init_worker(rules: Sequence[Rule], settings: Mapping[str, RuleSetting]) -> None:
    global _worker_rules, _worker_settings
    _worker_rules = tuple(rules)
    _worker_settings = settings


def _check_file_in_worker(path: Path) -> tuple[tuple[Diagnostic, ...], str | None]:
    """Run in a worker process; return diagnostics or a failure message, never raise.

    ``Diagnostic`` (``engine/rule.py``) is a frozen dataclass built only from
    primitives -- ``Path``, ``int``, ``str`` -- with no AST node attached, so it
    round-trips through the executor's pickling unchanged. Failures are returned as
    plain strings rather than a raised ``AnalysisError`` so a worker never needs the
    caller to reconstruct an exception from pickled state.
    """
    try:
        diagnostics = check_file(path, _worker_rules, _worker_settings)
    except AnalysisError as error:
        return (), str(error)
    return diagnostics, None


def _worker_count(jobs: int | None, file_count: int) -> int:
    if jobs is not None:
        return max(1, jobs)
    return max(1, min(os.cpu_count() or 1, file_count))


def _run_sequential(
    files: Sequence[Path], rules: Sequence[Rule], settings: Mapping[str, RuleSetting]
) -> RunOutcome:
    diagnostics: list[Diagnostic] = []
    failures: list[str] = []
    for path in files:
        try:
            diagnostics.extend(check_file(path, rules, settings))
        except AnalysisError as error:
            failures.append(str(error))
    diagnostics.sort(key=lambda diagnostic: diagnostic.sort_key)
    return RunOutcome(diagnostics=tuple(diagnostics), failures=tuple(failures))


def _run_parallel(
    files: Sequence[Path],
    rules: Sequence[Rule],
    settings: Mapping[str, RuleSetting],
    jobs: int | None,
) -> RunOutcome:
    diagnostics: list[Diagnostic] = []
    failures: list[str] = []
    workers = _worker_count(jobs, len(files))
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(tuple(rules), settings),
    ) as executor:
        # `executor.map` yields results in the order of `files`, not completion
        # order, so aggregation below is deterministic independent of scheduling --
        # the same guarantee the sequential path gives by construction.
        for file_diagnostics, failure in executor.map(_check_file_in_worker, files):
            diagnostics.extend(file_diagnostics)
            if failure is not None:
                failures.append(failure)
    diagnostics.sort(key=lambda diagnostic: diagnostic.sort_key)
    return RunOutcome(diagnostics=tuple(diagnostics), failures=tuple(failures))


def run(
    files: Sequence[Path],
    rules: Sequence[Rule],
    settings: Mapping[str, RuleSetting],
    *,
    jobs: int | None = None,
    parallel_threshold: int = _PARALLEL_FILE_THRESHOLD,
) -> RunOutcome:
    """Check every file in ``files``.

    Sequential when ``len(files) <= parallel_threshold`` or ``jobs == 1``; above
    that threshold a ``ProcessPoolExecutor`` checks files across worker processes.
    ``jobs=None`` (the CLI default) picks a worker count
    automatically from ``os.cpu_count()``, capped at the file count. Diagnostics
    and failures come back sorted/ordered identically to the sequential path
    regardless of which path ran.

    ``parallel_threshold`` defaults to the module constant and exists as an
    argument -- not a value tests reach in and patch -- so a test can exercise the
    parallel path on a small, fast fixture set without spawning dozens of worker
    processes.
    """
    if len(files) > parallel_threshold and jobs != 1:
        return _run_parallel(files, rules, settings, jobs)
    return _run_sequential(files, rules, settings)


def format_text(diagnostics: Sequence[Diagnostic]) -> str:
    """``path:line:col rule-id message`` -- one diagnostic per line.

    A ``warn``-severity diagnostic gets a ``warning: `` marker ahead of the
    message; an ``error`` diagnostic's line is unchanged from before severities
    existed, so this stays a silent no-op for every all-error run.
    """
    lines = []
    for diagnostic in diagnostics:
        marker = "warning: " if diagnostic.severity == "warn" else ""
        lines.append(
            f"{diagnostic.path}:{diagnostic.line}:{diagnostic.col}"
            f" {diagnostic.rule_id} {marker}{diagnostic.message}"
        )
    return "\n".join(lines)


def format_json(diagnostics: Sequence[Diagnostic]) -> str:
    """A single JSON array document, keys and sort order per FR-4.

    Diagnostics arrive already sorted by ``(path, line, col, rule_id)`` -- see
    ``RunOutcome`` -- so this only shapes each one into the exact key set FR-4
    requires (camelCase ``endLine``/``endCol``, ``rule`` not ``rule_id``).
    """
    payload = [
        {
            "path": str(diagnostic.path),
            "line": diagnostic.line,
            "col": diagnostic.col,
            "endLine": diagnostic.end_line,
            "endCol": diagnostic.end_col,
            "rule": diagnostic.rule_id,
            "severity": diagnostic.severity,
            "message": diagnostic.message,
        }
        for diagnostic in diagnostics
    ]
    return json.dumps(payload)


def _escape_workflow_data(value: str) -> str:
    """Escape a GitHub Actions workflow-command *value* (the part after ``::``)."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_workflow_property(value: str) -> str:
    """Escape a GitHub Actions workflow-command *property* (a ``key=value`` field)."""
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def format_github(diagnostics: Sequence[Diagnostic]) -> str:
    """GitHub Actions ``::error``/``::warning`` annotations, one per diagnostic.

    Field order and names follow ``actions/toolkit``'s ``error``/``warning``
    commands; escaping follows its documented ``%``/CR/LF (and ``:``/``,`` for
    properties) rules so a path or message containing those characters cannot
    corrupt the command line. A ``warn``-severity diagnostic emits ``::warning``
    instead of ``::error``; every other field is unchanged.
    """
    lines = []
    for diagnostic in diagnostics:
        command = "warning" if diagnostic.severity == "warn" else "error"
        file_field = _escape_workflow_property(str(diagnostic.path))
        title_field = _escape_workflow_property(diagnostic.rule_id)
        message_field = _escape_workflow_data(diagnostic.message)
        lines.append(
            f"::{command} file={file_field},line={diagnostic.line},col={diagnostic.col},"
            f"endLine={diagnostic.end_line},endColumn={diagnostic.end_col},"
            f"title={title_field}::{message_field}"
        )
    return "\n".join(lines)
