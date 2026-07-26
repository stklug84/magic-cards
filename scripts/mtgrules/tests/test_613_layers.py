"""Continuous effects and the layer system (CR 611-613)."""

from __future__ import annotations

import unittest
from typing import TYPE_CHECKING

from mtgrules.abilities import StaticAbility
from mtgrules.layers import ContinuousEffect
from mtgrules.objects import Zone
from mtgrules.tests.helpers import creature, make_game

if TYPE_CHECKING:
    from mtgrules.game import Game
    from mtgrules.objects import Characteristics, GameObject, Player


def anthem(_source: GameObject, dp: int, dt: int, controller: Player) -> StaticAbility:
    """Layer 7c: creatures of *controller* get +dp/+dt."""

    def continuous(_game: Game, src: GameObject) -> list[ContinuousEffect]:
        def applies(_g: Game, obj: GameObject, ch: Characteristics) -> bool:
            return obj.controller is controller and "Creature" in ch.types

        def boost(_g: Game, _o: GameObject, ch: Characteristics) -> None:
            ch.power = (ch.power or 0) + dp
            ch.toughness = (ch.toughness or 0) + dt

        return [
            ContinuousEffect(
                layer=7,
                sublayer="c",
                source=src,
                applies_to=applies,
                apply=boost,
            ),
        ]

    return StaticAbility(continuous=continuous, text="anthem")


