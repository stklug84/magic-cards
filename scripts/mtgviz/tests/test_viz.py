"""Recorder / replay conformance tests.

A seeded game's viz stream must be complete, JSON-serializable,
navigable, and its final snapshot must match the engine record.
Run from the scripts/ directory (mtgcards/mtgrules/mtgviz importable).

The fixture decks are synthetic lists of cards from the public
knowledge graph (sets/*.ttl); the real deck pool lives in the private
magic-decks repository together with its own integration tests.
"""

import json
import random
import unittest
from pathlib import Path
from typing import Any, ClassVar

from mtgviz.recorder import Recorder
from mtgviz.tui import ViewState, format_event

REPO = Path(__file__).resolve().parent.parent.parent.parent

#: synthetic fixture pool: cards guaranteed by the public sets/*.ttl graphs
_COMMANDER = "Pia Nalaar, Chief Mechanic"
_SPELLS = ["Sol Ring", "Arcane Signet"]
_LANDS = ["Swamp"] * 38


def _run_recorded(
    seed: int = 11,
    turn_cap: int = 12,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one recorded game; returns (engine record, viz records)."""
    # Deferred: pulls in the rules engine and the card graph, which the
    # pure-navigation tests below do not need at import time. RUF100 is
    from mtgcards.database import CardDatabase  # noqa: PLC0415
    from mtgcards.deck import Deck  # noqa: PLC0415
    from mtgrules.adapter import MatchOptions, run_game  # noqa: PLC0415

    db = CardDatabase(REPO)
    decks = [
        Deck(
            name=f"Fixture #{i}",
            path=f"fixture-{i}.txt",
            cards=[*_SPELLS, *_LANDS],
            commander=_COMMANDER,
        )
        for i in (1, 2)
    ]
    sink_records: list[dict[str, Any]] = []
    rec: dict[str, Any] = run_game(
        decks,
        db,
        random.Random(seed),
        MatchOptions(turn_cap=turn_cap),
        recorder=Recorder(sink_records.append),
    )
    return rec, sink_records


def _snapshot_or_fail(view: ViewState) -> dict[str, Any]:
    """Return the current snapshot, failing the test when there is none."""
    snap = view.snapshot()
    if snap is None:
        msg = "no snapshot at cursor"
        raise AssertionError(msg)
    return snap


class TestViz(unittest.TestCase):
    """Conformance of the recorded stream and the ViewState navigation."""

    rec: ClassVar[dict[str, Any]]
    records: ClassVar[list[dict[str, Any]]]

    @classmethod
    def setUpClass(cls) -> None:
        """Record one seeded game shared by all test methods."""
        cls.rec, cls.records = _run_recorded()

    def test_stream_shape(self) -> None:
        """Stream contains events, snapshots, and a final end record."""
        kinds = {r["t"] for r in self.records}
        self.assertIn("e", kinds)
        self.assertIn("s", kinds)
        self.assertEqual(self.records[-1]["t"], "end")
        # seq strictly increasing over events + snapshots
        seqs = [r["seq"] for r in self.records if "seq" in r]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))

    def test_json_serializable(self) -> None:
        """Every record survives json.dumps (the JSONL writer contract)."""
        for r in self.records:
            json.dumps(r, default=str)

    def test_final_snapshot_matches_engine_record(self) -> None:
        """Golden invariant: last snapshot's life totals == game record."""
        last_snap = [r for r in self.records if r["t"] == "s"][-1]
        for pd in last_snap["players"]:
            self.assertEqual(pd["life"], self.rec["players"][pd["name"]]["life"])
        end = self.records[-1]
        self.assertEqual(end["winner"], self.rec["winner"])
        self.assertEqual(end["reason"], self.rec["reason"])
        self.assertEqual(end["turns"], self.rec["turns"])

    def test_every_event_formats(self) -> None:
        """format_event renders every event kind to a non-empty line."""
        for r in self.records:
            if r["t"] == "e":
                line = format_event(r)
                self.assertIsInstance(line, str)
                self.assertTrue(line)

    def test_view_navigation(self) -> None:
        """Turn jumps, phase/turn seeks, and stepping stay in bounds."""
        stream = [r for r in self.records if r["t"] in ("e", "s")]
        view = ViewState(stream, {"seed": 11})
        view.jump_turn(3)
        self.assertLessEqual(_snapshot_or_fail(view)["turn"], 3)
        cur = view.cursor
        view.next_phase(1)
        self.assertGreater(view.cursor, cur)
        view.next_turn(1)
        self.assertGreaterEqual(_snapshot_or_fail(view)["turn"], 3)
        view.cursor = len(stream) - 1
        self.assertTrue(view.at_end())
        # stepping back never crashes and reaches the front
        for _ in range(len(stream) + 2):
            view.step(-1)
        self.assertLessEqual(view.cursor, 0)

    def test_determinism_of_stream(self) -> None:
        """The same seed reproduces the identical record stream."""
        _, again = _run_recorded()
        self.assertEqual(len(self.records), len(again))
        self.assertEqual(self.records[-1], again[-1])


if __name__ == "__main__":
    unittest.main()
