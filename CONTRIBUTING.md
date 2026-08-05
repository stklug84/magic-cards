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
- Keep gitignored data files out of PRs; the committed TTL files and
  `collection.csv` are the source of truth.

## Repository layout

| Path | Purpose |
| --- | --- |
| `MagicCards*.ttl`, `MagicExternalCards.ttl`, `MagicSimulationAnnotations.ttl` | Root ontologies and individuals (the knowledge graph) |
| `queries/` | SPARQL query library, one numbered topic directory per area (see `queries/INDEX.md`) |
| `scripts/mtgcards/` | Card data model, deck loading, TTL-backed card database |
| `scripts/mtgrules/` | Comprehensive-Rules engine (stdlib only) + conformance tests |
| `scripts/mtgviz/` | Game visualization: recorder, replay, live TUI + tests |
| `scripts/*.py` | Entry points: validators, consistency check, generators, matchup simulator |
| `decks/`, `sets/` | Per-deck and per-set TTL slices |
| `strategies/` | Deck lists and strategy notes for the simulator |

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
mypy                              # reads [tool.mypy]; non-strict for now
bandit -c pyproject.toml -r scripts
```

Repository and workflow linting:

```sh
yamllint --strict .
actionlint
npx --yes markdownlint-cli2 "**/*.md"
```

Knowledge-graph validation:

```sh
python3 scripts/validate_ttl.py
python3 scripts/validate_sparql.py
python3 scripts/check_consistency.py
```

OWL reasoning (CI runs this via the `rdf / owl (robot reason)` check;
HermiT needs ~2 min for the ontology and ~8 min for the full per-set
closure):

```sh
curl -fsSL -o /tmp/robot.jar \
  https://github.com/ontodev/robot/releases/download/v1.9.8/robot.jar
java -jar /tmp/robot.jar reason --reasoner hermit \
  --catalog catalog-v001.xml --input MagicCardsOntology.ttl --output /tmp/r.owl
java -jar /tmp/robot.jar reason --reasoner hermit \
  --catalog catalog-v001.xml --input MagicCardIndividuals.ttl --output /tmp/r.owl
```

`catalog-v001.xml` maps the graph's `urn:` ontology IRIs to local files
(ROBOT cannot dereference them). Regenerate it after adding, removing,
or renaming any ontology file — the `owl` CI job fails on unresolvable
imports when the catalog is stale:

```sh
python3 scripts/generate_catalog.py
```

Note for debugging a future inconsistency: `robot explain` rejects this
graph because `xsd:date` is outside the OWL 2 datatype map (plain
`reason` is unaffected). Strip `^^xsd:date` literals into strings on a
scratch copy first, then explain that copy.

Simulator tests and smoke matchup (must stay green after every change to
`scripts/`):

```sh
cd scripts
python3 -m unittest discover -s mtgcards/tests -t .
python3 -m unittest discover -s mtgrules/tests -t .
python3 -m unittest discover -s mtgviz/tests -t .
cd ..
python3 scripts/simulate_matchup.py \
  strategies/station-swarm-counter-deck.txt \
  strategies/blight-curse-deck.txt --games 3 --seed 7
```

Python strictness: ruff runs with `select = ["ALL"]` and only permanent,
individually justified ignores (see `[tool.ruff.lint]` in
`pyproject.toml`); mypy runs `--strict` over all of `scripts/`. There is
no ignore baseline and no per-file-ignore section — new suppressions are
allowed only per line (`# noqa: <RULE>` / `# nosec <ID>`) with a
justification comment.

## Working on the CI infrastructure

- **Keep the separation of concerns**: repo-specific checks (TTL
  conventions, graph consistency, simulator tests) live in
  `.github/workflows/validate.yml`; generic RDF and Python validation is
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

## Branch protection (pending)

This repository is currently **private on a free plan**, so repository
rulesets and CodeQL/GHAS are unavailable. Once the repo is public (or on
a Pro/Team plan), apply the ruleset below — it mirrors the
`curriculum-vitae` "Update main" ruleset (minus the CodeQL rules, which
this repo cannot use) with this repo's required status checks:

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
        "require_code_owner_review": false,
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
          { "context": "Rules engine + viz unit tests" }
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

## Checklist before opening a PR

- [ ] `ruff check scripts/`, `ruff format --check scripts/`, `mypy` and
      `bandit -c pyproject.toml -r scripts` clean
- [ ] Both unittest suites and the 3-game smoke matchup pass
- [ ] `actionlint` and `yamllint --strict .` clean on any touched YAML
- [ ] No build byproducts or unrelated changes staged
- [ ] README updated if behavior, layout, or conventions changed
