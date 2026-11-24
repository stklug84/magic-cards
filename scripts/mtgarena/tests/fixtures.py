"""Synthetic card data for the offline tests: no network, no Chrome, no Playwright.

A small Arena card dump (``CARDS`` + ``LOC``), a Scryfall bulk extract
(``SCRYFALL``) and set list (``SET_CODES_PAYLOAD``), a collection response
(``PAYLOAD``) and a picks file (``PICKS``), each entry chosen to exercise one
rule of the resolution.
"""

from __future__ import annotations

from typing import Any

from mtgarena import catalog, render, untapped

#: a JSON object as the sources deliver it
Json = dict[str, Any]


def _title(title_id: int, text: str) -> Json:
    """One entry of ``loc_en.json``."""
    return {"id": title_id, "text": text}


def _arena(
    grpid: int,
    title_id: int,
    printing: str,
    rarity: int | None = None,
    **flags: object,
) -> Json:
    """One entry of Untapped's ``cards.json``; *printing* is ``"SET number"``.

    Flags such as ``IsRebalanced`` are keywords.
    """
    set_code, number = printing.split()
    card: Json = {
        "grpid": grpid,
        "titleId": title_id,
        "set": set_code,
        "collectorNumber": number,
    }
    if rarity is not None:
        card["rarity"] = rarity
    card.update(flags)
    return card


def _scryfall(
    card_id: str,
    name: str,
    printing: str,
    rarity: str,
    type_line: str,
    **fields: object,
) -> Json:
    """One Scryfall card object; *printing* is ``"set number"``.

    ``arena_id``, ``digital`` and ``lang`` are keywords.
    """
    set_code, number = printing.split()
    card: Json = {
        "object": "card",
        "id": card_id,
        "name": name,
        "set": set_code,
        "collector_number": number,
        "rarity": rarity,
        "type_line": type_line,
        "digital": False,
        "lang": "en",
    }
    card.update(fields)
    return card


