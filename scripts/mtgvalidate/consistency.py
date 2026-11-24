"""Cross-file consistency checks over the combined knowledge graph.

Loads every TTL file under the graph roots into one graph and checks:

  1. undefined-terms - every mc: property / class used in instance data
                       is declared in MagicCardsOntology.ttl.
  2. dangling-refs   - every mc: individual used as subject or object of
                       an mc: property is typed (rdf:type) somewhere.
  3. card-entries    - every DeckEntry / CollectionEntry references an
                       existing card individual and carries a positive
                       :quantity; the entries of a physical collection carry
                       finish + condition, those of a collection held on a
                       digital platform (:isHeldOn) neither, and hold only
                       printings available on that platform; no collection
                       lists a variant twice; each Commander deck's entries
                       total 100 cards (CR 903.5a); the paper collection's
                       entries match collection.csv and the MTG Arena
                       collection's match arena-collection.json where those
                       local inputs are present.
  4. synergy-domain  - synergy properties only connect card individuals.
  5. behavior-hooks  - every :hasBehaviorHook subject is a Card
                       individual, every hook carries exactly one
                       whitelisted :behaviorKey (mtgcards.behaviors.
                       BEHAVIOR_KEYS) and one JSON-parseable
                       :behaviorValue; :threatWeight is only asserted on
                       Card individuals.
  6. card-iris       - a card individual's IRI names nothing else: no
                       rdf:type besides the card classes, not a class or
                       property of the ontology (a card named like a set
                       or a keyword merges with it into one node otherwise,
                       and no reasoner objects).

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

#: the local inventory inputs the collection graphs are generated from (the
#: wheel ships without mtgarena, so the snapshot's name is restated here;
#: mtgarena/tests/test_contract.py keeps it equal to the tool's)
COLLECTION_CSV = "collection.csv"
ARENA_JSON = "arena-collection.json"

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


@dataclass(frozen=True)
class EntryPlace:
    """Where a collection entry belongs: its collection and that one's platform."""

    #: the Collection listing the entry (None: listed by none)
    collection: Node | None
    #: the platform the collection is held on (None: none stated, i.e. paper)
    platform: Node | None


def _entry_places(idx: GraphIndex) -> tuple[dict[Node, EntryPlace], list[str]]:
    """Map every listed collection entry to its collection and platform.

    A collection states the platform it is held on with :isHeldOn, at most
    once; one that states none is a physical collection. Returns the map
    plus the errors for collections with several platforms.
    """
    held_on = idx.ref("isHeldOn")
    has_entry = idx.ref("hasCollectionEntry")
    places: dict[Node, EntryPlace] = {}
    errors: list[str] = []
    for coll in sorted(idx.combined.subjects(RDF.type, idx.ref("Collection")), key=str):
        platforms = sorted(idx.combined.objects(coll, held_on), key=str)
        if len(platforms) > 1:
            errors.append(
                f"card-entries: collection {coll} is held on {len(platforms)} "
                f"platforms ({', '.join(map(str, platforms))})",
            )
        place = EntryPlace(coll, platforms[0] if platforms else None)
        for entry in idx.combined.objects(coll, has_entry):
            places[entry] = place
    return places, errors


def _is_digital(idx: GraphIndex, place: EntryPlace) -> bool:
    """Return True if the entry's collection is held on a digital platform."""
    return place.platform is not None and place.platform != idx.ref("PaperMagic")


def _shape_errors(idx: GraphIndex, entry: Node, place: EntryPlace) -> list[str]:
    """Return the finish, condition and platform errors of one entry.

    Physical copies carry a finish and a condition; digital copies have
    neither, and a digital collection holds only printings available on
    its platform.
    """
    attributes = (idx.ref("hasFinish"), idx.ref("hasCondition"))
    if not _is_digital(idx, place):
        return [
            f"card-entries: {entry} has no {prop}"
            for prop in attributes
            if not list(idx.combined.objects(entry, prop))
        ]
    errors = [
        f"card-entries: {entry} of {place.collection}, held on {place.platform}, "
        f"has {prop} - digital copies have neither finish nor condition"
        for prop in attributes
        if list(idx.combined.objects(entry, prop))
    ]
    available_on = idx.ref("isAvailableOn")
    errors.extend(
        f"card-entries: {entry} holds {card}, which is not available on "
        f"{place.platform}, the platform of {place.collection}"
        for card in idx.combined.objects(entry, idx.ref("entryCard"))
        if (card, available_on, place.platform) not in idx.combined
    )
    return errors


def _variant(idx: GraphIndex, entry: Node, place: EntryPlace) -> tuple[str, ...]:
    """Return the variant key of an entry: collection, card, finish, condition."""
    return (
        str(place.collection),
        str(next(iter(idx.combined.objects(entry, idx.ref("entryCard"))), "")),
        str(next(iter(idx.combined.objects(entry, idx.ref("hasFinish"))), "")),
        str(next(iter(idx.combined.objects(entry, idx.ref("hasCondition"))), "")),
    )


def _quantity(idx: GraphIndex, entries: set[Node]) -> int:
    """Return the summed :quantity of *entries*."""
    quantity = idx.ref("quantity")
    return sum(int(str(n)) for e in entries for n in idx.combined.objects(e, quantity))


