"""Targeting (CR 115, 601.2c-d, 608.2b): fixes F5, F7, F9, F11 and P3.

F5:  a 'cost: effect' line is an activated ability only if every cost
     component is one the engine charges; trigger lines are parsed first.
F7:  target legality is fully re-checked on resolution (608.2b).
F9:  divided damage is announced at casting (601.2d).
F11: spells and abilities may have several targets per "target" word
     ("up to two", "any number of", "one or two", "any target").
P3:  the AI never aims a harmful effect at its best permanent when forced
     to pick one of its own, nor a beneficial one at the enemy's best.
"""

from __future__ import annotations

import unittest

from mtgrules import compiler
from mtgrules.abilities import (
    ActivatedAbility,
    SpellAbility,
    TargetSpec,
    TriggeredAbility,
)
from mtgrules.compiler import compile_card, parse_effect_text
from mtgrules.effects import DealDamage, Destroy, PutCounters, Untap
from mtgrules.objects import GameObject, Zone
from mtgrules.policy import _is_beneficial
from mtgrules.tests.helpers import (
    FakeRef,
    card_in_hand,
    creature,
    give_mana,
    make_game,
    settle,
)


def _activated(ref: FakeRef) -> list[ActivatedAbility]:
    return [a for a in compile_card(ref).abilities if isinstance(a, ActivatedAbility)]


class TestCostParsing(unittest.TestCase):
    """F5: only real, engine-charged costs make an activated ability."""

    def setUp(self) -> None:
        """Forget earlier unknown-clause notes for the test card names."""
        for name in ("Modal", "Looter", "Drawer", "Sacker", "Lifer", "Untapper"):
            compiler.UNKNOWN_CLAUSES.pop(name, None)

    def test_modal_trigger_is_not_a_free_activated_ability(self) -> None:
        """'When this creature enters, choose one: ...' is not cost-free."""
        ch = compile_card(
            FakeRef("Modal", "When this creature enters, choose one: Draw a card.")
        )
        self.assertEqual(
            [a for a in ch.abilities if isinstance(a, ActivatedAbility)], []
        )
        self.assertTrue(any(isinstance(a, TriggeredAbility) for a in ch.abilities))

    def test_unmodelled_cost_component_is_reported(self) -> None:
        """A discard cost the engine cannot charge makes the line unmodelled."""
        self.assertEqual(
            _activated(FakeRef("Looter", "{1}, {T}, Discard a card: Draw a card.")), []
        )
        self.assertIn(
            "{1}, {T}, Discard a card: Draw a card.", compiler.UNKNOWN_CLAUSES["Looter"]
        )

    def test_untap_symbol_is_not_charged(self) -> None:
        """{Q} is not a cost the engine knows, so the line is unmodelled."""
        self.assertEqual(_activated(FakeRef("Untapper", "{Q}: Draw a card.")), [])

    def test_charged_costs_still_compile(self) -> None:
        """Mana, {T}, sacrifice and life costs are activated abilities."""
        [a] = _activated(FakeRef("Drawer", "{2}, {T}: Draw a card."))
        self.assertEqual((a.cost.generic, a.tap_cost), (2, True))
        [b] = _activated(
            FakeRef(
                "Sacker",
                "{T}, Sacrifice this artifact: Draw a card.",
                types={"Artifact"},
            )
        )
        self.assertEqual(b.sac_cost, "self")
        [c] = _activated(FakeRef("Lifer", "Pay 2 life, {T}: Draw a card."))
        self.assertEqual((c.life_cost, c.tap_cost), (2, True))
        self.assertNotIn("Drawer", compiler.UNKNOWN_CLAUSES)


_FORKED_TEXT = (
    "This spell deals 4 damage divided as you choose among one, two, or three targets."
)


class TestTargetCounts(unittest.TestCase):
    """F11: the compiler reads how many targets a 'target' word allows."""

    def _spec(self, text: str) -> TargetSpec:
        _effect, targets = parse_effect_text(text, "Spell")
        self.assertEqual(len(targets), 1, text)
        return targets[0]

    def test_single_target(self) -> None:
        """'Destroy target creature.' is exactly one creature."""
        spec = self._spec("Destroy target creature.")
        self.assertEqual((spec.what, spec.count, spec.minimum), ("creature", 1, 1))

    def test_up_to_n(self) -> None:
        """'up to two' allows zero to two."""
        spec = self._spec("Destroy up to two target creatures.")
        self.assertEqual((spec.count, spec.minimum, spec.optional), (2, 0, True))

    def test_exactly_n(self) -> None:
        """'two target creatures' requires two."""
        spec = self._spec("Destroy two target creatures.")
        self.assertEqual((spec.count, spec.minimum), (2, 2))

    def test_any_number(self) -> None:
        """'any number of' is unbounded and may be none."""
        spec = self._spec("Exile any number of target creatures you control.")
        self.assertGreaterEqual(spec.count, 99)
        self.assertEqual((spec.minimum, spec.controller), (0, "you"))

    def test_one_or_two(self) -> None:
        """'one or two' is one to two."""
        spec = self._spec("Tap one or two target creatures.")
        self.assertEqual((spec.count, spec.minimum), (2, 1))

    def test_any_target(self) -> None:
        """'any target' is a creature, planeswalker or player."""
        effect, targets = parse_effect_text(
            "This spell deals 3 damage to any target.", "Bolt"
        )
        self.assertIsInstance(effect, DealDamage)
        self.assertEqual((targets[0].what, targets[0].count), ("any", 1))

    def test_divided_damage_spec(self) -> None:
        """Divided damage targets 'one, two, or three' any-targets."""
        effect, targets = parse_effect_text(_FORKED_TEXT, "Forked")
        self.assertIsInstance(effect, DealDamage)
        if not isinstance(effect, DealDamage):
            self.fail("not a damage effect")
        self.assertEqual(effect.to, "divided")
        self.assertEqual(
            (targets[0].what, targets[0].count, targets[0].minimum), ("any", 3, 1)
        )


