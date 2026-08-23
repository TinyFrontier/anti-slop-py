"""Tests for the single-pass walker: document order, parent links, MRO dispatch."""

from __future__ import annotations

import ast
from pathlib import Path

from anti_slop.engine.comments import build_comment_map
from anti_slop.engine.context import RuleContext
from anti_slop.engine.rule import Diagnostic, Rule, on
from anti_slop.engine.walker import ParentMap, Walker
from harness import PROBE_METADATA

SOURCE = """class Repository:
    def save(self, value):
        return value
"""

PATH = Path("snippet.py")


def _report_statement(context: RuleContext, node: ast.stmt) -> None:
    context.report(node, "seen", kind=type(node).__name__)


def _report_name(context: RuleContext, node: ast.Name) -> None:
    context.report(node, "seen", kind=type(node).__name__)


STATEMENT_PROBE = Rule(
    id="probe-statements",
    description="probe rule subscribing to the ast.stmt base class",
    messages={"seen": "{kind}"},
    handlers=(on(ast.stmt, _report_statement),),
    metadata=PROBE_METADATA,
)

NAME_PROBE = Rule(
    id="probe-names",
    description="probe rule subscribing to a single leaf node type",
    messages={"seen": "{kind}"},
    handlers=(on(ast.Name, _report_name),),
    metadata=PROBE_METADATA,
)


def run_probe(rule: Rule) -> tuple[list[str], ParentMap, ast.Module]:
    tree = ast.parse(SOURCE)
    parents = ParentMap()
    collected: list[Diagnostic] = []
    context = RuleContext(
        path=PATH,
        source=SOURCE,
        lines=SOURCE.splitlines(),
        comments=build_comment_map(PATH, SOURCE),
        parents=parents,
        rule=rule,
        options={},
        report=collected.append,
    )
    walker = Walker(parents)
    walker.subscribe(context, rule.handlers)
    walker.run(tree)
    return [diagnostic.message for diagnostic in collected], parents, tree


def test_parent_links_climb_to_the_module() -> None:
    _, parents, tree = run_probe(STATEMENT_PROBE)

    class_def = tree.body[0]
    assert isinstance(class_def, ast.ClassDef)
    function_def = class_def.body[0]
    assert isinstance(function_def, ast.FunctionDef)

    assert parents.parent_of(function_def) is class_def
    assert parents.parent_of(class_def) is tree
    assert parents.parent_of(tree) is None
    assert list(parents.ancestors(function_def)) == [class_def, tree]


def test_subscribing_to_a_base_class_catches_every_subclass_in_document_order() -> None:
    seen, _, _ = run_probe(STATEMENT_PROBE)
    assert seen == ["ClassDef", "FunctionDef", "Return"]


def test_subscribing_to_a_leaf_type_visits_only_that_type() -> None:
    seen, _, _ = run_probe(NAME_PROBE)
    assert seen == ["Name"]
