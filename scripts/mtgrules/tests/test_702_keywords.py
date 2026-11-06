"""Keyword abilities the compiler recognised but the engine ignored (F10).

Lifelink (702.15), exalted (702.83), fear (702.36), intimidate (702.13),
landwalk (702.14) and flash (702.8a, with the casting-timing rules
117.1a / 307.1 it is an exception to).
"""

from __future__ import annotations

import unittest
from typing import TYPE_CHECKING

from mtgrules.abilities import SpellAbility, TriggeredAbility, TriggerSpec
from mtgrules.combat import CombatPhase, can_block
from mtgrules.compiler import compile_card
from mtgrules.effects import GainLife, Noop
from mtgrules.events import Event, EventType
from mtgrules.objects import GameObject, Zone
from mtgrules.replacements import Replacement
from mtgrules.tests.helpers import (
    FakeRef,
    card_in_hand,
    creature,
    give_mana,
    make_game,
    settle,
)

if TYPE_CHECKING:
    from mtgrules.game import Game
    from mtgrules.objects import Player


class TestLifelink(unittest.TestCase):
    """Rule 702.15b: damage from a lifelink source gains that much life."""

    def setUp(self) -> None:
        """Set up a two-player game in combat."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        self.game.phase = "combat"

    def test_combat_damage_gains_life(self) -> None:
        """An unblocked 2/2 lifelink attacker: 2 damage, +2 life."""
        a = creature(self.game, self.p0, power=2, toughness=2, keywords={"lifelink"})
        a.attacking = self.p1
        phase = CombatPhase(self.game)
        phase.attackers = [a]
        phase.damage_step(first_strike=False)
        settle(self.game)
        self.assertEqual(self.p1.life, 38)
        self.assertEqual(self.p0.life, 42)

    def test_noncombat_damage_gains_life(self) -> None:
        """Lifelink applies to any damage the source deals (not just combat)."""
        src = creature(self.game, self.p0, power=3, toughness=3, keywords={"lifelink"})
        self.game.deal_damage(src, self.p1, 3)
        self.assertEqual(self.p0.life, 43)

    def test_lifelink_gain_can_be_prevented(self) -> None:
        """The gain goes through the life-gain event, so prevention applies."""
        self.game.replacements.floating.append(
            Replacement(
                event_type=EventType.GAIN_LIFE,
                replace=lambda _g, _e: None,  # prevent all life gain
            ),
        )
        src = creature(self.game, self.p0, power=3, toughness=3, keywords={"lifelink"})
        self.game.deal_damage(src, self.p1, 3)
        self.assertEqual(self.p0.life, 40)
        self.assertEqual(self.p1.life, 37)


class TestExalted(unittest.TestCase):
    """Rule 702.83: a creature attacking alone gets +1/+1 per exalted instance."""

    def setUp(self) -> None:
        """Set up a two-player game in combat; p0 is the active player."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        self.game.phase = "combat"

    def test_compiles_to_a_trigger(self) -> None:
        """The exalted keyword line yields a triggered ability."""
        ch = compile_card(FakeRef("Exalted Guy", "Exalted"))
        self.assertIn("exalted", ch.keywords)
        self.assertEqual(sum(isinstance(a, TriggeredAbility) for a in ch.abilities), 1)

    def test_attacking_alone_gets_the_bonus(self) -> None:
        """One attacker + two exalted permanents = +2/+2 until end of turn."""
        attacker = creature(self.game, self.p0, "Knight", power=2, toughness=2)
        for n in ("Angel", "Paladin"):
            ex = GameObject(compile_card(FakeRef(n, "Exalted")), self.p0)
            ex.zone = Zone.BATTLEFIELD
            ex.entered_this_turn = True  # cannot attack this turn
            self.p0.battlefield.append(ex)
        self.game.bump()
        CombatPhase(self.game)._declare_attackers()  # noqa: SLF001 - the declaration step is what fires exalted
        settle(self.game)
        self.assertIs(attacker.attacking, self.p1)
        ch = attacker.chars(self.game)
        self.assertEqual((ch.power, ch.toughness), (4, 4))
        self.game.layers.end_of_turn_cleanup()
        ch = attacker.chars(self.game)
        self.assertEqual((ch.power, ch.toughness), (2, 2))

    def test_two_attackers_get_nothing(self) -> None:
        """Exalted only triggers for a creature that attacks alone."""
        a = creature(self.game, self.p0, "Knight", power=2, toughness=2)
        b = creature(self.game, self.p0, "Squire", power=2, toughness=2)
        ex = GameObject(compile_card(FakeRef("Angel", "Exalted")), self.p0)
        ex.zone = Zone.BATTLEFIELD
        ex.entered_this_turn = True
        self.p0.battlefield.append(ex)
        self.game.bump()
        CombatPhase(self.game)._declare_attackers()  # noqa: SLF001 - see above
        settle(self.game)
        self.assertIs(a.attacking, self.p1)
        self.assertIs(b.attacking, self.p1)
        self.assertEqual(a.chars(self.game).power, 2)
        self.assertEqual(b.chars(self.game).power, 2)

    def test_attack_events_carry_alone(self) -> None:
        """The ATTACKS event says whether the creature attacks alone."""
        seen: list[bool] = []

        def record(_game: Game, _src: GameObject, ev: Event) -> bool:
            seen.append(bool(ev.data.get("alone")))
            return False

        creature(
            self.game,
            self.p0,
            "Watcher",
            abilities=[
                TriggeredAbility(
                    trigger=TriggerSpec(EventType.ATTACKS, condition=record),
                    effect=Noop(),
                ),
            ],
            entered_this_turn=True,
        )
        creature(self.game, self.p0, "Knight", power=2, toughness=2)
        CombatPhase(self.game)._declare_attackers()  # noqa: SLF001 - see above
        self.assertEqual(seen, [True])


