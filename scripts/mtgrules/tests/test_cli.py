"""CLI deck loading, Scryfall resolution options and clean error exits."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mtgcards.deck import Deck
from mtgcards.graph import GRAPH_SCHEMA
from mtgrules import cli
from mtgrules.cli import _load_decks, _resolve_txt_decks, build_parser

REPO = Path(__file__).resolve().parent.parent.parent.parent


class TestOfflineFlag(unittest.TestCase):
    """--offline skips the Scryfall resolution entirely."""

    def test_parser_accepts_offline(self) -> None:
        """The flag parses and defaults to False."""
        ap = build_parser()
        self.assertFalse(ap.parse_args(["a.txt", "b.txt"]).offline)
        self.assertTrue(ap.parse_args(["a.txt", "b.txt", "--offline"]).offline)

    def test_offline_skips_resolution(self) -> None:
        """With offline=True no lookup happens and the db is untouched."""
        # Deferred: keep the expensive graph load out of module import.
        from mtgcards.database import CardDatabase  # noqa: PLC0415

        db = CardDatabase(REPO)
        before = len(db.index)
        deck = Deck(
            name="t",
            path="t.txt",
            cards=["No Such Card Offline Xyz"],
            commander="Sol Ring",
        )
        _resolve_txt_decks([deck], db, offline=True)
        self.assertEqual(len(db.index), before)
        self.assertNotIn("No Such Card Offline Xyz", db.index)


class TestCleanExits(unittest.TestCase):
    """Data errors end in sys.exit(message), never in a traceback (C3/C4)."""

    def setUp(self) -> None:
        """Create a scratch directory for deck files and a fake graph root."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_incompatible_bundle_exits_cleanly(self) -> None:
        """An unreadable graph_schema becomes a one-line exit message."""
        manifest = {
            "graph_schema": GRAPH_SCHEMA + 1,
            "graph_version": "2099-01-01",
            "requires_tools": ">=9.9.9",
        }
        (self.root / "GRAPH-MANIFEST.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        deck = self.root / "a.txt"
        deck.write_text("1 Pia Nalaar, Chief Mechanic\n1 Sol Ring\n", encoding="utf-8")
        with (
            mock.patch.object(cli, "REPO", self.root),
            self.assertRaises(SystemExit) as cm,
        ):
            cli.main([str(deck), str(deck), "--offline", "--games", "1"])
        # sys.exit(message): the interpreter prints the message to stderr
        # and exits with status 1; the exception carries the message.
        self.assertIsInstance(cm.exception.code, str)
        self.assertIn(f"graph_schema {GRAPH_SCHEMA + 1}", str(cm.exception.code))

    def test_bad_counts_are_usage_errors(self) -> None:
        """--seeds 0, --games 0 and a non-numeric --seeds exit 2 (C1)."""
        deck = self.root / "a.txt"
        deck.write_text("1 Pia Nalaar, Chief Mechanic\n1 Sol Ring\n", encoding="utf-8")
        for argv in (
            ["--seeds", "0"],
            ["--seeds", "abc"],
            ["--games", "0"],
            ["--turn-cap", "0"],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as cm:
                cli.main([str(deck), str(deck), "--offline", *argv])
            self.assertEqual(cm.exception.code, 2)

    def test_skipped_decklist_lines_warn(self) -> None:
        """Lines the decklist parser cannot read are reported on stderr."""
        # Deferred: keep the expensive graph load out of module import.
        from mtgcards.database import CardDatabase  # noqa: PLC0415

        deck = self.root / "sloppy.txt"
        deck.write_text(
            "// Commander\n1 Pia Nalaar, Chief Mechanic\nSol Ring\n1 Forest\n",
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            decks = _load_decks([deck], CardDatabase(REPO))
        self.assertEqual(decks[0].cards, ["Forest"])
        self.assertIn("skipped 1 line(s)", stderr.getvalue())
        self.assertIn("3: 'Sol Ring'", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
