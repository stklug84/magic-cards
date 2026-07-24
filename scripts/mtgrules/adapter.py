"""Match runner: decklists + knowledge graph -> rules-engine games.

Bridges the mtgsim data layer (CardDatabase, deck loading, stats
conventions) to the mtgrules kernel and reports what the card compiler
could not model, so nothing is skipped silently.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

from . import compiler, overrides
from .cr import rule
from .game import Game
from .objects import GameObject, Player, Zone
from .policy import DefaultPolicy
from .turns import TurnRunner

REPO = Path(__file__).resolve().parent.parent.parent


def _build_library(deck, db, owner):
    objs = []
    for name in deck.cards:
        ref = db.get(name)
        base = compiler.compile_card(ref)
        objs.append(GameObject(base, owner, card_ref=ref))
    return objs


@rule("903.6", "103.4", "103.5")
def setup_game(decks, db, rng, turn_cap=40, log=None) -> Game:
    players = []
    for deck in decks:
        p = Player(name=deck.name, deck_name=deck.name)
        players.append(p)
    policies = {p.name: DefaultPolicy(rng) for p in players}
    game = Game(players, rng, policies, turn_cap=turn_cap, log=log)

    for p, deck in zip(players, decks):
        p.library = _build_library(deck, db, p)
        rng.shuffle(p.library)
        if deck.commander:
            ref = db.get(deck.commander)
            base = compiler.compile_card(ref)
            cmd = GameObject(base, p, card_ref=ref)
            cmd.commander = True
            cmd.zone = Zone.COMMAND
            p.command.append(cmd)
            p.commander_obj = cmd

        # rule 103.5 London mulligan
        pol = policies[p.name]
        mulls = 0
        while True:
            hand = p.library[:7]
            if pol.keep_hand(game, p, hand, mulls) or mulls >= 6:
                p.library = p.library[7:]
                p.hand = hand
                for c in hand:
                    c.zone = Zone.HAND
                if mulls:
                    bottom = pol.bottom_cards(game, p, hand, mulls)
                    for c in bottom[:mulls]:
                        p.hand.remove(c)
                        c.zone = Zone.LIBRARY
                        p.library.append(c)
                break
            mulls += 1
            rng.shuffle(p.library)
    return game


def run_game(decks, db, seed, turn_cap=40, verbose=False):
    rng = random.Random(seed)

    events = []

    def log(event, **kw):
        events.append((event, kw))
        if verbose:
            print(f"  [{event}] {kw}")

    game = setup_game(decks, db, rng, turn_cap=turn_cap, log=log)
    runner = TurnRunner(game)
    order = list(game.players)
    while not game.game_over and game.turn < turn_cap:
        runner.take_turn()
        if game.game_over:
            break
        # advance to next surviving player
        for _ in range(len(order)):
            game.active_idx = (game.active_idx + 1) % len(order)
            if not order[game.active_idx].lost:
                break
    winner = game.winner
    if winner is None:
        alive = game.alive()
        if len(alive) == 1:
            winner = alive[0]
        elif alive:
            winner = max(alive, key=lambda p: p.life)   # turn-cap tiebreak
    return {"winner": winner.name if winner else None,
            "turns": game.turn,
            "players": {p.name: dict(p.stats, life=p.life,
                                     lost=p.lose_reason)
                        for p in game.players}}


def run_match(deck_files, games=10, seed=42, turn_cap=40, verbose=False):
    sys.path.insert(0, str(REPO / "scripts"))
    from mtgsim.database import CardDatabase
    from mtgsim.deck import load_deck

    db = CardDatabase(REPO)
    decks = [load_deck(f) for f in deck_files]
    wins = {d.name: 0 for d in decks}
    draws = 0
    turns = []
    for i in range(games):
        result = run_game(decks, db, seed=seed + i, turn_cap=turn_cap,
                          verbose=verbose)
        turns.append(result["turns"])
        if result["winner"] is None:
            draws += 1
        else:
            wins[result["winner"]] += 1

    print(f"=== rules engine: {games} games, seed {seed} ===")
    for name, w in wins.items():
        print(f"  {name:40s} {w:3d} wins ({100 * w / games:5.1f} %)")
    if draws:
        print(f"  {'(no winner at turn cap)':40s} {draws:3d}")
    print(f"  game length: avg {sum(turns) / len(turns):.1f} turns")

    report_model_coverage(decks, db)
    return wins


def report_model_coverage(decks, db):
    """Which cards carry unmodeled clauses or documented simplifications."""
    pool = set()
    for d in decks:
        pool.update(d.cards)
        if d.commander:
            pool.add(d.commander)
    unknown = {name: clauses
               for name, clauses in sorted(compiler.UNKNOWN_CLAUSES.items())
               if name in pool or name.split(" // ")[0] in pool}
    notes = {n: t for n, t in sorted(overrides.NOTES.items())
             if n in pool or n.split(" // ")[0] in pool}
    if notes:
        print(f"  simplified implementations: {len(notes)}")
    if unknown:
        n_clauses = sum(len(c) for c in unknown.values())
        print(f"  unmodeled oracle clauses: {n_clauses} "
              f"on {len(unknown)} cards (inert, not silently wrong):")
        for name, clauses in unknown.items():
            for c in sorted(clauses):
                print(f"    - {name}: {c[:90]}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("decks", nargs="+")
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--turn-cap", type=int, default=40)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    run_match(args.decks, games=args.games, seed=args.seed,
              turn_cap=args.turn_cap, verbose=args.verbose)
