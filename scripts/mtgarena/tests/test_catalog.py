"""The card data and the resolution of Arena card ids to Scryfall printings."""

from __future__ import annotations

import gzip
import io
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from mtgarena import catalog, render, untapped
from mtgarena.tests.fixtures import (
    CARDS,
    LOC,
    PICKS,
    SCRYFALL,
    SET_CODES_PAYLOAD,
    make_catalog,
    make_snapshot,
    owned,
)


def moxfield_row(count: int, name: str, printing: str) -> str:
    """Return the Moxfield CSV row a matched printing ``"set number"`` gets."""
    set_code, number = printing.split()
    return (
        f'"{count}","0","{name}","{set_code}","Near Mint","English","","mtga",'
        f'"","{number}","False","False",""'
    )


class ParsingTests(unittest.TestCase):
    """The sources, parsed into the slim records the resolution works on."""

    def test_clean_name(self) -> None:
        """Aftermath's ``///`` becomes ``//`` and spaces collapse."""
        self.assertEqual(catalog.clean_name("Dusk /// Dawn"), "Dusk // Dawn")
        self.assertEqual(catalog.clean_name("  Two   Spaces "), "Two Spaces")

    def test_parse_arena_cards(self) -> None:
        """The dump joined with the English strings, flags and rarities read."""
        arena = catalog.parse_arena_cards(CARDS, LOC)
        self.assertEqual(arena[91665].name, "Byway Barterer")
        self.assertEqual(arena[91665].rarity, "rare")
        self.assertEqual(arena[65001].name, "Dusk // Dawn")
        self.assertTrue(arena[70001].basic)
        self.assertEqual(arena[70001].rarity, "land")
        self.assertTrue(arena[80001].rebalanced)
        self.assertTrue(arena[80001].digital_only)
        self.assertTrue(arena[94288].secondary)
        self.assertTrue(arena[99001].token)
        self.assertIsNone(arena[99001].rarity)
        self.assertEqual(arena[55555].name, "")  # titleId not in the English strings
        self.assertEqual(arena[77001].set, "ANB")

    def test_parse_scryfall_cards(self) -> None:
        """Printings by arena_id and English printings by set and number."""
        index = catalog.parse_scryfall_cards(SCRYFALL)
        self.assertEqual(index.collisions, 0)
        self.assertEqual(
            set(index.by_arena_id),
            {91665, 65001, 70001, 80001, 94287, 79714, 110005, 110006, 120099},
        )
        self.assertTrue(index.by_arena_id[70001].basic)
        self.assertTrue(index.by_arena_id[80001].digital)
        self.assertEqual(index.by_arena_id[70001].set, "dom")
        self.assertEqual(index.by_set_number["fra", "150"].scryfall_id, "s-phoenix")
        # English wins over the German row
        self.assertEqual(index.by_set_number["dom", "168"].scryfall_id, "s-elves")
        # English printings without arena_id are kept
        self.assertEqual(index.by_set_number["lea", "232"].name, "Black Lotus")
        # the German row without an arena_id is of no use
        self.assertEqual(len(index.cards), len(SCRYFALL) - 1)

    def test_scryfall_card_drops_useless_rows(self) -> None:
        """A row neither English nor on Arena, or with a bool id, is dropped."""
        self.assertIsNone(
            catalog.scryfall_card(
                {"id": "x", "name": "Blitz", "set": "x", "collector_number": "1"}
                | {"lang": "de"},
            ),
        )
        self.assertIsNone(
            catalog.scryfall_card(
                {"id": "bad", "arena_id": True, "name": "Nope", "lang": "ja"},
            ),
        )
        kept = catalog.scryfall_card(
            {"id": "de", "arena_id": 5, "name": "Blitz", "set": "X"}
            | {"collector_number": "1", "lang": "de"},
        )
        if kept is None:
            self.fail("a German row with an arena_id is kept")
        self.assertEqual((kept.arena_id, kept.set), (5, "x"))

    def test_scryfall_collisions_prefer_english(self) -> None:
        """Two printings claiming one arena_id: the English one wins."""
        common = {"set": "x", "collector_number": "1", "rarity": "rare"}
        objects = [
            {"id": "de", "arena_id": 5, "name": "Blitz", "lang": "de"} | common,
            {"id": "en", "arena_id": 5, "name": "Bolt", "lang": "en"} | common,
            {"id": "ja", "arena_id": 5, "name": "Bolt", "lang": "ja"} | common,
            {"id": "bad", "arena_id": True, "name": "Nope", "lang": "ja"},
        ]
        index = catalog.parse_scryfall_cards(objects)
        self.assertEqual(index.collisions, 2)
        self.assertEqual(index.by_arena_id[5].scryfall_id, "en")
        self.assertEqual(index.by_set_number["x", "1"].scryfall_id, "en")

    def test_index_round_trips_through_the_cache_format(self) -> None:
        """The slim records written to the cache rebuild the same index."""
        index = catalog.parse_scryfall_cards(SCRYFALL)
        records = json.loads(json.dumps([asdict(c) for c in index.cards]))
        rebuilt = catalog.ScryfallIndex.build(
            catalog.ScryfallCard(**record) for record in records
        )
        self.assertEqual(rebuilt.by_arena_id, index.by_arena_id)
        self.assertEqual(rebuilt.by_set_number, index.by_set_number)

    def test_scryfall_objects_streams_jsonl(self) -> None:
        """JSON Lines, with the old format's brackets and commas tolerated."""
        lines = [
            "[\n",
            json.dumps(SCRYFALL[0]) + ",\n",
            json.dumps(SCRYFALL[5]) + ",\n",
            json.dumps(SCRYFALL[1]) + "\n",
            "]\n",
            "\n",
        ]
        objects = list(catalog.scryfall_objects(lines))
        self.assertEqual(
            [o["id"] for o in objects], ["s-byway", "s-no-arena", "s-dusk"]
        )
        gz = gzip.compress("".join(lines).encode())
        decoded = (raw.decode() for raw in gzip.GzipFile(fileobj=io.BytesIO(gz)))
        self.assertEqual(len(list(catalog.scryfall_objects(decoded))), 3)

    def test_parse_set_codes(self) -> None:
        """The arena_code map, from the response or a bare list."""
        expected = {"DAR": "dom", "FRA": "fra", "ANA": "xana", "PZA": "pza"}
        self.assertEqual(catalog.parse_set_codes(SET_CODES_PAYLOAD), expected)
        self.assertEqual(catalog.parse_set_codes(SET_CODES_PAYLOAD["data"]), expected)
        self.assertEqual(catalog.parse_set_codes({"data": "nope"}), {})
        self.assertEqual(catalog.parse_set_codes(None), {})

    def test_parse_alchemy_sets(self) -> None:
        """The Alchemy set codes, from the response or a bare list."""
        expected = frozenset({"ymid", "yone", "ytdm", "yfra"})
        self.assertEqual(catalog.parse_alchemy_sets(SET_CODES_PAYLOAD), expected)
        self.assertEqual(
            catalog.parse_alchemy_sets(SET_CODES_PAYLOAD["data"]), expected
        )
        self.assertEqual(catalog.parse_alchemy_sets({"data": "nope"}), frozenset())
        self.assertEqual(catalog.parse_alchemy_sets(None), frozenset())

    def test_load_sets_reads_the_cached_list(self) -> None:
        """A fresh cache is read without a download; no Alchemy sets is a note."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            sets = cache / "scryfall-sets.json"
            sets.write_text(json.dumps(SET_CODES_PAYLOAD), encoding="utf-8")
            codes, alchemy = catalog.load_sets(cache, log=lambda _: None)
            self.assertEqual((codes["PZA"], "ytdm" in alchemy), ("pza", True))
            notes: list[str] = []
            only_dom = {"data": [{"code": "dom", "arena_code": "DAR"}]}
            sets.write_text(json.dumps(only_dom), encoding="utf-8")
            self.assertEqual(
                catalog.load_sets(cache, log=notes.append),
                ({"DAR": "dom"}, frozenset()),
            )
            self.assertIn("stay Arena-only", notes[0])

    def test_name_index_holds_english_front_faces(self) -> None:
        """The name index holds English printings under their front face."""
        index = catalog.parse_scryfall_cards(SCRYFALL)
        self.assertEqual(
            [c.scryfall_id for c in index.by_name["doubling season"]],
            ["s-doubling-pza", "s-doubling-rav"],  # not the Japanese row
        )
        self.assertEqual(
            [c.scryfall_id for c in index.by_name["jace, vryn's prodigy"]],
            ["s-jace"],
        )
        self.assertEqual(
            catalog.name_key("Rumor Gatherer // Rumor Spreader"),
            "rumor gatherer",
        )
        self.assertEqual(catalog.name_key("Urza\u2019s  Saga"), "urza's saga")
        self.assertEqual(catalog.ScryfallIndex.empty().by_name, {})

    def test_names_agree(self) -> None:
        """Case, apostrophes and Arena's front-face titles do not matter."""
        jace = "Jace, Vryn's Prodigy // Jace, Telepath Unbound"
        self.assertTrue(
            catalog.names_agree("Sol'kanar the Tainted", "Sol'Kanar the Tainted")
        )
        self.assertTrue(catalog.names_agree("Jace, Vryn's Prodigy", jace))
        self.assertTrue(catalog.names_agree("Dusk // Dawn", "Dusk // Dawn"))
        self.assertTrue(catalog.names_agree("Urza\u2019s Saga", "Urza's Saga"))
        self.assertFalse(
            catalog.names_agree("A-Galvanic Discharge", "Galvanic Discharge")
        )
        self.assertFalse(catalog.names_agree("Jace", jace))
        self.assertFalse(catalog.names_agree("", "Anything"))
        self.assertFalse(catalog.names_agree("Anything", None))


