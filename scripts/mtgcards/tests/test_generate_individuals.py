"""MTG Arena facts the generator writes into each card block.

Every printing's block records the platforms it is available on and, for a
printing on MTG Arena, the name and set code Arena lists it under - the
fields of Arena's import line "N Name (SET) number" - and its Arena ID; its
legalities cover the paper and the MTG Arena formats. These tests pin the
derivation rules, which were checked against Arena's own card database:
Arena lists a multi-faced card under its front face unless it is a split
card, and uses the set's own code where Scryfall has no arena_code.

The generator is a script next to the packages in scripts/, so this module
only runs from a checkout (``python3 -m unittest discover -s
mtgcards/tests -t .`` in scripts/); from an installed wheel it skips.
"""

from __future__ import annotations

import importlib
import unittest
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType


def _import_generator() -> ModuleType | None:
    """Return generate_individuals when it is importable, else None."""
    try:
        return importlib.import_module("generate_individuals")
    except ModuleNotFoundError:
        return None


_GEN = _import_generator()

#: the legality keys every Scryfall card object carries for the tracked formats
_LEGALITIES = dict.fromkeys(
    (
        "commander",
        "standard",
        "modern",
        "legacy",
        "vintage",
        "pioneer",
        "pauper",
        "historic",
        "timeless",
        "alchemy",
        "brawl",
        "standardbrawl",
    ),
    "legal",
)


def _card(**fields: Any) -> dict[str, Any]:  # noqa: ANN401 - Scryfall JSON values are heterogeneous
    """Return a Scryfall-shaped card object for a paper printing on all platforms."""
    card: dict[str, Any] = {
        "name": "Starfield Vocalist",
        "layout": "normal",
        "games": ["paper", "arena", "mtgo"],
        "arena_id": 96652,
        "legalities": dict(_LEGALITIES),
    }
    card.update(fields)
    return card


class _GeneratorTestCase(unittest.TestCase):
    """Skips when the generator script is not importable."""

    def setUp(self) -> None:
        """Bind the generator module and a fresh generation context."""
        if _GEN is None:
            self.skipTest("generate_individuals.py is only importable from scripts/")
        self.gen = _GEN
        self.ctx = self.gen.GenContext(
            vocab={},
            sub_by_label={},
            kw_map={},
            rulings={},
            notes=self.gen.Notes(),
        )
        self.info = self.gen.PrintingInfo(
            set_ind="EdgeOfEternities",
            language="English",
            localized=None,
            arena_set_code="EOE",
        )

    def platform_lines(self, card: dict[str, Any]) -> list[str]:
        """Render the platform and MTG Arena lines of *card*."""
        lines: list[str] = self.gen._platform_lines(card, self.info, self.ctx)  # noqa: SLF001 - the unit under test
        return lines


class TestArenaName(_GeneratorTestCase):
    """The name MTG Arena lists a printing under."""

    def test_single_faced_card_keeps_its_name(self) -> None:
        """A one-faced card is listed under its card name."""
        self.assertEqual(self.gen.arena_name(_card()), "Starfield Vocalist")

    def test_multi_faced_cards_use_the_front_face(self) -> None:
        """Transforming, modal, adventure and prepare cards: front face only."""
        for layout in ("transform", "modal_dfc", "adventure", "prepare"):
            with self.subTest(layout=layout):
                card = _card(name="Leech Collector // Bloodletting", layout=layout)
                self.assertEqual(self.gen.arena_name(card), "Leech Collector")

    def test_split_cards_keep_the_full_name(self) -> None:
        """Split cards such as Rooms keep "A // B" on MTG Arena."""
        card = _card(name="Dollmaker's Shop // Porcelain Gallery", layout="split")
        self.assertEqual(self.gen.arena_name(card), card["name"])


class TestArenaSetCode(_GeneratorTestCase):
    """The set code MTG Arena lists a set's printings under."""

    def test_scryfall_arena_code_wins(self) -> None:
        """Dominaria is DAR on MTG Arena."""
        self.assertEqual(self.gen.arena_set_code({"arena_code": "dar"}, "dom"), "DAR")

    def test_missing_arena_code_falls_back_to_the_set_code(self) -> None:
        """Scryfall leaves arena_code empty for some Arena sets (e.g. FIC)."""
        self.assertEqual(self.gen.arena_set_code({}, "fic"), "FIC")
        self.assertEqual(self.gen.arena_set_code({"arena_code": None}, "j25"), "J25")


class TestPlatformLines(_GeneratorTestCase):
    """Platform availability and the printing's MTG Arena identity."""

    def test_arena_printing(self) -> None:
        """All platforms in order, then the Arena ID, name and set code."""
        self.assertEqual(
            self.platform_lines(_card()),
            [
                "    :isAvailableOn :PaperMagic ,",
                "                   :MTGArena ,",
                "                   :MagicOnline ;",
                '    :arenaId "96652"^^xsd:positiveInteger ;',
                '    :arenaName "Starfield Vocalist" ;',
                '    :arenaSetCode "EOE" ;',
            ],
        )

    def test_printing_not_on_arena_has_no_arena_identity(self) -> None:
        """Without Arena there is no Arena ID, name or set code."""
        lines = self.platform_lines(_card(games=["paper", "mtgo"], arena_id=None))
        self.assertEqual(
            lines,
            [
                "    :isAvailableOn :PaperMagic ,",
                "                   :MagicOnline ;",
            ],
        )

    def test_arena_printing_without_known_id(self) -> None:
        """Scryfall can list a printing on Arena without its Arena ID."""
        lines = self.platform_lines(_card(arena_id=None))
        self.assertFalse(any(":arenaId" in line for line in lines))
        self.assertIn('    :arenaName "Starfield Vocalist" ;', lines)
        self.assertIn('    :arenaSetCode "EOE" ;', lines)

    def test_arena_id_implies_availability_on_arena(self) -> None:
        """An Arena ID puts the printing on MTG Arena even if games omits it."""
        lines = self.platform_lines(_card(games=["paper"]))
        self.assertEqual(
            lines[:2],
            ["    :isAvailableOn :PaperMagic ,", "                   :MTGArena ;"],
        )

    def test_unknown_platform_is_reported_not_emitted(self) -> None:
        """A games value without a :GamePlatform lands in the notes."""
        lines = self.platform_lines(_card(games=["paper", "astral"], arena_id=None))
        self.assertEqual(lines, ["    :isAvailableOn :PaperMagic ;"])
        self.assertEqual(self.ctx.notes.games, {"astral"})

    def test_arena_name_is_escaped(self) -> None:
        """Quotes in a name survive as a valid Turtle string literal."""
        lines = self.platform_lines(_card(name='Kongming, "Sleeping Dragon"'))
        self.assertIn('    :arenaName "Kongming, \\"Sleeping Dragon\\"" ;', lines)


class TestArenaFormatLegalities(_GeneratorTestCase):
    """Legalities cover the paper and the MTG Arena formats."""

    def test_one_mapping_per_tracked_format(self) -> None:
        """Every tracked format, Arena-only ones included, gets a mapping."""
        text = "\n".join(self.gen._legality_lines(_card(), self.ctx))  # noqa: SLF001 - the unit under test
        for _, fmt in self.gen.FORMATS:
            with self.subTest(format=fmt):
                self.assertIn(f":inFormat :{fmt} ;", text)
        for fmt in ("Historic", "Timeless", "Alchemy", "Brawl", "StandardBrawl"):
            with self.subTest(arena_format=fmt):
                self.assertIn(f":inFormat :{fmt} ;", text)
        self.assertTrue(text.endswith(" ."))


if __name__ == "__main__":
    unittest.main()
