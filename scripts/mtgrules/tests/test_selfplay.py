"""The self-play tuner's CLI (fix C2) and its fixed seating.

C2: ``.ttl`` deck graphs load, zero counts are usage errors, decks whose
file names coincide get distinct seat names. Self-play keeps deck 0 as
the starting player (its two seatings cancel the play/draw advantage),
while ``mtg-simulate`` draws the starting player at random (F12).
"""

from __future__ import annotations

import contextlib
import io
import random
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

from mtgcards.deck import Deck
from mtgrules import selfplay
from mtgrules.adapter import MatchOptions, setup_game
from mtgrules.selfplay import parse_tune

if TYPE_CHECKING:
    from mtgcards.database import CardDatabase

REPO = Path(__file__).resolve().parent.parent.parent.parent
_TXT = "// Commander\n1 Pia Nalaar, Chief Mechanic\n1 Sol Ring\n10 Swamp\n"


class TestParseTune(unittest.TestCase):
    """--tune KNOB=v1,v2 parsing."""

    def test_axes_and_types(self) -> None:
        """Ints, bools and floats are typed by knob; axes form a product."""
        combos = parse_tune(["aggression=0.7,1.5", "hold_reactive_mana=1,no"])
        self.assertEqual(len(combos), 4)
        self.assertEqual(combos[0], {"aggression": 0.7, "hold_reactive_mana": True})
        self.assertEqual(parse_tune(["race_life=10"]), [{"race_life": 10}])

    def test_unknown_knob_exits(self) -> None:
        """A knob that is not a PolicyProfile field is rejected."""
        with self.assertRaises(SystemExit):
            parse_tune(["swagger=1"])


class TestSelfplayCli(unittest.TestCase):
    """Deck loading and argument validation of mtg-selfplay."""

    db: CardDatabase

    @classmethod
    def setUpClass(cls) -> None:
        """Load the knowledge graph once (the .ttl deck needs its individuals)."""
        # Deferred: the graph load is only needed by this module.
        from mtgcards.database import CardDatabase  # noqa: PLC0415

        cls.db = CardDatabase(REPO)

    def setUp(self) -> None:
        """Create a scratch directory for deck files."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _run(self, argv: list[str]) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            selfplay.main(argv)
        return out.getvalue()

    def test_zero_counts_are_usage_errors(self) -> None:
        """--games 0 and --seeds 0 exit 2 instead of dividing by zero."""
        deck = self.root / "mirror.txt"
        deck.write_text(_TXT, encoding="utf-8")
        for argv in (["--games", "0"], ["--seeds", "0"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as cm:
                selfplay.main([str(deck), *argv])
            self.assertEqual(cm.exception.code, 2)

    def test_ttl_deck_graphs_load(self) -> None:
        """A .ttl deck instance graph is resolved through the knowledge graph."""
        name2ind = {name: ind for ind, name in self.db.ind2name.items()}
        pia, ring, swamp = (
            name2ind["Pia Nalaar, Chief Mechanic"],
            name2ind["Sol Ring"],
            name2ind["Swamp"],
        )
        deck = self.root / "graph.ttl"
        deck.write_text(
            f":{pia} :isCommanderOf :D .\n"
            f':D :hasDeckEntry [ :entryCard :{ring} ; :quantity "1" ] ,\n'
            f'               [ :entryCard :{swamp} ; :quantity "10" ] .\n',
            encoding="utf-8",
        )
        out = self._run([str(deck), "--games", "1", "--turn-cap", "2"])
        self.assertIn("preset round-robin: graph#1 vs graph#2", out)

    def test_same_file_name_gets_distinct_seats(self) -> None:
        """Two decks called deck.txt in different folders become deck and deck#2."""
        (self.root / "a").mkdir()
        (self.root / "b").mkdir()
        for sub in ("a", "b"):
            (self.root / sub / "deck.txt").write_text(_TXT, encoding="utf-8")
        out = self._run(
            [
                str(self.root / "a" / "deck.txt"),
                str(self.root / "b" / "deck.txt"),
                "--games",
                "1",
                "--turn-cap",
                "2",
            ],
        )
        self.assertIn("deck vs deck#2", out)


class TestFixedSeating(unittest.TestCase):
    """setup_game honours an explicit starting player (self-play seatings)."""

    def test_starting_player_option(self) -> None:
        """MatchOptions.starting_player overrides the random choice (103.1)."""
        # Deferred: the graph load is only needed by this module.
        from mtgcards.database import CardDatabase  # noqa: PLC0415

        db = CardDatabase(REPO)
        decks = [
            Deck(
                name=f"S{i}",
                path=f"s{i}.txt",
                cards=["Swamp"] * 10,
                commander="Sol Ring",
            )
            for i in range(2)
        ]
        for start in (0, 1):
            game = setup_game(
                decks, db, random.Random(3), MatchOptions(starting_player=start)
            )
            self.assertEqual(game.active_idx, start)
