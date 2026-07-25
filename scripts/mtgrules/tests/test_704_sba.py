"""State-based actions (CR 704) and commander damage (903.10a)."""

import unittest

from ..objects import Zone
from .helpers import creature, make_game, settle


class TestSBA(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def test_704_5a_zero_life_loses(self):
        self.p1.life = 0
        settle(self.game)
        self.assertTrue(self.p1.lost)
        self.assertIs(self.game.winner, self.p0)

    def test_704_5b_draw_from_empty_library(self):
        self.p1.library = []
        self.game.draw(self.p1, 1)
        settle(self.game)
        self.assertTrue(self.p1.lost)
        self.assertIn("704.5b", self.p1.lose_reason)

    def test_704_5f_zero_toughness_dies(self):
        c = creature(self.game, self.p0, toughness=2)
        self.game.put_counters(c, "-1/-1", 2)
        settle(self.game)
        self.assertEqual(c.zone, Zone.GRAVEYARD)

    def test_704_5g_lethal_damage_destroys(self):
        c = creature(self.game, self.p0, toughness=3)
        src = creature(self.game, self.p1, power=3)
        self.game.deal_damage(src, c, 3)
        settle(self.game)
        self.assertEqual(c.zone, Zone.GRAVEYARD)

    def test_704_5g_nonlethal_damage_survives(self):
        c = creature(self.game, self.p0, toughness=3)
        src = creature(self.game, self.p1, power=2)
        self.game.deal_damage(src, c, 2)
        settle(self.game)
        self.assertEqual(c.zone, Zone.BATTLEFIELD)

    def test_704_5h_deathtouch_any_damage_destroys(self):
        c = creature(self.game, self.p0, toughness=9)
        src = creature(self.game, self.p1, power=1, keywords={"deathtouch"})
        self.game.deal_damage(src, c, 1)
        settle(self.game)
        self.assertEqual(c.zone, Zone.GRAVEYARD)

    def test_702_12_indestructible_survives_lethal(self):
        c = creature(self.game, self.p0, toughness=1, keywords={"indestructible"})
        src = creature(self.game, self.p1, power=5)
        self.game.deal_damage(src, c, 5)
        settle(self.game)
        self.assertEqual(c.zone, Zone.BATTLEFIELD)

    def test_704_5q_counter_annihilation(self):
        c = creature(self.game, self.p0, power=2, toughness=2)
        c.counters["+1/+1"] = 3
        c.counters["-1/-1"] = 2
        settle(self.game)
        self.assertEqual(c.counters.get("+1/+1", 0), 1)
        self.assertEqual(c.counters.get("-1/-1", 0), 0)

    def test_704_5j_legend_rule(self):
        a = creature(self.game, self.p0, name="Legend", supertypes={"Legendary"})
        b = creature(self.game, self.p0, name="Legend", supertypes={"Legendary"})
        settle(self.game)
        on_bf = [o for o in (a, b) if o.zone == Zone.BATTLEFIELD]
        self.assertEqual(len(on_bf), 1)

    def test_704_5j_different_controllers_keep_both(self):
        a = creature(self.game, self.p0, name="Legend", supertypes={"Legendary"})
        b = creature(self.game, self.p1, name="Legend", supertypes={"Legendary"})
        settle(self.game)
        self.assertEqual(a.zone, Zone.BATTLEFIELD)
        self.assertEqual(b.zone, Zone.BATTLEFIELD)

    def test_704_5d_token_ceases_outside_battlefield(self):
        from ..abilities import TokenSpec

        toks = self.game.create_tokens(
            self.p0,
            TokenSpec(name="Soldier", power=1, toughness=1),
            1,
        )
        self.game.destroy(toks[0])
        settle(self.game)
        self.assertEqual(toks[0].zone, "ceased")
        self.assertNotIn(toks[0], self.p0.graveyard)

    def test_903_10a_commander_damage(self):
        cmd = creature(self.game, self.p0, name="General", power=7)
        cmd.commander = True
        for _ in range(3):
            self.game.deal_damage(cmd, self.p1, 7, combat=True)
        settle(self.game)
        self.assertTrue(self.p1.lost)
        self.assertIn("903.10a", self.p1.lose_reason)

    def test_120_3a_noncommander_damage_reduces_life_only(self):
        c = creature(self.game, self.p0, power=7)
        for _ in range(3):
            self.game.deal_damage(c, self.p1, 7, combat=True)
        settle(self.game)
        self.assertEqual(self.p1.life, 40 - 21)
        self.assertFalse(self.p1.lost)


if __name__ == "__main__":
    unittest.main()
