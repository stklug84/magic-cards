"""The snapshot, the files derived from it and the change report."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mtgarena import render
from mtgarena.tests.fixtures import PAYLOAD, make_snapshot, owned

#: the 13 columns of a Moxfield collection export, as collection.csv has them
MOXFIELD_HEADER = ",".join(
    f'"{column}"'
    for column in (
        "Count",
        "Tradelist Count",
        "Name",
        "Edition",
        "Condition",
        "Language",
        "Foil",
        "Tags",
        "Last Modified",
        "Collector Number",
        "Alter",
        "Proxy",
        "Purchase Price",
    )
)


class SnapshotTests(unittest.TestCase):
    """The snapshot: sorted, counted, deterministic."""

    def test_natural_key(self) -> None:
        """Collector numbers sort by their digit runs."""
        numbers = ["100", "12b", "2", "12a", "12", None]
        self.assertEqual(
            sorted(numbers, key=render.natural_key),
            [None, "2", "12", "12a", "12b", "100"],
        )

    def test_snapshot_is_sorted_and_counted(self) -> None:
        """Entries sort by name, nameless last; the totals add up."""
        snapshot = make_snapshot()
        names = [c["name"] for c in snapshot["cards"]]
        self.assertEqual(
            names[:3], ["A-Fires of Invention", "Byway Barterer", "Dusk // Dawn"]
        )
        # the title-less and the unknown printing sort last
        self.assertEqual(names[-2:], [None, None])
        self.assertEqual(
            [c["arena_id"] for c in snapshot["cards"][-2:]], [12345, 55555]
        )
        self.assertEqual(
            snapshot["totals"],
            {
                "cards": 34,
                "printings": 8,
                "names": 8,
                "basic_land_cards": 20,
                "scryfall": 5,
                "scryfall_by_set_number": 0,
                "scryfall_by_name": 0,
                "scryfall_by_pick": 0,
                "arena_only": 2,
                "unknown": 1,
                "digital_printings": 1,
            },
        )
        self.assertIn(
            "1 digital-only printings (Alchemy, Arena-only sets; left out of the CSV "
            "with --exclude-digital)",
            render.summary_lines(snapshot),
        )
        newer = ("digital_printings", "scryfall_by_set_number", "scryfall_by_name")
        older_totals = {
            k: v
            for k, v in snapshot["totals"].items()
            if k not in {*newer, "scryfall_by_pick"}
        }
        # snapshots written before these totals existed still summarise
        older = {"totals": older_totals, "cards": []}
        self.assertEqual(len(render.summary_lines(older)), 3)
        self.assertEqual(snapshot["source"], "untapped.gg")
        self.assertNotIn("fetched_at", snapshot)

    def test_snapshot_is_deterministic(self) -> None:
        """The order of the response does not change the file."""
        reversed_payload = {"cards": list(reversed(PAYLOAD["cards"]))}
        self.assertEqual(
            render.dump_snapshot(make_snapshot()),
            render.dump_snapshot(make_snapshot(reversed_payload)),
        )
        self.assertTrue(render.dump_snapshot(make_snapshot()).endswith("\n"))

    def test_label(self) -> None:
        """Messages name a printing as Arena does, else by its grpid."""
        snapshot = make_snapshot()
        by_id = {c["arena_id"]: c for c in snapshot["cards"]}
        self.assertEqual(render.label(by_id[91665]), "Byway Barterer (BLB) 129")
        self.assertEqual(render.label(by_id[12345]), "grpid 12345")
        self.assertEqual(render.label(by_id[55555]), "grpid 55555")


class DerivedFileTests(unittest.TestCase):
    """The Moxfield CSV and the Arena-format TXT."""

    def test_moxfield_csv(self) -> None:
        """Scryfall matches only, basics and digital printings as asked."""
        snapshot = make_snapshot()
        text = render.dump_moxfield_csv(snapshot)
        lines = text.splitlines()
        self.assertEqual(lines[0], MOXFIELD_HEADER)
        self.assertEqual(len(lines), 1 + 4)  # 5 Scryfall matches minus the basic
        self.assertIn(
            '"4","0","Byway Barterer","blb","Near Mint","English","","mtga","",'
            '"129","False","False",""',
            lines,
        )
        self.assertNotIn("Forest", text)
        self.assertNotIn("Only On Arena", text)
        self.assertNotIn("12345", text)
        with_basics = render.dump_moxfield_csv(
            snapshot, include_basics=True
        ).splitlines()
        self.assertEqual(len(with_basics), 1 + 5)
        self.assertIn('"20","0","Forest","dom"', "\n".join(with_basics))
        without_digital = render.dump_moxfield_csv(snapshot, exclude_digital=True)
        # the Alchemy card (Scryfall digital: true) is gone
        self.assertEqual(len(without_digital.splitlines()), 1 + 3)
        self.assertNotIn("A-Fires of Invention", without_digital)
        self.assertIn("Byway Barterer", without_digital)
        # the TXT is Arena's format: never filtered
        self.assertIn("A-Fires of Invention", render.dump_arena_txt(snapshot))

    def test_arena_txt(self) -> None:
        """Arena's own codes and numbers; no basics, no untitled printing."""
        snapshot = make_snapshot()
        lines = render.dump_arena_txt(snapshot).splitlines()
        self.assertEqual(lines[0], "2 A-Fires of Invention (Y22) 9")
        self.assertIn("1 Jace, Vryn's Prodigy (PIO) 60", lines)
        self.assertIn("3 Only On Arena (ANB) 5", lines)
        self.assertNotIn("55555", "\n".join(lines))  # no title, no importable line
        self.assertEqual(len(lines), 5)  # no basic, unknown or untitled printing
        with_basics = render.dump_arena_txt(snapshot, include_basics=True)
        self.assertEqual(len(with_basics.splitlines()), 6)
        self.assertEqual(render.dump_arena_txt({"cards": [], "totals": {}}), "")

    def test_write_outputs_only_when_changed(self) -> None:
        """Files are written atomically, and only when their content changes."""
        snapshot = make_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            written = render.write_outputs(out, snapshot)
            self.assertEqual(
                sorted(p.name for p in written),
                sorted([render.SNAPSHOT_NAME, render.CSV_NAME, render.TXT_NAME]),
            )
            self.assertEqual(render.write_outputs(out, snapshot), [])
            self.assertEqual(render.load_snapshot(out / render.SNAPSHOT_NAME), snapshot)
            self.assertFalse(list(out.glob("*.tmp")))
            (out / render.SNAPSHOT_NAME).write_text("{not json", encoding="utf-8")
            with self.assertRaises(SystemExit):
                render.load_snapshot(out / render.SNAPSHOT_NAME)


