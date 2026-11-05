"""CPython reference vectors for the Rust ``pycompat`` crate (parity L1).

Writes one JSON file per topic into ``rust/crates/pycompat/tests/vectors``
(or the directory given as the first argument). The Rust tests read them,
so the files are committed; ``cargo xtask vectors`` regenerates them and
``cargo xtask vectors --check`` fails when the committed files are stale.

Every value here is produced by the CPython behaviour the simulator's
output depends on: ``random.Random`` seeded with an int (shuffles,
``randrange``, ``getrandbits``, ``random``), ``json.dumps`` with its
default separators and ``ensure_ascii``, ``format(x, spec)`` for the
specs the reports use, ``statistics.mean/median/stdev``, ``repr``, and
the ``str`` semantics of the data layer's readers (``str.splitlines``,
``int()``, ``str(PurePosixPath)``, ``urllib.parse.quote``, ``str.strip``).

A second set, written into ``rust/crates/mtgrules/tests/vectors``, pins
the Python *engine's* own semantics where the Rust engine must match
them exactly: mana-cost parsing and the mana pool's payment order
(``mana.json``).
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

_RUST = Path(__file__).resolve().parent.parent.parent / "rust" / "crates"
#: the pycompat vectors (CPython semantics)
_DEFAULT_OUT = _RUST / "pycompat" / "tests" / "vectors"
#: the engine vectors (the Python engine's own semantics, e.g. mana payment)
_RULES_OUT = _RUST / "mtgrules" / "tests" / "vectors"

_SEEDS = [0, 1, 7, 42, 11, 123456789, 2**31 - 1, 2**32, 2**32 + 1, 2**64 - 1, -5]
_RANDRANGE_NS = [1, 2, 3, 7, 10, 100, 1000, 65536, 2**32, 2**40]
_SHUFFLE_SIZES = [0, 1, 2, 3, 7, 10, 40, 99]
_GETRANDBITS_KS = [1, 5, 31, 32, 33, 53, 64, 100, 128]


def random_vectors() -> list[dict[str, Any]]:
    """Per seed: the first outputs of every generator method the engine uses.

    Each method starts from a fresh ``Random(seed)`` so the vectors are
    independent of one another.
    """
    out: list[dict[str, Any]] = []
    for seed in _SEEDS:
        entry: dict[str, Any] = {"seed": seed}
        rng = random.Random(seed)
        entry["getrandbits32"] = [rng.getrandbits(32) for _ in range(16)]
        rng = random.Random(seed)
        entry["random"] = [rng.random() for _ in range(6)]
        rng = random.Random(seed)
        entry["randrange"] = [
            {"n": n, "value": rng.randrange(n)} for n in _RANDRANGE_NS
        ]
        entry["shuffles"] = []
        for size in _SHUFFLE_SIZES:
            rng = random.Random(seed)
            items = list(range(size))
            rng.shuffle(items)
            entry["shuffles"].append({"size": size, "order": items})
        entry["getrandbits"] = []
        for k in _GETRANDBITS_KS:
            rng = random.Random(seed)
            entry["getrandbits"].append({"k": k, "value": str(rng.getrandbits(k))})
        # the simulator's pattern: one stream shared by several shuffles
        rng = random.Random(seed)
        shared = []
        for size in (7, 5, 9):
            items = list(range(size))
            rng.shuffle(items)
            shared.append(items)
        shared_pick = rng.randrange(4)
        entry["shared_stream"] = {"shuffles": shared, "then_randrange_4": shared_pick}
        out.append(entry)
    return out


def json_vectors() -> list[dict[str, Any]]:
    """Values with their ``json.dumps(value, default=str)`` text."""
    values: list[Any] = [
        None,
        True,
        False,
        0,
        -1,
        12345678901234567890,
        0.0,
        -0.0,
        1.0,
        0.1,
        0.5,
        2.5,
        1e16,
        1e15,
        123456789.123,
        1e-5,
        0.0001,
        1e22,
        5e-324,
        1.7976931348623157e308,
        3.141592653589793,
        100.0 / 3.0,
        "",
        "plain",
        'quote" backslash\\ slash/',
        "newline\n tab\t cr\r bell\x07 nul\x00",
        "ünïcödé — “quotes” ✓",
        "emoji 😀 and \U0001d518",  # a non-BMP letter: two UTF-16 escapes
        "\u2212 minus",
        [],
        [1, 2.0, "x", None, True],
        {},
        {"b": 1, "a": 2, "c": {"z": [1, {"y": None}], "d": 0.25}},
        {"stats": {"mulligans": 1, "life": 40, "drain": 2.5, "lost": ""}},
        {"n": 1, "who": "Fixture #1", "x": None, "commander": True},
        [[1.5, -2.25], {"k": "v"}, "end"],
    ]
    return [{"value": v, "dumps": json.dumps(v, default=str)} for v in values]


_FLOAT_SPECS = [
    ".1f",
    ".0f",
    ".2f",
    "5.1f",
    "+5.1f",
    "17.1f",
    "16.1f",
    "18.2f",
    "g",
    ".3g",
]
_FLOAT_VALUES = [
    0.0,
    -0.0,
    1.0,
    2.5,
    3.5,
    0.5,
    1.5,
    0.125,
    0.375,
    0.25,
    0.35,
    0.05,
    0.15,
    2.675,
    99.95,
    99.949999,
    100.0 / 3.0,
    200.0 / 3.0,
    33.35,
    123456789.125,
    1e16,
    1e-5,
    1e-7,
    1234.5678,
    -7.25,
    -0.04,
    0.25 * 100,
    (1 / 3) * 100,
    (7 / 9) * 100,
    8.0,
    0.25,
    4.0,
]
_INT_SPECS = ["d", "4d", "3d", ">3", "<8d", "08d"]
_INT_VALUES = [0, 1, -1, 7, 42, 999, 10000, -12345]
_STR_SPECS = ["<38s", ">18s", ">14", "<14", "<20s", "^9"]
_STR_VALUES = [
    "",
    "a",
    "Fixture #1",
    "a string longer than the field width asks for",
    "ünï",
]


def format_vectors() -> list[dict[str, Any]]:
    """``format(value, spec)`` for the specs the reports and the TUI use."""
    out: list[dict[str, Any]] = []
    for spec in _FLOAT_SPECS:
        out.extend(
            {"kind": "float", "value": v, "spec": spec, "text": format(v, spec)}
            for v in _FLOAT_VALUES
        )
    for spec in _INT_SPECS:
        out.extend(
            {"kind": "int", "value": v, "spec": spec, "text": format(v, spec)}
            for v in _INT_VALUES
        )
    for spec in _STR_SPECS:
        out.extend(
            {"kind": "str", "value": v, "spec": spec, "text": format(v, spec)}
            for v in _STR_VALUES
        )
    out.extend(
        {"kind": "repr", "value": v, "spec": "", "text": repr(v)} for v in _FLOAT_VALUES
    )
    out.extend(
        {"kind": "repr", "value": v, "spec": "", "text": repr(v)}
        for v in [1e100, 1e-100, 5e-324, 1.7976931348623157e308, 123456789012345680.0]
    )
    return out


_STATS_CASES = [
    ([3, 5], 10),
    ([0, 10], 10),
    ([1, 1, 1], 3),
    ([1, 2, 4], 7),
    ([0, 0, 0, 0], 20),
    ([2, 3, 5, 7, 11], 13),
    ([1, 1, 2, 3, 5, 8, 13], 20),
    ([17], 20),
    ([5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16], 50),
    ([333, 666, 999], 1000),
]


def stats_vectors() -> dict[str, Any]:
    """``statistics`` results for per-seed win rates and game lengths."""
    rates: list[dict[str, Any]] = []
    for wins, games in _STATS_CASES:
        r = [w / games for w in wins]
        rates.append(
            {
                "wins": wins,
                "games": games,
                "mean": statistics.mean(r),
                "stdev": statistics.stdev(r) if len(r) > 1 else 0.0,
            },
        )
    lengths = [
        [12],
        [12, 19],
        [20, 18, 21],
        [1, 2, 3, 4],
        [40, 40, 40, 40, 40],
        [7, 7, 8, 9, 10, 10],
        [28, 19, 20, 20, 22, 17, 31],
    ]
    turns = [
        {"turns": t, "mean": statistics.mean(t), "median": statistics.median(t)}
        for t in lengths
    ]
    return {"rates": rates, "turns": turns}


def repr_vectors() -> list[dict[str, Any]]:
    """``repr`` of the event payloads the TUI falls back to printing."""
    values: list[Any] = [
        {"spell": "Olivia's Dragoon", "who": "Black B"},
        {"who": "p0", "n": 2, "total": 3},
        {"who": "p0", "card": "Sol Ring", "x": None, "commander": True},
        {"a": 1.5, "b": [1, 2, "x"], "c": {"d": False}},
        {},
        [],
        "it's",
        'say "hi"',
        "both ' and \"",
        "tab\there",
        "ünï — ok",
        "\x07bell",
        -0.0,
        1e16,
        True,
        None,
    ]
    return [{"value": v, "repr": repr(v)} for v in values]


_SPLITLINES_TEXTS = [
    "",
    "one line",
    "a\nb",
    "a\nb\n",
    "a\r\nb\rc\n\nd",
    "\n",
    "\r\n\r\n",
    "x\u2028y\x0cz\x1cw\x85v",
    "trailing\r",
    "1 Sol Ring\r\n2 Island\r\n",
]
_INT_TEXTS = [
    "1",
    " 42 ",
    "-7",
    "+3",
    "1_000",
    "0",
    "007",
    "\t12\n",
    "1.0",
    "",
    " ",
    "1__0",
    "_1",
    "1_",
    "abc",
    "0x10",
    "\uff11\uff12",  # fullwidth digits: int() accepts them, this port does not
    "True",
    "12345678901234567890",
]
_PATH_TEXTS = [
    "",
    ".",
    "..",
    "a",
    "./a",
    "a/./b",
    "a//b",
    "a/b/",
    "a/b/.",
    "/",
    "//",
    "///",
    "//net/share",
    "///a",
    "/var//x/",
    "../a/../b",
    "decks/x.ttl",
    "a/b/../c",
]
_QUOTE_TEXTS = [
    "",
    "Sol Ring",
    "Llanowar Elves",
    "Pia Nalaar, Chief Mechanic",
    "Lim-Dûl's Vault",
    "a/b_c.d~e",
    "+&=?#%",
    "ünï — ok",
    "😀",
    "Fire // Ice",
]
_STRIP_TEXTS = [
    "  a  ",
    "\t\n a b \r\n",
    "\x1c\x1dx\x1e\x1f",
    "\u2003y\u3000",
    "",
    "   ",
]


def str_vectors() -> dict[str, Any]:
    """``str`` semantics the data layer's readers depend on."""
    ints: list[dict[str, Any]] = []
    for text in _INT_TEXTS:
        try:
            ints.append({"text": text, "value": str(int(text)), "error": None})
        except ValueError as err:
            ints.append({"text": text, "value": None, "error": str(err)})
    return {
        "splitlines": [{"text": t, "lines": t.splitlines()} for t in _SPLITLINES_TEXTS],
        "int": ints,
        "posix_path": [{"text": t, "str": str(PurePosixPath(t))} for t in _PATH_TEXTS],
        "quote": [{"text": t, "quoted": quote(t)} for t in _QUOTE_TEXTS],
        "strip": [{"text": t, "stripped": t.strip()} for t in _STRIP_TEXTS],
    }


