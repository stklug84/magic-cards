#!/usr/bin/env python3
"""Validate all Turtle (.ttl) files in the repository.

Thin wrapper over the packaged validator, kept so the CI job and the
CONTRIBUTING recipe keep working from a checkout without installing
anything. The checks themselves live in :mod:`mtgvalidate.ttl` so a
downstream repository can run them against a published graph bundle.

Checks:
  1. syntax      - every .ttl file parses as valid Turtle (rdflib).
  2. conventions - every .ttl file declares the standard prefixes and an
                   owl:Ontology header.
  3. imports     - every owl:imports target resolves to an ontology IRI
                   declared by some file in the repository.

Exit code 0 on success, 1 on any failure.

Usage:
  python3 scripts/validate_ttl.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtgvalidate.cli import main

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    raise SystemExit(main(["--check", "ttl", str(ROOT)]))