class ChangeTests(unittest.TestCase):
    """What changed since the previous snapshot."""

    def test_changes(self) -> None:
        """Added, increased, decreased and gone printings, then the totals."""
        old = make_snapshot()
        self.assertEqual(
            render.changes(old, None),
            ["first snapshot: 8 printings, 34 cards"],
        )
        self.assertEqual(render.changes(old, old), ["no changes"])
        new = make_snapshot(
            owned(
                (91665, 4),
                (65001, 3),  # +2
                (70001, 19),  # -1
                (94287, 1),
                (77001, 3),
                (55555, 1),
                (12345, 2),
                (99001, 1),  # new (a token, oddly); 80001 is gone
            ),
        )
        lines = render.changes(new, old)
        self.assertIn("+1 Treasure (BLB) T1", lines)
        self.assertIn("+2 Dusk // Dawn (AKH) 210  (1 -> 3)", lines)
        self.assertIn("-1 Forest (DAR) 266  (20 -> 19)", lines)
        self.assertIn("-2 A-Fires of Invention (Y22) 9  (gone)", lines)
        self.assertEqual(
            lines[-1],
            "1 printings added, 1 increased (+3 cards); 1 decreased, 1 gone "
            "(-3 cards); total 34 -> 34 cards",
        )

    def test_changes_are_capped(self) -> None:
        """At most MAX_LINES_PER_KIND lines per kind, then a count."""
        cards = [(100000 + i, 1) for i in range(render.MAX_LINES_PER_KIND + 5)]
        new = make_snapshot(owned(*cards))
        lines = render.changes(new, make_snapshot(owned(*cards[:1])))
        self.assertIn("... and 4 more", lines)

    def test_shrink(self) -> None:
        """A smaller collection is reported with both totals."""
        old = make_snapshot()
        self.assertIsNone(render.shrink(old, None))
        self.assertIsNone(render.shrink(old, old))
        smaller = make_snapshot(owned((91665, 4)))
        self.assertEqual(render.shrink(smaller, old), (34, 4))
        self.assertIsNone(render.shrink(old, smaller))


if __name__ == "__main__":
    unittest.main()
