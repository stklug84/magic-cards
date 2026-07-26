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

from rdflib import RDF, Graph, URIRef
from rdflib.namespace import OWL

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_PREFIXES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "": "urn:stklug84:MagicCardsOntology:2026-02-27#",
}


def ttl_files() -> list[Path]:
    """Return every tracked .ttl file; exit 1 if the repo has none."""
    files = sorted(p for p in ROOT.rglob("*.ttl") if ".git" not in p.parts)
    if not files:
        print("ERROR: no .ttl files found", file=sys.stderr)  # noqa: T201 - validator FAIL output on stderr
        sys.exit(1)
    return files


def check_syntax(files: list[Path]) -> tuple[dict[Path, Graph], list[str]]:
    """Check 1 (syntax): every .ttl file must parse as valid Turtle.

    Returns the successfully parsed graphs plus one error per file that
    rdflib rejects; prints one OK line per parsed file.
    """
    errors: list[str] = []
    graphs: dict[Path, Graph] = {}
    for path in files:
        rel = path.relative_to(ROOT)
        graph = Graph()
        try:
            graph.parse(path, format="turtle")
        except Exception as exc:  # noqa: BLE001 - report any parse failure
            errors.append(f"{rel}: syntax error: {exc}")
            continue
        graphs[path] = graph
        print(f"OK   syntax      {rel} ({len(graph)} triples)")  # noqa: T201 - validator OK line, grepped by CI
    return graphs, errors


def check_conventions(graphs: dict[Path, Graph]) -> tuple[set[URIRef], list[str]]:
    """Check 2 (conventions): standard prefixes and one owl:Ontology header.

    Every file must declare the REQUIRED_PREFIXES bindings verbatim and
    exactly one owl:Ontology. Returns the declared ontology IRIs (input
    to the imports check) and the convention violations.
    """
    errors: list[str] = []
    declared_ontologies: set[URIRef] = set()
    for path, graph in graphs.items():
        rel = path.relative_to(ROOT)
        prefixes = {p: str(ns) for p, ns in graph.namespaces()}
        for prefix, expected_ns in REQUIRED_PREFIXES.items():
            actual = prefixes.get(prefix)
            if actual != expected_ns:
                errors.append(
                    f"{rel}: prefix '{prefix}:' is {actual!r}, want {expected_ns!r}",
                )

        ontologies = list(graph.subjects(RDF.type, OWL.Ontology))
        if len(ontologies) != 1:
            errors.append(
                f"{rel}: expected 1 owl:Ontology declaration, found {len(ontologies)}",
            )
        declared_ontologies.update(o for o in ontologies if isinstance(o, URIRef))
    return declared_ontologies, errors


def check_imports(
    graphs: dict[Path, Graph],
    declared_ontologies: set[URIRef],
) -> list[str]:
    """Check 3 (imports): every owl:imports target must be declared in-repo."""
    return [
        f"{path.relative_to(ROOT)}: owl:imports target not found in repo: {target}"
        for path, graph in graphs.items()
        for target in graph.objects(None, OWL.imports)
        if target not in declared_ontologies
    ]


def report(n_files: int, errors: list[str]) -> int:
    """Print the summary and FAIL lines; return the process exit code."""
    # T201+RUF100 (below): this validator's program output, consumed by
    print()  # noqa: T201
    print(f"Checked {n_files} TTL files.")  # noqa: T201
    if errors:
        print(f"\n{len(errors)} error(s):", file=sys.stderr)  # noqa: T201
        for err in errors:
            print(f"FAIL {err}", file=sys.stderr)  # noqa: T201
        return 1
    print("All TTL checks passed.")  # noqa: T201
    return 0


def main() -> int:
    """Run the three TTL checks over the repository and report."""
    files = ttl_files()
    graphs, errors = check_syntax(files)
    declared_ontologies, convention_errors = check_conventions(graphs)
    errors.extend(convention_errors)
    errors.extend(check_imports(graphs, declared_ontologies))
    return report(len(files), errors)


if __name__ == "__main__":
    sys.exit(main())
