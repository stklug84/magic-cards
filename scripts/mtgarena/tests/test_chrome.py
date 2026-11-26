"""Chrome: the sign-in providers' cookies a session deletes from the profile.

The profile is a fake cookie store behind the two DevTools protocol commands
the clean-up sends, so nothing starts Chrome.
"""

from __future__ import annotations

import unittest
from typing import TYPE_CHECKING, Any, cast

from mtgarena import chrome

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page

UNTAPPED: list[dict[str, Any]] = [
    {"name": "sessionid", "domain": ".untapped.gg", "path": "/"},
    {"name": "csrftoken", "domain": "mtga.untapped.gg", "path": "/"},
]
PROVIDERS: list[dict[str, Any]] = [
    {"name": "SID", "domain": ".google.com", "path": "/"},
    {"name": "LSID", "domain": "accounts.google.com", "path": "/"},
    {"name": "NID", "domain": ".google.de", "path": "/"},
    {"name": "LOGIN_INFO", "domain": ".youtube.com", "path": "/"},
    {"name": "__dcfduid", "domain": ".discord.com", "path": "/"},
    {"name": "auth-token", "domain": ".twitch.tv", "path": "/"},
    {
        "name": "__Host-GAPS",
        "domain": "accounts.google.com",
        "path": "/",
        "partitionKey": {
            "topLevelSite": "https://untapped.gg",
            "hasCrossSiteAncestor": False,
        },
    },
]
PROVIDER_HOSTS = (
    "accounts.google.com",
    "discord.com",
    "google.com",
    "google.de",
    "twitch.tv",
    "youtube.com",
)


class FakeProfile:
    """A cookie store behind a browser-level and a page-level CDP session.

    A deletion of a cookie named in *stuck* has no effect, like one Chrome
    refuses.
    """

    def __init__(
        self, cookies: list[dict[str, Any]], stuck: frozenset[str] = frozenset()
    ) -> None:
        """Hold copies of *cookies*."""
        self.cookies = [dict(cookie) for cookie in cookies]
        self.stuck = stuck
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Answer Storage.getCookies and Network.deleteCookies."""
        self.calls.append((method, params))
        if method == "Storage.getCookies":
            return {"cookies": [dict(cookie) for cookie in self.cookies]}
        if method == "Network.deleteCookies" and params is not None:
            if params["name"] not in self.stuck:
                self.cookies = [c for c in self.cookies if not _same(c, params)]
            return {}
        msg = f"unexpected command {method}"
        raise AssertionError(msg)

    def deletions(self) -> list[dict[str, Any]]:
        """Return the parameters of every Network.deleteCookies sent."""
        return [p for m, p in self.calls if m == "Network.deleteCookies" and p]


def _same(cookie: dict[str, Any], params: dict[str, Any]) -> bool:
    """Tell whether *params* name exactly *cookie*."""
    keys = ("name", "domain", "path", "partitionKey")
    return all(cookie.get(key) == params.get(key) for key in keys)


class FakeBrowser:
    """The Playwright browser: hands out the browser-level session."""

    def __init__(self, profile: FakeProfile | None) -> None:
        """Serve *profile*, or fail like a Chrome that has gone away."""
        self.profile = profile

    def new_browser_cdp_session(self) -> FakeProfile:
        """Return the browser-level session."""
        if self.profile is None:
            msg = "Target page, context or browser has been closed"
            raise RuntimeError(msg)
        return self.profile


class FakeContext:
    """The Playwright context: hands out the page-level session."""

    def __init__(self, profile: FakeProfile) -> None:
        """Serve *profile*."""
        self.profile = profile

    def new_cdp_session(self, _page: object) -> FakeProfile:
        """Return the page-level session."""
        return self.profile


class ProviderDomainTests(unittest.TestCase):
    """Which cookie domains belong to a sign-in provider."""

    def test_the_providers_and_their_subdomains(self) -> None:
        """Google (any country, and YouTube), Discord and Twitch match."""
        for domain in (
            "google.com",
            ".google.com",
            "accounts.google.com",
            ".google.de",
            "www.google.co.uk",
            ".google.com.au",
            ".youtube.com",
            "discord.com",
            ".discordapp.com",
            "id.twitch.tv",
            "PASSPORT.TWITCH.TV",
        ):
            with self.subTest(domain=domain):
                self.assertTrue(chrome.is_provider_domain(domain))

    def test_everything_else(self) -> None:
        """Untapped.gg and look-alike domains do not match."""
        for domain in (
            "untapped.gg",
            ".untapped.gg",
            "api.mtga.untapped.gg",
            "notgoogle.com",
            "google.com.evil.example",
            "googleusercontent.com",
            "mydiscord.com",
            "twitch.tv.example",
            "",
        ):
            with self.subTest(domain=domain):
                self.assertFalse(chrome.is_provider_domain(domain))


class ForgetProvidersTests(unittest.TestCase):
    """Deleting the providers' cookies, and only those."""

    def test_only_the_providers_cookies_are_deleted(self) -> None:
        """Every provider cookie goes; the Untapped.gg session stays."""
        profile = FakeProfile(UNTAPPED + PROVIDERS)
        result = chrome.forget_providers(profile, profile)
        self.assertEqual(profile.cookies, UNTAPPED)
        self.assertEqual(result, chrome.Forgotten(7, PROVIDER_HOSTS, 0))

    def test_each_cookie_by_its_exact_name_domain_path_and_partition(self) -> None:
        """The deletion names one cookie exactly, partition key included."""
        profile = FakeProfile(PROVIDERS)
        chrome.forget_providers(profile, profile)
        self.assertEqual(profile.deletions(), PROVIDERS)

    def test_a_cookie_chrome_keeps_is_counted(self) -> None:
        """A cookie still there after its deletion is reported as left."""
        profile = FakeProfile(UNTAPPED + PROVIDERS, stuck=frozenset({"SID"}))
        result = chrome.forget_providers(profile, profile)
        self.assertEqual((result.removed, result.left), (6, 1))

    def test_nothing_to_delete(self) -> None:
        """A profile without provider cookies is listed once and left alone."""
        profile = FakeProfile(UNTAPPED)
        result = chrome.forget_providers(profile, profile)
        self.assertEqual(result, chrome.Forgotten(0, (), 0))
        self.assertEqual(profile.calls, [("Storage.getCookies", None)])


