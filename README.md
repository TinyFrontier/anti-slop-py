# anti-slop-py

[![skills.sh](https://skills.sh/b/TinyFrontier/anti-slop-py)](https://skills.sh/TinyFrontier/anti-slop-py)

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

All 15 core rules are implemented, with the full valid/invalid test matrix and
self-lint clean on this repository's own source, plus the first opt-in contrib group
(`fastapi`, 4 rules, off unless configured). Adoption tooling is in place: presets,
a findings baseline, and diff-scoped runs. `no-adhoc-isinstance` is turned off in
this repository's own `pyproject.toml` for the reason recorded there — an AST
analyzer's domain objects are themselves `ast` nodes, so `isinstance` over node
classes here already is the recipe the rule prescribes, not the pattern it bans.

## Install into a repository (agent skill)

anti-slop-py is vendored, not depended upon. `skills/install-anti-slop-py/` is the
procedure for a coding agent: inspect the target repository, copy the linter in, merge
`[tool.anti-slop]`, wire up pre-commit and CI, hand Ruff its duplicate rules
(`ANN401`, `B009`, `B010`, `PGH003`) over to anti-slop, run the linter, report the diff.

Add the skill to your agent with [skills](https://skills.sh):

```bash
npx skills add TinyFrontier/anti-slop-py --skill install-anti-slop-py
```

then ask the agent to install anti-slop in the current repository. Alternatively,
point the agent at `skills/install-anti-slop-py/SKILL.md` directly, or copy the
files by hand:

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
python -m anti_slop --explain no-widen-then-cast
python -m anti_slop --rule no-object-parameters src/
python -m anti_slop --generate-baseline           # accept today's findings
python -m anti_slop --diff origin/main            # only what this change touched
```

Exit codes: `0` clean, `1` violations found, `2` configuration or usage error.

Configure in `pyproject.toml`:

```toml
[tool.anti-slop]
include = ["src", "tests"]
exclude = [".venv/**", "tools/anti_slop/**"]
# preset = "recommended"  # starting levels for the core rules; see "Presets"
# groups = ["fastapi"]    # opt-in rule groups; off unless listed here
# baseline = ".anti-slop-baseline.json"  # findings to hide; see "Baseline"

[tool.anti-slop.rules]
no-object-parameters = { level = "error", allow-object = false }
no-adhoc-isinstance = "warn"   # levels: "error" | "warn" | "off"
```

A `warn`-level rule reports its diagnostics (marked `warning:`) but does not fail
the run: warn-only findings exit 0. Use it to stage contested rules during adoption —
rule by rule here, or a whole tier at a time with a preset (see "Presets" below and
"Opinionated by design"). To keep every rule at `error` while the findings that
already exist stay out of the way, record them in a baseline instead (see
"Baseline").

Suppress deliberately, and always by rule id:

```python
def save(value: object) -> None:  # anti-slop: ignore[no-object-parameters]
    ...
```

`# anti-slop: skip-file` (in the first 5 lines) skips a whole file. A suppression
without a rule id is a configuration error, not a silent blanket opt-out.

### Presets

`preset` sets the starting level of every **core** rule at once, from the rule's own
tier and confidence:

```toml
[tool.anti-slop]
preset = "recommended"
```

| Preset | Escape-hatch rules | Architectural rules | Use it when |
|---|---|---|---|
| `strict` | `error` | `error` | the default posture — identical to naming no preset at all |
| `recommended` | `error` | `warn` | adopting the tool: hatches block, policy reports |
| `minimal` | `error`, high confidence only (`no-unsafe-dict-values` → `off`) | `off` | you want the indisputable subset and nothing else |
| `legacy` | `warn` | `off` | a large existing codebase, before anything is cleaned up |

`[tool.anti-slop.rules]` is applied on top and always wins, per rule — a preset is
where a project starts, not a ceiling on what it can say afterwards. Setting only an
option (`no-object-parameters = { allow-object = true }`) leaves the preset's level
alone; naming a level replaces it. Omitting `preset` is not a preset: every rule
starts at `error`, exactly as before presets existed. Rules of an opt-in group are
never touched by a preset — they arrive with `groups` and are configured by name.

### Explaining a rule

`--explain <rule-id>` prints why a rule exists, in five sections — what it catches,
why it matters, what to write instead, when to turn it off, and its known false
positives:

```bash
python -m anti_slop --explain no-widen-then-cast
python -m anti_slop --explain fastapi/no-state-attribute-access   # group not needed
python -m anti_slop --explain no-widen-then-cast --format json
```

It describes a rule rather than a repository, so it needs no configuration and works
for the rules of an opt-in group without enabling the group. An unknown id exits `2`
and suggests the closest names.

`--list-rules` shows each active rule's tier and confidence next to its summary, and
`--list-rules --format json` emits the same catalogue as a stable machine-readable
document — one object per rule with `id`, `summary`, `tier`, `confidence`,
`default_level` (the level it runs at under the configuration that was loaded), `fix`
(always `"none"`), `tags`, and `options`:

```bash
python -m anti_slop --list-rules --format json
```

One element of that array:

```json
{
  "id": "no-unsafe-dict-values",
  "summary": "Dict-like value types must name a domain type, not `Any`/`object`.",
  "tier": "escape-hatch",
  "confidence": "medium",
  "default_level": "error",
  "fix": "none",
  "tags": ["typing", "dict"],
  "options": []
}
```

`confidence` says how much a finding can be trusted without reading the surrounding
code: `high` — the construct itself is the defect; `medium` — detection depends on
resolution this linter does only partially; `policy` — the construct is legal and
idiomatic for some teams, and the finding states this project's chosen policy. `fix`
is `"none"` on every rule and stays that way: the recipe is spelled out in the
diagnostic, and a linter that rewrites code to satisfy its own rules becomes a
generator of exactly the code it was built to reject.

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

### Opt-in group: `fastapi`

Framework policy stays out of the core. The `fastapi` group is off until a project
asks for it, and it is meant for a repository with a **direct** FastAPI dependency:

```toml
[tool.anti-slop]
groups = ["fastapi"]
```

Its rules carry the group prefix everywhere — in `[tool.anti-slop.rules]`, in the
diagnostics, and in `# anti-slop: ignore[fastapi/...]` suppressions:

- `fastapi/no-dict-body-parameters` — rejects a route body annotated `dict`/`Dict`/
  `Mapping`/`Any`; parameters marked `Depends`/`Query`/`Header`/`Path`/`Cookie` are
  not the body and are left alone.
- `fastapi/no-untyped-route-response` — rejects a route handler with no return
  annotation, or one returning `dict`/`Any`; `-> None` and `Response` types are valid.
- `fastapi/no-raw-request-parsing` — rejects `request.json()`/`form()`/`body()` inside
  a handler whose `request` parameter is annotated `Request`.
- `fastapi/no-state-attribute-access` — rejects `app.state.x` / `request.app.state.x`
  (and the `getattr`/`setattr`/`delattr` forms), anywhere in the module.

Enabling the group also retargets one core rule: `no-module-mocking` keeps its problem
statement but swaps its recipe for `app.dependency_overrides`, the seam FastAPI already
provides. A function counts as a route handler only when its decorator base resolves,
within the same module, to a `FastAPI(...)`/`APIRouter(...)` construction — a router
imported from elsewhere, or a `.get` decorator belonging to another framework, is left
alone rather than guessed at.

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

The four snippets below need `groups = ["fastapi"]` in `[tool.anti-slop]`; each was
run through the linter with the full registry enabled and reports exactly its own
rule.

### `fastapi/no-dict-body-parameters`

```python
from fastapi import FastAPI

app = FastAPI()

@app.post("/users")
async def create_user(payload: dict) -> UserOut:
    return store(payload)
```

Declare the body as a pydantic model instead — validation, the 422 response and the
OpenAPI schema all follow from the annotation:

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class UserIn(BaseModel):
    email: str

@app.post("/users")
async def create_user(payload: UserIn) -> UserOut:
    return store(payload)
```

### `fastapi/no-untyped-route-response`

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/users/{user_id}")
async def read_user(user_id: int) -> dict[str, str]:
    return load_user(user_id)
```

### `fastapi/no-raw-request-parsing`

```python
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/users")
async def create_user(request: Request) -> UserOut:
    payload = await request.json()
    return UserOut(**payload)
```

### `fastapi/no-state-attribute-access`

```python
from fastapi import FastAPI

app = FastAPI()

@app.on_event("startup")
async def start() -> None:
    app.state.engine = create_engine(DSN)
```

Hand the engine out through a dependency instead of a shared bag:

```python
from fastapi import Depends, FastAPI

app = FastAPI()

def get_engine() -> Engine:
    return ENGINE

@app.get("/users/{user_id}")
async def read_user(user_id: int, engine: Engine = Depends(get_engine)) -> UserOut:
    return await load_user(engine, user_id)
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

## Opinionated by design

**anti-slop is intentionally opinionated. Some rules encode architectural
preferences rather than universal Python correctness.** The tool is executable
architectural policy for the places ordinary linters and type checkers stay silent —
not a claim that every flagged construct is objectively wrong. Defaults preserve
that policy; the project is built so a team changes them without forking: the copy
is vendored, every rule takes a per-rule level, and every finding is suppressible
by rule id.

The fifteen core rules split into two tiers. The split is not just prose here: every
rule declares its `tier` and `confidence` as machine-readable metadata, which is what
`--list-rules`, `--explain` and the presets all read.

**Escape-hatch rules** ban constructs whose main effect is to discard evidence the
type checker already had. These are near-universal — enable them first, everywhere:
`no-any-parameters`, `no-any-returns`, `no-any-type-aliases`, `no-chained-casts`,
`no-conditional-empty-dict-spread`, `no-dynamic-dispatch`,
`no-known-value-widening`, `no-unsafe-dict-values`, `no-widen-then-cast`,
`require-safety-comment`.

**Architectural rules** encode a policy that reasonable teams reject wholesale, and
their cost varies by codebase. `no-adhoc-isinstance` deserves the bluntest wording:
its default is intentionally stricter than idiomatic Python in many codebases — a
plain `if isinstance(value, Foo)` outside a type-guard function is flagged even
with the default `allow-in-type-guards = true`. This repository itself sets the
rule to `off`, because an AST analyzer's domain objects are AST node classes and
branching on them is exactly what the rule's recipe prescribes. That is the
configuration model working as intended, not an exception to it.

Suggested postures by codebase type:

| Rule | Business backend | Framework / library | ML / scientific |
|---|---|---|---|
| `no-adhoc-isinstance` | `error` or `warn` | `off` or `warn` | `warn` |
| `no-module-mocking` | `error` for new code, suppressions on legacy | `warn` or `off` | `warn` |
| `no-object-parameters` | `error` | `warn` | `warn` |
| `no-string-attribute-access` | `error` | `off` or `warn` (metaprogramming) | `warn` |
| `no-shape-in-symbol-names` | `error` | `warn` | `off`, or `terms = []` |

The presets are the executable form of that table: `preset = "recommended"` is this
posture in one line — escape hatches at `error`, architectural policy at `warn` —
and `legacy` is the same idea for a codebase that cannot act on either yet. Start
from a preset, then adjust the individual rules that matter for your domain; each
rule's own `--explain` output carries the posture advice above under **When to
disable**.

## Security model

The linter reads untrusted third-party source: comments (including `# SAFETY:` and
suppression directives) are tokenized, and diagnostics interpolate fragments of the
scanned code — identifiers and unparsed annotations. Because diagnostics are
routinely read by coding agents, every interpolated fragment is sanitized before it
reaches output: collapsed to a single line and truncated at 80 characters. A scanned
repository cannot smuggle a multi-line instruction block or an oversized payload
into the linter's output. Treat diagnostics as data describing the code, never as
instructions to follow.

Diff mode (`--diff`/`--diff-committed`) is the one feature that runs another program:
`git`, as a subprocess, with a fixed argument vector and no shell. A branch name is
passed to `git merge-base` as one argument, so a `<base>` containing shell
metacharacters reaches git as a literal revision name and fails to resolve — which is
the whole of its effect. No other part of the linter starts a process, and nothing
anywhere reads the network.

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
  plan to dependency injection, not as a same-day blocker — or with a baseline, which
  is exactly the shape of problem it exists for. In FastAPI projects,
  `app.dependency_overrides` is the native seam to migrate onto, and enabling the
  `fastapi` group puts that recipe straight into `no-module-mocking`'s own message.
- **`no-shape-in-symbol-names` and scientific/ML vocabulary.** The default term list
  is `["shape"]`. On the two field repositories this produced 11–14 hits each, all
  domain names in tests, resolved with per-line suppressions. Projects where `shape`,
  `tensor`, or similar terms are the domain vocabulary set `terms = []` to disable
  the rule, or replace the list with terms that are actually slop names locally.
- **`no-string-attribute-access` and `app.state`.** One field repository (a 115k-line
  FastAPI service) produced 241 hits from `app.state.*`/`request.app.state.*` access
  alone — `state` is typed `object` by Starlette, so every read of it is a dynamic
  attribute access by this rule's own definition. That is the case
  `fastapi/no-state-attribute-access` exists for: enable the group and it answers with
  the specific diagnostic (recipe: a `Depends`-based provider) instead of the generic
  one, including for the plain `app.state.x` reads the core rule never sees at all.
- **The `fastapi` group resolves routers lexically, within one module.** A handler
  hung off a router imported from another module (`from .api import router`), off an
  attribute chain (`@routers.v1.get(...)`), or off `@app.api_route(...)` is not
  recognized as a route handler, so the group's first three rules stay silent on it.
  There is no cross-module inference anywhere in this linter, and a group that guessed
  would be wrong on every non-FastAPI object with a `.get` method.

## Baseline

A baseline records the findings a project has decided not to fix yet, so that a run
fails only on what is *new*. It is the answer for a codebase adopting the tool after
the fact, where turning a rule on would otherwise mean five thousand diagnostics on
day one:

```bash
python -m anti_slop --generate-baseline                  # write .anti-slop-baseline.json
python -m anti_slop --baseline .anti-slop-baseline.json  # hide what it records
```

`--generate-baseline` writes every current finding and exits `0` without reporting
any of them. Running with `--baseline` then hides exactly those, so the run is
genuinely clean: hidden findings do not reach the exit code and do not appear in any
output format. Anything the file does not cover is reported normally.

Point a project at its baseline once and every run honours it, CI and laptop alike:

```toml
[tool.anti-slop]
baseline = ".anti-slop-baseline.json"
```

`--baseline` overrides the configured path (and is where `--generate-baseline`
writes, too). A configured path is relative to the directory holding
`pyproject.toml`; a path on the command line is relative to the working directory,
like `--config`. **A baseline is applied only when it is asked for** — a file that
merely happens to sit at the default name is never picked up on its own.

The file is a sorted, indented JSON document, made to be committed and reviewed:

```json
{
  "entries": {
    "1e3a6c4f1c8b0d92": 1,
    "5b8d2f0a9c7e4413": 3,
    "9f10c2be77a4d05e": 1
  },
  "version": 1
}
```

Each key is a **finding fingerprint**:
`sha256(rule id + path relative to the config root + the normalized text of the
violating line)`, truncated to 16 hex digits. The value is how many findings carried
that fingerprint. Counting rather than listing is what keeps the file stable: with
three identical violations recorded as `3`, adding a fourth reports exactly one new
finding instead of renumbering the other three.

What that identity does and does not survive — the honest limits:

| Change | Baseline entry |
|---|---|
| Lines inserted or removed above the finding | **holds** — no line number in the fingerprint |
| The whole block re-indented | **holds** — the line is normalized before hashing |
| Whitespace inside the line changed (`value:  object`) | **holds** — internal runs collapse |
| The violation moved into another function or class | **holds** — no enclosing scope in the fingerprint |
| An identical violation added elsewhere in the same file | reported, once |
| The violating line's text edited (a rename, a reformat across two lines) | **invalidated** — reported as new |
| The file renamed or moved | **invalidated** — reported as new |

The last two are deliberate. A fingerprint loose enough to survive them would be
loose enough to hide a genuinely new violation behind an old record, and hiding a
real finding is the one failure mode a baseline must not have. Regenerate the file
after a rename or a large reformat.

An entry whose count exceeds what the run actually found is **stale** — the code it
recorded was fixed, moved, or reformatted. Stale entries are never an error; a build
must not start failing because somebody cleaned something up. (A diff-scoped run
reports no stale count at all: it only looked at part of the project, so every entry
for a file it skipped would be "stale" on every run.) `--format text` ends a
baselined run with one summary line:

```
7 baselined findings hidden (2 stale entries)
```

It is printed only when there is something to say, and never in `json`, `sarif` or
`github` output, whose documents stay documents. Warnings are baselined exactly like
errors — one mechanism, no second policy. In SARIF, each result carries the same
fingerprint under `partialFingerprints`, so a code-scanning alert and the baseline
entry that silences it name the same finding.

## Checking a diff

`--diff <base>` reports only findings on the lines this change touched — the mode to
reach for after editing a file, and the one to run in a pull request:

```bash
python -m anti_slop --diff origin/main            # working tree vs the merge base
python -m anti_slop --diff-committed origin/main  # committed history only
```

`--diff` compares the **working tree** against `git merge-base <base> HEAD`: staged
edits, unstaged edits, and untracked files (which count in full, having nothing to be
compared against). That default is deliberate — an agent or a developer runs the
check right after editing, before committing anything, and a mode that only looked at
`HEAD` would answer "clean" about code sitting unsaved in the editor.
`--diff-committed` is the strict variant for CI: `<merge base>..HEAD`, ignoring the
working tree entirely.

A diagnostic survives the filter when its **anchor line** — the `line` every format
reports as its location — is one of the lines the change added or rewrote. A
multi-line construct whose anchor line was not touched is not this change's finding.
Existing violations in files nobody touched stay silent, which is the point.

With no paths given, diff mode walks the changed files themselves rather than the
whole `include` tree — faster, and closer to the question being asked. Paths given on
the command line still bound the walk, with the diff filtering on top.
`--diff` combines with `--baseline`: the diff narrows first, the baseline hides
second, and the summary omits the stale count because a partial run cannot know it.

Outside a git repository, or with a `<base>` git cannot resolve, the run exits `2`
with the reason rather than reporting a misleading clean tree. Git queries run from
the configuration root, so a project nested inside a larger repository and a run
started from a subdirectory both resolve the repository the checked files belong to.

## Output formats

`--format text` (the default) prints `path:line:col rule-id message`, one
diagnostic per line. Three machine-readable formats are also available, for CI:

```bash
python -m anti_slop --format json src/      # a single JSON array on stdout
python -m anti_slop --format github src/    # GitHub Actions ::error annotations
python -m anti_slop --format sarif src/     # a single SARIF 2.1.0 document
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

`--format sarif` prints one SARIF 2.1.0 document (schema:
`docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json`),
ready for tools that consume the OASIS format — GitHub code scanning among them.
`tool.driver.rules` lists only the rules that produced a result, each carrying its
summary, problem/recipe prose and `{tier, confidence, tags}` under `properties`; a
result's `locations[].physicalLocation.artifactLocation.uri` is POSIX and relative
to the configuration root, resolved against `originalUriBaseIds.SRCROOT`. Every
result also carries `partialFingerprints: {"antiSlop/v1": "<fingerprint>"}` — the
same finding identity a baseline entry is keyed by (see "Baseline"), so a viewer that
tracks alerts across commits follows a finding through the line churn above it.
Severity maps the same way as the other formats — `error` stays `"error"`, `warn`
becomes `"warning"` — and, like `--format json`, an empty run still prints a
complete, valid document rather than nothing:

```json
{
  "version": "2.1.0",
  "runs": [{
    "tool": {"driver": {"name": "anti-slop",
      "rules": [{"id": "no-object-parameters", "helpUri": "...#rules"}]}},
    "originalUriBaseIds": {"SRCROOT": {"uri": "file:///abs/project/"}},
    "results": [{"ruleId": "no-object-parameters", "ruleIndex": 0, "level": "error",
      "locations": [{"physicalLocation": {
        "artifactLocation": {"uri": "mod.py", "uriBaseId": "SRCROOT"}}}],
      "partialFingerprints": {"antiSlop/v1": "1e3a6c4f1c8b0d92"}}]
  }]
}
```

`--format sarif` describes a *run*, not a rule, so `--list-rules` and `--explain`
still accept only `text`/`json` and reject it with exit code `2`, same as `github`.

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

## Canary benchmark

`scripts/canary_bench.py` runs the `recommended` preset against five pinned public
repositories (a FastAPI app, a Django project, a library, a CLI tool, a
scientific/ML library) and publishes a reproducible findings table to
[`bench/RESULTS.md`](bench/RESULTS.md) — LOC, runtime, findings by rule, and a
`--generate-baseline` + `--baseline` round trip that must exit 0 on every
repository. It is a canary, not a labelled benchmark: nobody has reviewed the
findings for true/false positives, so read the counts as "a policy rule fired
this many times", never as "this many bugs" — see the table's own header for the
full disclaimer. Regenerate with `.venv/bin/python scripts/canary_bench.py`.

## Development

```bash
uv venv --python 3.12
uv pip install -e . --group dev
.venv/bin/pytest
.venv/bin/ty check src/ tests/
```
