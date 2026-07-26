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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

COLORS = "WUBRG"

#: a card has this many colors or more -> multicolored (CR 105.4)
_MULTICOLORED_MIN = 2

KEYWORDS = (
    "flying",
    "trample",
    "deathtouch",
    "lifelink",
    "vigilance",
    "haste",
    "menace",
    "reach",
    "defender",
    "first strike",
    "double strike",
    "hexproof",
    "indestructible",
    "wither",
    "infect",
    "myriad",
    "populate",
    "proliferate",
    "flash",
)

NUM_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "x": 2,
}


@dataclass
class CardData:
    """Printed characteristics plus derived/authored behavior hooks."""

    name: str
    mana_cost: str = ""
    mv: int = 0
    types: set[str] = field(default_factory=set)  # card types
    supertypes: set[str] = field(default_factory=set)
    subtypes: set[str] = field(default_factory=set)
    loyalty: int | None = None
    color_identity: set[str] = field(default_factory=set)
    power: int | None = None
    toughness: int | None = None
    oracle: str = ""
    keywords: set[str] = field(default_factory=set)
    behavior: dict[str, Any] = field(default_factory=dict)  # merged hooks
    source: str = "unknown"  # graph|custom|stub

    # ---- convenience -------------------------------------------------
    @property
    def is_land(self) -> bool:
        """Whether the type line contains Land."""
        return "Land" in self.types

    @property
    def is_creature(self) -> bool:
        """Whether the type line contains Creature."""
        return "Creature" in self.types

    @property
    def is_artifact(self) -> bool:
        """Whether the type line contains Artifact."""
        return "Artifact" in self.types

    @property
    def is_multicolored(self) -> bool:
        """Whether the color identity spans two or more colors."""
        return len(self.color_identity) >= _MULTICOLORED_MIN

    def b(
        self,
        key: str,
        # Hook values are heterogeneous by design (bools, counts, sets,
        # tuples; see behaviors.BEHAVIOR_KEYS), so Any is deliberate here.
        default: Any = None,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        """Look up a behavior hook value, falling back to *default*."""
        return self.behavior.get(key, default)


def _num(word: str) -> int:
    """Convert a count word ('two', '3', 'x') to an integer."""
    word = word.lower().strip()
    if word.isdigit():
        return int(word)
    return NUM_WORDS.get(word, 1)


BASIC_LAND_COLORS = {
    "Plains": "W",
    "Island": "U",
    "Swamp": "B",
    "Mountain": "R",
    "Forest": "G",
}


def _derive_keywords(card: CardData, low: str) -> None:
    """Collect keyword abilities (line-leading or comma-listed)."""
    for kw in KEYWORDS:
        if re.search(rf"(?:^|\n|, |; )({kw})(?:$|,|;| \(|\n| from)", low):
            card.keywords.add(kw)


def _derive_land(card: CardData, low: str, b: dict[str, Any]) -> None:
    """Land mana colors, enters-tapped, and fetch-land classification."""
    # basic land types imply the matching mana ability
    for sub, c in BASIC_LAND_COLORS.items():
        if sub in card.subtypes:
            b.setdefault("land_colors", set()).add(c)
    if not card.is_land:
        return
    if "enters the battlefield tapped" in low or "enters tapped" in low:
        b["enters_tapped"] = True
    if "any color" in low or "any one color" in low:
        b["land_colors"] = set(COLORS)
    else:
        # note: `low` is lowercased, so the symbol classes must be too
        colors = {c.upper() for c in re.findall(r"add [^.\n]*?\{([wubrg])\}", low)}
        colors |= {
            m.upper()
            for pair in re.findall(r"\{([wubrg])\} or \{([wubrg])\}", low)
            for m in pair
        }
        if colors:
            b.setdefault("land_colors", set()).update(colors)
    if (
        "search your library for a basic land" in low
        or "search your library for a plains" in low
    ):
        b["fetch_land"] = True
    if not b.get("land_colors") and not b.get("fetch_land"):
        b["land_colors"] = {"C"}


def _derive_mana_producers(card: CardData, low: str, b: dict[str, Any]) -> None:
    """Mana rocks and dorks ({T}: add ..., 'mana of any color')."""
    m = re.search(r"\{t\}: add (\{[cwubrg]\}(?:\{[cwubrg]\})*)", low)
    if m and not card.is_land:
        syms = re.findall(r"\{([cwubrg])\}", m.group(1))
        b["rock_mana"] = len(syms)
        b["rock_colors"] = {s.upper() for s in syms if s != "c"} or {"C"}
    if not card.is_land and (
        "add one mana of any color" in low or "mana of any color" in low
    ):
        b.setdefault("rock_mana", 1)
        b["rock_colors"] = set(COLORS)


def _derive_ramp(card: CardData, low: str, b: dict[str, Any]) -> None:
    """Ramp spells that search the library for lands."""
    if "Sorcery" not in card.types and "Instant" not in card.types:
        return
    m = re.search(
        r"search your librar(?:y|ies) for (?:up to )?"
        r"(a|an|one|two|three)[^.]*land",
        low,
    )
    if m:
        b["ramp_lands"] = _num(m.group(1))


def _derive_draw(card: CardData, low: str, b: dict[str, Any]) -> None:
    """Draw counts of noncreature, nonland draw spells."""
    m = re.search(r"draw (a|an|one|two|three|four|x) cards?", low)
    if m and "Creature" not in card.types and "Land" not in card.types:
        b["draw_cards"] = _num(m.group(1))


def _derive_interaction(card: CardData, low: str, b: dict[str, Any]) -> None:
    """Counterspell and targeted-removal classification."""
    if re.search(r"counter target [^.]*spell", low):
        b["counterspell"] = True
    if (
        "Instant" not in card.types
        and "Sorcery" not in card.types
        and "Enchantment" not in card.types
    ):
        return
    m = re.search(
        r"(destroy|exile) target (creature|permanent|artifact"
        r"|enchantment|nonland permanent)",
        low,
    )
    if m and "all" not in low.partition(m.group(0))[0][-20:]:
        b["removal"] = True
        b["removal_exile"] = m.group(1) == "exile"
        scope = m.group(2)
        b["removal_scope"] = {
            "creature": "creature",
            "artifact": "art_ench",
            "enchantment": "art_ench",
            "permanent": "any",
            "nonland permanent": "any",
        }.get(scope, "any")


def _derive_wipes(low: str, b: dict[str, Any]) -> None:
    """Board wipes: destroy-all and damage-to-each-creature."""
    if re.search(r"destroy all creatures", low):
        b["wipe"] = {"style": "destroy"}
    m = re.search(r"deals (\d+|x) damage to each creature", low)
    if m:
        b["wipe"] = {"style": "damage", "dmg": _num(m.group(1)) or 5}


def _derive_tokens(card: CardData, text: str, low: str, b: dict[str, Any]) -> None:
    """Token creation, classified by trigger (ETB, dies, upkeep, burst)."""
    for m in re.finditer(
        r"create (a|an|one|two|three|four|five|x|\d+)"
        r"(?: tapped)?[^.]*?(\d+)/(\d+)[^.]*?"
        r"((?:artifact )?)creature tokens?",
        low,
    ):
        n, p, t = _num(m.group(1)), int(m.group(2)), int(m.group(3))
        art = "artifact" in (m.group(4) or "") or "thopter" in m.group(0)
        trig = text[: m.start()].lower()
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


def _derive_anthem(card: CardData, low: str, b: dict[str, Any]) -> None:
    """Enchantment anthems ('creatures you control get +N/+N')."""
    m = re.search(
        r"(creatures?|creature tokens?) you control get "
        r"\+(\d+)/\+(\d+)",
        low,
    )
    if m and "Enchantment" in card.types:
        b["anthem"] = {
            "boost": int(m.group(3)),
            "tokens_only": "token" in m.group(1),
            "art_only": "artifact creature" in low,
        }


def _derive_counter_systems(card: CardData, low: str, b: dict[str, Any]) -> None:
    """Counter sub-systems: proliferate, populate, energy."""
    if "proliferate" in low:
        b["proliferate"] = low.count("proliferate twice") + 1 if "twice" in low else 1
    if "populate" in low and "Creature" not in card.types:
        b.setdefault("populate_per_turn", 1)
    if "{e}" in low:
        m2 = re.search(r"you get (\{e\})+", low)
        b["energy_gain"] = low.count("{e}") if m2 else 1


def derive_from_oracle(card: CardData) -> None:
    """Populate card.keywords and derived behavior keys from oracle text.

    Derived keys never overwrite existing behavior entries (hand-authored
    hooks and custom-card definitions take precedence).
    """
    text = card.oracle or ""
    low = text.lower()
    b: dict[str, Any] = {}

    _derive_keywords(card, low)
    _derive_land(card, low, b)
    _derive_mana_producers(card, low, b)
    _derive_ramp(card, low, b)
    _derive_draw(card, low, b)
    _derive_interaction(card, low, b)
    _derive_wipes(low, b)
    _derive_tokens(card, text, low, b)
    _derive_anthem(card, low, b)
    _derive_counter_systems(card, low, b)

    # merge (existing behavior wins)
    for k, v in b.items():
        card.behavior.setdefault(k, v)


def parse_type_line(
    types: Iterable[str],
    subtypes: Iterable[str],
    card: CardData,
) -> None:
    """Set card (sub)types from already-tokenized type-line words."""
    card.types = set(types)
    card.subtypes = set(subtypes)
