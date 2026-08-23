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

Analysis is purely syntactic — stdlib `ast` and `tokenize`, no type checker in the
loop, no external dependency, no installation. Semantics beyond raw syntax come from
a local scope table (lexical name and import resolution, alias chains) built once per
file, the same "AST plus scopes, nothing heavier" discipline the original Oxlint
plugin follows.

Port of [dmmulroy/anti-slop](https://github.com/dmmulroy/anti-slop) (an Oxlint plugin
for TypeScript) to Python semantics.

## Status

All 15 core rules are implemented, with the full valid/invalid
test matrix from section 3.6 and self-lint clean on this repository's own source. The
opt-in `fastapi` contrib group (section 3.5) has not landed; `no-adhoc-isinstance` is
turned off in this repository's own `pyproject.toml` for the reason recorded there —
an AST analyzer's domain objects are themselves `ast` nodes, so `isinstance` over node
classes here already is the recipe the rule prescribes, not the pattern it bans.

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

## Rules

- `no-adhoc-isinstance` — rejects `isinstance`/`issubclass` branching outside a
  function whose return annotation is `TypeGuard[...]`/`TypeIs[...]`. Option
  `allow-in-type-guards` (default `true`); set to `false` for a strict mode that
  flags every call regardless of context.
- `no-any-parameters` — rejects the `Any` escape hatch on function parameters.
- `no-any-returns` — rejects a return contract of `Any`, including the result type
  of `Awaitable[Any]`/`Coroutine[..., Any]`.
- `no-any-type-aliases` — rejects a type alias that resolves to `Any`, following
  alias chains and `Any | None`-shaped unions within the module.
- `no-chained-casts` — rejects a `cast(...)` call whose value is itself a `cast`.
- `no-conditional-empty-dict-spread` — rejects a dict or `dict(...)` keyword spread
  whose source is a ternary with an empty dict on either branch.
- `no-dynamic-dispatch` — rejects `globals()[...]`/`locals()[...]`/`vars(obj)[...]`
  subscription and `operator.attrgetter`/`methodcaller` — dispatch by a runtime name.
- `no-known-value-widening` — rejects a literal dict/list/set/tuple/constant value
  whose annotation widens it to `Any` or `object`.
- `no-module-mocking` — rejects `unittest.mock.patch`/`mock.patch` (including
  `.object`/`.multiple`), the `mocker.patch` fixture, and `monkeypatch.setattr` —
  string-target patching of another module's attribute.
- `no-object-parameters` — rejects the `object` top-type parameter annotation.
  Options: `allow-object` (default `false`), `allow-variadic-object` (default
  `true`, exempts `*args: object`/`**kwargs: object`).
- `no-shape-in-symbol-names` — rejects a banned term in a *declared* name (function,
  class, parameter, assignment target, import alias). Option `terms` (default
  `["shape"]`); an empty list disables the rule.
- `no-string-attribute-access` — rejects `getattr`/`setattr`/`delattr` calls.
- `no-unsafe-dict-values` — rejects a dict-like container (`dict`, `Mapping`,
  `defaultdict`, …) whose value type is `Any`/`object`, or a union containing
  either, resolved through alias names as well as literal subscripts.
- `no-widen-then-cast` — rejects widening a proven binding to `Any`/`object` and
  then `cast`ing it back to a narrow type in the same straight-line scope.
- `require-safety-comment` — requires every `cast` and every checker suppression
  (`# type: ignore`, `# ty: ignore[...]`, `# pyright: ignore[...]`) to carry an
  adjacent `# SAFETY: <invariant>` comment, and requires every suppression to name
  an error code.

## Violation examples

Each snippet below is rejected by the named rule, and only by that rule — verified
by running `python -m anti_slop` on it during development of this README.

### `no-adhoc-isinstance`

```python
def handle(value):
    if isinstance(value, str):
        print(value)
```

Extract reusable narrowing into its own `TypeIs` function instead:

```python
from typing import TypeIs

def is_str(value) -> TypeIs[str]:
    return isinstance(value, str)
```

### `no-any-parameters`

```python
from typing import Any

def handle(payload: Any) -> None: ...
```

### `no-any-returns`

```python
from typing import Any

def load_user() -> Any: ...
```

### `no-any-type-aliases`

```python
from typing import Any

Metadata = Any
```

### `no-chained-casts`

```python
from typing import cast

# SAFETY: value was already validated as User upstream; both casts assert the same fact.
user = cast(User, cast(object, value))
```

### `no-conditional-empty-dict-spread`

```python
def build_options(timeout: int | None) -> dict[str, int]:
    return {**({"timeout": timeout} if timeout is not None else {})}
```

### `no-dynamic-dispatch`

```python
handler = globals()[name]
```

### `no-known-value-widening`

