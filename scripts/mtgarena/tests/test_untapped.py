"""Untapped.gg: the addresses, the account on the page and the response's shape."""

from __future__ import annotations

import json
import unittest

from mtgarena import untapped
from mtgarena.tests.fixtures import PAYLOAD

API = "https://api.mtga.untapped.gg/api/v1/account"


class UrlTests(unittest.TestCase):
    """The addresses of the profile, collection page and collection request."""

    def test_parse_profile_url(self) -> None:
        """User and player id come from any profile or collection URL."""
        self.assertEqual(
            untapped.parse_profile_url(
                "https://mtga.untapped.gg/profile/abc-123/XYZ789/collection?x=1",
            ),
            ("abc-123", "XYZ789"),
        )
        self.assertEqual(
            untapped.parse_profile_url("https://mtga.untapped.gg/en/profile/u1/p1"),
            ("u1", "p1"),
        )
        self.assertIsNone(untapped.parse_profile_url("https://mtga.untapped.gg/decks"))

    def test_api_url_and_matcher(self) -> None:
        """The collection request is recognised on the API host only."""
        url = untapped.collections_api_url("u 1", "p/1")
        self.assertEqual(url, f"{API}/collections/p%2F1?user_id=u+1")
        self.assertTrue(untapped.is_collection_response(url))
        self.assertFalse(
            untapped.is_collection_response(
                "https://mtga.untapped.gg/api/v1/account/collections/p1",
            ),
        )
        self.assertFalse(untapped.is_collection_response(f"{API}/decks/p1"))

    def test_masked_url_hides_ids(self) -> None:
        """Printed URLs never carry the account's ids."""
        masked = untapped.masked_url(f"{API}/collections/P777?user_id=U999&x=2")
        self.assertNotIn("P777", masked)
        self.assertNotIn("U999", masked)
        self.assertIn("/collections/<player_id>?user_id=<user_id>&x=<x>", masked)
        self.assertEqual(
            untapped.masked_url("https://mtga.untapped.gg/profile/U9/P9/collection"),
            "https://mtga.untapped.gg/profile/<user_id>/<player_id>/collection",
        )


class AccountTests(unittest.TestCase):
    """The signed-in account and its linked Arena players."""

    def test_parse_account_state(self) -> None:
        """The page script's result gives the user id, players and source."""
        self.assertEqual(
            untapped.parse_account_state({"signedIn": False}),
            (None, [], None),
        )
        self.assertEqual(untapped.parse_account_state(None), (None, [], None))
        user, players, source = untapped.parse_account_state(
            {
                "signedIn": True,
                "userId": 42,
                "source": "__NEXT_DATA__",
                "players": [
                    {"playerId": "P1", "playerName": "Me#123"},
                    {"playerId": None},
                ],
            },
        )
        self.assertEqual(user, "42")
        self.assertEqual(players, [untapped.Player("P1", "Me#123")])
        self.assertEqual(source, "__NEXT_DATA__")

    def test_choose_player(self) -> None:
        """A name or id picks a player; an ambiguous or unknown choice stops."""
        one = [untapped.Player("P1", "Me#123")]
        two = [*one, untapped.Player("P2", "Alt#999")]
        self.assertEqual(untapped.choose_player(one, None), one[0])
        self.assertEqual(untapped.choose_player(two, "alt#999"), two[1])
        self.assertEqual(untapped.choose_player(two, "P1"), two[0])
        with self.assertRaises(SystemExit):
            untapped.choose_player(two, None)
        with self.assertRaises(SystemExit):
            untapped.choose_player(two, "nobody")
        with self.assertRaises(SystemExit):
            untapped.choose_player([], None)

    def test_page_script_never_touches_the_cookie_header(self) -> None:
        """``__NEXT_DATA__.props.cookieHeader`` holds the session: ids only."""
        self.assertNotIn("cookieHeader", untapped.ACCOUNT_JS)
        stripped = untapped.ACCOUNT_JS.lower().replace("cookieheader", "")
        self.assertNotIn("cookie", stripped)
        for field in ("userId", "players", "playerId", "playerName", "signedIn"):
            self.assertIn(field, untapped.ACCOUNT_JS)
        self.assertIn("source", untapped.ACCOUNT_JS)


class PayloadTests(unittest.TestCase):
    """The collection response, checked before it can become the collection."""

    def test_validate_merges_and_drops_zero(self) -> None:
        """Duplicate grpids add up and a quantity of 0 adds nothing."""
        entries = untapped.validate_collection(PAYLOAD)
        by_id = {e.arena_id: e.quantity for e in entries}
        self.assertEqual(by_id[91665], 4)  # the 0 row adds nothing
        self.assertEqual(len(entries), 8)
        self.assertEqual([e.arena_id for e in entries], sorted(by_id))

    def test_validate_accepts_bare_list_and_digit_strings(self) -> None:
        """A bare list of rows and numbers written as strings are accepted."""
        entries = untapped.validate_collection(
            [{"grpid": "7", "quantity": "2"}, {"grpid": 7, "quantity": 1}],
        )
        self.assertEqual(entries, [untapped.Entry(7, 3)])

    def test_validate_rejects_bad_shapes(self) -> None:
        """Every other shape is a ValueError (CollectionFormatError)."""
        bad_payloads: tuple[object, ...] = (
            {"cards": "nope"},
            {"cards": []},
            {"cards": [{"grpid": 1, "quantity": 0}]},
            {"cards": [{"grpid": True, "quantity": 1}]},
            {"cards": [{"grpid": -5, "quantity": 1}]},
            {"cards": [{"grpid": 5, "quantity": -1}]},
            {"cards": [{"grpid": 5, "quantity": 1.5}]},
            {"cards": ["x"]},
            {"items": [{"grpid": 5, "quantity": 1}]},
            "text",
            None,
        )
        for bad in bad_payloads:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                untapped.validate_collection(bad)

    def test_collection_meta_keeps_scalars_only(self) -> None:
        """Only the scalar fields next to the card list are printed."""
        self.assertEqual(
            untapped.collection_meta(PAYLOAD),
            {"last_updated": "2026-10-01T12:00:00Z"},
        )
        self.assertEqual(
            untapped.collection_meta({"cards": [], "nested": {"a": 1}}), {}
        )
        self.assertEqual(untapped.collection_meta([]), {})

    def test_shape_has_names_and_types_only(self) -> None:
        """The probe's shape shows key names and types, never the values."""
        shaped = untapped.shape(
            {
                "cards": [{"grpid": 91665, "quantity": 4}],
                "token": "SECRET-VALUE",
                "a1b2c3d4-0000-x": 1,
            },
        )
        if not isinstance(shaped, dict):
            self.fail("an object's shape is an object")
        text = json.dumps(shaped)
        self.assertNotIn("SECRET-VALUE", text)
        self.assertNotIn("91665", text)
        self.assertEqual(shaped["cards"], [{"grpid": "int", "quantity": "int"}])
        self.assertEqual(shaped["token"], "str")
        self.assertIn("<id>", shaped)


if __name__ == "__main__":
    unittest.main()