LOC = [
    _title(1, ""),
    _title(100, "Byway Barterer"),
    _title(101, "Dusk /// Dawn"),
    _title(102, "Forest"),
    _title(103, "A-Fires of Invention"),
    _title(104, "Jace, Vryn's Prodigy"),
    _title(105, "Jace, Telepath Unbound"),
    _title(106, "Treasure"),
    _title(107, "Only On Arena"),
    _title(108, "Scoured Barrens"),
    _title(109, "Fracture Phoenix"),
    _title(110, "Llanowar Elves"),
    _title(111, "Arena Calls It Differently"),
    _title(112, "Rumor Gatherer"),
    _title(113, "Mountain"),
    _title(114, "A-Gnarlid Colony"),
    _title(115, "Dragon Typhoon"),
    _title(116, "Doubling Season"),
    _title(117, "Cavalier of Dawn"),
    _title(118, "Twice Brewed Potion"),
]
CARDS = [
    _arena(91665, 100, "BLB 129", 4, types=[2]),
    _arena(65001, 101, "AKH 210", 4),
    _arena(70001, 102, "DAR 266", 1, supertypes=[1], types=[5]),
    _arena(80001, 103, "Y22 9", 4, IsRebalanced=True, IsDigitalOnly=True),
    _arena(94287, 104, "PIO 60", 5, linkedFaces=[94288]),
    _arena(94288, 105, "PIO 60", 5, isSecondaryCard=True),
    _arena(99001, 106, "BLB T1", isToken=True),
    _arena(77001, 107, "ANB 5", 2),
    _arena(55555, 999999, "XYZ 1", 2),
    # Arena files some common dual lands under rarity 1 ("land"), like basics;
    # the Basic supertype is absent.
    _arena(79714, 108, "NEO 269", 1, types=[5]),
    # A new set Scryfall has not given arena_ids yet: set + number + name.
    _arena(110001, 109, "FRA 150", 4),
    # The same, through the arena_code map (DAR -> dom).
    _arena(110002, 110, "DAR 168", 2),
    # Same set and number on Scryfall, different name: must NOT match.
    _arena(110003, 111, "KTK 7", 3),
    # A double-faced card: Arena's title is the front face only.
    _arena(110004, 112, "FRA 151", 3),
    # Scryfall's arena_id 110005 points at an Island (a data error); the
    # Mountain is found by set + number.
    _arena(110005, 113, "ANA 4", 1, supertypes=[1], types=[5]),
    # Scryfall files the rebalanced card's arena_id under the original;
    # nothing to fall back to.
    _arena(110006, 114, "Y22 40", 2, IsRebalanced=True),
    # Alchemy under Arena's yearly code: Scryfall has it in ytdm, under another
    # number and without an arena_id.
    _arena(120001, 115, "Y25 10", 4, IsDigitalOnly=True),
    # A promo Arena numbers 0: the only English Doubling Season in Scryfall's
    # pza (PZA -> pza by arena_code).
    _arena(120002, 116, "PZA 0", 5),
    # Numbered 0 as well, but Scryfall lists two printings in spg: no match.
    _arena(120003, 117, "SPG 0", 5),
    # An Alchemy name in two of Scryfall's Alchemy sets: no match.
    _arena(120004, 118, "Y26 5", 3, IsDigitalOnly=True),
    # A yearly Alchemy code searches Alchemy sets only, not the paper Fracture
    # Phoenix in fra.
    _arena(120005, 109, "Y24 9", 4, IsDigitalOnly=True),
    # The name search stays in the card's set: Llanowar Elves is in dom only.
    _arena(120006, 110, "SPG 0", 5),
]
SET_CODES_PAYLOAD: Json = {
    "object": "list",
    "data": [
        {"code": "dom", "arena_code": "DAR", "name": "Dominaria"},
        {"code": "fra", "arena_code": "FRA", "name": "Reality Fracture"},
        {"code": "xana", "arena_code": "ANA", "name": "Arena New Player Experience"},
        {"code": "lea", "name": "Limited Edition Alpha"},
        {"code": "pza", "arena_code": "PZA", "name": "TMNT Source Material"},
        {"code": "spg", "name": "Special Guests"},
        # Alchemy sets carry no arena_code; Arena files their cards under Y22, ...
        {"code": "ymid", "name": "Alchemy: Innistrad", "set_type": "alchemy"},
        {"code": "yone", "name": "Alchemy: Phyrexia", "set_type": "alchemy"},
        {"code": "ytdm", "name": "Alchemy: Tarkir", "set_type": "alchemy"},
        {"code": "yfra", "name": "Alchemy: Reality Fracture", "set_type": "alchemy"},
        {"code": "", "arena_code": "BAD", "set_type": "alchemy"},
    ],
}
ELF = "Creature \u2014 Elf Druid"
KNIGHT = "Creature \u2014 Elemental Knight"
SCRYFALL = [
    _scryfall(
        "s-byway",
        "Byway Barterer",
        "blb 129",
        "rare",
        "Creature \u2014 Raccoon Rogue",
        arena_id=91665,
    ),
    _scryfall("s-dusk", "Dusk // Dawn", "akh 210", "rare", "Sorcery", arena_id=65001),
    _scryfall(
        "s-forest",
        "Forest",
        "dom 266",
        "common",
        "Basic Land \u2014 Forest",
        arena_id=70001,
    ),
    _scryfall(
        "s-fires",
        "A-Fires of Invention",
        "ymid 9",
        "rare",
        "Enchantment",
        arena_id=80001,
        digital=True,
    ),
    _scryfall(
        "s-jace",
        "Jace, Vryn's Prodigy // Jace, Telepath Unbound",
        "pio 60",
        "mythic",
        "Legendary Creature \u2014 Human Wizard // Legendary Planeswalker \u2014 Jace",
        arena_id=94287,
    ),
    _scryfall("s-no-arena", "Black Lotus", "lea 232", "rare", "Artifact"),
    _scryfall(
        "s-barrens", "Scoured Barrens", "neo 269", "common", "Land", arena_id=79714
    ),
    # no arena_id yet (new set); reachable by set + number
    _scryfall("s-phoenix", "Fracture Phoenix", "fra 150", "rare", "Creature"),
    _scryfall(
        "s-rumor",
        "Rumor Gatherer // Rumor Spreader",
        "fra 151",
        "uncommon",
        "Creature // Creature",
    ),
    # a German printing listed first must not shadow the English one in the
    # set+number index
    _scryfall("s-elves-de", "Llanowarelfen", "dom 168", "common", ELF, lang="de"),
    _scryfall("s-elves", "Llanowar Elves", "dom 168", "common", ELF),
    # same set + number as Arena's 110003, but a different card
    _scryfall("s-ktk7", "Something Else Entirely", "ktk 7", "uncommon", "Instant"),
    # arena_id data errors
    _scryfall(
        "s-wrong-island",
        "Island",
        "xana 2",
        "common",
        "Basic Land \u2014 Island",
        arena_id=110005,
        digital=True,
    ),
    _scryfall(
        "s-ana-mountain",
        "Mountain",
        "xana 4",
        "common",
        "Basic Land \u2014 Mountain",
        digital=True,
    ),
    _scryfall(
        "s-gnarlid",
        "Gnarlid Colony",
        "znr 40",
        "common",
        "Creature \u2014 Beast",
        arena_id=110006,
    ),
    # Alchemy printings without arena_ids, numbered differently from Arena
    _scryfall(
        "s-typhoon", "Dragon Typhoon", "ytdm 12", "rare", "Instant", digital=True
    ),
    _scryfall(
        "s-potion-yone",
        "Twice Brewed Potion",
        "yone 3",
        "uncommon",
        "Artifact",
        digital=True,
    ),
    _scryfall(
        "s-potion-yfra",
        "Twice Brewed Potion",
        "yfra 7",
        "uncommon",
        "Artifact",
        digital=True,
    ),
    # promos Arena numbers 0; a Japanese row (kept for its arena_id) must not
    # make the pza name ambiguous
    _scryfall("s-doubling-pza", "Doubling Season", "pza 11", "mythic", "Enchantment"),
    _scryfall(
        "s-doubling-ja",
        "Doubling Season",
        "pza 11",
        "mythic",
        "Enchantment",
        arena_id=120099,
        lang="ja",
    ),
    _scryfall("s-doubling-rav", "Doubling Season", "rav 158", "rare", "Enchantment"),
    _scryfall("s-cavalier-84", "Cavalier of Dawn", "spg 84", "mythic", KNIGHT),
    _scryfall("s-cavalier-94", "Cavalier of Dawn", "spg 94", "mythic", KNIGHT),
]
PAYLOAD: Json = {
    "cards": [
        {"grpid": 91665, "quantity": 4},
        {"grpid": 65001, "quantity": 1},
        {"grpid": 70001, "quantity": 20},
        {"grpid": 80001, "quantity": 2},
        {"grpid": 94287, "quantity": 1},
        {"grpid": 77001, "quantity": 3},
        {"grpid": 55555, "quantity": 1},
        {"grpid": 12345, "quantity": 2},
        {"grpid": 91665, "quantity": 0},
    ],
    "last_updated": "2026-10-01T12:00:00Z",
}

