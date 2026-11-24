"""Card data for Arena card ids, from two public sources cached outside the repo.

* ``mtgajson.untapped.gg/v1/latest/cards.json`` + ``loc_en.json`` -- Untapped's
  dump of Arena's own card database: every Arena printing with its grpid,
  Arena set code, collector number, rarity and English title.  This is what
  the collection response's grpids mean.
* Scryfall's ``default-cards`` bulk file -- Arena's grpid is Scryfall's
  ``arena_id``, so the printings that carry one give the Scryfall id, the
  paper set code and the canonical name (Arena's codes differ at times:
  ``DAR`` for Dominaria, ``Y26`` for Alchemy sets).  ~80 MB gzipped; it is
  streamed once per Scryfall update and kept as a slim index.  Scryfall's
  ``/sets`` list adds the ``arena_code`` -> set code map and the codes of
  its Alchemy sets.

Resolution order per owned card:

1. The Scryfall printing picked by hand for the card in ``arena-picks.json``,
   if Scryfall has it in English and its name agrees with the pick's and
   with Arena's title -- for a card the lookups below cannot place, or
   place wrongly, such as a name Scryfall lists twice in a set.
2. Scryfall printing with that ``arena_id`` -- if its name agrees with
   Arena's title (about 0.2 % of Scryfall's arena_ids point at a different
   card: a Mountain filed as an Island, a rebalanced ``A-`` card filed as
   the original).
3. Scryfall printing with the same set (via the arena_code map) and
   collector number, again only when the names agree -- Scryfall assigns
   arena_ids to a new set weeks after release, and this covers that gap
   without risking a wrong printing.
4. The only English Scryfall printing in that set whose name agrees -- for
   collector numbers the two do not share (Arena numbers some promos ``0``).
   Arena files Alchemy cards under yearly codes (``Y23`` ... ``Y26``) that
   stand for several Scryfall sets (``ybro``, ``yone``, ...) and have no
   arena_code, and most Alchemy sets since 2023 have no arena_ids on
   Scryfall, so for those codes every Scryfall set of type ``alchemy`` is
   searched.  A name with two candidate printings matches neither; a pick
   (step 1) settles it.
5. Arena data only (rebalanced cards and other printings Scryfall lacks).
6. unknown (a grpid the dump does not know).

A printing the dump knows but has no English title for keeps its set and
number and gets no name, so it is kept in the JSON and left out of the TXT.
The cache lives under ``~/.cache/mtga-collection/catalog``.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from email.utils import formatdate, parsedate_to_datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

    #: where progress and notes go (print-like, one line per call)
    Log = Callable[[str], None]

CARDS_URL = "https://mtgajson.untapped.gg/v1/latest/cards.json"
LOC_URL = "https://mtgajson.untapped.gg/v1/latest/loc_en.json"
SCRYFALL_BULK_INDEX = "https://api.scryfall.com/bulk-data/default-cards"
SCRYFALL_SETS = "https://api.scryfall.com/sets"

#: Scryfall requires a User-Agent and an Accept header on every request
USER_AGENT = "stklug84-arena-collection/1.0 (https://github.com/stklug84/magic-cards)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json;q=0.9,*/*;q=0.8"}
DEFAULT_MAX_AGE = 6 * 3600.0
#: seconds a download may take: the card dumps, and the streamed bulk file
DOWNLOAD_TIMEOUT = 120
BULK_TIMEOUT = 300

#: Arena's CardRarity enum as it appears in the Untapped dump (tokens have
#: none). Code 1 is the "land" slot: basic lands, but also the common dual
#: lands of some sets -- so it says nothing about a card being basic; the
#: supertype does.
RARITY = {1: "land", 2: "common", 3: "uncommon", 4: "rare", 5: "mythic"}
BASIC_SUPERTYPE = 1
#: Arena's yearly Alchemy set codes (Y22, Y23, ...): each stands for several
#: of Scryfall's Alchemy sets
ALCHEMY_YEAR = re.compile(r"Y\d\d", re.IGNORECASE)
ALCHEMY_SET_TYPE = "alchemy"


def _stdout(line: str) -> None:
    """Write one progress line to standard output (the default log)."""
    sys.stdout.write(line + "\n")


@dataclass(frozen=True)
class ArenaCard:
    """One printing of Arena's own card database (Untapped's dump)."""

    arena_id: int
    name: str
    set: str
    collector_number: str
    rarity: str | None
    basic: bool
    token: bool
    secondary: bool
    rebalanced: bool
    digital_only: bool


@dataclass(frozen=True)
class ScryfallCard:
    """The fields of a Scryfall card object this tool needs (the slim index)."""

    scryfall_id: str
    name: str
    set: str
    collector_number: str
    rarity: str
    basic: bool
    digital: bool
    lang: str
    arena_id: int | None = None


@dataclass
class ScryfallIndex:
    """The slim Scryfall data, by arena_id, by (set, number) and by name.

    The set-and-number and name indexes hold English printings only.
    """

    by_arena_id: dict[int, ScryfallCard]
    by_set_number: dict[tuple[str, str], ScryfallCard]
    cards: list[ScryfallCard]
    collisions: int = 0
    #: name_key -> the English printings of that name
    by_name: dict[str, list[ScryfallCard]] = field(default_factory=dict)

    @classmethod
    def build(cls, cards: Iterable[ScryfallCard]) -> ScryfallIndex:
        """Index *cards*.

        When two printings claim the same arena_id (data errors happen), the
        English one wins, then the first seen.
        """
        by_arena_id: dict[int, ScryfallCard] = {}
        by_set_number: dict[tuple[str, str], ScryfallCard] = {}
        by_name: dict[str, list[ScryfallCard]] = {}
        kept: list[ScryfallCard] = []
        collisions = 0
        for card in cards:
            kept.append(card)
            if card.arena_id is not None:
                current = by_arena_id.get(card.arena_id)
                if current is None:
                    by_arena_id[card.arena_id] = card
                else:
                    collisions += 1
                    if current.lang != "en" and card.lang == "en":
                        by_arena_id[card.arena_id] = card
            if card.lang == "en" and card.set and card.collector_number:
                by_set_number.setdefault((card.set, card.collector_number), card)
            if card.lang == "en" and card.set and card.name:
                by_name.setdefault(name_key(card.name), []).append(card)
        return cls(by_arena_id, by_set_number, kept, collisions, by_name)

    @classmethod
    def empty(cls) -> ScryfallIndex:
        """Return an index without cards (resolution without Scryfall)."""
        return cls({}, {}, [])


@dataclass(frozen=True)
class Pick:
    """A Scryfall printing chosen by hand for an Arena card, by set and number."""

    arena_id: int
    name: str
    set: str
    collector_number: str


@dataclass(frozen=True)
class Printing:
    """One owned printing, resolved as far as the sources allow."""

    arena_id: int
    quantity: int
    #: scryfall-pick | scryfall | scryfall-set | scryfall-name | arena | unknown
    source: str
    name: str | None
    set: str | None
    collector_number: str | None
    rarity: str | None
    scryfall_id: str | None
    arena_name: str | None
    arena_set: str | None
    arena_collector_number: str | None
    basic: bool
    digital: bool
    rebalanced: bool
    token: bool
    secondary: bool


# ------------------------------------------------------------- pure parsing


def clean_name(text: str) -> str:
    """Write an Arena title as Scryfall does: ``///`` is ``//``, spaces collapse."""
    return re.sub(r"\s+", " ", text.replace("///", "//")).strip()


