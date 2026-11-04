"""Card-characteristic extraction from set TTL blocks.

The generator emits multi-valued predicates as Turtle object lists, with
the objects of one predicate separated by ',' across several lines. A
regex that captured a single object per predicate would silently keep only
the first one, so cards would quietly lose subtypes, colour identity and
land mana colours. These tests pin the object-list parsing and keep the
legacy repeated-predicate form loading, so an older graph bundle still
works. They also pin that the MTG Arena lines of a printing on Arena
(:isAvailableOn, :arenaName, ...) leave the extracted characteristics
untouched.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

from mtgcards.ttl_loader import load_graph_cards

if TYPE_CHECKING:
    from mtgcards.cards import CardData

_HEADER = """\
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix :     <urn:test#> .

"""

#: object-list form: the style the generator emits
_OBJECT_LIST = """\
:PiaNalaarChiefMechanic rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Pia Nalaar, Chief Mechanic" ;
    :manaCost "{G}{U}{R}" ;
    :manaValue "3"^^xsd:nonNegativeInteger ;
    :hasSuperType :Legendary ;
    :hasCardType :Artifact ,
                 :Creature ;
    :hasSubType :Human ,
                :Artificer ;
    :hasColorIdentity :Blue ,
                      :Red ,
                      :Green ;
    :powerValue "2"^^xsd:integer ;
    :toughnessValue "4"^^xsd:integer ;
    :oracleText \"\"\"Vehicles you control have haste.\"\"\"@en .
"""

#: legacy form: one predicate line per object
_REPEATED = """\
:PiaNalaarChiefMechanic rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Pia Nalaar, Chief Mechanic" ;
    :manaValue "3"^^xsd:nonNegativeInteger ;
    :hasSuperType :Legendary ;
    :hasCardType :Artifact ;
    :hasCardType :Creature ;
    :hasSubType :Human ;
    :hasSubType :Artificer ;
    :hasColorIdentity :Blue ;
    :hasColorIdentity :Red ;
    :hasColorIdentity :Green ;
    :oracleText \"\"\"Vehicles you control have haste.\"\"\"@en .
"""

_LAND = """\
:AetherHub rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Aether Hub" ;
    :manaValue "0"^^xsd:nonNegativeInteger ;
    :hasCardType :Land ;
    :producesMana :White ,
                  :Blue ,
                  :Black ,
                  :Red ,
                  :Green ;
    :entersTapped "true"^^xsd:boolean ;
    :oracleText \"\"\"{T}: Add {C}.\"\"\"@en .
"""

#: an MTG Arena printing of a transforming card: the platform object list
#: and the Arena fields sit between the card facts and the legalities, and
#: :arenaName holds the front face only
_ARENA_DFC = """\
:LunarchVeteranInnistradMidnightHunt27 rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Lunarch Veteran // Luminous Phantom" ;
    :manaCost "{W}" ;
    :manaValue "1"^^xsd:nonNegativeInteger ;
    :hasCardType :Creature ;
    :hasSubType :Human ,
                :Cleric ,
                :Spirit ;
    :hasColorIdentity :White ;
    :powerValue "1"^^xsd:integer ;
    :toughnessValue "1"^^xsd:integer ;
    :oracleText \"\"\"Whenever another creature enters, you gain 1 life.\"\"\"@en ;
    :isAvailableOn :PaperMagic ,
                   :MTGArena ,
                   :MagicOnline ;
    :arenaId "78354"^^xsd:positiveInteger ;
    :arenaName "Lunarch Veteran" ;
    :arenaSetCode "MID" ;
    :hasLegality
      [ rdf:type :LegalityMapping ; :inFormat :Historic ; :hasLegalityStatus :Legal ] .
"""

_SINGLE = """\
:Forest rdf:type owl:NamedIndividual ,
                  :Card ;
    :cardName "Forest" ;
    :manaValue "0"^^xsd:nonNegativeInteger ;
    :hasSuperType :Basic ;
    :hasCardType :Land ;
    :hasSubType :Forest ;
    :producesMana :Green ;
    :oracleText \"\"\"({T}: Add {G}.)\"\"\"@en .