#: the picks file: the SPG Cavalier of Dawn Scryfall lists twice, and a pick that
#: overrides the set-and-name match of the PZA Doubling Season (pza 11)
PICKS = [
    {
        "arena_id": 120003,
        "name": "Cavalier of Dawn",
        "set": "SPG",
        "collector_number": "84",
        "note": "Arena numbers it 0",
    },
    {
        "arena_id": 120002,
        "name": "Doubling Season",
        "set": "rav",
        "collector_number": "158",
    },
]


def owned(*pairs: tuple[int, int]) -> Json:
    """Return a collection response owning ``(grpid, quantity)`` pairs."""
    return {"cards": [{"grpid": grpid, "quantity": qty} for grpid, qty in pairs]}


def make_catalog(*, scryfall: bool = True) -> catalog.Catalog:
    """Return the catalog of the synthetic sources, with or without Scryfall."""
    arena = catalog.parse_arena_cards(CARDS, LOC)
    if not scryfall:
        return catalog.Catalog(arena, catalog.ScryfallIndex.empty())
    return catalog.Catalog(
        arena,
        catalog.parse_scryfall_cards(SCRYFALL),
        catalog.parse_set_codes(SET_CODES_PAYLOAD),
        catalog.parse_alchemy_sets(SET_CODES_PAYLOAD),
    )


def make_snapshot(payload: Json = PAYLOAD, *, scryfall: bool = True) -> render.Snapshot:
    """Return the snapshot ``pull`` would build from *payload*."""
    cat = make_catalog(scryfall=scryfall)
    entries = untapped.validate_collection(payload)
    return render.build_snapshot([cat.resolve(e.arena_id, e.quantity) for e in entries])
