"""Diff mode over real git fixture repositories: ``--diff`` and ``--diff-committed``.

Every repository here is built by driving git itself rather than by faking its
output: the whole point of the feature is that it agrees with git about what changed,
and a hand-written patch string could only ever prove that the parser agrees with the
test author. The parser gets its own unit tests at the bottom, on the cases a fixture
repository cannot easily produce on demand.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from anti_slop.__main__ import main
from anti_slop.engine.baseline import DEFAULT_BASELINE_NAME
from anti_slop.engine.diff import parse_patch

RULE_ID = "no-object-parameters"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="diff mode is built on the git CLI"
)

PROJECT_CONFIG = """
    [tool.anti-slop]
    include = ["src"]
"""

LEGACY_MODULE = "def legacy(value: object) -> None:\n    ...\n"
APPENDED_VIOLATION = "\n\ndef appended(value: object) -> None:\n    ...\n"
CLEAN_APPENDED = "\n\ndef appended(value: int) -> int:\n    return value\n"


@pytest.fixture(autouse=True)
def hermetic_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore the developer's own git configuration and identity, in both directions.

    The linter runs its git queries in this same process's environment, so setting
    these here covers the fixture's commits and the code under test alike -- a global
    ``commit.gpgsign`` or ``diff.renames`` cannot reach either.
    """
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


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return completed.stdout


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return path


def make_repo(root: Path, project: Path | None = None) -> Path:
    """A git repository whose project (at ``project``, default ``root``) has one file.

    ``src/legacy.py`` starts out committed and already violating, which is what a
    repository adopting this tool actually looks like: diff mode has to stay silent
    about it until somebody touches it.
    """
    home = root if project is None else project
    write(home / "pyproject.toml", PROJECT_CONFIG)
    write(home / "src" / "legacy.py", LEGACY_MODULE)
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-b", "main", ".")
    git(root, "add", "-A")
    git(root, "commit", "-m", "initial")
    return home


def check(project: Path, *arguments: str) -> int:
    return main([*arguments, "--config", str(project / "pyproject.toml")])


def findings(captured: str) -> list[str]:
    return [line for line in captured.splitlines() if RULE_ID in line]


# --------------------------------------------------------------------------- #
# the working tree is the default
# --------------------------------------------------------------------------- #


def test_an_uncommitted_edit_is_reported_and_the_old_lines_are_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    module = project / "src" / "legacy.py"
    module.write_text(
        module.read_text(encoding="utf-8") + APPENDED_VIOLATION, encoding="utf-8"
    )

    code = check(project, "--diff", "main")
    reported = findings(capsys.readouterr().out)

    assert code == 1
    assert len(reported) == 1
    assert reported[0].startswith(f"{module}:5:")  # the appended def, not line 1


def test_an_uncommitted_edit_is_invisible_to_diff_committed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    module = project / "src" / "legacy.py"
    module.write_text(
        module.read_text(encoding="utf-8") + APPENDED_VIOLATION, encoding="utf-8"
    )

    code = check(project, "--diff-committed", "main")
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out == ""


