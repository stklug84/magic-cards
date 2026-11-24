# magic-cards — repo guide

An OWL/RDF knowledge graph of a personal Magic: The Gathering collection:
a hand-authored schema, per-set card individuals generated from a private
Moxfield export, card-to-card synergies, a catalog of reusable SPARQL
queries, and a CR-grounded Commander simulator that reads the graph (the
Rust workspace under `rust/`; the Python it replaced is git history).

Read this before touching anything. The rules below are the ones that are
expensive to rediscover.

## The two kinds of file

**Generated — never hand-edit.** The next generator run silently reverts
any manual change.

| Path | Produced by |
|---|---|
| `sets/*.ttl` | `scripts/generate_individuals.py generate` |
| `MagicCardCollection.ttl`, `MagicArenaCollection.ttl` | `scripts/generate_individuals.py collection` |
| `MagicCardIndividuals.ttl` (import block only) | `scripts/update_imports.py` |
| `catalog-v001.xml` | `scripts/generate_catalog.py` |

**Hand-authored — edit directly.**

| Path | Contents |
|---|---|
| `MagicCardsOntology.ttl` | The schema (TBox): classes, properties, constraints, vocabulary individuals |
| `MagicCardSynergies.ttl` | Card-to-card synergy assertions |
| `MagicSimulationAnnotations.ttl` | Behavior hooks and AI threat weights (house opinion, not card facts) |
| `queries/**/*.rq` | The SPARQL catalog |
| `arena-picks.json` | MTG Arena printings picked by hand where no lookup decides (read by `scripts/arena_collection.py`) |

To change a generated file, change the generator and regenerate.

## Licensing rules that must not be relaxed

- **Never commit `collection.csv` or `arena-collection.json`.** They are the
  private inventories and the generator's inputs: the paper collection's
  Moxfield export, and the MTG Arena collection that
  `scripts/arena_collection.py pull` reads from Untapped.gg. `.gitignore`'s
  `*.csv` rule and its `/arena-collection.json` line guard them; the `*.csv`
  and `*.txt` rules also keep the tool's `arena-collection.moxfield.csv` and
  `arena-collection.txt` out. Only `arena-picks.json` is tracked.
- **Never commit `MagicCompRules-*.txt`.** Wizards' Comprehensive Rules are
  not redistributable. `.gitignore`'s `*.txt` rule is load-bearing — it is
  also why the licence file is the extensionless `LICENSE`, never
  `LICENSE.txt`. Do not narrow either rule without replacing the guard.
- Hand-authored modelling files carry `SPDX-License-Identifier:
  CC-BY-SA-4.0`; every TTL carries the Wizards Fan Content Policy
  disclaimer banner. Keep both when adding a file.
- The graph models **no valuation data**. Prices stay in the local CSV.

## The pipeline

Order matters. `build_vocab.py` reads the ontology, so schema changes come
first; `collection` reads `sets/`, so it comes after `generate`.

```sh
uv run scripts/arena_collection.py pull             # Untapped.gg -> arena-collection.json (Chrome; when Arena changed)
python3 scripts/build_vocab.py                      # ontology -> vocabulary extract
python3 scripts/generate_individuals.py fetch       # Scryfall -> cache (network)
python3 scripts/generate_individuals.py generate    # -> sets/*.ttl, imports.json
python3 scripts/generate_individuals.py collection  # -> both collection graphs (no network)
python3 scripts/update_imports.py                   # -> MagicCardIndividuals.ttl imports
```

Every step reads both inventories, and the generator stops when one is
missing: `generate` rewrites every set file, so a run without the Arena
snapshot would drop the individuals only the MTG Arena collection holds.
`collection` resolves individuals from the existing `sets/*.ttl`, so it can
run standalone after an inventory-only change. There is no CI
regeneration: the inventories never leave the machine, and `pull` needs a
signed-in Chrome.

## Validation

Needs `rdflib` (`pip install "rdflib>=7,<8"`, or the `validate` extra).

```sh
python3 scripts/validate_ttl.py         # syntax, prefixes, one owl:Ontology, imports resolve
python3 scripts/validate_sparql.py      # query syntax, mc: prefix, every mc: term exists
python3 scripts/check_consistency.py    # cross-file graph invariants
cd scripts && python3 -m unittest discover -s mtgcards/tests -t .   # generator + bundle builder
cd scripts && python3 -m unittest discover -s mtgarena/tests -t .   # MTG Arena collection tool (offline)
```

