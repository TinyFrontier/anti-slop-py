"""``require-safety-comment`` -- every cast and every checker suppression states its invariant.

PLAN.md section 3.4, row 15 (port of ``require-safety-comment-for-type-assertion``).
A ``typing.cast`` and a ``# type: ignore`` are the same move written two ways: both
tell the checker "accept this, I have proof you cannot see". The proof is the whole
point, and it is exactly what disappears when an agent reaches for either one to make
a red line go away. This rule demands the proof be written down as
``# SAFETY: <invariant>`` -- the invariant the checker cannot express, verified by a
human or by code the checker cannot follow.

There are no exceptions. TypeScript's ``as const`` has no Python counterpart (PLAN.md
section 3.4, callout after the table), and ``cast`` is unsafe by construction: unlike
``isinstance``, it checks nothing at runtime, it only silences the checker.

Three independent defects are reported:

``cast``
    a ``cast(...)`` call with no ``# SAFETY:`` comment covering it. Coverage follows
    :func:`~anti_slop.engine.comments.safety_comment_for`, which also accepts a
    comment above the *owning statement* -- the port of the original's
    ``commentOwnerKinds``, so a cast buried inside a multi-line expression is still
    covered by the comment above that expression's statement.

``suppression``
    a ``# type: ignore`` / ``# ty: ignore[...]`` / ``# pyright: ignore[...]`` with no
    ``# SAFETY:`` on its own line or in the comment block directly above it. There is
    no node to climb from, so the owning-statement fallback does not apply: a
    directive suppresses exactly the line it sits on.

``bare_type_ignore`` / ``bare_suppression``
    a suppression that names no error code. This is a violation *always*, even with a
    perfect ``# SAFETY:`` comment: a code-less directive silences every error on the
    line, including errors introduced later by edits that have nothing to do with the
    invariant that was written down.

The last check is deliberately independent of the ``# SAFETY:`` check, so a bare
``# type: ignore`` with no safety comment reports twice -- once for the missing
invariant, once for the missing code. They are separate defects with separate fixes.

Not a checker suppression: anti-slop's own ``# anti-slop: ignore[rule-id]``. It names
a rule of *this* linter, is already required to name it (PLAN.md FR-3), and is parsed
by a different directive grammar entirely.

Known limitation, shared with ``no-chained-casts``: callee detection is syntactic --
a bare name ``cast`` or any attribute access ending in ``.cast``. An unrelated helper
literally named ``cast`` (SQLAlchemy's, say) is flagged too. Narrowing this to names
that actually resolve to ``typing.cast`` needs import resolution.
"""

from __future__ import annotations

import ast

from anti_slop.engine.comments import (
    CheckerSuppression,
    checker_suppressions,
    safety_above,
    safety_comment_for,
    safety_invariant_at,
)
from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Rule, on

__all__ = ["RULE", "RULE_ID"]

RULE_ID = "require-safety-comment"

# How much of a rendered target type to show in a message before truncating.
_TARGET_LIMIT = 60

_CAST_MESSAGE = (
    "`cast` to `{target}` asserts a claim the type checker cannot verify: from here on"
    " the value is `{target}` on your word alone, with nothing checked at runtime."
    " State the invariant you actually verified -- the one the checker cannot express"
    " -- in a `# SAFETY:` comment on this line or directly above this statement, e.g."
    " `# SAFETY: the row is selected by primary key, so the column is never NULL`."
    " If no such invariant exists, do not cast: parse the value into `{target}` where"
    " it enters this code -- a dataclass or TypedDict constructor, a pydantic/msgspec"
    " model, or an explicit `isinstance` check -- so the type is proven, not asserted."
)

_SUPPRESSION_MESSAGE = (
    "`{directive}` silences the checker without saying why that is safe. The checker"
    " reported here because it could not prove something; suppressing it moves the"
    " proof onto the reader and leaves nothing for the next edit to re-check. Write"
    " the verified invariant the checker cannot express in a `# SAFETY:` comment on"
    " this line or directly above it, e.g. `# SAFETY: the loader populates the"
    " registry before any lookup runs`. If you cannot state one, fix the type instead"
    " of hiding the error."
)

_BARE_TYPE_IGNORE_MESSAGE = (
    "`{directive}` names no error code, so it silences every error on this line --"
    " including errors introduced later by edits unrelated to the invariant you had"
    " in mind. Name the code you actually mean: `# type: ignore[arg-type]`. Note that"
    " ty ignores the codes inside `type: ignore[...]` and treats the whole directive"
    " as blanket anyway, so where ty is the checker of this repository, prefer its own"
    " dialect `# ty: ignore[<rule>]`, which really is scoped to the named rule."
)