class TestMultipleTargets(unittest.TestCase):
    """F11 at run time: choosing, resolving and fizzling with several targets."""

    def setUp(self) -> None:
        """Set up a two-player game, p0 active with mana."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        give_mana(self.p0, C=3)

    def _spell(self, text: str) -> GameObject:
        effect, targets = parse_effect_text(text, "Spell")
        return card_in_hand(
            self.game,
            self.p0,
            "Spell",
            mana_cost="{1}",
            abilities=[SpellAbility(effect=effect, targets=targets)],
        )

    def test_up_to_two_destroys_the_two_biggest_threats(self) -> None:
        """'Destroy up to two target creatures' picks two distinct enemies."""
        small = creature(self.game, self.p1, "Small", power=1, toughness=1)
        mid = creature(self.game, self.p1, "Mid", power=3, toughness=3)
        big = creature(self.game, self.p1, "Big", power=6, toughness=6)
        spell = self._spell("Destroy up to two target creatures.")
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        item = self.game.stack[-1]
        self.assertEqual(len(item.targets), 2)
        self.assertEqual(len(set(map(id, item.targets))), 2)  # rule 115.3
        settle(self.game)
        self.assertEqual(big.zone, Zone.GRAVEYARD)
        self.assertEqual(mid.zone, Zone.GRAVEYARD)
        self.assertEqual(small.zone, Zone.BATTLEFIELD)

    def test_required_count_must_be_met(self) -> None:
        """'Destroy two target creatures' cannot be cast with one legal target."""
        creature(self.game, self.p1, "Only", power=2, toughness=2)
        spell = self._spell("Destroy two target creatures.")
        self.assertFalse(self.game.cast_spell(self.p0, spell))
        self.assertEqual(spell.zone, Zone.HAND)

    def test_partial_illegality_resolves_the_rest(self) -> None:
        """One of two targets leaving does not stop the spell (608.2b)."""
        a = creature(self.game, self.p1, "A", power=4, toughness=4)
        b = creature(self.game, self.p1, "B", power=4, toughness=4)
        spell = self._spell("Destroy two target creatures.")
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        self.game.move_zone(a, Zone.HAND)  # bounced in response
        settle(self.game)
        self.assertEqual(a.zone, Zone.HAND)
        self.assertEqual(b.zone, Zone.GRAVEYARD)
        self.assertEqual(spell.zone, Zone.GRAVEYARD)

    def test_all_targets_illegal_fizzles(self) -> None:
        """Every target gone: the spell does not resolve (608.2b)."""
        a = creature(self.game, self.p1, "A", power=4, toughness=4)
        spell = self._spell("Destroy up to two target creatures.")
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        self.game.move_zone(a, Zone.HAND)
        resolved: list[str] = []
        self.game.log = lambda kind, **_kw: resolved.append(kind)
        settle(self.game)
        self.assertIn("fizzle", resolved)
        self.assertNotIn("resolve", resolved)

    def test_optional_targets_are_only_good_ones(self) -> None:
        """'up to two' never fills its second slot with the caster's own creature."""
        creature(self.game, self.p0, "Mine", power=5, toughness=5)
        enemy = creature(self.game, self.p1, "Theirs", power=2, toughness=2)
        spell = self._spell("Destroy up to two target creatures.")
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        self.assertEqual(self.game.stack[-1].targets, [enemy])


