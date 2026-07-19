#!/usr/bin/env python3
"""Validate all Turtle (.ttl) files in the repository.

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

from rdflib import Graph, RDF, URIRef
from rdflib.namespace import OWL

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_PREFIXES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "": "urn:stklug84:MagicCardsOntology:2026-02-27#",
}


def ttl_files() -> list[Path]:
    files = sorted(
        p
        for p in ROOT.rglob("*.ttl")
        if ".git" not in p.parts
    )
    if not files:
        print("ERROR: no .ttl files found", file=sys.stderr)
        sys.exit(1)
    return files


def main() -> int:
    errors: list[str] = []
    graphs: dict[Path, Graph] = {}

    files = ttl_files()

    # --- 1. syntax ------------------------------------------------------
    for path in files:
        rel = path.relative_to(ROOT)
        graph = Graph()
        try:
            graph.parse(path, format="turtle")
        except Exception as exc:  # noqa: BLE001 - report any parse failure
            errors.append(f"{rel}: syntax error: {exc}")
            continue
        graphs[path] = graph
        print(f"OK   syntax      {rel} ({len(graph)} triples)")

    # --- 2. conventions ---------------------------------------------------
    declared_ontologies: set[URIRef] = set()
    for path, graph in graphs.items():
        rel = path.relative_to(ROOT)
        prefixes = {p: str(ns) for p, ns in graph.namespaces()}
        for prefix, expected_ns in REQUIRED_PREFIXES.items():
            actual = prefixes.get(prefix)
            if actual != expected_ns:
                errors.append(
                    f"{rel}: prefix '{prefix}:' is {actual!r}, expected {expected_ns!r}"
                )

        ontologies = list(graph.subjects(RDF.type, OWL.Ontology))
        if len(ontologies) != 1:
            errors.append(
                f"{rel}: expected exactly 1 owl:Ontology declaration, found {len(ontologies)}"
            )
        declared_ontologies.update(o for o in ontologies if isinstance(o, URIRef))

    # --- 3. imports resolve ----------------------------------------------
    for path, graph in graphs.items():
        rel = path.relative_to(ROOT)
        for target in graph.objects(None, OWL.imports):
            if target not in declared_ontologies:
                errors.append(f"{rel}: owl:imports target not found in repo: {target}")

    # --- report ------------------------------------------------------------
    print()
    print(f"Checked {len(files)} TTL files.")
    if errors:
        print(f"\n{len(errors)} error(s):", file=sys.stderr)
        for err in errors:
            print(f"FAIL {err}", file=sys.stderr)
        return 1
    print("All TTL checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
