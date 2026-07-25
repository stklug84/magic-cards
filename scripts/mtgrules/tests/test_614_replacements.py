"""Replacement and prevention effects (CR 614-616)."""

import unittest

from ..abilities import TREASURE, TokenSpec
from .helpers import creature, make_game


def _hooked(game, player, name, override_fn):
    """Battlefield permanent carrying an overrides.py implementation."""
    obj = creature(game, player, name=name, power=1, toughness=1)
    ch = obj.base
    override_fn(ch, _FakeRef(name))
    game.replacements._cache.clear()
    game.bump()
    return obj


class _FakeRef:
    def __init__(self, name):
        self.name = name
        self.behavior = {}
        self.color_identity = set()
        self.types = set()


SOLDIER = TokenSpec(name="Soldier", power=1, toughness=1, types=frozenset({"Creature"}))


class TestReplacements(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def test_614_1c_token_doubler(self):
        from ..overrides import _doubling_season

        _hooked(self.game, self.p0, "Doubling Season", _doubling_season)
        made = self.game.create_tokens(self.p0, SOLDIER, 2)
        self.assertEqual(len(made), 4)

    def test_616_2_two_doublers_stack_multiplicatively(self):
        from ..overrides import _adrix_and_nev, _doubling_season

        _hooked(self.game, self.p0, "Doubling Season", _doubling_season)
        _hooked(self.game, self.p0, "Adrix and Nev, Twincasters", _adrix_and_nev)
        made = self.game.create_tokens(self.p0, SOLDIER, 1)
        self.assertEqual(len(made), 4)  # 1 * 2 * 2

    def test_614_1c_doubler_ignores_opponents_tokens(self):
        from ..overrides import _doubling_season

        _hooked(self.game, self.p0, "Doubling Season", _doubling_season)
        made = self.game.create_tokens(self.p1, SOLDIER, 2)
        self.assertEqual(len(made), 2)

    def test_counter_doubler(self):
        from ..overrides import _doubling_season

        _hooked(self.game, self.p0, "Doubling Season", _doubling_season)
        c = creature(self.game, self.p0)
        self.game.put_counters(c, "+1/+1", 2)
        self.assertEqual(c.counters["+1/+1"], 4)

    def test_615_prevention_no_life_gain(self):
        from ..overrides import _everlasting_torment

        _hooked(self.game, self.p0, "Everlasting Torment", _everlasting_torment)
        self.game.gain_life(self.p0, 5)
        self.assertEqual(self.p0.life, 40)

    def test_academy_manufactor_one_of_each(self):
        from ..overrides import _academy_manufactor

        _hooked(self.game, self.p0, "Academy Manufactor", _academy_manufactor)
        made = self.game.create_tokens(self.p0, TREASURE, 1)
        names = sorted(t.base.name for t in made)
        self.assertEqual(names, ["Clue", "Food", "Treasure"])

    def test_ojer_taq_triples_creature_tokens_only(self):
        from ..overrides import _ojer_taq

        _hooked(self.game, self.p0, "Ojer Taq", _ojer_taq)
        made = self.game.create_tokens(self.p0, SOLDIER, 1)
        self.assertEqual(len(made), 3)
        made = self.game.create_tokens(self.p0, TREASURE, 1)
        self.assertEqual(len(made), 1)  # not a creature token

    def test_614_enters_tapped_replacement_effectless_zone(self):
        """ZONE_CHANGE events carry the tapped flag through emit()."""
        from ..events import EventType
        from ..replacements import Replacement

        def matches(g, event):
            return event.data.get("to") == "battlefield"

        def replace(g, event):
            event.data["tapped"] = True
            return event

        self.game.replacements.floating.append(
            Replacement(EventType.ZONE_CHANGE, matches=matches, replace=replace),
        )
        from ..objects import Characteristics, GameObject, Zone

        card = GameObject(Characteristics(name="Land", types={"Land"}), self.p0)
        card.zone = Zone.HAND
        self.p0.hand.append(card)
        self.game.move_zone(card, Zone.BATTLEFIELD)
        self.assertTrue(card.tapped)


if __name__ == "__main__":
    unittest.main()
