"""MTG Arena facts the generator writes into each card block.

Every printing's block records the platforms it is available on and, for a
printing on MTG Arena, the name and set code Arena lists it under - the
fields of Arena's import line "N Name (SET) number" - and its Arena ID; its
legalities cover the paper and the MTG Arena formats. These tests pin the
derivation rules, which were checked against Arena's own card database:
Arena lists a multi-faced card under its front face unless it is a split
card, and uses the set's own code where Scryfall has no arena_code.

The keyword tests pin how Scryfall's keyword list becomes keyword and
ability-word triples, including the Comprehensive Rules keyword variants
(typecycling, "[type]walk", multikicker), the subtype-supplement tests
pin when sets/SubTypeSupplement.ttl exists and which subtype class a
supplemented subtype joins, and the naming tests pin that a card individual
never takes the IRI of an ontology term.

The MTG Arena collection tests pin how the snapshot pulled from Untapped.gg
becomes printings (basic lands and unmatched printings left out), joins the
paper printings, overrides Scryfall's Arena identity of the printings it
holds, and renders as a collection held on MTG Arena with one entry per
printing.

The generator is a script next to the packages in scripts/, so this module
only runs from a checkout (``python3 -m unittest discover -s
mtgcards/tests -t .`` in scripts/); from an installed wheel it skips.
"""

from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest import mock

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


#: keyword map as load_vocab() builds it: keyed by label and by individual
_KW_MAP = {
    "Flying": ("hasKeywordAbility", "Flying"),
    "Cycling": ("hasKeywordAbility", "Cycling"),
    "Kicker": ("hasKeywordAbility", "Kicker"),
    "Landwalk": ("hasKeywordAbility", "Landwalk"),
    "Plainswalk": ("hasKeywordAbility", "Plainswalk"),
    "Scry": ("hasKeywordAction", "Scry"),
    "Landfall": ("hasAbilityWord", "Landfall"),
    "Council's dilemma": ("hasAbilityWord", "CouncilsDilemma"),
    "CouncilsDilemma": ("hasAbilityWord", "CouncilsDilemma"),
}


class TestKeywordLines(_GeneratorTestCase):
    """Keywords, CR keyword variants and ability words on a card block."""

    def setUp(self) -> None:
        """Give the context the keyword map of a small vocabulary."""
        super().setUp()
        self.ctx.kw_map = dict(_KW_MAP)

    def keyword_lines(self, *keywords: str) -> list[str]:
        """Render the keyword lines of a card with *keywords*."""
        card = _card(keywords=list(keywords))
        lines: list[str] = self.gen._keyword_lines(card, self.ctx)  # noqa: SLF001 - the unit under test
        return lines

    def test_ability_words_follow_the_keyword_buckets(self) -> None:
        """An ability word never moves a card's keyword lines (rule 207.2c)."""
        self.assertEqual(
            self.keyword_lines("Landfall", "Flying", "Scry"),
            [
                "    :hasKeywordAbility :Flying ;",
                "    :hasKeywordAction :Scry ;",
                "    :hasAbilityWord :Landfall ;",
            ],
        )

    def test_ability_word_label_resolves_by_individual_name(self) -> None:
        """Scryfall's sentence-case label reaches :CouncilsDilemma."""
        self.assertEqual(
            self.keyword_lines("Council's dilemma"),
            ["    :hasAbilityWord :CouncilsDilemma ;"],
        )

    def test_variant_without_its_keyword_resolves_to_it(self) -> None:
        """A multikicker cost is a kicker cost (rule 702.33c)."""
        self.assertEqual(
            self.keyword_lines("Multikicker"), ["    :hasKeywordAbility :Kicker ;"]
        )
        self.assertEqual(self.ctx.notes.keywords, set())

    def test_variant_listed_with_its_keyword_adds_nothing(self) -> None:
        """Typecycling is cycling (rule 702.29f): one :Cycling, no notes."""
        self.assertEqual(
            self.keyword_lines(
                "Flying", "Plainscycling", "Landcycling", "Typecycling", "Cycling"
            ),
            ["    :hasKeywordAbility :Flying ,", "                       :Cycling ;"],
        )
        self.assertEqual(self.ctx.notes.keywords, set())

    def test_variant_with_its_own_individual_keeps_it(self) -> None:
        """:Plainswalk is not folded into :Landwalk; islandwalk is."""
        self.assertEqual(
            self.keyword_lines("Plainswalk", "Islandwalk"),
            [
                "    :hasKeywordAbility :Plainswalk ,",
                "                       :Landwalk ;",
            ],
        )

    def test_unknown_keyword_becomes_a_note(self) -> None:
        """A flavor word has no individual and is reported, not emitted."""
        note = (
            '    # Note: keyword "Water Always Wins" not defined in MagicCardsOntology'
        )
        self.assertEqual(
            self.keyword_lines("Flying", "Water Always Wins"),
            ["    :hasKeywordAbility :Flying ;", note],
        )
        self.assertEqual(self.ctx.notes.keywords, {"Water Always Wins"})