```python
config: object = {"retries": 3}
```

Narrow the annotation to what is actually known instead:

```python
from typing import Final

CONFIG: Final = {"retries": 3}
```

### `no-module-mocking`

```python
from unittest.mock import patch

@patch("app.services.user_store.save")
def test_save(mock_save) -> None: ...
```

### `no-object-parameters`

```python
def save(value: object) -> None: ...
```

### `no-shape-in-symbol-names`

```python
def compute_shape(board: list[int]) -> int:
    return len(board)
```

Scientific/ML codebases where `shape` is domain vocabulary disable the rule outright:

```toml
[tool.anti-slop.rules]
no-shape-in-symbol-names = { level = "error", terms = [] }
```

### `no-string-attribute-access`

```python
value = getattr(obj, "attr")
```

### `no-unsafe-dict-values`

```python
from typing import Any

payload: dict[str, Any] = {}
```

### `no-widen-then-cast`

```python
from typing import Any, cast

def handle(u: User) -> None:
    raw: Any = u
    # SAFETY: raw is u widened one line up; this cast only claims that same fact back.
    user = cast(User, raw)
```

### `require-safety-comment`

```python
from typing import cast

user_id = cast(UserId, value)
```

Add a specific justification immediately before the necessary cast:

```python
from typing import cast

# SAFETY: parse_user_id validated the identifier before casting.
user_id = cast(UserId, value)
```

## Coexistence with ruff and ty

In a repository that also runs ruff and ty, anti-slop-py occupies a separate lane:
**ruff** — style and correctness, **ty** — types, **anti-slop** — evidence policy. A
type checker *permits* `Any`, `cast`, and `# type: ignore` by construction — they are
legal holes in its own type system, so it says nothing about them; anti-slop bans
exactly those holes. The stricter the type checker, the more that control matters:
the moment an agent hits a ty error, `cast`/`ignore` is the first thing it reaches
for, and that is exactly where anti-slop's rules fire.

Overlap with ruff's own rule set is narrow — 12 of the 15 core rules have no ruff
equivalent at all, and ruff has no public API for custom rules:

| Ruff | anti-slop rule | Overlap |
|---|---|---|
| `ANN401` | `no-any-parameters` | Partial: parameters only, no agent-executable message |
| `B009`/`B010` | `no-string-attribute-access` | Only the literal-name `getattr`/`setattr` form; dynamic names are not caught |
| `PGH003` | `require-safety-comment` | Weaker: requires an error code on `# type: ignore`, not a stated invariant |
| `E722`/`S110` | contrib `no-silent-except` (not in core v1) | Overlaps fully — one more reason to keep it out of core |

When the install skill finds ruff in the target repository, it turns off the
duplicate ruff rules (`ANN401`, `B009`, `B010`, `PGH003`) in favor of the anti-slop
equivalents — wider coverage, agent-executable messages — and records that in the
install report.

## Known limitations & adoption notes

- **Detection is syntactic, not resolved.** Rules that look for a builtin
  (`isinstance`, `getattr`, `globals`, `cast`, …) match the bare name; a local
  variable or parameter that shadows one is indistinguishable from the builtin and
  still gets flagged. `Any`/`object` detection matches `typing.Any` and any
  attribute access ending in `.Any` from a non-`typing` module the same way, since
  there is no import resolution to tell them apart — this can only produce a false
  positive, never a false negative on the real `typing.Any`.
- **`no-module-mocking` on an existing test suite.** In both repositories used for
  field validation on two production services, 100% of `no-module-mocking` hits
  were in tests already built around `mock.patch`/`monkeypatch.setattr` — hundreds of
  hits on a codebase adopting anti-slop after the fact. Turn it on with a migration
  plan to dependency injection, not as a same-day blocker. In FastAPI projects,
  `app.dependency_overrides` is the native seam to migrate onto; the `fastapi`
  contrib group is planned to reference that recipe directly
  from `no-module-mocking`'s own message.
- **`no-shape-in-symbol-names` and scientific/ML vocabulary.** The default term list
  is `["shape"]`. On the two field repositories this produced 11–14 hits each, all
  domain names in tests, resolved with per-line suppressions. Projects where `shape`,
  `tensor`, or similar terms are the domain vocabulary set `terms = []` to disable
  the rule, or replace the list with terms that are actually slop names locally.
- **`no-string-attribute-access` and `app.state`.** One field repository (a 115k-line
  FastAPI service) produced 241 hits from `app.state.*`/`request.app.state.*` access
  alone — `state` is typed `object` by Starlette, so every read of it is a dynamic
  attribute access by this rule's own definition. This is the concrete case the
  planned `fastapi` contrib group's `no-state-attribute-access` rule (recipe:
  `Depends`-based providers) is meant to replace with a more specific diagnostic.

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
