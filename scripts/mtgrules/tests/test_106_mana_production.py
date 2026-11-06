"""Mana production (CR 106, 605): fixes F1 and F6.

F1: a source that says "Add {B} or {R}" (or "Add one mana of any color")
produces ONE mana of a chosen type; only "Add {B}{R}" produces two. The
engine used to add every listed type, so dual lands made two mana.

F6: "Add one mana of any color in your commander's color identity"
respects the identity; the compiler used to flag it as "any color" too,
which bypassed the identity restriction.
"""

from __future__ import annotations

import unittest
from typing import TYPE_CHECKING

from mtgrules.abilities import ActivatedAbility
from mtgrules.compiler import compile_card
from mtgrules.effects import AddMana, Ctx
from mtgrules.manasys import parse_cost
from mtgrules.objects import GameObject, Zone
from mtgrules.tests.helpers import FakeRef, creature, make_game

if TYPE_CHECKING:
    from mtgrules.game import Game
    from mtgrules.objects import Player


def _mana_abilities(ref: FakeRef) -> list[ActivatedAbility]:
    ch = compile_card(ref)
    return [
        a for a in ch.abilities if isinstance(a, ActivatedAbility) and a.is_mana_ability
    ]


def _add_mana(ab: ActivatedAbility) -> AddMana:
    if not isinstance(ab.effect, AddMana):
        msg = f"not a plain mana ability: {ab.effect!r}"
        raise TypeError(msg)
    return ab.effect


def _source(
    game: Game, player: Player, effect: AddMana, name: str = "Source"
) -> GameObject:
    return creature(
        game,
        player,
        name,
        types=("Land",),
        abilities=[
            ActivatedAbility(tap_cost=True, is_mana_ability=True, effect=effect)
        ],
    )


def _ability(obj: GameObject) -> ActivatedAbility:
    ab = obj.base.abilities[0]
    if not isinstance(ab, ActivatedAbility):
        msg = f"not an activated ability: {ab!r}"
        raise TypeError(msg)
    return ab


class TestCompiledMana(unittest.TestCase):
    """What the compiler makes of the oracle 'Add ...' templates."""

    def test_or_is_a_choice_of_one(self) -> None:
        """'{T}: Add {U} or {B}.' produces one mana of either type."""
        [ab] = _mana_abilities(
            FakeRef(
                "Talisman", "{T}: Add {U} or {B}.", types={"Artifact"}, mana_cost="{2}"
            ),
        )
        eff = _add_mana(ab)
        self.assertEqual(eff.types, ("U", "B"))
        self.assertTrue(eff.choice)
        self.assertEqual(eff.amount, 1)

    def test_three_way_choice(self) -> None:
        """'{T}: Add {W}, {U}, or {B}.' is a three-way choice of one."""
        [ab] = _mana_abilities(
            FakeRef("Tri", "{T}: Add {W}, {U}, or {B}.", types={"Land"})
        )
        eff = _add_mana(ab)
        self.assertEqual(eff.types, ("W", "U", "B"))
        self.assertTrue(eff.choice)

    def test_sequence_adds_all(self) -> None:
        """'{T}: Add {U}{B}.' (a bounce land) really produces two mana."""
        [ab] = _mana_abilities(FakeRef("Aqueduct", "{T}: Add {U}{B}.", types={"Land"}))
        eff = _add_mana(ab)
        self.assertEqual(eff.types, ("U", "B"))
        self.assertFalse(eff.choice)

    def test_sol_ring_stays_two_colorless(self) -> None:
        """'{T}: Add {C}{C}.' is two colorless mana."""
        [ab] = _mana_abilities(
            FakeRef(
                "Sol Ring", "{T}: Add {C}{C}.", types={"Artifact"}, mana_cost="{1}"
            ),
        )
        self.assertEqual(_add_mana(ab).types, ("C", "C"))
        self.assertFalse(_add_mana(ab).choice)

    def test_n_mana_of_any_one_color(self) -> None:
        """'{T}: Add three mana of any one color.' is any-color with amount 3."""
        [ab] = _mana_abilities(
            FakeRef(
                "Gilded Lotus",
                "{T}: Add three mana of any one color.",
                types={"Artifact"},
                mana_cost="{5}",
            ),
        )
        eff = _add_mana(ab)
        self.assertTrue(eff.any_color)
        self.assertEqual(eff.amount, 3)

    def test_commander_identity_is_not_any_color(self) -> None:
        """Identity mana is flagged as identity only (F6)."""
        [ab] = _mana_abilities(
            FakeRef(
                "Arcane Signet",
                "{T}: Add one mana of any color in your commander's color identity.",
                types={"Artifact"},
                mana_cost="{2}",
            ),
        )
        eff = _add_mana(ab)
        self.assertTrue(eff.commander_identity)
        self.assertFalse(eff.any_color)

    def test_graph_dual_land_is_a_choice(self) -> None:
        """A land known only by its graph colours offers one of them."""
        [ab] = _mana_abilities(
            FakeRef(
                "Canyon Slough",
                "",
                types={"Land"},
                mana_cost="",
                behavior={"land_colors": {"B", "R"}},
            ),
        )
        eff = _add_mana(ab)
        self.assertEqual(eff.types, ("B", "R"))
        self.assertTrue(eff.choice)

    def test_graph_basic_land_is_one_colour(self) -> None:
        """A basic land's graph fallback is a single-type source."""
        [ab] = _mana_abilities(
            FakeRef(
                "Forest",
                "",
                types={"Land"},
                mana_cost="",
                behavior={"land_colors": {"G"}},
            ),
        )
        eff = _add_mana(ab)
        self.assertEqual(eff.types, ("G",))
        self.assertFalse(eff.choice)


