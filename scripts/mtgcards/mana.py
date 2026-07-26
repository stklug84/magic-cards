"""Color-aware mana system.

Costs are parsed from mana cost strings ({2}{G}{G}, hybrid {B/R}, {X}).
Mana sources (lands, rocks, dorks, treasures) carry the set of colors they
can produce; payment uses a scarcity-first greedy assignment which is exact
for the source counts seen in Commander decks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

COLORS = "WUBRG"


@dataclass
class Cost:
    """A parsed mana cost: generic part, colored pips, hybrid pips, X."""

    generic: int = 0
    pips: dict[str, int] = field(default_factory=dict)  # color -> count
    hybrid: list[frozenset[str]] = field(default_factory=list)
    has_x: bool = False

    @property
    def mv(self) -> int:
        """Mana value of the cost (X counts as zero)."""
        return self.generic + sum(self.pips.values()) + len(self.hybrid)


def parse_cost(cost_str: str) -> Cost:
    """Parse a mana cost string like ``{2}{G}{G}`` into a Cost."""
    c = Cost()
    for sym in re.findall(r"\{([^}]+)\}", cost_str or ""):
        if sym.isdigit():
            c.generic += int(sym)
        elif sym == "X":
            c.has_x = True
        elif "/" in sym:
            opts = frozenset(s for s in sym.split("/") if s in COLORS)
            if opts:
                c.hybrid.append(opts)
            else:
                c.generic += 1
        elif sym in COLORS:
            c.pips[sym] = c.pips.get(sym, 0) + 1
        elif sym in ("C", "S"):
            c.generic += 1
    return c


class Source:
    """One mana source on the battlefield."""

    __slots__ = ("colors", "name", "tapped")

    def __init__(self, colors: Iterable[str] | None, name: str = "") -> None:
        """Create a source producing *colors* (colorless when empty)."""
        self.colors = frozenset(colors) if colors else frozenset({"C"})
        self.tapped = False
        self.name = name

    def can(self, color: str) -> bool:
        """Whether this source can produce *color*."""
        return color in self.colors or "ANY" in self.colors


def pay(
    cost: Cost,
    sources: Iterable[Source],
    treasures: int,
    x_value: int = 0,
) -> tuple[list[Source], int] | None:
    """Try to pay `cost` (+x generic) from untapped sources and treasures.

    Returns (sources tapped, treasure count consumed) or None if
    unpayable. Greedy: colored pips are paid from the most restricted
    matching source first; treasures are wildcards spent last.
    """
    avail = [s for s in sources if not s.tapped]
    used: list[Source] = []
    need: list[frozenset[str]] = []
    for color, k in cost.pips.items():
        need.extend([frozenset({color})] * k)
    need.extend(cost.hybrid)
    # most constrained pip first
    need.sort(key=len)
    treasure_budget = treasures
    for opts in need:
        cands = [s for s in avail if (s.colors & opts) or "ANY" in s.colors]
        if cands:
            cands.sort(key=lambda s: len(s.colors))
            src = cands[0]
            avail.remove(src)
            used.append(src)
        elif treasure_budget > 0:
            treasure_budget -= 1
        else:
            return None
    generic = cost.generic + x_value
    # prefer colorless-only sources for generic
    avail.sort(key=lambda s: (len(s.colors & set(COLORS)), len(s.colors)))
    while generic > 0 and avail:
        used.append(avail.pop(0))
        generic -= 1
    if generic > treasure_budget:
        return None
    treasure_spent = (treasures - treasure_budget) + generic
    for s in used:
        s.tapped = True
    return used, treasure_spent


def can_pay(
    cost: Cost,
    sources: Iterable[Source],
    treasures: int,
    x_value: int = 0,
) -> bool:
    """Non-destructive payment check."""
    avail = [s for s in sources if not s.tapped]
    need: list[frozenset[str]] = []
    for color, k in cost.pips.items():
        need.extend([frozenset({color})] * k)
    need.extend(cost.hybrid)
    need.sort(key=len)
    treasure_budget = treasures
    pool = list(avail)
    for opts in need:
        cands = [s for s in pool if (s.colors & opts) or "ANY" in s.colors]
        if cands:
            cands.sort(key=lambda s: len(s.colors))
            pool.remove(cands[0])
        elif treasure_budget > 0:
            treasure_budget -= 1
        else:
            return False
    return len(pool) + treasure_budget >= cost.generic + x_value


def potential(sources: Iterable[Source], treasures: int) -> int:
    """Upper bound on payable mana: untapped sources plus treasures."""
    return sum(1 for s in sources if not s.tapped) + treasures
