"""Recorder / replay conformance: a seeded game's viz stream is complete,
JSON-serializable, navigable, and its final snapshot matches the engine."""

import json
import random
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from mtgviz.recorder import Recorder                 # noqa: E402
from mtgviz.tui import ViewState, format_event       # noqa: E402


def _run_recorded(seed=11, turn_cap=12):
    from mtgcards.database import CardDatabase
    from mtgcards.deck import load_deck
    from mtgrules.adapter import run_game
    db = CardDatabase(REPO)
    decks = [load_deck(REPO / "strategies"
                       / "station-swarm-counter-deck.txt"),
             load_deck(REPO / "strategies" / "blight-curse-deck.txt")]
    sink_records = []
    rec = run_game(decks, db, random.Random(seed), turn_cap=turn_cap,
                   recorder=Recorder(sink_records.append))
    return rec, sink_records


class TestViz(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec, cls.records = _run_recorded()

    def test_stream_shape(self):
        kinds = {r["t"] for r in self.records}
        self.assertIn("e", kinds)
        self.assertIn("s", kinds)
        self.assertEqual(self.records[-1]["t"], "end")
        # seq strictly increasing over events + snapshots
        seqs = [r["seq"] for r in self.records if "seq" in r]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))

    def test_json_serializable(self):
        for r in self.records:
            json.dumps(r, default=str)

    def test_final_snapshot_matches_engine_record(self):
        """Golden invariant: last snapshot's life totals == game record."""
        last_snap = [r for r in self.records if r["t"] == "s"][-1]
        for pd in last_snap["players"]:
            self.assertEqual(pd["life"],
                             self.rec["players"][pd["name"]]["life"])
        end = self.records[-1]
        self.assertEqual(end["winner"], self.rec["winner"])
        self.assertEqual(end["reason"], self.rec["reason"])
        self.assertEqual(end["turns"], self.rec["turns"])

    def test_every_event_formats(self):
        for r in self.records:
            if r["t"] == "e":
                line = format_event(r)
                self.assertIsInstance(line, str)
                self.assertTrue(line)

    def test_view_navigation(self):
        stream = [r for r in self.records if r["t"] in ("e", "s")]
        view = ViewState(stream, {"seed": 11})
        view.jump_turn(3)
        snap = view.snapshot()
        self.assertIsNotNone(snap)
        self.assertLessEqual(snap["turn"], 3)
        cur = view.cursor
        view.next_phase(1)
        self.assertGreater(view.cursor, cur)
        view.next_turn(1)
        self.assertGreaterEqual(view.snapshot()["turn"], 3)
        view.cursor = len(stream) - 1
        self.assertTrue(view.at_end())
        # stepping back never crashes and reaches the front
        for _ in range(len(stream) + 2):
            view.step(-1)
        self.assertLessEqual(view.cursor, 0)

    def test_determinism_of_stream(self):
        _, again = _run_recorded()
        self.assertEqual(len(self.records), len(again))
        self.assertEqual(self.records[-1], again[-1])


if __name__ == "__main__":
    unittest.main()
