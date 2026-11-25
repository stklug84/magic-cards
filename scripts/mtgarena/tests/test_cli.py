"""The command line: the account cache and the offline commands.

``resolve`` and ``export`` run without Untapped.gg and Chrome; the card data
is mocked here, so nothing touches the network.
"""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mtgarena import cli, render, untapped
from mtgarena.chrome import ChromeConfig
from mtgarena.tests.fixtures import PAYLOAD, PICKS, make_catalog, make_snapshot, owned

RESOLVE_PAYLOAD = owned((120001, 2), (91665, 4), (70001, 9))


def _args(**overrides: object) -> argparse.Namespace:
    """Return the account options of the command line, with *overrides*."""
    base: dict[str, object] = {"profile_url": None, "player": None}
    base.update(overrides)
    return argparse.Namespace(**base)


def _quietly(argv: list[str]) -> tuple[int, str]:
    """Run the command line with *argv*; return its status and its output."""
    printed = io.StringIO()
    with redirect_stdout(printed):
        status = cli.main(argv)
    return status, printed.getvalue()


class DefaultsTests(unittest.TestCase):
    """Where the files go by default: the repository root, untracked."""

    def test_the_files_go_to_the_repository_root(self) -> None:
        """The snapshot lands where the generator reads it."""
        root = Path(__file__).resolve().parents[3]
        self.assertEqual(cli.OUT_DIR, root)
        self.assertTrue((root / "MagicCardsOntology.ttl").exists())
        self.assertEqual(render.SNAPSHOT_NAME, "arena-collection.json")
        self.assertEqual(cli.PICKS_NAME, "arena-picks.json")

    def test_only_the_picks_are_tracked(self) -> None:
        """The outputs and temporary files are ignored anywhere; the picks are not."""
        root = Path(__file__).resolve().parents[3]
        ignore = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("arena-collection.*", ignore)  # the JSON, the CSV and the TXT
        self.assertIn("*.tmp", ignore)  # an interrupted write's temporary copy
        for name in (render.SNAPSHOT_NAME, render.CSV_NAME, render.TXT_NAME):
            self.assertTrue(name.startswith("arena-collection."))
        self.assertFalse(cli.PICKS_NAME.startswith("arena-collection."))


