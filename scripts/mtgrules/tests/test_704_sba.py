"""State-based actions (CR 704) and commander damage (903.10a)."""

from __future__ import annotations

import unittest

from mtgrules.abilities import TokenSpec
from mtgrules.objects import Zone
from mtgrules.tests.helpers import creature, make_game, settle


class TestSBA(unittest.TestCase):
    """The state-based actions of rule 704.5."""

    def setUp(self) -> None:
        """Set up a fresh two-player game."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def test_704_5a_zero_life_loses(self) -> None:
        """A player at zero life loses (rule 704.5a)."""
        self.p1.life = 0
        settle(self.game)
        self.assertTrue(self.p1.lost)
        self.assertIs(self.game.winner, self.p0)

    def test_704_5b_draw_from_empty_library(self) -> None:
        """Drawing from an empty library loses (rule 704.5b)."""
        self.p1.library = []
        self.game.draw(self.p1, 1)
        settle(self.game)
        self.assertTrue(self.p1.lost)
        self.assertIn("704.5b", self.p1.lose_reason)

    def test_704_5f_zero_toughness_dies(self) -> None:
        """Zero toughness puts the creature in the graveyard (704.5f)."""
        c = creature(self.game, self.p0, toughness=2)
        self.game.put_counters(c, "-1/-1", 2)
        settle(self.game)
        self.assertEqual(c.zone, Zone.GRAVEYARD)

    def test_704_5g_lethal_damage_destroys(self) -> None:
        """Damage at least toughness destroys the creature (704.5g)."""
        c = creature(self.game, self.p0, toughness=3)
        src = creature(self.game, self.p1, power=3)
        self.game.deal_damage(src, c, 3)
        settle(self.game)
        self.assertEqual(c.zone, Zone.GRAVEYARD)

    def test_704_5g_nonlethal_damage_survives(self) -> None:
        """Damage below toughness leaves the creature alive."""
        c = creature(self.game, self.p0, toughness=3)
        src = creature(self.game, self.p1, power=2)
        self.game.deal_damage(src, c, 2)
        settle(self.game)
        self.assertEqual(c.zone, Zone.BATTLEFIELD)

    def test_704_5h_deathtouch_any_damage_destroys(self) -> None:
        """Any deathtouch damage destroys the creature (704.5h)."""
        c = creature(self.game, self.p0, toughness=9)
        src = creature(self.game, self.p1, power=1, keywords={"deathtouch"})
        self.game.deal_damage(src, c, 1)
        settle(self.game)
        self.assertEqual(c.zone, Zone.GRAVEYARD)

    def test_702_12_indestructible_survives_lethal(self) -> None:
        """Indestructible survives lethal damage (rule 702.12b)."""
        c = creature(self.game, self.p0, toughness=1, keywords={"indestructible"})
        src = creature(self.game, self.p1, power=5)
        self.game.deal_damage(src, c, 5)
        settle(self.game)
        self.assertEqual(c.zone, Zone.BATTLEFIELD)

    def test_704_5q_counter_annihilation(self) -> None:
        """+1/+1 and -1/-1 counters annihilate pairwise (704.5q)."""
        c = creature(self.game, self.p0, power=2, toughness=2)
        c.counters["+1/+1"] = 3
        c.counters["-1/-1"] = 2
        settle(self.game)
        self.assertEqual(c.counters.get("+1/+1", 0), 1)
        self.assertEqual(c.counters.get("-1/-1", 0), 0)

    def test_704_5j_legend_rule(self) -> None:
        """Two same-name legends under one player: one dies (704.5j)."""
        a = creature(self.game, self.p0, name="Legend", supertypes={"Legendary"})
        b = creature(self.game, self.p0, name="Legend", supertypes={"Legendary"})
        settle(self.game)
        on_bf = [o for o in (a, b) if o.zone == Zone.BATTLEFIELD]
        self.assertEqual(len(on_bf), 1)

    def test_704_5j_different_controllers_keep_both(self) -> None:
        """The legend rule is per player (rule 704.5j)."""
        a = creature(self.game, self.p0, name="Legend", supertypes={"Legendary"})
        b = creature(self.game, self.p1, name="Legend", supertypes={"Legendary"})
        settle(self.game)
        self.assertEqual(a.zone, Zone.BATTLEFIELD)
        self.assertEqual(b.zone, Zone.BATTLEFIELD)

    def test_704_5d_token_ceases_outside_battlefield(self) -> None:
        """A destroyed token ceases instead of hitting the graveyard."""
        toks = self.game.create_tokens(
            self.p0,
            TokenSpec(name="Soldier", power=1, toughness=1),
            1,
        )
        self.game.destroy(toks[0])
        settle(self.game)
        self.assertEqual(toks[0].zone, "ceased")
        self.assertNotIn(toks[0], self.p0.graveyard)

    def test_903_10a_commander_damage(self) -> None:
        """21 combat damage from one commander loses (rule 903.10a)."""
        cmd = creature(self.game, self.p0, name="General", power=7)
        cmd.commander = True
        for _ in range(3):
            self.game.deal_damage(cmd, self.p1, 7, combat=True)
        settle(self.game)
        self.assertTrue(self.p1.lost)
        self.assertIn("903.10a", self.p1.lose_reason)

    def test_120_3a_noncommander_damage_reduces_life_only(self) -> None:
        """Ordinary combat damage only reduces life (rule 120.3a)."""
        c = creature(self.game, self.p0, power=7)
        for _ in range(3):
            self.game.deal_damage(c, self.p1, 7, combat=True)
        settle(self.game)
        self.assertEqual(self.p1.life, 40 - 21)
        self.assertFalse(self.p1.lost)


if __name__ == "__main__":
    unittest.main()
