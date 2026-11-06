"""The AI's action choice (fixes P1, P2, P4).

P1: the policy only proposes actions the engine will accept (an illegal
    choice used to count as passing priority).
P2: loyalty legality comes from the engine; keeping a planeswalker alive
    is a separate, explicit preference.
P4: tactical flash play - at the end of an opponent's turn the AI casts
    flash creatures and non-counter instants instead of wasting its mana.
"""

from __future__ import annotations

import unittest

from mtgrules.abilities import ActivatedAbility, SpellAbility
from mtgrules.effects import CounterSpell, DrawCards, Effect, GainLife
from mtgrules.tests.helpers import card_in_hand, creature, give_mana, make_game


def _draw_ability(
    *, tap_cost: bool = False, sorcery_only: bool = False
) -> ActivatedAbility:
    return ActivatedAbility(
        effect=DrawCards(1),
        text="draw",
        tap_cost=tap_cost,
        sorcery_only=sorcery_only,
    )


class TestLegalityPrecheck(unittest.TestCase):
    """P1: the policy never proposes what the engine would refuse."""

    def setUp(self) -> None:
        """Set up a two-player game; p0 is active in its second main phase."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        self.game.phase = "main2"

    def test_summoning_sick_tap_ability_is_not_proposed(self) -> None:
        """A {T} ability of a creature that entered this turn is skipped."""
        sick = creature(
            self.game,
            self.p0,
            "Fresh",
            abilities=[_draw_ability(tap_cost=True)],
            entered_this_turn=True,
        )
        self.assertIsNone(self.game.policy(self.p0).choose_action(self.game, self.p0))
        sick.entered_this_turn = False
        action = self.game.policy(self.p0).choose_action(self.game, self.p0)
        self.assertIsNotNone(action)
        if action is None:
            self.fail("expected an activation")
        self.assertEqual(action[0], "activate")

    def test_sorcery_speed_ability_outside_the_window_is_not_proposed(self) -> None:
        """'Activate only as a sorcery' is skipped while the stack is non-empty."""
        creature(
            self.game,
            self.p0,
            "Slow",
            abilities=[_draw_ability(tap_cost=True, sorcery_only=True)],
        )
        self.assertEqual(
            self.game.policy(self.p0).choose_action(self.game, self.p0)[0],  # type: ignore[index]
            "activate",
        )
        self.game.phase = "combat"
        self.assertIsNone(self.game.policy(self.p0).choose_action(self.game, self.p0))

    def test_additional_cost_that_cannot_be_paid_is_not_proposed(self) -> None:
        """A spell needing a sacrificed creature is held without a creature."""
        self.game.phase = "main1"
        give_mana(self.p0, C=2)
        spell = card_in_hand(
            self.game,
            self.p0,
            "Altar",
            abilities=[SpellAbility(effect=GainLife(5))],
        )
        spell.base.additional_cost = "sacrifice_creature"
        self.assertIsNone(self.game.policy(self.p0).choose_action(self.game, self.p0))
        creature(self.game, self.p0, "Fodder", power=1, toughness=1)
        action = self.game.policy(self.p0).choose_action(self.game, self.p0)
        self.assertEqual(action[0], "cast")  # type: ignore[index]

    def test_proposed_actions_are_accepted_by_the_engine(self) -> None:
        """Whatever the policy proposes, the engine performs."""
        give_mana(self.p0, C=3)
        creature(self.game, self.p0, "Ready", abilities=[_draw_ability(tap_cost=True)])
        card_in_hand(
            self.game, self.p0, "Heal", abilities=[SpellAbility(effect=GainLife(1))]
        )
        self.game.phase = "main1"
        for _ in range(4):
            action = self.game.policy(self.p0).choose_action(self.game, self.p0)
            if action is None:
                break
            self.assertTrue(self.game._perform(self.p0, action), action)  # noqa: SLF001 - the engine's own executor is the oracle here
            while self.game.stack:
                self.game.resolve_top()


class TestLoyalty(unittest.TestCase):
    """P2: legality from the engine, survival as a stated preference."""

    def setUp(self) -> None:
        """Set up p0 active in main1 with a planeswalker at 3 loyalty."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        self.game.phase = "main1"
        self.walker = creature(
            self.game,
            self.p0,
            "Walker",
            power=0,
            toughness=0,
            types=("Planeswalker",),
            abilities=[
                ActivatedAbility(loyalty_cost=-3, effect=DrawCards(1), text="-3: draw"),
            ],
        )
        self.walker.counters["loyalty"] = 3

    def test_lethal_minus_ability_is_legal_but_declined(self) -> None:
        """-3 at 3 loyalty is legal (606.5) yet the AI keeps the walker alive."""
        ab = self.walker.base.abilities[0]
        if not isinstance(ab, ActivatedAbility):
            self.fail("expected an activated ability")
        self.assertTrue(self.game.activation_legal(self.p0, self.walker, ab))
        self.assertIsNone(self.game.policy(self.p0).choose_action(self.game, self.p0))
        self.walker.counters["loyalty"] = 4
        action = self.game.policy(self.p0).choose_action(self.game, self.p0)
        self.assertEqual(action[0], "activate")  # type: ignore[index]

    def test_loyalty_ability_only_in_the_sorcery_window(self) -> None:
        """Outside a main phase with an empty stack nothing is proposed."""
        self.walker.counters["loyalty"] = 5
        self.game.phase = "end"
        self.assertIsNone(self.game.policy(self.p0).choose_action(self.game, self.p0))


