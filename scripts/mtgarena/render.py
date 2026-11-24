"""The snapshot, the files derived from it, and the change report.

``arena-collection.json``          one entry per owned Arena printing; the source
                                   of the other two and the generator's input
``arena-collection.moxfield.csv``  Moxfield's collection CSV -- the same 13
                                   columns as ``collection.csv``, so Moxfield,
                                   Archidekt and ManaBox import it
``arena-collection.txt``           Arena's own import format, ``N Name (SET)
                                   num``, with Arena's set codes

Everything is sorted by card name and carries no timestamps and no account
data, so an unchanged collection produces no diff and each diff lists the
cards gained.  Basic lands are kept in the JSON but left out of the CSV and
TXT by default: Arena hands them out without limit.  Digital-only printings
(Alchemy, Arena-only sets) stay in the CSV unless ``exclude_digital`` is set,
for importers that do not know those sets.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from mtgarena.catalog import Printing

#: a collection snapshot: ``source``, ``totals`` and the ``cards`` list
Snapshot = dict[str, Any]
#: one entry of a snapshot's ``cards`` list
CardRecord = dict[str, Any]

SNAPSHOT_NAME = "arena-collection.json"
CSV_NAME = "arena-collection.moxfield.csv"
TXT_NAME = "arena-collection.txt"

MOXFIELD_COLUMNS = [
    "Count",
    "Tradelist Count",
    "Name",
    "Edition",
    "Condition",
    "Language",
    "Foil",
    "Tags",
    "Last Modified",
    "Collector Number",
    "Alter",
    "Proxy",
    "Purchase Price",
]
MOXFIELD_TAG = "mtga"
MAX_LINES_PER_KIND = 60

_CHUNK = re.compile(r"(\d+)")


# ------------------------------------------------------------------ the snapshot


def natural_key(number: str | None) -> tuple[tuple[int, int | str], ...]:
    """Order collector numbers by their digit runs: ``'12a' < '100'``."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in _CHUNK.split(number or "")
        if part
    )


def sort_key(
    card: CardRecord,
) -> tuple[bool, str, str, tuple[tuple[int, int | str], ...], int]:
    """Order snapshot entries: by name (nameless last), set, number, Arena id."""
    name = card.get("name")
    return (
        name is None,
        (name or "").casefold(),
        card.get("set") or "",
        natural_key(card.get("collector_number")),
        card["arena_id"],
    )


def card_record(printing: Printing) -> CardRecord:
    """Return the snapshot entry of one resolved printing."""
    return {
        "arena_id": printing.arena_id,
        "quantity": printing.quantity,
        "name": printing.name,
        "set": printing.set,
        "collector_number": printing.collector_number,
        "rarity": printing.rarity,
        "scryfall_id": printing.scryfall_id,
        "source": printing.source,
        "arena_name": printing.arena_name,
        "arena_set": printing.arena_set,
        "arena_collector_number": printing.arena_collector_number,
        "basic": printing.basic,
        "digital": printing.digital,
        "rebalanced": printing.rebalanced,
    }


def build_snapshot(printings: list[Printing]) -> Snapshot:
    """Return the snapshot of the resolved printings, with its totals."""
    cards = sorted((card_record(p) for p in printings), key=sort_key)
    names = {c["name"] or f"#{c['arena_id']}" for c in cards}

    def count(source: str) -> int:
        return sum(1 for c in cards if c["source"] == source)

    totals = {
        "cards": sum(c["quantity"] for c in cards),
        "printings": len(cards),
        "names": len(names),
        "basic_land_cards": sum(c["quantity"] for c in cards if c["basic"]),
        "scryfall": sum(1 for c in cards if c["scryfall_id"]),
        "scryfall_by_set_number": count("scryfall-set"),
        "scryfall_by_name": count("scryfall-name"),
        "scryfall_by_pick": count("scryfall-pick"),
        "arena_only": count("arena"),
        "unknown": count("unknown"),
        "digital_printings": sum(1 for c in cards if c["digital"]),
    }
    return {"source": "untapped.gg", "totals": totals, "cards": cards}


def dump_snapshot(snapshot: Snapshot) -> str:
    """Serialise a snapshot as written to ``arena-collection.json``."""
    return json.dumps(snapshot, indent=1, ensure_ascii=False) + "\n"


