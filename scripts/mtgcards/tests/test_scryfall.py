"""Scryfall client, JSON-to-CardData mapping, and database resolution."""

from __future__ import annotations

import io
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any

from mtgcards.scryfall import ScryfallClient, card_from_scryfall

_LLANOWAR = {
    "name": "Llanowar Elves",
    "mana_cost": "{G}",
    "cmc": 1.0,
    "type_line": "Creature \u2014 Elf Druid",
    "oracle_text": "{T}: Add {G}.",
    "power": "1",
    "toughness": "1",
    "color_identity": ["G"],
}

_DELVER = {
    "name": "Delver of Secrets // Insectile Aberration",
    "cmc": 1.0,
    "type_line": "Creature \u2014 Human Wizard // Creature \u2014 Human Insect",
    "color_identity": ["U"],
    "card_faces": [
        {
            "name": "Delver of Secrets",
            "mana_cost": "{U}",
            "type_line": "Creature \u2014 Human Wizard",
            "oracle_text": "At the beginning of your upkeep, look at the "
            "top card of your library.",
            "power": "1",
            "toughness": "1",
        },
        {
            "name": "Insectile Aberration",
            "mana_cost": "",
            "type_line": "Creature \u2014 Human Insect",
            "oracle_text": "Flying",
            "power": "3",
            "toughness": "2",
        },
    ],
}

_VRASKA = {
    "name": "Vraska, Betrayal's Sting",
    "mana_cost": "{4}{B}{B}",
    "cmc": 6.0,
    "type_line": "Legendary Planeswalker \u2014 Vraska",
    "oracle_text": "Compleated",
    "loyalty": "6",
    "color_identity": ["B"],
}

_TARMOGOYF = {
    "name": "Tarmogoyf",
    "mana_cost": "{1}{G}",
    "cmc": 2.0,
    "type_line": "Creature \u2014 Lhurgoyf",
    "oracle_text": "Tarmogoyf's power is equal to ...",
    "power": "*",
    "toughness": "1+*",
    "color_identity": ["G"],
}


class TestCardFromScryfall(unittest.TestCase):
    """Mapping Scryfall card JSON onto CardData."""

    def test_creature(self) -> None:
        """Printed characteristics map onto the CardData fields."""
        card = card_from_scryfall(_LLANOWAR)
        self.assertEqual(card.name, "Llanowar Elves")
        self.assertEqual(card.mana_cost, "{G}")
        self.assertEqual(card.mv, 1)
        self.assertEqual(card.types, {"Creature"})
        self.assertEqual(card.subtypes, {"Elf", "Druid"})
        self.assertEqual((card.power, card.toughness), (1, 1))
        self.assertEqual(card.color_identity, {"G"})
        self.assertEqual(card.source, "scryfall")

    def test_double_faced_uses_front_face(self) -> None:
        """DFCs keep the full name but front-face characteristics."""
        card = card_from_scryfall(_DELVER)
        self.assertEqual(card.name, "Delver of Secrets // Insectile Aberration")
        self.assertEqual(card.mana_cost, "{U}")
        self.assertEqual(card.subtypes, {"Human", "Wizard"})
        self.assertEqual((card.power, card.toughness), (1, 1))
        self.assertIn("upkeep", card.oracle)

    def test_planeswalker_and_supertype(self) -> None:
        """Supertypes split from card types; loyalty parses."""
        card = card_from_scryfall(_VRASKA)
        self.assertEqual(card.supertypes, {"Legendary"})
        self.assertEqual(card.types, {"Planeswalker"})
        self.assertEqual(card.loyalty, 6)

    def test_star_power_toughness(self) -> None:
        """Non-numeric P/T values ('*', '1+*') become None."""
        card = card_from_scryfall(_TARMOGOYF)
        self.assertIsNone(card.power)
        self.assertIsNone(card.toughness)