The simulator's gates run from `rust/` (toolchain pinned by
`rust-toolchain.toml`; `cargo install cargo-deny cargo-llvm-cov --locked`
and `rustup component add llvm-tools-preview` once):

```sh
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features     # needs MagicCompRules-*.txt at the repo root
cargo deny check
cargo llvm-cov --workspace --all-features --summary-only --fail-under-lines 90
```

`validate_sparql.py` is why a term cannot be deleted from the ontology
while a query still references it: removing `:purchasePrice` required
deleting the query that used it.

## Turtle house style

- **One predicate, one object list.** Assert a multi-valued predicate once
  and separate objects with `,`; never repeat the predicate. Objects align
  under the first:

  ```turtle
  :hasSubType :Human ,
              :Artificer ;
  ```

- **`MagicCardsOntology.ttl` layout**: `Ontology Definition` →
  `Class Definitions` → `Property Definitions` (object properties, then
  datatype properties, each grouped by topic) → `Constraints` →
  `Individuals`. Full-width 80-column `#` banners between sections,
  `# --- Topic ---` sub-headers within them. File a declaration by its
  kind: no classes among the properties, no card-fact properties under
  `Simulation Behavior Annotations`.
- **Reformatting must be provably semantics-preserving.** Verify with
  `rdflib.compare.graph_diff` over `to_isomorphic` graphs, not by reading
  the diff.
- **Collection entries are variants, not lots.** One `CollectionEntry` per
  distinct (printing, finish, condition); its quantity is the total held.
  IRIs encode the attributes (`:SolRing…EntryFoilNearMint`), so a new
  finish or condition never renames an existing entry.

## The trap: text-based readers

The simulator's readers — `rust/crates/mtgcards/src/ttl_loader.rs`,
`deck.rs` and `behavior.rs` — parse the generated TTL with **regexes, not
an RDF library**. A layout change in the generator can therefore break
card loading while every RDF validator still passes — cards silently lose
subtypes, colours, colour identity or land mana colours.

Any change to how the generator lays out a predicate must be mirrored in
those readers and covered by `rust/crates/mtgcards/tests/ttl_loader.rs`.
`GRAPH-MANIFEST.json`'s `graph_schema` versions that predicate vocabulary:
bump it (`scripts/mtgcards/graph.py` and `rust/crates/mtgcards/src/graph.rs`)
when the vocabulary changes, not when a set is added.

## Two lists that must not drift

`BEHAVIOR_KEYS` exists twice: in `scripts/mtgcards/behaviors.py` (the
whitelist `check_consistency.py` applies to `MagicSimulationAnnotations.ttl`)
and in `rust/crates/mtgcards/src/behavior.rs` (what the simulator loads).
`rust/crates/mtgcards/tests/behavior_keys.rs` reads the Python file and
fails when they differ, so a new key lands in both in one commit.

## Python rules

`ruff` runs `select = ["ALL"]`; `mypy` runs `--strict` over `scripts/`.
No ignore baseline, no per-file-ignores — new suppressions only per line
(`# noqa: <RULE>` / `# nosec <ID>`) with a justification comment. The
packaged `mtgcards` must stay import-clean without rdflib; only
`mtgvalidate` may use it, behind the `validate` extra. `mtgarena` (the MTG
Arena collection tool) is repository tooling, not part of the wheel, and
imports Playwright only inside the live commands, so its offline tests and
the generator run without it. The generator and the validator restate its
snapshot's file name: `scripts/mtgarena/tests/test_contract.py` keeps the
three in step.

## Rust rules

`unsafe_code = "forbid"`; clippy `all` + `pedantic`, every warning an
error; no crate-wide `allow` — a suppression is one
`#[expect(clippy::<lint>, reason = "…")]` on one item. Every
rules-implementing item carries `#[rule("704.5g", …)]`; the binaries
validate the registry against the Comprehensive Rules text at startup.
The CPython-compatible layers (`pycompat`) are pinned by frozen vectors
under `rust/crates/*/tests/vectors/`: a seed must keep producing the
bytes it produced before the cutover.

## Agents

Repo-specific subagents live in `.opencode/agents/`:

| Agent | Owns |
|---|---|
| `graph-generator` | Running the import pipeline and diagnosing generator failures |
| `ontology-curator` | Hand-authored schema edits to `MagicCardsOntology.ttl` |
| `graph-validator` | Read-only validation and failure triage |
| `sparql-author` | The `queries/` catalog and `INDEX.md` |
| `graph-release` | Graph bundles, `GRAPH-MANIFEST.json`, `graph-*` tags |

Slash commands: `/graph-validate`, `/graph-regenerate`.
