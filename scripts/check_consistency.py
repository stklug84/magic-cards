#!/usr/bin/env python3
"""Cross-file consistency checks for the Magic card knowledge graph.

Thin wrapper over the packaged validator, kept so the CI job and the
CONTRIBUTING recipe keep working from a checkout without installing
anything. The checks themselves live in :mod:`mtgvalidate.consistency`
so a downstream deck repository can run them against a published graph
bundle - the Commander-total and dangling-reference checks are what catch
a deck graph that miscounts or references a card the graph no longer
defines.

Checks:
  1. undefined-terms - every mc: property / class used in instance data is
                       declared in MagicCardsOntology.ttl.
  2. dangling-refs   - every mc: individual used as subject or object of an
                       mc: property is typed (rdf:type) somewhere in the repo.
  3. card-entries    - every DeckEntry / CollectionEntry references an
                       existing card individual and carries a positive
                       :quantity; CollectionEntries carry finish + condition;
                       each Commander deck's entries total 100 cards; the
                       collection's entry quantities match collection.csv.
  4. synergy-domain  - synergy properties only connect card individuals.
  5. behavior-hooks  - every :hasBehaviorHook subject is a Card individual,
                       every hook carries exactly one whitelisted
                       :behaviorKey (scripts/mtgcards/behaviors.BEHAVIOR_KEYS)
                       and one JSON-parseable :behaviorValue; :threatWeight
                       is only asserted on Card individuals.

Exit code 0 on success, 1 on any failure.

Usage:
  python3 scripts/check_consistency.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtgvalidate.cli import main

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    raise SystemExit(main(["--check", "consistency", str(ROOT)]))