class ResolutionTests(unittest.TestCase):
    """Each owned card resolved to a Scryfall printing, in the documented order."""

    def test_set_number_fallback(self) -> None:
        """Without an arena_id, set and number match when the names agree."""
        cat = make_catalog()
        self.assertEqual(cat.candidate_set_codes("DAR"), ["dom", "dar"])
        self.assertEqual(cat.candidate_set_codes("fra"), ["fra"])
        self.assertEqual(cat.candidate_set_codes("Y26"), ["y26"])
        phoenix = cat.resolve(110001, 2)
        self.assertEqual(
            (phoenix.source, phoenix.scryfall_id, phoenix.set, phoenix.rarity),
            ("scryfall-set", "s-phoenix", "fra", "rare"),
        )
        elves = cat.resolve(110002, 4)
        self.assertEqual(
            (elves.source, elves.scryfall_id, elves.set, elves.arena_set),
            ("scryfall-set", "s-elves", "dom", "DAR"),
        )
        other = cat.resolve(110003, 1)
        self.assertEqual(
            (other.source, other.scryfall_id, other.name),
            ("arena", None, "Arena Calls It Differently"),
        )
        rumor = cat.resolve(110004, 1)
        self.assertEqual(
            (rumor.source, rumor.name),
            ("scryfall-set", "Rumor Gatherer // Rumor Spreader"),
        )
        self.assertEqual(
            make_catalog(scryfall=False).resolve(110001, 2).source, "arena"
        )
        snapshot = make_snapshot(owned((110001, 2), (110003, 1)))
        self.assertEqual(snapshot["totals"]["scryfall"], 1)
        self.assertEqual(snapshot["totals"]["scryfall_by_set_number"], 1)
        self.assertEqual(snapshot["totals"]["arena_only"], 1)
        text = render.dump_moxfield_csv(snapshot)
        self.assertIn('"2","0","Fracture Phoenix","fra"', text)
        self.assertNotIn("Differently", text)
        self.assertIn(
            "(1 of them by set and collector number)",
            "\n".join(render.summary_lines(snapshot)),
        )

    def test_set_name_fallback(self) -> None:
        """Alchemy and promo printings match by the only name in their set(s).

        Regression: 82 Alchemy printings (Y23-Y26) and 2 PZA promos of a real
        collection were left Arena-only though Scryfall lists them: without
        arena_ids, under other set codes, and (PZA "0") other numbers.
        """
        cat = make_catalog()
        typhoon = cat.resolve(120001, 2)
        self.assertEqual(
            (typhoon.source, typhoon.scryfall_id, typhoon.name, typhoon.set),
            ("scryfall-name", "s-typhoon", "Dragon Typhoon", "ytdm"),
        )
        self.assertEqual((typhoon.collector_number, typhoon.digital), ("12", True))
        # Arena's own code and number stay
        self.assertEqual(
            (typhoon.arena_set, typhoon.arena_collector_number),
            ("Y25", "10"),
        )
        doubling = cat.resolve(120002, 1)
        self.assertEqual(
            (doubling.source, doubling.scryfall_id, doubling.set),
            ("scryfall-name", "s-doubling-pza", "pza"),
        )
        self.assertEqual(doubling.collector_number, "11")
        for grpid, why in (
            (120003, "two printings of the name in spg"),
            (120004, "the name is in two Alchemy sets"),
            (120005, "a yearly Alchemy code searches no paper set"),
            (120006, "the name is in another set only"),
            (110003, "the name is nowhere on Scryfall"),
            (110006, "the rebalanced A- card is not on Scryfall"),
        ):
            with self.subTest(why=why):
                printing = cat.resolve(grpid, 1)
                self.assertEqual(
                    (printing.source, printing.scryfall_id), ("arena", None)
                )
        self.assertEqual(
            cat.name_set_codes("Y25"),
            frozenset({"y25", "ymid", "yone", "ytdm", "yfra"}),
        )
        self.assertEqual(cat.name_set_codes("PZA"), frozenset({"pza"}))
        self.assertEqual(cat.name_set_codes("DAR"), frozenset({"dom", "dar"}))
        without_set_types = catalog.Catalog(cat.arena, cat.scryfall, cat.set_codes)
        self.assertEqual(without_set_types.resolve(120001, 2).source, "arena")
        self.assertEqual(without_set_types.resolve(120002, 1).source, "scryfall-name")
        self.assertEqual(
            make_catalog(scryfall=False).resolve(120001, 2).source, "arena"
        )

    def test_set_name_matches_reach_the_csv(self) -> None:
        """Name matches get CSV rows; the TXT keeps Arena's code and number."""
        payload = owned((120001, 2), (120002, 1), (120003, 1), (110001, 1))
        snapshot = make_snapshot(payload)
        t = snapshot["totals"]
        self.assertEqual(
            (t["scryfall"], t["scryfall_by_set_number"], t["scryfall_by_name"]),
            (3, 1, 2),
        )
        self.assertEqual(t["arena_only"], 1)
        text = render.dump_moxfield_csv(snapshot)
        self.assertIn(moxfield_row(2, "Dragon Typhoon", "ytdm 12"), text)
        self.assertIn(moxfield_row(1, "Doubling Season", "pza 11"), text)
        self.assertNotIn("Cavalier of Dawn", text)
        without_digital = render.dump_moxfield_csv(snapshot, exclude_digital=True)
        self.assertNotIn("Dragon Typhoon", without_digital)  # Scryfall: digital
        self.assertIn("Doubling Season", without_digital)
        self.assertIn(
            "2 Dragon Typhoon (Y25) 10",
            render.dump_arena_txt(snapshot).splitlines(),
        )
        self.assertIn(
            "(1 of them by set and collector number, 2 of them by set and name)",
            "\n".join(render.summary_lines(snapshot)),
        )

    def test_arena_id_match_needs_the_same_name(self) -> None:
        """An arena_id that names another card is refused and reported.

        Regression: 9 of 1616 by-id matches in a real collection named a
        different card (Scryfall data errors).
        """
        cat = make_catalog()
        mountain = cat.resolve(110005, 20)
        self.assertEqual(
            (mountain.source, mountain.name, mountain.scryfall_id, mountain.basic),
            ("scryfall-set", "Mountain", "s-ana-mountain", True),
        )
        rejected_island = cat.rejected_arena_id(110005)
        if rejected_island is None:
            self.fail("the Island Scryfall files under 110005 is reported")
        self.assertEqual(rejected_island.scryfall_id, "s-wrong-island")
        gnarlid = cat.resolve(110006, 1)
        self.assertEqual(
            (gnarlid.source, gnarlid.name, gnarlid.scryfall_id, gnarlid.rebalanced),
            ("arena", "A-Gnarlid Colony", None, True),
        )
        rejected_gnarlid = cat.rejected_arena_id(110006)
        if rejected_gnarlid is None:
            self.fail("the original Gnarlid Colony is reported")
        self.assertEqual(rejected_gnarlid.name, "Gnarlid Colony")
        self.assertIsNone(cat.rejected_arena_id(91665))  # an agreeing match
        self.assertIsNone(cat.rejected_arena_id(12345))  # nothing known at all
        # a grpid the Arena dump does not know keeps Scryfall's by-id answer
        orphan_only = catalog.Catalog({}, catalog.parse_scryfall_cards(SCRYFALL))
        self.assertEqual(orphan_only.resolve(110005, 1).name, "Island")

    def test_resolution_precedence(self) -> None:
        """arena_id first; Arena's data only when Scryfall has nothing."""
        cat = make_catalog()
        matched = cat.resolve(91665, 4)
        self.assertEqual(
            (matched.source, matched.set, matched.scryfall_id, matched.arena_set),
            ("scryfall", "blb", "s-byway", "BLB"),
        )
        forest = cat.resolve(70001, 20)
        self.assertEqual(
            (forest.set, forest.arena_set, forest.basic, forest.rarity),
            ("dom", "DAR", True, "common"),
        )
        jace = cat.resolve(94287, 1)
        self.assertEqual(jace.name, "Jace, Vryn's Prodigy // Jace, Telepath Unbound")
        self.assertEqual(jace.arena_name, "Jace, Vryn's Prodigy")
        arena_only = cat.resolve(77001, 3)
        self.assertEqual(
            (
                arena_only.source,
                arena_only.set,
                arena_only.scryfall_id,
                arena_only.name,
            ),
            ("arena", "anb", None, "Only On Arena"),
        )
        unknown = cat.resolve(12345, 2)
        self.assertEqual(
            (unknown.source, unknown.name, unknown.arena_name, unknown.quantity),
            ("unknown", None, None, 2),
        )
        untitled = cat.resolve(55555, 1)
        self.assertEqual(
            (untitled.source, untitled.name, untitled.arena_name, untitled.arena_set),
            ("arena", None, None, "XYZ"),
        )
        self.assertTrue(cat.resolve(80001, 2).rebalanced)
        self.assertTrue(make_catalog(scryfall=False).resolve(80001, 2).rebalanced)
        self.assertEqual(make_catalog(scryfall=False).resolve(91665, 4).source, "arena")

    def test_land_rarity_is_not_basic(self) -> None:
        """Arena's rarity code 1 ("land") does not make a land basic.

        Regression: 159 nonbasic common lands carry it, like basics do.
        """
        arena = catalog.parse_arena_cards(CARDS, LOC)
        self.assertFalse(arena[79714].basic)
        self.assertEqual(arena[79714].rarity, "land")
        for scryfall in (True, False):
            barrens = make_catalog(scryfall=scryfall).resolve(79714, 2)
            self.assertFalse(barrens.basic, msg=f"scryfall={scryfall}")
        # a real basic without Scryfall
        self.assertTrue(make_catalog(scryfall=False).resolve(70001, 1).basic)
        snapshot = make_snapshot(owned((79714, 2), (70001, 9)))
        self.assertIn("Scoured Barrens", render.dump_moxfield_csv(snapshot))
        self.assertNotIn("Forest", render.dump_moxfield_csv(snapshot))


