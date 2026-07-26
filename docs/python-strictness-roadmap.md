# Python strictness roadmap

Task list for the follow-up run that removes the **temporary** lint
baseline introduced on 2026-07-25. Violation counts below are the real
numbers measured with `ruff check --isolated --select ALL --statistics
scripts/` (ruff 0.16.0) after the initial cleanup (2,911 findings total,
of which 204 are covered by permanent, individually-justified ignores).

## Goal state

- `[tool.ruff.lint] select = ["ALL"]` with **only** permanent,
  individually-justified ignores (formatter conflicts `COM812`/`ISC001`,
  docstring-convention picks `D203`/`D213`, `S311`, `PT009`/`PT027`).
- `mypy --strict` clean over all of `scripts/` — then flip the
  `mypy-strict` input of the `python` job in `.github/workflows/lint.yml`
  to `"true"` and drop the strictness settings from `pyproject.toml`.
- Bandit clean without `skips` (or with per-line `# nosec` plus a
  justification comment for genuine false positives).
- No per-file ignores: this run must not introduce
  `[tool.ruff.lint.per-file-ignores]` sections; targeted per-line
  `# noqa`/`# nosec` with a justification comment are the only allowed
  local suppressions.

Work through the sections in order; after each one, remove the
corresponding `TEMPORARY (roadmap #N)` entries from `pyproject.toml` and
confirm CI is green. That removal **is** the acceptance criterion for
every section below.

## 1. Type annotations (`ANN*`) — 1,566 findings

`ANN001` 984, `ANN202` 275, `ANN201` 274, `ANN204` 29, `ANN003` 3,
`ANN205` 1.

- Annotate module by module, starting with the `mtgcards` leaf modules
  (`mana`, `deck`, `cards`, `stats`, `database`, `behaviors`,
  `ttl_loader`), then `mtgviz`, then `mtgrules` (bottom-up: `events`,
  `objects`, `manasys`, `abilities`, … `game` last), then the top-level
  scripts.
- Coordinate with the mypy strict migration (section below): annotating a
  package and flipping its strict override should land together.
- Replace the `Any` placeholders introduced by the initial cleanup
  (`StackItem`/`PendingTrigger` fields, `Player.mana_pool`,
  `TestIntegration.db`) with real protocol/union types.

## 2. Docstrings (`D1*`, `D205`) — 401 findings

`D102` 220, `D101` 58, `D103` 48, `D205` 48, `D107` 17, `D105` 10.

- One documentation pass per package; the rules-engine modules already
  cite CR rule numbers — keep that convention in the new docstrings.
- `D205` is mostly mechanical (insert a blank line after the summary
  line or condense the summary to one line).

## 3. Unused arguments (`ARG*`) — 271 findings

`ARG001` 168, `ARG005` 72, `ARG002` 31.

- The bulk are callback signatures (`applies(g, obj, ch)`,
  `matches(g, event)`, policy hooks). Rename genuinely-unused parameters
  to `_`/`_name` where the signature is fixed by the callback protocol;
  define shared `Protocol` types while annotating (section 1) so the
  signatures are enforced rather than convention.

## 4. Import hygiene (`TID252`, `PLC0415`, `E402`) — 150 findings

`TID252` 76 (relative imports), `PLC0415` 69 (function-level imports),
`E402` 5 (imports after `sys.path` manipulation).

- Convert `from .x import y` to absolute `from mtgrules.x import y`.
- Hoist function-level imports to module top; where they exist to break
  real import cycles (e.g. `mtgrules.compiler` ↔ `mtgrules.effects`),
  restructure the modules instead of keeping the deferred import.
- Give the tooling a proper packaging story (e.g. `[tool.setuptools]`/
  editable install of `scripts/` packages) so the entry scripts stop
  mutating `sys.path`, which removes the `E402` sites.

## 5. `print` calls (`T201`) — 63 findings

- The validators (`validate_ttl.py`, `validate_sparql.py`,
  `check_consistency.py`) and generators report via `print`. Route
  diagnostics through `logging` and keep `print` only for the actual
  program output of CLI entry points (then suppress per line or restrict
  output to a single reporting function).

## 6. Copyright notices (`CPY001`) — 51 findings

- Decide on the house header (SPDX one-liner recommended) and add it to
  every file under `scripts/`; alternatively adopt the rule as a
  permanent ignore with a written justification if the house style
  rejects per-file notices.

## 7. Complexity (`C901`, `PLR09xx`) — 96 findings