_COSTS = [
    "",
    "{0}",
    "{2}{G}{G}",
    "{X}{R}",
    "{B/R}{W}",
    "{2/W}",
    "{C}{S}{Q}",
    "{X}{X}{2}{G}{G}{B/R}{C}{S}{2/W}",
    "{W}{U}{B}{R}{G}",
    "{10}",
    "{G/U}{G/U}",
    "{W/P}",
    "{C}{C}",
]
#: (mana added to a fresh pool, in order), cost to pay
_POOL_CASES: list[tuple[list[tuple[str, int]], str]] = [
    ([("U", 1), ("R", 1), ("C", 1)], "{U/R}"),
    ([("U", 1), ("R", 2)], "{U/R}"),
    ([("R", 2), ("U", 1)], "{U/R}"),
    ([("W", 1), ("G", 3)], "{2}"),
    ([("C", 1), ("G", 1)], "{1}"),
    ([("G", 2)], "{G}{G}{G}"),
    ([("G", 2), ("C", 1)], "{C}{C}"),
    (
        [("W", 1), ("U", 1), ("B", 1), ("R", 1), ("G", 1), ("C", 1)],
        "{W/U}{U/B}{B/R}{2}",
    ),
    ([("B", 1), ("R", 1), ("G", 1)], "{B/G}{R/G}{G}"),
    ([("W", 2), ("U", 2), ("B", 2), ("R", 2), ("G", 2)], "{3}{W}{W}"),
    ([("W", 1), ("U", 3), ("B", 2)], "{4}"),
    ([("R", 1)], "{X}{R}"),
    ([], "{0}"),
    ([("C", 2)], "{W}"),
    ([("W", 1), ("U", 1), ("B", 1), ("R", 1), ("G", 1)], "{W/U/B}{2}"),
]
_ORDER_CASES = [
    ["G", "C", "W", "zz", "B"],
    ["R", "B"],
    ["C", "G", "U"],
    ["any", "U", "W"],
    [],
    ["G", "G", "W"],
]
_LAND_CASES = [
    ["R", "B"],
    ["W", "U", "B", "R", "G"],
    ["C"],
    [],
    ["G", "W", "U", "B", "R", "C"],
    ["U", "X"],
]


