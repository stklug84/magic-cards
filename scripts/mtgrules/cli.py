"""Command-line interface for the CR-grounded matchup simulator.

    python3 scripts/simulate_matchup.py DECK1 DECK2 [DECK3 DECK4] [options]

2-4 decklist files are required. Statistics can be pooled over multiple RNG
seeds (--seeds) to separate matchup signal from seed variance. A single
game can be watched live in a TUI (--watch) or recorded (--viz-file) and
replayed later (--replay).
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mtgcards.database import CardDatabase
from mtgcards.deck import load_deck
from mtgcards.stats import Aggregator

from .adapter import REPO, report_model_coverage, run_game
from .policy import PROFILES, get_profile


def parse_seeds(base_seed: int, spec: str):
    """--seeds accepts a count ('5' -> base..base+4) or an explicit
    comma-separated list ('1,7,100').
    """
    if "," in spec:
        return [int(s) for s in spec.split(",") if s.strip()]
    count = int(spec)
    if count < 1:
        msg = "--seeds must be >= 1"
        raise argparse.ArgumentTypeError(msg)
    return [base_seed + i for i in range(count)]


def build_parser():
    ap = argparse.ArgumentParser(
        prog="simulate_matchup",
        description="CR-grounded Commander matchup simulator (2-4 decks).",
    )
    ap.add_argument(
        "decks",
        nargs="*",
        help="2-4 decklist files (format: 'N Card Name' lines, "
        "// comments, commander under a '// Commander' "
        "section header)",
    )
    ap.add_argument("--games", type=int, default=20, help="games per seed (default 20)")
    ap.add_argument("--seed", type=int, default=42, help="base RNG seed (default 42)")
    ap.add_argument(
        "--seeds",
        default="1",
        metavar="N|LIST",
        help="number of consecutive seeds starting at --seed, "
        "or an explicit comma-separated list (e.g. "
        "'5' or '1,7,100'); statistics are pooled and "
        "reported per seed (default 1)",
    )
    ap.add_argument("--turn-cap", type=int, default=40)
    ap.add_argument("--verbose", action="store_true", help="one-line log per game")
    ap.add_argument("--log-file", metavar="PATH", help="write per-game records (JSONL)")
    ap.add_argument(
        "--custom-cards",
        metavar="PATH",
        help="JSON file overriding card definitions for "
        "unreleased/unverified cards (opt-in; by default "
        "all card data comes from the knowledge graph)",
    )
    ap.add_argument(
        "--profile",
        action="append",
        default=[],
        metavar="SEAT=NAME",
        help=f"AI profile per seat, e.g. 1=aggressive; "
        f"profiles: {', '.join(sorted(PROFILES))}",
    )
    ap.add_argument(
        "--watch",
        action="store_true",
        help="watch a single game live in the TUI (implies --games 1; requires 'rich')",
    )
    ap.add_argument(
        "--viz-file",
        metavar="PATH",
        help="record per-game visualization events + snapshots (JSONL) for --replay",
    )
    ap.add_argument(
        "--replay",
        metavar="PATH",
        help="replay a recorded --viz-file in the TUI (deck arguments are not needed)",
    )
    ap.add_argument(
        "--game",
        type=int,
        default=1,
        metavar="N",
        help="game number to replay from --replay file (1-based, default 1)",
    )
    return ap


def _parse_profiles(specs, n_decks):
    profiles = [None] * n_decks
    for spec in specs:
        seat, _, name = spec.partition("=")
        try:
            idx = int(seat) - 1
        except ValueError:
            sys.exit(f"--profile expects SEAT=NAME, got {spec!r}")
        if not 0 <= idx < n_decks:
            sys.exit(f"--profile seat {seat} out of range")
        profiles[idx] = get_profile(name)
    return profiles


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.replay:
        from mtgviz.replay import replay_file

        replay_file(args.replay, game=args.game)
        return

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

    db = CardDatabase(REPO, args.custom_cards)
    decks = [load_deck(p) for p in deck_paths]
    seen: dict = {}
    for d in decks:  # same list twice -> unique seats
        seen[d.name] = seen.get(d.name, 0) + 1
        if seen[d.name] > 1:
            d.name = f"{d.name}#{seen[d.name]}"
    for d in decks:
        if d.commander is None:
            sys.exit(f"{d.path}: no '// Commander' section found")
    profiles = _parse_profiles(args.profile, len(decks))

    # unknown-card audit
    for d in decks:
        for c in {*d.cards, d.commander}:
            db.get(c)
    if db.stubbed:
        print(
            f"warning: {len(db.stubbed)} unknown card(s) stubbed as inert "
            f"3-mana sorceries: {', '.join(sorted(set(db.stubbed))[:8])}"
            f"{' ...' if len(set(db.stubbed)) > 8 else ''}",
            file=sys.stderr,
        )

    if args.watch:
        from mtgviz.live import watch_game

        watch_game(
            decks,
            db,
            seed=seeds[0],
            turn_cap=args.turn_cap,
            profiles=profiles,
            viz_path=args.viz_file,
        )
        return

    viz_writer = None
    if args.viz_file:
        from mtgviz.recorder import VizWriter

        viz_writer = VizWriter(args.viz_file)

    agg = None
    game_no = 0
    for seed in seeds:
        rng = random.Random(seed)
        for g in range(args.games):
            game_no += 1
            recorder = None
            if viz_writer is not None:
                from mtgviz.recorder import Recorder

                recorder = Recorder(viz_writer.game_sink(game_no, seed))
            rec = run_game(
                decks,
                db,
                rng,
                turn_cap=args.turn_cap,
                profiles=profiles,
                recorder=recorder,
            )
            rec["seed"] = seed
            if agg is None:
                agg = Aggregator(list(rec["players"].keys()))
            agg.add(rec)
            if args.verbose:
                lifes = " ".join(
                    f"{n.split('#')[0][:12]}:{d['life']}"
                    for n, d in rec["players"].items()
                )
                print(
                    f"  s{seed} g{g + 1:>3} T{rec['turns']:>2} "
                    f"winner={rec['winner'][:24]:<24s} "
                    f"({rec['reason']}) {lifes}",
                )
    if viz_writer is not None:
        viz_writer.close()
        print(
            f" viz recording written to {args.viz_file} "
            f"(replay with --replay {args.viz_file})",
        )

    print(agg.report(seeds, args.games))
    counts = {}
    for d in decks:
        for c in {*d.cards, d.commander}:
            counts[db.get(c).source] = counts.get(db.get(c).source, 0) + 1
    print(
        " card data sources: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
    )
    report_model_coverage(decks, db)
    if args.log_file:
        agg.write_jsonl(args.log_file)
        print(f" per-game JSONL written to {args.log_file}")


if __name__ == "__main__":
    main()
