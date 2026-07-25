"""Poison/infect/toxic (702.90/702.164/704.5c), spell copies (707),
cycling (702.29), additional costs / cost reduction (601.2b/f), and the
new compiler templates behind them.
"""

import unittest

from ..abilities import (
    ActivatedAbility,
    SpellAbility,
    StaticAbility,
    TriggeredAbility,
)
from ..compiler import compile_card
from ..effects import (
    CopySpell,
    Drain,
    GainLife,
    LoseLifeTargetMV,
    TargetControllerBasicLand,
    TargetControllerGainsPower,
)
from ..objects import Zone
from .helpers import card_in_hand, creature, give_mana, make_game, settle


class _Ref:
    def __init__(
        self,
        name,
        oracle,
        types=("Creature",),
        mana_cost="{1}",
        power=1,
        toughness=1,
    ):
        self.name = name
        self.oracle = oracle
        self.types = set(types)
        self.subtypes = set()
        self.supertypes = set()
        self.mana_cost = mana_cost
        self.power = power
        self.toughness = toughness
        self.loyalty = None
        self.color_identity = set()
        self.behavior = {}
        self.keywords = set()


class TestPoison(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def test_702_90b_infect_damage_is_poison(self):
        atk = creature(
            self.game,
            self.p0,
            name="Blighted",
            power=3,
            keywords={"infect"},
        )
        self.game.deal_damage(atk, self.p1, 3, combat=True)
        self.assertEqual(self.p1.poison, 3)
        self.assertEqual(self.p1.life, 40)  # no life loss

    def test_702_164_toxic_adds_poison(self):
        atk = creature(self.game, self.p0, name="Rat", power=1, keywords={"toxic:2"})
        self.game.deal_damage(atk, self.p1, 1, combat=True)
        self.assertEqual(self.p1.life, 39)  # normal damage too
        self.assertEqual(self.p1.poison, 2)

    def test_704_5c_ten_poison_loses(self):
        self.p1.poison = 10
        self.game.check_state_based_actions()
        self.assertTrue(self.p1.lost)
        self.assertIn("704.5c", self.p1.lose_reason)


class TestCopies(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def test_707_10_copy_resolves_and_ceases(self):
        spell = card_in_hand(
            self.game,
            self.p0,
            name="Rite",
            mana_cost="{1}",
            abilities=[SpellAbility(effect=GainLife(3))],
        )
        give_mana(self.p0, C=1)
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        item = self.game.stack[-1]
        copy = self.game.copy_spell(item, self.p1)
        self.assertEqual(len(self.game.stack), 2)
        self.game.resolve_top()  # copy (p1) resolves
        self.game.resolve_top()  # original resolves
        self.assertEqual(self.p1.life, 43)
        self.assertEqual(self.p0.life, 43)
        # rule 704.5e / 707.10a: the copy is in no graveyard
        self.assertNotIn(copy.obj, self.p1.graveyard)
        self.assertNotIn(copy.obj, self.p0.graveyard)
        self.assertEqual(spell.zone, Zone.GRAVEYARD)

    def test_compiler_copy_template(self):
        ch = compile_card(
            _Ref(
                "Fork2",
                "Copy target instant or sorcery spell. You may "
                "choose new targets for the copy.",
                types=("Instant",),
            ),
        )
        sa = next(a for a in ch.abilities if isinstance(a, SpellAbility))
        parts = getattr(sa.effect, "parts", [sa.effect])
        self.assertTrue(any(isinstance(p, CopySpell) for p in parts))
        self.assertEqual(sa.targets[0].what, "spell")

    def test_uncounterable_static(self):
        ch = compile_card(
            _Ref(
                "Chimil2",
                "Spells you control can't be countered.",
                types=("Artifact",),
            ),
        )
        st = next(a for a in ch.abilities if isinstance(a, StaticAbility))
        self.assertTrue(st.uncounterable_spells)
        # runtime: counter_spell is a no-op
        from ..objects import GameObject

        shield = GameObject(ch, self.p0)
        shield.zone = Zone.BATTLEFIELD
        shield.controller = self.p0
        self.p0.battlefield.append(shield)
        spell = card_in_hand(
            self.game,
            self.p0,
            name="Rite",
            mana_cost="{1}",
            abilities=[SpellAbility(effect=GainLife(1))],
        )
        give_mana(self.p0, C=1)
        self.game.cast_spell(self.p0, spell)
        self.game.counter_spell(self.game.stack[-1])
        self.assertEqual(len(self.game.stack), 1)  # still there


class TestCycling(unittest.TestCase):
    def test_702_29a_cycle_from_hand(self):
        game = make_game()
        p0 = game.players[0]
        ch = compile_card(_Ref("Slough2", "Cycling {2}", types=("Land",), mana_cost=""))
        ab = next(
            a for a in ch.abilities if isinstance(a, ActivatedAbility) and a.from_hand
        )
        from ..objects import GameObject

        card = GameObject(ch, p0)
        card.zone = Zone.HAND
        p0.hand.append(card)
        top = card_in_hand(game, p0, name="TopCard")
        p0.hand.remove(top)
        top.zone = Zone.LIBRARY
        p0.library.append(top)
        give_mana(p0, C=2)
        self.assertTrue(game.activate_ability(p0, card, ab))
        self.assertEqual(card.zone, Zone.GRAVEYARD)  # discarded as cost
        settle(game)
        self.assertIn(top, p0.hand)  # drew a card


class TestCastModifiers(unittest.TestCase):
    def setUp(self):
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def test_601_2f_cost_reduction_per_creature(self):
        ch = compile_card(
            _Ref(
                "Blasphemy2",
                "This spell costs {1} less to cast for each creature on the "
                "battlefield.\nBlasphemy2 deals 13 damage to each creature.",
                types=("Sorcery",),
                mana_cost="{8}{R}",
            ),
        )
        self.assertEqual(ch.cost_less_per_creature, 1)
        from ..objects import GameObject

        for i in range(8):
            creature(self.game, self.p1, name=f"c{i}")
        spell = GameObject(ch, self.p0)
        spell.zone = Zone.HAND
        self.p0.hand.append(spell)
        give_mana(self.p0, R=1)  # {8} reduced to {0}
        self.assertTrue(self.game.cast_spell(self.p0, spell))

    def test_601_2b_additional_cost_sacrifice(self):
        ch = compile_card(
            _Ref(
                "Intent2",
                "As an additional cost to cast this spell, sacrifice a "
                "creature.\nSearch your library for a card, put that card "
                "into your hand, then shuffle.",
                types=("Sorcery",),
                mana_cost="{B}",
            ),
        )
        self.assertEqual(ch.additional_cost, "sacrifice_creature")
        from ..objects import GameObject

        spell = GameObject(ch, self.p0)
        spell.zone = Zone.HAND
        self.p0.hand.append(spell)
        give_mana(self.p0, B=1)
        # no creature to sacrifice -> cannot cast
        self.assertFalse(self.game.cast_spell(self.p0, spell))
        fodder = creature(self.game, self.p0, name="Fodder")
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        self.assertEqual(fodder.zone, Zone.GRAVEYARD)


class TestRiderTemplates(unittest.TestCase):
    def test_swords_rider(self):
        ch = compile_card(
            _Ref(
                "StP2",
                "Exile target creature. Its controller gains life equal to its power.",
                types=("Instant",),
                mana_cost="{W}",
            ),
        )
        sa = next(a for a in ch.abilities if isinstance(a, SpellAbility))
        parts = getattr(sa.effect, "parts", [sa.effect])
        self.assertTrue(any(isinstance(p, TargetControllerGainsPower) for p in parts))
        game = make_game()
        p0, p1 = game.players
        bear = creature(game, p1, name="Bear", power=4, toughness=4)
        from ..objects import GameObject

        spell = GameObject(ch, p0)
        spell.zone = Zone.HAND
        p0.hand.append(spell)
        give_mana(p0, W=1)
        self.assertTrue(game.cast_spell(p0, spell))
        game.resolve_top()
        self.assertEqual(bear.zone, Zone.EXILE)
        self.assertEqual(p1.life, 44)

    def test_trophy_and_feed_riders_compile(self):
        ch = compile_card(
            _Ref(
                "Trophy2",
                "Destroy target permanent an opponent controls. "
                "Its controller may search their library for a basic land "
                "card, put it onto the battlefield, then shuffle.",
                types=("Instant",),
            ),
        )
        sa = next(a for a in ch.abilities if isinstance(a, SpellAbility))
        parts = getattr(sa.effect, "parts", [sa.effect])
        self.assertTrue(any(isinstance(p, TargetControllerBasicLand) for p in parts))
        ch = compile_card(
            _Ref(
                "Feed2",
                "Destroy target creature or enchantment an opponent "
                "controls. You lose life equal to that permanent's mana "
                "value.",
                types=("Sorcery",),
            ),
        )
        sa = next(a for a in ch.abilities if isinstance(a, SpellAbility))
        parts = getattr(sa.effect, "parts", [sa.effect])
        self.assertTrue(any(isinstance(p, LoseLifeTargetMV) for p in parts))

    def test_exsanguinate_merges_to_drain(self):
        ch = compile_card(
            _Ref(
                "Exsang2",
                "Each opponent loses X life. You gain life equal "
                "to the life lost this way.",
                types=("Sorcery",),
                mana_cost="{X}{B}{B}",
            ),
        )
        sa = next(a for a in ch.abilities if isinstance(a, SpellAbility))
        eff = sa.effect
        parts = getattr(eff, "parts", [eff])
        self.assertTrue(any(isinstance(p, Drain) for p in parts))


class TestDiesWithCounterTriggers(unittest.TestCase):
    def test_village_pillagers_style(self):
        ch = compile_card(
            _Ref(
                "Pillager2",
                "Whenever a creature an opponent controls with "
                "a counter on it dies, you create a tapped Treasure token.",
            ),
        )
        next(a for a in ch.abilities if isinstance(a, TriggeredAbility))
        game = make_game()
        p0, p1 = game.players
        from ..objects import GameObject

        src = GameObject(ch, p0)
        src.zone = Zone.BATTLEFIELD
        src.controller = p0
        p0.battlefield.append(src)
        victim = creature(game, p1, name="Marked", power=1, toughness=1)
        game.put_counters(victim, "-1/-1", 1)
        settle(game)  # SBA kills the 1/1
        self.assertEqual(victim.zone, Zone.GRAVEYARD)
        treasures = [o for o in p0.battlefield if "Treasure" in o.base.subtypes]
        self.assertEqual(len(treasures), 1)
        self.assertTrue(treasures[0].tapped)

    def test_once_each_turn_gate(self):
        ch = compile_card(
            _Ref(
                "Reaper2",
                "Whenever a creature an opponent controls with a "
                "-1/-1 counter on it dies, you may put that card onto the "
                "battlefield under your control. Do this only once each "
                "turn.",
            ),
        )
        trig = next(a for a in ch.abilities if isinstance(a, TriggeredAbility))
        self.assertTrue(trig.once_each_turn)
        game = make_game()
        p0, p1 = game.players
        from ..objects import GameObject

        src = GameObject(ch, p0)
        src.zone = Zone.BATTLEFIELD
        src.controller = p0
        p0.battlefield.append(src)
        for n in ("A", "B"):
            v = creature(game, p1, name=n, power=2, toughness=2)
            game.put_counters(v, "-1/-1", 2)
        settle(game)  # both die to SBAs
        stolen = [o for o in p0.battlefield if o.base.name in ("A", "B")]
        self.assertEqual(len(stolen), 1)  # only once this turn


if __name__ == "__main__":
    unittest.main()