"""


def _load(*blocks: str) -> dict[str, CardData]:
    """Write *blocks* as one set file and load it through load_graph_cards."""
    with tempfile.TemporaryDirectory() as tmp:
        sets_dir = Path(tmp)
        (sets_dir / "TestSet.ttl").write_text(
            _HEADER + "\n".join(blocks),
            encoding="utf-8",
        )
        return dict(load_graph_cards(sets_dir))


class TestObjectLists(unittest.TestCase):
    """Multi-valued predicates survive the object-list encoding."""

    def test_object_list_keeps_every_object(self) -> None:
        """A comma object list yields all objects, not just the first."""
        card = _load(_OBJECT_LIST)["Pia Nalaar, Chief Mechanic"]
        self.assertEqual(card.types, {"Artifact", "Creature"})
        self.assertEqual(card.subtypes, {"Human", "Artificer"})
        self.assertEqual(card.color_identity, {"U", "R", "G"})
        self.assertEqual(card.supertypes, {"Legendary"})

    def test_repeated_predicate_form_still_loads(self) -> None:
        """The pre-object-list encoding parses to the same characteristics."""
        new = _load(_OBJECT_LIST)["Pia Nalaar, Chief Mechanic"]
        old = _load(_REPEATED)["Pia Nalaar, Chief Mechanic"]
        self.assertEqual(old.types, new.types)
        self.assertEqual(old.subtypes, new.subtypes)
        self.assertEqual(old.color_identity, new.color_identity)
        self.assertEqual(old.supertypes, new.supertypes)

    def test_single_object_predicate(self) -> None:
        """A one-object predicate is unaffected by the list handling."""
        card = _load(_SINGLE)["Forest"]
        self.assertEqual(card.types, {"Land"})
        self.assertEqual(card.subtypes, {"Forest"})
        self.assertEqual(card.supertypes, {"Basic"})
        self.assertEqual(card.behavior["land_colors"], {"G"})

    def test_scalar_fields_still_parse(self) -> None:
        """Object lists do not disturb the neighbouring scalar predicates."""
        card = _load(_OBJECT_LIST)["Pia Nalaar, Chief Mechanic"]
        self.assertEqual(card.mana_cost, "{G}{U}{R}")
        self.assertEqual(card.mv, 3)
        self.assertEqual(card.power, 2)
        self.assertEqual(card.toughness, 4)
        self.assertIn("haste", card.oracle)


class TestArenaFields(unittest.TestCase):
    """The MTG Arena lines of a card block leave the card facts intact."""

    def test_arena_name_does_not_replace_the_card_name(self) -> None:
        """:arenaName is not read as the card name; the DFC keeps both keys."""
        cards = _load(_ARENA_DFC)
        full = cards["Lunarch Veteran // Luminous Phantom"]
        self.assertEqual(full.name, "Lunarch Veteran // Luminous Phantom")
        self.assertIs(cards["Lunarch Veteran"], full)

    def test_platform_list_does_not_leak_into_characteristics(self) -> None:
        """The :isAvailableOn object list touches no extracted field."""
        card = _load(_ARENA_DFC)["Lunarch Veteran // Luminous Phantom"]
        self.assertEqual(card.types, {"Creature"})
        self.assertEqual(card.subtypes, {"Human", "Cleric", "Spirit"})
        self.assertEqual(card.color_identity, {"W"})
        self.assertEqual(card.supertypes, set())
        self.assertEqual((card.mana_cost, card.mv), ("{W}", 1))
        self.assertEqual((card.power, card.toughness), (1, 1))
        self.assertIn("gain 1 life", card.oracle)


class TestLandFacts(unittest.TestCase):
    """Graph-authored land facts read through the object-list form."""

    def test_produces_mana_list(self) -> None:
        """Every produced colour reaches the land_colors behavior."""
        card = _load(_LAND)["Aether Hub"]
        self.assertEqual(card.behavior["land_colors"], {"W", "U", "B", "R", "G"})
        self.assertTrue(card.behavior["enters_tapped"])


if __name__ == "__main__":
    unittest.main()