class TestEvasion(unittest.TestCase):
    """Fear, intimidate and landwalk restrict who can block (rule 509.1b)."""

    def setUp(self) -> None:
        """Set up a two-player game."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def test_702_36b_fear(self) -> None:
        """Fear: blocked only by artifact and/or black creatures."""
        a = creature(self.game, self.p0, keywords={"fear"}, colors={"B"})
        white = creature(self.game, self.p1, colors={"W"})
        black = creature(self.game, self.p1, colors={"B"})
        robot = creature(self.game, self.p1, types=("Artifact", "Creature"))
        self.assertFalse(can_block(self.game, white, a))
        self.assertTrue(can_block(self.game, black, a))
        self.assertTrue(can_block(self.game, robot, a))

    def test_702_13b_intimidate(self) -> None:
        """Intimidate: blocked only by artifacts and/or creatures sharing a colour."""
        a = creature(self.game, self.p0, keywords={"intimidate"}, colors={"R", "G"})
        blue = creature(self.game, self.p1, colors={"U"})
        green = creature(self.game, self.p1, colors={"G", "W"})
        robot = creature(self.game, self.p1, types=("Artifact", "Creature"))
        self.assertFalse(can_block(self.game, blue, a))
        self.assertTrue(can_block(self.game, green, a))
        self.assertTrue(can_block(self.game, robot, a))

    def test_702_14c_landwalk(self) -> None:
        """Islandwalk: unblockable while the defender controls an Island."""
        a = creature(self.game, self.p0, keywords={"islandwalk"})
        blocker = creature(self.game, self.p1)
        self.assertTrue(can_block(self.game, blocker, a))
        creature(self.game, self.p1, "Island", types=("Land",), subtypes=("Island",))
        self.assertFalse(can_block(self.game, blocker, a))
        # the attacker's own lands do not matter
        creature(self.game, self.p0, "Swamp", types=("Land",), subtypes=("Swamp",))
        b2 = creature(self.game, self.p0, keywords={"swampwalk"})
        self.assertTrue(can_block(self.game, blocker, b2))

    def test_every_basic_landwalk_parses(self) -> None:
        """All five basic landwalks are recognised as keywords."""
        ch = compile_card(
            FakeRef(
                "Walker", "Plainswalk, islandwalk, swampwalk\nMountainwalk\nForestwalk"
            ),
        )
        self.assertEqual(
            {k for k in ch.keywords if k.endswith("walk")},
            {"plainswalk", "islandwalk", "swampwalk", "mountainwalk", "forestwalk"},
        )


class TestCastingTiming(unittest.TestCase):
    """Rules 117.1a / 307.1 with flash (702.8a) as the exception."""

    def setUp(self) -> None:
        """Set up a two-player game; p0 is active in its first main phase."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players
        self.game.phase = "main1"

    def _spell(
        self,
        player: Player,
        *,
        types: tuple[str, ...],
        keywords: frozenset[str] = frozenset(),
    ) -> GameObject:
        card = card_in_hand(
            self.game,
            player,
            "Thing",
            mana_cost="{1}",
            types=types,
            abilities=[SpellAbility(effect=GainLife(1))],
        )
        card.base.keywords |= set(keywords)
        return card

    def test_sorcery_only_in_own_main_phase_with_empty_stack(self) -> None:
        """The active player casts a sorcery in main1; nobody else can."""
        give_mana(self.p0, C=1)
        give_mana(self.p1, C=1)
        mine = self._spell(self.p0, types=("Sorcery",))
        theirs = self._spell(self.p1, types=("Sorcery",))
        self.assertFalse(self.game.cast_spell(self.p1, theirs))
        self.assertTrue(self.game.cast_spell(self.p0, mine))
        # the stack is no longer empty: a second sorcery must wait
        give_mana(self.p0, C=1)
        second = self._spell(self.p0, types=("Sorcery",))
        self.assertFalse(self.game.cast_spell(self.p0, second))

    def test_sorcery_not_in_combat(self) -> None:
        """A sorcery cannot be cast outside the main phases (307.1)."""
        self.game.phase = "combat"
        give_mana(self.p0, C=1)
        card = self._spell(self.p0, types=("Sorcery",))
        self.assertFalse(self.game.cast_spell(self.p0, card))

    def test_instant_any_time_with_priority(self) -> None:
        """Instants ignore the sorcery-timing restriction (117.1a)."""
        self.game.phase = "combat"
        give_mana(self.p1, C=1)
        card = self._spell(self.p1, types=("Instant",))
        self.assertTrue(self.game.cast_spell(self.p1, card))

    def test_flash_creature_at_instant_speed(self) -> None:
        """A creature with flash may be cast whenever an instant could be."""
        self.game.phase = "end"
        give_mana(self.p1, C=1)
        card = self._spell(self.p1, types=("Creature",), keywords=frozenset({"flash"}))
        self.assertTrue(self.game.cast_spell(self.p1, card))
        give_mana(self.p1, C=1)
        slow = self._spell(self.p1, types=("Creature",))
        self.assertFalse(self.game.cast_spell(self.p1, slow))
