"""Starting the game (CR 103): fixes F12 and F8.

F12: the starting player is chosen at random (103.1) instead of always
being seat 0; libraries are shuffled and mulligans taken in turn order
from that player (103.3, 103.5). F8: in a game of three or more players
the first mulligan is free (103.5c).
"""

from __future__ import annotations

import random
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

from mtgcards.deck import Deck
from mtgrules.adapter import MatchOptions, _mulligans, run_game, setup_game
from mtgrules.policy import DefaultPolicy

if TYPE_CHECKING:
    from mtgcards.database import CardDatabase
    from mtgrules.game import Game
    from mtgrules.objects import GameObject, Player

REPO = Path(__file__).resolve().parent.parent.parent.parent

#: an all-land library: the default policy always mulligans (> 5 lands)
#: until its max_mulligans (3), which makes the hand sizes predictable
_COMMANDER = "Pia Nalaar, Chief Mechanic"
_ALL_LANDS = ["Swamp"] * 40


def _decks(n: int) -> list[Deck]:
    return [
        Deck(
            name=f"Seat {i}",
            path=f"seat-{i}.txt",
            cards=list(_ALL_LANDS),
            commander=_COMMANDER,
        )
        for i in range(n)
    ]


class TestGameSetup(unittest.TestCase):
    """setup_game: starting player, shuffles and mulligans."""

    db: CardDatabase

    @classmethod
    def setUpClass(cls) -> None:
        """Load the knowledge graph once."""
        # Deferred: the graph load is only needed by this module.
        from mtgcards.database import CardDatabase  # noqa: PLC0415

        cls.db = CardDatabase(REPO)

    def test_103_1_starting_player_is_random(self) -> None:
        """Across seeds every seat gets to start a two-player game."""
        starters = {
            setup_game(_decks(2), self.db, random.Random(seed)).active_idx
            for seed in range(12)
        }
        self.assertEqual(starters, {0, 1})

    def test_103_1_starting_player_is_seeded(self) -> None:
        """The same seed picks the same starting player."""
        a = setup_game(_decks(3), self.db, random.Random(5)).active_idx
        b = setup_game(_decks(3), self.db, random.Random(5)).active_idx
        self.assertEqual(a, b)

    def test_103_5_two_players_bottom_one_card_per_mulligan(self) -> None:
        """Two players: three mulligans leave a four-card hand (no free one)."""
        game = setup_game(_decks(2), self.db, random.Random(1))
        for p in game.players:
            self.assertEqual(p.stats["mulligans"], 3)
            self.assertEqual(len(p.hand), 4)
            self.assertEqual(len(p.library), 36)

    def test_103_5c_multiplayer_first_mulligan_is_free(self) -> None:
        """Three players: the first mulligan costs no card (103.5c).

        The default policy stops after three *counted* mulligans, so it
        takes four in total and still bottoms three.
        """
        game = setup_game(_decks(3), self.db, random.Random(1))
        for p in game.players:
            self.assertEqual(p.stats["mulligans"], 4)
            self.assertEqual(len(p.hand), 4)
            self.assertEqual(len(p.library), 36)

    def test_103_5c_only_the_first_mulligan_is_free(self) -> None:
        """A policy taking exactly two mulligans bottoms 2 (2p) or 1 (3p)."""

        class TwoMulligans(DefaultPolicy):
            def __init__(self) -> None:
                super().__init__(random.Random(0))
                self.calls: dict[str, int] = {}

            def keep_hand(
                self,
                _game: Game,
                _player: Player,
                hand: list[GameObject],
                mulls: int,
            ) -> bool:
                del hand, mulls  # a fixed decision: mulligan twice, then keep
                self.calls[_player.name] = self.calls.get(_player.name, 0) + 1
                return self.calls[_player.name] > 2  # noqa: PLR2004 - "two mulligans"

        for n_players, hand_size in ((2, 5), (3, 6)):
            with self.subTest(players=n_players):
                game = setup_game(_decks(n_players), self.db, random.Random(1))
                policy = TwoMulligans()
                for p in game.players:
                    game.policies[p.name] = policy
                # re-run the mulligan phase alone on fresh libraries
                for p in game.players:
                    p.library = [*p.hand, *p.library]
                    p.hand = []
                _mulligans(game, game.players, game.policies, random.Random(2))
                for p in game.players:
                    self.assertEqual(p.stats["mulligans"], 2, p.name)
                    self.assertEqual(len(p.hand), hand_size, p.name)

    def test_starting_player_is_recorded(self) -> None:
        """The game record names the player who took the first turn."""
        rec = run_game(_decks(2), self.db, random.Random(3), MatchOptions(turn_cap=1))
        self.assertIn(rec["starting_player"], {"Seat 0", "Seat 1"})
        self.assertEqual(rec["turns"], 1)

    def test_103_8a_starting_player_skips_the_first_draw(self) -> None:
        """Whoever starts a two-player game draws no card on turn 1."""
        for seed in range(6):
            game = setup_game(_decks(2), self.db, random.Random(seed))
            hand_before = len(game.active_player.hand)
            # Deferred: TurnRunner lives in mtgrules.turns.
            from mtgrules.turns import TurnRunner  # noqa: PLC0415

            TurnRunner(game).take_turn()
            # a land drop is the only hand change of an all-land hand
            self.assertEqual(len(game.active_player.hand), hand_before - 1, seed)