def _titles(loc: Iterable[object]) -> dict[int, str]:
    """Return the English strings of ``loc_en.json`` by id."""
    titles: dict[int, str] = {}
    for item in loc:
        if not isinstance(item, dict):
            continue
        key, text = item.get("id"), item.get("text")
        if isinstance(key, int) and isinstance(text, str):
            titles[key] = text
    return titles


def parse_arena_cards(
    cards: Iterable[object],
    loc: Iterable[object],
) -> dict[int, ArenaCard]:
    """Join Untapped's ``cards.json`` with ``loc_en.json`` by titleId."""
    titles = _titles(loc)
    out: dict[int, ArenaCard] = {}
    for card in cards:
        if not isinstance(card, dict) or not isinstance(card.get("grpid"), int):
            continue
        supertypes = card.get("supertypes") or []
        rarity_code = card.get("rarity")
        title_id = card.get("titleId")
        title = titles.get(title_id, "") if isinstance(title_id, int) else ""
        out[card["grpid"]] = ArenaCard(
            arena_id=card["grpid"],
            name=clean_name(title),  # "" when the English strings lack the title
            set=str(card.get("set") or ""),
            collector_number=str(card.get("collectorNumber") or ""),
            rarity=RARITY.get(rarity_code) if isinstance(rarity_code, int) else None,
            basic=BASIC_SUPERTYPE in supertypes,
            token=bool(card.get("isToken")),
            secondary=bool(card.get("isSecondaryCard")),
            rebalanced=bool(card.get("IsRebalanced")),
            digital_only=bool(card.get("IsDigitalOnly")),
        )
    return out


