"""``FindingFingerprint`` -- the identity of a finding, independent of where it sits.

One finding is "the same finding" as another when three things match: the rule that
reported it, the file it lives in, and the text of the line it is anchored to. The
fingerprint is a short digest of exactly those three, and of nothing else::

    sha256(rule_id + "\\0" + posix path relative to the config root + "\\0" +
           normalized source line)[:16]

The line *number* is deliberately absent: adding an import at the top of a file must
not invalidate every recorded finding below it. So is any enclosing ``class``/``def``
chain -- moving a violation from one function to another does not make it a new
violation, and renaming a function would otherwise invalidate every finding inside
it. So is any ordinal ("the third hit of this rule in this file"): inserting an
identical violation *above* an existing one would renumber every later hit and report
them all as new. Duplicate identical findings are handled by counting them instead --
see :mod:`anti_slop.engine.baseline`.

Normalization of the anchored line is ``strip`` plus collapsing every internal
whitespace run to a single space, so re-indenting a block or padding an operator does
not change identity. Everything else about the line is significant: renaming a symbol
on it, or reformatting it across two lines, produces a different fingerprint and
therefore a new finding. That is the honest trade -- the alternative is a fingerprint
so loose that a genuinely new violation hides behind an old record.

The same value is what SARIF results carry as ``partialFingerprints``
(:data:`FINGERPRINT_KEY`), so a baseline entry and a code-scanning alert name the
same finding.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from anti_slop.engine.rule import Diagnostic

__all__ = [
    "FINGERPRINT_KEY",
    "FINGERPRINT_LENGTH",
    "SourceLines",
    "fingerprint",
    "fingerprint_map",
    "fingerprints_for",
    "normalize_line",
    "relative_key",
]

# Half of a SHA-256 digest in hex. 64 bits of identity is far past the point where a
# collision within one repository is plausible, and short enough that a baseline file
# stays readable in a pull request diff.
FINGERPRINT_LENGTH = 16

# The `partialFingerprints` property name SARIF results carry. The `/v1` suffix is the
# versioning seam SARIF intends it for: if the recipe above ever changes, consumers
# see a new key rather than silently mismatched values under the old one.
FINGERPRINT_KEY = "antiSlop/v1"

_SEPARATOR = "\0"


def normalize_line(text: str) -> str:
    """``text`` stripped, with every internal whitespace run collapsed to one space."""
    return " ".join(text.split())


def fingerprint(*, rule_id: str, relative_path: str, line_text: str) -> str:
    """The fingerprint of one finding, from its three identifying parts."""
    payload = _SEPARATOR.join((rule_id, relative_path, normalize_line(line_text)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def relative_key(path: Path, root: Path) -> str:
    """``path`` as a POSIX path relative to ``root``.

    A file outside ``root`` -- linting another checkout by absolute path -- has no
    relative form, so its absolute POSIX path is used instead. Such a fingerprint is
    tied to that checkout's location on disk, which is the honest answer: a baseline
    is a statement about one repository at one path.
    """
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


class SourceLines:
    """Reads (and remembers) the lines of the files a run reported findings in.

    Only files that produced at least one diagnostic are ever opened, and each is read
    once however many findings it holds. A file that cannot be read any more -- deleted
    between the walk and the report -- yields an empty line rather than raising: a
    fingerprint of an empty line is stable and useless, which is the correct outcome
    for a finding whose source is gone.
    """

    __slots__ = ("_files",)

    def __init__(self) -> None:
        self._files: dict[Path, tuple[str, ...]] = {}

    def line(self, path: Path, number: int) -> str:
        """Line ``number`` (1-based) of ``path``, or ``""`` when it cannot be read."""
        lines = self._files.get(path)
        if lines is None:
            lines = self._read(path)
            self._files[path] = lines
        if 1 <= number <= len(lines):
            return lines[number - 1]
        return ""

    def _read(self, path: Path) -> tuple[str, ...]:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ()
        return tuple(text.splitlines())


def fingerprints_for(
    diagnostics: Sequence[Diagnostic], root: Path, sources: SourceLines | None = None
) -> tuple[str, ...]:
    """One fingerprint per diagnostic, in the order given.

    Positional, not keyed: two identical findings in one file share a fingerprint, and
    the baseline's counting depends on seeing both.
    """
    reader = SourceLines() if sources is None else sources
    return tuple(
        fingerprint(
            rule_id=diagnostic.rule_id,
            relative_path=relative_key(diagnostic.path, root),
            line_text=reader.line(diagnostic.path, diagnostic.line),
        )
        for diagnostic in diagnostics
    )


def fingerprint_map(
    diagnostics: Sequence[Diagnostic], root: Path
) -> dict[Diagnostic, str]:
    """``diagnostic -> fingerprint``, for consumers that look one up by finding."""
    return dict(zip(diagnostics, fingerprints_for(diagnostics, root), strict=True))