class TestTacticalFlash(unittest.TestCase):
    """P4: the end of an opponent's turn is a window for instant-speed plays."""

    def setUp(self) -> None:
        """Set up a two-player game with p1 active in its end step."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        self.game.active_idx = 1
        self.game.phase = "end"
        give_mana(self.p0, C=3)

    def _hand(
        self,
        name: str,
        types: tuple[str, ...],
        effect: Effect,
        keywords: frozenset[str] = frozenset(),
    ) -> None:
        card = card_in_hand(
            self.game,
            self.p0,
            name,
            mana_cost="{1}",
            types=types,
            abilities=[SpellAbility(effect=effect)],
            power=2,
            toughness=2,
        )
        card.base.keywords |= set(keywords)

    def test_flash_creature_is_cast_at_the_opponents_end_step(self) -> None:
        """A flash creature in hand is cast when the opponent's turn ends."""
        self._hand("Ambusher", ("Creature",), GainLife(0), frozenset({"flash"}))
        action = self.game.policy(self.p0).choose_action(self.game, self.p0)
        self.assertIsNotNone(action)
        if action is None:
            self.fail("expected a cast")
        self.assertEqual((action[0], action[1].base.name), ("cast", "Ambusher"))
        self.assertTrue(self.game._perform(self.p0, action))  # noqa: SLF001 - see above

    def test_non_flash_creature_waits(self) -> None:
        """Without flash the creature is held for the AI's own main phase."""
        self._hand("Bear", ("Creature",), GainLife(0))
        self.assertIsNone(self.game.policy(self.p0).choose_action(self.game, self.p0))

    def test_instant_is_cast_but_counterspell_is_held(self) -> None:
        """A useful instant is cast; a counterspell stays in hand."""
        self._hand("Counter", ("Instant",), CounterSpell())
        self.assertIsNone(self.game.policy(self.p0).choose_action(self.game, self.p0))
        self._hand("Heal", ("Instant",), GainLife(3))
        action = self.game.policy(self.p0).choose_action(self.game, self.p0)
        self.assertEqual((action[0], action[1].base.name), ("cast", "Heal"))  # type: ignore[index]

    def test_not_in_the_ai_s_own_end_step_or_in_combat(self) -> None:
        """The window is the *opponent's* end step only."""
        self._hand("Ambusher", ("Creature",), GainLife(0), frozenset({"flash"}))
        self.game.phase = "combat"
        self.assertIsNone(self.game.policy(self.p0).choose_action(self.game, self.p0))
        self.game.phase = "end"
        self.game.active_idx = 0
        self.assertIsNone(self.game.policy(self.p0).choose_action(self.game, self.p0))
        self.assertEqual(len(self.p0.hand), 1)