def mana_vectors() -> dict[str, Any]:
    """Pin the engine's mana system: costs, pool payment, canonical order."""
    from mtgrules.manasys import (  # noqa: PLC0415 - engine import kept local to this topic
        ManaPool,
        land_mana_types,
        mana_order,
        parse_cost,
    )

    costs = []
    for text in _COSTS:
        c = parse_cost(text)
        costs.append(
            {
                "cost": text,
                "generic": c.generic,
                "pips": dict(c.pips),
                "hybrid": [sorted(h) for h in c.hybrid],
                "colorless": c.colorless,
                "x_count": c.x_count,
                "mv": c.mv,
            },
        )
    pools = []
    for added, text in _POOL_CASES:
        pool = ManaPool()
        for mana_type, n in added:
            pool.add(mana_type, n)
        cost = parse_cost(text)
        can = pool.can_pay(cost)
        paid = pool.pay(cost)
        pools.append(
            {
                "added": [list(a) for a in added],
                "cost": text,
                "can_pay": can,
                "paid": paid,
                "after": dict(pool.mana),
            },
        )
    return {
        "costs": costs,
        "pools": pools,
        "order": [{"types": t, "ordered": mana_order(t)} for t in _ORDER_CASES],
        "lands": [
            {
                "colors": c,
                "types": list(land_mana_types(c)[0]),
                "any": land_mana_types(c)[1],
            }
            for c in _LAND_CASES
        ],
    }


