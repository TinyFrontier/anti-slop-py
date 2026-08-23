# anti-slop-py

A standalone, zero-dependency Python linter that rejects **low-evidence, low-signal
patterns** — the ones coding agents leave behind when they reach for an escape hatch
instead of naming a contract: `Any` and `object` parameters, `cast` cascades,
unexplained `# type: ignore`, string-target module mocking, ad-hoc `isinstance`
narrowing. A type checker *permits* all of these by construction, because they are
legal holes in its own type system; anti-slop bans exactly those holes. Every
diagnostic is written to be executed by an agent: it says what to write instead —
parse at the I/O boundary, name the domain type, inject a real seam — not merely
that something is forbidden. It is meant to be **vendored** into a repository, read,
and adjusted to the team's standards, not depended upon.

Port of [dmmulroy/anti-slop](https://github.com/dmmulroy/anti-slop) (an Oxlint plugin
for TypeScript) to Python semantics. See `PLAN.md` for the full specification.

## Status

Phase 0 — engine skeleton, with `no-object-parameters` as the smoke rule.
The remaining 14 core rules land in phases 1–2 (`PLAN.md` section 5).

## Usage

```bash
python -m anti_slop src/            # check paths, or [tool.anti-slop].include
python -m anti_slop --list-rules
python -m anti_slop --rule no-object-parameters src/
```

Exit codes: `0` clean, `1` violations found, `2` configuration or usage error.

Configure in `pyproject.toml`:

```toml
[tool.anti-slop]
include = ["src", "tests"]
exclude = [".venv/**", "tools/anti_slop/**"]

[tool.anti-slop.rules]
no-object-parameters = { level = "error", allow-object = false }
```

Suppress deliberately, and always by rule id:

```python
def save(value: object) -> None:  # anti-slop: ignore[no-object-parameters]
    ...
```

`# anti-slop: skip-file` (in the first 5 lines) skips a whole file. A suppression
without a rule id is a configuration error, not a silent blanket opt-out.

## Development

```bash
uv venv --python 3.12
uv pip install -e . --group dev
.venv/bin/pytest
.venv/bin/ty check src/ tests/
```

A full README with an example violation per rule ships in phase 5.
