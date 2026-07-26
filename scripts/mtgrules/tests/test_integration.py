"""Full-game integration: the two repo decks, invariant checks."""

import random
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from mtgrules.adapter import MatchOptions, run_game, setup_game
from mtgrules.objects import GameObject
from mtgrules.turns import TurnRunner

if TYPE_CHECKING:
    from mtgcards.database import CardDatabase
    from mtgcards.deck import Deck

REPO = Path(__file__).resolve().parent.parent.parent.parent

#: Commander deck size: 99 cards plus the commander (rule 903.5a)
_DECK_TOTAL = 100


def _decks_db() -> "tuple[list[Deck], CardDatabase]":
    """Load the two repo decks and the card database."""
    # Deferred: loading the knowledge graph is expensive and only the
    # integration tests need it at import time. RUF100 is listed because
    from mtgcards.database import CardDatabase  # noqa: PLC0415
    from mtgcards.deck import load_deck  # noqa: PLC0415

    db = CardDatabase(REPO)
    decks = [
        load_deck(REPO / "strategies" / "station-swarm-counter-deck.txt"),
        load_deck(REPO / "strategies" / "blight-curse-deck.txt"),
    ]
    return decks, db


class TestIntegration(unittest.TestCase):
    """Whole-game runs over the real deck pool."""

    decks: "ClassVar[list[Deck]]"
    db: "ClassVar[CardDatabase]"

    @classmethod
    def setUpClass(cls) -> None:
        """Load decks and the card database once for all tests."""
        cls.decks, cls.db = _decks_db()

    def test_games_complete(self) -> None:
        """Games over several seeds finish within the turn cap."""
        for seed in (1, 2, 3):
            result = run_game(
                self.decks,
                self.db,
                random.Random(seed),
                MatchOptions(turn_cap=40),
            )
            self.assertLessEqual(result["turns"], 40)

    def test_determinism(self) -> None:
        """The same seed reproduces the exact same game record."""
        a = run_game(self.decks, self.db, random.Random(11))
        b = run_game(self.decks, self.db, random.Random(11))
        self.assertEqual(a, b)

    def test_record_shape(self) -> None:
        """The record must satisfy the mtgcards.stats.Aggregator contract."""
        rec = run_game(
            self.decks,
            self.db,
            random.Random(3),
            MatchOptions(turn_cap=40),
        )
        self.assertIn("winner", rec)
        self.assertIn("turns", rec)
        self.assertIn("reason", rec)
        for pdata in rec["players"].values():
            self.assertIn("life", pdata)
            self.assertIn("mulligans", pdata)
            self.assertIsInstance(pdata["cards_cast"], list)

    def test_card_conservation(self) -> None:
        """Rule 400: every nontoken card stays in exactly one zone.

        Each player owns exactly their 99 + commander at all times.
        """
        rng = random.Random(5)
        game = setup_game(self.decks, self.db, rng)
        runner = TurnRunner(game)
        for _ in range(12):
            if game.game_over:
                break
            runner.take_turn()
            game.active_idx = (game.active_idx + 1) % len(game.players)
            for p in game.players:
                cards = [
                    c
                    for zone in (p.library, p.hand, p.graveyard, p.exile, p.command)
                    for c in zone
                    if not c.is_token
                ]
                cards += [
                    c
                    for q in game.players
                    for c in q.battlefield
                    if not c.is_token and c.owner is p
                ]
                cards += [
                    i.obj
                    for i in game.stack
                    if i.is_spell
                    and isinstance(i.obj, GameObject)
                    and i.obj.owner is p
                    and not i.obj.is_token
                ]
                self.assertEqual(
                    len(cards),
                    _DECK_TOTAL,
                    f"{p.name}: {len(cards)} cards accounted for",
                )
                self.assertEqual(len(cards), len({c.id for c in cards}))

    def test_no_negative_resources(self) -> None:
        """Mana pools and counters never go negative."""
        rng = random.Random(9)
        game = setup_game(self.decks, self.db, rng)
        runner = TurnRunner(game)
        for _ in range(10):
            if game.game_over:
                break
            runner.take_turn()
            game.active_idx = (game.active_idx + 1) % len(game.players)
            for p in game.players:
                self.assertGreaterEqual(p.mana_pool.total(), 0)
                for o in p.battlefield:
                    for kind, n in o.counters.items():
                        self.assertGreaterEqual(n, 0, (o, kind))


if __name__ == "__main__":
    unittest.main()
