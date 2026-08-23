"""Rule and diagnostic primitives: the vocabulary every anti-slop rule is written in.

A rule is data, not a class hierarchy: an id, agent-executable messages, an option
schema, a block of :class:`RuleMetadata`, and a tuple of node-type -> handler
bindings. The walker (``walker.py``) collects the bindings of every enabled rule and
dispatches one AST pass to all of them at once.

:class:`RuleMetadata` is the single source of truth for everything *about* a rule
rather than *inside* it: which tier it belongs to, how much a finding can be trusted,
and the four prose blocks ``--explain`` prints. A rule module's docstring describes
its detection mechanics; the metadata describes the policy, so neither has to
restate the other.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from anti_slop.engine.context import RuleContext

__all__ = [
    "CONFIDENCES",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_POLICY",
    "FIXES",
    "FIX_NONE",
    "MAX_TAGS",
    "TIERS",
    "TIER_ARCHITECTURAL",
    "TIER_ESCAPE_HATCH",
    "TIER_FRAMEWORK",
    "AnchorNode",
    "BoolOption",
    "Diagnostic",
    "Handler",
    "HandlerEntry",
    "OptionSpec",
    "OptionValue",
    "Rule",
    "RuleMetadata",
    "StrListOption",
    "on",
]

# The two tiers of the core rule set, plus the tier every contrib group's rules
# carry. `escape-hatch` bans constructs whose main effect is to discard evidence the
# type checker already had; `architectural` encodes a policy reasonable teams reject
# wholesale; `framework` is opt-in policy about one framework's own API.
TIER_ESCAPE_HATCH = "escape-hatch"
TIER_ARCHITECTURAL = "architectural"
TIER_FRAMEWORK = "framework"
TIERS: tuple[str, ...] = (TIER_ESCAPE_HATCH, TIER_ARCHITECTURAL, TIER_FRAMEWORK)

# How much a finding can be trusted without reading the surrounding code. `high` --
# the construct itself is the defect; `medium` -- the detection depends on resolution
# this linter does only partially; `policy` -- the construct is legal and idiomatic
# for some teams, and the finding is a statement of the project's chosen policy.
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_POLICY = "policy"
CONFIDENCES: tuple[str, ...] = (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_POLICY,
)

# Every rule declares `fix = "none"`, and the field exists to keep saying so: the
# recipe is spelled out in the diagnostic, and a linter that rewrites code to satisfy
# its own rules becomes a generator of exactly the code it was built to reject.
FIX_NONE = "none"
FIXES: tuple[str, ...] = (FIX_NONE,)

# Tags are a short index, not a description: three is already more than a reader
# scans in a listing.
MAX_TAGS = 3

# Nodes that carry source positions and can therefore anchor a diagnostic.
type AnchorNode = (
    ast.expr | ast.stmt | ast.arg | ast.excepthandler | ast.keyword | ast.alias
)

# The value a rule option can hold after configuration parsing.
type OptionValue = bool | tuple[str, ...]

# A handler receives the per-file, per-rule context and the node it subscribed to.
type Handler = Callable[[RuleContext, ast.AST], None]

# A rule id is kebab-case, optionally prefixed by the opt-in group that owns it:
# `no-object-parameters` (core), `fastapi/no-dict-body-parameters` (contrib group).
# The prefix is part of the id everywhere -- configuration, diagnostics, and the
# suppression directive alike -- so a group can never collide with a core rule.
_RULE_ID_PATTERN = re.compile(
    r"(?:[a-z][a-z0-9]*(?:-[a-z0-9]+)*/)?[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
)


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One reported violation. Columns are 1-based, matching editor conventions.

    ``severity`` mirrors the ``RuleSetting`` the rule ran under: ``"error"`` or
    ``"warn"``. It defaults to ``"error"`` and sits last so every prior positional
    or keyword construction of a ``Diagnostic`` keeps working unchanged.
    """

    path: Path
    line: int
    col: int
    end_line: int
    end_col: int
    rule_id: str
    message: str
    severity: str = "error"

    @property
    def sort_key(self) -> tuple[str, int, int, str]:
        return (str(self.path), self.line, self.col, self.rule_id)


@dataclass(frozen=True, slots=True)
class BoolOption:
    """A boolean rule option, named in kebab-case as it appears in pyproject.toml."""

    name: str
    default: bool
    description: str


@dataclass(frozen=True, slots=True)
class StrListOption:
    """A list-of-strings rule option (e.g. the term list of a naming rule)."""

    name: str
    default: tuple[str, ...]
    description: str