class TestLayers(unittest.TestCase):
    """Layer order, dependencies, timestamps, and durations (CR 613)."""

    def setUp(self) -> None:
        """Set up a fresh two-player game."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def test_613_3_layer7c_effects_and_counters(self) -> None:
        """Anthems (7c) and +1/+1 counters stack additively."""
        c = creature(self.game, self.p0, power=2, toughness=2)
        src = creature(self.game, self.p0, name="Anthem Source", power=1, toughness=1)
        src.base.abilities.append(anthem(src, 1, 1, self.p0))
        self.game.bump()
        c.counters["+1/+1"] = 2
        self.game.bump()
        ch = c.chars(self.game)
        self.assertEqual((ch.power, ch.toughness), (5, 5))

    def test_613_4_sublayer_7b_before_7c(self) -> None:
        """A 'becomes 0/1' (7b) effect then +1/+1 counters (7c)."""
        c = creature(self.game, self.p0, power=5, toughness=5)
        c.counters["+1/+1"] = 1

        def set01(_g: Game, _o: GameObject, ch: Characteristics) -> None:
            ch.power = 0
            ch.toughness = 1

        self.game.add_floating_effect(
            ContinuousEffect(
                layer=7,
                sublayer="b",
                source=None,
                applies_to=lambda _g, o, _ch: o is c,
                apply=set01,
                duration="end_of_turn",
            ),
        )
        ch = c.chars(self.game)
        # 7b sets to 0/1, then the counter applies in 7c -> 1/2
        self.assertEqual((ch.power, ch.toughness), (1, 2))

    def test_613_1_layer4_type_change_before_layer6(self) -> None:
        """Layer 4 type changes blank later layer-6 grants.

        Imprisoned-in-the-Moon-style: layer 4 turns the permanent into
        a land, so a layer-6 'all creatures gain flying' no longer
        applies (dependency 613.8).
        """
        c = creature(self.game, self.p0, power=3, toughness=3)

        # timestamp 1: all creatures gain flying (layer 6)
        self.game.add_floating_effect(
            ContinuousEffect(
                layer=6,
                source=None,
                applies_to=lambda _g, _o, ch: "Creature" in ch.types,
                apply=lambda _g, _o, ch: ch.keywords.add("flying"),
                duration="end_of_turn",
            ),
        )

        def to_land(_g: Game, _o: GameObject, ch: Characteristics) -> None:
            ch.types.clear()
            ch.types.add("Land")

        # timestamp 2 (later!): c becomes a land, loses all card types
        self.game.add_floating_effect(
            ContinuousEffect(
                layer=4,
                source=None,
                applies_to=lambda _g, o, _ch: o is c,
                apply=to_land,
                duration="end_of_turn",
            ),
        )
        ch = c.chars(self.game)
        self.assertEqual(ch.types, {"Land"})
        # layer 4 applies before layer 6 regardless of timestamps (613.1),
        # so the flying grant no longer matches
        self.assertNotIn("flying", ch.keywords)

    def test_613_8_dependency_within_a_layer(self) -> None:
        """A dependent same-layer effect waits for its dependency.

        Two layer-4 effects where what B applies to depends on A: A adds
        subtype 'Wolf' to Bears; B turns all Wolves into Birds. B is
        timestamp-earlier but depends on A, so A must be applied first
        (rule 613.8b).
        """
        c = creature(self.game, self.p0, name="Bear", subtypes={"Bear"})

        def wolf_to_bird(_g: Game, _o: GameObject, ch: Characteristics) -> None:
            ch.subtypes.discard("Wolf")
            ch.subtypes.add("Bird")

        eff_b = ContinuousEffect(
            layer=4,
            source=None,
            applies_to=lambda _g, _o, ch: "Wolf" in ch.subtypes,
            apply=wolf_to_bird,
            duration="end_of_turn",
        )
        eff_a = ContinuousEffect(
            layer=4,
            source=None,
            applies_to=lambda _g, _o, ch: "Bear" in ch.subtypes,
            apply=lambda _g, _o, ch: ch.subtypes.add("Wolf"),
            duration="end_of_turn",
        )
        # B has the earlier timestamp, but B depends on A (applying A
        # changes what B applies to) -> A must be applied first (613.8b)
        self.game.add_floating_effect(eff_b)
        self.game.add_floating_effect(eff_a)
        ch = c.chars(self.game)
        self.assertIn("Bird", ch.subtypes)

    def test_613_7_timestamp_order_without_dependency(self) -> None:
        """Independent same-layer setters: the later timestamp wins."""
        c = creature(self.game, self.p0, power=1, toughness=1)

        def setter(p: int, t: int) -> ContinuousEffect:
            def apply(_g: Game, _o: GameObject, ch: Characteristics) -> None:
                ch.power = p
                ch.toughness = t

            return ContinuousEffect(
                layer=7,
                sublayer="b",
                source=None,
                applies_to=lambda _g, o, _ch: o is c,
                apply=apply,
                duration="end_of_turn",
            )

        self.game.add_floating_effect(setter(3, 3))
        self.game.add_floating_effect(setter(5, 5))
        ch = c.chars(self.game)
        self.assertEqual((ch.power, ch.toughness), (5, 5))

    def test_514_2_until_end_of_turn_expires(self) -> None:
        """'Until end of turn' effects end at cleanup (rule 514.2)."""
        c = creature(self.game, self.p0, power=1, toughness=1)

        def pump(_g: Game, _o: GameObject, ch: Characteristics) -> None:
            ch.power = (ch.power or 0) + 3

        self.game.add_floating_effect(
            ContinuousEffect(
                layer=7,
                sublayer="c",
                source=None,
                applies_to=lambda _g, o, _ch: o is c,
                apply=pump,
                duration="end_of_turn",
            ),
        )
        self.assertEqual(c.chars(self.game).power, 4)
        self.game.layers.end_of_turn_cleanup()
        self.assertEqual(c.chars(self.game).power, 1)

    def test_611_3_static_effect_ends_with_source(self) -> None:
        """A static ability's effect ends when its source leaves."""
        c = creature(self.game, self.p0, power=2, toughness=2)
        src = creature(self.game, self.p0, name="Anthem", power=0, toughness=4)
        src.base.abilities.append(anthem(src, 2, 2, self.p0))
        self.game.bump()
        self.assertEqual(c.chars(self.game).power, 4)
        self.game.move_zone(src, Zone.GRAVEYARD)
        self.assertEqual(c.chars(self.game).power, 2)


if __name__ == "__main__":
    unittest.main()
