#!/usr/bin/env python3
"""Cross-file consistency checks for the Magic card knowledge graph.

Loads all TTL files into one combined graph and checks:
  1. undefined-terms - every mc: property / class used in instance data is
                       declared in MagicCardsOntology.ttl.
  2. dangling-refs   - every mc: individual used as subject or object of an
                       mc: property is typed (rdf:type) somewhere in the repo.
  3. deck-entries    - every DeckEntry references an existing card individual
                       and carries a positive :hasCount.
  4. synergy-domain  - synergy properties only connect card individuals.

Exit code 0 on success, 1 on any failure.

Usage:
  python3 scripts/check_consistency.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from rdflib import Graph, RDF, RDFS, URIRef, Literal
from rdflib.namespace import OWL

ROOT = Path(__file__).resolve().parent.parent
MC = "urn:stklug84:MagicCardsOntology:2026-02-27#"

SYNERGY_PROPS = {
    URIRef(MC + name)
    for name in ("hasSynergyWith", "amplifies", "isAmplifiedBy", "enables", "isEnabledBy")
}


def mc_ref(name: str) -> URIRef:
    return URIRef(MC + name)


def load() -> tuple[Graph, Graph]:
    ontology = Graph()
    ontology.parse(ROOT / "MagicCardsOntology.ttl", format="turtle")

    combined = Graph()
    for path in sorted(ROOT.rglob("*.ttl")):
        if ".git" in path.parts:
            continue
        combined.parse(path, format="turtle")
    return ontology, combined


def main() -> int:
    errors: list[str] = []
    ontology, combined = load()
    print(f"Ontology: {len(ontology)} triples; combined graph: {len(combined)} triples.")

    declared_props = set(ontology.subjects(RDF.type, OWL.ObjectProperty))
    declared_props |= set(ontology.subjects(RDF.type, OWL.DatatypeProperty))
    declared_props |= set(ontology.subjects(RDF.type, OWL.AnnotationProperty))
    declared_classes = set(ontology.subjects(RDF.type, OWL.Class))
    typed = set(combined.subjects(RDF.type, None))

    # --- 1. undefined terms ------------------------------------------------
    used_props = {p for p in combined.predicates(None, None) if str(p).startswith(MC)}
    for prop in sorted(used_props):
        if prop not in declared_props:
            errors.append(f"undefined-terms: property used but not declared: {prop}")

    used_classes = {
        c for c in combined.objects(None, RDF.type)
        if isinstance(c, URIRef) and str(c).startswith(MC)
    }
    for cls in sorted(used_classes):
        if cls not in declared_classes:
            errors.append(f"undefined-terms: class used but not declared: {cls}")

    # --- 2. dangling references ----------------------------------------------
    for s, p, o in combined:
        if not str(p).startswith(MC):
            continue
        for node, role in ((s, "subject"), (o, "object")):
            if (
                isinstance(node, URIRef)
                and str(node).startswith(MC)
                and node not in typed
                and node not in declared_props
                and node not in declared_classes
            ):
                errors.append(
                    f"dangling-refs: {role} {node} of {p} has no rdf:type anywhere"
                )

    # --- 3. deck entries -----------------------------------------------------
    deck_entry_cls = mc_ref("DeckEntry")
    has_card = mc_ref("hasCard")
    has_count = mc_ref("hasCount")
    card_cls = mc_ref("Card")

    card_classes = {card_cls} | {
        c for c in combined.transitive_subjects(RDFS.subClassOf, card_cls)
    }

    def is_card(node: URIRef) -> bool:
        return any(t in card_classes for t in combined.objects(node, RDF.type))

    entry_classes = {deck_entry_cls} | {
        c for c in combined.transitive_subjects(RDFS.subClassOf, deck_entry_cls)
    }
    entries = {
        e
        for cls in entry_classes
        for e in combined.subjects(RDF.type, cls)
    }
    for entry in sorted(entries):
        cards = list(combined.objects(entry, has_card))
        if not cards:
            errors.append(f"deck-entries: {entry} has no {has_card}")
        for card in cards:
            if not is_card(card):
                errors.append(f"deck-entries: {entry} references non-card {card}")
        counts = list(combined.objects(entry, has_count))
        if not counts:
            errors.append(f"deck-entries: {entry} has no {has_count}")
        for count in counts:
            if not isinstance(count, Literal) or int(count) < 1:
                errors.append(f"deck-entries: {entry} has invalid count {count!r}")

    # --- 4. synergy domain/range ---------------------------------------------
    for prop in sorted(SYNERGY_PROPS):
        for s, o in combined.subject_objects(prop):
            for node, role in ((s, "subject"), (o, "object")):
                if isinstance(node, URIRef) and not is_card(node):
                    errors.append(
                        f"synergy-domain: {role} {node} of {prop} is not a Card individual"
                    )

    # --- report ------------------------------------------------------------
    print(
        f"Checked {len(used_props)} properties, {len(used_classes)} classes, "
        f"{len(entries)} deck entries."
    )
    if errors:
        unique = sorted(set(errors))
        print(f"\n{len(unique)} error(s):", file=sys.stderr)
        for err in unique:
            print(f"FAIL {err}", file=sys.stderr)
        return 1
    print("All consistency checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