class TestSubtypeSupplement(_GeneratorTestCase):
    """sets/SubTypeSupplement.ttl exists only while the ontology lacks a subtype."""

    def test_stale_supplement_is_removed(self) -> None:
        """No missing subtype: the supplement of an earlier run goes away."""
        with tempfile.TemporaryDirectory() as tmp:
            sets = Path(tmp) / "sets"
            sets.mkdir()
            (sets / "SubTypeSupplement.ttl").write_text("# stale\n")
            imports: list[tuple[str, str, int]] = []
            with mock.patch.object(self.gen, "ROOT", Path(tmp)):
                self.gen._write_subtype_supplement(self.gen.Notes(), imports)  # noqa: SLF001 - the unit under test
            self.assertFalse((sets / "SubTypeSupplement.ttl").exists())
            self.assertEqual(imports, [])

    def test_missing_subtype_is_supplemented_and_imported_first(self) -> None:
        """A subtype the ontology lacks is declared and imported first."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "sets").mkdir()
            notes = self.gen.Notes(subtypes={"Gizmo": ("Gizmo", "ArtifactTypes")})
            imports = [("urn:x", "Aetherdrift.ttl", 3)]
            with mock.patch.object(self.gen, "ROOT", Path(tmp)):
                self.gen._write_subtype_supplement(notes, imports)  # noqa: SLF001 - the unit under test
            text = (Path(tmp) / "sets" / "SubTypeSupplement.ttl").read_text()
            self.assertIn(":Gizmo rdf:type owl:NamedIndividual ,", text)
            self.assertEqual(imports[0][1], "SubTypeSupplement.ttl")


#: the vocabulary extract as build_vocab.py writes it, cut down to a keyword
#: action, a subtype, a class's label and two sets
_VOCAB: dict[str, Any] = {
    "KeywordAction": ["Explore"],
    "SubType": ["Elf"],
    "_labels": {"Card": "Card", "Explore": "Explore", "Elf": "Elf"},
    "_setcodes": {"AER": "AetherRevolt", "WHO": "DoctorWhoCommander"},
}


class TestIndividualName(_GeneratorTestCase):
    """A card never takes the IRI of an ontology term."""

    def name(self, card: str, key: tuple[str, str]) -> str:
        """Return the individual name of a single printing of *card*."""
        set_ind = {"who": "DoctorWhoCommander", "mh3": "ModernHorizons3"}
        inputs = self.gen.GenerateInputs(
            printings={},
            cards={},
            localized={},
            sets_meta={},
            set_ind=set_ind,
            new_sets={},
            existing_keys=set(),
            existing_names=self.gen.reserved_names(_VOCAB, set_ind),
        )
        printing = {
            "name": card,
            "set": key[0],
            "num": key[1],
            "language": "English",
            "count": 1,
        }
        result: str = self.gen._individual_name(printing, key, {card: [key]}, inputs)  # noqa: SLF001 - the unit under test
        return result

    def test_reserved_names_hold_terms_and_sets(self) -> None:
        """Vocabulary individuals, labelled terms and all Set individuals."""
        names = self.gen.reserved_names(_VOCAB, {"zzz": "NewSet"})
        self.assertLessEqual(
            {"Explore", "Elf", "Card", "AetherRevolt", "DoctorWhoCommander", "NewSet"},
            names,
        )

    def test_card_named_like_a_term_takes_set_and_number(self) -> None:
        """Explore and Aether Revolt the cards are not the keyword and the set."""
        self.assertEqual(
            self.name("Explore", ("who", "231")),
            "ExploreDoctorWhoCommander231",
        )
        self.assertEqual(
            self.name("Aether Revolt", ("mh3", "113")),
            "AetherRevoltModernHorizons3113",
        )

    def test_other_cards_keep_the_plain_name(self) -> None:
        """A name no term has stays plain."""
        self.assertEqual(self.name("Aether Spike", ("mh3", "50")), "AetherSpike")


class TestSupplementClass(_GeneratorTestCase):
    """The subtype class a subtype the ontology lacks joins."""

    def test_artifact_creature_subtype_is_a_creature_type(self) -> None:
        """Rule 205.3g closes the artifact types: "Artifact Creature - Android"."""
        self.assertEqual(
            self.gen.supplement_class(["Artifact", "Creature"]), "CreatureTypes"
        )

    def test_kindred_subtype_is_a_creature_type(self) -> None:
        """Kindred cards share the creature types (rule 205.3m)."""
        self.assertEqual(
            self.gen.supplement_class(["Kindred", "Instant"]), "CreatureTypes"
        )

    def test_other_cards_take_their_first_type(self) -> None:
        """A noncreature artifact's new subtype is an artifact type."""
        self.assertEqual(self.gen.supplement_class(["Artifact"]), "ArtifactTypes")
        self.assertEqual(self.gen.supplement_class(["Land"]), "LandTypes")
        self.assertEqual(self.gen.supplement_class([]), "CreatureTypes")