def load_snapshot(path: Path) -> Snapshot | None:
    """Return the snapshot in *path*; None when the file does not exist."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        msg = f"{path}: not valid JSON ({exc}); fix or delete it"
        raise SystemExit(msg) from None
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        msg = f"{path}: not a collection snapshot (no 'cards' list)"
        raise SystemExit(msg)
    return data


def summary_lines(snapshot: Snapshot) -> list[str]:
    """Summarise a snapshot's totals in a few lines."""
    t = snapshot["totals"]
    fallbacks = [
        f"{n} of them {how}"
        for n, how in (
            (t.get("scryfall_by_set_number"), "by set and collector number"),
            (t.get("scryfall_by_name"), "by set and name"),
            (t.get("scryfall_by_pick"), "picked by hand"),
        )
        if n
    ]
    by_set = f" ({', '.join(fallbacks)})" if fallbacks else ""
    lines = [
        f"{t['cards']} cards, {t['printings']} printings, {t['names']} distinct names",
        (
            f"{t['scryfall']} printings matched on Scryfall{by_set}, "
            f"{t['arena_only']} known to Arena only, {t['unknown']} unknown"
        ),
    ]
    if t["basic_land_cards"]:
        lines.append(
            f"{t['basic_land_cards']} basic lands (kept in the JSON, left out of the "
            "CSV/TXT unless --include-basics)",
        )
    if t.get("digital_printings"):
        lines.append(
            f"{t['digital_printings']} digital-only printings (Alchemy, Arena-only "
            "sets; left out of the CSV with --exclude-digital)",
        )
    return lines


def label(card: CardRecord) -> str:
    """Write ``Name (SET) num`` as Arena shows it, for messages."""
    if card.get("arena_name"):
        return (
            f"{card['arena_name']} ({card['arena_set']}) "
            f"{card['arena_collector_number']}"
        )
    if card.get("name"):
        code = (card.get("set") or "?").upper()
        return f"{card['name']} ({code}) {card.get('collector_number') or '?'}"
    return f"grpid {card['arena_id']}"


# ----------------------------------------------------------------- derived files


def _exported(snapshot: Snapshot, *, include_basics: bool) -> list[CardRecord]:
    """Return the entries the CSV and TXT list: no basic lands unless asked."""
    return [c for c in snapshot["cards"] if include_basics or not c["basic"]]


def moxfield_rows(
    snapshot: Snapshot,
    *,
    include_basics: bool = False,
    exclude_digital: bool = False,
) -> list[list[str]]:
    """Return the Moxfield CSV rows: Scryfall-matched printings only."""
    rows = []
    for card in _exported(snapshot, include_basics=include_basics):
        if not card["scryfall_id"]:
            continue  # Moxfield knows printings by Scryfall's set code and number
        if exclude_digital and card["digital"]:
            continue  # for importers that know no Alchemy / Arena-only sets
        rows.append(
            [
                str(card["quantity"]),
                "0",
                card["name"],
                card["set"],
                "Near Mint",
                "English",
                "",
                MOXFIELD_TAG,
                "",
                card["collector_number"],
                "False",
                "False",
                "",
            ],
        )
    return rows


def dump_moxfield_csv(
    snapshot: Snapshot,
    *,
    include_basics: bool = False,
    exclude_digital: bool = False,
) -> str:
    """Write the Moxfield collection CSV of a snapshot."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(MOXFIELD_COLUMNS)
    writer.writerows(
        moxfield_rows(
            snapshot,
            include_basics=include_basics,
            exclude_digital=exclude_digital,
        ),
    )
    return buffer.getvalue()


def arena_lines(snapshot: Snapshot, *, include_basics: bool = False) -> list[str]:
    """Return the snapshot's lines of Arena's import format ``N Name (SET) num``."""
    return [
        f"{card['quantity']} {card['arena_name']} ({card['arena_set']}) "
        f"{card['arena_collector_number']}"
        for card in _exported(snapshot, include_basics=include_basics)
        # a grpid the Arena dump does not know cannot be written as an Arena line
        if card.get("arena_name")
    ]


def dump_arena_txt(snapshot: Snapshot, *, include_basics: bool = False) -> str:
    """Write the Arena-format list of a snapshot."""
    lines = arena_lines(snapshot, include_basics=include_basics)
    return "\n".join(lines) + ("\n" if lines else "")


# --------------------------------------------------------- changes between runs


def _by_id(snapshot: Snapshot | None) -> dict[int, CardRecord]:
    """Return the snapshot's entries by Arena card id."""
    return {c["arena_id"]: c for c in snapshot["cards"]} if snapshot else {}


