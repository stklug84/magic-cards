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

import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rdflib import RDF, RDFS, Graph, Literal, URIRef
from rdflib.namespace import OWL

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mtgcards.behaviors import BEHAVIOR_KEYS

if TYPE_CHECKING:
    from rdflib.term import Node

ROOT = Path(__file__).resolve().parent.parent
MC = "urn:stklug84:MagicCardsOntology:2026-02-27#"

#: CR 903.5a: a Commander deck contains exactly 100 cards.
COMMANDER_DECK_SIZE = 100

SYNERGY_PROPS = {
    URIRef(MC + name)
    for name in (
        "hasSynergyWith",
        "amplifies",
        "isAmplifiedBy",
        "enables",
        "isEnabledBy",
    )
}


def mc_ref(name: str) -> URIRef:
    """Return the URIRef of a local name in the mc: namespace."""
    return URIRef(MC + name)


def load() -> tuple[Graph, Graph]:
    """Parse the ontology alone and all repo TTL files into one graph."""
    ontology = Graph()
    ontology.parse(ROOT / "MagicCardsOntology.ttl", format="turtle")

    combined = Graph()
    for path in sorted(ROOT.rglob("*.ttl")):
        if ".git" in path.parts:
            continue
        combined.parse(path, format="turtle")
    return ontology, combined


@dataclass
class GraphIndex:
    """The combined repository graph plus the derived lookup sets."""

    ontology: Graph
    combined: Graph
    declared_props: set[Node]
    declared_classes: set[Node]
    typed: set[Node]
    card_classes: set[Node]

    def is_card(self, node: Node) -> bool:
        """Return True if the node is typed as :Card or a subclass of it."""
        return any(
            t in self.card_classes for t in self.combined.objects(node, RDF.type)
        )


def index_graphs() -> GraphIndex:
    """Load the graphs and precompute the sets shared by all checks."""
    ontology, combined = load()
    declared_props = set(ontology.subjects(RDF.type, OWL.ObjectProperty))
    declared_props |= set(ontology.subjects(RDF.type, OWL.DatatypeProperty))
    declared_props |= set(ontology.subjects(RDF.type, OWL.AnnotationProperty))
    declared_classes = set(ontology.subjects(RDF.type, OWL.Class))
    typed = set(combined.subjects(RDF.type, None))
    card_cls = mc_ref("Card")
    card_classes: set[Node] = {card_cls} | {
        # transitive_subjects is typed Node | None (it echoes its object
        # argument, which is optional); card_cls is never None here.
        s
        for s in combined.transitive_subjects(RDFS.subClassOf, card_cls)
        if s is not None
    }
    return GraphIndex(
        ontology=ontology,
        combined=combined,
        declared_props=declared_props,
        declared_classes=declared_classes,
        typed=typed,
        card_classes=card_classes,
    )


def check_undefined_terms(idx: GraphIndex) -> tuple[list[str], int, int]:
    """Check 1 (undefined-terms): every used mc: term must be declared.

    Every mc: predicate and every mc: class used in instance data must
    be declared in MagicCardsOntology.ttl. Returns the errors plus the
    used property / class counts for the summary line.
    """
    used_props = {
        p for p in idx.combined.predicates(None, None) if str(p).startswith(MC)
    }
    errors = [
        f"undefined-terms: property used but not declared: {prop}"
        for prop in sorted(used_props, key=str)
        if prop not in idx.declared_props
    ]
    used_classes = {
        c
        for c in idx.combined.objects(None, RDF.type)
        if isinstance(c, URIRef) and str(c).startswith(MC)
    }
    errors.extend(
        f"undefined-terms: class used but not declared: {cls}"
        for cls in sorted(used_classes, key=str)
        if cls not in idx.declared_classes
    )
    return errors, len(used_props), len(used_classes)


def check_dangling_refs(idx: GraphIndex) -> list[str]:
    """Check 2 (dangling-refs): every referenced mc: individual is typed.

    Every mc: URIRef used as subject or object of an mc: property must
    carry an rdf:type somewhere in the repo (or be a declared property
    or class).
    """
    errors: list[str] = []
    for s, p, o in idx.combined:
        if not str(p).startswith(MC):
            continue
        errors.extend(
            f"dangling-refs: {role} {node} of {p} has no rdf:type anywhere"
            for node, role in ((s, "subject"), (o, "object"))
            if (
                isinstance(node, URIRef)
                and str(node).startswith(MC)
                and node not in idx.typed
                and node not in idx.declared_props
                and node not in idx.declared_classes
            )
        )
    return errors


