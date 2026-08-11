"""Turtle file checks: syntax, house conventions, and import resolution.

  1. syntax      - every .ttl file parses as valid Turtle (rdflib).
  2. conventions - every .ttl file declares the standard prefixes and
                   exactly one owl:Ontology header.
  3. imports     - every owl:imports target resolves to an ontology IRI
                   declared by some file under the graph roots.

Check 3 is why a consumer must validate against the graph bundle rather
than its own files alone: deck graphs import the ontology IRI, which only
the bundled MagicCardsOntology.ttl declares.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rdflib import RDF, Graph, URIRef
from rdflib.namespace import OWL

if TYPE_CHECKING:
    from pathlib import Path

    from mtgvalidate.context import ValidationContext

#: prefixes every graph file must bind verbatim; the empty prefix is
#: filled in from the context's ontology IRI
REQUIRED_PREFIXES = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
}


def check_syntax(
    ctx: ValidationContext,
    files: list[Path],
) -> tuple[dict[Path, Graph], list[str]]:
    """Check 1 (syntax): every .ttl file must parse as valid Turtle.

    Returns the successfully parsed graphs plus one error per file that
    rdflib rejects; prints one OK line per parsed file.
    """
    errors: list[str] = []
    graphs: dict[Path, Graph] = {}
    for path in files:
        rel = ctx.display(path)
        graph = Graph()
        try:
            graph.parse(path, format="turtle")
        except Exception as exc:  # noqa: BLE001 - report any parse failure
            errors.append(f"{rel}: syntax error: {exc}")
            continue
        graphs[path] = graph
        print(f"OK   syntax      {rel} ({len(graph)} triples)")  # noqa: T201 - validator OK line
    return graphs, errors


def check_conventions(
    ctx: ValidationContext,
    graphs: dict[Path, Graph],
) -> tuple[set[URIRef], list[str]]:
    """Check 2 (conventions): standard prefixes and one owl:Ontology header.

    Returns the declared ontology IRIs (input to the imports check) and
    the convention violations.
    """
    errors: list[str] = []
    expected = {**REQUIRED_PREFIXES, "": ctx.ontology_iri}
    declared_ontologies: set[URIRef] = set()
    for path, graph in graphs.items():
        rel = ctx.display(path)
        prefixes = {p: str(ns) for p, ns in graph.namespaces()}
        for prefix, expected_ns in expected.items():
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
    ctx: ValidationContext,
    graphs: dict[Path, Graph],
    declared_ontologies: set[URIRef],
) -> list[str]:
    """Check 3 (imports): every owl:imports target must be declared."""
    return [
        f"{ctx.display(path)}: owl:imports target not found "
        f"under the graph roots: {target}"
        for path, graph in graphs.items()
        for target in graph.objects(None, OWL.imports)
        if target not in declared_ontologies
    ]


def run(ctx: ValidationContext) -> list[str]:
    """Run all three TTL checks and return the collected errors."""
    files = ctx.ttl_files()
    if not files:
        return ["ttl: no .ttl files found under the graph roots"]
    graphs, errors = check_syntax(ctx, files)
    declared_ontologies, convention_errors = check_conventions(ctx, graphs)
    errors.extend(convention_errors)
    errors.extend(check_imports(ctx, graphs, declared_ontologies))
    print(f"\nChecked {len(files)} TTL files.")  # noqa: T201 - validator summary line
    return errors
