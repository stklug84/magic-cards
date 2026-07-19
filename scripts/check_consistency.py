#!/usr/bin/env python3
"""Cross-file consistency checks for the Magic card knowledge graph.

Loads all TTL files into one combined graph and checks:
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

    # --- 3. card entries (deck + collection) ----------------------------------
    card_entry_cls = mc_ref("CardEntry")
    entry_card = mc_ref("entryCard")
    quantity = mc_ref("quantity")
    card_cls = mc_ref("Card")

    card_classes = {card_cls} | {
        c for c in combined.transitive_subjects(RDFS.subClassOf, card_cls)
    }

    def is_card(node: URIRef) -> bool:
        return any(t in card_classes for t in combined.objects(node, RDF.type))

    entry_classes = {card_entry_cls} | {
        c for c in combined.transitive_subjects(RDFS.subClassOf, card_entry_cls)
    }
    entries = {
        e
        for cls in entry_classes
        for e in combined.subjects(RDF.type, cls)
    }
    for entry in sorted(entries):
        cards = list(combined.objects(entry, entry_card))
        if len(cards) != 1:
            errors.append(f"card-entries: {entry} has {len(cards)} {entry_card} values")
        for card in cards:
            if not is_card(card):
                errors.append(f"card-entries: {entry} references non-card {card}")
        counts = list(combined.objects(entry, quantity))
        if not counts:
            errors.append(f"card-entries: {entry} has no {quantity}")
        for count in counts:
            if not isinstance(count, Literal) or int(count) < 1:
                errors.append(f"card-entries: {entry} has invalid quantity {count!r}")

    # collection entries carry finish + condition; quantities match the csv
    coll_entry_cls = mc_ref("CollectionEntry")
    has_finish = mc_ref("hasFinish")
    has_condition = mc_ref("hasCondition")
    coll_entries = set(combined.subjects(RDF.type, coll_entry_cls))
    coll_total = 0
    for entry in sorted(coll_entries):
        for prop in (has_finish, has_condition):
            if not list(combined.objects(entry, prop)):
                errors.append(f"card-entries: {entry} has no {prop}")
        for count in combined.objects(entry, quantity):
            coll_total += int(count)

    csv_path = ROOT / "collection.csv"
    if csv_path.exists():
        import csv as _csv

        csv_rows = list(_csv.DictReader(open(csv_path)))
        csv_total = sum(int(r["Count"]) for r in csv_rows)
        if coll_entries and len(coll_entries) != len(csv_rows):
            errors.append(
                f"card-entries: {len(coll_entries)} collection entries but "
                f"{len(csv_rows)} rows in collection.csv"
            )
        if coll_entries and coll_total != csv_total:
            errors.append(
                f"card-entries: collection quantities sum to {coll_total} but "
                f"collection.csv counts sum to {csv_total}"
            )

    # every Commander deck's entries must total exactly 100 cards
    commander_deck_cls = mc_ref("CommanderDeck")
    has_deck_entry = mc_ref("hasDeckEntry")
    for deck in sorted(set(combined.subjects(RDF.type, commander_deck_cls))):
        deck_entries = list(combined.objects(deck, has_deck_entry))
        if not deck_entries:
            continue
        total = sum(
            int(count)
            for e in deck_entries
            for count in combined.objects(e, quantity)
        )
        if total != 100:
            errors.append(
                f"card-entries: Commander deck {deck} entries total {total}, expected 100"
            )

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
        f"{len(entries)} card entries ({len(coll_entries)} collection, "
        f"{len(entries) - len(coll_entries)} deck)."
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
