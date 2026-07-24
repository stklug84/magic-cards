"""Card data model and oracle-text derivation.

CardData carries printed characteristics (cost, types, P/T, oracle text).
`derive_from_oracle` extracts machine-usable features from oracle text:
keywords, land mana colors, enters-tapped, token creation, draw counts,
removal/counterspell/wipe classification, anthems, proliferate/populate.

Hand-authored behavior hooks (behaviors.py) are merged on top and always win.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

COLORS = "WUBRG"

KEYWORDS = (
    "flying", "trample", "deathtouch", "lifelink", "vigilance", "haste",
    "menace", "reach", "defender", "first strike", "double strike",
    "hexproof", "indestructible", "wither", "infect", "myriad", "populate",
    "proliferate", "flash",
)

NUM_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "x": 2,
}


@dataclass
class CardData:
    name: str
    mana_cost: str = ""
    mv: int = 0
    types: set = field(default_factory=set)       # card types
    supertypes: set = field(default_factory=set)
    subtypes: set = field(default_factory=set)
    loyalty: int | None = None
    color_identity: set = field(default_factory=set)
    power: int | None = None
    toughness: int | None = None
    oracle: str = ""
    keywords: set = field(default_factory=set)
    behavior: dict = field(default_factory=dict)  # merged effect hooks
    source: str = "unknown"                       # graph|custom|stub

    # ---- convenience -------------------------------------------------
    @property
    def is_land(self):
        return "Land" in self.types

    @property
    def is_creature(self):
        return "Creature" in self.types

    @property
    def is_artifact(self):
        return "Artifact" in self.types

    @property
    def is_multicolored(self):
        return len(self.color_identity) >= 2

    def b(self, key, default=None):
        return self.behavior.get(key, default)


def _num(word):
    word = word.lower().strip()
    if word.isdigit():
        return int(word)
    return NUM_WORDS.get(word, 1)


BASIC_LAND_COLORS = {
    "Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R",
    "Forest": "G",
}


def derive_from_oracle(card: CardData) -> None:
    """Populate card.keywords and derived behavior keys from oracle text.

    Derived keys never overwrite existing behavior entries (hand-authored
    hooks and custom-card definitions take precedence).
    """
    text = card.oracle or ""
    low = text.lower()
    b = {}

    # ---- keywords (line-leading or comma-listed) ---------------------
    for kw in KEYWORDS:
        if re.search(rf"(?:^|\n|, |; )({kw})(?:$|,|;| \(|\n| from)", low):
            card.keywords.add(kw)
    # basic land types imply mana ability
    for sub, c in BASIC_LAND_COLORS.items():
        if sub in card.subtypes:
            b.setdefault("land_colors", set()).add(c)

    # ---- lands --------------------------------------------------------
    if card.is_land:
        if "enters the battlefield tapped" in low or "enters tapped" in low:
            b["enters_tapped"] = True
        if "any color" in low or "any one color" in low:
            b["land_colors"] = set(COLORS)
        else:
            # note: `low` is lowercased, so the symbol classes must be too
            colors = {c.upper() for c in
                      re.findall(r"add [^.\n]*?\{([wubrg])\}", low)}
            colors |= {m.upper() for pair in re.findall(
                r"\{([wubrg])\} or \{([wubrg])\}", low) for m in pair}
            if colors:
                b.setdefault("land_colors", set()).update(colors)
        if "search your library for a basic land" in low \
                or "search your library for a plains" in low:
            b["fetch_land"] = True
        if not b.get("land_colors") and not b.get("fetch_land"):
            b["land_colors"] = {"C"}

    # ---- mana rocks / dorks -------------------------------------------
    m = re.search(r"\{t\}: add (\{[cwubrg]\}(?:\{[cwubrg]\})*)", low)
    if m and not card.is_land:
        syms = re.findall(r"\{([cwubrg])\}", m.group(1))
        b["rock_mana"] = len(syms)
        b["rock_colors"] = {s.upper() for s in syms if s != "c"} or {"C"}
    if not card.is_land and ("add one mana of any color" in low
                             or "mana of any color" in low):
        b.setdefault("rock_mana", 1)
        b["rock_colors"] = set(COLORS)

    # ---- ramp spells ----------------------------------------------------
    if ("Sorcery" in card.types or "Instant" in card.types):
        m = re.search(r"search your librar(?:y|ies) for (?:up to )?"
                      r"(a|an|one|two|three)[^.]*land", low)
        if m:
            b["ramp_lands"] = _num(m.group(1))

    # ---- card draw ------------------------------------------------------
    m = re.search(r"draw (a|an|one|two|three|four|x) cards?", low)
    if m and "Creature" not in card.types and "Land" not in card.types:
        b["draw_cards"] = _num(m.group(1))

    # ---- counterspells --------------------------------------------------
    if re.search(r"counter target [^.]*spell", low):
        b["counterspell"] = True

    # ---- removal --------------------------------------------------------
    if "Instant" in card.types or "Sorcery" in card.types \
            or "Enchantment" in card.types:
        m = re.search(r"(destroy|exile) target (creature|permanent|artifact"
                      r"|enchantment|nonland permanent)", low)
        if m and "all" not in low.split(m.group(0))[0][-20:]:
            b["removal"] = True
            b["removal_exile"] = m.group(1) == "exile"
            scope = m.group(2)
            b["removal_scope"] = {
                "creature": "creature", "artifact": "art_ench",
                "enchantment": "art_ench", "permanent": "any",
                "nonland permanent": "any",
            }.get(scope, "any")

    # ---- board wipes ------------------------------------------------------
    if re.search(r"destroy all creatures", low):
        b["wipe"] = {"style": "destroy"}
    m = re.search(r"deals (\d+|x) damage to each creature", low)
    if m:
        b["wipe"] = {"style": "damage", "dmg": _num(m.group(1)) or 5}

    # ---- token creation ----------------------------------------------------
    for m in re.finditer(
            r"create (a|an|one|two|three|four|five|x|\d+)"
            r"(?: tapped)?[^.]*?(\d+)/(\d+)[^.]*?"
            r"((?:artifact )?)creature tokens?", low):
        n, p, t = _num(m.group(1)), int(m.group(2)), int(m.group(3))
        art = "artifact" in (m.group(4) or "") or "thopter" in m.group(0)
        trig = text[:m.start()].lower()
        entry = (n, p, t, art)
        if "when" in trig[-120:] and "enters" in trig[-120:]:
            b.setdefault("etb_tokens", entry)
        elif "dies" in trig[-80:]:
            b.setdefault("death_tokens", entry)
        elif "at the beginning" in trig[-120:]:
            b.setdefault("tokens_per_turn", n)
        elif "Instant" in card.types or "Sorcery" in card.types:
            b.setdefault("burst_tokens", entry)
    if re.search(r"create (a|two|three|x)?[^.]{0,40}treasure token", low):
        if "Instant" in card.types or "Sorcery" in card.types:
            b.setdefault("burst_treasures", 2)
        else:
            b.setdefault("treasures_per_turn", 1)

    # ---- anthems ---------------------------------------------------------
    m = re.search(r"(creatures?|creature tokens?) you control get "
                  r"\+(\d+)/\+(\d+)", low)
    if m and "Enchantment" in card.types:
        b["anthem"] = {"boost": int(m.group(3)),
                       "tokens_only": "token" in m.group(1),
                       "art_only": "artifact creature" in low}

    # ---- counter sub-systems ----------------------------------------------
    if "proliferate" in low:
        b["proliferate"] = low.count("proliferate twice") + 1 \
            if "twice" in low else 1
    if "populate" in low and "Creature" not in card.types:
        b.setdefault("populate_per_turn", 1)
    if "{e}" in low:
        m2 = re.search(r"you get (\{e\})+", low)
        b["energy_gain"] = low.count("{e}") if m2 else 1

    # merge (existing behavior wins)
    for k, v in b.items():
        card.behavior.setdefault(k, v)


def parse_type_line(types, subtypes, card: CardData):
    card.types = set(types)
    card.subtypes = set(subtypes)