class TestResolutionRecheck(unittest.TestCase):
    """F7: the full legality check runs again on resolution (608.2b)."""

    def setUp(self) -> None:
        """Set up a two-player game, p0 active with mana."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        give_mana(self.p0, C=2)

    def test_hexproof_gained_in_response_fizzles_the_spell(self) -> None:
        """A target that becomes illegal (hexproof) is no longer affected."""
        enemy = creature(self.game, self.p1, "Shy", power=2, toughness=2)
        spell = card_in_hand(
            self.game,
            self.p0,
            "Murder",
            abilities=[
                SpellAbility(effect=Destroy(), targets=[TargetSpec(what="creature")])
            ],
        )
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        enemy.base.keywords.add("hexproof")  # "in response"
        self.game.bump()
        settle(self.game)
        self.assertEqual(enemy.zone, Zone.BATTLEFIELD)
        self.assertEqual(spell.zone, Zone.GRAVEYARD)

    def test_controller_change_makes_an_opponent_only_target_illegal(self) -> None:
        """'target creature an opponent controls' stolen in response is illegal."""
        enemy = creature(self.game, self.p1, "Stolen", power=2, toughness=2)
        spell = card_in_hand(
            self.game,
            self.p0,
            "Smite",
            abilities=[
                SpellAbility(
                    effect=Destroy(),
                    targets=[TargetSpec(what="creature", controller="opponent")],
                ),
            ],
        )
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        enemy.controller = self.p0  # gained control in response
        self.p1.battlefield.remove(enemy)
        self.p0.battlefield.append(enemy)
        self.game.bump()
        settle(self.game)
        self.assertEqual(enemy.zone, Zone.BATTLEFIELD)


class TestDividedDamage(unittest.TestCase):
    """F9: the division is announced on casting (601.2d)."""

    def setUp(self) -> None:
        """Set up a two-player game, p0 active with mana."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        give_mana(self.p0, C=2)

    def _forked(self) -> GameObject:
        effect, targets = parse_effect_text(_FORKED_TEXT, "Forked")
        return card_in_hand(
            self.game,
            self.p0,
            "Forked",
            abilities=[SpellAbility(effect=effect, targets=targets)],
        )

    def test_division_is_fixed_at_casting(self) -> None:
        """Lethal to the biggest threat, the rest to the face; no wasted point."""
        creature(self.game, self.p1, "Two", power=2, toughness=2)
        three = creature(self.game, self.p1, "Three", power=3, toughness=3)
        spell = self._forked()
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        item = self.game.stack[-1]
        self.assertEqual(item.targets, [three, self.p1])  # 1 point can't kill the 2/2
        self.assertEqual(item.division, [3, 1])

    def test_a_vanished_target_s_share_is_lost(self) -> None:
        """Damage assigned to a target that left is not redistributed."""
        a = creature(self.game, self.p1, "A", power=2, toughness=2)
        b = creature(self.game, self.p1, "B", power=2, toughness=2)
        spell = self._forked()
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        self.assertEqual(self.game.stack[-1].division, [2, 2])
        self.game.move_zone(a, Zone.HAND)
        settle(self.game)
        self.assertEqual(a.zone, Zone.HAND)
        self.assertEqual(b.zone, Zone.GRAVEYARD)
        self.assertEqual(self.p1.life, 40)

    def test_player_is_a_legal_divided_target(self) -> None:
        """With no creatures the whole amount goes to the opponent's face."""
        spell = self._forked()
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        self.assertEqual(self.game.stack[-1].targets, [self.p1])
        settle(self.game)
        self.assertEqual(self.p1.life, 36)


class TestTargetFallbacks(unittest.TestCase):
    """P3: forced self-targeting hits the least valuable permanent."""

    def setUp(self) -> None:
        """Set up a two-player game, p0 active with mana."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        give_mana(self.p0, C=2)

    def test_forced_harmful_target_is_the_least_valuable_own_creature(self) -> None:
        """'Destroy target creature' with only own creatures hits the 1/1."""
        weak = creature(self.game, self.p0, "Weak", power=1, toughness=1)
        strong = creature(self.game, self.p0, "Strong", power=5, toughness=5)
        spell = card_in_hand(
            self.game,
            self.p0,
            "Murder",
            abilities=[
                SpellAbility(effect=Destroy(), targets=[TargetSpec(what="creature")])
            ],
        )
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        settle(self.game)
        self.assertEqual(weak.zone, Zone.GRAVEYARD)
        self.assertEqual(strong.zone, Zone.BATTLEFIELD)

    def test_forced_beneficial_target_is_the_least_valuable_enemy(self) -> None:
        """'+1/+1 counter on target creature' with only enemies picks the 1/1."""
        weak = creature(self.game, self.p1, "Weak", power=1, toughness=1)
        strong = creature(self.game, self.p1, "Strong", power=5, toughness=5)
        spell = card_in_hand(
            self.game,
            self.p0,
            "Grow",
            abilities=[
                SpellAbility(
                    effect=PutCounters("+1/+1", 1),
                    targets=[TargetSpec(what="creature")],
                ),
            ],
        )
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        settle(self.game)
        self.assertEqual(weak.counters.get("+1/+1", 0), 1)
        self.assertEqual(strong.counters.get("+1/+1", 0), 0)

    def test_untap_is_beneficial(self) -> None:
        """Untapping is a favour, so it targets the caster's own creatures."""
        self.assertTrue(_is_beneficial(SpellAbility(effect=Untap(on="target"))))
        self.assertFalse(_is_beneficial(SpellAbility(effect=Destroy())))
