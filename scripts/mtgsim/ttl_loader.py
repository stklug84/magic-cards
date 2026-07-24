"""Load card characteristics from the TTL knowledge graph (sets/*.ttl and
MagicExternalCards.ttl).

Regex-based extraction (no rdflib dependency) of the fields the simulator
needs: name, mana cost, mana value, types, subtypes, color identity,
power/toughness and oracle text. Results are indexed by full card name and,
for double-faced cards, by the front-face name as well.
"""

from __future__ import annotations

import re
from pathlib import Path

from .cards import CardData

_FIELD_RE = {
    "name": re.compile(r':cardName "([^"]+)"'),
    "cost": re.compile(r':manaCost "([^"]*)"'),
    "mv": re.compile(r':manaValue "(\d+)"'),
    "power": re.compile(r':powerValue "(-?\d+)"'),
    "tough": re.compile(r':toughnessValue "(-?\d+)"'),
}
_TYPE_RE = re.compile(r":hasCardType :(\w+)")
_SUPER_RE = re.compile(r":hasSuperType :(\w+)")
_LOYALTY_RE = re.compile(r':loyalty "(\d+)"')
_SUB_RE = re.compile(r":hasSubType :(\w+)")
_CI_RE = re.compile(r":hasColorIdentity :(\w+)")
_PRODUCES_RE = re.compile(r":producesMana :(\w+)")
_TAPPED_RE = re.compile(r':entersTapped "true"')
_FETCHLAND_RE = re.compile(r':isFetchLand "true"')
_ORACLE_RE = re.compile(
    r':oracleText\s+(?:"""(.*?)"""|"([^"]*)")', re.S)

COLOR_NAME = {"White": "W", "Blue": "U", "Black": "B", "Red": "R",
              "Green": "G"}

# CamelCase subtype individuals -> display words (only ones we act on)
_SUBTYPE_WORDS = {"Plains", "Island", "Swamp", "Mountain", "Forest",
                  "Spacecraft", "Saga", "Equipment", "Vehicle", "Gate",
                  "Treasure", "Food", "Clue"}


def _blocks(text):
    """Yield per-individual blocks (terminated by ' .')."""
    for block in re.split(r"\n\s*\n", text):
        if ":cardName" in block:
            yield block


def load_graph_cards(sets_dir: Path, extra_files: tuple = (),
                     ind2name: dict | None = None) -> dict:
    """Return {card name: CardData} for every card in the graph.

    Reads every TTL file in *sets_dir* plus any *extra_files* (e.g. the
    out-of-collection card graph MagicExternalCards.ttl). Inventoried
    printings win: the first definition of a card name is kept. If
    *ind2name* is given, it is filled with {individual local name: card
    name} for every card individual encountered (all printings).
    """
    index = {}
    files = sorted(Path(sets_dir).glob("*.ttl"))
    files += [Path(f) for f in extra_files if Path(f).exists()]
    for f in files:
        text = f.read_text(encoding="utf-8")
        for block in _blocks(text):
            m = _FIELD_RE["name"].search(block)
            if not m:
                continue
            name = m.group(1)
            if ind2name is not None:
                im = re.match(r":(\w+) rdf:type", block.lstrip())
                if im:
                    ind2name[im.group(1)] = name
            if name in index:
                continue
            card = CardData(name=name, source="graph")
            mc = _FIELD_RE["cost"].search(block)
            card.mana_cost = mc.group(1) if mc else ""
            mv = _FIELD_RE["mv"].search(block)
            card.mv = int(mv.group(1)) if mv else 0
            p = _FIELD_RE["power"].search(block)
            t = _FIELD_RE["tough"].search(block)
            card.power = int(p.group(1)) if p else None
            card.toughness = int(t.group(1)) if t else None
            card.types = set(_TYPE_RE.findall(block))
            card.supertypes = set(_SUPER_RE.findall(block))
            lm = _LOYALTY_RE.search(block)
            card.loyalty = int(lm.group(1)) if lm else None
            card.subtypes = set(_SUB_RE.findall(block))
            # normalize concatenated subtype individuals (:SpacecraftPlanar)
            for s in list(card.subtypes):
                for word in _SUBTYPE_WORDS:
                    if s != word and s.startswith(word):
                        card.subtypes.add(word)
            card.color_identity = {COLOR_NAME[c]
                                   for c in _CI_RE.findall(block)
                                   if c in COLOR_NAME}
            om = _ORACLE_RE.search(block)
            if om:
                card.oracle = om.group(1) or om.group(2) or ""
            # mana facts (:producesMana / :entersTapped / :isFetchLand)
            # are authoritative for lands; the oracle-text regexes in
            # cards.derive_from_oracle remain as fallback only
            if "Land" in card.types:
                produced = _PRODUCES_RE.findall(block)
                if produced:
                    card.behavior["land_colors"] = {
                        COLOR_NAME.get(c, "C") for c in produced}
                if _TAPPED_RE.search(block):
                    card.behavior["enters_tapped"] = True
                if _FETCHLAND_RE.search(block):
                    card.behavior["fetch_land"] = True
            index[name] = card
            # front face of double-faced cards
            if " // " in name:
                front = name.split(" // ")[0]
                index.setdefault(front, card)
    return index
