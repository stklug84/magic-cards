"""Casting, the stack, priority, and resolution (CR 117, 405, 601-608)."""

from __future__ import annotations

import unittest
from typing import TYPE_CHECKING

from mtgrules.abilities import (
    SpellAbility,
    TargetSpec,
    TriggeredAbility,
    TriggerSpec,
)
from mtgrules.effects import CounterSpell, Destroy, DrawCards, GainLife
from mtgrules.events import Event, EventType
from mtgrules.objects import Zone
from mtgrules.tests.helpers import card_in_hand, creature, give_mana, make_game, settle

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mtgrules.effects import Ctx, Effect
    from mtgrules.game import Game
    from mtgrules.objects import GameObject, Player


class TestStack(unittest.TestCase):
    """Casting, countering, targeting, and trigger ordering."""

    def setUp(self) -> None:
        """Set up a fresh two-player game."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def _sorcery(
        self,
        player: Player,
        effect: Effect,
        targets: Iterable[TargetSpec] = (),
        cost: str = "{1}",
    ) -> GameObject:
        """Put a synthetic sorcery with the given effect in hand."""
        return card_in_hand(
            self.game,
            player,
            name="TestSpell",
            mana_cost=cost,
            abilities=[SpellAbility(effect=effect, targets=list(targets))],
        )

    def test_601_2_cast_and_resolve(self) -> None:
        """A cast spell resolves off the stack and hits the graveyard."""
        spell = self._sorcery(self.p0, DrawCards(2))
        self.p0.library = [
            card_in_hand(self.game, self.p0, name=f"c{i}") for i in range(3)
        ]
        for c in self.p0.library:
            self.p0.hand.remove(c)
            c.zone = Zone.LIBRARY
        give_mana(self.p0, C=1)
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        self.assertEqual(len(self.game.stack), 1)
        self.game.resolve_top()
        self.assertEqual(len(self.p0.hand), 2)
        self.assertEqual(spell.zone, Zone.GRAVEYARD)  # rule 608.2m

    def test_601_2h_cannot_cast_without_mana(self) -> None:
        """Casting fails (and the card stays) when mana can't be paid."""
        spell = self._sorcery(self.p0, DrawCards(1), cost="{4}")
        self.assertFalse(self.game.cast_spell(self.p0, spell))
        self.assertEqual(spell.zone, Zone.HAND)

    def test_608_3_permanent_resolves_to_battlefield(self) -> None:
        """A permanent spell resolves onto the battlefield."""
        c = card_in_hand(
            self.game,
            self.p0,
            name="Bear",
            mana_cost="{1}",
            types=("Creature",),
            power=2,
            toughness=2,
        )
        give_mana(self.p0, C=1)
        self.assertTrue(self.game.cast_spell(self.p0, c))
        self.game.resolve_top()
        self.assertEqual(c.zone, Zone.BATTLEFIELD)

    def test_701_5_counterspell(self) -> None:
        """A counterspell removes the spell before it resolves."""
        victim = self._sorcery(self.p0, DrawCards(1))
        give_mana(self.p0, C=1)
        self.game.cast_spell(self.p0, victim)
        counter = card_in_hand(
            self.game,
            self.p1,
            name="Cancel",
            mana_cost="{1}",
            types=("Instant",),
            abilities=[
                SpellAbility(effect=CounterSpell(), targets=[TargetSpec(what="spell")]),
            ],
        )
        give_mana(self.p1, C=1)
        self.assertTrue(self.game.cast_spell(self.p1, counter))
        self.game.resolve_top()  # counter resolves first
        self.assertEqual(len(self.game.stack), 0)  # victim countered
        self.assertEqual(victim.zone, Zone.GRAVEYARD)
        self.assertEqual(len(self.p0.hand), 0)  # never resolved

    def test_608_2b_fizzle_on_illegal_target(self) -> None:
        """A spell whose only target left fizzles to the graveyard."""
        bear = creature(self.game, self.p1, name="Bear")
        spell = self._sorcery(self.p0, Destroy(), targets=[TargetSpec(what="creature")])
        give_mana(self.p0, C=1)
        self.assertTrue(self.game.cast_spell(self.p0, spell))
        # target dies in response
        self.game.move_zone(bear, Zone.GRAVEYARD)
        self.game.resolve_top()
        self.assertEqual(spell.zone, Zone.GRAVEYARD)

    def test_115_4_cannot_target_hexproof(self) -> None:
        """Hexproof blanks opposing targeting (rule 702.11)."""
        creature(self.game, self.p1, name="Sneaky", keywords={"hexproof"})
        spell = self._sorcery(self.p0, Destroy(), targets=[TargetSpec(what="creature")])
        give_mana(self.p0, C=1)
        # only potential target is hexproof -> cast fails for want of
        # a legal target (rule 601.2c)
        self.assertFalse(self.game.cast_spell(self.p0, spell))

    def test_115_4_own_hexproof_targetable(self) -> None:
        """A player may target their own hexproof creature."""
        creature(self.game, self.p0, name="Mine", keywords={"hexproof"})
        spell = self._sorcery(self.p0, Destroy(), targets=[TargetSpec(what="creature")])
        give_mana(self.p0, C=1)
        self.assertTrue(self.game.cast_spell(self.p0, spell))

    def test_603_3b_apnap_trigger_order(self) -> None:
        """APNAP: active player's triggers go on the stack first.

        Both players' triggers: the active player's go on the stack
        first, so the nonactive player's resolve first (LIFO).
        """
        order: list[str] = []

        class Probe(GainLife):
            """A life-gain effect that records its resolution order."""

            def __init__(self, tag: str) -> None:
                """Tag the probe."""
                super().__init__(amount=0)
                self.tag = tag

            def resolve(self, game: Game, ctx: Ctx) -> None:
                """Record the tag instead of gaining life."""
                del game, ctx
                order.append(self.tag)

        for player, tag in ((self.p0, "active"), (self.p1, "nonactive")):
            c = creature(self.game, player, name=f"w_{tag}")
            c.base.abilities.append(
                TriggeredAbility(
                    trigger=TriggerSpec(
                        EventType.BEGIN_STEP,
                        condition=lambda _g, _s, e: e.data.get("step") == "test",
                    ),
                    effect=Probe(tag),
                ),
            )
        self.game.bump()
        self.game.queue_triggers(Event(EventType.BEGIN_STEP, {"step": "test"}))
        settle(self.game)
        self.assertEqual(order, ["nonactive", "active"])

    def test_903_8_commander_tax(self) -> None:
        """Each prior commander cast taxes the next by {2} (rule 903.8)."""
        cmd = card_in_hand(
            self.game,
            self.p0,
            name="General",
            mana_cost="{2}",
            types=("Creature",),
            power=2,
            toughness=2,
        )
        cmd.commander = True
        self.p0.hand.remove(cmd)
        cmd.zone = Zone.COMMAND
        self.p0.command.append(cmd)
        self.p0.commander_obj = cmd
        self.p0.commander_casts = 1  # one previous cast
        give_mana(self.p0, C=2)
        self.assertFalse(self.game.cast_spell(self.p0, cmd, from_command=True))
        give_mana(self.p0, C=2)  # now 4 total
        self.assertTrue(self.game.cast_spell(self.p0, cmd, from_command=True))
        self.assertEqual(self.p0.commander_casts, 2)

    def test_903_9_commander_to_command_zone(self) -> None:
        """A dying commander returns to the command zone (rule 903.9)."""
        cmd = creature(self.game, self.p0, name="General")
        cmd.commander = True
        self.p0.commander_obj = cmd
        self.game.destroy(cmd)
        self.assertEqual(cmd.zone, Zone.COMMAND)


if __name__ == "__main__":
    unittest.main()
