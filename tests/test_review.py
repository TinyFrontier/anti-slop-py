"""``anti-slop review``: the subcommand, its posture, and its two renderings.

The fixture is a real git repository carrying a real uncommitted change, for the same
reason ``test_diff.py`` builds one: this mode's whole claim is that it reports what an
agent just did to the working tree, and a hand-written patch string could only prove
that the test author agrees with themselves.

The compatibility tests at the bottom are the other half of the feature. A
subcommand in front of a CLI that already takes positional paths is a chance to break
every existing invocation, so the ones that matter are pinned here explicitly --
including the repository that happens to contain a directory called ``review``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from anti_slop import CORE_RULES
from anti_slop.__main__ import main
from anti_slop.engine.baseline import DEFAULT_BASELINE_NAME
from anti_slop.engine.catalog import condense
from anti_slop.engine.config import known_rules
from anti_slop.engine.review import SECTIONS
from anti_slop.engine.rule import Rule

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="review is built on the git CLI"
)

HIGH_RULE = "no-widen-then-cast"
MEDIUM_RULE = "no-unsafe-dict-values"
POLICY_RULE = "no-adhoc-isinstance"

PROJECT_CONFIG = """
    [tool.anti-slop]
    include = ["src"]
"""

BASE_MODULE = """
    class Invoice:
        total: int
"""

# One finding per confidence, and nothing else: the cast carries its `# SAFETY:`
# comment so `require-safety-comment` stays quiet, and `render` takes a union rather
# than `object` so the only architectural finding is the `isinstance` branch.
AGENT_CHANGE = """
    from typing import Any, cast


    class Invoice:
        total: int


    def settle(invoice: Invoice) -> Invoice:
        raw: Any = invoice
        # SAFETY: raw is invoice widened one line up; the cast claims that back.
        return cast(Invoice, raw)


    def index(rows: dict[str, Any]) -> int:
        return len(rows)


    def render(value: Invoice | int) -> str:
        if isinstance(value, Invoice):
            return "invoice"
        return "other"
"""

POLICY_ONLY_CHANGE = """
    class Invoice:
        total: int


    def render(value: Invoice | int) -> str:
        if isinstance(value, Invoice):
            return "invoice"
        return "other"
"""

CLEAN_CHANGE = """
    class Invoice:
        total: int


    def doubled(invoice: Invoice) -> int:
        return invoice.total * 2
"""


@pytest.fixture(autouse=True)
def hermetic_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore the developer's own git configuration and identity, in both directions."""
    settings = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "anti-slop tests",
        "GIT_AUTHOR_EMAIL": "tests@example.invalid",
        "GIT_COMMITTER_NAME": "anti-slop tests",
        "GIT_COMMITTER_EMAIL": "tests@example.invalid",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)


def git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments), cwd=repo, capture_output=True, encoding="utf-8", check=True
    )


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return path


def make_repo(root: Path, config: str = PROJECT_CONFIG) -> Path:
    """A committed repository with one clean module, ready to be edited."""
    write(root / "pyproject.toml", config)
    write(root / "src" / "billing.py", BASE_MODULE)
    git(root, "init", "-b", "main", ".")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    return root


def edit(root: Path, content: str) -> Path:
    return write(root / "src" / "billing.py", content)


def review(root: Path, *arguments: str) -> int:
    return main(
        [
            "review",
            "--base",
            "main",
            *arguments,
            "--config",
            str(root / "pyproject.toml"),
        ]
    )


def headings(output: str) -> list[str]:
    titles = {heading for _, heading in SECTIONS}
    return [line for line in output.splitlines() if line in titles]


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #


def test_three_confidences_are_three_sections_in_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, AGENT_CHANGE)

    code = review(root)
    out = capsys.readouterr().out

    assert code == 1
    assert headings(out) == ["HIGH CONFIDENCE", "MEDIUM CONFIDENCE", "POLICY"]
    assert out.index(HIGH_RULE) < out.index(MEDIUM_RULE) < out.index(POLICY_RULE)
    assert out.rstrip().endswith(
        "3 findings: 1 high confidence, 1 medium confidence, 1 policy"
        " (2 blocking, 1 warning)"
    )


def test_each_finding_carries_a_location_message_why_and_instead(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, AGENT_CHANGE)

    review(root)
    lines = capsys.readouterr().out.splitlines()
    start = lines.index("HIGH CONFIDENCE")

    assert lines[start + 1].startswith(f"src/billing.py:11 {HIGH_RULE}")
    assert lines[start + 2].startswith("  `raw` is `invoice` widened")
    assert lines[start + 3].startswith("  Why: A binding with a proven type")
    assert lines[start + 4].startswith("  Instead: Delete the widening step")


def test_an_empty_section_is_omitted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, POLICY_ONLY_CHANGE)

    review(root)
    out = capsys.readouterr().out

    assert headings(out) == ["POLICY"]


