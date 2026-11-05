# Contributing

Thanks for your interest in improving this repository. It is primarily a
personal MTG knowledge-graph project (ontology, card data, SPARQL query
library and a rules-engine simulator), but its validation infrastructure
(reusable RDF/Python workflows, cross-file consistency checks) is designed
to be reusable — contributions to either are welcome.

## Ground rules

- Open a pull request from a feature branch; all changes reach `main`
  through PRs, and CI validates the full knowledge graph on every PR.
- Keep PRs focused: one logical change (a deck, an ontology extension, a
  CI improvement) per PR.
- Do not commit build byproducts (`__pycache__/`, `.mypy_cache/`,
  `.ruff_cache/`, generated reports). They are gitignored; keep it that
  way.
- Keep gitignored data files out of PRs; the committed TTL files are the
  source of truth. The inventory (`collection.csv`) is a local, untracked
  generator input containing private data — never commit it.

## Repository layout

| Path | Purpose |
| --- | --- |
| `MagicCards*.ttl`, `MagicSimulationAnnotations.ttl` | Root ontologies and individuals (the knowledge graph) |
| `queries/` | SPARQL query library, one numbered topic directory per area (see `queries/INDEX.md`) |
| `rust/` | The simulator: a Cargo workspace (`pycompat`, `mtgcards`, `mtgrules`, `mtgviz`, `mtgsim` and the `#[rule]` macro crates) |
| `scripts/mtgcards/` | The graph-bundle contract (`graph.py`) and the behavior-key whitelist (`behaviors.py`) shared with the simulator |
| `scripts/mtgvalidate/` | Graph/query validators, root-parameterized (needs rdflib; the `validate` extra) |
| `scripts/*.py` | Entry points: validator wrappers, generators, graph-bundle builder |
| `sets/` | Per-set TTL slices (generated card individuals) |

