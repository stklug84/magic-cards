"""Self-play tuning of the AI policy profiles.

Two modes (both play each pairing in both seatings to cancel the
play/draw advantage):

  round-robin (default): every preset profile against every other on the
  given matchup; prints the win matrix.

      python3 -m mtgrules.selfplay DECK1 [DECK2] --games 10 --seeds 2

  grid tuning (--tune): candidate profiles are variations of --base with
  the given knob values (cartesian product); each candidate plays against
  the plain base profile and candidates are ranked by pooled win rate.

      python3 -m mtgrules.selfplay DECK1 --games 10 \
          --tune aggression=0.7,1.0,1.5 --tune wipe_board_deficit=4,6,9

With one deck file, a mirror match is played (the second seat gets a
'#2'-suffixed copy).
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mtgcards.database import CardDatabase
from mtgcards.deck import (
    Deck,
    load_deck,
)
from mtgcards.stats import wilson_ci

from .adapter import REPO, run_game
from .policy import PROFILES, PolicyProfile, get_profile

_INT_KNOBS = {
    "wipe_board_deficit",
    "counter_value_threshold",
    "mulligan_min_lands",
    "mulligan_max_lands",
    "max_mulligans",
    "race_life",
}
_BOOL_KNOBS = {"hold_reactive_mana"}


def _clone_deck(deck: Deck, suffix: str) -> Deck:
    return Deck(
        name=deck.name + suffix,
        path=deck.path,
        cards=list(deck.cards),
        commander=deck.commander,
    )


def _pair_winrate(decks, db, prof_a, prof_b, games, seeds, turn_cap=40):
    """Win rate of prof_a over `games` per seed, both seatings."""
    wins = total = 0
    for seed in seeds:
        for seating in (0, 1):
            rng = random.Random(seed * 2 + seating)
            profiles = [prof_a, prof_b] if seating == 0 else [prof_b, prof_a]
            a_name = decks[seating].name
            for _ in range(games):
                rec = run_game(decks, db, rng, profiles=profiles, turn_cap=turn_cap)
                total += 1
                if rec["winner"] == a_name:
                    wins += 1
    return wins, total


def _variant(base: PolicyProfile, assignment: dict) -> PolicyProfile:
    label = ",".join(f"{k}={v}" for k, v in assignment.items())
    return dataclasses.replace(base, name=f"{base.name}[{label}]", **assignment)


def parse_tune(specs):
    """--tune KNOB=v1,v2 ... -> list of {knob: value} assignments."""
    axes = []
    for spec in specs:
        knob, _, values = spec.partition("=")
        if not values or not hasattr(PolicyProfile, knob):
            sys.exit(
                f"--tune expects KNOB=v1,v2 with a PolicyProfile field, got {spec!r}",
            )
        parsed = []
        for v in values.split(","):
            if knob in _BOOL_KNOBS:
                parsed.append(v.lower() in ("1", "true", "yes"))
            elif knob in _INT_KNOBS:
                parsed.append(int(v))
            else:
                parsed.append(float(v))
        axes.append([(knob, v) for v in parsed])
    return [dict(combo) for combo in itertools.product(*axes)]


def round_robin(decks, db, games, seeds, turn_cap=40):
    names = sorted(PROFILES)
    print(
        f"=== preset round-robin: {decks[0].name} vs {decks[1].name}, "
        f"{games} games x {len(seeds)} seed(s) x 2 seatings ===",
    )
    header = f"{'row wins vs':<14}" + "".join(f"{n:>14}" for n in names)
    print(header)
    for a in names:
        row = f"{a:<14}"
        for b in names:
            if a == b:
                row += f"{'-':>14}"
                continue
            wins, total = _pair_winrate(
                decks,
                db,
                PROFILES[a],
                PROFILES[b],
                games,
                seeds,
                turn_cap,
            )
            row += f"{100 * wins / total:>13.1f}%"
        print(row)


def grid_tune(decks, db, games, seeds, base_name, tune_specs, turn_cap=40):
    base = get_profile(base_name)
    assignments = parse_tune(tune_specs)
    print(
        f"=== grid tuning vs '{base_name}': {len(assignments)} "
        f"candidates, {games} games x {len(seeds)} seed(s) x 2 "
        f"seatings each ===",
    )
    results = []
    for assignment in assignments:
        cand = _variant(base, assignment)
        wins, total = _pair_winrate(decks, db, cand, base, games, seeds, turn_cap)
        results.append((wins / total, wins, total, cand.name))
    results.sort(reverse=True)
    for rate, wins, total, name in results:
        lo, hi = wilson_ci(wins, total)
        print(
            f"  {100 * rate:5.1f} % ({wins:3d}/{total}) "
            f"CI {100 * lo:.0f}-{100 * hi:.0f} %  {name}",
        )
    best = results[0]
    print(f"best: {best[3]} at {100 * best[0]:.1f} %")


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="mtgrules.selfplay",
        description="Self-play round-robin / grid tuning of AI profiles.",
    )
    ap.add_argument("decks", nargs="+", help="1 deck (mirror) or 2 decks")
    ap.add_argument(
        "--games",
        type=int,
        default=10,
        help="games per seed per seating (default 10)",
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--seeds",
        type=int,
        default=1,
        help="consecutive seeds starting at --seed",
    )
    ap.add_argument("--turn-cap", type=int, default=40)
    ap.add_argument(
        "--base",
        default="default",
        help="base preset for --tune (default 'default')",
    )
    ap.add_argument(
        "--tune",
        action="append",
        default=[],
        metavar="KNOB=v1,v2",
        help="grid axis, repeatable (e.g. aggression=0.7,1.5)",
    )
    args = ap.parse_args(argv)

    if len(args.decks) == 1:
        d = load_deck(args.decks[0])
        decks = [_clone_deck(d, "#1"), _clone_deck(d, "#2")]
    elif len(args.decks) == 2:
        decks = [load_deck(f) for f in args.decks]
    else:
        sys.exit("pass 1 (mirror) or 2 decklist files")
    db = CardDatabase(REPO)
    seeds = [args.seed + i for i in range(args.seeds)]

    if args.tune:
        grid_tune(
            decks,
            db,
            args.games,
            seeds,
            args.base,
            args.tune,
            turn_cap=args.turn_cap,
        )
    else:
        round_robin(decks, db, args.games, seeds, turn_cap=args.turn_cap)


if __name__ == "__main__":
    main()