def test_a_policy_finding_warns_and_does_not_decide_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The `agent` preset in one test: policy reports, and reporting is not failing."""
    root = make_repo(tmp_path)
    edit(root, POLICY_ONLY_CHANGE)

    code = review(root)
    out = capsys.readouterr().out

    assert code == 0
    assert "  warning: `isinstance" in out
    assert "1 finding: 1 policy (0 blocking, 1 warning)" in out


def test_a_clean_change_says_so_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, CLEAN_CHANGE)

    code = review(root)
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.strip() == "No findings on the lines this change touched."


def test_an_untouched_violation_is_not_this_change_s(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    write(root / "src" / "legacy.py", "def save(value: object) -> None:\n    ...\n")
    git(root, "add", "-A")
    git(root, "commit", "-m", "legacy")
    edit(root, CLEAN_CHANGE)

    assert review(root) == 0
    assert "legacy.py" not in capsys.readouterr().out


def test_paths_are_relative_to_the_configuration_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, AGENT_CHANGE)

    review(root)
    out = capsys.readouterr().out

    assert "src/billing.py:11" in out
    assert str(root) not in out


# --------------------------------------------------------------------------- #
# --format json
# --------------------------------------------------------------------------- #


def test_json_carries_the_base_the_preset_and_the_enriched_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, AGENT_CHANGE)

    code = review(root, "--format", "json")
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["base"] == "main"
    assert payload["preset"] == "agent"
    assert [finding["rule"] for finding in payload["findings"]] == [
        HIGH_RULE,
        MEDIUM_RULE,
        POLICY_RULE,
    ]
    first = payload["findings"][0]
    assert set(first) == {
        "path",
        "line",
        "col",
        "endLine",
        "endCol",
        "rule",
        "severity",
        "message",
        "confidence",
        "tier",
        "why",
        "instead",
    }
    assert first["path"] == "src/billing.py"
    assert first["confidence"] == "high"
    assert first["tier"] == "escape-hatch"
    assert payload["findings"][2]["severity"] == "warn"


def test_json_is_byte_identical_across_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, AGENT_CHANGE)

    review(root, "--format", "json")
    first = capsys.readouterr().out
    review(root, "--format", "json")
    second = capsys.readouterr().out

    assert first == second
    assert first.strip()


def test_a_clean_json_review_is_still_a_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, CLEAN_CHANGE)

    code = review(root, "--format", "json")
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["findings"] == []


@pytest.mark.parametrize("output_format", ["github", "sarif"])
def test_a_diagnostics_only_format_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], output_format: str
) -> None:
    root = make_repo(tmp_path)
    edit(root, AGENT_CHANGE)

    code = review(root, "--format", output_format)
    captured = capsys.readouterr()

    assert code == 2
    assert "renders a report, not a diagnostics stream" in captured.err


# --------------------------------------------------------------------------- #
# which posture reviews the change
# --------------------------------------------------------------------------- #


def test_the_repository_preset_loses_to_the_review_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`strict` would fail this change; review is `agent`, so policy only reports."""
    root = make_repo(
        tmp_path,
        """
            [tool.anti-slop]
            include = ["src"]
            preset = "strict"
        """,
    )
    edit(root, POLICY_ONLY_CHANGE)

    code = review(root)
    out = capsys.readouterr().out

    assert code == 0
    assert "warning:" in out
    assert main(["--diff", "main", "--config", str(root / "pyproject.toml")]) == 1


def test_rules_still_win_over_the_review_preset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The full order: review's default, then --preset, then [tool.anti-slop.rules]."""
    root = make_repo(
        tmp_path,
        f"""
            [tool.anti-slop]
            include = ["src"]
            preset = "legacy"

            [tool.anti-slop.rules]
            "{POLICY_RULE}" = "error"
        """,
    )
    edit(root, POLICY_ONLY_CHANGE)

    code = review(root)
    out = capsys.readouterr().out

    assert code == 1
    assert "warning:" not in out


def test_preset_selects_another_posture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, POLICY_ONLY_CHANGE)

    assert review(root, "--preset", "agent-strict") == 1
    assert "warning:" not in capsys.readouterr().out

    assert review(root, "--preset", "minimal") == 0
    assert capsys.readouterr().out.strip() == (
        "No findings on the lines this change touched."
    )


def test_the_preset_name_reaches_the_json_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, AGENT_CHANGE)

    review(root, "--format", "json", "--preset", "agent-strict")
    payload = json.loads(capsys.readouterr().out)

    assert payload["preset"] == "agent-strict"


def test_an_unknown_preset_exits_two_and_lists_the_known_ones(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)

    code = review(root, "--preset", "paranoid")
    captured = capsys.readouterr()

    assert code == 2
    assert "unknown preset 'paranoid'" in captured.err
    assert "agent-strict" in captured.err


# --------------------------------------------------------------------------- #
# the other filters, and the failure modes
# --------------------------------------------------------------------------- #


def test_a_baseline_hides_what_it_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, AGENT_CHANGE)
    assert main(["--generate-baseline", "--config", str(root / "pyproject.toml")]) == 0
    capsys.readouterr()

    code = review(root, "--baseline", str(root / DEFAULT_BASELINE_NAME))
    out = capsys.readouterr().out

    assert code == 0
    assert "No findings on the lines this change touched." in out
    assert "3 baselined findings hidden (0 stale entries)" in out


