"""The snapshot this tool writes is the snapshot the generator and validator read.

``generate_individuals.py`` and ``mtgvalidate.consistency`` cannot import
this package: magic-decks downloads the generator alone and imports it, and
the validator ships in the wheel without it. They restate the file name and
read the entry fields themselves, so these tests pin that both agree with
the tool, the way ``rust/crates/mtgcards/tests/behavior_keys.rs`` pins the
behavior keys.
"""

from __future__ import annotations

import ast
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

from mtgarena import cli, render
from mtgarena.tests.fixtures import make_snapshot, owned

if TYPE_CHECKING:
    from types import ModuleType

SCRIPTS = Path(__file__).resolve().parents[2]


def _constant(path: Path, name: str) -> object:
    """Return the literal a module assigns to *name*, read without importing it."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return ast.literal_eval(node.value)
    msg = f"{path.name} assigns no {name}"
    raise AssertionError(msg)


def _generator() -> ModuleType:
    """Import the generator script that sits next to this package."""
    return importlib.import_module("generate_individuals")


class FileNameTests(unittest.TestCase):
    """One file name for the snapshot, in all three places."""

    def test_generator_reads_the_file_the_tool_writes(self) -> None:
        """generate_individuals.ARENA_JSON_NAME is the tool's snapshot."""
        self.assertEqual(_generator().ARENA_JSON_NAME, render.SNAPSHOT_NAME)

    def test_validator_mirrors_the_file_the_tool_writes(self) -> None:
        """mtgvalidate.consistency.ARENA_JSON is the tool's snapshot."""
        consistency = SCRIPTS / "mtgvalidate" / "consistency.py"
        self.assertEqual(_constant(consistency, "ARENA_JSON"), render.SNAPSHOT_NAME)

    def test_picks_are_not_the_snapshot(self) -> None:
        """The tracked picks file never shares a name with an untracked output."""
        self.assertNotIn(
            cli.PICKS_NAME,
            {render.SNAPSHOT_NAME, render.CSV_NAME, render.TXT_NAME},
        )


class SnapshotContractTests(unittest.TestCase):
    """The generator's reading of a snapshot the tool wrote."""

    def test_generator_reads_the_printings_and_arenas_identity(self) -> None:
        """Matched non-basic printings, keyed by Scryfall, with Arena's identity."""
        snapshot = make_snapshot(
            owned((120001, 2), (91665, 4), (70001, 9), (77001, 3), (110006, 1)),
        )
        with tempfile.TemporaryDirectory() as tmp:
            render.write_outputs(Path(tmp), snapshot)
            text = (Path(tmp) / render.SNAPSHOT_NAME).read_text(encoding="utf-8")
        printings = _generator().arena_printings(json.loads(text))
        # the basic Forest and the Arena-only printings stay out
        self.assertEqual(set(printings), {("ytdm", "12"), ("blb", "129")})
        typhoon = printings["ytdm", "12"]
        self.assertEqual(
            (typhoon.name, typhoon.arena_id, typhoon.quantity),
            ("Dragon Typhoon", 120001, 2),
        )
        self.assertEqual(
            (typhoon.arena_name, typhoon.set_code, typhoon.collector_number),
            ("Dragon Typhoon", "Y25", "10"),
        )
        byway = printings["blb", "129"]
        self.assertEqual((byway.set_code, byway.collector_number), ("BLB", "129"))


if __name__ == "__main__":
    unittest.main()
