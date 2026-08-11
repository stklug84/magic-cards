#!/usr/bin/env python3
"""Generate per-set Turtle instance files for the Magic card collection.

Pipeline:
  1. fetch    - read collection.csv, resolve every printing via the Scryfall API
                (batch /cards/collection endpoint), fetch localized printings,
                set metadata and WotC (Gatherer) rulings. Results are cached.
  2. generate - emit one TTL file per set under sets/, following the modelling
                conventions of MagicCardIndividuals.ttl / MagicCardsOntology.ttl,
                and print the owl:imports block for the master file.
  3. collection - emit MagicCardCollection.ttl with one reified CollectionEntry
                per collected variant, i.e. per (printing, finish, condition)
                combination, carrying the total quantity held in that variant.
                Card individuals are resolved from the existing sets/*.ttl
                files, so this step needs no network access.

Usage:
  python3 scripts/generate_individuals.py fetch
  python3 scripts/generate_individuals.py generate
  python3 scripts/generate_individuals.py collection
"""

from __future__ import annotations

import csv
import gzip
import json
import re
import sys
import tempfile
import time
import unicodedata
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT = Path(__file__).resolve().parent.parent
# Interchange contract: build_vocab.py writes onto_vocab.json /
# existing_cards.json and update_imports.py reads imports.json from the
# platform temp dir (/tmp on the Linux CI runners, where TMPDIR is unset).
TMP = Path(tempfile.gettempdir())
CACHE = TMP / "scryfall_cache"
CACHE.mkdir(exist_ok=True)
VOCAB_JSON = TMP / "onto_vocab.json"
EXISTING_JSON = TMP / "existing_cards.json"
IMPORTS_JSON = TMP / "imports.json"
API = "https://api.scryfall.com"
UA = {
    "User-Agent": "stklug84-inventory-ttl-generator/1.0",
    "Accept": "application/json",
}
TODAY = "2026-07-19"
NS_DATE = "2026-07-19"

#: A Scryfall card (or set / bulk-data) JSON object: heterogeneous,
#: API-defined, consumed via .get() narrowing at each use site.
ScryCard = dict[str, Any]
#: (lowercase set code, collector number) identifying one printing.
PrintKey = tuple[str, str]

BASICS = {
    "Plains",
    "Island",
    "Swamp",
    "Mountain",
    "Forest",
    "Wastes",
    "Snow-Covered Plains",
    "Snow-Covered Island",
    "Snow-Covered Swamp",
    "Snow-Covered Mountain",
    "Snow-Covered Forest",
    "Snow-Covered Wastes",
}

WUBRG = ("W", "U", "B", "R", "G")
COLOR = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
RARITY = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "mythic": "MythicRare",
    "special": "Special",
    "bonus": "Bonus",
}
LEGALITY = {
    "legal": "Legal",
    "not_legal": "NotLegal",
    "banned": "Banned",
    "restricted": "Restricted",
}
FORMATS = [
    ("commander", "Commander"),
    ("standard", "Standard"),
    ("modern", "Modern"),
    ("legacy", "Legacy"),
    ("vintage", "Vintage"),
    ("pioneer", "Pioneer"),
    ("pauper", "Pauper"),
]
LANG = {"English": "English", "German": "German"}


class Printing(TypedDict):
    """One unique printing aggregated over its collection.csv rows."""

    name: str
    set: str
    num: str
    language: str
    count: int


class RulingEntry(TypedDict):
    """One WotC ruling as cached by the fetch step."""

    date: str
    text: str


@dataclass
class Notes:
    """Anomalies collected while generating, reported at the end."""

    #: new individual -> (source label, vocabulary class) for subtypes
    #: missing from the ontology
    subtypes: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: keywords without an ontology individual (emitted as TTL comments)
    keywords: set[str] = field(default_factory=set)
    #: printings skipped because the master file already models them
    skipped: list[tuple[str, PrintKey]] = field(default_factory=list)
    #: printings the Scryfall cache has no data for
    missing: list[PrintKey] = field(default_factory=list)


@dataclass
class PrintingInfo:
    """Per-printing context for one card block."""

    set_ind: str
    language: str
    localized: ScryCard | None


@dataclass
class GenContext:
    """Ontology vocabulary and shared lookup tables for block generation."""

    vocab: dict[str, Any]
    sub_by_label: dict[str, str]
    kw_map: dict[str, tuple[str, str]]
    rulings: dict[str, list[RulingEntry]]
    notes: Notes


# ---------------------------------------------------------------- helpers


def _https_request(url: str, data: bytes | None = None) -> urllib.request.Request:
    """Build a Request for the URL, refusing any scheme other than https."""
    if not url.startswith("https://"):
        msg = f"refusing to fetch non-https URL: {url}"
        raise ValueError(msg)
    return urllib.request.Request(  # noqa: S310 - https enforced above  # nosec B310
        url,
        headers=UA,
        data=data,
    )


def http_json(url: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET (or POST, when data is given) an https URL and decode the JSON."""
    req = _https_request(url, json.dumps(data).encode() if data else None)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - https enforced by _https_request  # nosec B310
        payload: dict[str, Any] = json.loads(r.read().decode())
    return payload


def pascal(name: str) -> str:
    """'Adrix and Nev, Twincasters' -> 'AdrixAndNevTwincasters'."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", "").replace("\u2019", "")
    tokens = re.split(r"[^A-Za-z0-9]+", s)
    return "".join(t[0].upper() + t[1:] if t else "" for t in tokens)