def test_a_staged_edit_is_reported_by_the_default_diff(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    module = project / "src" / "legacy.py"
    module.write_text(
        module.read_text(encoding="utf-8") + APPENDED_VIOLATION, encoding="utf-8"
    )
    git(tmp_path, "add", "-A")

    assert check(project, "--diff", "main") == 1
    assert len(findings(capsys.readouterr().out)) == 1


def test_an_untouched_file_keeps_its_violations_to_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    write(project / "src" / "other.py", "def other(value: object) -> None:\n    ...\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "second module")

    # A clean edit to `legacy.py`: the diff touches a line, but no line it touches
    # violates anything, and neither module's existing violations are this change's.
    module = project / "src" / "legacy.py"
    module.write_text(
        module.read_text(encoding="utf-8") + CLEAN_APPENDED, encoding="utf-8"
    )

    code = check(project, "--diff", "main")
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out == ""


def test_an_untracked_file_is_checked_in_full(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    newcomer = write(
        project / "src" / "newcomer.py",
        """
            def first(value: object) -> None:
                ...


            def second(value: object) -> None:
                ...
        """,
    )

    code = check(project, "--diff", "main")
    reported = findings(capsys.readouterr().out)

    assert code == 1
    assert len(reported) == 2
    assert all(line.startswith(str(newcomer)) for line in reported)


def test_an_untracked_file_is_invisible_to_diff_committed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    write(project / "src" / "newcomer.py", "def first(value: object) -> None:\n    ...\n")

    assert check(project, "--diff-committed", "main") == 0
    assert capsys.readouterr().out == ""


def test_an_ignored_untracked_file_stays_ignored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    write(tmp_path / ".gitignore", "src/generated.py\n")
    write(project / "src" / "generated.py", "def gen(value: object) -> None:\n    ...\n")

    assert check(project, "--diff", "main") == 0
    assert capsys.readouterr().out == ""


def test_a_committed_change_is_reported_by_both_modes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    git(tmp_path, "checkout", "-q", "-b", "feature")
    module = project / "src" / "legacy.py"
    module.write_text(
        module.read_text(encoding="utf-8") + APPENDED_VIOLATION, encoding="utf-8"
    )
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "add a violation")

    assert check(project, "--diff", "main") == 1
    assert len(findings(capsys.readouterr().out)) == 1
    assert check(project, "--diff-committed", "main") == 1
    assert len(findings(capsys.readouterr().out)) == 1


# --------------------------------------------------------------------------- #
# where the run starts from, and where the repository starts
# --------------------------------------------------------------------------- #


def test_a_project_nested_below_the_git_root_run_from_a_subdirectory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git root, configuration root and working directory are three different places."""
    project = make_repo(tmp_path, project=tmp_path / "service")
    module = project / "src" / "legacy.py"
    module.write_text(
        module.read_text(encoding="utf-8") + APPENDED_VIOLATION, encoding="utf-8"
    )

    monkeypatch.chdir(project / "src")
    code = main(["--diff", "main"])  # configuration found by upward discovery
    reported = findings(capsys.readouterr().out)

    assert code == 1
    assert len(reported) == 1
    assert reported[0].startswith(f"{module}:5:")


def test_a_changed_file_outside_the_configured_roots_is_not_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    write(tmp_path / "scripts" / "tool.py", "def tool(value: object) -> None:\n    ...\n")

    assert check(project, "--diff", "main") == 0
    assert capsys.readouterr().out == ""


def test_outside_a_git_repository_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path / "pyproject.toml", PROJECT_CONFIG)
    write(tmp_path / "src" / "legacy.py", LEGACY_MODULE)

    code = check(tmp_path, "--diff", "main")
    captured = capsys.readouterr()

    assert code == 2
    assert "not inside a git repository" in captured.err


def test_an_unresolvable_base_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)

    code = check(project, "--diff", "origin/no-such-branch")
    captured = capsys.readouterr()

    assert code == 2
    assert "cannot find a merge base between 'origin/no-such-branch'" in captured.err


def test_diff_and_diff_committed_cannot_be_combined(tmp_path: Path) -> None:
    project = make_repo(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        check(project, "--diff", "main", "--diff-committed", "main")
    assert excinfo.value.code == 2


# --------------------------------------------------------------------------- #
# combining with the other filters
# --------------------------------------------------------------------------- #


def test_explicit_paths_bound_the_walk_and_the_diff_still_filters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    untouched = write(
        project / "src" / "other.py", "def other(value: object) -> None:\n    ...\n"
    )
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-m", "second module")

    module = project / "src" / "legacy.py"
    module.write_text(
        module.read_text(encoding="utf-8") + APPENDED_VIOLATION, encoding="utf-8"
    )
    # `other.py` is committed and untouched; naming it explicitly must not resurrect
    # its violation, and naming only it must leave the changed file out of the walk.
    assert check(project, str(untouched), "--diff", "main") == 0
    assert capsys.readouterr().out == ""

    assert check(project, str(project / "src"), "--diff", "main") == 1
    assert len(findings(capsys.readouterr().out)) == 1


def test_diff_and_baseline_apply_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = make_repo(tmp_path)
    module = project / "src" / "legacy.py"
    module.write_text(
        module.read_text(encoding="utf-8") + APPENDED_VIOLATION, encoding="utf-8"
    )

    # Record everything, including the change that has not been committed yet.
    assert check(project, "--generate-baseline") == 0
    capsys.readouterr()
    baseline = project / DEFAULT_BASELINE_NAME

    assert check(project, "--diff", "main", "--baseline", str(baseline)) == 0
    # The baseline also records `legacy.py`'s original violation, which this
    # diff-narrowed run never looks at -- and which must not therefore be announced as
    # stale on every pull request.
    assert (
        capsys.readouterr().out.strip()
        == "1 baselined findings hidden (0 stale entries)"
    )

    # One more changed line the baseline does not know about is reported again.
    module.write_text(
        module.read_text(encoding="utf-8")
        + "\n\ndef newest(value: object) -> None:\n    ...\n",
        encoding="utf-8",
    )
    assert check(project, "--diff", "main", "--baseline", str(baseline)) == 1
    captured = capsys.readouterr()
    assert len(findings(captured.out)) == 1
    assert "1 baselined findings hidden" in captured.out


# --------------------------------------------------------------------------- #
# the unified-diff parser, on the shapes a fixture cannot conjure on demand
# --------------------------------------------------------------------------- #


def test_parse_patch_reads_hunk_ranges_and_skips_deletions(tmp_path: Path) -> None:
    patch = textwrap.dedent("""
        diff --git a/src/kept.py b/src/kept.py
        --- a/src/kept.py
        +++ b/src/kept.py
        @@ -3,0 +4 @@ def anchor():
        +    one_added_line = 1
        @@ -10,2 +11,3 @@
        +    first = 1
        +    second = 2
        +    third = 3
        @@ -30,2 +32,0 @@
        diff --git a/src/gone.py b/src/gone.py
        --- a/src/gone.py
        +++ /dev/null
        @@ -1,4 +0,0 @@
    """).lstrip("\n")

    parsed = parse_patch(patch, tmp_path)

    assert set(parsed) == {(tmp_path / "src" / "kept.py").resolve()}
    assert parsed[(tmp_path / "src" / "kept.py").resolve()] == frozenset(
        {4, 11, 12, 13}
    )


def test_parse_patch_of_an_empty_diff_is_empty(tmp_path: Path) -> None:
    assert parse_patch("", tmp_path) == {}
