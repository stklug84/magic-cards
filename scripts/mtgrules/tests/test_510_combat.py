"""Combat damage and combat keywords (CR 508-511, 702)."""

from __future__ import annotations

import unittest

from mtgrules.combat import CombatPhase, can_attack, can_block
from mtgrules.objects import Zone
from mtgrules.tests.helpers import creature, make_game, settle


class TestCombat(unittest.TestCase):
    """Combat damage assignment and combat keywords."""

    def setUp(self) -> None:
        """Set up a two-player game in the combat phase."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        self.game.phase = "combat"

    def _fight(self) -> None:
        """Run the damage step(s) for the declared attack."""
        phase = CombatPhase(self.game)
        phase.attackers = [o for o in self.p0.battlefield if o.attacking is not None]
        fs = [
            a
            for a in phase.combatants()
            if a.chars(self.game).keywords & {"first strike", "double strike"}
        ]
        if fs:
            phase.damage_step(first_strike=True)
            settle(self.game)
        phase.damage_step(first_strike=False)
        settle(self.game)

    def test_510_1c_unblocked_hits_player(self) -> None:
        """An unblocked attacker damages the defending player."""
        a = creature(self.game, self.p0, power=3)
        a.attacking = self.p1
        self._fight()
        self.assertEqual(self.p1.life, 37)

    def test_510_1c_blocked_trades(self) -> None:
        """Equal-stat attacker and blocker trade; no damage through."""
        a = creature(self.game, self.p0, power=2, toughness=2)
        b = creature(self.game, self.p1, power=2, toughness=2)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self._fight()
        self.assertEqual(a.zone, Zone.GRAVEYARD)
        self.assertEqual(b.zone, Zone.GRAVEYARD)
        self.assertEqual(self.p1.life, 40)  # no damage through

    def test_702_19_trample_excess_to_player(self) -> None:
        """Trample assigns lethal to the blocker, excess to the player."""
        a = creature(self.game, self.p0, power=6, toughness=6, keywords={"trample"})
        b = creature(self.game, self.p1, power=1, toughness=2)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self._fight()
        self.assertEqual(b.zone, Zone.GRAVEYARD)
        self.assertEqual(self.p1.life, 36)  # 6 - 2 lethal = 4 through

    def test_702_2b_deathtouch_trample_assigns_one(self) -> None:
        """With deathtouch, one damage is lethal; the rest tramples."""
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

    def test_510_5_first_strike_kills_before_normal_damage(self) -> None:
        """A first-striker kills its blocker before taking damage."""
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

    def test_702_4_double_strike_hits_twice(self) -> None:
        """A double-striker deals damage in both damage steps."""
        a = creature(self.game, self.p0, power=3, keywords={"double strike"})
        a.attacking = self.p1
        self._fight()
        self.assertEqual(self.p1.life, 34)

    def test_509_1b_flying_blocked_only_by_reach_or_flying(self) -> None:
        """Flying restricts blockers to reach or flying (rule 702.9c)."""
        a = creature(self.game, self.p0, keywords={"flying"})
        ground = creature(self.game, self.p1)
        reach = creature(self.game, self.p1, keywords={"reach"})
        flier = creature(self.game, self.p1, keywords={"flying"})
        self.assertFalse(can_block(self.game, ground, a))
        self.assertTrue(can_block(self.game, reach, a))
        self.assertTrue(can_block(self.game, flier, a))

    def test_302_6_summoning_sickness(self) -> None:
        """Fresh creatures can't attack unless they have haste."""
        a = creature(self.game, self.p0, entered_this_turn=True)
        self.assertFalse(can_attack(self.game, a))
        h = creature(self.game, self.p0, entered_this_turn=True, keywords={"haste"})
        self.assertTrue(can_attack(self.game, h))

    def test_702_80_wither_damage_as_counters(self) -> None:
        """Wither deals damage as -1/-1 counters (rule 702.80)."""
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

    # -- fixes F2 / F3: both sides of combat deal their damage ----------

    def test_510_4_first_striker_takes_regular_step_damage(self) -> None:
        """A first-striker that fails to kill its blocker is hit back (510.4)."""
        a = creature(
            self.game, self.p0, power=2, toughness=2, keywords={"first strike"}
        )
        b = creature(self.game, self.p1, power=3, toughness=3)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self._fight()
        self.assertEqual(b.zone, Zone.BATTLEFIELD)
        self.assertEqual(b.damage, 2)  # first-strike damage marked
        self.assertEqual(a.zone, Zone.GRAVEYARD)  # regular-step damage back

    def test_702_7b_first_strike_blocker_deals_damage(self) -> None:
        """A first-strike blocker kills a normal attacker before it strikes."""
        a = creature(self.game, self.p0, power=2, toughness=2)
        b = creature(
            self.game, self.p1, power=2, toughness=1, keywords={"first strike"}
        )
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self._fight()
        self.assertEqual(a.zone, Zone.GRAVEYARD)
        self.assertEqual(b.zone, Zone.BATTLEFIELD)  # the attacker never struck
        self.assertEqual(b.damage, 0)

    def test_510_1_zero_power_attacker_is_damaged_by_its_blocker(self) -> None:
        """A 0-power attacker assigns nothing but still takes the block (510.1)."""
        a = creature(self.game, self.p0, power=0, toughness=4)
        b = creature(self.game, self.p1, power=2, toughness=2)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self._fight()
        self.assertEqual(a.damage, 2)
        self.assertEqual(b.damage, 0)
        self.assertEqual(self.p1.life, 40)

    def test_702_19d_trample_with_removed_blocker_hits_player(self) -> None:
        """Trample assigns all its damage to the player if the blocker left."""
        a = creature(self.game, self.p0, power=3, toughness=3, keywords={"trample"})
        b = creature(self.game, self.p1, power=1, toughness=1)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self.game.move_zone(b, Zone.GRAVEYARD)  # removed before damage
        self._fight()
        self.assertEqual(self.p1.life, 37)

    def test_510_1c_blocked_without_trample_deals_nothing_if_blocker_left(self) -> None:
        """Without trample a blocked creature whose blocker left deals no damage."""
        a = creature(self.game, self.p0, power=3, toughness=3)
        b = creature(self.game, self.p1, power=1, toughness=1)
        a.attacking = self.p1
        a.blocked_by = [b]
        b.blocking = [a]
        self.game.move_zone(b, Zone.GRAVEYARD)
        self._fight()
        self.assertEqual(self.p1.life, 40)


if __name__ == "__main__":
    unittest.main()
