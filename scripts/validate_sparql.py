#!/usr/bin/env python3
"""Validate all SPARQL query files (.rq) in the repository.

Thin wrapper over the packaged validator, kept so the CI job and the
CONTRIBUTING recipe keep working from a checkout without installing
anything. The checks themselves live in :mod:`mtgvalidate.sparql`.

Checks:
  1. syntax   - every .rq file parses as a valid SPARQL 1.1 query (rdflib).
  2. prefixes - every query declares the mc: prefix with the canonical
                ontology namespace when it uses mc: terms.
  3. terms    - every mc: term referenced in a query exists in the combined
                repository graph (ontology + individuals), catching typos in
                class/property/individual names.

Exit code 0 on success, 1 on any failure.

Usage:
  python3 scripts/validate_sparql.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtgvalidate.cli import main

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    raise SystemExit(main(["--check", "sparql", str(ROOT)]))
