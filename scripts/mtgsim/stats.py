"""Result aggregation: Wilson confidence intervals, per-card win-rate lift,
mulligan/curve reports, JSONL export."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict


def wilson_ci(wins, n, z=1.96):
    """95% Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


class Aggregator:
    def __init__(self, player_names):
        self.names = player_names
        self.records = []

    def add(self, record):
        self.records.append(record)

    # ---- basic -----------------------------------------------------------
    def wins(self, name, seed=None):
        return sum(1 for r in self.records if r["winner"] == name
                   and (seed is None or r.get("seed") == seed))

    def stat_avg(self, name, key):
        vals = [r["players"][name].get(key, 0) for r in self.records]
        return statistics.mean(vals) if vals else 0.0

    # ---- per-card win correlation -----------------------------------------
    def card_lift(self, name, min_games=8):
        """For each card: win rate in games where it was cast vs the deck's
        overall win rate. Returns [(card, cast_games, winrate, lift)]."""
        overall = self.wins(name) / max(1, len(self.records))
        cast_games = defaultdict(int)
        cast_wins = defaultdict(int)
        for r in self.records:
            won = r["winner"] == name
            for card in r["players"][name].get("cards_cast", []):
                cast_games[card] += 1
                if won:
                    cast_wins[card] += 1
        rows = []
        for card, n in cast_games.items():
            if n < min_games:
                continue
            wr = cast_wins[card] / n
            rows.append((card, n, wr, wr - overall))
        rows.sort(key=lambda r: -abs(r[3]))
        return rows

    # ---- per-seed summary ---------------------------------------------------
    def seed_lines(self, seeds, games_per_seed, width):
        """Per-seed win rates + between-seed spread for each player."""
        lines = ["-" * width, f" per-seed win rates ({games_per_seed} "
                              f"games each)"]
        header = f"   {'seed':<8s}" + "".join(
            f"{nm[:16]:>18s}" for nm in self.names)
        lines.append(header)
        rates = {nm: [] for nm in self.names}
        for seed in seeds:
            row = f"   {seed:<8d}"
            for nm in self.names:
                w = self.wins(nm, seed)
                r = w / games_per_seed
                rates[nm].append(r)
                row += f"{100*r:17.1f}%"
            lines.append(row)
        row = f"   {'std':<8s}"
        for nm in self.names:
            sd = statistics.stdev(rates[nm]) if len(rates[nm]) > 1 else 0.0
            row += f"{100*sd:16.1f}pp"
        lines.append(row)
        return lines

    # ---- report ------------------------------------------------------------
    def report(self, seeds, games_per_seed):
        n = len(self.records)
        lines = []
        width = 72
        seed_desc = str(seeds[0]) if len(seeds) == 1 else \
            f"{len(seeds)} seeds ({seeds[0]}..{seeds[-1]})" \
            if seeds == list(range(seeds[0], seeds[0] + len(seeds))) \
            else f"{len(seeds)} seeds ({','.join(map(str, seeds))})"
        lines.append("=" * width)
        lines.append(f" mtgsim v2 - {n} games, seed {seed_desc}, "
                     f"{len(self.names)} players")
        lines.append("=" * width)
        for name in self.names:
            w = self.wins(name)
            lo, hi = wilson_ci(w, n)
            lines.append(f" {name:<38s} {w:4d} wins "
                         f"({100*w/n:5.1f} %, 95% CI {100*lo:.0f}-"
                         f"{100*hi:.0f} %)")
        draws = sum(1 for r in self.records if r["winner"] == "draw")
        if draws:
            lines.append(f" {'draws':<38s} {draws:4d}       "
                         f"({100*draws/n:5.1f} %)")
        if len(seeds) > 1:
            lines.extend(self.seed_lines(seeds, games_per_seed, width))
        lines.append("-" * width)
        turns = [r["turns"] for r in self.records]
        lines.append(f" game length: avg {statistics.mean(turns):.1f} "
                     f"turns, median {statistics.median(turns):.0f}")
        reasons = defaultdict(int)
        for r in self.records:
            reasons[r["reason"]] += 1
        lines.append(" endings: " + ", ".join(
            f"{k}={v}" for k, v in sorted(reasons.items(),
                                          key=lambda x: -x[1])))
        lines.append("-" * width)
        keys = ["life", "mulligans", "tokens_created", "tokens_killed",
                "treasures_made", "counters_placed", "blowfly_chain_kills",
                "necroskitter_steals", "grave_robs",
                "kulrath_locked", "drain", "drained_taken", "combat_damage",
                "counterspells_used", "protection_saves", "blink_resets",
                "commander_locks", "removal_used", "wipes_cast",
                "proliferates", "energy_gained"]
        seen = [k for k in keys
                if any(self.stat_avg(nm, k) for nm in self.names)]
        header = " per-game avg          " + "".join(
            f"{nm[:16]:>18s}" for nm in self.names)
        lines.append(header)
        for k in seen:
            row = f"   {k:<20s}"
            for nm in self.names:
                row += f"{self.stat_avg(nm, k):18.2f}"
            lines.append(row)
        lines.append("-" * width)
        lines.append(" card win-rate lift (min 8 casts, top 6 by |lift|)")
        for nm in self.names:
            rows = self.card_lift(nm)[:6]
            lines.append(f"  {nm}:")
            if not rows:
                lines.append("    (not enough samples)")
            for card, cnt, wr, lift in rows:
                lines.append(f"    {card:<38s} cast {cnt:3d}x  "
                             f"wr {100*wr:5.1f} %  lift {100*lift:+5.1f} pp")
        lines.append("=" * width)
        return "\n".join(lines)

    def write_jsonl(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            for r in self.records:
                fh.write(json.dumps(r, default=str) + "\n")
