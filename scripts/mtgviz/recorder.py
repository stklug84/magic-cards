"""Recorder: taps a mtgrules Game's log stream, emits schema records.

Usage (wired by mtgrules.adapter.run_game):

    recorder = Recorder(sink)           # sink: callable(dict)
    run_game(..., recorder=recorder)    # attach() + on_event() + finish()

Snapshots are taken after state-changing events so the TUI board stays
current; pure log chatter (draw, trigger, ...) only appends to the log.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from mtgviz import schema

if TYPE_CHECKING:
    from mtgviz.schema import GameLike, PlayerLike, Record, RecordSink

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
    """Translates one game's log events into schema records for a sink."""

    def __init__(self, sink: RecordSink) -> None:
        """Send all records of one game to *sink*."""
        self.sink = sink
        self.game: GameLike | None = None
        self.seq = 0

    def attach(self, game: GameLike) -> None:
        """Bind the game and emit the opening snapshot (opening hands)."""
        self.game = game
        self.seq += 1
        self.sink(schema.snapshot(game, self.seq))

    def on_event(self, kind: str, **kw: object) -> None:
        """Record one engine log event (plus a snapshot when it mutates)."""
        if self.game is None:
            return
        self.seq += 1
        self.sink(schema.event(self.game, self.seq, kind, kw))
        if kind in SNAPSHOT_AFTER:
            self.seq += 1
            self.sink(schema.snapshot(self.game, self.seq))

    def finish(self, game: GameLike, winner: PlayerLike | None, reason: str) -> None:
        """Emit the final snapshot and the game-footer record."""
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

    def __init__(self, path: str | Path) -> None:
        """Open *path* for writing; close() releases the handle."""
        # The JSONL handle deliberately outlives __init__: it collects
        # multiple games and is closed by the owning CLI via close().
        self.fh = Path(path).open("w", encoding="utf-8")  # noqa: SIM115

    def game_sink(self, game_no: int, seed: int) -> RecordSink:
        """Write the game header and return the record sink for that game."""
        self.fh.write(json.dumps({"t": "game", "game": game_no, "seed": seed}) + "\n")

        def sink(rec: Record) -> None:
            self.fh.write(json.dumps(rec, default=str) + "\n")

        return sink

    def close(self) -> None:
        """Flush and close the JSONL file."""
        self.fh.close()