`C901` 32, `PLR0912` 27, `PLR0913` 11, `PLR0915` 11, `PLR0917` 10,
`PLR0911` 5.

Worst offenders by measured complexity (C901):

| Function | Where | Complexity |
| --- | --- | --- |
| `parse_effect_clause` | `mtgrules/compiler.py` | 44 |
| `main` | `check_consistency.py` / `mtgrules/cli.py` | 43 / 24 |
| `derive_from_oracle` | `mtgcards/cards.py` | 33 |
| `card_block` | `generate_individuals.py` | 28 |
| `run_replay` | `mtgviz/replay.py` | 24 |
| `check_state_based_actions` | `mtgrules/game.py` | 24 |
| `activate_ability` | `mtgrules/game.py` | 24 |

- Split the parser/derivation functions into per-pattern handler tables
  (dict of regex → handler) instead of long `if` chains; split `main`
  functions into one function per check. `mtgviz/tui.py` rendering
  functions (`PLR0915` 66/53 statements) split naturally per panel.
- For `PLR0913`/`PLR0917`, introduce parameter dataclasses or
  keyword-only arguments.

## 8. Magic values (`PLR2004`) — 28 findings

- Name the constants (life totals, deck sizes, phase counts, CR-derived
  numbers). Most belong next to the CR rule annotations they implement.

## 9. Private-member access (`SLF001`) — 17 findings

- Cross-module `_underscore` access (mostly the layer system and tests).
  Promote genuinely-shared members to public API or add accessor
  methods.

## 10. Manual list comprehensions (`PERF401`) — 16 findings

- Rewrite as comprehensions/`extend` where it does not hurt readability;
  suppress per line with justification where the loop form is clearer.

## 11. Boolean positional arguments (`FBT*`) — 14 findings

`FBT002` 12, `FBT001` 2.

- Make boolean flags keyword-only (`*,` marker). Mechanical; touches
  call sites, so run the full gate after each module.

## 12. File handling (`PTH123` 9, `SIM115` 8) — 17 findings

- Migrate `open()` to `Path.open()`/`Path.read_text()`; wrap the
  remaining bare handles (`mtgviz` writers) in context managers or make
  their owners context managers.

## 13. Loop-variable overwrites (`PLW2901`) — 7 findings

- Rename the shadowing assignment inside the loop body (e.g.
  `sentence = sentence.strip()` → new name). Verify each with the tests.

## 14. Security-adjacent (`S108` 4, `S310` 4) — 8 findings

- `generate_individuals.py`: replace the hardcoded `/tmp` cache with
  `platformdirs`/`tempfile` and validate the URL scheme before
  `urlopen`. Then drop **both** the ruff ignores and the matching
  `B108`/`B310` entries from `[tool.bandit] skips`.

## 15. Singletons (`PERF203` 1, `PLW0603` 1) — 2 findings

- `check_consistency.py`: move the `try`/`except` out of the JSON-value
  loop (pre-validate or collect afterwards).
- `mtgrules/cr.py`: replace the `global _cr_singleton` with
  `functools.cache` on `get_cr()`.

## mypy `--strict` migration plan

Ratchet package by package with `[[tool.mypy.overrides]]` and
`strict = true` per completed package (the ratchet is allowed **during**
the run; the end state has no overrides because the global config is
strict):

1. `mtgcards` (leaf, no intra-repo dependencies)
2. `mtgviz`
3. `mtgrules` (bottom-up within the package; `game.py` last)
4. Top-level scripts (`validate_ttl.py`, `validate_sparql.py`,
   `check_consistency.py`, `generate_individuals.py`,
   `simulate_matchup.py`)

Then delete all overrides, set strict globally (or rely on the workflow
input), and flip `mypy-strict: "true"` in `.github/workflows/lint.yml`.

## Knowledge-graph follow-up (from the CI restructuring)

Done (2026-07-25): the `owl:hasKey (:setName)` axiom and the flat
adventure-card modeling of `generate_individuals.py` made the graph
HermiT-inconsistent; both are fixed, `catalog-v001.xml` (regenerable via
`scripts/generate_catalog.py`) resolves the `urn:` imports, and the
`rdf / owl (robot reason)` job now reasons `MagicCardsOntology.ttl` and
the full `MagicCardIndividuals.ttl` closure in CI.

## Final tasks

- Remove `[tool.bandit] skips` entries that section 14 obsoletes; keep
  only `B311` (or replace it with per-line `# nosec` + justification).
- Update `CONTRIBUTING.md` (linting section and the pointer to this
  file).
- Delete this roadmap file.
