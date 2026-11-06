"""Canonical dump of the card database (parity level L2).

Loads a graph root through :class:`mtgcards.database.CardDatabase` exactly
as the simulator does and writes every index entry as one JSON line, in
index order, with Python sets as sorted lists and tuples as lists. The
Rust ``mtgcards`` crate has the same dump
(``cargo run -p mtgcards --example dump_cards``); ``cargo xtask parity``
runs both and diffs them line by line.

Line 1 is a summary (``names``, ``records``, ``ind2name``, ``stubbed``);
every further line is ``{"key": <index name>, "record": <ordinal of the
shared record>, "card": {...}}``. The record ordinal numbers distinct
objects in order of first appearance, so an alias (the front face of a
double-faced card) shares its ordinal with the full name — the aliasing
is part of the contract.

Usage (from ``scripts/``)::

    python3 -m parity.dump_cards --root .. [--custom-cards F] [--extra-graph G ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mtgcards.database import CardDatabase

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mtgcards.cards import CardData


def canonical(value: object) -> object:
    """Turn sets into sorted lists and tuples into lists; dicts keep order."""
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in value.items()}
    return value


def card_record(card: CardData) -> dict[str, Any]:
    """Return the dataclass fields of *card* in declaration order."""
    return {
        "name": card.name,
        "mana_cost": card.mana_cost,
        "mv": card.mv,
        "types": canonical(card.types),
        "supertypes": canonical(card.supertypes),
        "subtypes": canonical(card.subtypes),
        "loyalty": card.loyalty,
        "color_identity": canonical(card.color_identity),
        "power": card.power,
        "toughness": card.toughness,
        "oracle": card.oracle,
        "keywords": canonical(card.keywords),
        "behavior": canonical(card.behavior),
        "source": card.source,
    }


def dump_lines(db: CardDatabase) -> Iterator[str]:
    """Yield the summary line, then one line per index entry."""
    ordinals: dict[int, int] = {}
    for card in db.index.values():
        ordinals.setdefault(id(card), len(ordinals))
    summary = {
        "names": len(db.index),
        "records": len(ordinals),
        "ind2name": dict(db.ind2name),
        "stubbed": list(db.stubbed),
    }
    yield json.dumps({"summary": summary})
    for key, card in db.index.items():
        record = {"key": key, "record": ordinals[id(card)], "card": card_record(card)}
        yield json.dumps(record)


def main(argv: list[str] | None = None) -> int:
    """Write the dump to stdout (or ``--out``); 0 on success."""
    parser = argparse.ArgumentParser(
        description="Canonical dump of the card database (parity level L2).",
    )
    parser.add_argument(
        "--root",
        default="..",
        help="graph root (default: the checkout)",
    )
    parser.add_argument(
        "--custom-cards",
        help="a --custom-cards JSON file to layer on top",
    )
    parser.add_argument(
        "--extra-graph",
        action="append",
        default=[],
        help="an extra card graph (repeatable)",
    )
    parser.add_argument("--out", help="write here instead of stdout")
    args = parser.parse_args(argv)
    extra_graphs = [Path(p) for p in args.extra_graph]
    db = CardDatabase(Path(args.root), args.custom_cards, extra_graphs)
    text = "\n".join(dump_lines(db)) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
