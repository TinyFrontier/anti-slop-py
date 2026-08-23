"""The rule catalogue: how rule metadata is rendered for a reader or a machine.

``--list-rules`` and ``--explain`` are two views of the same data. Neither reads a
rule's source or its docstring: everything printed here comes from :class:`Rule` and
its :class:`~anti_slop.engine.rule.RuleMetadata`, so the prose a user is shown and the
prose a rule declares can never drift apart.

The JSON shapes are a contract. ``--list-rules --format json`` emits one object per
rule with the keys ``id``, ``summary``, ``tier``, ``confidence``, ``default_level``,
``fix``, ``tags`` and ``options``; ``--explain --format json`` emits the same
identity fields plus the four prose blocks. Both are covered by snapshot tests, so a
key does not change name without the change being noticed.
"""

from __future__ import annotations

import difflib
import json
import re
import textwrap
from collections.abc import Iterable, Mapping, Sequence

from anti_slop.engine.rule import BoolOption, OptionSpec, Rule

__all__ = [
    "DEFAULT_LEVEL",
    "OPTION_TYPE_BOOL",
    "OPTION_TYPE_STRING_LIST",
    "condense",
    "explain_json",
    "explain_text",
    "listing_key",
    "rule_list_json",
    "rule_list_text",
    "similar_rule_ids",
]

# How an option's value type is named in the JSON listing.
OPTION_TYPE_BOOL = "bool"
OPTION_TYPE_STRING_LIST = "string-list"

# The level of a rule the configuration never mentions -- the same answer
# `config._default_settings` gives.
DEFAULT_LEVEL = "error"

# An unknown rule id is answered with a short list of near misses, not the whole
# registry: three is enough to catch a typo and short enough to read.
_MAX_SUGGESTIONS = 3
_SUGGESTION_CUTOFF = 0.6

# Prose is wrapped for a terminal; the indent marks a section's body.
_WRAP_WIDTH = 79
_BODY_INDENT = "  "

# `condense` takes at most this many opening sentences, and stops early once they
# exceed this many characters: a review comment gets a reason, not an essay.
# `--explain` stays the long form.
_CONDENSED_SENTENCES = 2
_CONDENSED_BUDGET = 240

# A sentence boundary: closing punctuation followed by whitespace. Periods inside
# code spans (`obj.attr`, `.shape`) carry no following space and are therefore not
# boundaries, which is why this needs no list of abbreviations.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")

# One JSON option entry, and one JSON rule entry. The unions are exact rather than
# `object`: this file is linted by the rules it describes.
type OptionJson = dict[str, str | bool | list[str]]
type RuleJson = dict[str, str | list[str] | list[OptionJson]]


def listing_key(rule: Rule) -> tuple[str, str]:
    """``("", id)`` for a core rule, ``(group, id)`` for a group rule.

    Sorting by this puts the core rules first and each group's rules after them,
    alphabetical within both.
    """
    return (rule.id.rpartition("/")[0], rule.id)


def rule_list_text(rules: Sequence[Rule]) -> str:
    """``id  [tier, confidence]  summary``, with each rule's options underneath."""
    lines: list[str] = []
    for rule in sorted(rules, key=listing_key):
        metadata = rule.metadata
        lines.append(
            f"{rule.id}  [{metadata.tier}, {metadata.confidence}]"
            f"  {rule.description}"
        )
        for spec in rule.options:
            lines.append(f"    {spec.name} (default: {_render_default(spec)})")
    return "\n".join(lines)


def rule_list_json(rules: Sequence[Rule], levels: Mapping[str, str]) -> str:
    """The rule listing as JSON, in the same order as :func:`rule_list_text`.

    ``levels`` maps a rule id to the level it runs at under the configuration that
    was loaded -- which is what a preset changes -- and lands in ``default_level``.
    """
    payload = [
        _rule_entry(rule, levels.get(rule.id, DEFAULT_LEVEL))
        for rule in sorted(rules, key=listing_key)
    ]
    return json.dumps(payload, indent=2)


