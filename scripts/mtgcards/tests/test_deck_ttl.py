"""Deck instance-graph (.ttl) parsing and load_deck dispatch."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mtgcards.deck import load_deck
from mtgcards.deck_ttl import load_deck_ttl

REPO = Path(__file__).resolve().parent.parent.parent.parent

_IND2NAME = {
    "AuntieOolCursewretch": "Auntie Ool, Cursewretch",
    "SolRingAetherdriftCommander57": "Sol Ring",
    "SwampDoctorWhoCommander200": "Swamp",
    "ArcaneSignetAetherdriftCommander52": "Arcane Signet",
}

_DECK_WITH_ENTRIES = """\
@prefix : <urn:test#> .

:TestDeck rdf:type :CommanderDeck ;
    :hasCard :AuntieOolCursewretch , :SolRingAetherdriftCommander57 ,
        :SwampDoctorWhoCommander200 ;
    :hasDeckEntry :E1 , :E2 , :E3 .

:AuntieOolCursewretch :isCommanderOf :TestDeck .

:E1 rdf:type :DeckEntry ;
    :entryCard :AuntieOolCursewretch ;
    :quantity "1"^^xsd:positiveInteger .

:E2 rdf:type :DeckEntry ;
    :entryCard :SolRingAetherdriftCommander57 ;
    :quantity "1"^^xsd:positiveInteger .

:E3 rdf:type :DeckEntry ;
    :entryCard :SwampDoctorWhoCommander200 ;
    :quantity "3"^^xsd:positiveInteger .
"""

_DECK_HASCARD_ONLY = """\
:TestDeck rdf:type :CommanderDeck ;
    :hasCard
  :SolRingAetherdriftCommander57 ,
  :SwampDoctorWhoCommander200 ;
    rdfs:label "x" .

:SolRingAetherdriftCommander57 :isCommanderOf :TestDeck .
"""


def _write(text: str) -> Path:
    """Write *text* to a temp .ttl file and return its path."""
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".ttl",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(text)
        return Path(f.name)


class TestDeckTTL(unittest.TestCase):
    """load_deck_ttl on synthetic deck graphs."""

    def test_entries_quantities_and_commander(self) -> None:
        """Reified DeckEntry counts are expanded; commander leaves the 99."""
        path = _write(_DECK_WITH_ENTRIES)
        self.addCleanup(path.unlink)
        deck = load_deck_ttl(path, _IND2NAME)
        self.assertEqual(deck.commander, "Auntie Ool, Cursewretch")
        self.assertEqual(deck.fmt, "ttl")
        self.assertEqual(sorted(set(deck.cards)), ["Sol Ring", "Swamp"])
        self.assertEqual(deck.cards.count("Swamp"), 3)
        self.assertEqual(deck.cards.count("Auntie Ool, Cursewretch"), 0)
        self.assertEqual(deck.size, 5)

    def test_hascard_fallback(self) -> None:
        """Without DeckEntry individuals the :hasCard list counts one each."""
        path = _write(_DECK_HASCARD_ONLY)
        self.addCleanup(path.unlink)
        deck = load_deck_ttl(path, _IND2NAME)
        self.assertEqual(deck.commander, "Sol Ring")
        self.assertEqual(deck.cards, ["Swamp"])

    def test_unresolved_individual_raises(self) -> None:
        """Referencing an individual missing from the graph is an error."""
        broken = _DECK_WITH_ENTRIES.replace(
            "SolRingAetherdriftCommander57",
            "NoSuchCardXyz",
        )
        path = _write(broken)
        self.addCleanup(path.unlink)
        with self.assertRaises(ValueError) as ctx:
            load_deck_ttl(path, _IND2NAME)
        self.assertIn("NoSuchCardXyz", str(ctx.exception))


class TestLoadDeckDispatch(unittest.TestCase):
    """load_deck routes by file suffix."""

    def test_ttl_requires_ind2name(self) -> None:
        """A .ttl deck without the graph map is a clear error."""
        path = _write(_DECK_WITH_ENTRIES)
        self.addCleanup(path.unlink)
        with self.assertRaises(ValueError):
            load_deck(path)

    def test_ttl_dispatch(self) -> None:
        """load_deck parses .ttl files through the deck-graph loader."""
        path = _write(_DECK_WITH_ENTRIES)
        self.addCleanup(path.unlink)
        deck = load_deck(path, _IND2NAME)
        self.assertEqual(deck.fmt, "ttl")
        self.assertEqual(deck.commander, "Auntie Ool, Cursewretch")

    def test_txt_unaffected(self) -> None:
        """Plain txt decklists keep the existing parser and fmt tag."""
        deck = load_deck(REPO / "strategies" / "blight-curse-deck.txt")
        self.assertEqual(deck.fmt, "txt")
        self.assertIsNotNone(deck.commander)
        self.assertGreater(deck.size, 0)


class TestRepoDeckGraphs(unittest.TestCase):
    """The checked-in decks/*.ttl graphs parse against the real graph."""

    def test_blight_curse_deck(self) -> None:
        """decks/BlightCurseTest.ttl resolves fully to a 100-card deck."""
        # Deferred: keep the expensive graph load out of module import.
        from mtgcards.database import CardDatabase  # noqa: PLC0415

        db = CardDatabase(REPO)
        deck = load_deck(REPO / "decks" / "BlightCurseTest.ttl", db.ind2name)
        self.assertEqual(deck.commander, "Auntie Ool, Cursewretch")
        self.assertEqual(deck.size, 100)
        # every resolved name is a graph card, not a stub
        for name in deck.cards:
            self.assertEqual(db.get(name).source, "graph", name)


if __name__ == "__main__":
    unittest.main()