def _owned(**fields: Any) -> dict[str, Any]:  # noqa: ANN401 - snapshot JSON values are heterogeneous
    """Return an entry of arena/collection.json for an owned Scryfall printing."""
    entry: dict[str, Any] = {
        "arena_id": 96652,
        "quantity": 2,
        "name": "Starfield Vocalist",
        "set": "eoe",
        "collector_number": "81",
        "scryfall_id": "0b2b5e1b",
        "arena_name": "Starfield Vocalist",
        "arena_set": "EOE",
        "arena_collector_number": "81",
        "basic": False,
        "digital": False,
        "rebalanced": False,
        "source": "scryfall",
    }
    entry.update(fields)
    return entry


class TestArenaPrintings(_GeneratorTestCase):
    """The MTG Arena snapshot as printings with Arena's identity."""

    def test_owned_printing_keeps_arenas_identity(self) -> None:
        """Keyed by Scryfall's set and number; Arena's ID, name, code, number."""
        owned = _owned(
            arena_id=91234,
            name="Dragon Typhoon",
            arena_name="Dragon Typhoon",
            set="ytdm",
            collector_number="12",
            arena_set="Y25",
            arena_collector_number="10",
        )
        printings = self.gen.arena_printings({"cards": [owned]})
        self.assertEqual(
            printings,
            {
                ("ytdm", "12"): self.gen.ArenaPrinting(
                    name="Dragon Typhoon",
                    arena_id=91234,
                    arena_name="Dragon Typhoon",
                    set_code="Y25",
                    collector_number="10",
                    quantity=2,
                ),
            },
        )

    def test_basic_lands_and_unmatched_printings_are_left_out(self) -> None:
        """MTG Arena grants basics in any number; unmatched ones have no card."""
        snapshot = {
            "cards": [
                _owned(name="Forest", basic=True),
                _owned(arena_id=1, name="A-Galvanic Discharge", scryfall_id=None),
            ],
        }
        self.assertEqual(self.gen.arena_printings(snapshot), {})

    def test_two_arena_ids_of_one_printing_add_up(self) -> None:
        """The copies add up and the lower Arena ID names the printing."""
        snapshot = {"cards": [_owned(arena_id=9, quantity=1), _owned(arena_id=3)]}
        printing = self.gen.arena_printings(snapshot)[("eoe", "81")]
        self.assertEqual((printing.arena_id, printing.quantity), (3, 3))