def scryfall_card(obj: dict[str, Any]) -> ScryfallCard | None:
    """Return the slim record of a Scryfall card object.

    None when it is of no use here: no arena_id and not English.
    """
    arena_id = obj.get("arena_id")
    if isinstance(arena_id, bool) or not isinstance(arena_id, int):
        arena_id = None
    lang = str(obj.get("lang") or "")
    if arena_id is None and lang != "en":
        return None
    return ScryfallCard(
        scryfall_id=str(obj.get("id") or ""),
        name=str(obj.get("name") or ""),
        set=str(obj.get("set") or "").lower(),
        collector_number=str(obj.get("collector_number") or ""),
        rarity=str(obj.get("rarity") or ""),
        basic=str(obj.get("type_line") or "").startswith("Basic"),
        digital=bool(obj.get("digital")),
        lang=lang,
        arena_id=arena_id,
    )


def parse_scryfall_cards(objects: Iterable[dict[str, Any]]) -> ScryfallIndex:
    """Index the useful Scryfall card objects of *objects*."""
    slim = (scryfall_card(obj) for obj in objects)
    return ScryfallIndex.build(card for card in slim if card is not None)


def scryfall_objects(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Yield the card objects of a JSON Lines stream.

    Tolerates the array brackets and trailing commas of the pre-2026 format.
    """
    for raw in lines:
        line = raw.strip().rstrip(",")
        if not line or line in {"[", "]"}:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            yield obj


def _set_list(payload: object) -> list[object]:
    """Return the set objects of Scryfall's ``/sets`` response (or a bare list)."""
    data = payload.get("data") if isinstance(payload, dict) else payload
    return data if isinstance(data, list) else []


def parse_set_codes(payload: object) -> dict[str, str]:
    """Map ``ARENA_CODE -> scryfall set code`` (e.g. ``DAR -> dom``) from ``/sets``."""
    out: dict[str, str] = {}
    for entry in _set_list(payload):
        if isinstance(entry, dict) and entry.get("arena_code") and entry.get("code"):
            out[str(entry["arena_code"]).upper()] = str(entry["code"]).lower()
    return out


def parse_alchemy_sets(payload: object) -> frozenset[str]:
    """Return Scryfall's Alchemy set codes (``ymid`` ... ``yfra``, ``hbg``)."""
    return frozenset(
        str(entry["code"]).lower()
        for entry in _set_list(payload)
        if isinstance(entry, dict)
        and entry.get("set_type") == ALCHEMY_SET_TYPE
        and entry.get("code")
    )


def _pick(index: int, item: object) -> Pick:
    """One entry of the picks file, or TypeError / ValueError saying what is off."""
    if not isinstance(item, dict):
        msg = f"pick [{index}] is not an object"
        raise TypeError(msg)
    arena_id = item.get("arena_id")
    texts = [item.get(key) for key in ("name", "set", "collector_number")]
    if (
        isinstance(arena_id, bool)
        or not isinstance(arena_id, int)
        or arena_id <= 0
        or not all(isinstance(t, str) and t.strip() for t in texts)
    ):
        msg = (
            f"pick [{index}] needs a positive integer arena_id and name, set and "
            'collector_number as strings, e.g. "84"'
        )
        raise ValueError(msg)
    name, set_code, number = (str(t).strip() for t in texts)
    return Pick(arena_id, name, set_code.lower(), number)


def parse_picks(payload: object) -> dict[int, Pick]:
    """Read the picks file by Arena card id.

    The file is a list of objects with ``arena_id``, ``name``, ``set`` and
    ``collector_number``. Further keys (a note, say) are ignored; anything
    else raises TypeError (not a list of objects) or ValueError.
    """
    if not isinstance(payload, list):
        msg = "expected a list of picks"
        raise TypeError(msg)
    picks: dict[int, Pick] = {}
    for index, item in enumerate(payload):
        pick = _pick(index, item)
        if pick.arena_id in picks:
            msg = f"pick [{index}]: arena_id {pick.arena_id} is picked twice"
            raise ValueError(msg)
        picks[pick.arena_id] = pick
    return picks


def _normal_name(name: str) -> str:
    """Casefold *name*, with straight apostrophes and single spaces."""
    return re.sub(r"\s+", " ", name.replace("\u2019", "'")).strip().casefold()


def name_key(name: str) -> str:
    """Return the normalised front face.

    What Arena's title and Scryfall's ``Front // Back`` name have in common.
    """
    return _normal_name(name).split(" // ")[0]


def names_agree(arena_name: str | None, scryfall_name: str | None) -> bool:
    """Tell whether two names are one card.

    Equal names, or Arena's front-face title against Scryfall's ``Front // Back``.
    """
    if not arena_name or not scryfall_name:
        return False
    a, s = _normal_name(arena_name), _normal_name(scryfall_name)
    return a == s or s.startswith(a + " // ")


# ---------------------------------------------------------------- resolution


@dataclass(frozen=True)
class _Resolved:
    """The Scryfall-or-Arena half of a printing: what the match decided."""

    source: str
    name: str | None
    set: str | None
    collector_number: str | None
    rarity: str | None
    scryfall_id: str | None
    basic: bool
    digital: bool

    @classmethod
    def from_scryfall(cls, source: str, hit: ScryfallCard) -> _Resolved:
        """Scryfall's printing: its type line decides basic, Arena's flags do not."""
        return cls(
            source,
            hit.name,
            hit.set,
            hit.collector_number,
            hit.rarity,
            hit.scryfall_id,
            basic=hit.basic,
            digital=hit.digital,
        )

    @classmethod
    def from_arena(cls, card: ArenaCard | None) -> _Resolved:
        """Arena's own data (source ``arena``), or nothing (``unknown``)."""
        if card is None:
            return cls(
                "unknown", None, None, None, None, None, basic=False, digital=False
            )
        return cls(
            "arena",
            card.name or None,
            card.set.lower(),
            card.collector_number,
            card.rarity,
            None,
            basic=card.basic,
            digital=card.digital_only,
        )


class Catalog:
    """Arena's card data and Scryfall's, and the printings picked by hand."""

    def __init__(
        self,
        arena: dict[int, ArenaCard],
        scryfall: ScryfallIndex,
        set_codes: dict[str, str] | None = None,
        alchemy_sets: Iterable[str] = (),
        picks: dict[int, Pick] | None = None,
    ) -> None:
        """Hold the sources; set codes and Alchemy sets come from ``/sets``."""
        self.arena = arena
        self.scryfall = scryfall
        self.set_codes = set_codes or {}
        self.alchemy_sets = frozenset(alchemy_sets)
        self.picks = dict(picks or {})

    def candidate_set_codes(self, arena_set: str) -> list[str]:
        """Return the Scryfall set codes an Arena set code may stand for.

        The arena_code mapping first, then the code itself.
        """
        upper = arena_set.upper()
        codes = [self.set_codes[upper]] if upper in self.set_codes else []
        if arena_set.lower() not in codes:
            codes.append(arena_set.lower())
        return codes

    def by_set_number(self, card: ArenaCard) -> ScryfallCard | None:
        """Return the printing with the card's set and number, if the names agree."""
        if not card.name or not card.collector_number:
            return None
        for code in self.candidate_set_codes(card.set):
            hit = self.scryfall.by_set_number.get((code, card.collector_number))
            if hit is not None and names_agree(card.name, hit.name):
                return hit
        return None

    def name_set_codes(self, arena_set: str) -> frozenset[str]:
        """Return where ``by_set_name`` looks.

        The candidate set codes, plus every Alchemy set for Arena's yearly
        ``Y23`` ... codes.
        """
        codes = frozenset(self.candidate_set_codes(arena_set))
        if ALCHEMY_YEAR.fullmatch(arena_set):
            return codes | self.alchemy_sets
        return codes

    def by_set_name(self, card: ArenaCard) -> ScryfallCard | None:
        """Return the only English printing in the card's set(s) whose name agrees.

        None when there are none or several.
        """
        if not card.name or not card.set:
            return None
        codes = self.name_set_codes(card.set)
        hits = [
            s
            for s in self.scryfall.by_name.get(name_key(card.name), [])
            if s.set in codes and names_agree(card.name, s.name)
        ]
        return hits[0] if len(hits) == 1 else None

    def _pick_problem(self, pick: Pick) -> tuple[ScryfallCard | None, str | None]:
        """Return the picked printing, or why the pick does not hold."""
        where = f"{pick.set} {pick.collector_number}"
        hit = self.scryfall.by_set_number.get((pick.set, pick.collector_number))
        card = self.arena.get(pick.arena_id)
        if hit is None:
            return None, f"Scryfall has no English printing {where}"
        if not names_agree(pick.name, hit.name):
            return None, f"Scryfall's {where} is {hit.name!r}"
        if card is not None and card.name and not names_agree(card.name, hit.name):
            return None, f"Arena's card {pick.arena_id} is {card.name!r}"
        return hit, None

    def by_pick(self, arena_id: int) -> ScryfallCard | None:
        """Return the printing picked by hand for the card, if the pick holds."""
        pick = self.picks.get(arena_id)
        return self._pick_problem(pick)[0] if pick else None

    def pick_problems(self) -> list[str]:
        """Return one line per pick that does not hold, in arena_id order."""
        problems = []
        for arena_id in sorted(self.picks):
            pick = self.picks[arena_id]
            problem = self._pick_problem(pick)[1]
            if problem:
                problems.append(
                    f"{arena_id} {pick.name} -> {pick.set} {pick.collector_number}: "
                    f"{problem}",
                )
        return problems

    def by_arena_id(self, arena_id: int, card: ArenaCard | None) -> ScryfallCard | None:
        """Return Scryfall's printing for the arena_id.

        None when Arena names the card differently (a Scryfall data error).
        """
        hit = self.scryfall.by_arena_id.get(arena_id)
        if hit is None or card is None:
            return hit
        return hit if names_agree(card.name, hit.name) else None

    def rejected_arena_id(self, arena_id: int) -> ScryfallCard | None:
        """Return the printing a by-id match would have given but was refused."""
        card = self.arena.get(arena_id)
        hit = self.scryfall.by_arena_id.get(arena_id)
        if (
            hit is not None
            and card is not None
            and not names_agree(card.name, hit.name)
        ):
            return hit
        return None

    def _match(self, arena_id: int, card: ArenaCard | None) -> _Resolved:
        """Try the lookups in the order of the module docstring."""
        hit = self.by_pick(arena_id)
        if hit is not None:
            return _Resolved.from_scryfall("scryfall-pick", hit)
        hit = self.by_arena_id(arena_id, card)
        if hit is not None:
            return _Resolved.from_scryfall("scryfall", hit)
        if card is not None:
            hit = self.by_set_number(card)
            if hit is not None:
                return _Resolved.from_scryfall("scryfall-set", hit)
            hit = self.by_set_name(card)
            if hit is not None:
                return _Resolved.from_scryfall("scryfall-name", hit)
        return _Resolved.from_arena(card)

    def resolve(self, arena_id: int, quantity: int) -> Printing:
        """Resolve one owned Arena card (grpid and copies) to a printing."""
        card = self.arena.get(arena_id)
        found = self._match(arena_id, card)
        rebalanced = bool(found.name and found.name.startswith("A-"))
        return Printing(
            arena_id=arena_id,
            quantity=quantity,
            source=found.source,
            name=found.name,
            set=found.set,
            collector_number=found.collector_number,
            rarity=found.rarity,
            scryfall_id=found.scryfall_id,
            arena_name=(card.name or None) if card else None,
            arena_set=card.set if card else None,
            arena_collector_number=card.collector_number if card else None,
            basic=found.basic,
            digital=found.digital,
            rebalanced=(card.rebalanced if card else False) or rebalanced,
            token=card.token if card else False,
            secondary=card.secondary if card else False,
        )


# -------------------------------------------------------- download and cache


def _meta_path(dest: Path) -> Path:
    """Return the sidecar file holding the download's ETag and dates."""
    return dest.with_suffix(dest.suffix + ".meta.json")


def _read_meta(dest: Path) -> dict[str, Any]:
    """Return the sidecar of *dest*, or an empty dict."""
    try:
        meta = json.loads(_meta_path(dest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _write_meta(dest: Path, meta: dict[str, Any]) -> None:
    """Write the sidecar of *dest*."""
    text = json.dumps(meta, indent=1, sort_keys=True) + "\n"
    _meta_path(dest).write_text(text, encoding="utf-8")


def _age(path: Path) -> float:
    """Seconds since *path* was last modified."""
    return time.time() - path.stat().st_mtime


def _https_request(url: str, headers: dict[str, str]) -> urllib.request.Request:
    """Build a request for an https URL only.

    The Scryfall download URI comes from a downloaded index, so the scheme
    is checked rather than trusted.
    """
    if not url.startswith("https://"):
        msg = f"refusing to fetch a non-https URL: {url}"
        raise SystemExit(msg)
    return urllib.request.Request(url, headers=headers)  # noqa: S310 - https enforced above


def _conditional_headers(dest: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """Request headers for a conditional GET of *dest*, and its sidecar."""
    headers = dict(HEADERS)
    meta = _read_meta(dest) if dest.exists() else {}
    if meta.get("etag"):
        headers["If-None-Match"] = str(meta["etag"])
    if meta.get("last_modified"):
        headers["If-Modified-Since"] = str(meta["last_modified"])
    return headers, meta


def cached_download(
    url: str,
    dest: Path,
    *,
    max_age: float = DEFAULT_MAX_AGE,
    log: Log = _stdout,
) -> Path:
    """Make *dest* hold *url*'s content.

    Refreshed with a conditional GET once older than *max_age* seconds; a
    cached copy is kept when the network fails.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and _age(dest) < max_age:
        return dest
    headers, meta = _conditional_headers(dest)
    request = _https_request(url, headers)
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310 - https enforced by _https_request  # nosec B310
            data = response.read()
            new_meta = {
                "url": url,
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified")
                or formatdate(usegmt=True),
                "fetched_at": formatdate(usegmt=True),
            }
    except urllib.error.HTTPError as exc:
        if exc.code == HTTPStatus.NOT_MODIFIED and dest.exists():
            dest.touch()  # fresh again for max_age
            return dest
        msg = f"{url}: HTTP {exc.code}"
        raise SystemExit(msg) from None
    except urllib.error.URLError as exc:
        if dest.exists():
            when = meta.get("fetched_at", "?")
            log(f"note: {url}: {exc.reason}; using the cached copy from {when}")
            return dest
        msg = f"{url}: {exc.reason}"
        raise SystemExit(msg) from None
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    _write_meta(dest, new_meta)
    log(f"fetched {url} ({len(data) / 1e6:.1f} MB)")
    return dest


def load_arena_cards(
    cache_dir: Path,
    *,
    max_age: float = DEFAULT_MAX_AGE,
    log: Log = _stdout,
) -> dict[int, ArenaCard]:
    """Return Arena's card database, from the cache or Untapped's dump."""
    cards_path = cached_download(
        CARDS_URL, cache_dir / "cards.json", max_age=max_age, log=log
    )
    loc_path = cached_download(
        LOC_URL, cache_dir / "loc_en.json", max_age=max_age, log=log
    )
    cards = json.loads(cards_path.read_text(encoding="utf-8"))
    loc = json.loads(loc_path.read_text(encoding="utf-8"))
    if not isinstance(cards, list) or not isinstance(loc, list):
        msg = f"{CARDS_URL} / {LOC_URL}: not lists; the dump format changed"
        raise SystemExit(msg)
    return parse_arena_cards(cards, loc)


def _stream_scryfall(uri: str) -> Iterator[str]:
    """Yield the bulk file's lines, without holding ~500 MB of JSON in memory."""
    request = _https_request(uri, HEADERS)
    with urllib.request.urlopen(request, timeout=BULK_TIMEOUT) as response:  # noqa: S310 - https enforced by _https_request  # nosec B310
        gzipped = (
            uri.endswith(".gz") or response.headers.get("Content-Encoding") == "gzip"
        )
        stream = gzip.GzipFile(fileobj=response) if gzipped else response
        for raw in stream:
            yield raw.decode("utf-8")


def _read_index(slim_path: Path) -> ScryfallIndex:
    """Rebuild the index from its cached slim records."""
    data = json.loads(slim_path.read_text(encoding="utf-8"))
    return ScryfallIndex.build(ScryfallCard(**record) for record in data)


def _bulk_download_uri(bulk: object) -> tuple[str, str, float]:
    """Return the bulk file's URI, its updated_at and its gzipped size in bytes."""
    if not isinstance(bulk, dict):
        bulk = {}
    uri = bulk.get("jsonl_download_uri") or bulk.get("download_uri")
    if not isinstance(uri, str) or not uri:
        msg = f"{SCRYFALL_BULK_INDEX}: no download URI in the bulk-data index"
        raise SystemExit(msg)
    size = bulk.get("compressed_size")
    return uri, str(bulk.get("updated_at") or ""), float(size or 0)


def _parse_bulk(uri: str) -> ScryfallIndex:
    """Stream the bulk file at *uri* into an index."""
    if ".jsonl" in uri:
        return parse_scryfall_cards(scryfall_objects(_stream_scryfall(uri)))
    # the pre-2026 single JSON array
    text = "".join(_stream_scryfall(uri))
    return parse_scryfall_cards(
        obj for obj in json.loads(text) if isinstance(obj, dict)
    )


def load_scryfall_index(
    cache_dir: Path,
    *,
    max_age: float = DEFAULT_MAX_AGE,
    log: Log = _stdout,
) -> tuple[ScryfallIndex, dict[str, Any]]:
    """Return the slim Scryfall index and its metadata.

    Rebuilt only when Scryfall's bulk file has a new ``updated_at``.
    """
    index_path = cached_download(
        SCRYFALL_BULK_INDEX,
        cache_dir / "scryfall-bulk-index.json",
        max_age=max_age,
        log=log,
    )
    bulk = json.loads(index_path.read_text(encoding="utf-8"))
    uri, updated_at, size = _bulk_download_uri(bulk)
    slim_path = cache_dir / "scryfall-index.json"
    meta = _read_meta(slim_path)
    if slim_path.exists() and meta.get("updated_at") == updated_at:
        return _read_index(slim_path), meta
    log(
        f"streaming Scryfall default-cards ({size / 1e6:.0f} MB gzipped, "
        f"updated {updated_at}) ...",
    )
    started = time.monotonic()
    try:
        index = _parse_bulk(uri)
    except (urllib.error.URLError, OSError) as exc:
        if slim_path.exists():
            when = meta.get("updated_at", "?")
            log(f"note: Scryfall download failed ({exc}); using the index from {when}")
            return _read_index(slim_path), meta
        msg = f"Scryfall bulk download failed: {exc}"
        raise SystemExit(msg) from None
    meta = {
        "updated_at": updated_at,
        "source": uri,
        "fetched_at": formatdate(usegmt=True),
        "arena_cards": len(index.by_arena_id),
        "set_number_cards": len(index.by_set_number),
        "collisions": index.collisions,
    }
    tmp = slim_path.with_suffix(".tmp")
    records = [asdict(card) for card in index.cards]
    tmp.write_text(json.dumps(records, separators=(",", ":")), encoding="utf-8")
    tmp.replace(slim_path)
    _write_meta(slim_path, meta)
    log(
        f"Scryfall: {len(index.by_arena_id)} printings with an arena_id "
        f"({index.collisions} collisions), {len(index.by_set_number)} English "
        f"printings by set and number, in {time.monotonic() - started:.0f}s",
    )
    return index, meta


def load_sets(
    cache_dir: Path,
    *,
    max_age: float = DEFAULT_MAX_AGE,
    log: Log = _stdout,
) -> tuple[dict[str, str], frozenset[str]]:
    """Return the Arena set code map and the Alchemy set codes of ``/sets``."""
    path = cached_download(
        SCRYFALL_SETS, cache_dir / "scryfall-sets.json", max_age=max_age, log=log
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    codes = parse_set_codes(payload)
    if not codes:
        msg = f"{SCRYFALL_SETS}: no set carries an arena_code; the API changed"
        raise SystemExit(msg)
    alchemy = parse_alchemy_sets(payload)
    if not alchemy:
        log(
            f"note: {SCRYFALL_SETS}: no set of type {ALCHEMY_SET_TYPE!r}; Arena's "
            "Alchemy cards (Y22 ...) stay Arena-only",
        )
    return codes, alchemy


def load_picks(path: Path) -> dict[int, Pick]:
    """Return the printings picked by hand in *path*; none without the file."""
    if not path.exists():
        return {}
    try:
        return parse_picks(json.loads(path.read_text(encoding="utf-8")))
    except (TypeError, ValueError) as exc:  # a JSONDecodeError is a ValueError too
        msg = f"{path}: {exc}"
        raise SystemExit(msg) from None


def load_catalog(
    cache_dir: Path,
    *,
    scryfall: bool = True,
    max_age: float = DEFAULT_MAX_AGE,
    log: Log = _stdout,
) -> Catalog:
    """Return the catalog: Arena's card data and, unless told not to, Scryfall's."""
    arena = load_arena_cards(cache_dir, max_age=max_age, log=log)
    fetched = _fetched(cache_dir / "cards.json")
    log(f"Arena card data: {len(arena)} printings ({fetched})")
    if not scryfall:
        return Catalog(arena, ScryfallIndex.empty())
    index, meta = load_scryfall_index(cache_dir, max_age=max_age, log=log)
    set_codes, alchemy_sets = load_sets(cache_dir, max_age=max_age, log=log)
    bulk_date = str(meta.get("updated_at", "?"))[:10]
    log(
        f"Scryfall index: {len(index.by_arena_id)} printings with an arena_id, "
        f"{len(index.by_set_number)} by set and number, {len(set_codes)} Arena set "
        f"codes, {len(alchemy_sets)} Alchemy sets (bulk data of {bulk_date})",
    )
    return Catalog(arena, index, set_codes, alchemy_sets)


def _fetched(path: Path) -> str:
    """When the cached *path* was published, for the log."""
    meta = _read_meta(path)
    stamp = meta.get("last_modified") or meta.get("fetched_at")
    if not stamp:
        return "cached"
    try:
        return "dump of " + parsedate_to_datetime(str(stamp)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "cached"