def esc_str(s: str) -> str:
    """Escape a value for a single-quoted Turtle string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def esc_long(s: str) -> str:
    """Escape a value for a triple-quoted Turtle string literal."""
    s = s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if s.endswith('"'):
        s = s[:-1] + '\\"'
    return s


# ---------------------------------------------------------------- fetch


def collection_csv_path() -> Path:
    """Return the inventory CSV path, or exit with guidance when absent.

    collection.csv is a local, untracked input (it is deliberately not
    distributed with the repository); regeneration only works where the
    inventory is present.
    """
    path = ROOT / "collection.csv"
    if not path.exists():
        msg = (
            "collection.csv not found - it is a local, untracked input "
            "(not distributed with the repository); restore your "
            "inventory export to the repo root before regenerating."
        )
        raise SystemExit(msg)
    return path


def load_rows() -> dict[PrintKey, Printing]:
    """Aggregate collection.csv rows into unique printings with counts."""
    with collection_csv_path().open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    printings: dict[PrintKey, Printing] = {}
    for r in rows:
        key = (r["Edition"].lower(), r["Collector Number"])
        if key not in printings:
            printings[key] = {
                "name": r["Name"],
                "set": key[0],
                "num": key[1],
                "language": r["Language"],
                "count": 0,
            }
        printings[key]["count"] += int(r["Count"])
        if r["Language"] != "English":
            printings[key]["language"] = r["Language"]
    return printings


def _fetch_cards(keys: list[PrintKey]) -> dict[str, ScryCard]:
    """Resolve every uncached printing via the batch collection endpoint."""
    cards_file = CACHE / "cards.json"
    cards: dict[str, ScryCard] = (
        json.loads(cards_file.read_text()) if cards_file.exists() else {}
    )
    todo = [k for k in keys if f"{k[0]}|{k[1]}" not in cards]
    not_found: list[Any] = []
    for i in range(0, len(todo), 75):
        batch = todo[i : i + 75]
        idents = [{"set": s, "collector_number": n} for s, n in batch]
        resp = http_json(f"{API}/cards/collection", {"identifiers": idents})
        not_found.extend(resp.get("not_found", []))
        for c in resp["data"]:
            cards[f"{c['set']}|{c['collector_number']}"] = c
        print(  # noqa: T201 - generator progress output
            f"  batch {i // 75 + 1}: {len(resp['data'])} found, "
            f"{len(resp.get('not_found', []))} missing",
        )
        cards_file.write_text(json.dumps(cards))
        time.sleep(0.15)
    if not_found:
        print("NOT FOUND:", json.dumps(not_found, indent=2))  # noqa: T201 - generator report output
    return cards


def _fetch_localized(printings: dict[PrintKey, Printing]) -> None:
    """Fetch the localized printing for every non-English collection row."""
    loc_file = CACHE / "localized.json"
    localized: dict[str, ScryCard] = (
        json.loads(loc_file.read_text()) if loc_file.exists() else {}
    )
    for k in sorted(printings):
        p = printings[k]
        if p["language"] == "English":
            continue
        lid = f"{k[0]}|{k[1]}"
        if lid in localized:
            continue
        code = {"German": "de"}[p["language"]]
        try:
            localized[lid] = http_json(f"{API}/cards/{k[0]}/{k[1]}/{code}")
        except Exception as e:  # noqa: BLE001
            print(f"  localized fetch failed for {lid}: {e}")  # noqa: T201 - generator progress output
        time.sleep(0.15)
    loc_file.write_text(json.dumps(localized))


def _fetch_sets(keys: list[PrintKey]) -> None:
    """Fetch the set metadata for every collected set code."""
    sets_file = CACHE / "sets.json"
    sets_meta: dict[str, ScryCard] = (
        json.loads(sets_file.read_text()) if sets_file.exists() else {}
    )
    for code in sorted({k[0] for k in keys}):
        if code not in sets_meta:
            sets_meta[code] = http_json(f"{API}/sets/{code}")
            time.sleep(0.1)
    sets_file.write_text(json.dumps(sets_meta))


def _fetch_rulings(cards: dict[str, ScryCard]) -> None:
    """Filter the Scryfall bulk rulings file down to our oracle ids (WotC only)."""
    rulings_file = CACHE / "rulings.json"
    if rulings_file.exists():
        return
    oracle_ids = set()
    for c in cards.values():
        oid = c.get("oracle_id") or (c.get("card_faces") or [{}])[0].get(
            "oracle_id",
        )
        if oid:
            oracle_ids.add(oid)
    bulk = http_json(f"{API}/bulk-data")
    uri = next(b["jsonl_download_uri"] for b in bulk["data"] if b["type"] == "rulings")
    print("downloading bulk rulings ...")  # noqa: T201 - generator progress output
    req = _https_request(uri)
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 - https enforced by _https_request  # nosec B310
        raw = r.read()
    if uri.endswith(".gz"):
        raw = gzip.decompress(raw)
    allr = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    mine = defaultdict(list)
    for ru in allr:
        if ru["oracle_id"] in oracle_ids and ru["source"] == "wotc":
            mine[ru["oracle_id"]].append(
                {"date": ru["published_at"], "text": ru["comment"]},
            )
    rulings_file.write_text(json.dumps(mine))
    print(f"kept rulings for {len(mine)} cards")  # noqa: T201 - generator report output


def cmd_fetch() -> None:
    """Resolve every collection.csv printing via Scryfall into the cache."""
    printings = load_rows()
    keys = sorted(printings)
    print(f"{len(printings)} unique printings")  # noqa: T201 - generator progress output
    cards = _fetch_cards(keys)
    _fetch_localized(printings)
    _fetch_sets(keys)
    _fetch_rulings(cards)
    print("fetch complete")  # noqa: T201 - generator report output


# ---------------------------------------------------------------- generate


def norm_label(s: str) -> str:
    """Normalize a label for lookups (straight apostrophe, lowercase)."""
    return s.replace("\u2019", "'").lower()


def load_vocab() -> tuple[dict[str, Any], dict[str, str], dict[str, tuple[str, str]]]:
    """Load the build_vocab.py extract and derive the lookup tables.

    Returns the raw vocabulary, the normalized-label -> SubType-individual
    map and the keyword -> (property, individual) map.
    """
    v: dict[str, Any] = json.loads(VOCAB_JSON.read_text())
    labels = v["_labels"]
    sub_by_label: dict[str, str] = {}
    for ind in v["SubType"]:
        sub_by_label[norm_label(labels.get(ind, ind))] = ind
        sub_by_label[norm_label(ind)] = ind
    kw: dict[str, tuple[str, str]] = {}
    for cls, prop in (
        ("KeywordAbility", "hasKeywordAbility"),
        ("KeywordAction", "hasKeywordAction"),
        ("Keyword", "hasKeyword"),
    ):
        for ind in v[cls]:
            key = labels.get(ind, ind)
            kw.setdefault(key, (prop, ind))
            kw.setdefault(ind, (prop, ind))
    return v, sub_by_label, kw


TYPE_CLASS = {
    "Creature": "CreatureTypes",
    "Artifact": "ArtifactTypes",
    "Enchantment": "EnchantmentTypes",
    "Planeswalker": "PlaneswalkerTypes",
    "Instant": "SpellTypes",
    "Sorcery": "SpellTypes",
    "Land": "LandTypes",
    "Plane": "PlanarTypes",
    "Battle": "BattleType",
    "Dungeon": "DungeonType",
    "Kindred": "CreatureTypes",
}


def tokenize_subtypes(
    right: str,
    types: list[str],
    sub_by_label: dict[str, str],
) -> list[str]:
    """Greedy longest-match against known (multi-word) subtype labels."""
    right = right.strip()
    if not right:
        return []
    if "Plane" in types:  # a plane's whole subtype line is one plane name
        return [right]
    words = right.split()
    subs: list[str] = []
    i = 0
    while i < len(words):
        for n in range(min(4, len(words) - i), 0, -1):
            cand = " ".join(words[i : i + n])
            if norm_label(cand) in sub_by_label or n == 1:
                subs.append(cand)
                i += n
                break
    return subs


def parse_type_line(
    tl: str,
    vocab: dict[str, Any],
    sub_by_label: dict[str, str],
    layout: str = "",
) -> tuple[list[str], list[str], list[str]]:
    """Parse a Scryfall type_line into (supertypes, card types, subtypes).

    Multi-face type lines ("A // B") are unioned, EXCEPT for the
    adventure layout: per CR 715.2 an adventurer card has only its
    primary (left) face's characteristics anywhere except on the stack
    as an Adventure, so the right face contributes its subtypes (the
    Adventure spell type) but neither its card types nor supertypes.
    Unioning them (e.g. Creature + Instant for "Legendary Creature -
    Human Scientist // Instant - Adventure") makes the knowledge graph
    OWL-inconsistent: SpellCardConstraint forbids creature subtypes on
    instants/sorceries (CreatureTypes and SpellTypes are disjoint).
    """
    supers: list[str] = []
    types: list[str] = []
    subs: list[str] = []
    for idx, part in enumerate(tl.split(" // ")):
        left, _, right = part.partition("\u2014")
        secondary_adventure_face = layout == "adventure" and idx > 0
        if not secondary_adventure_face:
            for w in left.split():
                word = "Kindred" if w == "Tribal" else w
                if word in vocab["SuperType"] and word not in supers:
                    supers.append(word)
                elif word in vocab["CardType"] and word not in types:
                    types.append(word)
        for sub in tokenize_subtypes(right, types, sub_by_label):
            if sub not in subs:
                subs.append(sub)
    return supers, types, subs


def object_list(prop: str, objects: Sequence[str], indent: str = "    ") -> list[str]:
    """Render one predicate with a comma-separated Turtle object list.

    Turtle's ',' groups several objects under a single predicate. Repeating
    the predicate line per object is equivalent RDF but redundant, so the
    house style is the object list; continuation lines align under the
    first object. Returns [] when there is nothing to assert.

    Consumers that read the generated files with regexes (notably
    mtgcards.ttl_loader) must parse these lists rather than a single
    object per predicate.
    """
    if not objects:
        return []
    head = f"{indent}{prop} "
    if len(objects) == 1:
        return [f"{head}{objects[0]} ;"]
    pad = " " * len(head)
    lines = [f"{head}{objects[0]} ,"]
    lines.extend(f"{pad}{obj} ," for obj in objects[1:-1])
    lines.append(f"{pad}{objects[-1]} ;")
    return lines


PRODUCED_ORDER = ["W", "U", "B", "R", "G", "C"]
PRODUCED_IND = {
    "W": "White",
    "U": "Blue",
    "B": "Black",
    "R": "Red",
    "G": "Green",
    "C": "Colorless",
}
_TAPPED_RE = re.compile(r"enters (?:the battlefield )?tapped(?! unless)", re.IGNORECASE)
_FETCH_RE = re.compile(r"search your librar(?:y|ies) for [^.]*land", re.IGNORECASE)


def mana_fact_lines(card: ScryCard) -> list[str]:
    """Turtle lines for :producesMana / :entersTapped / :isFetchLand.

    :producesMana comes from Scryfall's structured produced_mana field;
    :entersTapped is asserted only for unconditional taplands ("... unless"
    forms are omitted); :isFetchLand for sacrifice-to-search lands that do
    not produce mana themselves.
    """
    produced = [s for s in card.get("produced_mana") or [] if s in PRODUCED_IND]
    lines = object_list(
        ":producesMana",
        [f":{PRODUCED_IND[sym]}" for sym in sorted(produced, key=PRODUCED_ORDER.index)],
    )
    faces = card.get("card_faces") or [card]
    oracle = "\n".join(f.get("oracle_text", "") for f in faces)
    type_line = card.get("type_line") or faces[0].get("type_line", "")
    is_land = "Land" in type_line
    if is_land and _TAPPED_RE.search(oracle):
        lines.append('    :entersTapped "true"^^xsd:boolean ;')
    if is_land and not produced and _FETCH_RE.search(oracle):
        lines.append('    :isFetchLand "true"^^xsd:boolean ;')
    return lines


def _identity_lines(ind: str, card: ScryCard, front: ScryCard) -> list[str]:
    """Block opener: individual declaration, name, mana cost and value."""
    out = [
        f":{ind} rdf:type owl:NamedIndividual ,",
        "                  :Card ;",
        f'    :cardName "{esc_str(card["name"])}" ;',
    ]
    mc = front.get("mana_cost") or card.get("mana_cost") or ""
    if mc:
        out.append(f'    :manaCost "{esc_str(mc)}" ;')
    cmc = card.get("cmc", front.get("cmc", 0)) or 0
    out.append(f'    :manaValue "{int(cmc)}"^^xsd:nonNegativeInteger ;')
    return out


def _type_lines(card: ScryCard, front: ScryCard, ctx: GenContext) -> list[str]:
    """Super/card/subtype triples; notes subtypes missing from the ontology."""
    supers, types, subs = parse_type_line(
        card.get("type_line") or front.get("type_line", ""),
        ctx.vocab,
        ctx.sub_by_label,
        layout=card.get("layout", ""),
    )
    out = object_list(":hasSuperType", [f":{s}" for s in supers])
    out.extend(object_list(":hasCardType", [f":{t}" for t in types]))
    targets: list[str] = []
    for sub in subs:
        target = ctx.sub_by_label.get(norm_label(sub)) or ctx.sub_by_label.get(
            norm_label(sub.replace("-", "")),
        )
        if target is None:
            target = pascal(sub)
            cls = TYPE_CLASS.get(types[0] if types else "Creature", "CreatureTypes")
            ctx.notes.subtypes.setdefault(target, (sub, cls))
        targets.append(f":{target}")
    out.extend(object_list(":hasSubType", targets))
    return out


def _color_lines(card: ScryCard, faces: list[ScryCard]) -> list[str]:
    """Rarity, color and color-identity triples (WUBRG order)."""
    out = [f"    :hasRarity :{RARITY[card['rarity']]} ;"]
    colors = card.get("colors")
    if colors is None:
        colors = sorted({c for f in faces for c in f.get("colors", [])})
    out.extend(
        object_list(":hasColor", [f":{COLOR[c]}" for c in WUBRG if c in colors]),
    )
    identity = card.get("color_identity", [])
    out.extend(
        object_list(
            ":hasColorIdentity",
            [f":{COLOR[c]}" for c in WUBRG if c in identity],
        ),
    )
    return out


def _printing_lines(card: ScryCard, info: PrintingInfo) -> list[str]:
    """Set membership, collector number, artist, language and mana facts."""
    out = [f"    :isInSet :{info.set_ind} ;"]
    num = card["collector_number"]
    if num.isdigit():
        out.append(f'    :cardNumber "{num}"^^xsd:integer ;')
    else:
        out.append(f'    :cardNumberString "{esc_str(num)}" ;')
    if card.get("artist"):
        out.append(f'    :artist "{esc_str(card["artist"])}" ;')
    out.append(f"    :hasLanguage :{LANG[info.language]} ;")
    out.extend(mana_fact_lines(card))
    return out


def _stat_lines(front: ScryCard) -> list[str]:
    """Power / toughness / loyalty / defense triples of the front face."""
    out = []
    if front.get("power") is not None:
        out.append(f'    :power "{esc_str(front["power"])}" ;')
        if re.fullmatch(r"-?\d+", front["power"]):
            out.append(f'    :powerValue "{front["power"]}"^^xsd:integer ;')
    if front.get("toughness") is not None:
        out.append(f'    :toughness "{esc_str(front["toughness"])}" ;')
        if re.fullmatch(r"-?\d+", front["toughness"]):
            out.append(f'    :toughnessValue "{front["toughness"]}"^^xsd:integer ;')
    if front.get("loyalty") and re.fullmatch(r"\d+", front["loyalty"]):
        out.append(f'    :loyalty "{front["loyalty"]}"^^xsd:integer ;')
    if front.get("defense") and re.fullmatch(r"\d+", front["defense"]):
        out.append(f'    :defenseValue "{front["defense"]}"^^xsd:nonNegativeInteger ;')
    return out


def _keyword_lines(card: ScryCard, ctx: GenContext) -> list[str]:
    """Keyword triples; unknown keywords become comments and a note.

    Keywords are bucketed per property (:hasKeywordAbility /
    :hasKeywordAction) so each is emitted once with an object list;
    buckets keep first-appearance order. Notes for keywords the ontology
    does not define follow the triples.
    """
    buckets: dict[str, list[str]] = defaultdict(list)
    notes: list[str] = []
    for kw in card.get("keywords", []):
        hit = ctx.kw_map.get(kw) or ctx.kw_map.get(pascal(kw))
        if hit:
            buckets[hit[0]].append(f":{hit[1]}")
        else:
            notes.append(
                f'    # Note: keyword "{kw}" not defined in MagicCardsOntology',
            )
            ctx.notes.keywords.add(kw)
    out: list[str] = []
    for prop, objects in buckets.items():
        out.extend(object_list(f":{prop}", objects))
    out.extend(notes)
    return out


def _text_lines(faces: list[ScryCard], info: PrintingInfo) -> list[str]:
    """Emit the printed / oracle / flavor text triples (localized if known)."""
    out = []
    loc = info.localized
    lang = "de" if loc else "en"
    lfaces = (loc.get("card_faces") or [loc]) if loc else faces
    printed = "\n//\n".join(
        f.get("printed_text") or f.get("oracle_text", "") for f in lfaces
    )
    oracle = "\n//\n".join(f.get("oracle_text", "") for f in faces)
    if printed:
        out.append(f'    :printedText """{esc_long(printed)}"""@{lang} ;')
    if oracle:
        out.append(f'    :oracleText """{esc_long(oracle)}"""@en ;')
    flavor_src = lfaces if loc else faces
    flavor = "\n//\n".join(f["flavor_text"] for f in flavor_src if f.get("flavor_text"))
    if flavor:
        out.append(f'    :flavorText """{esc_long(flavor)}"""@{lang} ;')
    return out


def _url_lines(card: ScryCard) -> list[str]:
    """Gatherer (when a multiverse id exists) and Scryfall URL triples."""
    out = []
    mvids = card.get("multiverse_ids") or []
    if mvids:
        out.append(
            f'    :gathererUrl "https://gatherer.wizards.com/Pages/Card/Details.aspx'
            f'?multiverseid={mvids[0]}"^^xsd:anyURI ;',
        )
    surl = card["scryfall_uri"].split("?")[0]
    out.append(f'    :scryfallUrl "{surl}"^^xsd:anyURI ;')
    return out


def _legality_lines(card: ScryCard, ctx: GenContext) -> list[str]:
    """Legality mappings and rulings; terminates the block with '.'."""
    out = []
    leg = card["legalities"]
    leg_lines = [
        f"      [ rdf:type :LegalityMapping ; :inFormat :{f_ind} ; "
        f":hasLegalityStatus :{LEGALITY[leg[f_key]]} ]"
        for f_key, f_ind in FORMATS
    ]
    rus = ctx.rulings.get(
        card.get("oracle_id") or (card.get("card_faces") or [{}])[0].get("oracle_id"),
        [],
    )
    if rus:
        out.append("    :hasLegality")
        out.append(" ,\n".join(leg_lines) + " ;")
        out.append("    :hasRuling")
        ru_lines = [
            "      [ rdf:type :Ruling ;\n"
            f'        :rulingDate "{ru["date"]}"^^xsd:date ;\n'
            f'        :rulingText """{esc_long(ru["text"])}"""@en ]'
            for ru in rus
        ]
        out.append(" ,\n".join(ru_lines) + " .")
    else:
        out.append("    :hasLegality")
        out.append(" ,\n".join(leg_lines) + " .")
    return out


def card_block(ind: str, card: ScryCard, info: PrintingInfo, ctx: GenContext) -> str:
    """Render one card individual as a Turtle block (one section per helper)."""
    faces: list[ScryCard] = card.get("card_faces") or [card]
    front = faces[0]
    out = _identity_lines(ind, card, front)
    out.extend(_type_lines(card, front, ctx))
    out.extend(_color_lines(card, faces))
    out.extend(_printing_lines(card, info))
    out.extend(_stat_lines(front))
    out.extend(_keyword_lines(card, ctx))
    out.extend(_text_lines(faces, info))
    out.extend(_url_lines(card))
    out.extend(_legality_lines(card, ctx))
    return "\n".join(out)


HEADER = """\
# ==============================================================================
# DISCLAIMER
#
# This file is an independent, fan-made instance graph that lists the card
# individuals of a personal Magic: The Gathering card collection printed in
# the set "{set_name}". It is not produced by, endorsed by, or affiliated
# with Wizards of the Coast LLC.
#
# Magic: The Gathering is a trademark of Wizards of the Coast LLC. All card
# names, card text, rules text, and game terminology referenced herein are the
# intellectual property of Wizards of the Coast LLC and/or their respective
# owners. This work is made available under the Wizards of the Coast Fan
# Content Policy (https://company.wizards.com/en/legal/fancontentpolicy).
#
# Card data sourced from Gatherer (https://gatherer.wizards.com) and Scryfall
# (https://scryfall.com) under their respective data distribution policies.
#
# This file is generated by scripts/generate_individuals.py.
# Do not edit manually.
# ==============================================================================

@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .

@prefix :     <urn:stklug84:MagicCardsOntology:2026-02-27#> .
@base         <{urn}> .

################################################################################
#
# Ontology Definition
#
################################################################################

<{urn}>
    rdf:type owl:Ontology ;
    owl:imports <urn:stklug84:MagicCardsOntology:2026-02-27#> ;
    rdfs:label "Magic Card Collection Individuals \u2013 {set_name}"@en ;
    rdfs:comment \"\"\"An instance graph describing the card individuals of a
personal Magic: The Gathering card collection that were printed in the set
{set_name} ({set_code}). One Card individual is defined per collected
printing, carrying its characteristics, format legalities and official
rulings. Card data was sourced from Gatherer and Scryfall. This file imports
MagicCardsOntology to reuse all existing class, property, set, keyword,
subtype, format and legality definitions.\"\"\"@en .

"""


@dataclass
class GenerateInputs:
    """The cached fetch results and derived name tables for one generate run."""

    printings: dict[PrintKey, Printing]
    cards: dict[str, ScryCard]
    localized: dict[str, ScryCard]
    sets_meta: dict[str, ScryCard]
    set_ind: dict[str, str]
    new_sets: dict[str, str]
    existing_keys: set[tuple[str, str, str]]
    existing_names: set[str]


def _resolve_set_individuals(
    printings: dict[PrintKey, Printing],
    setcodes: dict[str, str],
    sets_meta: dict[str, ScryCard],
) -> tuple[dict[str, str], dict[str, str]]:
    """Map set codes to Set individuals, minting names for unknown sets."""
    new_sets: dict[str, str] = {}
    set_ind: dict[str, str] = {}
    for code in sorted({k[0] for k in printings}):
        up = code.upper()
        if up in setcodes:
            set_ind[code] = setcodes[up]
        else:
            ind = pascal(sets_meta[code]["name"])
            set_ind[code] = ind
            new_sets[code] = ind
    return set_ind, new_sets


def _individual_name(
    p: Printing,
    key: PrintKey,
    per_name: dict[str, list[PrintKey]],
    inputs: GenerateInputs,
) -> str:
    """Choose the individual name; suffix set+number to avoid collisions."""
    base = pascal(p["name"].split(" // ")[0])
    multi = len(per_name[p["name"]]) > 1
    if p["name"] in BASICS or multi or base in inputs.existing_names:
        return f"{base}{inputs.set_ind[key[0]]}{re.sub(r'[^A-Za-z0-9]', '', key[1])}"
    return base


def _generate_blocks(
    inputs: GenerateInputs,
    ctx: GenContext,
) -> tuple[dict[str, list[str]], dict[str, PrintKey]]:
    """Render every new printing into per-set Turtle blocks.

    Skips printings already modelled in the master file and aborts on
    individual-name collisions. Returns the blocks grouped by set code
    and the name -> printing map of emitted individuals.
    """
    per_name: dict[str, list[PrintKey]] = defaultdict(list)
    for k, p in inputs.printings.items():
        per_name[p["name"]].append(k)

    used: dict[str, PrintKey] = {}
    blocks_by_set: dict[str, list[str]] = defaultdict(list)
    for key in sorted(
        inputs.printings,
        key=lambda k: (
            inputs.set_ind[k[0]],
            (0, int(k[1])) if k[1].isdigit() else (1, 0),
            k[1],
        ),
    ):
        p = inputs.printings[key]
        card = inputs.cards.get(f"{key[0]}|{key[1]}")
        if card is None:
            ctx.notes.missing.append(key)
            continue
        if (p["name"], key[0], key[1]) in inputs.existing_keys:
            ctx.notes.skipped.append((p["name"], key))
            continue
        ind = _individual_name(p, key, per_name, inputs)
        if ind in used:
            msg = f"individual name collision: {ind} ({used[ind]} vs {key})"
            raise SystemExit(msg)
        used[ind] = key
        info = PrintingInfo(
            set_ind=inputs.set_ind[key[0]],
            language=p["language"],
            localized=inputs.localized.get(f"{key[0]}|{key[1]}"),
        )
        blocks_by_set[key[0]].append(card_block(ind, card, info, ctx))
    return blocks_by_set, used


def _write_set_files(
    blocks_by_set: dict[str, list[str]],
    inputs: GenerateInputs,
) -> list[tuple[str, str, int]]:
    """Write one sets/<SetIndividual>.ttl per set; return the import entries."""
    (ROOT / "sets").mkdir(exist_ok=True)
    imports: list[tuple[str, str, int]] = []
    for code in sorted(blocks_by_set, key=lambda c: inputs.set_ind[c]):
        sname = inputs.sets_meta[code]["name"]
        urn = f"urn:stklug84:MagicCardIndividuals:{code.upper()}:{NS_DATE}#"
        parts = [HEADER.format(set_name=sname, set_code=code.upper(), urn=urn)]
        if code in inputs.new_sets:
            sm = inputs.sets_meta[code]
            parts.append(
                "#" * 80 + "\n#\n# Set Individual (not defined in "
                "MagicCardsOntology)\n#\n" + "#" * 80 + "\n\n"
                f":{inputs.new_sets[code]} rdf:type owl:NamedIndividual ,\n"
                "                  :Set ;\n"
                f'    :setName "{esc_str(sm["name"])}" ;\n'
                f'    :setCode "{code.upper()}" ;\n'
                f'    :cardCount "{sm["card_count"]}"^^xsd:integer ;\n'
                f'    :releaseDate "{sm["released_at"]}"^^xsd:date .\n',
            )
        # missing subtypes used by cards of this set
        parts.append("#" * 80 + "\n#\n# Card Individuals\n#\n" + "#" * 80 + "\n")
        parts.append("\n\n".join(blocks_by_set[code]))
        out = ROOT / "sets" / f"{inputs.set_ind[code]}.ttl"
        out.write_text("\n".join(parts) + "\n")
        imports.append((urn, out.name, len(blocks_by_set[code])))
    return imports


def _write_subtype_supplement(
    notes: Notes,
    imports: list[tuple[str, str, int]],
) -> None:
    """Write sets/SubTypeSupplement.ttl for subtypes the ontology lacks."""
    if not notes.subtypes:
        return
    urn = f"urn:stklug84:MagicCardIndividuals:SubTypeSupplement:{NS_DATE}#"
    lines = [
        HEADER.format(set_name="SubType Supplement", set_code="SUPPLEMENT", urn=urn),
    ]
    lines.append(
        "#" * 80 + "\n#\n# SubType individuals referenced by the "
        "collection but not defined in\n# MagicCardsOntology\n#\n" + "#" * 80 + "\n",
    )
    lines.extend(
        f":{ind} rdf:type owl:NamedIndividual ,\n"
        f"                  :{cls} ;\n"
        f'    rdfs:label "{esc_str(label)}" .\n'
        for ind, (label, cls) in sorted(notes.subtypes.items())
    )
    out = ROOT / "sets" / "SubTypeSupplement.ttl"
    out.write_text("\n".join(lines))
    imports.insert(0, (urn, out.name, len(notes.subtypes)))


def _report_generate(
    used: dict[str, PrintKey],
    blocks_by_set: dict[str, list[str]],
    notes: Notes,
    imports: list[tuple[str, str, int]],
) -> None:
    """Print the generate summary and write the update_imports.py input."""
    # T201+RUF100 (below): this generator's program output, consumed by
    print(f"cards written: {len(used)}  files: {len(blocks_by_set)}")  # noqa: T201
    print(f"skipped (already in master): {len(notes.skipped)}")  # noqa: T201
    if notes.missing:
        print("MISSING from scryfall:", notes.missing)  # noqa: T201
    if notes.subtypes:
        print("new subtypes:", sorted(notes.subtypes))  # noqa: T201
    if notes.keywords:
        print("unknown keywords (comment notes):", sorted(notes.keywords))  # noqa: T201
    IMPORTS_JSON.write_text(
        json.dumps(
            [{"urn": u, "file": f, "cards": n} for u, f, n in imports],
            indent=2,
        ),
    )


def cmd_generate() -> None:
    """Emit the per-set TTL files from the cached fetch results."""
    vocab, sub_by_label, kw_map = load_vocab()
    setcodes: dict[str, str] = vocab["_setcodes"]  # CODE -> individual
    printings = load_rows()
    cards: dict[str, ScryCard] = json.loads((CACHE / "cards.json").read_text())
    localized: dict[str, ScryCard] = json.loads((CACHE / "localized.json").read_text())
    sets_meta: dict[str, ScryCard] = json.loads((CACHE / "sets.json").read_text())
    rulings: dict[str, list[RulingEntry]] = json.loads(
        (CACHE / "rulings.json").read_text(),
    )
    existing: list[dict[str, str]] = json.loads(EXISTING_JSON.read_text())

    # skip-list: (name, setcode, number) of individuals already in the master file
    inv_sets = {ind: code for code, ind in setcodes.items()}
    existing_keys = {
        (e["name"], (inv_sets.get(e["set"]) or "").lower(), e["num"]) for e in existing
    }
    existing_names = {e["ind"] for e in existing}

    set_ind, new_sets = _resolve_set_individuals(printings, setcodes, sets_meta)
    inputs = GenerateInputs(
        printings=printings,
        cards=cards,
        localized=localized,
        sets_meta=sets_meta,
        set_ind=set_ind,
        new_sets=new_sets,
        existing_keys=existing_keys,
        existing_names=existing_names,
    )
    notes = Notes()
    ctx = GenContext(
        vocab=vocab,
        sub_by_label=sub_by_label,
        kw_map=kw_map,
        rulings=rulings,
        notes=notes,
    )
    blocks_by_set, used = _generate_blocks(inputs, ctx)
    imports = _write_set_files(blocks_by_set, inputs)
    _write_subtype_supplement(notes, imports)
    _report_generate(used, blocks_by_set, notes, imports)


# ---------------------------------------------------------------- collection

FINISH = {"": "Nonfoil", "foil": "Foil", "etched": "EtchedFoil"}
CONDITION = {
    "Near Mint": "NearMint",
    "Lightly Played": "LightlyPlayed",
    "Moderately Played": "ModeratelyPlayed",
    "Heavily Played": "HeavilyPlayed",
    "Damaged": "Damaged",
}
#: emission order of the entries of one printing (best finish/condition first)
FINISH_ORDER = ["Nonfoil", "Foil", "EtchedFoil"]
CONDITION_ORDER = [
    "NearMint",
    "LightlyPlayed",
    "ModeratelyPlayed",
    "HeavilyPlayed",
    "Damaged",
]

#: one collected variant: (card individual, set file, finish, condition)
VariantKey = tuple[str, str, str, str]

COLLECTION_HEADER = """\
# ==============================================================================
# DISCLAIMER
#
# This file is an independent, fan-made instance graph that describes the
# physical inventory of a personal Magic: The Gathering card collection. It is
# not produced by, endorsed by, or affiliated with Wizards of the Coast LLC.
#
# Magic: The Gathering is a trademark of Wizards of the Coast LLC. All card
# names, card text, rules text, and game terminology referenced herein are the
# intellectual property of Wizards of the Coast LLC and/or their respective
# owners. This work is made available under the Wizards of the Coast Fan
# Content Policy (https://company.wizards.com/en/legal/fancontentpolicy).
#
# This file is generated by scripts/generate_individuals.py.
# Do not edit manually.
# ==============================================================================

@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .

@prefix :     <urn:stklug84:MagicCardsOntology:2026-02-27#> .
@base         <urn:stklug84:MagicCardCollection:{ns_date}#> .

################################################################################
#
# Ontology Definition
#
################################################################################

<urn:stklug84:MagicCardCollection:{ns_date}#>
    rdf:type owl:Ontology ;
    owl:imports <urn:stklug84:MagicCardsOntology:2026-02-27#> ,
                <urn:stklug84:MagicCardIndividuals:{ns_date}#> ;
    rdfs:label "Magic Card Collection Inventory"@en ;
    rdfs:comment \"\"\"An instance graph describing the physical inventory of a
personal Magic: The Gathering card collection. Defines the Collection
individual and one reified CollectionEntry per distinct combination of
printing, finish and condition, carrying the total number of copies held
in that variant. Card individuals are defined in the per-set instance
graphs aggregated by MagicCardIndividuals.\"\"\"@en .
"""


def load_card_map() -> dict[tuple[str, str], tuple[str, str]]:
    """Scan sets/*.ttl -> {(SETCODE, collector_number): (individual, file)}."""
    card_map: dict[tuple[str, str], tuple[str, str]] = {}
    for path in sorted((ROOT / "sets").glob("*.ttl")):
        text = path.read_text()
        code_match = re.search(
            r"@base\s+<urn:stklug84:MagicCardIndividuals:"
            r"([^:>]+):[^>]*>",
            text,
        )
        if code_match is None:
            msg = f"{path.name}: no MagicCardIndividuals @base declaration"
            raise SystemExit(msg)
        code = code_match.group(1)
        blocks = re.split(r"\n(?=:\w+ rdf:type owl:NamedIndividual)", text)
        for b in blocks[1:]:
            ind_match = re.match(r":(\w+)", b)
            if ind_match is None:  # unreachable: the split anchors on ':\w+'
                msg = f"{path.name}: malformed individual block"
                raise SystemExit(msg)
            ind = ind_match.group(1)
            m = re.search(r':cardNumber "([^"]+)"', b) or re.search(
                r':cardNumberString "([^"]+)"',
                b,
            )
            if m is None:
                continue  # not a card individual (e.g. Set / SubType)
            key = (code.upper(), m.group(1))
            if key in card_map:
                msg = f"duplicate printing {key}: {card_map[key][0]} vs {ind}"
                raise SystemExit(
                    msg,
                )
            card_map[key] = (ind, path.name)
    return card_map


def collection_variants(
    rows: list[dict[str, str]],
    card_map: dict[tuple[str, str], tuple[str, str]],
) -> tuple[dict[VariantKey, int], list[tuple[str, str]]]:
    """Group inventory rows into variants, summing their copy counts.

    A variant is one (card individual, finish, condition) combination: two
    rows describing the same printing in the same finish and condition are
    the same physical stack split across acquisitions, so they become a
    single entry whose quantity is their total. Rows whose printing has no
    individual under sets/ are reported as missing instead.
    """
    variants: dict[VariantKey, int] = {}
    missing: list[tuple[str, str]] = []
    for r in rows:
        key = (r["Edition"].upper(), r["Collector Number"])
        if key not in card_map:
            missing.append(key)
            continue
        ind, fname = card_map[key]
        variant = (
            ind,
            fname,
            FINISH[r["Foil"].strip()],
            CONDITION[r["Condition"].strip()],
        )
        variants[variant] = variants.get(variant, 0) + int(r["Count"])
    return variants, missing


def cmd_collection() -> None:
    """Emit MagicCardCollection.ttl with one entry per collected variant."""
    with collection_csv_path().open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    card_map = load_card_map()
    variants, missing = collection_variants(rows, card_map)

    refs: list[str] = []
    total = 0
    by_file: dict[str, list[str]] = defaultdict(list)
    for (ind, fname, finish, condition), count in sorted(
        variants.items(),
        key=lambda item: (
            item[0][1],
            item[0][0],
            FINISH_ORDER.index(item[0][2]),
            CONDITION_ORDER.index(item[0][3]),
        ),
    ):
        # attribute-based IRI: derived only from the variant's own
        # attributes, so adding a lot in a new finish or condition never
        # renames an existing entry
        entry = f"{ind}Entry{finish}{condition}"
        total += count
        lines = [
            f":{entry} rdf:type owl:NamedIndividual ,",
            "                  :CollectionEntry ;",
            f"    :entryCard :{ind} ;",
            f'    :quantity "{count}"^^xsd:positiveInteger ;',
            f"    :hasFinish :{finish} ;",
            f"    :hasCondition :{condition} .",
        ]
        by_file[fname].append("\n".join(lines))
        refs.append(entry)

    parts = [COLLECTION_HEADER.format(ns_date=NS_DATE)]
    parts.append("#" * 80 + "\n#\n# Collection Individual\n#\n" + "#" * 80 + "\n")
    parts.append(
        ":MagicCardCollection rdf:type owl:NamedIndividual ,\n"
        "                  :Collection ;\n"
        '    rdfs:label "Magic Card Collection"@en ;\n'
        '    rdfs:comment """The physical card collection. Each collection\n'
        "entry describes the copies of one printing held in one finish and\n"
        'condition."""@en ;\n'
        "    :hasCollectionEntry\n  " + " ,\n  ".join(f":{e}" for e in refs) + " .\n",
    )
    parts.append(
        "#" * 80
        + "\n#\n# Collection Entries (grouped by set file)\n#\n"
        + "#" * 80
        + "\n",
    )
    for fname in sorted(by_file):
        parts.append(f"# --- sets/{fname} ---\n")
        parts.append("\n\n".join(by_file[fname]) + "\n")
    (ROOT / "MagicCardCollection.ttl").write_text("\n".join(parts))

    print(f"entries written: {len(refs)}  physical cards: {total}")  # noqa: T201 - generator report output
    if missing:
        print(f"NOT IN sets/*.ttl ({len(missing)}):", missing)  # noqa: T201 - generator report output


# ---------------------------------------------------------------- augment

_AUG_URL_RE = re.compile(
    r':scryfallUrl "https://scryfall\.com/card/'
    r'([^/"]+)/([^/"]+)/',
)
_AUG_LANG_RE = re.compile(r"^    :hasLanguage :\w+ ;$", re.MULTILINE)


@dataclass
class AugmentStats:
    """Counters for the augment-mana summary line."""

    blocks: int = 0
    facts: int = 0
    skipped: int = 0


def _augment_files() -> list[Path]:
    """Return the TTL files whose card blocks augment-mana post-processes."""
    files = sorted((ROOT / "sets").glob("*.ttl"))
    ext = ROOT / "MagicExternalCards.ttl"
    if ext.exists():
        files.append(ext)
    return files


def _augment_cache(files: list[Path]) -> dict[str, ScryCard]:
    """Load the Scryfall cache, fetching printings the files need on demand.

    Returns the cache keyed by lowercase 'set|number'.
    """
    cards_file = CACHE / "cards.json"
    cards: dict[str, ScryCard] = (
        json.loads(cards_file.read_text()) if cards_file.exists() else {}
    )
    by_key = {k.lower(): v for k, v in cards.items()}

    # collect printings referenced by blocks but missing from the cache
    todo: list[tuple[str, str]] = []
    for path in files:
        todo.extend(
            (s, n)
            for s, n in _AUG_URL_RE.findall(path.read_text())
            if f"{s}|{n}".lower() not in by_key
        )
    for i in range(0, len(todo), 75):
        batch = todo[i : i + 75]
        idents = [{"set": s, "collector_number": n} for s, n in batch]
        resp = http_json(f"{API}/cards/collection", {"identifiers": idents})
        for c in resp["data"]:
            key = f"{c['set']}|{c['collector_number']}"
            cards[key] = c
            by_key[key.lower()] = c
        if resp.get("not_found"):
            print("NOT FOUND:", resp["not_found"])  # noqa: T201 - generator report output
        time.sleep(0.15)
    if todo:
        cards_file.write_text(json.dumps(cards))
    return by_key


def _augment_block(
    block: str,
    fname: str,
    by_key: dict[str, ScryCard],
    stats: AugmentStats,
) -> str:
    """Insert missing mana-fact triples into one card block (idempotent)."""
    m = _AUG_URL_RE.search(block)
    if not (m and ":cardName" in block):
        return block
    stats.blocks += 1
    if ":producesMana" in block or ":entersTapped" in block or ":isFetchLand" in block:
        stats.skipped += 1
        return block
    card = by_key.get(f"{m.group(1)}|{m.group(2)}".lower())
    if card is None:
        print(f"no scryfall data: {fname} {m.group(1)}/{m.group(2)}")  # noqa: T201 - generator progress output
        return block
    lines = mana_fact_lines(card)
    if not lines:
        return block
    lm = _AUG_LANG_RE.search(block)
    if lm is None:
        return block
    stats.facts += len(lines)
    return block[: lm.end()] + "\n" + "\n".join(lines) + block[lm.end() :]


def cmd_augment_mana() -> None:
    """Backfill :producesMana / :entersTapped / :isFetchLand triples.

    Post-processes the existing sets/*.ttl (and, when a local copy is
    present, MagicExternalCards.ttl) card
    blocks in place (keyed by :scryfallUrl against the Scryfall cache,
    fetching uncached printings on demand). Idempotent: blocks that already
    carry mana-fact triples are left untouched. Avoids a full `generate`
    run, which could rename individuals.
    """
    files = _augment_files()
    by_key = _augment_cache(files)

    stats = AugmentStats()
    for path in files:
        text = path.read_text()
        blocks = re.split(r"\n(?=:\w+ rdf:type owl:NamedIndividual)", text)
        out = [_augment_block(block, path.name, by_key, stats) for block in blocks]
        if out != blocks:
            path.write_text("\n".join(out))
    print(  # noqa: T201 - generator report output
        f"blocks: {stats.blocks}  facts inserted: {stats.facts}  "
        f"already present: {stats.skipped}",
    )


if __name__ == "__main__":
    {
        "fetch": cmd_fetch,
        "generate": cmd_generate,
        "collection": cmd_collection,
        "augment-mana": cmd_augment_mana,
    }[sys.argv[1]]()