type OptionSpec = BoolOption | StrListOption


@dataclass(frozen=True, slots=True)
class HandlerEntry:
    """A single ``node type -> handler`` subscription, produced by :func:`on`."""

    node_type: type[ast.AST]
    handler: Handler


def on[NodeT: ast.AST](
    node_type: type[NodeT],
    handler: Callable[[RuleContext, NodeT], None],
) -> HandlerEntry:
    """Bind ``handler`` to ``node_type`` for the shared single-pass walker."""
    # SAFETY: the walker looks the handler up by the runtime type of the node it is
    # about to dispatch, so `handler` is only ever called with an instance of
    # `node_type` (or a subclass). Widening the declared parameter to `ast.AST` for
    # storage cannot therefore produce a call with an unexpected node type.
    erased = cast("Handler", handler)
    return HandlerEntry(node_type=node_type, handler=erased)


@dataclass(frozen=True, slots=True)
class RuleMetadata:
    """What a rule *is*, as data: its tier, its trustworthiness, and its rationale.

    The four prose fields become four of the five sections ``--explain`` prints; the
    fifth, "what it catches", is the rule's own ``description``. They are written
    once, here, and read by the CLI, so a rule module's docstring can stay about
    detection mechanics instead of restating policy.

    Every field is mandatory and every field is validated at construction: a rule
    that ships without a rationale, or with a tier nobody defined, fails at import
    rather than reaching a user as a blank ``--explain`` section.
    """

    tier: str
    confidence: str
    fix: str
    tags: tuple[str, ...]
    problem: str
    recipe: str
    when_to_disable: str
    fp_caveats: str

    def __post_init__(self) -> None:
        _require_member("tier", self.tier, TIERS)
        _require_member("confidence", self.confidence, CONFIDENCES)
        _require_member("fix", self.fix, FIXES)
        self._check_tags()
        _require_text("problem", self.problem)
        _require_text("recipe", self.recipe)
        _require_text("when_to_disable", self.when_to_disable)
        _require_text("fp_caveats", self.fp_caveats)

    def _check_tags(self) -> None:
        if not self.tags:
            message = "rule metadata declares no tags"
            raise ValueError(message)
        if len(self.tags) > MAX_TAGS:
            message = (
                f"rule metadata declares {len(self.tags)} tags,"
                f" more than the {MAX_TAGS} a listing shows"
            )
            raise ValueError(message)
        if len(set(self.tags)) != len(self.tags):
            message = f"rule metadata repeats a tag: {', '.join(self.tags)}"
            raise ValueError(message)
        for tag in self.tags:
            _require_text("tag", tag)


def _require_member(field: str, value: str, allowed: Sequence[str]) -> None:
    if value not in allowed:
        message = (
            f"rule metadata {field}={value!r} is not one of {', '.join(allowed)}"
        )
        raise ValueError(message)


def _require_text(field: str, value: str) -> None:
    if not value.strip():
        message = f"rule metadata {field} is empty"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Rule:
    """A rule definition.

    ``messages`` maps a message id to a ``str.format`` template; every template must
    say what to do *instead*, not merely that something is forbidden. ``metadata``
    carries the rule's tier, confidence, tags and rationale -- see
    :class:`RuleMetadata`.
    """

    id: str
    description: str
    messages: Mapping[str, str]
    handlers: tuple[HandlerEntry, ...]
    metadata: RuleMetadata
    options: tuple[OptionSpec, ...] = ()

    def __post_init__(self) -> None:
        if _RULE_ID_PATTERN.fullmatch(self.id) is None:
            message = f"rule id {self.id!r} is not kebab-case, or 'group/kebab-case'"
            raise ValueError(message)
        if not self.description.strip():
            message = f"rule {self.id!r} declares no description"
            raise ValueError(message)
        if not self.messages:
            message = f"rule {self.id!r} declares no messages"
            raise ValueError(message)
        seen: set[str] = set()
        for spec in self.options:
            if spec.name in seen:
                message = f"rule {self.id!r} declares option {spec.name!r} twice"
                raise ValueError(message)
            seen.add(spec.name)

    def option_names(self) -> frozenset[str]:
        return frozenset(spec.name for spec in self.options)

    def option_spec(self, name: str) -> OptionSpec | None:
        for spec in self.options:
            if spec.name == name:
                return spec
        return None

    def default_options(self) -> dict[str, OptionValue]:
        return {spec.name: spec.default for spec in self.options}
