"""Card characteristics from the Scryfall API (for txt decklists).

Named-card lookup (GET /cards/named?exact=NAME) on stdlib urllib with a
persistent JSON file cache in the platform temp dir (next to the printing
cache used by generate_individuals.py). Failures degrade gracefully:
ScryfallClient.fetch returns None on network errors or unknown names and
callers fall back to the knowledge graph or the inert stub, so the
simulator keeps working offline. A network-level error trips a circuit
breaker that stops further fetch attempts for the process lifetime.
"""

from __future__ import annotations

import json
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from mtgcards.cards import CardData

API = "https://api.scryfall.com"
UA = {
    "User-Agent": "stklug84-matchup-simulator/1.0",
    "Accept": "application/json",
}
CACHE_FILE = Path(tempfile.gettempdir()) / "scryfall_cache" / "named.json"
#: courtesy delay between live API requests (Scryfall asks for 50-100ms;
#: 150ms matches the generate_individuals.py fetcher and avoids bursts)
RATE_LIMIT_S = 0.15
#: base sleep before retrying a rate-limited request (doubles per retry)
BACKOFF_S = 1.0
_HTTP_NOT_FOUND = 404
_HTTP_TOO_MANY = 429
_MAX_ATTEMPTS = 3

#: A Scryfall card JSON object: heterogeneous, API-defined, consumed via
#: .get() narrowing at each use site.
ScryCard = dict[str, Any]

# CR 205.4c supertypes (Scryfall type_line words left of the em dash)
SUPERTYPES = frozenset(
    {"Basic", "Elite", "Host", "Legendary", "Ongoing", "Snow", "World"},
)


def _https_request(url: str) -> urllib.request.Request:
    """Build a Request for the URL, refusing any scheme other than https."""
    if not url.startswith("https://"):
        msg = f"refusing to fetch non-https URL: {url}"
        raise ValueError(msg)
    return urllib.request.Request(  # noqa: S310 - https enforced above  # nosec B310
        url,
        headers=UA,
    )


def http_json(url: str) -> dict[str, Any]:
    """GET an https URL and decode the JSON payload."""
    req = _https_request(url)
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - https enforced by _https_request  # nosec B310
        payload: dict[str, Any] = json.loads(r.read().decode())
    return payload


class ScryfallClient:
    """Cached named-card lookups against the Scryfall API."""

    def __init__(
        self,
        cache_file: str | Path = CACHE_FILE,
        http: Any = None,  # noqa: ANN401 - injectable (url: str) -> dict for tests
    ) -> None:
        """Load the name cache; *http* overrides the fetch function."""
        self._cache_file = Path(cache_file)
        self._http = http if http is not None else http_json
        # name -> card JSON, or None for a cached 'unknown name' miss
        self._cache: dict[str, ScryCard | None] = {}
        self._dirty = False
        self.offline = False
        if self._cache_file.exists():
            try:
                self._cache = json.loads(self._cache_file.read_text(encoding="utf-8"))
            except ValueError:
                self._cache = {}

    def fetch(self, name: str) -> ScryCard | None:
        """Return the Scryfall card JSON for *name*, or None on failure.

        Cache hits (including cached 'unknown name' misses) never touch
        the network. Rate-limited requests (429) are retried with
        exponential backoff. A network-level error - or persistent rate
        limiting - sets self.offline and suppresses all further live
        requests.
        """
        if name in self._cache:
            return self._cache[name]
        if self.offline:
            return None
        url = f"{API}/cards/named?exact={urllib.parse.quote(name)}"
        data = self._request(url, name)
        if data is not None:
            self._cache[name] = data
            self._dirty = True
        return data

    def _request(self, url: str, name: str) -> ScryCard | None:
        """Perform the live lookup with 429 backoff; None on failure."""
        for attempt in range(_MAX_ATTEMPTS):
            try:
                time.sleep(RATE_LIMIT_S)
                payload: ScryCard = self._http(url)
            except urllib.error.HTTPError as err:
                if err.code == _HTTP_NOT_FOUND:
                    # unknown card name: a definitive, cacheable answer
                    self._cache[name] = None
                    self._dirty = True
                elif err.code == _HTTP_TOO_MANY and attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(BACKOFF_S * 2**attempt)
                    continue
                elif err.code == _HTTP_TOO_MANY:
                    # still throttled after backoff: the server asked us
                    # to stop, so stop for the rest of the process
                    self.offline = True
                return None
            except (urllib.error.URLError, TimeoutError, OSError):
                self.offline = True
                return None
            return payload
        return None  # pragma: no cover - loop always returns

    def save(self) -> None:
        """Persist newly fetched entries to the cache file."""
        if not self._dirty:
            return
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._cache_file.write_text(json.dumps(self._cache), encoding="utf-8")
        self._dirty = False


def _int_or_none(value: Any) -> int | None:  # noqa: ANN401 - API JSON value
    """Parse a Scryfall power/toughness/loyalty value ('*' -> None)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def card_from_scryfall(data: ScryCard) -> CardData:
    """Build a CardData from one Scryfall card JSON object.

    Multi-faced cards keep the canonical 'Front // Back' name but take
    their playable characteristics from the front face, mirroring the
    front-face aliasing of the TTL graph loader.
    """
    face = data
    faces = data.get("card_faces")
    if isinstance(faces, list) and faces and "oracle_text" not in data:
        face = faces[0]
    card = CardData(name=str(data.get("name", "")), source="scryfall")
    card.mana_cost = str(face.get("mana_cost") or "")
    card.mv = int(data.get("cmc") or 0)
    type_line = str(face.get("type_line") or data.get("type_line") or "")
    left, _, right = type_line.partition("\u2014")
    for word in left.split():
        (card.supertypes if word in SUPERTYPES else card.types).add(word)
    card.subtypes = set(right.split())
    card.power = _int_or_none(face.get("power"))
    card.toughness = _int_or_none(face.get("toughness"))
    card.loyalty = _int_or_none(face.get("loyalty"))
    ci = data.get("color_identity")
    card.color_identity = set(ci) if isinstance(ci, list) else set()
    card.oracle = str(face.get("oracle_text") or "")
    return card