def _write(out_dir: Path, files: dict[str, Any]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in files.items():
        path = out_dir / name
        path.write_text(
            json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def write_all(out_dir: Path) -> list[Path]:
    """Write the pycompat vector files into *out_dir*; return the paths."""
    return _write(
        out_dir,
        {
            "random.json": random_vectors(),
            "json.json": json_vectors(),
            "format.json": format_vectors(),
            "stats.json": stats_vectors(),
            "repr.json": repr_vectors(),
            "str.json": str_vectors(),
        },
    )


def write_rules(out_dir: Path) -> list[Path]:
    """Write the engine vector files into *out_dir*; return the paths."""
    return _write(out_dir, {"mana.json": mana_vectors()})


def main(argv: list[str] | None = None) -> None:
    """Write the vectors (optionally into the directories named in argv).

    ``argv[0]`` is the pycompat directory, ``argv[1]`` the engine one.
    """
    args = sys.argv[1:] if argv is None else argv
    out_dir = Path(args[0]) if args else _DEFAULT_OUT
    rules_dir = Path(args[1]) if len(args) > 1 else _RULES_OUT
    for path in [*write_all(out_dir), *write_rules(rules_dir)]:
        print(path)  # noqa: T201 - CLI report


if __name__ == "__main__":
    main()
