"""Everything that depends on how mtga.untapped.gg is built.

Untapped.gg is where the Arena collection can be reached from a browser: the
Untapped Companion reads it out of the running game and syncs it to the
account, and the collection page then loads the whole collection with one
request to ``api.mtga.untapped.gg/api/v1/account/collections/<player_id>``
(the page itself renders only ~50 cards at a time).  This module knows the
addresses, finds the account on the page, captures that one response and
checks its shape.  When the site changes, this is the module to fix; the
``probe`` command prints what it sees.

Nothing here prints or returns cookies.  The page's Next.js data carries the
session's cookie header (``props.cookieHeader``), so the scripts run inside
the page return only the handful of fields named below, never the data blob.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from playwright.sync_api import Page

ORIGIN = "https://mtga.untapped.gg"
API_HOST = "api.mtga.untapped.gg"
COLLECTIONS_PATH = "/api/v1/account/collections/"
#: the command line of this tool, for the hints in messages
TOOL = "uv run scripts/arena_collection.py"

NOT_SIGNED_IN = (
    "Not signed in to Untapped.gg on the tool's Chrome profile.\n"
    f"Run `{TOOL} login`, sign in to Untapped.gg in the window that opens, "
    "quit that window with Cmd+Q, then retry."
)
RUN_PROBE = f"run `{TOOL} probe` to see what the site returns now"

#: seconds to wait for the page to show the signed-in account
ACCOUNT_WAIT = 15.0
#: how deep ``shape`` descends into a response
MAX_SHAPE_DEPTH = 12

_PROFILE_RE = re.compile(r"/profile/(?P<user>[^/?#]+)/(?P<player>[^/?#]+)")
_ID_LIKE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-|^\d{4,}$|^[0-9a-f]{16,}$", re.IGNORECASE
)

# Runs inside the page.  Returns the signed-in account's id and its linked
# Arena players -- and nothing else from the page data (it also holds the
# session's cookie header).  Falls back to profile links in the DOM.
ACCOUNT_JS = """
() => {
  const out = { signedIn: false, userId: null, players: [], source: null };
  const el = document.getElementById('__NEXT_DATA__');
  if (el) {
    try {
      const data = JSON.parse(el.textContent);
      const props = (data && data.props) || {};
      const account = props.initialMtgaAccount
        || (props.pageProps && props.pageProps.initialMtgaAccount) || null;
      if (account && account.id) {
        out.signedIn = true;
        out.userId = String(account.id);
        out.players = (account.mtga_players || []).map((p) => ({
          playerId: p && p.player_id != null ? String(p.player_id) : null,
          playerName: p && p.player_name ? String(p.player_name) : null,
        })).filter((p) => p.playerId);
        out.source = '__NEXT_DATA__';
        return out;
      }
    } catch (e) { /* fall through to the DOM */ }
  }
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/profile/"]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\\/profile\\/([^/?#]+)\\/([^/?#]+)/);
    if (!m || seen.has(m[2])) continue;
    seen.add(m[2]);
    out.userId = out.userId || m[1];
    const name = (a.textContent || '').trim() || null;
    out.players.push({ playerId: m[2], playerName: name });
  }
  if (out.userId) { out.signedIn = true; out.source = 'profile-link'; }
  return out;
}
"""

# Fallback for `pull --fetch`: the same request the page makes, issued from
# inside the page so the browser attaches the session itself.
FETCH_JS = """
async (args) => {
  const res = await fetch(args.url, {
    credentials: 'include',
    headers: { 'Accept': 'application/json' },
    redirect: 'follow',
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch (e) { json = null; }
  const contentType = res.headers.get('content-type') || '';
  return { status: res.status, contentType, json };
}
"""


class CollectionFormatError(ValueError):
    """The collection response is not shaped as this tool expects."""


class ResponseInfo(TypedDict):
    """How the collection response was obtained, for printing (no account ids)."""

    status: int
    url: str
    how: str


@dataclass(frozen=True)
class Player:
    """An Arena account linked to the Untapped.gg login."""

    player_id: str
    player_name: str | None


@dataclass(frozen=True)
class Account:
    """The Untapped.gg login and the Arena player whose collection is read."""

    user_id: str
    player_id: str
    player_name: str | None = None


@dataclass(frozen=True)
class Entry:
    """One owned Arena printing: Arena's card id (grpid) and the copies owned."""

    arena_id: int
    quantity: int


# --------------------------------------------------------------------- addresses


def profile_url(user_id: str, player_id: str) -> str:
    """Return the Untapped.gg profile page of an Arena player."""
    return f"{ORIGIN}/profile/{user_id}/{player_id}"


def collection_url(user_id: str, player_id: str) -> str:
    """Return the Untapped.gg collection page of an Arena player."""
    return f"{profile_url(user_id, player_id)}/collection"


def collections_api_url(user_id: str, player_id: str) -> str:
    """Return the API address the collection page loads the collection from."""
    query = urllib.parse.urlencode({"user_id": user_id})
    player = urllib.parse.quote(player_id, safe="")
    return f"https://{API_HOST}{COLLECTIONS_PATH}{player}?{query}"


def parse_profile_url(url: str) -> tuple[str, str] | None:
    """Return ``(user_id, player_id)`` from a profile or collection URL, else None."""
    match = _PROFILE_RE.search(urllib.parse.urlsplit(url.strip()).path)
    if not match:
        return None
    user = urllib.parse.unquote(match.group("user"))
    return user, urllib.parse.unquote(match.group("player"))


def is_collection_response(url: str) -> bool:
    """Tell whether *url* is the collection request of the collection page."""
    parts = urllib.parse.urlsplit(url)
    return parts.netloc == API_HOST and parts.path.startswith(COLLECTIONS_PATH)


def masked_url(url: str) -> str:
    """Return *url* with the account identifiers replaced, for printing."""
    parts = urllib.parse.urlsplit(url)
    path = re.sub(r"(/collections/)[^/?#]+", r"\1<player_id>", parts.path)
    path = _PROFILE_RE.sub("/profile/<user_id>/<player_id>", path)
    query = "&".join(
        f"{k}=<{k}>"
        for k, _ in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    )
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))


