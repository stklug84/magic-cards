"""Match runner: decklists + knowledge graph -> rules-engine games.

Bridges the mtgcards data layer (CardDatabase, deck loading, stats
conventions) to the mtgrules kernel and reports what the card compiler
could not model, so nothing is skipped silently.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mtgrules import compiler, overrides
from mtgrules.cr import rule
from mtgrules.game import Game
from mtgrules.objects import GameObject, Player, Zone
from mtgrules.policy import DefaultPolicy
from mtgrules.turns import TurnRunner

if TYPE_CHECKING:
    # I001+RUF100: mtgcards and mtgrules are both first-party under
    # know that and wants the two packages in separate sections.
    import random
    from collections.abc import Sequence

    from mtgcards.database import CardDatabase
    from mtgcards.deck import Deck
    from mtgrules.policy import PolicyProfile
    from mtgrules.protocols import LogFn, RecorderLike

#: knowledge-graph root: a magic-cards checkout. Defaults to the checkout
#: this file lives in; installed packages set MTG_GRAPH_ROOT instead.
REPO = Path(
    os.environ.get("MTG_GRAPH_ROOT") or Path(__file__).resolve().parent.parent.parent,
)

#: rule 103.5: a London mulligan can go down to a zero-card keep at most
#: six times from a seven-card hand
_MAX_MULLIGANS = 6


@dataclass
class MatchOptions:
    """Per-match knobs shared by setup_game and run_game."""

    turn_cap: int = 40
    #: optional (event, **fields) log sink
    log: LogFn | None = None
    #: per-seat AI profiles (None entries use the default profile)
    profiles: Sequence[PolicyProfile | None] | None = None


def _build_library(deck: Deck, db: CardDatabase, owner: Player) -> list[GameObject]:
    """Compile every deck card into a GameObject owned by *owner*."""
    objs = []
    for name in deck.cards:
        ref = db.get(name)
        base = compiler.compile_card(ref)
        objs.append(GameObject(base, owner, card_ref=ref))
    return objs


@rule("903.6", "103.4", "103.5")
def setup_game(
    decks: Sequence[Deck],
    db: CardDatabase,
    rng: random.Random,
    options: MatchOptions | None = None,
) -> Game:
    """Build a game: libraries, commanders, and London mulligans."""
    opts = options or MatchOptions()
    players = [Player(name=deck.name, deck_name=deck.name) for deck in decks]
    profiles = opts.profiles or [None] * len(players)
    policies = {
        p.name: DefaultPolicy(rng, profile)
        for p, profile in zip(players, profiles, strict=False)
    }
    game = Game(players, rng, policies, turn_cap=opts.turn_cap, log=opts.log)

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
            if pol.keep_hand(game, p, hand, mulls) or mulls >= _MAX_MULLIGANS:
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


def run_game(
    decks: Sequence[Deck],
    db: CardDatabase,
    rng: random.Random,
    options: MatchOptions | None = None,
    recorder: RecorderLike | None = None,
) -> dict[str, Any]:
    """Play one game and return an Aggregator-compatible record.

    The record is {winner, turns, reason, players: {name: {stats...,
    life, lost, cards_cast}}}. *options.log* is an optional (event, **kw)
    sink; *recorder* an optional mtgviz Recorder (attached after setup,
    notified of every log event, finished with the outcome).
    """
    opts = options or MatchOptions()
    last_loss: dict[str, str | None] = {"why": None}
    sinks: list[LogFn] = []
    if opts.log is not None:
        sinks.append(opts.log)

    def fanout(event: str, **kw: object) -> None:
        if event == "player_loses":
            why = kw.get("why")
            last_loss["why"] = why if isinstance(why, str) else None
        for s in sinks:
            s(event, **kw)

    game = setup_game(decks, db, rng, dataclasses.replace(opts, log=fanout))
    if recorder is not None:
        recorder.attach(game)
        sinks.append(recorder.on_event)
    runner = TurnRunner(game)
    order = list(game.players)
    while not game.game_over and game.turn < opts.turn_cap:
        runner.take_turn()
        if game.game_over:
            break
        _advance_active(game, order)
    winner, reason = _decide_outcome(game, last_loss["why"])
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


def _advance_active(game: Game, order: list[Player]) -> None:
    """Advance the turn marker to the next surviving player."""
    for _ in range(len(order)):
        game.active_idx = (game.active_idx + 1) % len(order)
        if not order[game.active_idx].lost:
            return


def _decide_outcome(game: Game, last_why: str | None) -> tuple[Player | None, str]:
    """Determine (winner, short reason label) for the finished game."""
    winner = game.winner
    if winner is not None:
        if winner.stats.get("mechanized_wins"):
            return winner, "alt_win"
        return winner, _REASONS.get(last_why or "", last_why or "elimination")
    alive = game.alive()
    if len(alive) == 1:
        return alive[0], _REASONS.get(last_why or "", "elimination")
    if alive:
        # turn-cap tiebreak
        return max(alive, key=lambda p: p.life), "turn_cap"
    return None, "draw"


def run_match(
    deck_files: Sequence[str | Path],
    *,
    games: int = 10,
    seed: int = 42,
    turn_cap: int = 40,
    verbose: bool = False,
) -> None:
    """Back-compat wrapper: delegates to the full CLI."""
    # Deferred: cli imports this module at top level; importing it back
    # eagerly would be a real cycle. RUF100 is listed because PLC0415 is
    from mtgrules.cli import main  # noqa: PLC0415

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


def report_model_coverage(decks: Sequence[Deck], _db: CardDatabase) -> None:
    """Report cards with unmodeled clauses or documented simplifications."""
    pool: set[str] = set()
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
    # #5 completes; the prints ARE this reporter's CLI output.
    if notes:
        print(f"  simplified implementations: {len(notes)}")  # noqa: T201
    if unknown:
        n_clauses = sum(len(c) for c in unknown.values())
        print(  # noqa: T201 - CLI report output
            f"  unmodeled oracle clauses: {n_clauses} "
            f"on {len(unknown)} cards (inert, not silently wrong):",
        )
        for name, clauses in unknown.items():
            for c in sorted(clauses):
                print(f"    - {name}: {c[:90]}")  # noqa: T201


if __name__ == "__main__":
    from mtgrules.cli import main

    main()