class TestScryfallClient(unittest.TestCase):
    """Cache behavior and failure handling of the client."""

    def setUp(self) -> None:
        """Point every client at a fresh temp cache file."""
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        self.cache = Path(tmp_dir) / "named.json"  # not yet created

    def test_fetch_caches_and_persists(self) -> None:
        """A fetched card is cached in memory and on disk."""
        calls: list[str] = []

        def fake_http(url: str) -> dict[str, Any]:
            calls.append(url)
            return dict(_LLANOWAR)

        client = ScryfallClient(cache_file=self.cache, http=fake_http)
        self.assertEqual(client.fetch("Llanowar Elves"), _LLANOWAR)
        self.assertEqual(client.fetch("Llanowar Elves"), _LLANOWAR)
        self.assertEqual(len(calls), 1)
        self.assertIn("Llanowar%20Elves", calls[0])
        client.save()

        # a fresh client reads the persisted entry without the network
        def no_http(url: str) -> dict[str, Any]:
            msg = f"unexpected fetch: {url}"
            raise AssertionError(msg)

        reloaded = ScryfallClient(cache_file=self.cache, http=no_http)
        self.assertEqual(reloaded.fetch("Llanowar Elves"), _LLANOWAR)

    def test_unknown_name_cached_as_miss(self) -> None:
        """A 404 is a definitive miss: cached, and never refetched."""
        calls: list[str] = []

        def http_404(url: str) -> dict[str, Any]:
            calls.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", None, io.BytesIO())  # type: ignore[arg-type]

        client = ScryfallClient(cache_file=self.cache, http=http_404)
        self.assertIsNone(client.fetch("No Such Card"))
        self.assertIsNone(client.fetch("No Such Card"))
        self.assertEqual(len(calls), 1)
        self.assertFalse(client.offline)

    def test_rate_limit_retries_then_trips_breaker(self) -> None:
        """A 429 is retried with backoff; persistent 429 goes offline."""
        import mtgcards.scryfall as sf  # noqa: PLC0415

        calls: list[str] = []

        def http_429(url: str) -> dict[str, Any]:
            calls.append(url)
            raise urllib.error.HTTPError(
                url,
                429,
                "Too Many Requests",
                None,  # type: ignore[arg-type]
                io.BytesIO(),
            )

        # avoid real backoff sleeps in the test
        original = sf.BACKOFF_S
        sf.BACKOFF_S = 0.0
        self.addCleanup(setattr, sf, "BACKOFF_S", original)
        client = ScryfallClient(cache_file=self.cache, http=http_429)
        self.assertIsNone(client.fetch("Sol Ring"))
        self.assertEqual(len(calls), 3)  # initial try + 2 retries
        self.assertTrue(client.offline)

    def test_rate_limit_recovers_within_retries(self) -> None:
        """A single 429 followed by success returns the card."""
        import mtgcards.scryfall as sf  # noqa: PLC0415

        state = {"calls": 0}

        def flaky_http(url: str) -> dict[str, Any]:
            state["calls"] += 1
            if state["calls"] == 1:
                raise urllib.error.HTTPError(
                    url,
                    429,
                    "Too Many Requests",
                    None,  # type: ignore[arg-type]
                    io.BytesIO(),
                )
            return dict(_LLANOWAR)

        original = sf.BACKOFF_S
        sf.BACKOFF_S = 0.0
        self.addCleanup(setattr, sf, "BACKOFF_S", original)
        client = ScryfallClient(cache_file=self.cache, http=flaky_http)
        self.assertEqual(client.fetch("Llanowar Elves"), _LLANOWAR)
        self.assertFalse(client.offline)

    def test_network_error_trips_circuit_breaker(self) -> None:
        """The first URLError stops all further live requests."""
        calls: list[str] = []

        def http_down(url: str) -> dict[str, Any]:
            calls.append(url)
            reason = "no network"
            raise urllib.error.URLError(reason)

        client = ScryfallClient(cache_file=self.cache, http=http_down)
        self.assertIsNone(client.fetch("Sol Ring"))
        self.assertTrue(client.offline)
        self.assertIsNone(client.fetch("Arcane Signet"))
        self.assertEqual(len(calls), 1)

    def test_rejects_non_https(self) -> None:
        """The default HTTP layer refuses non-https URLs."""
        from mtgcards.scryfall import _https_request  # noqa: PLC0415

        with self.assertRaises(ValueError):
            _https_request("http://api.scryfall.com/cards/named?exact=x")


class TestDatabaseResolveScryfall(unittest.TestCase):
    """CardDatabase.resolve_scryfall layering and fallback."""

    def _client(self, responses: dict[str, dict[str, Any] | None]) -> ScryfallClient:
        """Build a client whose HTTP layer serves canned card JSON."""
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        cache = Path(tmp_dir) / "named.json"

        def fake_http(url: str) -> dict[str, Any]:
            for name, data in responses.items():
                if name.replace(" ", "%20") in url and data is not None:
                    return data
            raise urllib.error.HTTPError(url, 404, "Not Found", None, io.BytesIO())  # type: ignore[arg-type]

        return ScryfallClient(cache_file=cache, http=fake_http)

    def test_resolve_overrides_and_reports_failures(self) -> None:
        """Fetched cards enter the index; failures are returned."""
        # Deferred: keep the expensive graph load out of module import.
        from mtgcards.database import CardDatabase  # noqa: PLC0415

        repo = Path(__file__).resolve().parent.parent.parent.parent
        db = CardDatabase(repo)
        client = self._client({"Llanowar Elves": dict(_LLANOWAR)})
        failed = db.resolve_scryfall(["Llanowar Elves", "No Such Card"], client)
        self.assertEqual(failed, ["No Such Card"])
        card = db.get("Llanowar Elves")
        self.assertEqual(card.source, "scryfall")
        # oracle derivation ran on the fetched card (mana dork ability)
        self.assertEqual(card.b("rock_mana"), 1)
        self.assertEqual(card.b("rock_colors"), {"G"})
        # the failed name falls back to graph or stub via db.get
        fallback = db.get("No Such Card")
        self.assertIn(fallback.source, ("graph", "stub"))


if __name__ == "__main__":
    unittest.main()
