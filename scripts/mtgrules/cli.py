"""Command-line interface for the CR-grounded matchup simulator.

    python3 scripts/simulate_matchup.py DECK1 DECK2 [DECK3 DECK4] [options]

2-4 deck files are required: txt decklists (card data fetched from the
Scryfall API, falling back to the knowledge graph offline; --offline
skips the lookups entirely) or .ttl deck instance graphs
(knowledge-graph content only, no network access).
Statistics can be pooled over multiple RNG
seeds (--seeds) to separate matchup signal from seed variance. A single
game can be watched live in a TUI (--watch) or recorded (--viz-file) and
replayed later (--replay).
"""

# I001+RUF100 (on the block below): mtgcards and mtgrules are both
# first-party under [tool.ruff] src=["scripts"]; the isolated future-state
# lint cannot know that and wants the two packages in separate sections.
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from mtgcards.database import CardDatabase
from mtgcards.deck import Deck, load_deck
from mtgcards.stats import Aggregator
from mtgrules.adapter import REPO, MatchOptions, report_model_coverage, run_game
from mtgrules.policy import PROFILES, get_profile

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mtgrules.policy import PolicyProfile

# a match takes 2-4 decklists (Commander pod sizes)
_MIN_DECKS = 2
_MAX_DECKS = 4
#: how many stubbed card names the warning lists before eliding
_STUB_PREVIEW = 8


def parse_seeds(base_seed: int, spec: str) -> list[int]:
    """Parse the --seeds specification into a seed list.

    Accepts a count ('5' -> base..base+4) or an explicit comma-separated
    list ('1,7,100').
    """
    if "," in spec:
        return [int(s) for s in spec.split(",") if s.strip()]
    count = int(spec)
    if count < 1:
        msg = "--seeds must be >= 1"
        raise argparse.ArgumentTypeError(msg)
    return [base_seed + i for i in range(count)]


