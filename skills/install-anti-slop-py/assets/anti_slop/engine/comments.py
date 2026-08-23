"""Comment extraction and suppression parsing.

``ast`` discards comments, so the comment map is built from ``tokenize``. The same
map serves ``require-safety-comment`` (phase 2), which needs the raw text of the
comment attached to a line.

Supported directives::

    # anti-slop: ignore[rule-id]            suppress on this line or the next line
    # anti-slop: ignore[rule-a, rule-b]     suppress several rules at once
    # anti-slop: skip-file                  skip the file (first 5 lines only)

A suppression without a rule id is a configuration error, not a silent blanket
"turn everything off".

Two further primitives sit on top of the same map, for phase 2
("Ключевые решения движка"):

* :func:`safety_comment_for` -- does a safety comment cover this AST node? The
  marker is ``SAFETY`` followed by a colon and the invariant it states; an empty one
  states nothing and does not count. Coverage looks at the node's own line and at the
  comment block above it, then at the same two places for the *statement that owns*
  the node -- the port of the original's ``commentOwnerKinds`` list, which in ``ast``
  is simply the ``ast.stmt`` base class. :func:`safety_invariant_at` and
  :func:`safety_above` are the two halves, usable on a bare line number for callers
  (such as a comment directive) that have no node to climb from.
* :func:`checker_suppressions` -- every type-checker suppression in the file, in the
  portable spelling and in the ty and pyright dialects, read from the token stream
  rather than from ``ast.parse(type_comments=True)`` so that codes and positions come
  from the same place as every other comment.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from anti_slop.engine.config import ConfigError

__all__ = [
    "SKIP_FILE_MAX_LINE",
    "CheckerSuppression",
    "CommentMap",
    "build_comment_map",
    "checker_suppressions",
    "safety_above",
    "safety_comment_for",
    "safety_invariant_at",
]

SKIP_FILE_MAX_LINE = 5

_DIRECTIVE_RE = re.compile(r"anti-slop\s*:\s*(?P<body>[^#]*)")
_IGNORE_RE = re.compile(r"^ignore\b\s*(?:\[(?P<ids>[^\]]*)\])?(?P<rest>.*)$")
_SKIP_FILE_RE = re.compile(r"^skip-file\b(?P<rest>.*)$")
_RULE_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")

# The original's safety marker, kept upper-case and kept loose around the colon, so
# that both the tight and the spaced spellings of it count. See the module docstring.
_SAFETY_RE = re.compile(r"\bSAFETY\s*:(?P<invariant>.*)$")

# A type-checker suppression *inside* a comment token. The leading hash is required
# for two reasons: anti-slop's own directive grammar can then never be mistaken for
# one, and a second directive sharing a comment token with a first is found at its
# own column rather than at the column of the token.
_CHECKER_IGNORE_RE = re.compile(
    r"#\s*(?P<checker>type|ty|pyright)\s*:\s*ignore(?:\s*\[(?P<codes>[^\]]*)\])?"
)

_EXAMPLE = "# anti-slop: ignore[no-object-parameters]"


@dataclass(frozen=True, slots=True)
class CheckerSuppression:
    """One ``# <checker>: ignore[...]`` directive found in a comment token.

    ``col``/``end_col`` are 0-based, matching ``ast`` column conventions, and span
    the directive itself rather than the whole comment, so two directives sharing a
    line report at distinct positions.
    """

    line: int
    col: int
    end_col: int
    checker: str
    codes: tuple[str, ...]
    text: str

    @property
    def has_codes(self) -> bool:
        """True when the directive names at least one error code.

        ``# type: ignore`` and ``# type: ignore[]`` are both code-less: they silence
        every error the checker would raise on that line, present and future.
        """
        return bool(self.codes)


@dataclass(frozen=True, slots=True)
class CommentMap:
    """Comments of one file, indexed by the line they start on."""

    path: Path
    by_line: Mapping[int, str]
    ignores: Mapping[int, frozenset[str]]
    skip_file: bool
    columns: Mapping[int, int] = field(default_factory=dict)
    standalone: frozenset[int] = frozenset()

    def text_at(self, line: int) -> str | None:
        return self.by_line.get(line)

    def column_at(self, line: int) -> int | None:
        """0-based column where the comment on ``line`` starts, if there is one."""
        return self.columns.get(line)

    def is_standalone(self, line: int) -> bool:
        """True when ``line`` holds a comment and nothing else.

        A comment on its own line belongs to what follows it; a comment trailing a
        statement belongs to that statement. Only the former can be part of a
        multi-line comment block.
        """
        return line in self.standalone

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
    columns: dict[int, int] = {}
    standalone: set[int] = set()
    ignores: dict[int, set[str]] = {}
    skip_file = False

    readline = io.StringIO(source).readline
    try:
        for token in tokenize.generate_tokens(readline):
            if token.type != tokenize.COMMENT:
                continue
            line = token.start[0]
            by_line[line] = token.string
            columns[line] = token.start[1]
            if not token.line[: token.start[1]].strip():
                standalone.add(line)
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
        columns=columns,
        standalone=frozenset(standalone),
    )


def safety_invariant_at(comments: CommentMap, line: int) -> str | None:
    """The invariant text of a ``# SAFETY:`` comment on ``line``, if any.

    Returns ``None`` when the line carries no comment, no ``SAFETY:`` marker, or a
    marker with nothing after the colon: an empty ``# SAFETY:`` states no invariant
    and is therefore no safety comment at all.
    """
    text = comments.text_at(line)
    if text is None:
        return None
    match = _SAFETY_RE.search(text)
    if match is None:
        return None
    invariant = match.group("invariant").strip()
    return invariant or None


def safety_above(comments: CommentMap, line: int) -> str | None:
    """The invariant stated in the comment block directly above ``line``, if any.

    A real invariant rarely fits on one line, so the whole contiguous run of own-line
    comments above ``line`` counts as one block::

        # SAFETY: the walker looks the handler up by the runtime type of the node it
        # is about to dispatch, so it is only ever called with that node type.
        erased = cast("Handler", handler)

    The climb stops at the first line that is not an own-line comment, so a comment
    trailing an unrelated statement two lines up never leaks into the block.
    """
    current = line - 1
    invariant = safety_invariant_at(comments, current)
    if invariant is not None:
        return invariant
    while comments.is_standalone(current):
        current -= 1
        invariant = safety_invariant_at(comments, current)
        if invariant is not None:
            return invariant
    return None


def safety_comment_for(
    comments: CommentMap,
    node: ast.AST,
    parent_of: Callable[[ast.AST], ast.AST | None],
) -> str | None:
    """The ``# SAFETY:`` invariant covering ``node``, or ``None``.

    Four positions count, in this order:

    1. the end of ``node``'s own line (a trailing comment);
    2. the comment block directly above ``node`` (see :func:`safety_above`);
    3. the end of the line of the *statement* that owns ``node``;
    4. the comment block directly above that statement.

    Positions 3 and 4 are what make a multi-line expression work: in

    .. code-block:: python

        # SAFETY: the row comes from our own primary key column
        users = [
            cast(User, row)
            for row in rows
        ]

    the ``cast`` node starts three lines below the comment, but its owning statement
    starts directly below it. ``ast.stmt`` is the Python equivalent of the original
    rule's ``commentOwnerKinds`` list (``ExpressionStatement``, ``Return``,
    ``Assign``, ``Raise``, ...): every one of those kinds is an ``ast.stmt``.

    ``parent_of`` is normally :meth:`RuleContext.parent_of`; passing it in keeps this
    module independent of the walker.
    """
    owner = _owning_statement(node, parent_of)
    own_line = _line_of(node)
    lines = [line for line in (own_line, owner.lineno if owner else None) if line]

    for line in lines:
        invariant = safety_invariant_at(comments, line) or safety_above(comments, line)
        if invariant is not None:
            return invariant
    return None


def checker_suppressions(comments: CommentMap) -> tuple[CheckerSuppression, ...]:
    """Every type-checker suppression in the file, in source order.

    One comment token can carry several (``# type: ignore[arg-type]  # pyright:
    ignore[reportAny]``); each is returned separately. The combined spelling
    ``# type: ignore[arg-type, ty:possibly-unbound]`` stays a single ``type``
    directive whose ``codes`` include the ty-targeted entry, because that is what it
    is: one suppression, read by two checkers.
    """
    found: list[CheckerSuppression] = []
    for line in sorted(comments.by_line):
        text = comments.by_line[line]
        base_col = comments.column_at(line) or 0
        for match in _CHECKER_IGNORE_RE.finditer(text):
            raw_codes = match.group("codes")
            codes = (
                ()
                if raw_codes is None
                else tuple(part.strip() for part in raw_codes.split(",") if part.strip())
            )
            found.append(
                CheckerSuppression(
                    line=line,
                    col=base_col + match.start(),
                    end_col=base_col + match.end(),
                    checker=match.group("checker"),
                    codes=codes,
                    text=match.group(0),
                )
            )
    return tuple(found)


def _line_of(node: ast.AST) -> int | None:
    """The 1-based start line of ``node``, or ``None`` when it carries no position.

    Position-less nodes exist -- ``ast.Load``, ``ast.operator``, ``ast.Module`` --
    and a rule may legitimately ask about one; they simply have no own line to check.
    """
    match node:
        case (
            ast.expr()
            | ast.stmt()
            | ast.arg()
            | ast.excepthandler()
            | ast.keyword()
            | ast.alias()
        ):
            return node.lineno
        case _:
            return None


def _owning_statement(
    node: ast.AST, parent_of: Callable[[ast.AST], ast.AST | None]
) -> ast.stmt | None:
    """``node`` itself when it is a statement, otherwise its nearest statement ancestor."""
    if isinstance(node, ast.stmt):
        return node
    current = parent_of(node)
    while current is not None:
        if isinstance(current, ast.stmt):
            return current
        current = parent_of(current)
    return None


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
