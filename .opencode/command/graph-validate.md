---
description: Validate the knowledge graph — TTL, SPARQL, consistency and the unit suites
---

Validate the knowledge graph in this repository and report the verdict.

Delegate via `task` to subagent `graph-validator`. Pass along any scope
narrowing from $ARGUMENTS (for example "only the TTL checks", "include
robot reason", "just the collection"); with no arguments, run the standard
sweep:

1. `riot --validate` over every `.ttl`
2. `python3 scripts/validate_ttl.py`
3. `python3 scripts/validate_sparql.py`
4. `python3 scripts/check_consistency.py`
5. the `mtgcards`, `mtgrules` and `mtgviz` unit suites from `scripts/`

OWL reasoning (`robot reason --reasoner hermit`) is **not** part of the
standard sweep — it takes ~2 min for the ontology and ~8 min for the full
closure. Run it only when $ARGUMENTS asks for it, or when the change under
review touched class axioms or constraints.

The validators need `rdflib`. If it is missing, create a local virtualenv
(`uv venv .venv && uv pip install --python .venv/bin/python "rdflib>=7,<8"`)
rather than installing into the system interpreter.

Report back: the verdict, the counts (TTL files, SPARQL files, triples,
collection entries), and one triage block per failure with a `file:line`
and a probable cause. Do not fix anything — this command is read-only.