def build_parser() -> argparse.ArgumentParser:
    """Build the simulate_matchup argument parser."""
    ap = argparse.ArgumentParser(
        prog="simulate_matchup",
        description="CR-grounded Commander matchup simulator (2-4 decks).",
    )
    ap.add_argument(
        "decks",
        nargs="*",
        help="2-4 deck files: txt decklists ('N Card Name' lines, "
        "// comments, commander under a '// Commander' section "
        "header; card data fetched from the Scryfall API) or .ttl "
        "deck instance graphs (card data from the knowledge graph "
        "only)",
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
        "--offline",
        action="store_true",
        help="skip the Scryfall lookups for txt decklists and resolve "
        "all cards from the knowledge graph (hermetic runs, e.g. CI)",
    )
    ap.add_argument(
        "--extra-cards",
        action="append",
        default=[],
        metavar="FILE.ttl",
        help="additional card TTL graph merged on top of sets/*.ttl "
        "(repeatable), e.g. an out-of-collection "
        "MagicExternalCards.ttl kept next to private deck graphs",
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


def _parse_profiles(specs: list[str], n_decks: int) -> list[PolicyProfile | None]:
    """Resolve --profile SEAT=NAME options into a per-seat profile list."""
    profiles: list[PolicyProfile | None] = [None] * n_decks
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


def _deck_paths(args: argparse.Namespace) -> list[Path]:
    """Validate the deck file arguments before any expensive loading."""
    deck_paths = [Path(d) for d in args.decks]
    if not _MIN_DECKS <= len(deck_paths) <= _MAX_DECKS:
        sys.exit("pass 2-4 deck files (txt decklists or .ttl deck graphs)")
    for p in deck_paths:
        if not p.exists():
            sys.exit(f"deck file not found: {p}")
    return deck_paths


def _load_decks(deck_paths: Sequence[Path], db: CardDatabase) -> list[Deck]:
    """Load and validate the deck files named on the command line."""
    try:
        decks = [load_deck(p, db.ind2name) for p in deck_paths]
    except ValueError as err:
        sys.exit(str(err))
    seen: dict[str, int] = {}
    for d in decks:  # same list twice -> unique seats
        seen[d.name] = seen.get(d.name, 0) + 1
        if seen[d.name] > 1:
            d.name = f"{d.name}#{seen[d.name]}"
    for d in decks:
        if d.commander is None:
            hint = (
                "no ':isCommanderOf' assertion found"
                if d.fmt == "ttl"
                else "no '// Commander' section found"
            )
            sys.exit(f"{d.path}: {hint}")
    return decks


def _resolve_txt_decks(
    decks: Sequence[Deck],
    db: CardDatabase,
    *,
    offline: bool = False,
) -> None:
    """Fetch Scryfall card data for every card of the txt decklists.

    .ttl decks stay on the knowledge graph; failed fetches (offline,
    unknown names) fall back to the graph entry or the inert stub, with
    a warning on stderr. With *offline* (--offline) the lookups are
    skipped entirely and everything resolves from the knowledge graph.
    """
    if offline:
        return
    names = sorted(
        {
            c
            for d in decks
            if d.fmt == "txt"
            for c in (*d.cards, d.commander)
            if c is not None
        },
    )
    if not names:
        return
    failed = db.resolve_scryfall(names)
    if failed:
        print(  # noqa: T201 - user-facing warning on stderr
            f"warning: Scryfall lookup failed for {len(failed)} card(s); "
            f"falling back to the knowledge graph: "
            f"{', '.join(failed[:_STUB_PREVIEW])}"
            f"{' ...' if len(failed) > _STUB_PREVIEW else ''}",
            file=sys.stderr,
        )


def _audit_unknown_cards(decks: Sequence[Deck], db: CardDatabase) -> None:
    """Resolve every card once and warn about stubbed unknowns."""
    for d in decks:
        for c in {*d.cards, d.commander}:
            if c is not None:
                db.get(c)
    if db.stubbed:
        print(  # noqa: T201 - user-facing warning on stderr
            f"warning: {len(db.stubbed)} unknown card(s) stubbed as inert "
            f"3-mana sorceries: {', '.join(sorted(set(db.stubbed))[:_STUB_PREVIEW])}"
            f"{' ...' if len(set(db.stubbed)) > _STUB_PREVIEW else ''}",
            file=sys.stderr,
        )


def _run_watch(
    args: argparse.Namespace,
    decks: Sequence[Deck],
    db: CardDatabase,
    seeds: list[int],
    profiles: list[PolicyProfile | None],
) -> None:
    """--watch: render a single live game in the TUI."""
    # Deferred: mtgviz.live imports mtgrules.adapter at module level, so a
    # top-level import here would be a real package cycle. RUF100 is
    from mtgviz.live import WatchOptions, watch_game  # noqa: PLC0415

    watch_game(
        decks,
        db,
        seed=seeds[0],
        options=WatchOptions(
            turn_cap=args.turn_cap,
            profiles=profiles,
            viz_path=args.viz_file,
        ),
    )


def _run_matches(
    args: argparse.Namespace,
    decks: Sequence[Deck],
    db: CardDatabase,
    seeds: list[int],
    profiles: list[PolicyProfile | None],
) -> Aggregator | None:
    """Run all requested games; return the pooled statistics."""
    viz_writer = None
    if args.viz_file:
        # Deferred for the same mtgviz <-> mtgrules cycle as in
        # _run_watch. RUF100: PLC0415 is still globally ignored.
        from mtgviz.recorder import VizWriter  # noqa: PLC0415

        viz_writer = VizWriter(args.viz_file)

    agg = None
    game_no = 0
    for seed in seeds:
        rng = random.Random(seed)
        for g in range(args.games):
            game_no += 1
            recorder = None
            if viz_writer is not None:
                # Deferred: see _run_watch. RUF100: PLC0415 globally off.
                from mtgviz.recorder import Recorder  # noqa: PLC0415

                recorder = Recorder(viz_writer.game_sink(game_no, seed))
            rec = run_game(
                decks,
                db,
                rng,
                MatchOptions(turn_cap=args.turn_cap, profiles=profiles),
                recorder=recorder,
            )
            rec["seed"] = seed
            if agg is None:
                agg = Aggregator(list(rec["players"].keys()))
            agg.add(rec)
            if args.verbose:
                _print_game_line(seed, g, rec)
    if viz_writer is not None:
        viz_writer.close()
        print(  # noqa: T201 - CLI progress output
            f" viz recording written to {args.viz_file} "
            f"(replay with --replay {args.viz_file})",
        )
    return agg


def _print_game_line(seed: int, g: int, rec: dict[str, object]) -> None:
    """--verbose: print the one-line summary of a finished game."""
    players = rec["players"]
    if not isinstance(players, dict):  # pragma: no cover - record contract
        return
    lifes = " ".join(f"{n.split('#')[0][:12]}:{d['life']}" for n, d in players.items())
    print(  # noqa: T201 - the --verbose CLI output itself
        f"  s{seed} g{g + 1:>3} T{rec['turns']:>2} "
        f"winner={str(rec['winner'])[:24]:<24s} "
        f"({rec['reason']}) {lifes}",
    )


def _final_report(
    args: argparse.Namespace,
    decks: Sequence[Deck],
    db: CardDatabase,
    seeds: list[int],
    agg: Aggregator,
) -> None:
    """Print the pooled statistics and the model-coverage report."""
    # #5 completes; these prints ARE the simulator's report output.
    print(agg.report(seeds, args.games))  # noqa: T201
    counts: dict[str, int] = {}
    for d in decks:
        for c in {*d.cards, d.commander}:
            if c is not None:
                counts[db.get(c).source] = counts.get(db.get(c).source, 0) + 1
    print(  # noqa: T201 - CLI report output
        " card data sources: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
    )
    report_model_coverage(decks, db)
    if args.log_file:
        agg.write_jsonl(args.log_file)
        print(f" per-game JSONL written to {args.log_file}")  # noqa: T201


def main(argv: list[str] | None = None) -> None:
    """Entry point of the simulate_matchup CLI."""
    args = build_parser().parse_args(argv)

    if args.replay:
        # Deferred: see _run_watch. RUF100: PLC0415 is globally off.
        from mtgviz.replay import replay_file  # noqa: PLC0415

        replay_file(args.replay, game=args.game)
        return

    try:
        seeds = parse_seeds(args.seed, args.seeds)
    except ValueError:
        sys.exit(f"invalid --seeds value: {args.seeds!r}")

    deck_paths = _deck_paths(args)
    db = CardDatabase(REPO, args.custom_cards, extra_graphs=args.extra_cards)
    decks = _load_decks(deck_paths, db)
    _resolve_txt_decks(decks, db, offline=args.offline)
    profiles = _parse_profiles(args.profile, len(decks))
    _audit_unknown_cards(decks, db)

    if args.watch:
        _run_watch(args, decks, db, seeds, profiles)
        return

    agg = _run_matches(args, decks, db, seeds, profiles)
    if agg is None:
        sys.exit("no games were played (check --games)")
    _final_report(args, decks, db, seeds, agg)


if __name__ == "__main__":
    main()
