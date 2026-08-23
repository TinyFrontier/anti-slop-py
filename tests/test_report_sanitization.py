"""Diagnostics must not relay multi-line or oversized fragments of scanned code.

Interpolated message fragments originate in untrusted third-party source and are
read by coding agents; sanitization keeps every diagnostic a single bounded line
(indirect-prompt-injection hardening).
"""

from __future__ import annotations

from harness import run_rule

from anti_slop.rules.no_unsafe_dict_values import RULE


def test_long_annotation_is_truncated_with_an_ellipsis() -> None:
    member = " | ".join(f"Member{i}" for i in range(30))
    (diagnostic,) = run_rule(
        RULE, f"def load() -> dict[str, Any | {member}]: ..."
    )
    assert "…" in diagnostic.message
    assert "Member29" not in diagnostic.message
    assert len(diagnostic.message) < 1000


def test_message_stays_on_a_single_line() -> None:
    snippet = (
        "def load() -> dict[\n"
        "    str,\n"
        "    Any,\n"
        "]: ..."
    )
    (diagnostic,) = run_rule(RULE, snippet)
    assert "\n" not in diagnostic.message
