"""Full-game integration: the two repo decks, invariant checks."""

import random
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from ..adapter import run_game, setup_game          # noqa: E402
from ..objects import Zone                          # noqa: E402
from ..turns import TurnRunner                      # noqa: E402


def _decks_db():
    from mtgsim.database import CardDatabase
    from mtgsim.deck import load_deck
    db = CardDatabase(REPO)
    decks = [load_deck(REPO / "strategies" / "ss-test.txt"),
             load_deck(REPO / "strategies" / "bb-test.txt")]
    return decks, db


class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.decks, cls.db = _decks_db()

    def test_games_complete(self):
        for seed in (1, 2, 3):
            result = run_game(self.decks, self.db, seed=seed, turn_cap=40)
            self.assertLessEqual(result["turns"], 40)

    def test_determinism(self):
        a = run_game(self.decks, self.db, seed=11)
        b = run_game(self.decks, self.db, seed=11)
        self.assertEqual(a, b)

    def test_card_conservation(self):
        """Rule 400: every nontoken card stays in exactly one zone; each
        player owns exactly their 99 + commander."""
        rng = random.Random(5)
        game = setup_game(self.decks, self.db, rng)
        runner = TurnRunner(game)
        for _ in range(12):
            if game.game_over:
                break
            runner.take_turn()
            game.active_idx = (game.active_idx + 1) % len(game.players)
            for p in game.players:
                cards = [c for zone in (p.library, p.hand, p.graveyard,
                                        p.exile, p.command)
                         for c in zone if not c.is_token]
                cards += [c for q in game.players for c in q.battlefield
                          if not c.is_token and c.owner is p]
                cards += [i.obj for i in game.stack
                          if i.is_spell and i.obj.owner is p
                          and not i.obj.is_token]
                self.assertEqual(
                    len(cards), 100,
                    f"{p.name}: {len(cards)} cards accounted for")
                self.assertEqual(len(cards), len({c.id for c in cards}))

    def test_no_negative_resources(self):
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