class SessionTests(unittest.TestCase):
    """The clean-up a Chrome session runs: it reports and never raises."""

    def _run(self, profile: FakeProfile | None) -> list[str]:
        """Run the session clean-up on *profile*; return what it logged."""
        lines: list[str] = []
        chrome.forget_providers_in_session(
            cast("Browser", FakeBrowser(profile)),
            cast("BrowserContext", FakeContext(profile or FakeProfile([]))),
            cast("Page", object()),
            lines.append,
        )
        return lines

    def test_reports_what_it_deleted(self) -> None:
        """One line names the count and the domains."""
        lines = self._run(FakeProfile(UNTAPPED + PROVIDERS))
        hosts = ", ".join(PROVIDER_HOSTS)
        expected = f"deleted 7 sign-in provider cookies ({hosts}) from the tool's"
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith(expected), lines[0])

    def test_one_cookie(self) -> None:
        """A single cookie is a cookie."""
        lines = self._run(FakeProfile(PROVIDERS[:1]))
        self.assertTrue(lines[0].startswith("deleted 1 sign-in provider cookie (go"))

    def test_silent_without_provider_cookies(self) -> None:
        """Nothing to delete, nothing to say."""
        self.assertEqual(self._run(FakeProfile(UNTAPPED)), [])

    def test_warns_about_a_cookie_chrome_keeps(self) -> None:
        """A cookie Chrome keeps is a warning that says what to do."""
        lines = self._run(FakeProfile(PROVIDERS, stuck=frozenset({"SID"})))
        self.assertIn(
            "warning: 1 sign-in provider cookie could not be deleted", lines[1]
        )
        self.assertIn("`login` window", lines[1])

    def test_a_failure_is_a_warning(self) -> None:
        """A Chrome that has gone away does not stop the run."""
        lines = self._run(None)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("warning: could not delete"), lines[0])


if __name__ == "__main__":
    unittest.main()