# ------------------------------------------------------------ the account on page


def parse_account_state(state: object) -> tuple[str | None, list[Player], str | None]:
    """Return ``(user_id, players, source)`` from ACCOUNT_JS's result.

    user_id is None when signed out.
    """
    if (
        not isinstance(state, dict)
        or not state.get("signedIn")
        or not state.get("userId")
    ):
        return None, [], None
    players = []
    for raw in state.get("players") or []:
        if isinstance(raw, dict) and raw.get("playerId"):
            name = raw.get("playerName")
            players.append(Player(str(raw["playerId"]), str(name) if name else None))
    source = state.get("source")
    return str(state["userId"]), players, str(source) if source else None


def _label(player: Player) -> str:
    """Name a player for messages."""
    return f"{player.player_name or '?'} ({player.player_id})"


def choose_player(players: list[Player], wanted: str | None) -> Player:
    """Return the linked Arena player to use.

    *wanted* is a player name (case-insensitive) or id; without it the only
    linked player is used.
    """
    if not players:
        msg = (
            "Untapped.gg lists no Arena account for this login. Run the Untapped "
            "Companion next to Arena once so it links the account and syncs the "
            "collection, then retry."
        )
        raise SystemExit(msg)
    if wanted:
        key = wanted.strip().casefold()
        hits = [
            p
            for p in players
            if key in {p.player_id.casefold(), (p.player_name or "").casefold()}
        ]
        if not hits:
            names = ", ".join(_label(p) for p in players)
            msg = f"--player {wanted!r} matches none of: {names}"
            raise SystemExit(msg)
        return hits[0]
    if len(players) > 1:
        msg = (
            "Several Arena accounts are linked to this Untapped.gg login; pick one "
            "with --player:\n  " + "\n  ".join(_label(p) for p in players)
        )
        raise SystemExit(msg)
    return players[0]


def discover_account(page: Page, wanted: str | None) -> tuple[Account, str | None]:
    """Return the signed-in account's Arena player on the page, and where it was found.

    Waits ACCOUNT_WAIT seconds for the page to show the account.
    """
    deadline = time.monotonic() + ACCOUNT_WAIT
    user_id: str | None = None
    players: list[Player] = []
    source: str | None = None
    while time.monotonic() < deadline:
        user_id, players, source = parse_account_state(page.evaluate(ACCOUNT_JS))
        if user_id and players:
            break
        time.sleep(0.5)
    if not user_id:
        msg = (
            NOT_SIGNED_IN + "\nIf you are signed in, the page no longer exposes the "
            "account; pass your collection URL once with --profile-url (copy it from "
            "the address bar on your Untapped.gg collection page)."
        )
        raise SystemExit(msg)
    player = choose_player(players, wanted)
    return Account(user_id, player.player_id, player.player_name), source


# ------------------------------------------------------- the collection response


def fetch_collection(
    page: Page,
    account: Account,
    *,
    timeout: float = 60.0,
) -> tuple[object, ResponseInfo]:
    """Open the collection page and return the JSON of its collection request.

    One page view, no extra request: Playwright waits for the response the
    page issues anyway.  The info carries the HTTP status and the masked URL.
    """
    # Playwright is imported here, not at the top, so the offline commands,
    # the tests and the generator run without it
    from playwright.sync_api import TimeoutError as PlaywrightTimeout  # noqa: PLC0415

    url = collection_url(account.user_id, account.player_id)
    try:
        with page.expect_response(
            lambda r: is_collection_response(r.url),
            timeout=timeout * 1000,
        ) as waiter:
            page.goto(url, wait_until="domcontentloaded")
        response = waiter.value
    except PlaywrightTimeout:
        user_id, _, _ = parse_account_state(page.evaluate(ACCOUNT_JS))
        if not user_id:
            raise SystemExit(NOT_SIGNED_IN) from None
        msg = (
            f"The collection page made no request to {API_HOST}{COLLECTIONS_PATH}... "
            f"within {timeout:.0f}s; {RUN_PROBE}, or retry with --fetch."
        )
        raise SystemExit(msg) from None
    info = ResponseInfo(
        status=response.status, url=masked_url(response.url), how="captured"
    )
    _check_status(response.status, info["url"])
    try:
        payload: object = response.json()
    except Exception as exc:  # noqa: BLE001 - whatever Playwright raises for non-JSON
        msg = f"{info['url']}: the response was not JSON ({exc}); {RUN_PROBE}"
        raise SystemExit(msg) from None
    return payload, info