_BARE_SUPPRESSION_MESSAGE = (
    "`{directive}` names no rule, so it silences every diagnostic {checker} would"
    " raise on this line, now and after any future edit. Name the rule you actually"
    " mean, e.g. `# {checker}: ignore[<rule>]`, so a new, unrelated error on this line"
    " still gets reported."
)


def _check_call(context: RuleContext, node: ast.Call) -> None:
    if not _is_cast_callee(node.func):
        return
    if safety_comment_for(context.comments, node, context.parent_of) is not None:
        return
    context.report(node, "cast", target=_target_name(node))


def _check_module(context: RuleContext, _module: ast.Module) -> None:
    """One pass over the comment map: casts live in the AST, suppressions do not.

    Subscribing to ``ast.Module`` is how a rule asks to run once per file; the module
    node itself carries nothing this check needs, because the directives come from
    the tokenizer-built comment map rather than from the tree.
    """
    for suppression in checker_suppressions(context.comments):
        _check_suppression(context, suppression)


def _check_suppression(context: RuleContext, suppression: CheckerSuppression) -> None:
    anchor = _anchor(suppression)
    directive = suppression.text
    if not _has_adjacent_safety(context, suppression.line):
        context.report(anchor, "suppression", directive=directive)
    if suppression.has_codes:
        return
    if suppression.checker == "type":
        context.report(anchor, "bare_type_ignore", directive=directive)
    else:
        context.report(
            anchor, "bare_suppression", directive=directive, checker=suppression.checker
        )


def _has_adjacent_safety(context: RuleContext, line: int) -> bool:
    """A suppression is covered by a ``# SAFETY:`` on its line or in the block above.

    There is no AST node to climb from, so the owning-statement fallback of
    :func:`~anti_slop.engine.comments.safety_comment_for` does not apply here: a
    directive suppresses exactly the line it sits on, and the invariant belongs next
    to it -- on the same line, or in the comment block immediately above it.
    """
    if safety_invariant_at(context.comments, line) is not None:
        return True
    return safety_above(context.comments, line) is not None


def _anchor(suppression: CheckerSuppression) -> ast.Pass:
    """A positioned stand-in node for a comment, which ``ast`` never represents.

    ``RuleContext.report`` anchors a diagnostic on an AST node, and a comment is not
    one. An ``ast.Pass`` carrying the directive's own source span is the smallest
    honest placeholder: it exists only to hand the position to the diagnostic and is
    never part of the parsed tree.
    """
    return ast.Pass(
        lineno=suppression.line,
        col_offset=suppression.col,
        end_lineno=suppression.line,
        end_col_offset=suppression.end_col,
    )


def _is_cast_callee(func: ast.expr) -> bool:
    """A bare ``cast`` name or any attribute access ending in ``.cast``.

    Duplicated from ``no_chained_casts`` on purpose: the two rules are read and
    edited independently once vendored, and a five-line syntactic predicate is not
    worth a shared module they would both have to be read alongside.
    """
    match func:
        case ast.Name(id="cast"):
            return True
        case ast.Attribute(attr="cast"):
            return True
        case _:
            return False


def _target_name(call: ast.Call) -> str:
    """The target type of ``cast(typ, val)`` as source text, for the message."""
    target = _target_argument(call)
    if target is None:
        return "the target type"
    match target:
        case ast.Constant(value=str() as text):
            rendered = text.strip()
        case _:
            rendered = ast.unparse(target)
    if len(rendered) > _TARGET_LIMIT:
        return rendered[: _TARGET_LIMIT - 1] + "…"
    return rendered


def _target_argument(call: ast.Call) -> ast.expr | None:
    """The type passed to ``cast(typ, val)``: the first positional arg, or ``typ=``."""
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == "typ":
            return keyword.value
    return None


RULE = Rule(
    id=RULE_ID,
    description=(
        "Every `cast` and every type-checker suppression must state its verified"
        " invariant in a `# SAFETY:` comment, and every suppression must name a code."
    ),
    messages={
        "cast": _CAST_MESSAGE,
        "suppression": _SUPPRESSION_MESSAGE,
        "bare_type_ignore": _BARE_TYPE_IGNORE_MESSAGE,
        "bare_suppression": _BARE_SUPPRESSION_MESSAGE,
    },
    handlers=(
        on(ast.Call, _check_call),
        on(ast.Module, _check_module),
    ),
)
