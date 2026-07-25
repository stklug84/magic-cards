"""Mana, mana pools, and cost payment (CR 106, 118, 202, 605).

Mana exists in mana pools (rule 106.4) and empties at the end of each step
and phase (rule 500.4). Mana abilities (rule 605) resolve immediately
without using the stack (rule 605.3b).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cr import rule

COLORS = "WUBRG"
MANA_TYPES = "WUBRGC"  # rule 106.1b: five colors + colorless


@rule("202.1", "107.4")
@dataclass
class Cost:
    """A parsed mana cost: generic, colored pips, hybrid options, X."""

    generic: int = 0
    pips: dict = field(default_factory=dict)  # color -> count
    hybrid: list = field(default_factory=list)  # list of frozenset(colors)
    colorless: int = 0  # {C} pips, rule 107.4c
    x_count: int = 0  # rule 107.3

    @property
    def mv(self) -> int:
        """Rule 202.3: mana value (X counts as 0 elsewhere than the stack)."""
        return (
            self.generic + sum(self.pips.values()) + len(self.hybrid) + self.colorless
        )

    def with_extra_generic(self, n: int) -> Cost:
        return Cost(
            self.generic + n,
            dict(self.pips),
            list(self.hybrid),
            self.colorless,
            self.x_count,
        )

    def reduced(self, n: int) -> Cost:
        """Cost reduction (rule 601.2f): only the generic part shrinks."""
        return Cost(
            max(0, self.generic - n),
            dict(self.pips),
            list(self.hybrid),
            self.colorless,
            self.x_count,
        )

    def with_x(self, x: int) -> Cost:
        """Rule 601.2b/107.3: chosen X becomes generic mana in the total
        cost.
        """
        return Cost(
            self.generic + x * self.x_count,
            dict(self.pips),
            list(self.hybrid),
            self.colorless,
            0,
        )


@rule("202.1")
def parse_cost(cost_str: str) -> Cost:
    c = Cost()
    for sym in re.findall(r"\{([^}]+)\}", cost_str or ""):
        if sym.isdigit():
            c.generic += int(sym)
        elif sym == "X":
            c.x_count += 1
        elif sym == "C":
            c.colorless += 1
        elif sym == "S":  # snow, rule 107.4g: any mana
            c.generic += 1
        elif "/" in sym:
            opts = frozenset(s for s in sym.split("/") if s in COLORS)
            if opts:
                c.hybrid.append(opts)
            else:  # {2/W} style, pay generic side
                c.generic += 1
        elif sym in COLORS:
            c.pips[sym] = c.pips.get(sym, 0) + 1
    return c


@rule("106.4", "500.4")
class ManaPool:
    """A player's mana pool: counts per mana type."""

    def __init__(self):
        self.mana: dict[str, int] = dict.fromkeys(MANA_TYPES, 0)

    def add(self, mana_type: str, n: int = 1):
        self.mana[mana_type] = self.mana.get(mana_type, 0) + n

    def total(self) -> int:
        return sum(self.mana.values())

    def empty(self):
        """Rule 500.4: pools empty at the end of each step and phase."""
        for t in self.mana:
            self.mana[t] = 0

    @rule("601.2g", "601.2h")
    def can_pay(self, cost: Cost) -> bool:
        return self._solve(cost, commit=False)

    def pay(self, cost: Cost) -> bool:
        return self._solve(cost, commit=True)

    def _solve(self, cost: Cost, commit: bool) -> bool:
        """Exact payment: colored pips first, then hybrid (scarcity-first),
        then {C}, then generic from the most plentiful types.
        """
        pool = dict(self.mana)
        for color, n in cost.pips.items():
            if pool.get(color, 0) < n:
                return False
            pool[color] -= n
        for opts in sorted(cost.hybrid, key=len):
            pick = max(opts, key=lambda c: pool.get(c, 0))
            if pool.get(pick, 0) < 1:
                return False
            pool[pick] -= 1
        if pool.get("C", 0) < cost.colorless:
            return False
        pool["C"] -= cost.colorless
        need = cost.generic
        # rule 106.1b: generic costs can be paid with any type; spend
        # colorless first, then the most plentiful colors
        for t in sorted(pool, key=lambda t: (t != "C", -pool[t])):
            take = min(need, pool[t])
            pool[t] -= take
            need -= take
        if need > 0:
            return False
        if commit:
            self.mana = pool
        return True