def test_explicit_paths_bound_the_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)
    edit(root, AGENT_CHANGE)
    write(root / "src" / "other.py", "def other(value: object) -> None:\n    ...\n")

    assert review(root, str(root / "src" / "other.py")) == 0
    assert "billing.py" not in capsys.readouterr().out


def test_outside_a_git_repository_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "pyproject.toml", PROJECT_CONFIG)
    write(tmp_path / "src" / "billing.py", BASE_MODULE)

    code = review(tmp_path)
    captured = capsys.readouterr()

    assert code == 2
    assert "not inside a git repository" in captured.err


def test_an_unresolvable_base_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_repo(tmp_path)

    code = main(
        [
            "review",
            "--base",
            "origin/no-such-branch",
            "--config",
            str(root / "pyproject.toml"),
        ]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "cannot find a merge base between 'origin/no-such-branch'" in captured.err


def test_base_is_required(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["review", "--config", str(root / "pyproject.toml")])
    assert excinfo.value.code == 2


def test_review_has_no_diff_committed_counterpart(tmp_path: Path) -> None:
    """Reviewing the working tree is the point; the committed variant stays on --diff."""
    root = make_repo(tmp_path)
    config = str(root / "pyproject.toml")
    with pytest.raises(SystemExit) as excinfo:
        main(["review", "--diff-committed", "main", "--config", config])
    assert excinfo.value.code == 2


# --------------------------------------------------------------------------- #
# the subcommand must not have changed the command
# --------------------------------------------------------------------------- #


def test_a_directory_called_review_is_reached_through_a_path_spelling(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bare word is the subcommand; `./review`, `review/` and `-- review` are paths."""
    write(tmp_path / "pyproject.toml", PROJECT_CONFIG)
    write(tmp_path / "review" / "mod.py", "def save(value: object) -> None:\n    ...\n")
    config = str(tmp_path / "pyproject.toml")
    monkeypatch.chdir(tmp_path)

    for spelling in ("./review", "review/"):
        assert main([spelling, "--config", config]) == 1
        assert "no-object-parameters" in capsys.readouterr().out

    # `--` ends the options, so nothing may follow the path it introduces: the
    # configuration comes from upward discovery here, as it would for a real caller.
    assert main(["--", "review"]) == 1
    assert "no-object-parameters" in capsys.readouterr().out


def test_the_bare_word_review_is_the_subcommand_even_next_to_such_a_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "pyproject.toml", PROJECT_CONFIG)
    write(tmp_path / "review" / "mod.py", "def save(value: object) -> None:\n    ...\n")

    with pytest.raises(SystemExit) as excinfo:
        main(["review", "--config", str(tmp_path / "pyproject.toml")])

    assert excinfo.value.code == 2  # --base is required
    assert "no-object-parameters" not in capsys.readouterr().out


def test_a_path_argument_after_a_flag_is_never_a_subcommand(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(tmp_path / "pyproject.toml", PROJECT_CONFIG)
    write(tmp_path / "review" / "mod.py", "def save(value: object) -> None:\n    ...\n")
    monkeypatch.chdir(tmp_path)

    code = main(["--config", str(tmp_path / "pyproject.toml"), "review"])

    assert code == 1
    assert "no-object-parameters" in capsys.readouterr().out


def test_the_checker_still_answers_every_prior_invocation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "pyproject.toml", PROJECT_CONFIG)
    module = write(
        tmp_path / "src" / "mod.py", "def save(value: object) -> None:\n    ...\n"
    )
    config = str(tmp_path / "pyproject.toml")

    assert main(["--list-rules"]) == 0
    assert "no-object-parameters" in capsys.readouterr().out

    assert main(["--explain", "no-widen-then-cast"]) == 0
    assert "What it catches" in capsys.readouterr().out

    assert main([str(module), "--config", config]) == 1
    assert "no-object-parameters" in capsys.readouterr().out

    assert main([str(module), "--config", config, "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)[0]["rule"] == "no-object-parameters"


# --------------------------------------------------------------------------- #
# `Why` / `Instead` are selected from the metadata, never generated
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "rule", known_rules(CORE_RULES, "tests"), ids=lambda rule: rule.id
)
def test_every_rule_condenses_to_a_prefix_of_its_own_prose(rule: Rule) -> None:
    for block in (rule.metadata.problem, rule.metadata.recipe):
        short = condense(block)
        assert short
        assert block.startswith(short)
        assert len(short) <= len(block)


def test_condense_takes_the_second_sentence_only_when_both_are_short() -> None:
    two = condense("First one. Second one.")
    one = condense(f"First one. {'x' * 300}.")

    assert two == "First one. Second one."
    assert one == "First one."


def test_condense_leaves_prose_without_a_sentence_break_whole() -> None:
    assert condense("`obj.attr` and `.shape` are not breaks") == (
        "`obj.attr` and `.shape` are not breaks"
    )
