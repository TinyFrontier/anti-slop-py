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

## Install into a repository (agent skill)

anti-slop-py is vendored, not depended upon. `skills/install-anti-slop-py/` is the
procedure for a coding agent: inspect the target repository, copy the linter in, merge
`[tool.anti-slop]`, wire up pre-commit and CI, hand Ruff its duplicate rules
(`ANN401`, `B009`, `B010`, `PGH003`) over to anti-slop, run the linter, report the diff.
Point the agent at `skills/install-anti-slop-py/SKILL.md`, or copy the files by hand:

```bash
cd /path/to/target-repo
python /path/to/anti-slop-py/skills/install-anti-slop-py/scripts/install.py
python tools/anti_slop --list-rules
```

`install.py` copies the package to `tools/anti_slop/anti_slop/` and writes a
`tools/anti_slop/__main__.py` launcher. Running the directory puts it on `sys.path`, so
`python tools/anti_slop` lints the repository on its own interpreter (3.12+) — no
install, no virtualenv, no `PYTHONPATH`. The script refuses to overwrite an existing
destination without `--force`.

The skill's assets are a byte-identical copy of `src/anti_slop`:

```bash
python scripts/sync_skill_assets.py           # refresh after changing src/
python scripts/sync_skill_assets.py --check   # CI guard, fails on drift
```

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

## Output formats

`--format text` (the default) prints `path:line:col rule-id message`, one
diagnostic per line. Two machine-readable formats are also available, for CI:

```bash
python -m anti_slop --format json src/      # a single JSON array on stdout
python -m anti_slop --format github src/    # GitHub Actions ::error annotations
```

`--format json` prints one JSON document — an array of objects with the keys
`path`, `line`, `col`, `endLine`, `endCol`, `rule`, `message`, sorted by
`(path, line, col)` — even `[]` on a clean run, so a consumer never has to treat
empty stdout as a special case. Configuration errors still go to stderr with
exit code `2`, in every format.

`--format github` prints one `::error file=...,line=...,col=...,endLine=...,
endColumn=...,title=<rule-id>::<message>` workflow command per diagnostic, ready
to annotate a pull request diff in GitHub Actions; nothing is printed on a clean
run. `%`, `\r`, `\n` (and `:`, `,` in the field values) are escaped per GitHub's
workflow-command rules, so a path or message containing them cannot corrupt the
annotation.

## Parallel file walk

Above 20 collected files, `python -m anti_slop` checks them across a
`concurrent.futures.ProcessPoolExecutor` pool instead of one file at a time; at or
below that threshold, or with `--jobs 1`, it stays on the single-process path.
`--jobs N` picks the worker count explicitly; the default auto-selects from
`os.cpu_count()`, capped at the number of files. Diagnostics and failures come
back sorted identically either way — parallelism changes wall-clock time, not
output.

```bash
python -m anti_slop src/              # auto: sequential ≤ 20 files, parallel above
python -m anti_slop --jobs 1 src/     # force sequential, e.g. for a deterministic trace
python -m anti_slop --jobs 8 src/     # force a specific worker count
```

## Use with pre-commit

`.pre-commit-hooks.yaml` at the repository root declares the `anti-slop` hook.
Add it to the target repository's `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/TinyFrontier/anti-slop-py
    rev: v0.1.0  # pin to a commit or tag
    hooks:
      - id: anti-slop
```

`language: python` makes `pre-commit` install this package into its own isolated
environment and run the `anti-slop` console script (`pyproject.toml`'s
`[project.scripts]`) on the staged Python files `pre-commit` passes it as
arguments; `types: [python]` limits it to `.py`/`.pyi` files, and
`require_serial: false` lets `pre-commit` run it alongside other hooks.
Configuration is still `[tool.anti-slop]` in the target repository's own
`pyproject.toml` — the hook needs no `args:`. `pre-commit try-repo . anti-slop`
run from this repository exercises the hook manifest directly, both on a clean
file (passes) and on a fixture violation (fails with the diagnostic on stdout).

## Development

```bash
uv venv --python 3.12
uv pip install -e . --group dev
.venv/bin/pytest
.venv/bin/ty check src/ tests/
```

A full README with an example violation per rule ships in phase 5.
