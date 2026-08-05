"""CLI deck loading and Scryfall resolution options."""

from __future__ import annotations

import unittest
from pathlib import Path

from mtgcards.deck import Deck
from mtgrules.cli import _resolve_txt_decks, build_parser

REPO = Path(__file__).resolve().parent.parent.parent.parent


class TestOfflineFlag(unittest.TestCase):
    """--offline skips the Scryfall resolution entirely."""

    def test_parser_accepts_offline(self) -> None:
        """The flag parses and defaults to False."""
        ap = build_parser()
        self.assertFalse(ap.parse_args(["a.txt", "b.txt"]).offline)
        self.assertTrue(ap.parse_args(["a.txt", "b.txt", "--offline"]).offline)

    def test_offline_skips_resolution(self) -> None:
        """With offline=True no lookup happens and the db is untouched."""
        # Deferred: keep the expensive graph load out of module import.
        from mtgcards.database import CardDatabase  # noqa: PLC0415

        db = CardDatabase(REPO)
        before = len(db.index)
        deck = Deck(
            name="t",
            path="t.txt",
            cards=["No Such Card Offline Xyz"],
            commander="Sol Ring",
        )
        _resolve_txt_decks([deck], db, offline=True)
        self.assertEqual(len(db.index), before)
        self.assertNotIn("No Such Card Offline Xyz", db.index)


if __name__ == "__main__":
    unittest.main()