def _csv_mirror(idx: GraphIndex, physical: set[Node]) -> list[str]:
    """Mirror the physical collection's entries against collection.csv.

    One entry per distinct printing/finish/condition variant (several
    acquisition rows of a variant collapse into one entry), and quantities
    summing to the CSV counts.
    """
    csv_path = idx.ctx.find(COLLECTION_CSV)
    if csv_path is None:
        # collection.csv is a local, untracked input (not distributed);
        # the CSV mirror check only runs where the inventory is present.
        print(  # noqa: T201 - validator progress line
            f"{COLLECTION_CSV} not found - skipping the CSV mirror check "
            "(entry shape checks still ran).",
        )
        return []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    csv_total = sum(int(r["Count"]) for r in csv_rows)
    csv_variants = {
        (
            r["Edition"].upper(),
            r["Collector Number"],
            r["Foil"].strip(),
            r["Condition"].strip(),
        )
        for r in csv_rows
    }
    errors: list[str] = []
    if physical and len(physical) != len(csv_variants):
        errors.append(
            f"card-entries: {len(physical)} physical collection entries but "
            f"{len(csv_variants)} distinct printing/finish/condition "
            f"variants in {COLLECTION_CSV}",
        )
    total = _quantity(idx, physical)
    if physical and total != csv_total:
        errors.append(
            f"card-entries: physical collection quantities sum to {total} but "
            f"{COLLECTION_CSV} counts sum to {csv_total}",
        )
    return errors


def _arena_mirror(idx: GraphIndex, held: set[Node]) -> list[str]:
    """Mirror the MTG Arena collection's entries against arena-collection.json.

    The snapshot holds the printings the generator turns into entries (as
    generate_individuals.arena_printings selects them): the non-basic
    printings matched to Scryfall, one entry per printing, with the copies
    summed.
    """
    json_path = idx.ctx.find(ARENA_JSON)
    if json_path is None:
        # the MTG Arena snapshot is a local, untracked input like the CSV
        print(  # noqa: T201 - validator progress line
            f"{ARENA_JSON} not found - skipping the MTG Arena mirror check "
            "(entry shape checks still ran).",
        )
        return []
    snapshot = json.loads(json_path.read_text(encoding="utf-8"))
    owned = [c for c in snapshot["cards"] if c["scryfall_id"] and not c["basic"]]
    printings = {(str(c["set"]).upper(), str(c["collector_number"])) for c in owned}
    copies = sum(int(c["quantity"]) for c in owned)
    errors: list[str] = []
    if len(held) != len(printings):
        errors.append(
            f"card-entries: {len(held)} MTG Arena collection entries but "
            f"{len(printings)} printings in {ARENA_JSON}",
        )
    total = _quantity(idx, held)
    if total != copies:
        errors.append(
            f"card-entries: MTG Arena collection quantities sum to {total} but "
            f"{ARENA_JSON} holds {copies} copies",
        )
    return errors


def check_collection_entries(idx: GraphIndex) -> tuple[list[str], int]:
    """Check 3b (card-entries): collection entries are well-formed variants.

    The entries of a physical collection carry :hasFinish and :hasCondition;
    those of a collection held on a digital platform (:isHeldOn :MTGArena)
    carry neither and hold only printings available on that platform. No
    collection lists the same variant twice - (card, finish, condition) in a
    physical collection, the card in a digital one - as such a pair is a
    split stack that belongs in one entry with the summed quantity. All of
    this holds for any graph root, so it also runs downstream against a
    published bundle.

    Where the local inventories are present the entries are additionally
    mirrored against them: the physical ones against collection.csv, the MTG
    Arena collection's against arena-collection.json. Both are untracked
    inputs; when one is absent (CI checkouts, and any downstream consumer)
    its half is skipped with a notice. Returns the errors plus the entry
    count.
    """
    places, errors = _entry_places(idx)
    coll_entries = set(idx.combined.subjects(RDF.type, idx.ref("CollectionEntry")))
    physical: set[Node] = set()
    on_arena: set[Node] = set()
    seen: dict[tuple[str, ...], str] = {}
    for entry in sorted(coll_entries, key=str):
        place = places.get(entry, EntryPlace(None, None))
        errors.extend(_shape_errors(idx, entry, place))
        if not _is_digital(idx, place):
            physical.add(entry)
        elif place.platform == idx.ref("MTGArena"):
            on_arena.add(entry)
        variant = _variant(idx, entry, place)
        if not variant[1]:
            continue  # no :entryCard - check 3a reports it
        if variant in seen:
            errors.append(
                f"card-entries: {entry} duplicates the variant of "
                f"{seen[variant]} (same collection, card, finish and "
                f"condition) - merge them into one entry with the summed "
                f"quantity",
            )
        else:
            seen[variant] = str(entry)
    errors.extend(_csv_mirror(idx, physical))
    errors.extend(_arena_mirror(idx, on_arena))
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


def check_card_iris(idx: GraphIndex) -> list[str]:
    """Check 6 (card-iris): a card individual's IRI names nothing else.

    A card named like a set or a keyword (Aether Revolt, Explore) must not
    take that individual's IRI: the two would merge into one node that is
    both, and no reasoner objects, since the classes are not disjoint. The
    generator suffixes such a card with its set and collector number.
    """
    cards = {
        s
        for cls in idx.card_classes
        for s in idx.combined.subjects(RDF.type, cls)
        if isinstance(s, URIRef)
    }
    errors: list[str] = []
    for card in sorted(cards, key=str):
        others = sorted(
            str(t).rsplit("#", 1)[-1]
            for t in idx.combined.objects(card, RDF.type)
            if t not in idx.card_classes and t != OWL.NamedIndividual
        )
        if card in idx.declared_classes or card in idx.declared_props:
            others.append("a term of the ontology")
        if others:
            errors.append(f"card-iris: {card} is a Card and also {', '.join(others)}")
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
    errors.extend(check_card_iris(idx))

    print(  # noqa: T201 - validator summary line
        f"Checked {stats.n_props} properties, {stats.n_classes} classes, "
        f"{stats.n_entries} card entries ({stats.n_coll_entries} collection, "
        f"{stats.n_entries - stats.n_coll_entries} deck), "
        f"{stats.n_hooks} behavior hooks.",
    )
    return errors
