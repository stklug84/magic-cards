"""Combat damage and combat keywords (CR 508-511, 702)."""

import unittest

from ..combat import CombatPhase, can_attack, can_block
from ..objects import Zone
from .helpers import creature, make_game, settle


class TestCombat(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        self.game.phase = "combat"

    def _fight(self):
        phase = CombatPhase(self.game)
        phase.attackers = [o for o in self.p0.battlefield if o.attacking is not None]
        fs = [
            a
            for a in phase._combatants()
            if a.chars(self.game).keywords & {"first strike", "double strike"}
        ]
        if fs:
            phase._damage_step(first_strike=True)
            settle(self.game)
        phase._damage_step(first_strike=False)
        settle(self.game)

    def test_510_1c_unblocked_hits_player(self):
        a = creature(self.game, self.p0, power=3)
        a.attacking = self.p1
        self._fight()
        self.assertEqual(self.p1.life, 37)

    def test_510_1c_blocked_trades(self):
        a = creature(self.game, self.p0, power=2, toughness=2)
        b = creature(self.game, self.p1, power=2, toughness=2)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self._fight()
        self.assertEqual(a.zone, Zone.GRAVEYARD)
        self.assertEqual(b.zone, Zone.GRAVEYARD)
        self.assertEqual(self.p1.life, 40)  # no damage through

    def test_702_19_trample_excess_to_player(self):
        a = creature(self.game, self.p0, power=6, toughness=6, keywords={"trample"})
        b = creature(self.game, self.p1, power=1, toughness=2)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self._fight()
        self.assertEqual(b.zone, Zone.GRAVEYARD)
        self.assertEqual(self.p1.life, 36)  # 6 - 2 lethal = 4 through

    def test_702_2b_deathtouch_trample_assigns_one(self):
        a = creature(
            self.game,
            self.p0,
            power=6,
            toughness=6,
            keywords={"trample", "deathtouch"},
        )
        b = creature(self.game, self.p1, power=1, toughness=4)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self._fight()
        self.assertEqual(b.zone, Zone.GRAVEYARD)  # 1 deathtouch = lethal
        self.assertEqual(self.p1.life, 35)  # 5 tramples through

    def test_510_5_first_strike_kills_before_normal_damage(self):
        a = creature(
            self.game,
            self.p0,
            power=2,
            toughness=1,
            keywords={"first strike"},
        )
        b = creature(self.game, self.p1, power=5, toughness=2)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self._fight()
        self.assertEqual(b.zone, Zone.GRAVEYARD)
        self.assertEqual(a.zone, Zone.BATTLEFIELD)  # never took damage

    def test_702_4_double_strike_hits_twice(self):
        a = creature(self.game, self.p0, power=3, keywords={"double strike"})
        a.attacking = self.p1
        self._fight()
        self.assertEqual(self.p1.life, 34)

    def test_509_1b_flying_blocked_only_by_reach_or_flying(self):
        a = creature(self.game, self.p0, keywords={"flying"})
        ground = creature(self.game, self.p1)
        reach = creature(self.game, self.p1, keywords={"reach"})
        flier = creature(self.game, self.p1, keywords={"flying"})
        self.assertFalse(can_block(self.game, ground, a))
        self.assertTrue(can_block(self.game, reach, a))
        self.assertTrue(can_block(self.game, flier, a))

    def test_302_6_summoning_sickness(self):
        a = creature(self.game, self.p0, entered_this_turn=True)
        self.assertFalse(can_attack(self.game, a))
        h = creature(self.game, self.p0, entered_this_turn=True, keywords={"haste"})
        self.assertTrue(can_attack(self.game, h))

    def test_702_80_wither_damage_as_counters(self):
        a = creature(self.game, self.p0, power=2, toughness=2, keywords={"wither"})
        b = creature(self.game, self.p1, power=1, toughness=4)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self._fight()
        self.assertEqual(b.counters.get("-1/-1", 0), 2)
        self.assertEqual(b.damage, 0)
        ch = b.chars(self.game)
        self.assertEqual((ch.power, ch.toughness), (-1, 2))


if __name__ == "__main__":
    unittest.main()
