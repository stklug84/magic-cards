"""Deck file parsing (txt decklists and .ttl deck instance graphs).

Files with a '.ttl' suffix are parsed as deck instance graphs (see
deck_ttl.load_deck_ttl); everything else is parsed as a plain decklist.

Decklist format (one card per line, // comments, commander marked by a
'// Commander' section header before its line):

    1 Sol Ring
    2 Island
    // Commander
    1 Some Legendary Creature

The count may carry the 'x' of Moxfield/Archidekt exports ('1x Sol Ring').
If the list has no '// Commander' section, the first card of the list is
used as the commander (the convention of exported decklists). Everything
after a '#' character is stripped (inline annotations, e.g. the
knowledge-graph individual), as is a trailing '(SET) collector-number
[*F*]' printing suffix (set code in either case):

    1 Sol Ring (C18) 222    # :SolRingAetherdriftCommander57

A line that is neither blank, a comment, a '//' header nor 'COUNT Card
Name' is not dropped silently: it is recorded in ``Deck.skipped`` as
``(line number, text)`` so callers can warn about it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# Trailing printing suffix of exported lists, for example "(EOE) 219",
# "(PLST) SOM-144", "(LCI) 26 *F*" or, lowercased by some exporters,
# "(lci) 26".
_PRINTING_RE = re.compile(r"\s+\([A-Za-z0-9]{2,6}\)\s+[A-Za-z0-9-]+\S*(\s+\*\w+\*)?$")
# "1", "1x", "4X"
_COUNT_RE = re.compile(r"^(\d+)[xX]?$")


@dataclass
class Deck:
    """A parsed decklist: expanded card names plus the commander."""

    name: str
    path: str
    cards: list[str] = field(default_factory=list)  # card names, expanded
    commander: str | None = None
    fmt: str = "txt"  # source file format: txt|ttl
    #: txt lines the parser could not read: (1-based line number, text)
    skipped: list[tuple[int, str]] = field(default_factory=list)

    @property
    def size(self) -> int:
        """Total card count including the commander."""
        return len(self.cards) + (1 if self.commander else 0)


def load_deck(
    path: str | Path,
    ind2name: Mapping[str, str] | None = None,
) -> Deck:
    """Parse the deck file at *path* into a Deck.

    '.ttl' files are deck instance graphs and require *ind2name*, the
    {individual local name: card name} map from the knowledge graph
    (CardDatabase.ind2name); everything else is a txt decklist.
    """
    deck_path = Path(path)
    if deck_path.suffix.lower() == ".ttl":
        # Deferred: deck_ttl imports Deck from this module, so a
        # top-level import here would be a real package cycle.
        from mtgcards.deck_ttl import load_deck_ttl  # noqa: PLC0415

        if ind2name is None:
            msg = f"{deck_path}: .ttl decks require the knowledge graph"
            raise ValueError(msg)
        return load_deck_ttl(deck_path, ind2name)
    deck = Deck(name=deck_path.stem, path=str(deck_path))
    in_commander = False
    for lineno, raw in enumerate(deck_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("//"):
            # a section header like '// Commander' (exact word, not e.g.
            # '// Ramp & fixing (WUBRG commander)')
            header = line.lstrip("/ ").strip().lower()
            in_commander = header in ("commander", "commanders")
            continue
        count_str, _, rest = line.partition(" ")
        count_match = _COUNT_RE.match(count_str)
        if count_match is None or not rest.strip():
            deck.skipped.append((lineno, line))
            continue
        count, name = int(count_match.group(1)), _PRINTING_RE.sub("", rest.strip())
        # exported lists write double-faced cards as 'Front / Back'; the
        # card database uses the canonical 'Front // Back' form
        name = name.replace(" / ", " // ")
        if in_commander and deck.commander is None:
            deck.commander = name
            count -= 1
        deck.cards.extend([name] * count)
    if deck.commander is None and deck.cards:
        # no '// Commander' section: promote the first card of the list
        deck.commander = deck.cards.pop(0)
    return deck
