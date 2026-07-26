"""Replacement and prevention effects (CR 614-616)."""

from __future__ import annotations

import unittest
from typing import TYPE_CHECKING

from mtgrules.abilities import TREASURE, TokenSpec
from mtgrules.events import EventType
from mtgrules.objects import Characteristics, GameObject, Zone
from mtgrules.overrides import (
    _academy_manufactor,
    _adrix_and_nev,
    _doubling_season,
    _everlasting_torment,
    _ojer_taq,
)
from mtgrules.replacements import Replacement
from mtgrules.tests.helpers import creature, make_game

if TYPE_CHECKING:
    from mtgrules.events import Event
    from mtgrules.game import Game
    from mtgrules.objects import Player
    from mtgrules.overrides import OverrideFn


class _FakeRef:
    """Minimal CardRef stand-in for override functions."""

    def __init__(self, name: str) -> None:
        """Fill every CardRef field with an empty value."""
        self.name = name
        self.mana_cost = ""
        self.oracle = ""
        self.types: set[str] = set()
        self.subtypes: set[str] = set()
        self.supertypes: set[str] = set()
        self.power: int | None = None
        self.toughness: int | None = None
        self.loyalty: int | None = None
        self.color_identity: set[str] = set()
        self.behavior: dict[str, object] = {}


def _hooked(
    game: Game,
    player: Player,
    name: str,
    override_fn: OverrideFn,
) -> GameObject:
    """Battlefield permanent carrying an overrides.py implementation."""
    obj = creature(game, player, name=name, power=1, toughness=1)
    ch = obj.base
    override_fn(ch, _FakeRef(name))
    game.replacements.clear_cache()
    game.bump()
    return obj


SOLDIER = TokenSpec(name="Soldier", power=1, toughness=1, types=frozenset({"Creature"}))


class TestReplacements(unittest.TestCase):
    """Replacement ordering, doubling, and prevention (CR 614-616)."""

    def setUp(self) -> None:
        """Set up a fresh two-player game."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def test_614_1c_token_doubler(self) -> None:
        """A token doubler doubles created tokens (rule 614.1c)."""
        _hooked(self.game, self.p0, "Doubling Season", _doubling_season)
        made = self.game.create_tokens(self.p0, SOLDIER, 2)
        self.assertEqual(len(made), 4)

    def test_616_2_two_doublers_stack_multiplicatively(self) -> None:
        """Two doublers each apply once per event (rule 616.2)."""
        _hooked(self.game, self.p0, "Doubling Season", _doubling_season)
        _hooked(self.game, self.p0, "Adrix and Nev, Twincasters", _adrix_and_nev)
        made = self.game.create_tokens(self.p0, SOLDIER, 1)
        self.assertEqual(len(made), 4)  # 1 * 2 * 2

    def test_614_1c_doubler_ignores_opponents_tokens(self) -> None:
        """A doubler only affects its controller's tokens."""
        _hooked(self.game, self.p0, "Doubling Season", _doubling_season)
        made = self.game.create_tokens(self.p1, SOLDIER, 2)
        self.assertEqual(len(made), 2)

    def test_counter_doubler(self) -> None:
        """Doubling Season also doubles placed counters."""
        _hooked(self.game, self.p0, "Doubling Season", _doubling_season)
        c = creature(self.game, self.p0)
        self.game.put_counters(c, "+1/+1", 2)
        self.assertEqual(c.counters["+1/+1"], 4)

    def test_615_prevention_no_life_gain(self) -> None:
        """A prevention effect removes the life-gain event (rule 615)."""
        _hooked(self.game, self.p0, "Everlasting Torment", _everlasting_torment)
        self.game.gain_life(self.p0, 5)
        self.assertEqual(self.p0.life, 40)

    def test_academy_manufactor_one_of_each(self) -> None:
        """Academy Manufactor turns one Treasure into all three tokens."""
        _hooked(self.game, self.p0, "Academy Manufactor", _academy_manufactor)
        made = self.game.create_tokens(self.p0, TREASURE, 1)
        names = sorted(t.base.name for t in made)
        self.assertEqual(names, ["Clue", "Food", "Treasure"])

    def test_ojer_taq_triples_creature_tokens_only(self) -> None:
        """Ojer Taq triples creature tokens but not Treasures."""
        _hooked(self.game, self.p0, "Ojer Taq", _ojer_taq)
        made = self.game.create_tokens(self.p0, SOLDIER, 1)
        self.assertEqual(len(made), 3)
        made = self.game.create_tokens(self.p0, TREASURE, 1)
        self.assertEqual(len(made), 1)  # not a creature token

    def test_614_enters_tapped_replacement_effectless_zone(self) -> None:
        """ZONE_CHANGE events carry the tapped flag through emit()."""

        def matches(_game: Game, event: Event) -> bool:
            return event.data.get("to") == "battlefield"

        def replace(_game: Game, event: Event) -> Event:
            event.data["tapped"] = True
            return event

        self.game.replacements.floating.append(
            Replacement(EventType.ZONE_CHANGE, matches=matches, replace=replace),
        )
        card = GameObject(Characteristics(name="Land", types={"Land"}), self.p0)
        card.zone = Zone.HAND
        self.p0.hand.append(card)
        self.game.move_zone(card, Zone.BATTLEFIELD)
        self.assertTrue(card.tapped)


if __name__ == "__main__":
    unittest.main()
