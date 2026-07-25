"""Recorder: taps a mtgrules Game's log stream, emits schema records.

Usage (wired by mtgrules.adapter.run_game):

    recorder = Recorder(sink)           # sink: callable(dict)
    run_game(..., recorder=recorder)    # attach() + on_event() + finish()

Snapshots are taken after state-changing events so the TUI board stays
current; pure log chatter (draw, trigger, ...) only appends to the log.
"""

from __future__ import annotations

import json

from . import schema

#: events after which the board snapshot is refreshed
SNAPSHOT_AFTER = {
    "phase",
    "resolve",
    "land",
    "attack",
    "block",
    "dies",
    "token",
    "counter",
    "life",
    "player_loses",
    "fizzle",
    "cast",
    "activate",
}


class Recorder:
    def __init__(self, sink):
        self.sink = sink
        self.game = None
        self.seq = 0

    def attach(self, game):
        self.game = game
        self.seq += 1
        self.sink(schema.snapshot(game, self.seq))  # opening hands

    def on_event(self, kind, **kw):
        if self.game is None:
            return
        self.seq += 1
        self.sink(schema.event(self.game, self.seq, kind, kw))
        if kind in SNAPSHOT_AFTER:
            self.seq += 1
            self.sink(schema.snapshot(self.game, self.seq))

    def finish(self, game, winner, reason):
        self.seq += 1
        self.sink(schema.snapshot(game, self.seq))
        self.sink(
            {
                "t": "end",
                "winner": winner.name if winner else "draw",
                "reason": reason,
                "turns": game.turn,
            },
        )


class VizWriter:
    """Writes recorder streams for one or more games to a JSONL file."""

    def __init__(self, path):
        self.fh = open(path, "w", encoding="utf-8")

    def game_sink(self, game_no: int, seed: int):
        self.fh.write(json.dumps({"t": "game", "game": game_no, "seed": seed}) + "\n")

        def sink(rec):
            self.fh.write(json.dumps(rec, default=str) + "\n")

        return sink

    def close(self):
        self.fh.close()