def explain_text(rule: Rule) -> str:
    """The five explain sections, wrapped for a terminal."""
    metadata = rule.metadata
    blocks = [
        f"{rule.id}  [{metadata.tier}, {metadata.confidence}, fix: {metadata.fix}]",
        f"tags: {', '.join(metadata.tags)}",
    ]
    for title, body in _sections(rule):
        blocks.append(f"{title}\n{_wrap(body)}")
    return "\n\n".join(blocks)


def explain_json(rule: Rule) -> str:
    """The same five sections as :func:`explain_text`, keyed by field name."""
    metadata = rule.metadata
    payload = {
        "id": rule.id,
        "summary": rule.description,
        "tier": metadata.tier,
        "confidence": metadata.confidence,
        "fix": metadata.fix,
        "tags": list(metadata.tags),
        "problem": metadata.problem,
        "recipe": metadata.recipe,
        "when_to_disable": metadata.when_to_disable,
        "fp_caveats": metadata.fp_caveats,
    }
    return json.dumps(payload, indent=2)


def condense(prose: str) -> str:
    """The opening sentence or two of a metadata block, for a one-line context.

    Selection, not summarization: the result is a prefix of what the rule already
    declares, so the short form ``review`` prints and the long form ``--explain``
    prints can never drift apart, and neither depends on the code being reviewed.
    Prose with no sentence break comes back whole.
    """
    sentences = _SENTENCE_BREAK.split(prose.strip())
    taken = sentences[:1]
    for sentence in sentences[1:_CONDENSED_SENTENCES]:
        candidate = f"{' '.join(taken)} {sentence}"
        if len(candidate) > _CONDENSED_BUDGET:
            break
        taken.append(sentence)
    return " ".join(taken)


def similar_rule_ids(wanted: str, known: Iterable[str]) -> tuple[str, ...]:
    """Up to three known ids close enough to ``wanted`` to be worth suggesting."""
    return tuple(
        difflib.get_close_matches(
            wanted, sorted(known), n=_MAX_SUGGESTIONS, cutoff=_SUGGESTION_CUTOFF
        )
    )


def _sections(rule: Rule) -> tuple[tuple[str, str], ...]:
    """Section title -> body, in the order ``--explain`` prints them.

    The first section is the rule's own one-line description; the other four are the
    prose blocks of its metadata.
    """
    metadata = rule.metadata
    return (
        ("What it catches", rule.description),
        ("Why it matters", metadata.problem),
        ("Write instead", metadata.recipe),
        ("When to disable", metadata.when_to_disable),
        ("False-positive caveats", metadata.fp_caveats),
    )


def _rule_entry(rule: Rule, level: str) -> RuleJson:
    metadata = rule.metadata
    return {
        "id": rule.id,
        "summary": rule.description,
        "tier": metadata.tier,
        "confidence": metadata.confidence,
        "default_level": level,
        "fix": metadata.fix,
        "tags": list(metadata.tags),
        "options": [_option_entry(spec) for spec in rule.options],
    }


def _option_entry(spec: OptionSpec) -> OptionJson:
    if isinstance(spec, BoolOption):
        return {"name": spec.name, "type": OPTION_TYPE_BOOL, "default": spec.default}
    return {
        "name": spec.name,
        "type": OPTION_TYPE_STRING_LIST,
        "default": list(spec.default),
    }


def _render_default(spec: OptionSpec) -> str:
    """An option's default as it would be written in ``pyproject.toml``."""
    if isinstance(spec, BoolOption):
        return "true" if spec.default else "false"
    return "[" + ", ".join(repr(term) for term in spec.default) + "]"


def _wrap(body: str) -> str:
    return textwrap.fill(
        body,
        width=_WRAP_WIDTH,
        initial_indent=_BODY_INDENT,
        subsequent_indent=_BODY_INDENT,
    )
