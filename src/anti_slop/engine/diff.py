"""Diff mode: restrict findings to the lines a change actually touched.

``--diff <base>`` answers the question an agent asks right after editing a file --
"did *I* just introduce something?" -- and it answers it about the **working tree**,
not about ``HEAD``. An edit that has not been committed yet is exactly the edit worth
checking; comparing only committed history would report a clean run on the code
sitting in the editor. So the default compares the current checkout (staged, unstaged
and untracked alike) against the merge base with ``<base>``.
``--diff-committed <base>`` is the strict variant for CI, comparing the merge base
with ``HEAD`` and nothing else.

Mechanically::

    git rev-parse --show-toplevel
    git merge-base <base> HEAD
    git diff --unified=0 --no-color --no-renames --no-ext-diff <merge-base> [HEAD]
    git ls-files --others --exclude-standard          # working-tree mode only

Every flag is passed explicitly rather than relied upon as a default, because the
defaults are the user's to change: ``diff.renames`` turns rename detection on, an
external diff driver replaces the format wholesale, and ``diff.noprefix`` removes the
``a/``/``b/`` prefixes this module parses. A deterministic invocation matters more
than a short one. ``GIT_OPTIONAL_LOCKS=0`` keeps a read-only query from taking the
index lock, so running this alongside an editor's own git integration cannot fail on
a lock it never needed.

The unified-diff headers are parsed here, with the standard library, rather than by
asking git for a machine format it does not have. Only the *new* side matters: a
diagnostic survives the filter when its anchor line is one of the lines the change
added or rewrote. A finding spanning several lines is judged by its anchor line
alone, the same line every format reports as its location -- a multi-line construct
whose anchor was not touched is not this change's finding.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ChangedLines", "DiffError", "changed_lines", "parse_patch"]


class DiffError(Exception):
    """Git is unavailable, the tree is not a repository, or ``<base>`` is unknown."""


# A read-only query has no business taking the index lock; without this git may
# refresh (and lock) the index behind the scenes and fail against a concurrent editor.
_GIT_ENVIRONMENT = {"GIT_OPTIONAL_LOCKS": "0"}

_DIFF_FLAGS = (
    "--unified=0",
    "--no-color",
    "--no-renames",
    "--no-ext-diff",
    "--src-prefix=a/",
    "--dst-prefix=b/",
)

_NEW_FILE_MARKER = "+++ "
_DEV_NULL = "/dev/null"
_DST_PREFIX = "b/"

# `@@ -<old start>[,<old count>] +<new start>[,<new count>] @@`; only the new side is
# read. An omitted count means one line, per the unified-diff format.
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True, slots=True)
class ChangedLines:
    """Which lines of which files one change touched.

    ``lines`` maps a resolved absolute path to the new-side line numbers the diff
    added or rewrote. ``whole_files`` holds files with no diff at all to compare
    against -- untracked ones -- for which every line counts as new.
    """

    toplevel: Path
    lines: Mapping[Path, frozenset[int]]
    whole_files: frozenset[Path]

    def paths(self) -> tuple[Path, ...]:
        """Every file this change touched, sorted, whether tracked or not."""
        return tuple(sorted({*self.lines, *self.whole_files}, key=str))

    def contains(self, path: Path, line: int) -> bool:
        """True when ``line`` of ``path`` is part of this change."""
        resolved = path.resolve()
        if resolved in self.whole_files:
            return True
        return line in self.lines.get(resolved, frozenset())


def changed_lines(base: str, *, root: Path, committed: bool = False) -> ChangedLines:
    """The lines ``base``..working tree (or ``base``..``HEAD``) touched.

    ``root`` is where the git queries run -- the configuration root, so that a run
    from a subdirectory, or of a project nested inside a larger repository, resolves
    the same repository the files being linted belong to. Paths come back resolved and
    absolute, against the repository's own top level rather than ``root``.
    """
    toplevel = _toplevel(root)
    merge_base = _merge_base(base, toplevel)

    revisions = (merge_base, "HEAD") if committed else (merge_base,)
    patch = _git(toplevel, "diff", *_DIFF_FLAGS, *revisions)
    untracked = () if committed else _untracked(toplevel)

    return ChangedLines(
        toplevel=toplevel,
        lines=parse_patch(patch, toplevel),
        whole_files=frozenset(untracked),
    )


def parse_patch(patch: str, toplevel: Path) -> dict[Path, frozenset[int]]:
    """New-side line numbers per file, from a ``--unified=0`` patch.

    Public because the parsing, not the subprocess, is the part worth testing directly.
    """
    collected: dict[Path, set[int]] = {}
    current: set[int] | None = None

    for line in patch.splitlines():
        if line.startswith(_NEW_FILE_MARKER):
            current = _target_lines(line[len(_NEW_FILE_MARKER) :], toplevel, collected)
            continue
        if current is None or not line.startswith("@@"):
            continue
        start, count = _hunk_range(line)
        current.update(range(start, start + count))

    return {path: frozenset(numbers) for path, numbers in collected.items()}


def _target_lines(
    target: str, toplevel: Path, collected: dict[Path, set[int]]
) -> set[int] | None:
    """The line set to fill for a ``+++`` header, or ``None`` for a deleted file."""
    if target == _DEV_NULL:
        return None
    relative = target[len(_DST_PREFIX) :] if target.startswith(_DST_PREFIX) else target
    return collected.setdefault((toplevel / relative).resolve(), set())


def _hunk_range(header: str) -> tuple[int, int]:
    match = _HUNK_HEADER.match(header)
    if match is None:
        return (0, 0)
    start = int(match.group(1))
    raw_count = match.group(2)
    return (start, 1 if raw_count is None else int(raw_count))


def _toplevel(root: Path) -> Path:
    try:
        output = _git(root, "rev-parse", "--show-toplevel")
    except DiffError as error:
        message = (
            f"{root.resolve()} is not inside a git repository, so there is no diff"
            f" to check against ({error})"
        )
        raise DiffError(message) from error
    return Path(output.strip()).resolve()


def _merge_base(base: str, toplevel: Path) -> str:
    try:
        output = _git(toplevel, "merge-base", base, "HEAD")
    except DiffError as error:
        message = (
            f"cannot find a merge base between {base!r} and HEAD in {toplevel}"
            f" ({error})"
        )
        raise DiffError(message) from error
    return output.strip()


def _untracked(toplevel: Path) -> tuple[Path, ...]:
    output = _git(toplevel, "ls-files", "--others", "--exclude-standard", "--full-name")
    return tuple(
        (toplevel / line).resolve() for line in output.splitlines() if line.strip()
    )


def _git(cwd: Path, *arguments: str) -> str:
    """Run one git command and return its stdout; a non-zero exit is a ``DiffError``.

    The argument vector is fixed and passed as a list -- no shell, no interpolation --
    so a branch name like ``$(rm -rf /)`` reaches git as a literal revision name and
    fails to resolve, which is the whole of its effect.
    """
    command = ("git", *arguments)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=_environment(),
        )
    except OSError as error:
        message = f"cannot run git: {error}"
        raise DiffError(message) from error

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        message = f"git {' '.join(arguments)} failed: {detail}"
        raise DiffError(message)
    return completed.stdout


def _environment() -> dict[str, str]:
    return {**os.environ, **_GIT_ENVIRONMENT}