class PickTests(unittest.TestCase):
    """The picks file: Scryfall printings picked by hand, by Arena card id."""

    def test_parse_picks(self) -> None:
        """Set codes are lower-cased and a note is ignored."""
        self.assertEqual(
            catalog.parse_picks(PICKS),
            {
                120003: catalog.Pick(120003, "Cavalier of Dawn", "spg", "84"),
                120002: catalog.Pick(120002, "Doubling Season", "rav", "158"),
            },
        )
        self.assertEqual(catalog.parse_picks([]), {})

    def test_parse_picks_rejects_bad_shapes(self) -> None:
        """Anything but a list of complete picks is a TypeError or ValueError."""
        good = {"arena_id": 7, "name": "X", "set": "x", "collector_number": "1"}
        for bad in (
            {"picks": [good]},
            ["x"],
            [{**good, "arena_id": True}],
            [{**good, "arena_id": 0}],
            [{**good, "arena_id": "7"}],
            [{**good, "collector_number": 1}],
            [{**good, "name": " "}],
            [{k: v for k, v in good.items() if k != "set"}],
            [good, {**good, "name": "Y"}],  # the same card picked twice
        ):
            with self.subTest(bad=bad), self.assertRaises((TypeError, ValueError)):
                catalog.parse_picks(bad)

    def test_load_picks(self) -> None:
        """No file means no picks; a broken file stops with its path."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "arena-picks.json"
            self.assertEqual(catalog.load_picks(path), {})
            path.write_text(json.dumps(PICKS), encoding="utf-8")
            self.assertEqual(set(catalog.load_picks(path)), {120002, 120003})
            for text in ("[{not json", json.dumps({"arena_id": 1})):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(SystemExit) as caught:
                    catalog.load_picks(path)
                self.assertIn(str(path), str(caught.exception))

    def test_a_pick_comes_first(self) -> None:
        """A pick places a card no lookup decides, and overrides one that does."""
        cat = make_catalog()
        # two printings in spg: no lookup decides
        self.assertEqual(cat.resolve(120003, 1).source, "arena")
        cat.picks = catalog.parse_picks(PICKS)
        self.assertEqual(cat.pick_problems(), [])
        cavalier = cat.resolve(120003, 1)
        self.assertEqual(
            (cavalier.source, cavalier.scryfall_id, cavalier.name),
            ("scryfall-pick", "s-cavalier-84", "Cavalier of Dawn"),
        )
        self.assertEqual(
            (cavalier.set, cavalier.collector_number, cavalier.rarity),
            ("spg", "84", "mythic"),
        )
        # Arena's own code and number stay
        self.assertEqual(
            (cavalier.arena_set, cavalier.arena_collector_number),
            ("SPG", "0"),
        )
        doubling = cat.resolve(120002, 1)  # the set-and-name lookup gives pza 11
        self.assertEqual(
            (doubling.source, doubling.scryfall_id),
            ("scryfall-pick", "s-doubling-rav"),
        )
        # a card without a pick resolves as before
        self.assertEqual(cat.resolve(91665, 4).source, "scryfall")

    def test_a_pick_must_name_the_same_card(self) -> None:
        """A pick that does not hold is reported and not used."""
        for pick, problem, source in (
            (
                catalog.Pick(120003, "Cavalier of Dawn", "spg", "99"),
                "Scryfall has no English printing spg 99",
                "arena",
            ),
            (
                catalog.Pick(120003, "Cavalier of Dawn", "ktk", "7"),
                "Scryfall's ktk 7 is 'Something Else Entirely'",
                "arena",
            ),
            (
                catalog.Pick(91665, "Cavalier of Dawn", "spg", "84"),
                "Arena's card 91665 is 'Byway Barterer'",
                "scryfall",
            ),
        ):
            with self.subTest(problem=problem):
                cat = make_catalog()
                cat.picks = {pick.arena_id: pick}
                where = f"{pick.set} {pick.collector_number}"
                self.assertEqual(
                    cat.pick_problems(),
                    [f"{pick.arena_id} {pick.name} -> {where}: {problem}"],
                )
                self.assertEqual(cat.resolve(pick.arena_id, 1).source, source)
        orphan = catalog.Catalog(
            {},
            catalog.parse_scryfall_cards(SCRYFALL),
            picks={5: catalog.Pick(5, "Cavalier of Dawn", "spg", "94")},
        )
        # a grpid the Arena dump does not know: nothing to compare against
        self.assertEqual(orphan.pick_problems(), [])
        self.assertEqual(orphan.resolve(5, 1).scryfall_id, "s-cavalier-94")

    def test_picks_reach_the_totals_and_the_csv(self) -> None:
        """A picked printing is counted, gets its CSV row and its Arena line."""
        cat = make_catalog()
        cat.picks = catalog.parse_picks(PICKS)
        entries = untapped.validate_collection(owned((120003, 1), (91665, 4)))
        snapshot = render.build_snapshot(
            [cat.resolve(e.arena_id, e.quantity) for e in entries],
        )
        t = snapshot["totals"]
        self.assertEqual(
            (t["scryfall"], t["scryfall_by_pick"], t["arena_only"]), (2, 1, 0)
        )
        self.assertIn(
            moxfield_row(1, "Cavalier of Dawn", "spg 84"),
            render.dump_moxfield_csv(snapshot),
        )
        # the TXT keeps Arena's code and number
        self.assertIn(
            "1 Cavalier of Dawn (SPG) 0",
            render.dump_arena_txt(snapshot).splitlines(),
        )
        self.assertIn(
            "2 printings matched on Scryfall (1 of them picked by hand)",
            "\n".join(render.summary_lines(snapshot)),
        )


if __name__ == "__main__":
    unittest.main()
