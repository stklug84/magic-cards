"""Deck instance-graph (.ttl) parsing.

Deck instance graphs (.ttl) reference card individuals; they do not embed
card characteristics. Contents are read from the reified deck entries
(:hasDeckEntry -> :entryCard + :quantity) with the plain :hasCard list as
fallback (one copy each), and the commander from the :isCommanderOf
assertion. Individual local names are resolved to card names through the
{individual: card name} map built from the knowledge graph by
ttl_loader.load_graph_cards (exposed as CardDatabase.ind2name).

Regex-based extraction, matching the house rule that the simulator path
has no rdflib dependency.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from mtgcards.deck import Deck

if TYPE_CHECKING:
    from collections.abc import Mapping

_COMMANDER_RE = re.compile(r":(\w+)\s+:isCommanderOf\b")
_ENTRY_RE = re.compile(r':entryCard\s+:(\w+)\s*;\s*:quantity\s+"(\d+)"')
# the :hasCard object list, terminated by the next ';' or the final '.'
_HASCARD_RE = re.compile(r":hasCard\s+(.*?)[;.]", re.DOTALL)
_IND_RE = re.compile(r":(\w+)")
#: how many unresolved individuals the error message lists before eliding
_UNRESOLVED_PREVIEW = 8


def _entries(text: str) -> list[tuple[str, int]]:
    """Extract (individual, copy count) pairs from a deck graph."""
    entries = [(ind, int(qty)) for ind, qty in _ENTRY_RE.findall(text)]
    if entries:
        return entries
    # no reified DeckEntry individuals: fall back to the :hasCard list
    m = _HASCARD_RE.search(text)
    if m:
        entries = [(ind, 1) for ind in _IND_RE.findall(m.group(1))]
    return entries


def load_deck_ttl(path: str | Path, ind2name: Mapping[str, str]) -> Deck:
    """Parse the deck instance graph at *path* into a Deck.

    Raises ValueError if the graph references card individuals that are
    not defined in the knowledge graph (*ind2name*).
    """
    deck_path = Path(path)
    text = deck_path.read_text(encoding="utf-8")
    entries = _entries(text)
    cm = _COMMANDER_RE.search(text)
    commander_ind = cm.group(1) if cm else None
    referenced = {ind for ind, _ in entries}
    if commander_ind:
        referenced.add(commander_ind)
    unresolved = sorted(ind for ind in referenced if ind not in ind2name)
    if unresolved:
        msg = (
            f"{deck_path}: {len(unresolved)} card individual(s) not found "
            f"in the knowledge graph: "
            f"{', '.join(unresolved[:_UNRESOLVED_PREVIEW])}"
            f"{' ...' if len(unresolved) > _UNRESOLVED_PREVIEW else ''}"
        )
        raise ValueError(msg)
    deck = Deck(name=deck_path.stem, path=str(deck_path), fmt="ttl")
    deck.commander = ind2name[commander_ind] if commander_ind else None
    commander_taken = False
    for ind, qty in entries:
        count = qty
        if not commander_taken and ind == commander_ind:
            # the commander is listed among the deck entries; the Deck
            # model keeps it out of the 99 (deck.cards)
            count -= 1
            commander_taken = True
        deck.cards.extend([ind2name[ind]] * count)
    return deck
