"""Command-line interface.

    python3 scripts/simulate_matchup.py DECK1 DECK2 [DECK3 DECK4] [options]

2-4 decklist files are required. Statistics can be pooled over multiple RNG
seeds (--seeds) to separate matchup signal from seed variance.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from .ai import PROFILES, get_profile
from .database import CardDatabase
from .deck import load_deck
from .game import Game
from .stats import Aggregator

REPO = Path(__file__).resolve().parent.parent.parent


def parse_seeds(base_seed: int, spec: str):
    """--seeds accepts a count ('5' -> base..base+4) or an explicit
    comma-separated list ('1,7,100')."""
    if "," in spec:
        return [int(s) for s in spec.split(",") if s.strip()]
    count = int(spec)
    if count < 1:
        raise argparse.ArgumentTypeError("--seeds must be >= 1")
    return [base_seed + i for i in range(count)]


def build_parser():
    ap = argparse.ArgumentParser(
        prog="simulate_matchup",
        description="Monte-Carlo Commander matchup simulator (2-4 decks).")
    ap.add_argument("decks", nargs="+",
                    help="2-4 decklist files (format: 'N Card Name' lines, "
                         "// comments, commander under a '// Commander' "
                         "section header)")
    ap.add_argument("--games", type=int, default=50,
                    help="games per seed (default 50)")
    ap.add_argument("--seed", type=int, default=42,
                    help="base RNG seed (default 42)")
    ap.add_argument("--seeds", default="1", metavar="N|LIST",
                    help="number of consecutive seeds starting at --seed, "
                         "or an explicit comma-separated list (e.g. "
                         "'5' or '1,7,100'); statistics are pooled and "
                         "reported per seed (default 1)")
    ap.add_argument("--turn-cap", type=int, default=25)
    ap.add_argument("--verbose", action="store_true",
                    help="one-line log per game")
    ap.add_argument("--log-file", metavar="PATH",
                    help="write per-game records (JSONL); adds event logs")
    ap.add_argument("--custom-cards", metavar="PATH",
                    help="JSON file overriding card definitions for "
                         "unreleased/unverified cards (opt-in; by default "
                         "all card data comes from the knowledge graph)")
    ap.add_argument("--profile", action="append", default=[],
                    metavar="SEAT=NAME",
                    help=f"AI profile per seat, e.g. 1=aggressive; "
                         f"profiles: {', '.join(sorted(PROFILES))}")
    ap.add_argument("--engine", choices=("heuristic", "rules"),
                    default="heuristic",
                    help="heuristic: fast Monte-Carlo abstraction "
                         "(default). rules: the CR-grounded rules engine "
                         "(scripts/mtgrules) with a real stack, priority, "
                         "state-based actions and the layer system")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    deck_paths = [Path(d) for d in args.decks]
    if not 2 <= len(deck_paths) <= 4:
        sys.exit("pass 2-4 decklist files")
    for p in deck_paths:
        if not p.exists():
            sys.exit(f"deck file not found: {p}")
    try:
        seeds = parse_seeds(args.seed, args.seeds)
    except ValueError:
        sys.exit(f"invalid --seeds value: {args.seeds!r}")

    if args.engine == "rules":
        from mtgrules.adapter import run_match
        run_match([str(p) for p in deck_paths], games=args.games,
                  seed=seeds[0], turn_cap=max(args.turn_cap, 40),
                  verbose=args.verbose)
        return

    db = CardDatabase(REPO, args.custom_cards)
    decks = [load_deck(p) for p in deck_paths]
    for d in decks:
        if d.commander is None:
            sys.exit(f"{d.path}: no '// Commander' section found")

    profiles = [PROFILES["default"]] * len(decks)
    for spec in args.profile:
        seat, _, name = spec.partition("=")
        idx = int(seat) - 1
        if not 0 <= idx < len(decks):
            sys.exit(f"--profile seat {seat} out of range")
        profiles[idx] = get_profile(name)

    # unknown-card audit
    for d in decks:
        for c in set(d.cards + [d.commander]):
            db.get(c)
    if db.stubbed:
        print(f"warning: {len(db.stubbed)} unknown card(s) stubbed as inert "
              f"3-mana sorceries: {', '.join(sorted(set(db.stubbed))[:8])}"
              f"{' ...' if len(set(db.stubbed)) > 8 else ''}",
              file=sys.stderr)

    agg = None
    for seed in seeds:
        rng = random.Random(seed)
        for g in range(args.games):
            game = Game(decks, db, rng, profiles, turn_cap=args.turn_cap,
                        log_events=bool(args.log_file))
            rec = game.run()
            rec["seed"] = seed
            if agg is None:
                agg = Aggregator(list(rec["players"].keys()))
            agg.add(rec)
            if args.verbose:
                lifes = " ".join(f"{n.split('#')[0][:12]}:{d['life']}"
                                 for n, d in rec["players"].items())
                print(f"  s{seed} g{g+1:>3} T{rec['turns']:>2} "
                      f"winner={rec['winner'][:24]:<24s} "
                      f"({rec['reason']}) {lifes}")

    print(agg.report(seeds, args.games))
    counts = {}
    for d in decks:
        for c in set(d.cards + [d.commander]):
            counts[db.get(c).source] = counts.get(db.get(c).source, 0) + 1
    print(" card data sources: " + ", ".join(
        f"{k}={v}" for k, v in sorted(counts.items())))
    if args.log_file:
        agg.write_jsonl(args.log_file)
        print(f" per-game JSONL written to {args.log_file}")


if __name__ == "__main__":
    main()