def check_entry_shape(idx: GraphIndex) -> tuple[list[str], int]:
    """Check 3a (card-entries): entry individuals are well-formed.

    Every CardEntry (or subclass) individual must reference exactly one
    existing card individual via :entryCard and carry a positive integer
    :quantity. Returns the errors plus the total entry count.
    """
    errors: list[str] = []
    card_entry_cls = mc_ref("CardEntry")
    entry_card = mc_ref("entryCard")
    quantity = mc_ref("quantity")

    entry_classes = {card_entry_cls} | set(
        idx.combined.transitive_subjects(RDFS.subClassOf, card_entry_cls),
    )
    entries = {e for cls in entry_classes for e in idx.combined.subjects(RDF.type, cls)}
    for entry in sorted(entries, key=str):
        cards = list(idx.combined.objects(entry, entry_card))
        if len(cards) != 1:
            errors.append(f"card-entries: {entry} has {len(cards)} {entry_card} values")
        errors.extend(
            f"card-entries: {entry} references non-card {card}"
            for card in cards
            if not idx.is_card(card)
        )
        counts = list(idx.combined.objects(entry, quantity))
        if not counts:
            errors.append(f"card-entries: {entry} has no {quantity}")
        errors.extend(
            f"card-entries: {entry} has invalid quantity {count!r}"
            for count in counts
            if not isinstance(count, Literal) or int(count) < 1
        )
    return errors, len(entries)


def check_collection_entries(idx: GraphIndex) -> tuple[list[str], int]:
    """Check 3b (card-entries): collection entries mirror collection.csv.

    Every CollectionEntry must carry :hasFinish and :hasCondition, and
    the entry count and summed quantities must match the rows of
    collection.csv. Returns the errors plus the collection entry count.
    """
    errors: list[str] = []
    quantity = mc_ref("quantity")
    has_finish = mc_ref("hasFinish")
    has_condition = mc_ref("hasCondition")
    coll_entries = set(idx.combined.subjects(RDF.type, mc_ref("CollectionEntry")))
    coll_total = 0
    for entry in sorted(coll_entries, key=str):
        errors.extend(
            f"card-entries: {entry} has no {prop}"
            for prop in (has_finish, has_condition)
            if not list(idx.combined.objects(entry, prop))
        )
        for count in idx.combined.objects(entry, quantity):
            coll_total += int(str(count))

    csv_path = ROOT / "collection.csv"
    if csv_path.exists():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        csv_total = sum(int(r["Count"]) for r in csv_rows)
        if coll_entries and len(coll_entries) != len(csv_rows):
            errors.append(
                f"card-entries: {len(coll_entries)} collection entries but "
                f"{len(csv_rows)} rows in collection.csv",
            )
        if coll_entries and coll_total != csv_total:
            errors.append(
                f"card-entries: collection quantities sum to {coll_total} but "
                f"collection.csv counts sum to {csv_total}",
            )
    return errors, len(coll_entries)


def check_commander_totals(idx: GraphIndex) -> list[str]:
    """Check 3c (card-entries): Commander decks total exactly 100 cards.

    Sums the :quantity of every :hasDeckEntry of each CommanderDeck
    individual (CR 903.5a); decks without entries are skipped.
    """
    errors: list[str] = []
    quantity = mc_ref("quantity")
    has_deck_entry = mc_ref("hasDeckEntry")
    decks = set(idx.combined.subjects(RDF.type, mc_ref("CommanderDeck")))
    for deck in sorted(decks, key=str):
        deck_entries = list(idx.combined.objects(deck, has_deck_entry))
        if not deck_entries:
            continue
        total = sum(
            int(str(count))
            for e in deck_entries
            for count in idx.combined.objects(e, quantity)
        )
        if total != COMMANDER_DECK_SIZE:
            errors.append(
                f"card-entries: Commander deck {deck} totals {total}, "
                f"expected {COMMANDER_DECK_SIZE}",
            )
    return errors


def _not_json(text: str) -> bool:
    """Return True if the text does not parse as JSON (helper, see PERF203)."""
    try:
        json.loads(text)
    except ValueError:
        return True
    return False


