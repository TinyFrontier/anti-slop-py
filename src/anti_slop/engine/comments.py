"""Comment extraction and suppression parsing (PLAN.md FR-3).

``ast`` discards comments, so the comment map is built from ``tokenize``. The same
map later serves ``require-safety-comment`` (phase 2), which needs the raw text of
the comment attached to a line.

Supported directives::

    # anti-slop: ignore[rule-id]            suppress on this line or the next line
    # anti-slop: ignore[rule-a, rule-b]     suppress several rules at once
    # anti-slop: skip-file                  skip the file (first 5 lines only)

A suppression without a rule id is a configuration error, not a silent blanket
"turn everything off".
"""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from anti_slop.engine.config import ConfigError

__all__ = ["SKIP_FILE_MAX_LINE", "CommentMap", "build_comment_map"]

SKIP_FILE_MAX_LINE = 5

_DIRECTIVE_RE = re.compile(r"anti-slop\s*:\s*(?P<body>[^#]*)")
_IGNORE_RE = re.compile(r"^ignore\b\s*(?:\[(?P<ids>[^\]]*)\])?(?P<rest>.*)$")
_SKIP_FILE_RE = re.compile(r"^skip-file\b(?P<rest>.*)$")
_RULE_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")

_EXAMPLE = "# anti-slop: ignore[no-object-parameters]"


@dataclass(frozen=True, slots=True)
class CommentMap:
    """Comments of one file, indexed by the line they start on."""

    path: Path
    by_line: Mapping[int, str]
    ignores: Mapping[int, frozenset[str]]
    skip_file: bool

    def text_at(self, line: int) -> str | None:
        return self.by_line.get(line)

    def suppresses(self, line: int, rule_id: str) -> bool:
        """True when ``rule_id`` is ignored on ``line`` or by the line above it."""
        own = self.ignores.get(line)
        if own is not None and rule_id in own:
            return True
        above = self.ignores.get(line - 1)
        return above is not None and rule_id in above


def build_comment_map(path: Path, source: str) -> CommentMap:
    """Tokenize ``source`` and collect comments plus anti-slop directives."""
    by_line: dict[int, str] = {}
    ignores: dict[int, set[str]] = {}
    skip_file = False

    readline = io.StringIO(source).readline
    try:
        for token in tokenize.generate_tokens(readline):
            if token.type != tokenize.COMMENT:
                continue
            line = token.start[0]
            by_line[line] = token.string
            directive = _DIRECTIVE_RE.search(token.string)
            if directive is None:
                continue
            body = directive.group("body").strip()
            rule_ids, is_skip_file = _parse_directive(body, path, line)
            if is_skip_file:
                skip_file = True
                continue
            ignores.setdefault(line, set()).update(rule_ids)
    except (tokenize.TokenError, IndentationError):
        # A file that does not tokenize does not parse either; the parser produces
        # the authoritative error message. Keep the comments gathered so far.
        pass

    return CommentMap(
        path=path,
        by_line=by_line,
        ignores={line: frozenset(ids) for line, ids in ignores.items()},
        skip_file=skip_file,
    )


def _parse_directive(body: str, path: Path, line: int) -> tuple[frozenset[str], bool]:
    skip_match = _SKIP_FILE_RE.match(body)
    if skip_match is not None:
        if line > SKIP_FILE_MAX_LINE:
            message = (
                f"{path}:{line}: '# anti-slop: skip-file' is only honoured in the"
                f" first {SKIP_FILE_MAX_LINE} lines of a file; move it to the top"
                f" or suppress individual rules with '{_EXAMPLE}'"
            )
            raise ConfigError(message)
        return frozenset(), True

    ignore_match = _IGNORE_RE.match(body)
    if ignore_match is None:
        message = (
            f"{path}:{line}: unknown anti-slop directive {body!r};"
            f" expected 'ignore[rule-id]' or 'skip-file'"
        )
        raise ConfigError(message)

    raw_ids = ignore_match.group("ids")
    if raw_ids is None:
        message = (
            f"{path}:{line}: '# anti-slop: ignore' must name the rule it suppresses,"
            f" e.g. '{_EXAMPLE}'"
        )
        raise ConfigError(message)

    rule_ids = [part.strip() for part in raw_ids.split(",") if part.strip()]
    if not rule_ids:
        message = (
            f"{path}:{line}: '# anti-slop: ignore[]' suppresses nothing;"
            f" name the rule, e.g. '{_EXAMPLE}'"
        )
        raise ConfigError(message)

    for rule_id in rule_ids:
        if _RULE_ID_RE.fullmatch(rule_id) is None:
            message = (
                f"{path}:{line}: {rule_id!r} is not a valid rule id"
                f" (expected kebab-case, e.g. '{_EXAMPLE}')"
            )
            raise ConfigError(message)

    return frozenset(rule_ids), False