def fetch_collection_direct(
    page: Page, account: Account
) -> tuple[object, ResponseInfo]:
    """Issue the collection request from inside the signed-in page (the fallback)."""
    url = collections_api_url(account.user_id, account.player_id)
    result = page.evaluate(FETCH_JS, {"url": url})
    status = int(result["status"])
    info = ResponseInfo(status=status, url=masked_url(url), how="fetched")
    _check_status(status, info["url"])
    if result["json"] is None:
        kind = result["contentType"]
        msg = f"{info['url']}: the response was not JSON ({kind}); {RUN_PROBE}"
        raise SystemExit(msg)
    payload: object = result["json"]
    return payload, info


def _check_status(status: int, where: str) -> None:
    """Stop with a hint unless the response is HTTP 200."""
    if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        raise SystemExit(NOT_SIGNED_IN)
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        msg = (
            f"{where}: Untapped.gg rate-limited the request (HTTP 429); wait a while "
            "and retry"
        )
        raise SystemExit(msg)
    if status != HTTPStatus.OK:
        msg = f"{where}: HTTP {status}; {RUN_PROBE}"
        raise SystemExit(msg)


def _as_int(value: object) -> int | None:
    """Return an integer or a string of digits as int, else None (never a bool)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _row(index: int, row: object) -> Entry:
    """One row of the response's card list, checked."""
    if not isinstance(row, dict):
        msg = f"cards[{index}] is not an object"
        raise CollectionFormatError(msg)
    arena_id = _as_int(row.get("grpid"))
    quantity = _as_int(row.get("quantity"))
    if arena_id is None or arena_id <= 0:
        msg = f"cards[{index}].grpid is not a positive integer"
        raise CollectionFormatError(msg)
    if quantity is None or quantity < 0:
        msg = f"cards[{index}].quantity is not a non-negative integer"
        raise CollectionFormatError(msg)
    return Entry(arena_id, quantity)


def validate_collection(payload: object) -> list[Entry]:
    """Return the owned printings in the collection response.

    Accepts ``{"cards": [{"grpid", "quantity"}, ...]}`` (or a bare list of
    such rows).  Rows with quantity 0 are dropped, duplicate grpids are
    summed; anything else unexpected raises CollectionFormatError (a
    ValueError) so a changed format can never be written out as "the
    collection".
    """
    cards = payload.get("cards") if isinstance(payload, dict) else payload
    if not isinstance(cards, list):
        msg = "expected a 'cards' list in the collection response"
        raise CollectionFormatError(msg)
    if not cards:
        msg = "the collection response lists no cards"
        raise CollectionFormatError(msg)
    counts: dict[int, int] = {}
    for index, raw in enumerate(cards):
        row = _row(index, raw)
        if row.quantity:
            counts[row.arena_id] = counts.get(row.arena_id, 0) + row.quantity
    if not counts:
        msg = "every quantity in the collection response is zero"
        raise CollectionFormatError(msg)
    return [Entry(arena_id, counts[arena_id]) for arena_id in sorted(counts)]


def collection_meta(payload: object) -> dict[str, str]:
    """Return the scalar fields next to the card list (a sync date), for printing."""
    if not isinstance(payload, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in sorted(payload.items())
        if k != "cards" and isinstance(v, (str, int, float))
    }


# ----------------------------------------------------------------- probe support


def _list_shape(values: list[object], depth: int) -> list[object]:
    """Merge the shapes of a list's elements into one."""
    merged: dict[str, object] = {}
    scalar_kinds: set[str] = set()
    for element in values:
        element_shape = shape(element, depth + 1)
        if isinstance(element_shape, dict):
            for key, value in element_shape.items():
                merged.setdefault(key, value)
        elif isinstance(element_shape, list):
            scalar_kinds.add("list")
        else:
            scalar_kinds.add(str(element_shape))
    if merged:
        return [merged]
    return [", ".join(sorted(scalar_kinds))] if scalar_kinds else []


def shape(value: object, depth: int = 0) -> object:
    """Return the key names and value types of a JSON value, never the values."""
    if depth > MAX_SHAPE_DEPTH:
        return "..."
    if isinstance(value, dict):
        return {
            ("<id>" if _ID_LIKE.match(str(k)) else str(k)): shape(v, depth + 1)
            for k, v in sorted(value.items())
        }
    if isinstance(value, list):
        return _list_shape(value, depth)
    if value is None:
        return "null"
    return type(value).__name__