def check_behavior_hooks(idx: GraphIndex) -> tuple[list[str], int]:
    """Check 5 (behavior-hooks): hooks are well-formed and on cards only.

    Every :hasBehaviorHook subject must be a Card individual; every hook
    carries exactly one whitelisted :behaviorKey and one JSON-parseable
    :behaviorValue; :threatWeight only appears on Card individuals.
    Returns the errors plus the hook count.
    """
    errors: list[str] = []
    has_hook = mc_ref("hasBehaviorHook")
    behavior_key = mc_ref("behaviorKey")
    behavior_value = mc_ref("behaviorValue")
    n_hooks = 0
    for subj, hook in idx.combined.subject_objects(has_hook):
        n_hooks += 1
        if not idx.is_card(subj):
            errors.append(f"behavior-hooks: {subj} has hooks but is not a Card")
        keys = list(idx.combined.objects(hook, behavior_key))
        if len(keys) != 1:
            errors.append(f"behavior-hooks: hook on {subj} has {len(keys)} keys")
        errors.extend(
            f"behavior-hooks: {subj} uses unknown key {key!r}"
            for key in keys
            if str(key) not in BEHAVIOR_KEYS
        )
        values = list(idx.combined.objects(hook, behavior_value))
        if len(values) != 1:
            errors.append(f"behavior-hooks: hook on {subj} has {len(values)} values")
        errors.extend(
            f"behavior-hooks: {subj} value is not JSON: {value!r}"
            for value in values
            if _not_json(str(value))
        )
    errors.extend(
        f"behavior-hooks: :threatWeight on non-card {subj}"
        for subj in idx.combined.subjects(mc_ref("threatWeight"), None)
        if not idx.is_card(subj)
    )
    return errors, n_hooks


def check_synergy_domain(idx: GraphIndex) -> list[str]:
    """Check 4 (synergy-domain): synergy properties only connect cards."""
    errors: list[str] = []
    for prop in sorted(SYNERGY_PROPS):
        for s, o in idx.combined.subject_objects(prop):
            errors.extend(
                f"synergy-domain: {role} {node} of {prop} is not a Card"
                for node, role in ((s, "subject"), (o, "object"))
                if isinstance(node, URIRef) and not idx.is_card(node)
            )
    return errors


@dataclass
class CheckStats:
    """Counts reported in the summary line."""

    n_props: int = 0
    n_classes: int = 0
    n_entries: int = 0
    n_coll_entries: int = 0
    n_hooks: int = 0


def report(stats: CheckStats, errors: list[str]) -> int:
    """Print the summary and FAIL lines; return the process exit code."""
    # T201+RUF100 (below): this validator's program output, consumed by
    print(  # noqa: T201
        f"Checked {stats.n_props} properties, {stats.n_classes} classes, "
        f"{stats.n_entries} card entries ({stats.n_coll_entries} collection, "
        f"{stats.n_entries - stats.n_coll_entries} deck), "
        f"{stats.n_hooks} behavior hooks.",
    )
    if errors:
        unique = sorted(set(errors))
        print(f"\n{len(unique)} error(s):", file=sys.stderr)  # noqa: T201
        for err in unique:
            print(f"FAIL {err}", file=sys.stderr)  # noqa: T201
        return 1
    print("All consistency checks passed.")  # noqa: T201
    return 0


def main() -> int:
    """Run every consistency check over the combined graph and report."""
    idx = index_graphs()
    print(  # noqa: T201 - validator progress line
        f"Ontology: {len(idx.ontology)} triples; "
        f"combined graph: {len(idx.combined)} triples.",
    )

    stats = CheckStats()
    errors, stats.n_props, stats.n_classes = check_undefined_terms(idx)
    errors.extend(check_dangling_refs(idx))
    entry_errors, stats.n_entries = check_entry_shape(idx)
    errors.extend(entry_errors)
    coll_errors, stats.n_coll_entries = check_collection_entries(idx)
    errors.extend(coll_errors)
    errors.extend(check_commander_totals(idx))
    hook_errors, stats.n_hooks = check_behavior_hooks(idx)
    errors.extend(hook_errors)
    errors.extend(check_synergy_domain(idx))
    return report(stats, errors)


if __name__ == "__main__":
    sys.exit(main())
