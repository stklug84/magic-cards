#!/usr/bin/env python3
"""Extract generator inputs from the ontology and external card graphs.

Writes the two JSON extracts consumed by scripts/generate_individuals.py
generate:

  1. /tmp/onto_vocab.json     - controlled vocabulary from
                                MagicCardsOntology.ttl: individuals per
                                vocabulary class (SubType, KeywordAbility,
                                KeywordAction, Keyword, SuperType, CardType,
                                including subclass closure), rdfs:label per
                                individual (_labels) and the setCode ->
                                Set-individual map (_setcodes).
  2. /tmp/existing_cards.json - card individuals defined outside sets/*.ttl
                                (MagicCardsOntology.ttl and
                                MagicExternalCards.ttl), so the generator can
                                skip printings already modelled in the master
                                files and suffix colliding individual names.

Exit code 0 on success, 1 on any failure.

Usage:
  python3 scripts/build_vocab.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from rdflib import RDF, RDFS, Graph, URIRef

ROOT = Path(__file__).resolve().parent.parent
NS = "urn:stklug84:MagicCardsOntology:2026-02-27#"

VOCAB_CLASSES = (
    "SubType",
    "KeywordAbility",
    "KeywordAction",
    "Keyword",
    "SuperType",
    "CardType",
)
EXISTING_SOURCES = ("MagicCardsOntology.ttl", "MagicExternalCards.ttl")

VOCAB_OUT = Path("/tmp/onto_vocab.json")
EXISTING_OUT = Path("/tmp/existing_cards.json")


def local(term: object) -> str | None:
    """Return the local name of a term in the ontology namespace."""
    s = str(term)
    return s[len(NS) :] if s.startswith(NS) else None


def subclass_closure(graph: Graph, root: URIRef) -> set[URIRef]:
    """Return root plus all direct and transitive rdfs:subClassOf children."""
    children: dict[URIRef, set[URIRef]] = defaultdict(set)
    for sub, sup in graph.subject_objects(RDFS.subClassOf):
        if isinstance(sub, URIRef) and isinstance(sup, URIRef):
            children[sup].add(sub)
    seen: set[URIRef] = set()
    stack = [root]
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(children.get(cls, ()))
    return seen


def individuals_of(graph: Graph, class_name: str) -> list[str]:
    """Return sorted local names of all individuals typed by the class tree."""
    inds: set[str] = set()
    for cls in subclass_closure(graph, URIRef(NS + class_name)):
        for ind in graph.subjects(RDF.type, cls):
            name = local(ind)
            if name:
                inds.add(name)
    return sorted(inds)


def build_vocab(graph: Graph) -> dict:
    """Assemble the vocabulary extract for /tmp/onto_vocab.json."""
    vocab: dict = {cls: individuals_of(graph, cls) for cls in VOCAB_CLASSES}

    labels: dict[str, str] = {}
    for subj, label in graph.subject_objects(RDFS.label):
        name = local(subj)
        if name and name not in labels:
            labels[name] = str(label)
    vocab["_labels"] = labels

    setcodes: dict[str, str] = {}
    for subj, code in graph.subject_objects(URIRef(NS + "setCode")):
        name = local(subj)
        if name:
            setcodes[str(code)] = name
    vocab["_setcodes"] = setcodes
    return vocab


def first_local(graph: Graph, subj: URIRef, prop: str) -> str:
    """Return the local name of the first object of subj/prop, or ''."""
    return next(
        (local(o) or "" for o in graph.objects(subj, URIRef(NS + prop))),
        "",
    )


def first_str(graph: Graph, subj: URIRef, prop: str) -> str:
    """Return the string value of the first object of subj/prop, or ''."""
    return next(
        (str(o) for o in graph.objects(subj, URIRef(NS + prop))),
        "",
    )


def build_existing() -> list[dict[str, str]]:
    """Collect card individuals defined outside sets/*.ttl."""
    existing: list[dict[str, str]] = []
    for filename in EXISTING_SOURCES:
        graph = Graph()
        graph.parse(ROOT / filename, format="turtle")
        for card in sorted(
            graph.subjects(URIRef(NS + "cardName"), None),
            key=str,
        ):
            if not isinstance(card, URIRef):
                continue
            ind = local(card)
            if ind is None:
                continue
            existing.append(
                {
                    "ind": ind,
                    "name": first_str(graph, card, "cardName"),
                    "set": first_local(graph, card, "isInSet"),
                    "num": first_str(graph, card, "cardNumber")
                    or first_str(graph, card, "cardNumberString"),
                },
            )
    return existing


def main() -> int:
    """Write both extracts; return a process exit code."""
    graph = Graph()
    graph.parse(ROOT / "MagicCardsOntology.ttl", format="turtle")

    vocab = build_vocab(graph)
    VOCAB_OUT.write_text(json.dumps(vocab))
    counts = ", ".join(f"{cls}: {len(vocab[cls])}" for cls in VOCAB_CLASSES)
    print(f"{VOCAB_OUT}: {counts}")
    print(
        f"{VOCAB_OUT}: _labels: {len(vocab['_labels'])}, "
        f"_setcodes: {len(vocab['_setcodes'])}",
    )

    existing = build_existing()
    EXISTING_OUT.write_text(json.dumps(existing))
    print(f"{EXISTING_OUT}: {len(existing)} card individuals")

    if not vocab["_setcodes"] or not vocab["SubType"]:
        print("ERROR: vocabulary extract is empty", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
