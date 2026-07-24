"""PolicyProfile presets: knobs actually change DefaultPolicy decisions."""

import random
import unittest

from ..policy import (PROFILES, DefaultPolicy, PolicyProfile, get_profile)
from .helpers import card_in_hand, creature, make_game


class TestProfiles(unittest.TestCase):
    def test_presets_exist(self):
        for name in ("default", "aggressive", "control"):
            self.assertIsInstance(get_profile(name), PolicyProfile)
        self.assertIs(get_profile("default"), PROFILES["default"])

    def test_unknown_profile_exits(self):
        with self.assertRaises(SystemExit):
            get_profile("yolo")

    def test_default_policy_uses_default_profile(self):
        pol = DefaultPolicy(random.Random(1))
        self.assertEqual(pol.profile.name, "default")

    def test_mulligan_knobs(self):
        game = make_game()
        p = game.players[0]
        strict = DefaultPolicy(random.Random(1), PolicyProfile(
            mulligan_min_lands=3, mulligan_max_lands=4))
        loose = DefaultPolicy(random.Random(1), PolicyProfile(
            mulligan_min_lands=1, mulligan_max_lands=6))
        hand = [card_in_hand(game, p, name=f"c{i}", types=("Sorcery",))
                for i in range(5)]
        hand += [card_in_hand(game, p, name=f"l{i}", types=("Land",))
                 for i in range(2)]
        self.assertFalse(strict.keep_hand(game, p, hand, mulls=0))
        self.assertTrue(loose.keep_hand(game, p, hand, mulls=0))
        # max_mulligans forces a keep
        self.assertTrue(DefaultPolicy(random.Random(1), PolicyProfile(
            mulligan_min_lands=3, max_mulligans=2)).keep_hand(
                game, p, hand, mulls=2))

    def test_aggression_changes_attacks(self):
        """A 1.5-aggression profile attacks into a wall a 0.7 one won't."""
        results = {}
        for name in ("aggressive", "control"):
            game = make_game()
            atk, dfn = game.players
            game.policies[atk.name] = DefaultPolicy(random.Random(1),
                                                    get_profile(name))
            attacker = creature(game, atk, name="Raider",
                                power=3, toughness=3)
            creature(game, dfn, name="Wall", power=3, toughness=3)
            picks = game.policies[atk.name].declare_attackers(
                game, atk, [attacker])
            results[name] = bool(picks)
        self.assertTrue(results["aggressive"])
        self.assertFalse(results["control"])

    def test_race_life_chump_blocks(self):
        """Below race_life, the control profile chump-blocks freely."""
        game = make_game()
        atk, dfn = game.players
        prof = PolicyProfile(race_life=14)
        pol = DefaultPolicy(random.Random(1), prof)
        big = creature(game, atk, name="Big", power=4, toughness=6)
        small = creature(game, dfn, name="Chump", power=1, toughness=1)
        dfn.life = 20                       # safe: no chump block
        self.assertEqual(pol.declare_blockers(game, dfn, [big], [small]),
                         [])
        dfn.life = 10                       # racing: block anything
        self.assertEqual(pol.declare_blockers(game, dfn, [big], [small]),
                         [(small, big)])


if __name__ == "__main__":
    unittest.main()