class TestMergePrintings(_GeneratorTestCase):
    """Both collections' printings, each generated once."""

    def test_paper_row_wins_and_arena_only_printing_joins(self) -> None:
        """A printing both hold keeps its paper row; Arena-only ones are English."""
        paper = {
            ("eoe", "81"): {
                "name": "Starfield Vocalist",
                "set": "eoe",
                "num": "81",
                "language": "German",
                "count": 1,
            },
        }
        arena = self.gen.arena_printings(
            {
                "cards": [
                    _owned(),
                    _owned(
                        arena_id=5,
                        name="Debris Beetle",
                        set="dft",
                        collector_number="199",
                    ),
                ]
            },
        )
        merged = self.gen.merge_printings(paper, arena)
        self.assertEqual(merged[("eoe", "81")], paper[("eoe", "81")])
        self.assertEqual(
            merged[("dft", "199")],
            {
                "name": "Debris Beetle",
                "set": "dft",
                "num": "199",
                "language": "English",
                "count": 0,
            },
        )

    def test_missing_snapshot_stops_the_run(self) -> None:
        """No snapshot, no run: generate would drop the Arena-only individuals."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(self.gen, "ROOT", Path(tmp)),
            self.assertRaises(SystemExit),
        ):
            self.gen.arena_collection_path()


class TestArenaPlatformLines(_GeneratorTestCase):
    """A printing the MTG Arena collection holds takes Arena's own identity."""

    def held(self, **fields: Any) -> list[str]:  # noqa: ANN401 - the dataclass fields vary in type
        """Render the platform lines of a printing the collection holds."""
        values: dict[str, Any] = {
            "name": "Starfield Vocalist",
            "arena_id": 96652,
            "arena_name": "Starfield Vocalist",
            "set_code": "EOE",
            "collector_number": "81",
            "quantity": 1,
        }
        values.update(fields)
        self.info.arena = self.gen.ArenaPrinting(**values)
        return self.platform_lines(_card(collector_number="81", arena_id=None))

    def test_arenas_id_fills_the_gap_scryfall_leaves(self) -> None:
        """Scryfall has no Arena ID for new sets; the collection's grpid is used."""
        self.assertIn('    :arenaId "96652"^^xsd:positiveInteger ;', self.held())

    def test_same_number_adds_no_arena_collector_number(self) -> None:
        """:arenaCollectorNumber only where Arena numbers the printing differently."""
        self.assertFalse(any(":arenaCollectorNumber" in line for line in self.held()))

    def test_alchemy_printing_takes_arenas_code_and_number(self) -> None:
        """Alchemy: Tarkir is Y25 on MTG Arena, Dragon Typhoon its number 10."""
        lines = self.held(arena_id=91234, set_code="Y25", collector_number="10")
        self.assertEqual(
            lines[-3:],
            [
                '    :arenaName "Starfield Vocalist" ;',
                '    :arenaSetCode "Y25" ;',
                '    :arenaCollectorNumber "10" ;',
            ],
        )

    def test_held_printing_is_available_on_arena(self) -> None:
        """Held on MTG Arena implies available there, whatever Scryfall's games say."""
        self.info.arena = self.gen.ArenaPrinting(
            name="Starfield Vocalist",
            arena_id=96652,
            arena_name="Starfield Vocalist",
            set_code="EOE",
            collector_number="81",
            quantity=1,
        )
        lines = self.platform_lines(
            _card(games=["paper"], arena_id=None, collector_number="81")
        )
        self.assertEqual(
            lines[:2],
            ["    :isAvailableOn :PaperMagic ,", "                   :MTGArena ;"],
        )


class TestCollectionGraphs(_GeneratorTestCase):
    """The paper and the MTG Arena collection graphs."""

    def test_arena_entries_have_no_finish_or_condition(self) -> None:
        """One entry per printing, named after the platform, with its copies."""
        arena = self.gen.arena_printings({"cards": [_owned(quantity=3)]})
        card_map = {("EOE", "81"): ("StarfieldVocalist", "EdgeOfEternities.ttl")}
        by_file, total, missing = self.gen.arena_entries(arena, card_map)
        self.assertEqual((total, missing), (3, []))
        block = (
            ":StarfieldVocalistEntryMTGArena rdf:type owl:NamedIndividual ,\n"
            "                  :CollectionEntry ;\n"
            "    :entryCard :StarfieldVocalist ;\n"
            '    :quantity "3"^^xsd:positiveInteger .'
        )
        self.assertEqual(
            by_file["EdgeOfEternities.ttl"],
            [("StarfieldVocalistEntryMTGArena", block)],
        )

    def test_printing_without_individual_is_reported(self) -> None:
        """A printing sets/ does not define is reported, not emitted."""
        arena = self.gen.arena_printings({"cards": [_owned()]})
        by_file, total, missing = self.gen.arena_entries(arena, {})
        self.assertEqual((dict(by_file), total, missing), ({}, 0, [("eoe", "81")]))

    def test_collections_name_their_platform(self) -> None:
        """Paper is held on :PaperMagic, the digital collection on :MTGArena."""
        entries = {"EdgeOfEternities.ttl": [("XEntryMTGArena", ":XEntryMTGArena .")]}
        arena = self.gen.render_collection(self.gen.ARENA_COLLECTION, entries)
        self.assertIn(
            "    :isHeldOn :MTGArena ;\n    :hasCollectionEntry\n  :XEntryMTGArena .\n",
            arena,
        )
        self.assertIn("<urn:stklug84:MagicArenaCollection:", arena)
        paper = self.gen.render_collection(self.gen.PAPER_COLLECTION, {})
        self.assertIn("    :isHeldOn :PaperMagic .\n", paper)
        self.assertNotIn(":hasCollectionEntry", paper)


if __name__ == "__main__":
    unittest.main()