class TestPayment(unittest.TestCase):
    """The engine's mana accounting for each source shape."""

    def setUp(self) -> None:
        """Set up a two-player game; p0 is the active player."""
        self.game = make_game()
        self.p0, self.p1 = self.game.players

    def test_dual_land_pays_one_mana(self) -> None:
        """One 'Add {B} or {R}' land cannot pay {2} and floats nothing."""
        land = _source(self.game, self.p0, AddMana(types=("B", "R"), choice=True))
        self.assertEqual(self.game._mana_amount(_ability(land)), 1)  # noqa: SLF001 - the amount is the quantity under test
        self.assertFalse(self.game.can_pay_mana(self.p0, parse_cost("{2}")))
        self.assertTrue(self.game.pay_mana(self.p0, parse_cost("{R}")))
        self.assertTrue(land.tapped)
        self.assertEqual(self.p0.mana_pool.total(), 0)

    def test_dual_land_chooses_the_needed_colour(self) -> None:
        """The same land pays {B} when black is what the cost needs."""
        _source(self.game, self.p0, AddMana(types=("B", "R"), choice=True))
        self.assertTrue(self.game.pay_mana(self.p0, parse_cost("{B}")))
        self.assertEqual(self.p0.mana_pool.total(), 0)

    def test_bounce_land_pays_two(self) -> None:
        """'Add {U}{B}' pays {U}{B} from a single tap."""
        _source(self.game, self.p0, AddMana(types=("U", "B")))
        self.assertTrue(self.game.pay_mana(self.p0, parse_cost("{U}{B}")))
        self.assertEqual(self.p0.mana_pool.total(), 0)

    def test_any_one_color_amount(self) -> None:
        """'three mana of any one color' pays {G}{G}{G}."""
        _source(self.game, self.p0, AddMana(any_color=True, amount=3))
        self.assertTrue(self.game.pay_mana(self.p0, parse_cost("{G}{G}{G}")))
        self.assertEqual(self.p0.mana_pool.total(), 0)

    def test_commander_identity_restricts_colours(self) -> None:
        """Identity mana of a mono-green commander cannot pay {U} (F6)."""
        cmd = GameObject(
            compile_card(FakeRef("Green Legend", "", color_identity={"G"})),
            self.p0,
            card_ref=FakeRef("Green Legend", "", color_identity={"G"}),
        )
        cmd.commander = True
        cmd.zone = Zone.COMMAND
        self.p0.commander_obj = cmd
        src = _source(self.game, self.p0, AddMana(commander_identity=True), "Signet")
        self.assertEqual(self.game.mana_colors_of(src, _ability(src)), frozenset({"G"}))
        self.assertFalse(self.game.can_pay_mana(self.p0, parse_cost("{U}")))
        self.assertTrue(self.game.can_pay_mana(self.p0, parse_cost("{G}")))

    def test_resolving_a_choice_adds_one_mana(self) -> None:
        """AddMana(choice) resolved as an effect adds exactly one mana."""
        AddMana(types=("U", "B"), choice=True).resolve(
            self.game, Ctx(controller=self.p0)
        )
        self.assertEqual(self.p0.mana_pool.total(), 1)
        self.assertEqual(self.p0.mana_pool.mana["U"], 1)  # canonical first

    def test_resolving_identity_mana_uses_canonical_order(self) -> None:
        """Identity mana resolved as an effect picks the WUBRG-first colour."""
        ref = FakeRef("Gruul Legend", "", color_identity={"R", "G"})
        cmd = GameObject(compile_card(ref), self.p0, card_ref=ref)
        cmd.commander = True
        cmd.zone = Zone.COMMAND
        self.p0.commander_obj = cmd
        AddMana(commander_identity=True).resolve(self.game, Ctx(controller=self.p0))
        self.assertEqual(self.p0.mana_pool.mana["R"], 1)
