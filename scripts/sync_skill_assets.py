#!/usr/bin/env python3
"""Mirror ``src/anti_slop`` into the install skill's assets (PLAN.md section 1.2).

The skill ships a vendored copy of the linter, so the copy has to be the source --
byte for byte, not "close enough". ``--check`` is the CI guard: it compares the two
trees exactly (file set plus contents) and fails with the list of divergences.

    python scripts/sync_skill_assets.py            # refresh the assets
    python scripts/sync_skill_assets.py --check    # verify, exit 1 on divergence

Standard library only: this script runs in the same zero-dependency environment as
the linter it copies.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

__all__ = ["check", "collect_relative_paths", "main", "sync"]

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "anti_slop"
DESTINATION = ROOT / "skills" / "install-anti-slop-py" / "assets" / "anti_slop"

# Caches and tests never belong in a vendored copy. The tests live in ``tests/``
# today; the filter also covers a future move of test files next to their rules.
EXCLUDED_DIRECTORY_NAMES = frozenset({"__pycache__", "tests"})
EXCLUDED_FILE_SUFFIXES = (".pyc", ".pyo")

SYNC_COMMAND = "python scripts/sync_skill_assets.py"

EXIT_OK = 0
EXIT_DIVERGED = 1


def is_excluded_file(name: str) -> bool:
    """Tests, byte-code caches and editor droppings stay out of the vendored copy."""
    if name.startswith(".") or name.endswith(EXCLUDED_FILE_SUFFIXES):
        return True
    return name.startswith("test_") or name.endswith("_test.py")


def walk(directory: Path) -> Iterator[Path]:
    """Yield every file under ``directory`` that belongs in the vendored copy."""
    for entry in sorted(directory.iterdir()):
        if entry.is_dir():
            if entry.name in EXCLUDED_DIRECTORY_NAMES or entry.name.startswith("."):
                continue
            yield from walk(entry)
        elif not is_excluded_file(entry.name):
            yield entry


def collect_relative_paths(root: Path) -> tuple[str, ...]:
    """Sorted POSIX paths of the copied files, relative to ``root``."""
    if not root.is_dir():
        return ()
    return tuple(
        sorted(path.relative_to(root).as_posix() for path in walk(root))
    )


def sync() -> int:
    """Rebuild the asset tree from ``src/anti_slop``."""
    if not SOURCE.is_dir():
        print(f"source tree is missing: {SOURCE}", file=sys.stderr)
        return EXIT_DIVERGED

    shutil.rmtree(DESTINATION, ignore_errors=True)
    relative_paths = collect_relative_paths(SOURCE)
    for relative in relative_paths:
        target = DESTINATION / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / relative, target)

    location = DESTINATION.relative_to(ROOT).as_posix()
    print(f"Synced {len(relative_paths)} files into {location}.")
    return EXIT_OK


def check() -> int:
    """Verify the asset tree matches ``src/anti_slop`` exactly."""
    expected = collect_relative_paths(SOURCE)
    actual = collect_relative_paths(DESTINATION)
    divergences: list[str] = []

    divergences.extend(
        f"missing from the skill assets: {relative}"
        for relative in expected
        if relative not in set(actual)
    )
    divergences.extend(
        f"not present in src/anti_slop: {relative}"
        for relative in actual
        if relative not in set(expected)
    )
    divergences.extend(
        f"content differs: {relative}"
        for relative in expected
        if relative in set(actual)
        and (SOURCE / relative).read_bytes() != (DESTINATION / relative).read_bytes()
    )

    if divergences:
        for divergence in divergences:
            print(divergence, file=sys.stderr)
        print(f"Skill assets are stale; run `{SYNC_COMMAND}`.", file=sys.stderr)
        return EXIT_DIVERGED

    print(f"Skill assets match src/anti_slop ({len(expected)} files).")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sync_skill_assets",
        description=(
            "Copy src/anti_slop into the install skill's assets, or verify the copy."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare instead of copying; exit 1 when the assets are stale",
    )
    arguments = parser.parse_args(argv)
    return check() if arguments.check else sync()


if __name__ == "__main__":
    raise SystemExit(main())
