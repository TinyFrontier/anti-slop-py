#!/usr/bin/env python3
"""Vendor the anti-slop-py linter into the repository this script is run from.

    python <skill-directory>/scripts/install.py [destination] [--force]

The destination (default ``tools/anti_slop``) receives the ``anti_slop`` package
plus a small ``__main__.py`` launcher, so the copy runs straight from the
checkout::

    python tools/anti_slop src/

Running a directory puts that directory on ``sys.path``, which is what lets the
nested package import as the top-level ``anti_slop`` module -- no installation, no
``PYTHONPATH``, no dependencies. Standard library only.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

__all__ = ["install", "main"]

SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS = SKILL_ROOT / "assets" / "anti_slop"

DEFAULT_DESTINATION = "tools/anti_slop"
PACKAGE_NAME = "anti_slop"

EXIT_OK = 0
EXIT_ERROR = 1

MINIMUM_PYTHON = (3, 12)

LAUNCHER_NAME = "__main__.py"
LAUNCHER = '''"""Run the vendored anti-slop linter: ``python {destination}``.

Executing this directory places it on ``sys.path``, so the sibling ``{package}``
package imports as a top-level module without being installed. Written by the
install-anti-slop-py skill; the package next to it is an unmodified copy.
"""

import sys

if sys.version_info < {minimum!r}:
    running = ".".join(str(part) for part in sys.version_info[:3])
    raise SystemExit(
        "anti-slop needs Python {minimum_text}+, but this is " + running + " ("
        + sys.executable + "). Point the runner at a newer interpreter."
    )

from {package}.__main__ import main  # noqa: E402

raise SystemExit(main())
'''


def install(destination: Path, *, force: bool) -> int:
    """Copy the package into ``destination`` and write the launcher next to it."""
    if not ASSETS.is_dir():
        print(f"skill assets are missing: {ASSETS}", file=sys.stderr)
        return EXIT_ERROR

    relative = _display_path(destination)
    if destination.exists() and not force:
        print(
            f"Refusing to overwrite {relative}."
            " Re-run with --force only after reviewing the existing files.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    package_root = destination / PACKAGE_NAME
    shutil.rmtree(package_root, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ASSETS, package_root)

    launcher = destination / LAUNCHER_NAME
    launcher.write_text(
        LAUNCHER.format(
            destination=relative,
            package=PACKAGE_NAME,
            minimum=MINIMUM_PYTHON,
            minimum_text=".".join(str(part) for part in MINIMUM_PYTHON),
        ),
        encoding="utf-8",
    )

    file_count = sum(1 for path in package_root.rglob("*") if path.is_file())
    print(f"Copied {file_count} files to {relative}/{PACKAGE_NAME}")
    print(f"Wrote the launcher {relative}/{LAUNCHER_NAME}")
    print()
    print("Wire it up:")
    print(f"  run:      python {relative} src/")
    print(f"  rules:    python {relative} --list-rules")
    print(f'  exclude:  add "{relative}/**" to [tool.anti-slop].exclude')
    print('  enable:   set every rule to "error" under [tool.anti-slop.rules]')
    return EXIT_OK


def _display_path(destination: Path) -> str:
    """The destination as written on the command line, when it is inside the repo."""
    working_directory = Path.cwd().resolve()
    resolved = destination.resolve()
    if resolved.is_relative_to(working_directory):
        return resolved.relative_to(working_directory).as_posix()
    return resolved.as_posix()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in arguments
    unknown = [
        argument
        for argument in arguments
        if argument.startswith("--") and argument != "--force"
    ]
    if unknown:
        print(f"unknown option(s): {', '.join(unknown)}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        return EXIT_ERROR

    positional = [argument for argument in arguments if not argument.startswith("--")]
    if len(positional) > 1:
        print("pass at most one destination path", file=sys.stderr)
        return EXIT_ERROR

    requested = positional[0] if positional else DEFAULT_DESTINATION
    destination = Path.cwd() / requested
    return install(destination, force=force)


if __name__ == "__main__":
    raise SystemExit(main())