class AccountCacheTests(unittest.TestCase):
    """The account ids, cached outside the repository."""

    def test_round_trip_and_selection(self) -> None:
        """The cache is read back, --player and --profile-url choose from it."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ChromeConfig(state_dir=Path(tmp) / "state")
            self.assertIsNone(cli.cached_account(_args(), cfg))
            account = untapped.Account("U1", "P1", "Me#123")
            players = [untapped.Player("P1", "Me#123"), untapped.Player("P2", "Alt#9")]
            cli.save_account_cache(cli.account_file(cfg), account, players)
            self.assertEqual(cli.cached_account(_args(), cfg), account)
            self.assertEqual(
                cli.cached_account(_args(player="alt#9"), cfg),
                untapped.Account("U1", "P2", "Alt#9"),
            )
            with self.assertRaises(SystemExit):
                cli.cached_account(_args(player="nobody"), cfg)
            from_url = cli.cached_account(
                _args(profile_url="https://mtga.untapped.gg/profile/U1/P1/collection"),
                cfg,
            )
            self.assertEqual(from_url, account)  # the name comes from the cache
            other = cli.cached_account(
                _args(profile_url="https://mtga.untapped.gg/profile/U7/P7"),
                cfg,
            )
            self.assertEqual(other, untapped.Account("U7", "P7", None))
            with self.assertRaises(SystemExit):
                cli.cached_account(
                    _args(profile_url="https://mtga.untapped.gg/decks"), cfg
                )
            mode = cli.account_file(cfg).parent.stat().st_mode & 0o777
            self.assertEqual(mode, 0o700)


class ExportTests(unittest.TestCase):
    """``export``: the CSV and TXT from the snapshot as it is."""

    def test_export_needs_a_snapshot(self) -> None:
        """Without a snapshot there is nothing to export."""
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(SystemExit):
            _quietly(["--out-dir", tmp, "export"])

    def test_export_rewrites_derived_files(self) -> None:
        """The options shape the CSV and TXT; the JSON is never touched."""
        snapshot = make_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            render.write_atomic(
                out / render.SNAPSHOT_NAME, render.dump_snapshot(snapshot)
            )
            self.assertEqual(
                _quietly(["--out-dir", tmp, "export", "--include-basics"])[0],
                0,
            )
            self.assertIn("Forest", (out / render.TXT_NAME).read_text(encoding="utf-8"))
            csv_text = (out / render.CSV_NAME).read_text(encoding="utf-8")
            self.assertIn("A-Fires of Invention", csv_text)
            self.assertEqual(
                _quietly(["--out-dir", tmp, "export", "--exclude-digital"])[0],
                0,
            )
            csv_text = (out / render.CSV_NAME).read_text(encoding="utf-8")
            self.assertNotIn("A-Fires of Invention", csv_text)
            self.assertNotIn("Forest", csv_text)  # basics out without --include-basics
            txt = (out / render.TXT_NAME).read_text(encoding="utf-8")
            self.assertIn("A-Fires of Invention", txt)
            self.assertEqual(render.load_snapshot(out / render.SNAPSHOT_NAME), snapshot)


class ResolveCommandTests(unittest.TestCase):
    """``resolve``: the snapshot's own cards, resolved again."""

    def test_snapshot_entries_round_trip(self) -> None:
        """A snapshot gives back the (grpid, quantity) pairs it was built from."""
        self.assertEqual(
            cli.snapshot_entries(make_snapshot()),
            untapped.validate_collection(PAYLOAD),
        )

    def test_resolve_rewrites_the_three_files(self) -> None:
        """Better card data, same cards: all three files are rewritten."""
        stale = make_snapshot(RESOLVE_PAYLOAD, scryfall=False)  # not one CSV row
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cli, "load_catalog", return_value=make_catalog()) as load,
        ):
            out = Path(tmp)
            render.write_outputs(out, stale)
            csv_path = out / render.CSV_NAME
            self.assertEqual(len(csv_path.read_text(encoding="utf-8").splitlines()), 1)
            self.assertEqual(_quietly(["--out-dir", tmp, "resolve", "--dry-run"])[0], 0)
            # a dry run writes nothing
            self.assertEqual(render.load_snapshot(out / render.SNAPSHOT_NAME), stale)
            status, printed = _quietly(["--out-dir", tmp, "resolve"])
            self.assertEqual(status, 0)
            # same cards and quantities, better card data
            self.assertIn("no changes", printed.splitlines())
            self.assertIn("3 printings, 15 cards from arena-collection.json", printed)
            self.assertEqual(
                render.load_snapshot(out / render.SNAPSHOT_NAME),
                make_snapshot(RESOLVE_PAYLOAD),  # as a pull would write it
            )
            csv_text = csv_path.read_text(encoding="utf-8")
            self.assertIn('"2","0","Dragon Typhoon","ytdm"', csv_text)
            self.assertIn('"4","0","Byway Barterer","blb"', csv_text)
            self.assertNotIn("Forest", csv_text)
            self.assertEqual(
                _quietly(["--out-dir", tmp, "resolve", "--include-basics"])[0],
                0,
            )
            self.assertIn(
                '"9","0","Forest","dom"', csv_path.read_text(encoding="utf-8")
            )
            self.assertEqual(load.call_count, 3)

    def test_resolve_applies_the_picks_next_to_the_snapshot(self) -> None:
        """arena-picks.json next to the snapshot places the card it picks."""
        payload = owned((120003, 1), (91665, 4))
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cli, "load_catalog", return_value=make_catalog()),
        ):
            out = Path(tmp)
            render.write_outputs(out, make_snapshot(payload))
            csv_path = out / render.CSV_NAME
            # two printings in spg, no pick
            self.assertNotIn("Cavalier of Dawn", csv_path.read_text(encoding="utf-8"))
            (out / cli.PICKS_NAME).write_text(json.dumps(PICKS), encoding="utf-8")
            status, printed = _quietly(["--out-dir", tmp, "resolve"])
            self.assertEqual(status, 0)
            self.assertIn("2 printings picked by hand in arena-picks.json", printed)
            self.assertIn(
                "picks printings for cards not in the collection: "
                "120002 Doubling Season",
                printed,
            )
            self.assertIn(
                '"1","0","Cavalier of Dawn","spg"',
                csv_path.read_text(encoding="utf-8"),
            )
            stored = render.load_snapshot(out / render.SNAPSHOT_NAME)
            if stored is None:
                self.fail("resolve wrote the snapshot")
            by_id = {c["arena_id"]: c for c in stored["cards"]}
            self.assertEqual(
                (by_id[120003]["source"], by_id[120003]["scryfall_id"]),
                ("scryfall-pick", "s-cavalier-84"),
            )

    def test_picks_can_live_elsewhere(self) -> None:
        """--picks reads the printings picked by hand from another file."""
        payload = owned((120003, 1))
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cli, "load_catalog", return_value=make_catalog()),
        ):
            out = Path(tmp)
            render.write_outputs(out, make_snapshot(payload))
            picks = out / "elsewhere.json"
            picks.write_text(json.dumps(PICKS), encoding="utf-8")
            status, printed = _quietly(
                ["--out-dir", tmp, "--picks", str(picks), "resolve"],
            )
            self.assertEqual(status, 0)
            self.assertIn("picked by hand in elsewhere.json", printed)

    def test_a_pick_that_does_not_hold_stops_resolve_and_pull(self) -> None:
        """A wrong pick stops both commands before Chrome opens or a file changes."""
        bad = [
            {
                "arena_id": 120003,
                "name": "Cavalier of Dawn",
                "set": "spg",
                "collector_number": "99",
            },
        ]
        snapshot = make_snapshot(owned((120003, 1)))
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cli, "load_catalog", return_value=make_catalog()),
            mock.patch.object(cli, "chrome_session") as session,
        ):
            out = Path(tmp)
            render.write_outputs(out, snapshot)
            (out / cli.PICKS_NAME).write_text(json.dumps(bad), encoding="utf-8")
            for command in (["resolve"], ["--state-dir", str(out / "state"), "pull"]):
                with self.subTest(command=command[-1]):
                    with self.assertRaises(SystemExit) as caught:
                        _quietly(["--out-dir", tmp, *command])
                    self.assertIn(
                        "120003 Cavalier of Dawn -> spg 99: Scryfall has no English "
                        "printing spg 99",
                        str(caught.exception),
                    )
            session.assert_not_called()  # pull stops before Chrome opens
            # and nothing is written
            self.assertEqual(render.load_snapshot(out / render.SNAPSHOT_NAME), snapshot)

    def test_resolve_needs_a_usable_snapshot(self) -> None:
        """No snapshot, or one without an owned card, stops before card data loads."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(cli, "load_catalog") as load,
        ):
            with self.assertRaises(SystemExit):
                _quietly(["--out-dir", tmp, "resolve"])  # no snapshot
            empty = json.dumps({"cards": [{"arena_id": 5, "quantity": 0}]})
            render.write_atomic(Path(tmp) / render.SNAPSHOT_NAME, empty)
            with self.assertRaises(SystemExit):
                _quietly(["--out-dir", tmp, "resolve"])  # no owned card in it
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
