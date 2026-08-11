---
description: Re-run the import pipeline from collection.csv, with a drift gate and verification
---

Regenerate the knowledge graph from the local `collection.csv`.

Delegate via `task` to subagent `graph-generator`, then to
`graph-validator` once regeneration completes.

Scope from $ARGUMENTS. With no arguments, run the full pipeline. Common
narrowings:

- *inventory only* (quantities, finishes, conditions changed, no new
  printings) — `collection` alone is enough; it needs no network;
- *no network* — skip `fetch` and rely on the warm cache in
  `$TMPDIR/scryfall_cache`.

Before regenerating, enforce the **drift gate**:

1. `git status` must be clean; if it is not, stop and report what is
   uncommitted rather than mixing it into the regeneration.
2. Run the pipeline with the generator **unmodified** and `git diff --stat`.
3. Any diff at this point is upstream Scryfall drift — new rulings, changed
   legalities — not a local change. Report it and ask whether it should
   ship now, land in its own commit, or be reverted. Do not fold it
   silently into an unrelated change.

Pipeline, in order (`build_vocab` reads the ontology, `collection` reads
`sets/`):

```sh
python3 scripts/build_vocab.py
python3 scripts/generate_individuals.py fetch
python3 scripts/generate_individuals.py generate
python3 scripts/generate_individuals.py collection
python3 scripts/update_imports.py
```

Then verify, and report the numbers:

- **inventory preserved** — total physical copies unchanged, every card's
  per-card total unchanged; only the entry count may fall, and only by
  merging duplicate variants;
- **card facts preserved** — load `sets/` through
  `mtgcards.ttl_loader.load_graph_cards` before and after, and compare the
  aggregate counts (this is what catches the regex readers silently
  dropping objects from a changed layout);
- **validation green** — hand off to `graph-validator`.

Never hand-edit a generated file to make a check pass. Fix the generator
and regenerate.
