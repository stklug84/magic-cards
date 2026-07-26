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
                per collection.csv row (quantity, finish, condition, purchase
                price), resolving card individuals from the existing sets/*.ttl
                files.

Usage:
  python3 scripts/generate_individuals.py fetch
  python3 scripts/generate_individuals.py generate
  python3 scripts/generate_individuals.py collection
"""

import csv
import json
import re
import sys
import time
import unicodedata
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path("/tmp/scryfall_cache")
CACHE.mkdir(exist_ok=True)
API = "https://api.scryfall.com"
UA = {
    "User-Agent": "stklug84-inventory-ttl-generator/1.0",
    "Accept": "application/json",
}
TODAY = "2026-07-19"
NS_DATE = "2026-07-19"

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

# ---------------------------------------------------------------- helpers


def http_json(url, data=None):
    req = urllib.request.Request(
        url,
        headers=UA,
        data=json.dumps(data).encode() if data else None,
    )
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def pascal(name: str) -> str:
    """'Adrix and Nev, Twincasters' -> 'AdrixAndNevTwincasters'."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", "").replace("\u2019", "")
    tokens = re.split(r"[^A-Za-z0-9]+", s)
    return "".join(t[0].upper() + t[1:] if t else "" for t in tokens)


def esc_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def esc_long(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if s.endswith('"'):
        s = s[:-1] + '\\"'
    return s


# ---------------------------------------------------------------- fetch


def load_rows():
    rows = list(csv.DictReader(open(ROOT / "collection.csv")))
    printings = {}  # (set, num) -> row info
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


def cmd_fetch():
    printings = load_rows()
    keys = sorted(printings)
    print(f"{len(printings)} unique printings")

    cards_file = CACHE / "cards.json"
    cards = json.loads(cards_file.read_text()) if cards_file.exists() else {}
    todo = [k for k in keys if f"{k[0]}|{k[1]}" not in cards]
    not_found = []
    for i in range(0, len(todo), 75):
        batch = todo[i : i + 75]
        idents = [{"set": s, "collector_number": n} for s, n in batch]
        resp = http_json(f"{API}/cards/collection", {"identifiers": idents})
        not_found.extend(resp.get("not_found", []))
        for c in resp["data"]:
            cards[f"{c['set']}|{c['collector_number']}"] = c
        print(
            f"  batch {i // 75 + 1}: {len(resp['data'])} found, "
            f"{len(resp.get('not_found', []))} missing",
        )
        cards_file.write_text(json.dumps(cards))
        time.sleep(0.15)
    if not_found:
        print("NOT FOUND:", json.dumps(not_found, indent=2))

    # localized printings (non-English rows)
    loc_file = CACHE / "localized.json"
    localized = json.loads(loc_file.read_text()) if loc_file.exists() else {}
    for k in keys:
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
            print(f"  localized fetch failed for {lid}: {e}")
        time.sleep(0.15)
    loc_file.write_text(json.dumps(localized))

    # set metadata
    sets_file = CACHE / "sets.json"
    sets_meta = json.loads(sets_file.read_text()) if sets_file.exists() else {}
    for code in sorted({k[0] for k in keys}):
        if code not in sets_meta:
            sets_meta[code] = http_json(f"{API}/sets/{code}")
            time.sleep(0.1)
    sets_file.write_text(json.dumps(sets_meta))

    # rulings: bulk file filtered to our oracle ids
    rulings_file = CACHE / "rulings.json"
    if not rulings_file.exists():
        oracle_ids = set()
        for c in cards.values():
            oid = c.get("oracle_id") or (c.get("card_faces") or [{}])[0].get(
                "oracle_id",
            )
            if oid:
                oracle_ids.add(oid)
        bulk = http_json(f"{API}/bulk-data")
        uri = next(b["download_uri"] for b in bulk["data"] if b["type"] == "rulings")
        print("downloading bulk rulings ...")
        req = urllib.request.Request(uri, headers=UA)
        with urllib.request.urlopen(req, timeout=300) as r:
            allr = json.loads(r.read().decode())
        mine = defaultdict(list)
        for ru in allr:
            if ru["oracle_id"] in oracle_ids and ru["source"] == "wotc":
                mine[ru["oracle_id"]].append(
                    {"date": ru["published_at"], "text": ru["comment"]},
                )
        rulings_file.write_text(json.dumps(mine))
        print(f"kept rulings for {len(mine)} cards")
    print("fetch complete")


# ---------------------------------------------------------------- generate


def norm_label(s: str) -> str:
    return s.replace("\u2019", "'").lower()


def load_vocab():
    v = json.load(open("/tmp/onto_vocab.json"))
    labels = v["_labels"]
    sub_by_label = {}
    for ind in v["SubType"]:
        sub_by_label[norm_label(labels.get(ind, ind))] = ind
        sub_by_label[norm_label(ind)] = ind
    kw = {}
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


def tokenize_subtypes(right, types, sub_by_label):
    """Greedy longest-match against known (multi-word) subtype labels."""
    right = right.strip()
    if not right:
        return []
    if "Plane" in types:  # a plane's whole subtype line is one plane name
        return [right]
    words = right.split()
    subs, i = [], 0
    while i < len(words):
        for n in range(min(4, len(words) - i), 0, -1):
            cand = " ".join(words[i : i + n])
            if norm_label(cand) in sub_by_label or n == 1:
                subs.append(cand)
                i += n
                break
    return subs


def parse_type_line(tl, vocab, sub_by_label, layout=""):
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
    supers, types, subs = [], [], []
    for idx, part in enumerate(tl.split(" // ")):
        left, _, right = part.partition("\u2014")
        secondary_adventure_face = layout == "adventure" and idx > 0
        if not secondary_adventure_face:
            words = left.split()
            for w in words:
                w = "Kindred" if w == "Tribal" else w
                if w in vocab["SuperType"] and w not in supers:
                    supers.append(w)
                elif w in vocab["CardType"] and w not in types:
                    types.append(w)
        for sub in tokenize_subtypes(right, types, sub_by_label):
            if sub not in subs:
                subs.append(sub)
    return supers, types, subs


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


def mana_fact_lines(card) -> list:
    """Turtle lines for :producesMana / :entersTapped / :isFetchLand.

    :producesMana comes from Scryfall's structured produced_mana field;
    :entersTapped is asserted only for unconditional taplands ("... unless"
    forms are omitted); :isFetchLand for sacrifice-to-search lands that do
    not produce mana themselves.
    """
    lines = []
    produced = [s for s in card.get("produced_mana") or [] if s in PRODUCED_IND]
    for sym in sorted(produced, key=PRODUCED_ORDER.index):
        lines.append(f"    :producesMana :{PRODUCED_IND[sym]} ;")
    faces = card.get("card_faces") or [card]
    oracle = "\n".join(f.get("oracle_text", "") for f in faces)
    type_line = card.get("type_line") or faces[0].get("type_line", "")
    is_land = "Land" in type_line
    if is_land and _TAPPED_RE.search(oracle):
        lines.append('    :entersTapped "true"^^xsd:boolean ;')
    if is_land and not produced and _FETCH_RE.search(oracle):
        lines.append('    :isFetchLand "true"^^xsd:boolean ;')
    return lines


def card_block(ind, card, info, vocab, sub_by_label, kw_map, rulings, notes):
    out = []
    add = out.append
    faces = card.get("card_faces") or [card]
    front = faces[0]

    add(f":{ind} rdf:type owl:NamedIndividual ,")
    add("                  :Card ;")
    add(f'    :cardName "{esc_str(card["name"])}" ;')
    mc = front.get("mana_cost") or card.get("mana_cost") or ""
    if mc:
        add(f'    :manaCost "{esc_str(mc)}" ;')
    cmc = card.get("cmc", front.get("cmc", 0)) or 0
    add(f'    :manaValue "{int(cmc)}"^^xsd:nonNegativeInteger ;')

    supers, types, subs = parse_type_line(
        card.get("type_line") or front.get("type_line", ""),
        vocab,
        sub_by_label,
        layout=card.get("layout", ""),
    )
    for s in supers:
        add(f"    :hasSuperType :{s} ;")
    for t in types:
        add(f"    :hasCardType :{t} ;")
    for sub in subs:
        target = sub_by_label.get(norm_label(sub)) or sub_by_label.get(
            norm_label(sub.replace("-", "")),
        )
        if target is None:
            target = pascal(sub)
            cls = TYPE_CLASS.get(types[0] if types else "Creature", "CreatureTypes")
            notes["subtypes"].setdefault(target, (sub, cls))
        add(f"    :hasSubType :{target} ;")

    add(f"    :hasRarity :{RARITY[card['rarity']]} ;")

    colors = card.get("colors")
    if colors is None:
        colors = sorted({c for f in faces for c in f.get("colors", [])})
    for c in ["W", "U", "B", "R", "G"]:
        if c in colors:
            add(f"    :hasColor :{COLOR[c]} ;")
    for c in ["W", "U", "B", "R", "G"]:
        if c in card.get("color_identity", []):
            add(f"    :hasColorIdentity :{COLOR[c]} ;")

    add(f"    :isInSet :{info['set_ind']} ;")
    num = card["collector_number"]
    if num.isdigit():
        add(f'    :cardNumber "{num}"^^xsd:integer ;')
    else:
        add(f'    :cardNumberString "{esc_str(num)}" ;')
    if card.get("artist"):
        add(f'    :artist "{esc_str(card["artist"])}" ;')
    add(f"    :hasLanguage :{LANG[info['language']]} ;")
    for line in mana_fact_lines(card):
        add(line)

    if front.get("power") is not None:
        add(f'    :power "{esc_str(front["power"])}" ;')
        if re.fullmatch(r"-?\d+", front["power"]):
            add(f'    :powerValue "{front["power"]}"^^xsd:integer ;')
    if front.get("toughness") is not None:
        add(f'    :toughness "{esc_str(front["toughness"])}" ;')
        if re.fullmatch(r"-?\d+", front["toughness"]):
            add(f'    :toughnessValue "{front["toughness"]}"^^xsd:integer ;')
    if front.get("loyalty") and re.fullmatch(r"\d+", front["loyalty"]):
        add(f'    :loyalty "{front["loyalty"]}"^^xsd:integer ;')
    if front.get("defense") and re.fullmatch(r"\d+", front["defense"]):
        add(f'    :defenseValue "{front["defense"]}"^^xsd:nonNegativeInteger ;')

    for kw in card.get("keywords", []):
        hit = kw_map.get(kw) or kw_map.get(pascal(kw))
        if hit:
            add(f"    :{hit[0]} :{hit[1]} ;")
        else:
            add(f'    # Note: keyword "{kw}" not defined in MagicCardsOntology')
            notes["keywords"].add(kw)

    loc = info.get("localized")
    lfaces = (loc.get("card_faces") or [loc]) if loc else faces
    printed = "\n//\n".join(
        f.get("printed_text") or f.get("oracle_text", "") for f in lfaces
    )
    oracle = "\n//\n".join(f.get("oracle_text", "") for f in faces)
    if printed:
        add(f'    :printedText """{esc_long(printed)}"""@{"de" if loc else "en"} ;')
    if oracle:
        add(f'    :oracleText """{esc_long(oracle)}"""@en ;')
    flavor_src = lfaces if loc else faces
    flavor = "\n//\n".join(f["flavor_text"] for f in flavor_src if f.get("flavor_text"))
    if flavor:
        add(f'    :flavorText """{esc_long(flavor)}"""@{"de" if loc else "en"} ;')

    mvids = card.get("multiverse_ids") or []
    if mvids:
        add(
            f'    :gathererUrl "https://gatherer.wizards.com/Pages/Card/Details.aspx'
            f'?multiverseid={mvids[0]}"^^xsd:anyURI ;',
        )
    surl = card["scryfall_uri"].split("?")[0]
    add(f'    :scryfallUrl "{surl}"^^xsd:anyURI ;')

    leg = card["legalities"]
    leg_lines = [
        f"      [ rdf:type :LegalityMapping ; :inFormat :{f_ind} ; "
        f":hasLegalityStatus :{LEGALITY[leg[f_key]]} ]"
        for f_key, f_ind in FORMATS
    ]
    rus = rulings.get(
        card.get("oracle_id") or (card.get("card_faces") or [{}])[0].get("oracle_id"),
        [],
    )
    if rus:
        add("    :hasLegality")
        add(" ,\n".join(leg_lines) + " ;")
        add("    :hasRuling")
        ru_lines = []
        for ru in rus:
            ru_lines.append(
                "      [ rdf:type :Ruling ;\n"
                f'        :rulingDate "{ru["date"]}"^^xsd:date ;\n'
                f'        :rulingText """{esc_long(ru["text"])}"""@en ]',
            )
        add(" ,\n".join(ru_lines) + " .")
    else:
        add("    :hasLegality")
        add(" ,\n".join(leg_lines) + " .")
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
# This file is generated by scripts/generate_individuals.py from
# collection.csv. Do not edit manually.
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


def cmd_generate():
    vocab, sub_by_label, kw_map = load_vocab()
    setcodes = vocab["_setcodes"]  # CODE -> individual
    printings = load_rows()
    cards = json.loads((CACHE / "cards.json").read_text())
    localized = json.loads((CACHE / "localized.json").read_text())
    sets_meta = json.loads((CACHE / "sets.json").read_text())
    rulings = json.loads((CACHE / "rulings.json").read_text())
    existing = json.load(open("/tmp/existing_cards.json"))

    # skip-list: (name, setcode, number) of individuals already in the master file
    inv_sets = {ind: code for code, ind in setcodes.items()}
    existing_keys = {
        (e["name"], (inv_sets.get(e["set"]) or "").lower(), e["num"]) for e in existing
    }
    existing_names = {e["ind"] for e in existing}

    # set individual names, creating entries for sets missing from the ontology
    new_sets = {}
    set_ind = {}
    for code in sorted({k[0] for k in printings}):
        up = code.upper()
        if up in setcodes:
            set_ind[code] = setcodes[up]
        else:
            ind = pascal(sets_meta[code]["name"])
            set_ind[code] = ind
            new_sets[code] = ind

    # printings-per-name to decide suffixing
    per_name = defaultdict(list)
    for k, p in printings.items():
        per_name[p["name"]].append(k)

    used, blocks_by_set = {}, defaultdict(list)
    notes = {"subtypes": {}, "keywords": set(), "skipped": [], "missing": []}

    for key in sorted(
        printings,
        key=lambda k: (
            set_ind[k[0]],
            (0, int(k[1])) if k[1].isdigit() else (1, 0),
            k[1],
        ),
    ):
        p = printings[key]
        card = cards.get(f"{key[0]}|{key[1]}")
        if card is None:
            notes["missing"].append(key)
            continue
        if (p["name"], key[0], key[1]) in existing_keys:
            notes["skipped"].append((p["name"], key))
            continue
        base = pascal(p["name"].split(" // ")[0])
        multi = len(per_name[p["name"]]) > 1
        if p["name"] in BASICS or multi or base in existing_names:
            ind = f"{base}{set_ind[key[0]]}{re.sub(r'[^A-Za-z0-9]', '', key[1])}"
        else:
            ind = base
        if ind in used:
            msg = f"individual name collision: {ind} ({used[ind]} vs {key})"
            raise SystemExit(msg)
        used[ind] = key
        info = {
            "set_ind": set_ind[key[0]],
            "language": p["language"],
            "localized": localized.get(f"{key[0]}|{key[1]}"),
        }
        blocks_by_set[key[0]].append(
            card_block(ind, card, info, vocab, sub_by_label, kw_map, rulings, notes),
        )

    # write per-set files
    (ROOT / "sets").mkdir(exist_ok=True)
    imports = []
    for code in sorted(blocks_by_set, key=lambda c: set_ind[c]):
        sname = sets_meta[code]["name"]
        urn = f"urn:stklug84:MagicCardIndividuals:{code.upper()}:{NS_DATE}#"
        parts = [HEADER.format(set_name=sname, set_code=code.upper(), urn=urn)]
        if code in new_sets:
            sm = sets_meta[code]
            parts.append(
                "#" * 80 + "\n#\n# Set Individual (not defined in "
                "MagicCardsOntology)\n#\n" + "#" * 80 + "\n\n"
                f":{new_sets[code]} rdf:type owl:NamedIndividual ,\n"
                "                  :Set ;\n"
                f'    :setName "{esc_str(sm["name"])}" ;\n'
                f'    :setCode "{code.upper()}" ;\n'
                f'    :cardCount "{sm["card_count"]}"^^xsd:integer ;\n'
                f'    :releaseDate "{sm["released_at"]}"^^xsd:date .\n',
            )
        # missing subtypes used by cards of this set
        parts.append("#" * 80 + "\n#\n# Card Individuals\n#\n" + "#" * 80 + "\n")
        parts.append("\n\n".join(blocks_by_set[code]))
        out = ROOT / "sets" / f"{set_ind[code]}.ttl"
        out.write_text("\n".join(parts) + "\n")
        imports.append((urn, out.name, len(blocks_by_set[code])))

    # supplemental subtype individuals file
    if notes["subtypes"]:
        urn = f"urn:stklug84:MagicCardIndividuals:SubTypeSupplement:{NS_DATE}#"
        lines = [
            HEADER.format(
                set_name="SubType Supplement", set_code="SUPPLEMENT", urn=urn
            ),
        ]
        lines.append(
            "#" * 80 + "\n#\n# SubType individuals referenced by the "
            "collection but not defined in\n# MagicCardsOntology\n#\n"
            + "#" * 80
            + "\n",
        )
        for ind, (label, cls) in sorted(notes["subtypes"].items()):
            lines.append(
                f":{ind} rdf:type owl:NamedIndividual ,\n"
                f"                  :{cls} ;\n"
                f'    rdfs:label "{esc_str(label)}" .\n',
            )
        out = ROOT / "sets" / "SubTypeSupplement.ttl"
        out.write_text("\n".join(lines))
        imports.insert(0, (urn, out.name, len(notes["subtypes"])))

    print(f"cards written: {len(used)}  files: {len(blocks_by_set)}")
    print(f"skipped (already in master): {len(notes['skipped'])}")
    if notes["missing"]:
        print("MISSING from scryfall:", notes["missing"])
    if notes["subtypes"]:
        print("new subtypes:", sorted(notes["subtypes"]))
    if notes["keywords"]:
        print("unknown keywords (comment notes):", sorted(notes["keywords"]))
    json.dump(
        [{"urn": u, "file": f, "cards": n} for u, f, n in imports],
        open("/tmp/imports.json", "w"),
        indent=2,
    )


# ---------------------------------------------------------------- collection

FINISH = {"": "Nonfoil", "foil": "Foil", "etched": "EtchedFoil"}
CONDITION = {
    "Near Mint": "NearMint",
    "Lightly Played": "LightlyPlayed",
    "Moderately Played": "ModeratelyPlayed",
    "Heavily Played": "HeavilyPlayed",
    "Damaged": "Damaged",
}

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
# This file is generated by scripts/generate_individuals.py from
# collection.csv. Do not edit manually.
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
    owl:imports <urn:stklug84:MagicCardsOntology:2026-02-27#> ;
    owl:imports <urn:stklug84:MagicCardIndividuals:{ns_date}#> ;
    rdfs:label "Magic Card Collection Inventory"@en ;
    rdfs:comment \"\"\"An instance graph describing the physical inventory of a
personal Magic: The Gathering card collection. Defines the Collection
individual and one reified CollectionEntry per acquisition lot from
collection.csv, carrying the number of copies, finish, condition and
(where recorded) purchase price of a collected printing. Card individuals
are defined in the per-set instance graphs aggregated by
MagicCardIndividuals.\"\"\"@en .
"""


def load_card_map():
    """Scan sets/*.ttl -> {(SETCODE, collector_number): (individual, file)}."""
    card_map = {}
    for path in sorted((ROOT / "sets").glob("*.ttl")):
        text = path.read_text()
        code = re.search(
            r"@base\s+<urn:stklug84:MagicCardIndividuals:"
            r"([^:>]+):[^>]*>",
            text,
        ).group(1)
        blocks = re.split(r"\n(?=:\w+ rdf:type owl:NamedIndividual)", text)
        for b in blocks[1:]:
            ind = re.match(r":(\w+)", b).group(1)
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


def cmd_collection():
    rows = list(csv.DictReader(open(ROOT / "collection.csv")))
    card_map = load_card_map()

    _entries, refs, missing = [], [], []
    seq = defaultdict(int)
    total = 0
    by_file = defaultdict(list)
    for r in rows:
        key = (r["Edition"].upper(), r["Collector Number"])
        if key not in card_map:
            missing.append(key)
            continue
        ind, fname = card_map[key]
        seq[ind] += 1
        entry = f"{ind}Entry{seq[ind]}"
        count = int(r["Count"])
        total += count
        lines = [
            f":{entry} rdf:type owl:NamedIndividual ,",
            "                  :CollectionEntry ;",
            f"    :entryCard :{ind} ;",
            f'    :quantity "{count}"^^xsd:positiveInteger ;',
            f"    :hasFinish :{FINISH[r['Foil'].strip()]} ;",
            f"    :hasCondition :{CONDITION[r['Condition'].strip()]} ;",
        ]
        price = r["Purchase Price"].strip()
        if price:
            lines.append(f'    :purchasePrice "{price}"^^xsd:decimal ;')
        lines[-1] = lines[-1][:-1].rstrip() + " ."
        by_file[fname].append("\n".join(lines))
        refs.append(entry)

    parts = [COLLECTION_HEADER.format(ns_date=NS_DATE)]
    parts.append("#" * 80 + "\n#\n# Collection Individual\n#\n" + "#" * 80 + "\n")
    parts.append(
        ":MagicCardCollection rdf:type owl:NamedIndividual ,\n"
        "                  :Collection ;\n"
        '    rdfs:label "Magic Card Collection"@en ;\n'
        '    rdfs:comment """The physical card collection inventoried\n'
        "in collection.csv. Each collection entry describes one acquisition\n"
        'lot of a specific printing."""@en ;\n'
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

    print(f"entries written: {len(refs)}  physical cards: {total}")
    if missing:
        print(f"NOT IN sets/*.ttl ({len(missing)}):", missing)


# ---------------------------------------------------------------- augment


def cmd_augment_mana():
    """Backfill :producesMana / :entersTapped / :isFetchLand triples.

    Post-processes the existing sets/*.ttl and MagicExternalCards.ttl card
    blocks in place (keyed by :scryfallUrl against the Scryfall cache,
    fetching uncached printings on demand). Idempotent: blocks that already
    carry mana-fact triples are left untouched. Avoids a full `generate`
    run, which could rename individuals.
    """
    url_re = re.compile(
        r':scryfallUrl "https://scryfall\.com/card/'
        r'([^/"]+)/([^/"]+)/',
    )
    lang_re = re.compile(r"^    :hasLanguage :\w+ ;$", re.MULTILINE)
    files = sorted((ROOT / "sets").glob("*.ttl"))
    ext = ROOT / "MagicExternalCards.ttl"
    if ext.exists():
        files.append(ext)

    cards_file = CACHE / "cards.json"
    cards = json.loads(cards_file.read_text()) if cards_file.exists() else {}
    by_key = {k.lower(): v for k, v in cards.items()}

    # collect printings referenced by blocks but missing from the cache
    todo = []
    for path in files:
        for s, n in url_re.findall(path.read_text()):
            if f"{s}|{n}".lower() not in by_key:
                todo.append((s, n))
    for i in range(0, len(todo), 75):
        batch = todo[i : i + 75]
        idents = [{"set": s, "collector_number": n} for s, n in batch]
        resp = http_json(f"{API}/cards/collection", {"identifiers": idents})
        for c in resp["data"]:
            key = f"{c['set']}|{c['collector_number']}"
            cards[key] = c
            by_key[key.lower()] = c
        if resp.get("not_found"):
            print("NOT FOUND:", resp["not_found"])
        time.sleep(0.15)
    if todo:
        cards_file.write_text(json.dumps(cards))

    n_blocks = n_facts = n_skipped = 0
    for path in files:
        text = path.read_text()
        blocks = re.split(r"\n(?=:\w+ rdf:type owl:NamedIndividual)", text)
        out = []
        changed = False
        for block in blocks:
            m = url_re.search(block)
            if m and ":cardName" in block:
                n_blocks += 1
                if (
                    ":producesMana" in block
                    or ":entersTapped" in block
                    or ":isFetchLand" in block
                ):
                    n_skipped += 1
                elif (
                    card := by_key.get(f"{m.group(1)}|{m.group(2)}".lower())
                ) is not None:
                    lines = mana_fact_lines(card)
                    if lines:
                        lm = lang_re.search(block)
                        if lm:
                            block = (
                                block[: lm.end()]
                                + "\n"
                                + "\n".join(lines)
                                + block[lm.end() :]
                            )
                            n_facts += len(lines)
                            changed = True
                else:
                    print(f"no scryfall data: {path.name} {m.group(1)}/{m.group(2)}")
            out.append(block)
        if changed:
            path.write_text("\n".join(out))
    print(
        f"blocks: {n_blocks}  facts inserted: {n_facts}  already present: {n_skipped}",
    )


if __name__ == "__main__":
    {
        "fetch": cmd_fetch,
        "generate": cmd_generate,
        "collection": cmd_collection,
        "augment-mana": cmd_augment_mana,
    }[sys.argv[1]]()