The reusable workflows (`rdf-validate.yml`, `python-validate.yml`) live in
the central
[`stklug84/github-workflows`](https://github.com/stklug84/github-workflows)
repository and are consumed version-pinned from the workflows here.

## Linting and testing locally

Python tooling (configuration lives in `pyproject.toml`; versions match
the CI pins):

```sh
ruff check scripts/
ruff format --check scripts/
mypy                              # reads [tool.mypy]; strict = true
bandit -c pyproject.toml -r scripts
```

Repository and workflow linting:

```sh
yamllint --strict .
actionlint
npx --yes markdownlint-cli2 "**/*.md" "!.venv" "!venv" "!node_modules" "!dist" "!.opencode"
```

Knowledge-graph validation (the three entry points are thin wrappers over
`scripts/mtgvalidate/`, which the `validate` extra also exposes as the
single `mtg-validate` command):

```sh
python3 scripts/validate_ttl.py
python3 scripts/validate_sparql.py
python3 scripts/check_consistency.py
```

Graph-bundle build — the data artifact downstream repositories pin (see
the README section on consuming the graph). `release-graph.yml` runs this
on `graph-*` tags, unpacks the result and validates it standalone before
publishing:

```sh
python3 scripts/build_graph_bundle.py --graph-version "$(date -u +%F)"
```

Tools release — a `vX.Y.Z` tag publishes the `magic-cards-tools` wheel
and the Rust simulator binaries (`release.yml`). The tag must equal the
version in `pyproject.toml` and the `[workspace.package]` version in
`rust/Cargo.toml`; bump both in one commit. The binaries are built in
release mode on each supported platform (Linux and macOS, x86_64 and
arm64), smoke-tested against the checked-out graph and the fetched
Comprehensive Rules, and attached as one tarball per target triple with
a SHA-256 file. The same build locally:

```sh
cd rust
cargo build --release --locked -p mtgsim   # target/release/mtg-{simulate,selfplay,cr}
```

OWL reasoning (CI runs this via the `rdf / owl (robot reason)` check;
HermiT needs ~2 min for the ontology and ~8 min for the full per-set
closure):

```sh
python3 scripts/generate_catalog.py
curl -fsSL -o /tmp/robot.jar \
  https://github.com/ontodev/robot/releases/download/v1.9.8/robot.jar
java -jar /tmp/robot.jar reason --reasoner hermit \
  --catalog catalog-v001.xml --input MagicCardsOntology.ttl --output /tmp/r.owl
java -jar /tmp/robot.jar reason --reasoner hermit \
  --catalog catalog-v001.xml --input MagicCardIndividuals.ttl --output /tmp/r.owl
```

`catalog-v001.xml` maps the graph's `urn:` ontology IRIs to local files
(ROBOT cannot dereference them). The catalog is untracked and generated
on demand: the `owl` CI job regenerates it before reasoning
(`owl-prepare-command`), and locally you rerun
`scripts/generate_catalog.py` after adding, removing, or renaming any
ontology file.

Note for debugging a future inconsistency: `robot explain` rejects this
graph because `xsd:date` is outside the OWL 2 datatype map (plain
`reason` is unaffected). Strip `^^xsd:date` literals into strings on a
scratch copy first, then explain that copy.

Graph tooling tests (the generator's helpers and the bundle builder;
must stay green after every change to `scripts/`):

```sh
cd scripts
python3 -m unittest discover -s mtgcards/tests -t .
```

Python strictness: ruff runs with `select = ["ALL"]` and only permanent,
individually justified ignores (see `[tool.ruff.lint]` in
`pyproject.toml`); mypy runs `--strict` over all of `scripts/`. There is
no ignore baseline and no per-file-ignore section — new suppressions are
allowed only per line (`# noqa: <RULE>` / `# nosec <ID>`) with a
justification comment.

The simulator (`rust/`; the compiler is pinned by
`rust/rust-toolchain.toml` and installed by rustup on first use;
`cargo install cargo-deny cargo-llvm-cov --locked` and `rustup component
add llvm-tools-preview` once). The tests that need the Comprehensive
Rules text expect `MagicCompRules-*.txt` at the repository root (see the
README; it is gitignored):

```sh
cd rust
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cargo deny check
cargo llvm-cov --workspace --all-features --summary-only --fail-under-lines 90
cargo bench -p mtgrules --bench engine   # criterion: compile the graph, the mana solver, a full game
```

The test suite has three layers. The rules tests (one file per rules
topic under `rust/crates/mtgrules/tests/`) pin the rules of play; the
frozen vectors (`rust/crates/*/tests/vectors/`) pin the bytes — the
CPython-compatible layers against CPython 3.12 and the mana solver
against the engine the Rust one replaced, so a seed keeps producing the
output it always did; and the extended suite covers what neither does by
construction — property tests (`proptest`: the compiler is total over
arbitrary oracle text, the mana pool never overpays, the
CPython-compatible formatting round-trips, the view state never leaves
its range), snapshot tests (`insta`: the terminal frame rendered through
ratatui's `TestBackend`, the plain text frame; `cargo insta review` after
an intended change of the frame), the whole card pool dealt into decks
that play four-player games under every AI profile (`tests/pool.rs`),
and every hand-written card run through one battery of events and
activations (`tests/overrides_drill.rs`). CI gates line coverage at 90 %
(`rust-coverage` in `validate.yml`) — new code arrives with its tests.

Three examples dump the data layers canonically, one JSON line per
entry, for reviewing a change by diff: `cargo run -p mtgcards --example
dump_cards -- --root ../..` (every card field and behaviour key),
`cargo run -p mtgrules --example dump_abilities -- --root ../..` (every
compiled ability, overrides included) and `cargo run -p mtgrules
--example dump_cr` (the parsed Comprehensive Rules, as lengths and
digests). Dump before and after, diff the two files.

`mtg-simulate --jobs N` runs the seeds of a run on `N` threads (`0` =
the available CPUs). A seed's games share one RNG stream and stay
sequential, so the seed is the unit of parallelism and the output —
statistics, `--verbose` lines, `--log-file` records, the `--viz-file`
recording — is byte-identical to the sequential run.

Rust strictness mirrors the Python rules: `unsafe` is forbidden, clippy
runs `all` + `pedantic` and every warning is an error in CI
(`[workspace.lints]` in `rust/Cargo.toml`, proper nouns for
`doc_markdown` in `rust/clippy.toml`). There is no crate-wide `allow`;
a suppression is allowed only on a single item as
`#[expect(clippy::<lint>, reason = "…")]`. The `#[rule]` registry's two
distributed slices live in their own crate (`mtgrules-registry`) for
that reason: linkme's registration macro is then external to every
crate that annotates rules, so its `#[link_section]` never trips the
`unsafe_code` lint and no crate needs an exception. Tests that need the
Comprehensive Rules text skip with a notice when it is absent;
`MTG_REQUIRE_CR=1` (set in CI) turns that into a failure.

The workspace replaced a Python simulator of the same shape (packages
`mtgrules`, `mtgviz` and the simulator half of `mtgcards`, removed at
the cutover; their sources and the level-by-level parity harness that
proved the two implementations byte-identical — `scripts/parity/`,
`rust/xtask` — live in the git history up to that commit). The
observable output is therefore defined by CPython behaviour and the
`pycompat` crate keeps it so: recordings, logs and seeds from before the
cutover replay unchanged. The Rust `BEHAVIOR_KEYS` list is checked
against `scripts/mtgcards/behaviors.py` (the whitelist the graph
validators apply) by a `mtgcards` test, so the two cannot drift.

The rules tests live one file per rules topic under
`rust/crates/mtgrules/tests/` (`sba_704.rs`, `stack_601.rs`,
`combat_510.rs`, `layers_613.rs`, ...; the shared fixtures — `make_game`,
`creature`, `card_in_hand`, `settle` — live in `tests/common/mod.rs`).
The `Game` type itself is split by concern: `src/game.rs` holds the state
and the shared helpers, `src/game/{zones,triggers,actions,mana,casting,
resolution,sba}.rs` the rules of play, `src/combat.rs` the combat phase
and `src/turns.rs` the turn structure. The decision policy is the
`Policy` trait (`src/policy.rs`; the plain-rules choices as defaults)
with the AI in `src/policy/default_policy.rs`; `src/adapter.rs` runs a
match from decklists and `src/selfplay.rs` the profile tuning. The
binaries live in `crates/mtgsim` (`mtg-simulate`, `mtg-selfplay`,
`mtg-cr`), the shared command-line pieces in `mtgsim::cli`. The Rust
binaries take `--graph-root` / `--cr-file` and validate the `#[rule]`
registry at startup; their usage text is clap's, the stdout reports and
exit codes those of the Python command lines they replaced.
`crates/mtgviz` is the visualization: the recorder and the `--viz-file`
writer (`schema`, `recorder`), the view state and event formatting
(`view`), `--replay` and `--watch` (`replay`, `live`) and the ratatui
frame (`tui`, `keys`). The engine is paced from the recorder sink on the
main thread (its arena is single-threaded); without a terminal the
front-ends fall back to plain text viewers.

Rules traceability works as in Python: every rules-implementing item
carries `#[rule("704.5g", ...)]` (`mtgrules::rule`) and rules out of
scope are declared with `unsupported!("903.4", "reason")`. The macro
checks the number syntax at compile time; whether a number exists in
the Comprehensive Rules is checked at startup by every binary
(`mtgrules::cr::check_registry`), and `mtg-cr check` / `mtg-cr coverage`
run that check and the implemented / unsupported / unclaimed report by
hand. The binaries find `MagicCompRules-*.txt` at the graph root
(`--graph-root`, `MTG_GRAPH_ROOT`, or the checkout above the working
directory); `--help` and `--version` never need it.

## Turtle house style

The validators enforce syntax, prefixes and imports; these conventions are
the part a reviewer checks by eye.

- **Never hand-edit a generated file.** `sets/*.ttl`,
  `MagicCardCollection.ttl` and the import block of
  `MagicCardIndividuals.ttl` come from `scripts/generate_individuals.py`
  and `scripts/update_imports.py`. Change the generator and regenerate;
  an edit made by hand is silently reverted by the next run. The
  hand-authored files are `MagicCardsOntology.ttl`,
  `MagicCardSynergies.ttl` and `MagicSimulationAnnotations.ttl`.
- **One predicate, one object list.** Assert a multi-valued predicate once
  and separate its objects with `,`; never repeat the predicate. Objects
  align under the first one:

  ```turtle
  :hasSubType :Human ,
              :Artificer ;
  ```

  Repeating `:hasSubType` on two lines is the same RDF, but it is not the
  house style and it breaks the assumption the text-based readers make.
- **Layout of `MagicCardsOntology.ttl`**: `Ontology Definition` →
  `Class Definitions` → `Property Definitions` (object properties first,
  then datatype properties, each grouped by topic) → `Constraints` →
  `Individuals`, separated by 80-column `#` banners with `# --- Topic ---`
  sub-headers. Put a declaration in the section matching its kind: classes
  never live among the properties, and card-fact properties never live
  under `Simulation Behavior Annotations`.
- **Reformatting must be provably semantics-preserving.** A layout change
  has to leave the triple set untouched; check it with rdflib rather than
  by reading the diff:

  ```python
  from rdflib import Graph
  from rdflib.compare import graph_diff, to_isomorphic
  before, after = Graph().parse("old.ttl"), Graph().parse("new.ttl")
  _, only_old, only_new = graph_diff(to_isomorphic(before), to_isomorphic(after))
  assert not only_old and not only_new
  ```

- **Text-based readers are coupled to the layout.** The simulator's
  readers (`rust/crates/mtgcards/src/ttl_loader.rs`, `deck.rs`,
  `behavior.rs`) parse the generated files with regexes instead of an RDF
  library. Any change to how the generator lays out a predicate must be
  mirrored there and covered by a test
  (`rust/crates/mtgcards/tests/ttl_loader.rs`) — otherwise cards silently
  lose facts while every validator still passes.

## Working on the CI infrastructure

- **Keep the separation of concerns**: repo-specific checks (TTL
  conventions, graph consistency, the tooling tests, the Rust test and
  coverage jobs) live in `.github/workflows/validate.yml`; generic RDF
  and Python validation is
  consumed from the reusable workflows in
  [`stklug84/github-workflows`](https://github.com/stklug84/github-workflows).
  New generic behavior belongs over there — bump the version pin here
  after a release.
- **Workflows** must pass `actionlint`.
- **Dependencies** are managed by Dependabot (`.github/dependabot.yml`):
  GitHub Actions in workflows (including the pinned reusable-workflow
  references) plus the pip dev/CI tooling in `pyproject.toml`. Do not
  hand-bump pinned versions in a feature PR; let Dependabot do it, or
  open a dedicated PR.

## Branch protection

This repository is **public**, which enables repository rulesets and
CodeQL code scanning on the free plan. The `main` branch is governed by
the "Update main" ruleset below — it mirrors the `curriculum-vitae` and
`actions` rulesets with this repo's required status checks (including
the two CodeQL analyze jobs from `.github/workflows/codeql.yml`):

```json
{
  "name": "Update main",
  "target": "branch",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "exclude": [],
      "include": ["~DEFAULT_BRANCH", "refs/heads/main"]
    }
  },
  "rules": [
    { "type": "creation" },
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    { "type": "required_signatures" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "required_reviewers": [],
        "require_code_owner_review": true,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false,
        "allowed_merge_methods": ["merge", "squash", "rebase"]
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          { "context": "actionlint (workflows)" },
          { "context": "yamllint" },
          { "context": "markdownlint" },
          { "context": "python / ruff (lint + format)" },
          { "context": "python / mypy (strict)" },
          { "context": "python / bandit (security)" },
          { "context": "rdf / turtle (riot --validate)" },
          { "context": "rdf / sparql (rdflib parse)" },
          { "context": "rdf / owl (robot reason)" },
          { "context": "TTL conventions and imports (rdflib)" },
          { "context": "SPARQL syntax, prefixes, and terms" },
          { "context": "Cross-file graph consistency" },
          { "context": "Graph tooling tests" },
          { "context": "Rust workspace tests (ubuntu-latest)" },
          { "context": "Rust workspace tests (macos-latest)" },
          { "context": "Rust line coverage (>= 90 %)" },
          { "context": "Rust (fmt + clippy + deny)" },
          { "context": "Analyze (actions)" },
          { "context": "Analyze (python)" }
        ]
      }
    }
  ],
  "bypass_actors": []
}
```

Apply with:

```sh
gh api -X POST repos/stklug84/magic-cards/rulesets --input ruleset.json
```

Once the first CodeQL runs on `main` are clean, additionally add the
`code_scanning` rule (mirroring `stklug84/actions`) so scanning results
gate merges — the `security-extended` suite includes the unpinned-tag
query, which flags floating first-party tags (`actions/checkout@v7`) as
warnings, so verify the alert set first:

```json
{
  "type": "code_scanning",
  "parameters": {
    "code_scanning_tools": [
      {
        "tool": "CodeQL",
        "security_alerts_threshold": "high_or_higher",
        "alerts_threshold": "errors"
      }
    ]
  }
}
```

## Checklist before opening a PR

- [ ] `ruff check scripts/`, `ruff format --check scripts/`, `mypy` and
      `bandit -c pyproject.toml -r scripts` clean
- [ ] The tooling unittest suite passes
- [ ] `cargo fmt`, `clippy`, `test`, `deny` and the coverage gate clean
      in `rust/` for any change under `rust/`
- [ ] `actionlint` and `yamllint --strict .` clean on any touched YAML
- [ ] No build byproducts or unrelated changes staged
- [ ] README updated if behavior, layout, or conventions changed