def _with_previous(cards: list[tuple[CardRecord, int]]) -> list[tuple[CardRecord, int]]:
    """Sort (entry, previous quantity) pairs like the snapshot's entries."""
    return sorted(cards, key=lambda pair: sort_key(pair[0]))


def changes(new: Snapshot, old: Snapshot | None) -> list[str]:
    """Report what changed from *old* to *new*: one line per printing, a total."""
    if old is None:
        totals = new["totals"]
        return [
            f"first snapshot: {totals['printings']} printings, {totals['cards']} cards",
        ]
    before, after = _by_id(old), _by_id(new)
    added = [after[i] for i in after if i not in before]
    removed = [before[i] for i in before if i not in after]
    up = [
        (after[i], before[i]["quantity"])
        for i in after
        if i in before and after[i]["quantity"] > before[i]["quantity"]
    ]
    down = [
        (after[i], before[i]["quantity"])
        for i in after
        if i in before and after[i]["quantity"] < before[i]["quantity"]
    ]
    if not (added or removed or up or down):
        return ["no changes"]
    lines: list[str] = []
    lines += _capped(
        [f"+{c['quantity']} {label(c)}" for c in sorted(added, key=sort_key)]
    )
    lines += _capped(
        [
            f"+{c['quantity'] - q} {label(c)}  ({q} -> {c['quantity']})"
            for c, q in _with_previous(up)
        ],
    )
    lines += _capped(
        [
            f"-{q - c['quantity']} {label(c)}  ({q} -> {c['quantity']})"
            for c, q in _with_previous(down)
        ],
    )
    lines += _capped(
        [f"-{c['quantity']} {label(c)}  (gone)" for c in sorted(removed, key=sort_key)],
    )
    gained = sum(c["quantity"] for c in added) + sum(c["quantity"] - q for c, q in up)
    lost = sum(c["quantity"] for c in removed) + sum(q - c["quantity"] for c, q in down)
    lines.append(
        f"{len(added)} printings added, {len(up)} increased (+{gained} cards); "
        f"{len(down)} decreased, {len(removed)} gone (-{lost} cards); "
        f"total {old['totals']['cards']} -> {new['totals']['cards']} cards",
    )
    return lines


def _capped(lines: list[str]) -> list[str]:
    """Keep MAX_LINES_PER_KIND lines at most, and say how many more there were."""
    if len(lines) <= MAX_LINES_PER_KIND:
        return lines
    rest = len(lines) - MAX_LINES_PER_KIND
    return [*lines[:MAX_LINES_PER_KIND], f"... and {rest} more"]


def shrink(new: Snapshot, old: Snapshot | None) -> tuple[int, int] | None:
    """Return ``(old_total, new_total)`` when the collection got smaller.

    Arena collections do not shrink, so a smaller one means a stale or
    partial sync.
    """
    if old is None:
        return None
    before, after = old["totals"]["cards"], new["totals"]["cards"]
    return (before, after) if after < before else None


# ------------------------------------------------------------------------- files


def write_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* through a temporary file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_outputs(
    out_dir: Path,
    snapshot: Snapshot,
    *,
    include_basics: bool = False,
    exclude_digital: bool = False,
) -> list[Path]:
    """Write the three files; return the paths whose content changed.

    ``exclude_digital`` affects the Moxfield CSV only: the TXT is Arena's own
    format, where digital printings are at home.
    """
    written = []
    csv_text = dump_moxfield_csv(
        snapshot,
        include_basics=include_basics,
        exclude_digital=exclude_digital,
    )
    for name, text in (
        (SNAPSHOT_NAME, dump_snapshot(snapshot)),
        (CSV_NAME, csv_text),
        (TXT_NAME, dump_arena_txt(snapshot, include_basics=include_basics)),
    ):
        path = out_dir / name
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        write_atomic(path, text)
        written.append(path)
    return written
