"""Cross-file consistency checks over the combined knowledge graph.

Loads every TTL file under the graph roots into one graph and checks:

  1. undefined-terms - every mc: property / class used in instance data
                       is declared in MagicCardsOntology.ttl.
  2. dangling-refs   - every mc: individual used as subject or object of
                       an mc: property is typed (rdf:type) somewhere.
  3. card-entries    - every DeckEntry / CollectionEntry references an
                       existing card individual and carries a positive
                       :quantity; CollectionEntries carry finish +
                       condition; each Commander deck's entries total 100
                       cards (CR 903.5a); the collection's entry
                       quantities match collection.csv when it is present.
  4. synergy-domain  - synergy properties only connect card individuals.
  5. behavior-hooks  - every :hasBehaviorHook subject is a Card
                       individual, every hook carries exactly one
                       whitelisted :behaviorKey (mtgcards.behaviors.
                       BEHAVIOR_KEYS) and one JSON-parseable
                       :behaviorValue; :threatWeight is only asserted on
                       Card individuals.

Checks 2, 3 and 5 are what make this useful downstream: they catch a deck
graph that references a card the knowledge graph no longer defines, that
miscounts to 99 or 101, or that uses a behavior key the engine does not
implement.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rdflib import RDF, RDFS, Graph, Literal, URIRef
from rdflib.namespace import OWL

from mtgcards.behaviors import BEHAVIOR_KEYS

if TYPE_CHECKING:
    from rdflib.term import Node

    from mtgvalidate.context import ValidationContext

#: CR 903.5a: a Commander deck contains exactly 100 cards.
COMMANDER_DECK_SIZE = 100

#: file declaring the TBox; required for the undefined-terms check
ONTOLOGY_FILE = "MagicCardsOntology.ttl"

#: local names of the synergy properties (check 4)
SYNERGY_PROP_NAMES = (
    "hasSynergyWith",
    "amplifies",
    "isAmplifiedBy",
    "enables",
    "isEnabledBy",
)


def load(ctx: ValidationContext) -> tuple[Graph, Graph]:
    """Parse the ontology alone and every TTL under the roots into one graph.

    Raises:
        FileNotFoundError: no MagicCardsOntology.ttl under the roots.

    """
    ontology_path = ctx.find(ONTOLOGY_FILE)
    if ontology_path is None:
        roots = ", ".join(str(r) for r in ctx.roots)
        msg = (
            f"{ONTOLOGY_FILE} not found under the graph roots ({roots}); "
            f"add a graph bundle root or a magic-cards checkout"
        )
        raise FileNotFoundError(msg)
    ontology = Graph()
    ontology.parse(ontology_path, format="turtle")

    combined = Graph()
    for path in ctx.ttl_files():
        combined.parse(path, format="turtle")
    return ontology, combined


@dataclass
class GraphIndex:
    """The combined graph plus the derived lookup sets."""

    ctx: ValidationContext
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

    def ref(self, local_name: str) -> URIRef:
        """Return the URIRef of *local_name* in the ontology namespace."""
        return self.ctx.ref(local_name)

    def in_ns(self, node: Node) -> bool:
        """Return True if *node* is an IRI in the ontology namespace."""
        return str(node).startswith(self.ctx.ontology_iri)


def index_graphs(ctx: ValidationContext) -> GraphIndex:
    """Load the graphs and precompute the sets shared by all checks."""
    ontology, combined = load(ctx)
    declared_props = set(ontology.subjects(RDF.type, OWL.ObjectProperty))
    declared_props |= set(ontology.subjects(RDF.type, OWL.DatatypeProperty))
    declared_props |= set(ontology.subjects(RDF.type, OWL.AnnotationProperty))
    declared_classes = set(ontology.subjects(RDF.type, OWL.Class))
    typed = set(combined.subjects(RDF.type, None))
    card_cls = ctx.ref("Card")
    card_classes: set[Node] = {card_cls} | {
        # transitive_subjects is typed Node | None (it echoes its object
        # argument, which is optional); card_cls is never None here.
        s
        for s in combined.transitive_subjects(RDFS.subClassOf, card_cls)
        if s is not None
    }
    return GraphIndex(
        ctx=ctx,
        ontology=ontology,
        combined=combined,
        declared_props=declared_props,
        declared_classes=declared_classes,
        typed=typed,
        card_classes=card_classes,
    )


def check_undefined_terms(idx: GraphIndex) -> tuple[list[str], int, int]:
    """Check 1 (undefined-terms): every used mc: term must be declared.

    Returns the errors plus the used property / class counts for the
    summary line.
    """
    used_props = {p for p in idx.combined.predicates(None, None) if idx.in_ns(p)}
    errors = [
        f"undefined-terms: property used but not declared: {prop}"
        for prop in sorted(used_props, key=str)
        if prop not in idx.declared_props
    ]
    used_classes = {
        c
        for c in idx.combined.objects(None, RDF.type)
        if isinstance(c, URIRef) and idx.in_ns(c)
    }
    errors.extend(
        f"undefined-terms: class used but not declared: {cls}"
        for cls in sorted(used_classes, key=str)
        if cls not in idx.declared_classes
    )
    return errors, len(used_props), len(used_classes)


def check_dangling_refs(idx: GraphIndex) -> list[str]:
    """Check 2 (dangling-refs): every referenced mc: individual is typed."""
    errors: list[str] = []
    for s, p, o in idx.combined:
        if not idx.in_ns(p):
            continue
        errors.extend(
            f"dangling-refs: {role} {node} of {p} has no rdf:type anywhere"
            for node, role in ((s, "subject"), (o, "object"))
            if (
                isinstance(node, URIRef)
                and idx.in_ns(node)
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
    card_entry_cls = idx.ref("CardEntry")
    entry_card = idx.ref("entryCard")
    quantity = idx.ref("quantity")

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
    """Check 3b (card-entries): collection entries are well-formed variants.

    Every CollectionEntry must carry :hasFinish and :hasCondition, and no
    two entries may describe the same (card, finish, condition) variant -
    such a pair is a split stack that belongs in one entry with the
    summed quantity. Both hold for any graph root, so they also run
    downstream against a published bundle.

    Where collection.csv is present the entries are additionally mirrored
    against it: one entry per distinct variant, and quantities summing to
    the CSV counts. The CSV is a local, untracked input; when it is absent
    (CI checkouts, and any downstream consumer) that half is skipped with
    a notice. Returns the errors plus the entry count.
    """
    errors: list[str] = []
    quantity = idx.ref("quantity")
    has_finish = idx.ref("hasFinish")
    has_condition = idx.ref("hasCondition")
    entry_card = idx.ref("entryCard")
    coll_entries = set(idx.combined.subjects(RDF.type, idx.ref("CollectionEntry")))
    coll_total = 0
    seen: dict[tuple[str, str, str], str] = {}
    for entry in sorted(coll_entries, key=str):
        errors.extend(
            f"card-entries: {entry} has no {prop}"
            for prop in (has_finish, has_condition)
            if not list(idx.combined.objects(entry, prop))
        )
        for count in idx.combined.objects(entry, quantity):
            coll_total += int(str(count))
        variant = (
            str(next(iter(idx.combined.objects(entry, entry_card)), "")),
            str(next(iter(idx.combined.objects(entry, has_finish)), "")),
            str(next(iter(idx.combined.objects(entry, has_condition)), "")),
        )
        if all(variant) and variant in seen:
            errors.append(
                f"card-entries: {entry} duplicates the variant of "
                f"{seen[variant]} (same card, finish and condition) - merge "
                f"them into one entry with the summed quantity",
            )
        elif all(variant):
            seen[variant] = str(entry)

    csv_path = idx.ctx.find("collection.csv")
    if csv_path is None:
        # collection.csv is a local, untracked input (not distributed);
        # the CSV mirror check only runs where the inventory is present.
        print(  # noqa: T201 - validator progress line
            "collection.csv not found - skipping the CSV mirror check "
            "(entry shape checks still ran).",
        )
    else:
        with csv_path.open(encoding="utf-8", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        csv_total = sum(int(r["Count"]) for r in csv_rows)
        # entries are grouped per variant, so several acquisition rows of
        # the same printing/finish/condition collapse into a single entry
        csv_variants = {
            (
                r["Edition"].upper(),
                r["Collector Number"],
                r["Foil"].strip(),
                r["Condition"].strip(),
            )
            for r in csv_rows
        }
        if coll_entries and len(coll_entries) != len(csv_variants):
            errors.append(
                f"card-entries: {len(coll_entries)} collection entries but "
                f"{len(csv_variants)} distinct printing/finish/condition "
                f"variants in collection.csv",
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
    quantity = idx.ref("quantity")
    has_deck_entry = idx.ref("hasDeckEntry")
    decks = set(idx.combined.subjects(RDF.type, idx.ref("CommanderDeck")))
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

    Returns the errors plus the hook count.
    """
    errors: list[str] = []
    has_hook = idx.ref("hasBehaviorHook")
    behavior_key = idx.ref("behaviorKey")
    behavior_value = idx.ref("behaviorValue")
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
        for subj in idx.combined.subjects(idx.ref("threatWeight"), None)
        if not idx.is_card(subj)
    )
    return errors, n_hooks


def check_synergy_domain(idx: GraphIndex) -> list[str]:
    """Check 4 (synergy-domain): synergy properties only connect cards."""
    errors: list[str] = []
    for prop in sorted(idx.ref(name) for name in SYNERGY_PROP_NAMES):
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


def run(ctx: ValidationContext) -> list[str]:
    """Run every consistency check over the combined graph."""
    idx = index_graphs(ctx)
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

    print(  # noqa: T201 - validator summary line
        f"Checked {stats.n_props} properties, {stats.n_classes} classes, "
        f"{stats.n_entries} card entries ({stats.n_coll_entries} collection, "
        f"{stats.n_entries - stats.n_coll_entries} deck), "
        f"{stats.n_hooks} behavior hooks.",
    )
    return errors
