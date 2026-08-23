"""File discovery, the per-file engine pass, and output formatting.

Exit codes (PLAN.md FR-1): ``0`` clean, ``1`` violations found, ``2``
configuration or usage error -- including a target file that does not parse,
because a file that cannot be analysed must not be reported as clean.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
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
    "format_text",
    "resolve_roots",
    "run",
]

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2

_PYTHON_SUFFIXES = (".py", ".pyi")


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
        if self.diagnostics:
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
            if _is_excluded(candidate, config, path_filter):
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(candidate)

    found.sort(key=str)
    return tuple(found)


def _is_excluded(path: Path, config: Config, path_filter: PathFilter) -> bool:
    try:
        relative = path.resolve().relative_to(config.root.resolve())
    except ValueError:
        return False
    return path_filter.excludes(PurePosixPath(relative.as_posix()))


def run(
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


def format_text(diagnostics: Sequence[Diagnostic]) -> str:
    """``path:line:col rule-id message`` -- one diagnostic per line (PLAN.md FR-4)."""
    return "\n".join(
        f"{diagnostic.path}:{diagnostic.line}:{diagnostic.col}"
        f" {diagnostic.rule_id} {diagnostic.message}"
        for diagnostic in diagnostics
    )
