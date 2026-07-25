"""Match runner: decklists + knowledge graph -> rules-engine games.

Bridges the mtgcards data layer (CardDatabase, deck loading, stats
conventions) to the mtgrules kernel and reports what the card compiler
could not model, so nothing is skipped silently.
"""

from __future__ import annotations

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
def setup_game(decks, db, rng, turn_cap=40, log=None, profiles=None) -> Game:
    players = []
    for deck in decks:
        p = Player(name=deck.name, deck_name=deck.name)
        players.append(p)
    profiles = profiles or [None] * len(players)
    policies = {
        p.name: DefaultPolicy(rng, profile)
        for p, profile in zip(players, profiles, strict=False)
    }
    game = Game(players, rng, policies, turn_cap=turn_cap, log=log)

    for p, deck in zip(players, decks, strict=False):
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
        p.stats["mulligans"] = mulls
    return game


#: raw rules-engine loss reasons (rule citations) -> short report labels
_REASONS = {
    "life 0 or less (704.5a)": "life",
    "drew from empty library (704.5b)": "decked",
    "ten or more poison counters (704.5c)": "poison",
    "21+ commander damage (903.10a)": "commander",
}


def run_game(decks, db, rng, turn_cap=40, log=None, profiles=None, recorder=None):
    """Play one game. Returns an Aggregator-compatible record:
    {winner, turns, reason, players: {name: {stats..., life, lost,
    cards_cast}}}. `log` is an optional (event, **kw) sink; `recorder`
    an optional mtgviz Recorder (attached after setup, notified of every
    log event, finished with the outcome).
    """
    last_loss = {"why": None}
    sinks = []
    if log is not None:
        sinks.append(log)

    def fanout(event, **kw):
        if event == "player_loses":
            last_loss["why"] = kw.get("why")
        for s in sinks:
            s(event, **kw)

    game = setup_game(decks, db, rng, turn_cap=turn_cap, log=fanout, profiles=profiles)
    if recorder is not None:
        recorder.attach(game)
        sinks.append(recorder.on_event)
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
    if winner is not None:
        if winner.stats.get("mechanized_wins"):
            reason = "alt_win"
        else:
            reason = _REASONS.get(last_loss["why"], last_loss["why"] or "elimination")
    else:
        alive = game.alive()
        if len(alive) == 1:
            winner = alive[0]
            reason = _REASONS.get(last_loss["why"], "elimination")
        elif alive:
            winner = max(alive, key=lambda p: p.life)  # turn-cap tiebreak
            reason = "turn_cap"
        else:
            reason = "draw"
    if recorder is not None:
        recorder.finish(game, winner, reason)
    return {
        "winner": winner.name if winner else "draw",
        "turns": game.turn,
        "reason": reason,
        "players": {
            p.name: dict(
                p.stats,
                life=p.life,
                lost=p.lose_reason,
                cards_cast=list(p.cards_cast),
            )
            for p in game.players
        },
    }


def run_match(deck_files, games=10, seed=42, turn_cap=40, verbose=False):
    """Back-compat wrapper: delegates to the full CLI."""
    from .cli import main

    argv = [str(f) for f in deck_files] + [
        "--games",
        str(games),
        "--seed",
        str(seed),
        "--turn-cap",
        str(turn_cap),
    ]
    if verbose:
        argv.append("--verbose")
    main(argv)


def report_model_coverage(decks, db):
    """Which cards carry unmodeled clauses or documented simplifications."""
    pool = set()
    for d in decks:
        pool.update(d.cards)
        if d.commander:
            pool.add(d.commander)
    unknown = {
        name: clauses
        for name, clauses in sorted(compiler.UNKNOWN_CLAUSES.items())
        if name in pool or name.split(" // ")[0] in pool
    }
    notes = {
        n: t
        for n, t in sorted(overrides.NOTES.items())
        if n in pool or n.split(" // ")[0] in pool
    }
    if notes:
        print(f"  simplified implementations: {len(notes)}")
    if unknown:
        n_clauses = sum(len(c) for c in unknown.values())
        print(
            f"  unmodeled oracle clauses: {n_clauses} "
            f"on {len(unknown)} cards (inert, not silently wrong):",
        )
        for name, clauses in unknown.items():
            for c in sorted(clauses):
                print(f"    - {name}: {c[:90]}")


if __name__ == "__main__":
    from .cli import main

    main()
